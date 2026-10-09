"""Keeps the index in step with the disk while the app runs.

Phase 2 built the watcher (files/service.py) and Phase 4 the index/delete
operations, but until 2026-09-11 nothing in the running app connected
them: /index-folder scanned once and that was it. A live test caught it —
the user renamed files and search kept handing back the old paths. This
module is that missing wiring: one job handler that routes watcher events
to the right indexer, and a small on-disk list of watched folders so the
watch resumes on the next launch without the user re-picking anything.
"""

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .files.discovery import IMAGE_EXTENSIONS, SUPPORTED_EXTENSIONS, VIDEO_EXTENSIONS, VISUAL_EXTENSIONS
from .files.jobs import Job, JobQueue
from .files.service import FileWatchService
from .indexing import Indexer, VisualIndexer, cleanup_tombstones, index_folder
from .indexing import folder_scan
from .indexing.folder_scan import POISONED, RECENT_FAILURES, RECENT_SKIPS, SKIP_KINDS, SKIPPED, _index_image_text, _note_failure as note_failure, _note_skip as note_skip
from .power import PowerMonitor, resource_mode

logger = logging.getLogger(__name__)

WATCHED_FOLDERS_FILE = "watched_folders.json"
WATCHED_FILES_FILE = "watched_files.json"  # Phase 12 "limited" access: single files
# The file being read right now, and the files the app died reading (see
# folder_scan.reading): both in the app's data folder, so they outlive a crash.
IN_PROGRESS_FILE = "indexing_in_progress.json"
CRASHED_FILES_FILE = "skipped_after_crash.json"
SUSPECTS_FILE = "interrupted_once.json"
MAX_CRASHED_FILES = 200
# How long a watched folder's "is it there?" answer is reused (2026-10-05):
# /status is polled every 700 ms and asked every root each time; a folder on
# a dead network share made each poll wait for Windows' network timeout.
REACHABILITY_TTL = 30.0


def load_watched_folders(config_dir: Path) -> list[str]:
    path = config_dir / WATCHED_FOLDERS_FILE
    if not path.exists():
        return []
    try:
        folders = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    # A folder that is missing right now stays listed (2026-10-04): it is
    # usually an unplugged drive or an offline share, not a deleted folder,
    # and dropping it here forgot it for good. The user removes it on the
    # Folders screen, where it shows as missing.
    return [f for f in folders if isinstance(f, str) and Path(f).is_absolute()]


def save_watched_folders(config_dir: Path, folders: list[str]) -> None:
    (config_dir / WATCHED_FOLDERS_FILE).write_text(json.dumps(sorted(set(folders)), indent=2))


def load_watched_files(config_dir: Path) -> list[str]:
    path = config_dir / WATCHED_FILES_FILE
    if not path.exists():
        return []
    try:
        files = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    return [f for f in files if isinstance(f, str) and Path(f).is_absolute() and Path(f).is_file()]


def save_watched_files(config_dir: Path, files: list[str]) -> None:
    (config_dir / WATCHED_FILES_FILE).write_text(json.dumps(sorted(set(files)), indent=2))


@dataclass
class IndexJob:
    """What the Status screen shows. One job at a time; folders queue."""

    state: str = "idle"  # idle | running | done | failed
    folder: str | None = None
    done: int = 0
    total: int = 0
    current_file: str | None = None
    failed: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    queued: list[str] = field(default_factory=list)


