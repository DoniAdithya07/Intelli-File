"""Ties discovery + hashing + debouncing + the file-record store + the job
queue together into one watch service. This is the Phase 2 deliverable:
select folders in, a debounced stream of index/delete jobs out.
"""

import logging
import os
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from ..storage.sqlite_store import FileRecordStore
from .debounce import DEFAULT_DEBOUNCE_SECONDS, Debouncer
from .discovery import SUPPORTED_EXTENSIONS, discover_files, is_indexable
from .identity import build_file_record, compute_file_hash
from .jobs import Job, JobQueue


logger = logging.getLogger(__name__)


def _real(path) -> str:
    """os.path.realpath without Windows' extended-length prefix. While a
    folder is being deleted, realpath() of a path inside it can come back
    as \\\\?\\C:\\... (the parent is in the delete-pending state, so Python
    falls back to the raw final-path name); that spelling never matches a
    watched root, and the deletion was silently dropped. Found on the first
    Windows run, 2026-09-25, as a timing-dependent Phase 2 failure."""
    real = os.path.realpath(path)
    if real.startswith("\\\\?\\UNC\\"):
        return "\\\\" + real[8:]
    if real.startswith("\\\\?\\"):
        return real[4:]
    return real


class _Handler(FileSystemEventHandler):
    def __init__(self, service: "FileWatchService"):
        self._service = service

    def dispatch(self, event):
        # watchdog's dispatcher thread has no exception guard: one raised
        # event handler ends live watching for EVERY folder, silently,
        # until the app restarts (found 2026-09-21 via a rename that hit a
        # UNIQUE constraint). Log and carry on; the next scan reconciles.
        try:
            super().dispatch(event)
        except Exception:
            logger.exception("File event handling failed for %s", getattr(event, "src_path", event))

    def _supported(self, path: str, gone: bool = False) -> bool:
        # `gone`: a delete / move-away event names a path that no longer
        # exists, so the size check must not run (it stats the file — and
        # every deletion was being dropped on that, 2026-09-21 macOS pass).
        return self._service.is_indexable_here(Path(path), must_exist=not gone)

    def _p(self, event_path) -> str:
        """The event's path spelled under the registered root, so records
        keyed by the scan (/var/…) and by the watcher (/private/var/…)
        are the same record."""
        path = event_path.decode(errors="ignore") if isinstance(event_path, bytes) else str(event_path)
        return self._service.canonical(path)

    def on_created(self, event):
        if not event.is_directory and self._supported(event.src_path):
            self._service._on_modified_or_created(self._p(event.src_path))

    def on_modified(self, event):
        if not event.is_directory and self._supported(event.src_path):
            self._service._on_modified_or_created(self._p(event.src_path))

    def on_deleted(self, event):
        if event.is_directory:
            # FSEvents reports a removed folder as one directory event; the
            # files inside get no events of their own.
            self._service._on_directory_gone(self._p(event.src_path))
        elif self._supported(event.src_path, gone=True):
            self._service._on_deleted(self._p(event.src_path))

    def on_moved(self, event):
        if event.is_directory:
            self._service._on_directory_moved(self._p(event.src_path), self._p(event.dest_path))
        else:
            self._service._on_moved(self._p(event.src_path), self._p(event.dest_path))


