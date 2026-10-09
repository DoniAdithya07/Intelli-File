"""Scan + index every supported file in a folder, skipping files whose
content hash hasn't changed since last time. Shared by the /index-folder
API endpoint and the command-line demo script.

Text/audio files and photos take different pipelines (see Indexer vs
VisualIndexer) but share one file-identity store, so hashing, change
detection and tombstoning work identically regardless of file type.
"""

import contextlib
import json
import logging
import os
import struct
import threading
import time
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..files.discovery import (
    AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, LONG_PATH_REASON, MAX_PATH, SUPPORTED_EXTENSIONS, VIDEO_EXTENSIONS, VISUAL_EXTENSIONS,
    discover_files, long_paths_enabled, permission_hint,
)
from ..files.identity import ONLINE_ONLY_REASON, OnlineOnlyFile, build_file_record, compute_file_hash
from .cleanup import cleanup_tombstones
from .indexer import Indexer

if TYPE_CHECKING:
    from .visual_indexer import VisualIndexer

logger = logging.getLogger(__name__)

def _unchanged_by_stat(path: Path, record) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    return stat.st_size == record.size and stat.st_mtime == record.modified_time


# progress(done, total, current_path, failed) — called before each file and once at the end.
ProgressCallback = Callable[[int, int, str | None, int], None]


def index_folder(
    indexer: Indexer,
    folder: str,
    visual_indexer: "VisualIndexer | None" = None,
    progress: ProgressCallback | None = None,
    force: bool = False,
    lock: "threading.RLock | None" = None,
    gate: threading.Event | None = None,
    throttle: Callable[[], float] | None = None,
) -> int:
    """Index every supported file under `folder` and reconcile the index
    with what's actually on disk: renamed files keep their vectors under
    the new path, files that vanished are purged. Returns how many files
    are indexed there in total (including ones skipped as unchanged, so
    the number reflects the folder's contents rather than how much work
    happened to be needed this run).

    Photos are only discovered when a visual_indexer is supplied — with
    no CLIP model installed, indexing an image would produce a file
    record pointing at nothing searchable.

    `lock`, when given, is held while each single file is examined and
    (re-)indexed, and again for the final purge — the same lock the live
    watcher's job handler holds per job, so the two writers never
    interleave on one file (2026-09-21; see LiveIndexing.write_lock).

    `gate` (Phase 10): the scan waits on it before each file, so pausing
    for battery/low-power happens between files and resuming continues
    from the next one — no rescan. `throttle()` returns seconds to sleep
    after each indexed file (Battery Saver mode).

    `force` re-embeds every file even when its hash is unchanged (the
    Re-index button). It replaces an earlier approach that purged the
    folder's records first and then queued a scan: found 2026-09-19, a
    re-index requested while the startup rescan of the same folder was
    still running purged 151 records, and the queue then dropped the
    request as a duplicate — the folder was left empty until the next
    launch. Re-embedding in place never has a moment with no records.
    """
    extensions = SUPPORTED_EXTENSIONS | (VISUAL_EXTENSIONS if visual_indexer is not None else set())
    record_store = indexer.file_record_store
    folder_prefix = str(Path(folder)) + os.sep  # same form discover_files yields
    skipped = SKIPPED[folder] = Counter()  # live: the Status screen reads it while the scan runs

    # An unplugged drive or an offline network share (2026-10-04): with the
    # folder missing the walk saw nothing, and the purge below deleted every
    # record under it — the whole drive had to be re-indexed when it came
    # back. While it is away nothing is written; the next scan catches up.
    if not Path(folder).is_dir():
        logger.warning("Folder %s is not reachable (unplugged drive or offline share?): its index is kept, scan skipped", folder)
        _note_failure(Path(folder), FileNotFoundError(2, "Folder is not reachable (unplugged drive or offline network share?). Its files stay in the index until it is back", folder))
        if progress is not None:
            progress(0, 0, None, 0)
        return sum(1 for r in record_store.list_active() if r.path.startswith(folder_prefix))

    # Walk first so progress can be reported as done/total (the Status
    # screen shows "7,820 / 10,000 files"); the walk is cheap next to embedding.
    unlistable: list[str] = []  # directories the OS refused to list: their records are not purged

    def walk_error(error: OSError) -> None:
        target = Path(getattr(error, "filename", None) or folder)
        logger.warning("Cannot list %s: %s", target, error)
        if len(str(target)) >= MAX_PATH - 12 and not long_paths_enabled():
            # A folder too deep for Windows' path limit: say so, not WinError 3.
            _note_failure(target, OSError("this folder's path is too long for Windows (over 260 characters, long paths off); move it to a shorter folder, or turn on \"Enable Win32 long paths\""))
        else:
            _note_failure(target, error)
        unlistable.append(str(target) + os.sep)

    def walk_skip(path: Path, reason: str) -> None:
        skipped["path_too_long" if reason == LONG_PATH_REASON else "too_large"] += 1
        _note_skip(path, reason)

    paths = list(discover_files([folder], extensions=extensions, on_error=walk_error, on_skip=walk_skip))
    if not paths and not os.access(folder, os.R_OK | os.X_OK):
        _note_failure(Path(folder), PermissionError(13, "Permission denied", folder))
        unlistable.append(folder_prefix)
    total = len(paths)
    count = 0
    failed = 0
    seen: set[str] = set()
    guard = lock if lock is not None else contextlib.nullcontext()
    for done, path in enumerate(paths):
        if gate is not None:
            gate.wait()
        if progress is not None:
            progress(done, total, str(path), failed)
        seen.add(str(path))
        before = count
        with guard:
            count, failed = _scan_one(indexer, visual_indexer, path, record_store, count, failed, force, skipped)
        if throttle is not None and count > before:
            pause = throttle()
            if pause > 0:
                time.sleep(pause)

    # Anything previously indexed under this folder that the walk didn't
    # see has been deleted, moved out, or renamed to an unsupported name.
    # Tombstone it and purge its chunks/vectors/keywords right away so it
    # can never come back as a dead search result. Not under a directory
    # the walk could not list: those files were not seen, not deleted.
    with guard:
        for record in record_store.list_active():
            if record.path.startswith(folder_prefix) and record.path not in seen and not record.path.startswith(tuple(unlistable)):
                record_store.mark_deleted(record.path)
        cleanup_tombstones(indexer, visual_indexer=visual_indexer)
    if skipped:
        logger.info("scan of %s skipped %s", folder, dict(skipped))
    if progress is not None:
        progress(total, total, None, failed)
    return count


