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
from .indexing.folder_scan import RECENT_FAILURES, _note_failure as note_failure
from .power import PowerMonitor, resource_mode

logger = logging.getLogger(__name__)

WATCHED_FOLDERS_FILE = "watched_folders.json"
WATCHED_FILES_FILE = "watched_files.json"  # Phase 12 "limited" access: single files


def load_watched_folders(config_dir: Path) -> list[str]:
    path = config_dir / WATCHED_FOLDERS_FILE
    if not path.exists():
        return []
    try:
        folders = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    return [f for f in folders if isinstance(f, str) and Path(f).is_absolute() and Path(f).is_dir()]


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
        self.extensions = extensions
        self.job = IndexJob()
        self._job_lock = threading.Lock()
        self._pending: list[str] = []
        self._force: set[str] = set()  # folders whose queued scan must re-embed everything
        self._runner: threading.Thread | None = None
        # Two threads write to the index: the folder-scan runner and the
        # watcher's job worker. The stores lock each call, but a file's
        # index step is several calls (record upsert, chunk delete, chunk
        # add) — a scan and a watcher event on the same file could
        # interleave them (2026-09-21). One lock, held per file by the scan
        # and per job by the worker, keeps each file's step atomic while
        # still letting watcher jobs run between a scan's files.
        self.write_lock = threading.RLock()
        # Called after every scan / job so caches derived from chunk
        # vectors (Phase 17's per-file centroids) are dropped.
        self.on_index_changed = lambda: None

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
        for file in load_watched_files(self.config_dir):
            self.watcher.add_file(file)
            self.index_single_file(file)
        # load_watched_folders() drops folders that no longer exist (e.g. the
        # temp folder a test run indexed); persist the pruned list.
        save_watched_folders(self.config_dir, folders)
        for folder in folders:
            self.watcher.add_root(folder)
        for folder in folders:
            # Reconcile whatever changed while the app was closed, off the
            # request thread so startup isn't blocked by a large folder.
            self.enqueue(folder)

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
            if state.on_battery and settings.get("pause_on_battery", True):
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

    def watch(self, folder: str) -> None:
        if not Path(folder).is_absolute():
            return
        self.watcher.add_root(folder)
        save_watched_folders(self.config_dir, self.watcher.watched_roots)

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
        for folder in list(self.watcher.watched_roots):
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
                self.job.state = "done"
                self.on_index_changed()
            except Exception as e:
                logger.exception("Indexing failed for %s", folder)
                self.job.state, self.job.error = "failed", str(e)
            self.job.finished_at = time.time()
            self.job.current_file = None

    def job_snapshot(self) -> dict:
        snapshot = asdict(self.job)
        snapshot["paused_reason"] = self.paused_reason if snapshot["state"] == "running" or snapshot["queued"] else None
        snapshot["recent_failures"] = list(RECENT_FAILURES[-10:])
        snapshot["recovered_at_start"] = getattr(self, "recovered_at_start", 0)
        return snapshot

    # ----- folder management -----

    def forget(self, folder: str) -> int:
        """Stop watching `folder` and purge everything indexed under it.
        Returns how many files were removed."""
        self.watcher.remove_root(folder)
        save_watched_folders(self.config_dir, self.watcher.watched_roots)
        prefix = str(Path(folder)) + os.sep
        # A watched folder inside this one keeps its files (2026-09-21).
        keep = [str(Path(r)) + os.sep for r in self.watcher.watched_roots if r.startswith(prefix)]
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

    def folder_stats(self) -> list[dict]:
        """Per watched folder: how many documents / photos / audio files are
        indexed under it — the numbers the Folders screen shows."""
        from .files.discovery import AUDIO_EXTENSIONS

        roots = self.watcher.watched_roots
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
                "exists": Path(root).is_dir(),
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
                "path": file, "kind": "file", "exists": Path(file).is_file(), "indexing": False, "queued": False,
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
            if record is None or record.deleted:
                return
            path = Path(job.path)
            if not path.exists():
                return  # deleted again before its turn; the delete job follows
            try:
                if self.visual_indexer is not None and path.suffix.lower() in VISUAL_EXTENSIONS:
                    self.visual_indexer.index_visual_file(path, job.file_id, record.hash)
                else:
                    self.indexer.index_file(path, job.file_id, record.hash)
            except Exception as e:
                # Unreadable / corrupt / locked: shown on Status, retried by
                # the next scan because `indexed` stays False (Phase 11).
                logger.exception("Live index failed for %s", path)
                note_failure(path, e)
                return
            self.indexer.file_record_store.mark_indexed(job.file_id)
            self.on_index_changed()