class LiveIndexing:
    """Owns the watcher + job queue for the app's lifetime, plus the one
    background indexing job the UI can watch."""

    def __init__(self, indexer: Indexer, visual_indexer: VisualIndexer | None, config_dir: Path):
        self.indexer = indexer
        self.visual_indexer = visual_indexer
        self.config_dir = config_dir
        folder_scan.IN_PROGRESS_FILE = config_dir / IN_PROGRESS_FILE
        folder_scan.SUSPECTS_FILE = config_dir / SUSPECTS_FILE
        extensions = SUPPORTED_EXTENSIONS | (VISUAL_EXTENSIONS if visual_indexer is not None else set())
        # Phase 10: one gate for the folder scan and the watcher's worker.
        # Set = indexing runs; cleared = paused between files. apply_power()
        # decides from the power state and the user's settings.
        self.gate = threading.Event()
        self.gate.set()
        self.paused_reason: str | None = None
        self.power: PowerMonitor | None = None
        self.settings_reader = lambda: {}  # set by the app: returns the settings document
        self.job_queue = JobQueue(self._handle_job, num_workers=1, gate=self.gate)
        self.watcher = FileWatchService(indexer.file_record_store, self.job_queue, extensions=extensions)
        self.watcher.on_skip = note_skip  # listed with the scan's skips on the Status screen
        self.extensions = extensions
        self.job = IndexJob()
        self._job_lock = threading.Lock()
        self._pending: list[str] = []
        self._force: set[str] = set()  # folders whose queued scan must re-embed everything
        self._runner: threading.Thread | None = None
        # Watched folders that were not reachable at startup (unplugged
        # drive, offline share): the watcher cannot watch them, but they are
        # kept in the saved list and on the Folders screen, and start being
        # watched when a scan finds them back.
        self.unreachable_roots: set[str] = set()
        # Two threads write to the index: the folder-scan runner and the
        # watcher's job worker. The stores lock each call, but a file's
        # index step is several calls (record upsert, chunk delete, chunk
        # add) — a scan and a watcher event on the same file could
        # interleave them (2026-09-21). One lock, held per file by the scan
        # and per job by the worker, keeps each file's step atomic while
        # still letting watcher jobs run between a scan's files.
        self.write_lock = threading.RLock()
        # Files whose live index step failed (2026-10-05): an attempt that died
        # after the vectors were written may have left chunks behind, so the
        # next attempt must replace, never append. Cleared on success.
        self._failed_attempts: set[str] = set()
        self._reachable: dict[str, tuple[float, bool]] = {}  # path -> (checked at, exists)
        self._probing: set[str] = set()
        self._reach_lock = threading.Lock()
        # Called after every scan / job so caches derived from chunk
        # vectors (Phase 17's per-file centroids) are dropped.
        self.on_index_changed = lambda: None
        self.on_folder_indexed = lambda folder: None  # a whole folder job finished

    def start(self) -> None:
        self.job_queue.start()
        folders = load_watched_folders(self.config_dir)
        # Crash recovery (Phase 11): records whose chunks were never
        # confirmed are re-indexed by the scans queued below — the scan
        # treats `indexed = 0` as "changed", so nothing else is rescanned
        # (unchanged files are skipped on size + mtime).
        unfinished = self.indexer.file_record_store.list_unfinished()
        if unfinished:
            logger.info("crash recovery: %d file(s) were mid-index at the last exit and will be re-indexed", len(unfinished))
        self.recovered_at_start = len(unfinished)
        self._skip_files_that_crashed()
        for file in load_watched_files(self.config_dir):
            self.watcher.add_file(file)
            self.index_single_file(file)
        for folder in folders:
            if Path(folder).is_dir():
                self.watcher.add_root(folder)
            else:
                logger.warning("Watched folder %s is not reachable (unplugged drive or offline share?); kept, and skipped until it is back", folder)
                self.unreachable_roots.add(folder)
        for folder in folders:
            # Reconcile whatever changed while the app was closed, off the
            # request thread so startup isn't blocked by a large folder.
            self.enqueue(folder)

    def _skip_files_that_crashed(self) -> None:
        """Crash recovery, part two (2026-10-05): the file being read when the
        app died (the in-progress marker is still there) joins the saved list
        of files that crashed it; every file on that list whose bytes have not
        changed since is skipped by the scan and the watcher, and shown as
        failed with the reason."""
        marker, saved = self.config_dir / IN_PROGRESS_FILE, self.config_dir / CRASHED_FILES_FILE
        crashed: dict[str, str] = {}
        try:
            loaded = json.loads(saved.read_text()) if saved.exists() else {}
            crashed = {h: p for h, p in loaded.items() if isinstance(h, str) and isinstance(p, str)} if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            logger.warning("Could not read %s", saved, exc_info=True)
        try:
            last = json.loads(marker.read_text()) if marker.exists() else None
        except (OSError, ValueError):
            last = None  # the app died while writing the marker itself
        suspects_file = self.config_dir / SUSPECTS_FILE
        try:
            loaded = json.loads(suspects_file.read_text()) if suspects_file.exists() else {}
            folder_scan.SUSPECTS.update({h: p for h, p in loaded.items() if isinstance(h, str) and isinstance(p, str)} if isinstance(loaded, dict) else {})
        except (OSError, ValueError):
            logger.warning("Could not read %s", suspects_file, exc_info=True)
        if isinstance(last, dict) and isinstance(last.get("hash"), str) and isinstance(last.get("path"), str) and last["hash"] not in folder_scan.SUSPECTS:
            # First time: closing the window mid-file looks exactly like a crash.
            logger.info("IntelliFile stopped while reading %s at the last run; it is read again", last["path"])
            folder_scan.SUSPECTS[last["hash"]] = last["path"]
            folder_scan.save_suspects()
        elif isinstance(last, dict) and isinstance(last.get("hash"), str) and isinstance(last.get("path"), str):
            logger.warning("IntelliFile closed while reading %s at the last two runs; it is skipped until it changes", last["path"])
            folder_scan.SUSPECTS.pop(last["hash"], None)
            folder_scan.save_suspects()
            crashed.pop(last["hash"], None)
            crashed[last["hash"]] = last["path"]
            crashed = dict(list(crashed.items())[-MAX_CRASHED_FILES:])
            try:
                saved.write_text(json.dumps(crashed, indent=2))
            except OSError:
                logger.warning("Could not save %s", saved, exc_info=True)
        marker.unlink(missing_ok=True)
        store = self.indexer.file_record_store
        for content_hash, path in crashed.items():
            POISONED[content_hash] = path
            record = store.get_by_path(path)
            if record is not None and not record.deleted and record.hash == content_hash and not record.indexed:
                note_failure(Path(path), folder_scan.ClosedWhileReading())

    def stop(self) -> None:
        self.gate.set()  # let workers reach their stop sentinel
        self.watcher.stop()
        self.job_queue.stop()
        if self.power is not None:
            self.power.stop()

    # ----- Phase 10: power awareness -----

    def attach_power(self, monitor: PowerMonitor) -> None:
        self.power = monitor
        monitor.on_change(lambda old, new: self.apply_power())
        self.apply_power()

    def apply_power(self) -> None:
        """Open or close the gate from the current power state and settings.
        Called on every power change and every settings change."""
        settings = self.settings_reader() or {}
        mode = resource_mode(settings.get("resource_mode", "balanced"))
        state = self.power.state() if self.power is not None else None
        reason = None
        if state is not None and not mode.ignore_power:
            if state.on_battery and settings.get("pause_on_battery", False):
                reason = "on battery power"
            elif state.low_power_mode and settings.get("pause_on_low_power", True):
                reason = "low power mode is on"
            elif state.on_battery and mode.defer_live_jobs_on_battery:
                reason = "battery saver mode, on battery"
        self.job_queue.inter_job_sleep = mode.inter_file_sleep
        changed = reason != self.paused_reason
        self.paused_reason = reason
        if reason is None:
            self.gate.set()
        else:
            self.gate.clear()
        if changed:
            logger.info("indexing %s", f"paused: {reason}" if reason else "resumed")

    def _throttle(self) -> float:
        settings = self.settings_reader() or {}
        return resource_mode(settings.get("resource_mode", "balanced")).inter_file_sleep

    def power_snapshot(self) -> dict:
        settings = self.settings_reader() or {}
        state = self.power.state().as_dict() if self.power is not None else None
        return {
            **(state or {"has_battery": False, "on_battery": False, "percent": None, "low_power_mode": False, "cpu_percent": 0.0, "app_cpu_percent": 0.0}),
            "paused": self.paused_reason is not None,
            "paused_reason": self.paused_reason,
            "mode": settings.get("resource_mode", "balanced"),
            "pending_jobs": self.job_queue.pending,
        }

    def roots(self) -> list[str]:
        """Every watched folder, reachable or not — what is saved and shown."""
        return sorted(set(self.watcher.watched_roots) | self.unreachable_roots)

    def watch(self, folder: str) -> None:
        if not Path(folder).is_absolute():
            return
        self.watcher.add_root(folder)
        if folder in self.watcher.watched_roots:
            self.unreachable_roots.discard(folder)
        save_watched_folders(self.config_dir, self.roots())

    # ----- Phase 12: access policy and single files -----

    # Set by the app: returns True when the access policy allows indexing.
    indexing_allowed = staticmethod(lambda: True)

    def watch_file(self, path: str) -> None:
        self.watcher.add_file(path)
        save_watched_files(self.config_dir, self.watcher.watched_files)

    def index_single_file(self, path: str) -> None:
        """Index one file now (background job), watching it for changes."""
        if not self.indexing_allowed():
            return
        p = Path(path)
        if not p.is_file():
            return
        from .files.identity import build_file_record, compute_file_hash

        store = self.indexer.file_record_store
        existing = store.get_by_path(str(p))
        try:
            file_hash = compute_file_hash(p)
        except OSError:
            return
        if existing is not None and existing.hash == file_hash and existing.indexed and not existing.deleted:
            return
        record = build_file_record(p, file_id=existing.file_id if existing else None, file_hash=file_hash, indexed=False)
        store.upsert(record)
        self.job_queue.submit(Job(kind="index", path=str(p), file_id=record.file_id))

    def forget_file(self, path: str) -> bool:
        self.watcher.remove_file(path)
        save_watched_files(self.config_dir, self.watcher.watched_files)
        store = self.indexer.file_record_store
        record = store.get_by_path(path)
        if record is None:
            return False
        with self.write_lock:
            store.mark_deleted(path)
            cleanup_tombstones(self.indexer, visual_indexer=self.visual_indexer)
        return True

    def forget_everything(self) -> int:
        """Downgrading access: stop watching all folders and files and drop
        every record. Returns how many files were removed."""
        removed = 0
        for folder in self.roots():
            removed += self.forget(folder)
        for file in list(self.watcher.watched_files):
            removed += 1 if self.forget_file(file) else 0
        with self.write_lock:
            store = self.indexer.file_record_store
            for record in store.list_active():
                store.mark_deleted(record.path)
                removed += 1
            cleanup_tombstones(self.indexer, visual_indexer=self.visual_indexer)
        return removed

    # ----- background indexing job -----

    def enqueue(self, folder: str, force: bool = False) -> None:
        """Index `folder` in the background; /status reports progress.
        Folders queue behind the running one (single lane — see _handle_job).
        A folder that is currently being scanned is still queued once more
        (the running scan may have started before the change that prompted
        this request); an already-pending folder is not queued twice, but a
        forced request upgrades the pending one."""
        if not self.indexing_allowed():
            logger.info("indexing of %s refused: file access is %s", folder, "denied")
            return
        with self._job_lock:
            if force:
                self._force.add(folder)
            if folder in self._pending:
                return
            self._pending.append(folder)
            self.job.queued = list(self._pending)
            if self._runner is None or not self._runner.is_alive():
                self._runner = threading.Thread(target=self._run_jobs, daemon=True)
                self._runner.start()

    def _run_jobs(self) -> None:
        while True:
            with self._job_lock:
                if not self._pending:
                    return
                folder = self._pending.pop(0)
                force = folder in self._force
                self._force.discard(folder)
                self.job = IndexJob(state="running", folder=folder, started_at=time.time(), queued=list(self._pending))

            def progress(done: int, total: int, current: str | None, failed: int) -> None:
                self.job.done, self.job.total, self.job.current_file, self.job.failed = done, total, current, failed

            try:
                index_folder(self.indexer, folder, visual_indexer=self.visual_indexer, progress=progress, force=force, lock=self.write_lock, gate=self.gate, throttle=self._throttle)
                if folder in self.unreachable_roots and Path(folder).is_dir():
                    self.watch(folder)  # the drive is back: watch it from now on
                self.job.state = "done"
                self.on_index_changed()
                self.on_folder_indexed(folder)
            except Exception as e:
                logger.exception("Indexing failed for %s", folder)
                # A sentence for the Status screen (2026-10-05), not the bare
                # exception text; the traceback is in the log above.
                detail = (str(e).strip() or type(e).__name__).rstrip(".")
                self.job.state, self.job.error = "failed", f"The scan stopped because of an unexpected problem: {detail}."
            self.job.finished_at = time.time()
            self.job.current_file = None

    def job_snapshot(self) -> dict:
        snapshot = asdict(self.job)
        snapshot["paused_reason"] = self.paused_reason if snapshot["state"] == "running" or snapshot["queued"] else None
        snapshot["recent_failures"] = list(RECENT_FAILURES[-10:])
        # Left alone on purpose in this folder's scan, every kind always present:
        # {"too_large": n, "online_only": n, "path_too_long": n}; and the last
        # skipped files, scan and watcher alike (2026-10-05).
        counts = SKIPPED.get(self.job.folder) or {}
        snapshot["skipped"] = {kind: counts.get(kind, 0) for kind in SKIP_KINDS}
        snapshot["recent_skips"] = [{"path": s["path"], "reason": s["reason"]} for s in RECENT_SKIPS[-10:]]
        snapshot["recovered_at_start"] = getattr(self, "recovered_at_start", 0)
        return snapshot

    # ----- folder management -----

    def forget(self, folder: str) -> int:
        """Stop watching `folder` and purge everything indexed under it.
        Returns how many files were removed."""
        self.watcher.remove_root(folder)
        self.unreachable_roots.discard(folder)
        save_watched_folders(self.config_dir, self.roots())
        prefix = str(Path(folder)) + os.sep
        # A watched folder inside this one keeps its files (2026-09-21).
        keep = [str(Path(r)) + os.sep for r in self.roots() if r.startswith(prefix)]
        store = self.indexer.file_record_store
        removed = 0
        with self.write_lock:
            for record in store.list_active():
                if record.path.startswith(prefix) and not any(record.path.startswith(k) for k in keep):
                    store.mark_deleted(record.path)
                    removed += 1
            cleanup_tombstones(self.indexer, visual_indexer=self.visual_indexer)
        return removed

    def reindex(self, folder: str) -> None:
        """Force a full re-embed of `folder`, in place — see index_folder(force=True)
        for why the records are no longer purged first."""
        self.enqueue(folder, force=True)

    def _exists(self, path: str, is_dir: bool, default: bool) -> bool:
        """Whether a watched folder/file is there, never waiting on the disk:
        the last answer (or `default` before the first) while a background
        thread re-checks it once REACHABILITY_TTL has passed."""
        with self._reach_lock:
            cached = self._reachable.get(path)
            if (cached is None or time.monotonic() - cached[0] > REACHABILITY_TTL) and path not in self._probing:
                self._probing.add(path)
                threading.Thread(target=self._probe, args=(path, is_dir), daemon=True).start()
        return cached[1] if cached is not None else default

    def _probe(self, path: str, is_dir: bool) -> None:
        try:
            found = Path(path).is_dir() if is_dir else Path(path).is_file()
        except OSError:
            found = False
        with self._reach_lock:
            self._reachable[path] = (time.monotonic(), found)
            self._probing.discard(path)

    def folder_stats(self) -> list[dict]:
        """Per watched folder: how many documents / photos / audio files are
        indexed under it — the numbers the Folders screen shows."""
        from .files.discovery import AUDIO_EXTENSIONS

        roots = self.roots()
        counts = {root: {"documents": 0, "photos": 0, "videos": 0, "audio": 0} for root in roots}
        # Deepest root first, so a file under a watched sub-folder counts
        # there and not under its watched parent too (2026-09-21).
        by_depth = sorted(roots, key=lambda r: -len(Path(r).parts))
        for record in self.indexer.file_record_store.list_active():
            for root in by_depth:
                if record.path.startswith(str(Path(root)) + os.sep):
                    ext = Path(record.path).suffix.lower()
                    kind = "photos" if ext in IMAGE_EXTENSIONS else "videos" if ext in VIDEO_EXTENSIONS else "audio" if ext in AUDIO_EXTENSIONS else "documents"
                    counts[root][kind] += 1
                    break
        entries = [
            {
                "path": root,
                "kind": "folder",
                "exists": self._exists(root, True, default=root not in self.unreachable_roots),
                "indexing": self.job.state == "running" and self.job.folder == root,
                "queued": root in self.job.queued,
                **counts[root],
            }
            for root in roots
        ]
        for file in self.watcher.watched_files:
            record = self.indexer.file_record_store.get_by_path(file)
            ext = Path(file).suffix.lower()
            kind = "photos" if ext in IMAGE_EXTENSIONS else "videos" if ext in VIDEO_EXTENSIONS else "audio" if ext in AUDIO_EXTENSIONS else "documents"
            entries.append({
                "path": file, "kind": "file", "exists": self._exists(file, False, default=True), "indexing": False, "queued": False,
                "documents": 0, "photos": 0, "videos": 0, "audio": 0, **{kind: 1 if record is not None and record.indexed else 0},
            })
        return entries

    def _handle_job(self, job: Job) -> None:
        # One worker on purpose: the indexers share SQLite/LanceDB handles
        # that aren't safe to hammer from several threads at once, and a
        # single background lane is plenty for a person editing files.
        if job.kind == "index" and not self.indexing_allowed():
            return
        with self.write_lock:
            if job.kind == "delete":
                self.indexer.delete_file(job.file_id)
                if self.visual_indexer is not None:
                    self.visual_indexer.delete_file(job.file_id)
                self.indexer.file_record_store.remove(job.file_id)
                return
            record = self.indexer.file_record_store.get_by_file_id(job.file_id)
            if record is None or record.deleted or record.hash in POISONED:
                return  # gone, or bytes the app died reading (see _skip_files_that_crashed)
            path = Path(job.path)
            if not path.exists():
                return  # deleted again before its turn; the delete job follows
            try:
                with folder_scan.reading(path, record.hash):
                    self._index_one(path, job, record)
            except Exception as e:
                # Unreadable / corrupt / locked: shown on Status, retried by
                # the next scan because `indexed` stays False (Phase 11).
                logger.exception("Live index failed for %s", path)
                self._failed_attempts.add(job.file_id)
                note_failure(path, e)
                return
            self._failed_attempts.discard(job.file_id)
            self.indexer.file_record_store.mark_indexed(job.file_id)
            self.on_index_changed()

    def _index_one(self, path: Path, job: Job, record) -> None:
        if self.visual_indexer is not None and path.suffix.lower() in VISUAL_EXTENSIONS:
            self.visual_indexer.index_visual_file(path, job.file_id, record.hash)
            _index_image_text(self.indexer, path, job.file_id)  # OCR text (improvement 4); never fails the file
        else:
            # A brand-new file has nothing to replace, so skip the two
            # deletes, one of them a full scan of the keyword table
            # (2026-10-05: 1,000 files copied into a watched folder
            # indexed at 0.6 files/s, slower with every file). Only
            # when no folder scan has indexed it meanwhile, and on the first
            # attempt only (see _failed_attempts).
            first_attempt = job.file_id not in self._failed_attempts
            self.indexer.index_file(path, job.file_id, record.hash, replace=not (job.new and not record.indexed and first_attempt))