# The last few failures with their reason, for the Status screen (Phase 11).
RECENT_FAILURES: list[dict] = []
MAX_RECENT_FAILURES = 20
# Content hashes that failed extraction in this process: a corrupt PDF is
# still corrupt on the next scan, so it is counted again but not re-parsed
# (and not re-logged) until its bytes change or a forced re-index.
FAILED_HASHES: dict[str, str] = {}
# Files a scan left alone on purpose, per folder, by reason ("too_large":
# over the size cap; "online_only": a OneDrive placeholder; "path_too_long").
# Not failures: the Status screen shows them so a folder that indexes fewer
# files than it holds says why (2026-10-04).
SKIPPED: dict[str, Counter] = {}
SKIP_KINDS = ("too_large", "online_only", "path_too_long")
# The last few skipped files with their reason, scan and watcher alike. Kept
# out of RECENT_FAILURES (2026-10-05): ten skipped videos pushed every real
# failure off the Status screen's list.
RECENT_SKIPS: list[dict] = []


# ----- failure reasons the user can read (2026-10-05) -----
# The Status screen showed "PdfStreamError: Stream has ended unexpectedly"
# and "ValueError: old .doc could not be read: error: unpack requires...".
# Every failure's reason is made here, once; the raw error stays in the log.

_KIND_NAMES = {
    ".pdf": "PDF", ".doc": "Word", ".docx": "Word", ".xls": "Excel", ".xlsx": "Excel", ".xlsm": "Excel",
    ".ppt": "PowerPoint", ".pptx": "PowerPoint", ".odt": "OpenDocument", ".ods": "OpenDocument",
    ".odp": "OpenDocument", ".epub": "EPUB", ".msg": "Outlook message", ".eml": "e-mail", ".rtf": "RTF",
}
# Error classes that mean "these bytes are not a valid file", by name so no
# parser library is imported here (pypdf, python-docx, openpyxl, xlrd, PIL, PyAV).
_DAMAGE_ERRORS = {
    "BadZipFile", "ParseError", "PyPdfError", "XLRDError", "PackageNotFoundError", "InvalidFileException",
    "UnidentifiedImageError", "InvalidDataError", "EOFError", "UnicodeDecodeError", "IndexError", "KeyError",
}
FILE_ACCESS_REFUSED = "Windows refused access (the file may be open in another program)."