class FileWatchService:
    def __init__(
        self,
        record_store: FileRecordStore,
        job_queue: JobQueue,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
        extensions: set[str] | None = None,
    ):
        self._record_store = record_store
        self._job_queue = job_queue
        self._debouncer = Debouncer(self._process_debounced_path, delay_seconds=debounce_seconds)
        self._observer = Observer()
        self._handler = _Handler(self)
        self._watched: set[str] = set()
        # Phase 12 "limited" access: single files, watched through their
        # parent directory (non-recursively) with events filtered to them.
        self._watched_files: set[str] = set()
        self._file_parents: dict[str, int] = {}  # parent dir -> how many watched files it holds
        # Photos join the watch set only when the app has a CLIP model
        # (see folder_scan.py for the same rule on the scan side).
        self.extensions = extensions if extensions is not None else SUPPORTED_EXTENSIONS

    def initial_scan(self, root_paths: list[str]) -> None:
        """One-time recursive scan of the selected folders, run at startup
        (or whenever a new folder is added)."""
        for path in discover_files(root_paths, extensions=self.extensions):
            self._reconcile_file(path)

    def start_watching(self, root_paths: list[str]) -> None:
        for root in root_paths:
            self.add_root(root)

    def add_root(self, root: str) -> None:
        """Watch one more folder. Safe to call at any time, including after
        the observer is already running — the live app adds folders one
        at a time as the user picks them."""
        if root in self._watched or not Path(root).is_dir():
            return
        self._observer.schedule(self._handler, root, recursive=True)
        self._watched.add(root)
        if not self._observer.is_alive():
            self._observer.start()

    def remove_root(self, root: str) -> None:
        """Stop watching one folder (the user removed it in the UI)."""
        if root not in self._watched:
            return
        self._watched.discard(root)
        for watch in list(self._observer._watches):  # watchdog keeps them keyed by path
            if watch.path == root:
                self._observer.unschedule(watch)

    @property
    def watched_roots(self) -> list[str]:
        return sorted(self._watched)

    @property
    def watched_files(self) -> list[str]:
        return sorted(self._watched_files)

    def add_file(self, path: str) -> None:
        p = Path(path)
        if path in self._watched_files or not p.is_file():
            return
        self._watched_files.add(path)
        parent = str(p.parent)
        if parent not in self._file_parents and parent not in self._watched:
            self._observer.schedule(self._handler, parent, recursive=False)
        self._file_parents[parent] = self._file_parents.get(parent, 0) + 1
        if not self._observer.is_alive():
            self._observer.start()

    def remove_file(self, path: str) -> None:
        if path not in self._watched_files:
            return
        self._watched_files.discard(path)
        parent = str(Path(path).parent)
        self._file_parents[parent] = self._file_parents.get(parent, 1) - 1
        if self._file_parents[parent] <= 0:
            self._file_parents.pop(parent, None)
            if parent not in self._watched:
                for watch in list(self._observer._watches):
                    if watch.path == parent:
                        self._observer.unschedule(watch)

    def canonical(self, path: str) -> str:
        """Re-spell a real path under the root (or watched file) as it was
        registered; unchanged when no root matches."""
        real = _real(path)
        for root in sorted(self._watched, key=len, reverse=True):
            rr = _real(root)
            if real == rr or real.startswith(rr + os.sep):
                return root + real[len(rr):]
        for file in self._watched_files:
            if _real(file) == real:
                return file
        return path

    def is_indexable_here(self, path: Path, must_exist: bool = True) -> bool:
        """The scan's file rule plus its directory pruning, relative to the
        watched root this path falls under (the deepest one when roots
        nest). A path under a parent watched only for single files counts
        only if it is one of those files."""
        # watchdog reports the REAL path (macOS: /private/var/… for a root
        # registered as /var/…), so roots and files are compared by realpath
        # (found 2026-09-21: new files in a symlinked folder were ignored).
        real = Path(_real(path))
        roots = [Path(_real(r)) for r in self._watched if real.is_relative_to(Path(_real(r)))]
        if not roots:
            real_files = {_real(f) for f in self._watched_files}
            return str(real) in real_files and is_indexable(path, self.extensions, must_exist=must_exist)
        root = max(roots, key=lambda r: len(r.parts))
        return is_indexable(real, self.extensions, root=root, must_exist=must_exist)

    def stop(self) -> None:
        if self._observer.is_alive():
            self._observer.stop()
            self._observer.join(timeout=5)
        self._debouncer.shutdown()

    # --- event handling ---

    def _on_modified_or_created(self, path: str) -> None:
        self._debouncer.trigger(path)

    def _process_debounced_path(self, path: str) -> None:
        self._reconcile_file(Path(path))

    def _reconcile_file(self, path: Path) -> None:
        if not path.exists():
            return
        existing = self._record_store.get_by_path(str(path))
        try:
            new_hash = compute_file_hash(path)
        except OSError:
            return  # gone or unreadable right now; the next event or scan will see it
        if existing is not None and existing.hash == new_hash and not existing.deleted and existing.indexed:
            return  # content unchanged and already active — nothing to do
        file_id = existing.file_id if existing is not None else None
        record = build_file_record(path, file_id=file_id, file_hash=new_hash, indexed=False)
        self._record_store.upsert(record)
        self._job_queue.submit(Job(kind="index", path=str(path), file_id=record.file_id))

    def _on_deleted(self, path: str) -> None:
        self._debouncer.cancel(path)
        record = self._record_store.mark_deleted(path)
        if record is not None:
            self._job_queue.submit(Job(kind="delete", path=path, file_id=record.file_id))

    def _on_directory_gone(self, dir_path: str) -> None:
        """Every record under a removed directory is tombstoned."""
        prefix = dir_path.rstrip(os.sep) + os.sep
        for record in self._record_store.list_active():
            if record.path.startswith(prefix):
                self._on_deleted(record.path)

    def _on_directory_moved(self, src_dir: str, dest_dir: str) -> None:
        """A renamed/moved folder: records move with it (or are tombstoned
        when it left the watched tree). macOS synthesises per-file moves
        too; a record already moved is simply not found under the old
        prefix any more, so this is safe to run after them."""
        prefix = src_dir.rstrip(os.sep) + os.sep
        dest_prefix = dest_dir.rstrip(os.sep) + os.sep
        for record in self._record_store.list_active():
            if not record.path.startswith(prefix):
                continue
            new_path = dest_prefix + record.path[len(prefix):]
            if self.is_indexable_here(Path(new_path), must_exist=False):
                self._record_store.rename(record.path, new_path)
            else:
                self._on_deleted(record.path)

    def _on_moved(self, src_path: str, dest_path: str) -> None:
        # A pure rename doesn't change content, so per the PRD's Rename
        # Handling section we update the path and skip re-embedding. If the
        # move also changed the content, the next MODIFY event catches it.
        self._debouncer.cancel(src_path)
        dest_supported = self.is_indexable_here(Path(dest_path))
        existing = self._record_store.get_by_path(src_path)
        if existing is None and not dest_supported:
            return
        if existing is not None and dest_supported:
            self._record_store.rename(src_path, dest_path)
        elif existing is not None:
            # Renamed to an extension we don't index (notes.txt -> notes.bak):
            # from the index's point of view the document is gone.
            self._on_deleted(src_path)
        elif dest_supported:
            # The reverse: something we ignored just became indexable.
            self._on_modified_or_created(dest_path)
