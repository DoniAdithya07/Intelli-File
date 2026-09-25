"""Scan + index every supported file in a folder, skipping files whose
content hash hasn't changed since last time. Shared by the /index-folder
API endpoint and the command-line demo script.

Text/audio files and photos take different pipelines (see Indexer vs
VisualIndexer) but share one file-identity store, so hashing, change
detection and tombstoning work identically regardless of file type.
"""

import contextlib
import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..files.discovery import SUPPORTED_EXTENSIONS, VISUAL_EXTENSIONS, discover_files, permission_hint
from ..files.identity import build_file_record, compute_file_hash
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

    # Walk first so progress can be reported as done/total (the Status
    # screen shows "7,820 / 10,000 files"); the walk is cheap next to embedding.
    def walk_error(error: OSError) -> None:
        target = Path(getattr(error, "filename", None) or folder)
        logger.warning("Cannot list %s: %s", target, error)
        _note_failure(target, error)

    paths = list(discover_files([folder], extensions=extensions, on_error=walk_error))
    if not paths and not os.access(folder, os.R_OK | os.X_OK):
        _note_failure(Path(folder), PermissionError(13, "Permission denied", folder))
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
            count, failed = _scan_one(indexer, visual_indexer, path, record_store, count, failed, force)
        if throttle is not None and count > before:
            pause = throttle()
            if pause > 0:
                time.sleep(pause)

    # Anything previously indexed under this folder that the walk didn't
    # see has been deleted, moved out, or renamed to an unsupported name.
    # Tombstone it and purge its chunks/vectors/keywords right away so it
    # can never come back as a dead search result.
    folder_prefix = str(Path(folder)) + os.sep  # same form discover_files yields
    with guard:
        for record in record_store.list_active():
            if record.path.startswith(folder_prefix) and record.path not in seen:
                record_store.mark_deleted(record.path)
        cleanup_tombstones(indexer, visual_indexer=visual_indexer)
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


def _note_failure(path: Path, error: Exception, content_hash: str | None = None) -> None:
    message = f"{type(error).__name__}: {str(error)[:160]}"
    if isinstance(error, PermissionError):
        message = f"PermissionError: {permission_hint(str(path))}"
    RECENT_FAILURES.append({"path": str(path), "error": message, "at": time.time()})
    del RECENT_FAILURES[:-MAX_RECENT_FAILURES]
    if content_hash:
        FAILED_HASHES[content_hash] = message


def _scan_one(indexer, visual_indexer, path: Path, record_store, count: int, failed: int, force: bool) -> tuple[int, int]:
    """One file of the scan: skip if unchanged, detect a rename, otherwise
    (re-)index. Returns the updated (count, failed)."""
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
    except OSError as e:
        # Deleted mid-read, or unreadable (permissions, locked by another
        # program): counted and shown, never fatal (Phase 11 / 12).
        logger.warning("Cannot read %s: %s", path, e)
        _note_failure(path, e)
        return count, failed + 1
    if existing is not None and not existing.deleted and existing.indexed and existing.hash == current_hash and not force:
        return count + 1, failed
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
        if visual_indexer is not None and path.suffix.lower() in VISUAL_EXTENSIONS:
            visual_indexer.index_visual_file(path, record.file_id, record.hash)
        else:
            indexer.index_file(path, record.file_id, record.hash)
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

