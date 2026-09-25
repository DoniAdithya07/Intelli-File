"""Job queue + worker pool: file events land here as jobs, workers process
them. The actual "process" step (parse/chunk/embed) doesn't exist yet —
that's Phase 3/4 — so the handler passed in for now is a stand-in.
"""

import logging
import queue
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Job:
    kind: str  # "index" | "delete"
    path: str
    file_id: str | None = None


class JobQueue:
    def __init__(self, handler, num_workers: int = 2, gate: threading.Event | None = None):
        self._queue: "queue.Queue[Job | None]" = queue.Queue()
        self._handler = handler
        self._num_workers = num_workers
        self._threads: list[threading.Thread] = []
        # Phase 10: workers wait on the gate before each job. Cleared =
        # paused (battery, low-power mode); set = running. The job in
        # progress always completes — pausing never leaves a half-written file.
        self._gate = gate if gate is not None else threading.Event()
        self._gate.set()
        self.inter_job_sleep = 0.0
        self._waiting = 0  # jobs taken off the queue but held at the gate
        self._waiting_lock = threading.Lock()

    def start(self) -> None:
        for _ in range(self._num_workers):
            t = threading.Thread(target=self._worker_loop, daemon=True)
            t.start()
            self._threads.append(t)

    def submit(self, job: Job) -> None:
        self._queue.put(job)

    def _worker_loop(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                self._queue.task_done()
                break
            with self._waiting_lock:
                self._waiting += 1
            self._gate.wait()
            with self._waiting_lock:
                self._waiting -= 1
            try:
                self._handler(job)
                if self.inter_job_sleep:
                    time.sleep(self.inter_job_sleep)
            except Exception:
                # A bad job must not kill the worker thread permanently —
                # log it and keep the pool alive for the next job.
                logger.exception("Job failed: %s", job)
            finally:
                self._queue.task_done()

    @property
    def pending(self) -> int:
        """Jobs not yet run: queued plus any held at the pause gate."""
        with self._waiting_lock:
            return self._queue.qsize() + self._waiting

    def join(self) -> None:
        """Block until every currently queued job has been processed."""
        self._queue.join()

    def stop(self) -> None:
        for _ in self._threads:
            self._queue.put(None)
        for t in self._threads:
            t.join(timeout=5)
        self._threads.clear()
