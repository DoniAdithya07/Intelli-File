import functools

from ..agent.missing import Known, why_missing
from ..files.browsing import KNOWN_FOLDERS, known_folder
from ..indexing.folder_scan import RECENT_FAILURES, RECENT_REMOVALS, RECENT_SKIPS


def gather_known(state) -> Known:
    """What the running app knows about its files: the records, the scan's
    stored reasons, the files taken out in this run, the watched folders and
    the indexing job. For Ask's index answers and GET /files/why-missing."""
    live = state.live_indexing
    store = state.indexer.file_record_store
    job = live.job
    indexing = job.state == "running" or bool(job.queued) or live.job_queue.pending > 0
    # While the scan is still listing a folder its total is 0: not known yet.
    waiting = max(job.total - job.done, 0) + live.job_queue.pending if job.state != "running" or job.total else None
    # A record parked by a rename keeps its old path before a NUL (FileRecordStore._vacate_path).
    tombstones = [{"path": r.path.split("\x00")[0]} for r in store.list_tombstoned()]
    return Known(
        active=[r.path for r in store.list_active()],
        unfinished=[r.path for r in store.list_unfinished()],
        failures=list(RECENT_FAILURES),
        skips=list(RECENT_SKIPS),
        deleted=tombstones + list(RECENT_REMOVALS),
        deleted_log=store.list_deletions(),
        folders=live.roots() + list(live.watcher.watched_files),
        extensions=frozenset(live.extensions),
        indexing=indexing,
        waiting=waiting if indexing else None,
        access_allowed=state.access.allows_indexing,
        places=own_folders(),
    )


@functools.lru_cache(maxsize=1)
def own_folders() -> dict[str, str]:
    """Where Windows keeps this user's Desktop, Documents, Downloads, ... (they can be
    redirected into OneDrive). Asked once: they do not move while the app runs."""
    found = {}
    for name in KNOWN_FOLDERS:
        try:
            path = known_folder(name)
        except OSError:
            path = None
        if path is not None:
            found[name.lower()] = str(path)
    return found


def explain_missing(state, name: str) -> dict:
    """Why a file is not in the results (GET /files/why-missing)."""
    return why_missing(name, gather_known(state))