# ----- a file that takes the whole app down (2026-10-05) -----
# A native crash in a parser (a video decoder, a PDF image filter) or the
# out-of-memory killer ends the process, not just the file: the next launch
# re-read the same file and died again, every launch. Before each file is
# read its path and hash go in IN_PROGRESS_FILE (in the app's data folder;
# set by watch_setup.LiveIndexing) and are removed after. A marker found at
# startup names the file that was being read when the app died; its hash
# joins POISONED and it is not read again until its bytes change.
IN_PROGRESS_FILE: Path | None = None
# Closing the window kills the backend outright (TerminateProcess), so the
# marker is also left behind by an ordinary exit mid-file. One leftover
# marker only makes the file a suspect; it is skipped (POISONED) when it is
# left behind a second time in a row, and reading it to the end clears it
# (code review 2026-10-06). SUSPECTS_FILE is set next to IN_PROGRESS_FILE.
SUSPECTS: dict[str, str] = {}  # content hash -> path
SUSPECTS_FILE: Path | None = None


def save_suspects() -> None:
    if SUSPECTS_FILE is not None:
        try:
            SUSPECTS_FILE.write_text(json.dumps(SUSPECTS, indent=2))
        except OSError:
            logger.warning("Could not save %s", SUSPECTS_FILE, exc_info=True)
POISONED: dict[str, str] = {}  # content hash -> path
CRASH_REASON = "IntelliFile closed unexpectedly while reading this file; it is skipped until it changes"


class ClosedWhileReading(Exception):
    """The app died while reading this file (found at the next startup)."""


@contextlib.contextmanager
def reading(path: Path, content_hash: str):
    marker = IN_PROGRESS_FILE
    if marker is not None:
        try:
            marker.write_text(json.dumps({"path": str(path), "hash": content_hash, "started_at": time.time()}))
        except OSError:
            logger.warning("Could not write %s", marker, exc_info=True)
            marker = None
    try:
        yield
    finally:
        if marker is not None:
            with contextlib.suppress(OSError):
                marker.unlink(missing_ok=True)
        if SUSPECTS.pop(content_hash, None) is not None:
            save_suspects()  # read to the end this time: no longer a suspect


