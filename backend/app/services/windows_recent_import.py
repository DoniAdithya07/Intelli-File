import logging
import threading
import time

from ..context import windows_recent

logger = logging.getLogger(__name__)


def import_windows_recent(state) -> None:
    settings = state.settings
    if not settings.get("import_windows_recent") or not settings.get("remember_activity"):
        return
    if not state.recent_import_lock.acquire(blocking=False):
        return  # one import at a time; the running one sees the same index
    try:
        state.recent_import["state"] = "running"
        result = windows_recent.import_recent(state.usage_store, state.indexer.file_record_store)
        state.recent_import = {"state": "done", "last": {**result, "at": time.time()}}
        logger.info("Windows recent items: %s", result)
    except Exception:
        logger.exception("Windows recent items import failed")
        state.recent_import = {"state": "failed", "last": state.recent_import.get("last")}
    finally:
        state.recent_import_lock.release()


def import_windows_recent_async(state) -> None:
    threading.Thread(target=import_windows_recent, args=(state,), daemon=True, name="recent-import").start()
