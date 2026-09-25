"""Per-key debouncing, per the PRD's File Modification Debouncing section:
repeated saves to the same file within the window reset the timer, and the
callback only fires once activity settles.

One scheduler thread serves every key. The first version started a
`threading.Timer` — a thread — per touched path, so copying ten thousand
files into a watched folder meant ten thousand threads (2026-09-21).
"""

import heapq
import logging
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

DEFAULT_DEBOUNCE_SECONDS = 30.0


class Debouncer:
    def __init__(self, callback: Callable[[str], None], delay_seconds: float = DEFAULT_DEBOUNCE_SECONDS):
        self._callback = callback
        self._delay_seconds = delay_seconds
        self._deadlines: dict[str, float] = {}  # key -> its current deadline
        self._heap: list[tuple[float, str]] = []  # (deadline, key); stale entries skipped on pop
        self._cv = threading.Condition()
        self._stopped = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="debouncer")
        self._thread.start()

    def trigger(self, key: str) -> None:
        with self._cv:
            deadline = time.monotonic() + self._delay_seconds
            self._deadlines[key] = deadline
            heapq.heappush(self._heap, (deadline, key))
            self._cv.notify()

    def cancel(self, key: str) -> None:
        with self._cv:
            self._deadlines.pop(key, None)

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._stopped and not self._heap:
                    self._cv.wait()
                if self._stopped:
                    return
                deadline, key = self._heap[0]
                if self._deadlines.get(key) != deadline:
                    heapq.heappop(self._heap)  # re-triggered or cancelled since; a newer entry exists (or none)
                    continue
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    self._cv.wait(timeout=remaining)
                    continue
                heapq.heappop(self._heap)
                self._deadlines.pop(key, None)
            try:
                self._callback(key)  # outside the lock: hashing a file takes time
            except Exception:
                # The scheduler must outlive any one path's failure (a file
                # that vanished between the event and the hash, say).
                logger.exception("Debounced handler failed for %s", key)

    def shutdown(self) -> None:
        with self._cv:
            self._stopped = True
            self._deadlines.clear()
            self._heap.clear()
            self._cv.notify_all()
        self._thread.join(timeout=2)