def _kind_name(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    return _KIND_NAMES.get(suffix) or suffix.lstrip(".").upper()


def _sentence(error: BaseException) -> str:
    """The error's own words, without the Python class name, as a sentence."""
    text = (error.strerror if isinstance(error, OSError) and error.strerror else str(error)).strip()[:300]
    if not text:
        return "It could not be read."
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


def describe_failure(path: Path, error: BaseException) -> str:
    """What the Status screen says about a file that could not be indexed."""
    if isinstance(error, ClosedWhileReading):
        return CRASH_REASON
    if isinstance(error, MemoryError):
        return "Too large to read on this computer."
    if type(error).__name__ in ("DecompressionBombError", "DecompressionBombWarning"):  # over PIL's pixel cap
        from PIL import Image

        return f"The image has too many pixels to read safely (over {Image.MAX_IMAGE_PIXELS // 1_000_000} million)."
    if isinstance(error, PermissionError):
        return f"{permission_hint(str(path))}." if path.is_dir() else FILE_ACCESS_REFUSED
    chain, cause = [], error
    while cause is not None and len(chain) < 5:  # the reader's error and what it wrapped
        chain.append(cause)
        cause = cause.__cause__
    names = {cls.__name__ for e in chain for cls in type(e).__mro__}
    text = str(error)
    if "FileNotDecryptedError" in names or "password" in text.lower():
        return "The file is password-protected."
    damaged = (
        names & _DAMAGE_ERRORS
        or any(isinstance(e, struct.error) for e in chain)
        # olefile reports a broken compound file as OSError without an errno.
        or any(type(e) is OSError and e.errno is None for e in chain[1:])
        or "damaged" in text
    )
    if damaged:
        return f"The file is damaged or not a valid {_kind_name(path)} file."
    return _sentence(error)


def _note_failure(path: Path, error: BaseException, content_hash: str | None = None) -> None:
    reason = describe_failure(path, error)
    logger.info("Not indexed: %s (%s: %s)", path, type(error).__name__, error)
    # "error" is the key the Status screen has always read; "reason" the same sentence.
    RECENT_FAILURES.append({"path": str(path), "error": reason, "reason": reason, "at": time.time()})
    del RECENT_FAILURES[:-MAX_RECENT_FAILURES]
    # Only failures that will happen again are remembered. A file locked by
    # Word, a half-written download or a full memory (OSError, MemoryError)
    # is fine a minute later; remembering it made the failure last until
    # the app restarted (2026-10-04).
    if content_hash and not isinstance(error, (OSError, MemoryError)):
        FAILED_HASHES[content_hash] = reason


def _note_skip(path: Path, reason: str) -> None:
    """A file left alone on purpose. One entry per path: the watcher hears
    about the same file at every save."""
    RECENT_SKIPS[:] = [s for s in RECENT_SKIPS if s["path"] != str(path)]
    RECENT_SKIPS.append({"path": str(path), "reason": reason})
    del RECENT_SKIPS[:-MAX_RECENT_FAILURES]


def _index_image_text(indexer, path: Path, file_id: str) -> None:
    """OCR text for an image (improvement 4). Photo search already has
    the image; an OCR failure must never fail the file."""
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        return
    try:
        indexer.index_image_text(path, file_id)
    except Exception:
        logger.warning("OCR failed for %s", path, exc_info=True)


def _scan_one(indexer, visual_indexer, path: Path, record_store, count: int, failed: int, force: bool, skipped: Counter | None = None) -> tuple[int, int]:
    """One file of the scan: skip if unchanged, detect a rename, otherwise
    (re-)index. Returns the updated (count, failed); files left alone on
    purpose are counted in `skipped`."""
    existing = record_store.get_by_path(str(path))
    if not path.exists():
        # Vanished between the walk and now (deleted or moved mid-index).
        # Its record, if any, is tombstoned by the purge at the end of the
        # scan; one such file must never abort the folder (Phase 11).
        return count, failed
    # Fast path (2026-09-21): a file whose size and mtime both match its
    # record has not changed — skip reading it. Before this every launch
    # re-hashed every byte of every watched file (a folder of videos =
    # gigabytes read just to confirm nothing happened). The hash is
    # still computed for anything new, touched or forced.
    if existing is not None and not existing.deleted and existing.indexed and not force and _unchanged_by_stat(path, existing):
        return count + 1, failed
    try:
        current_hash = compute_file_hash(path)
    except OnlineOnlyFile:
        if skipped is not None:
            skipped["online_only"] += 1
        _note_skip(path, ONLINE_ONLY_REASON)
        return count, failed
    except OSError as e:
        # Deleted mid-read, or unreadable (permissions, locked by another
        # program): counted and shown, never fatal (Phase 11 / 12).
        logger.warning("Cannot read %s: %s", path, e)
        _note_failure(path, e)
        return count, failed + 1
    if existing is not None and not existing.deleted and existing.indexed and existing.hash == current_hash and not force:
        return count + 1, failed
    if current_hash in POISONED and not force:
        # The app died reading these bytes: normal scans skip them. Re-index
        # (force) is the user asking to try again, so it reads them (2026-10-06);
        # if it crashes again the marker puts the file back on the list.
        return count, failed + 1
    if current_hash in FAILED_HASHES and not force:
        return count, failed + 1  # known-bad bytes: counted, not re-parsed
    # (A tombstoned record with the same hash — the file was briefly
    # unreadable when a search looked for it — falls through and is
    # re-indexed as active.)

    # Rename detection (found by a live test, 2026-09-11: the user
    # renamed files and search kept returning the old paths). Nothing
    # at this path, but an active record with identical content whose
    # own path is gone — that's the same file under a new name. Keep
    # its file_id and vectors, just move the record; no re-embedding,
    # per the PRD's Rename Handling section.
    if existing is None:
        twin = record_store.find_active_by_hash(current_hash)
        if twin is not None and not Path(twin.path).exists():
            record_store.rename(twin.path, str(path))
            return count + 1, failed

    try:
        record = build_file_record(path, file_id=existing.file_id if existing else None, file_hash=current_hash, indexed=False)
    except OSError as e:
        _note_failure(path, e)
        return count, failed + 1
    record_store.upsert(record)
    try:
        with reading(path, record.hash):
            if visual_indexer is not None and path.suffix.lower() in VISUAL_EXTENSIONS:
                visual_indexer.index_visual_file(path, record.file_id, record.hash)
                _index_image_text(indexer, path, record.file_id)
            else:
                indexer.index_file(path, record.file_id, record.hash, replace=existing is not None)
        record_store.mark_indexed(record.file_id)
    except Exception as e:
        # One unreadable file (password-protected PDF, truncated audio)
        # must not abort the whole folder; it is counted and shown on
        # the Status screen as "failed / skipped". Its record must not
        # keep the NEW hash, or the next scan would see "unchanged" and
        # never retry (2026-09-21): restore the previous record (its old
        # chunks are still there — the indexer no longer deletes them
        # before extraction succeeds) or drop a never-indexed file.
        logger.exception("Failed to index %s", path)
        _note_failure(path, e, current_hash)
        failed += 1
        if existing is not None:
            record_store.upsert(existing)
        else:
            record_store.mark_deleted(record.path)
    return count + 1, failed

