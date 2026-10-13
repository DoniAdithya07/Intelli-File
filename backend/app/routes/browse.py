"""Read-only browsing for the Explorer-style window (2026-10-12).

Drives, quick access (Desktop, Documents, … plus the folders the user told
IntelliFile to index), one folder's contents, and recently used files. These
only read names, sizes and dates: nothing here opens a file, reads its
content or changes anything. Which paths may be browsed is decided in one
place, files/browsing.py (check_browse_path). Same token and Host guard as
every other route (middleware in main.py).
"""

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from ..context import windows_recent
from ..files import browsing
from ..files.browsing import BrowseError

router = APIRouter(prefix="/browse")
logger = logging.getLogger(__name__)

ACTIVITY_OWN = "Opened in IntelliFile"
ACTIVITY_WINDOWS = "Recently used"
# How many opens are read to fill one page of "recent": the same file is opened many times,
# and files that were deleted since are skipped.
RECENT_EVENT_WINDOW = 500


def _refuse(error: BrowseError) -> JSONResponse:
    return JSONResponse({"error": error.message}, status_code=error.status)


def _browsing_allowed(request: Request) -> BrowseError | None:
    # Listing a folder is not indexing, but the user who switched file access off meant it.
    if request.app.state.access.mode == "denied":
        return BrowseError(403, "File access is switched off. Allow it first in Settings, under File access.")
    return None


def _watched_roots(state) -> list[str]:
    return [os.path.normcase(os.path.normpath(r)) for r in state.live_indexing.roots()]


@router.get("/drives")
def drives_endpoint(request: Request):
    """{"drives": [{letter, path, label, type, ready, total_bytes, free_bytes}]}"""
    denied = _browsing_allowed(request)
    if denied:
        return _refuse(denied)
    return {"drives": browsing.list_drives()}


@router.get("/quick-access")
def quick_access_endpoint(request: Request):
    """{"items": [{name, path, kind: "known" | "pinned", icon}]}"""
    denied = _browsing_allowed(request)
    if denied:
        return _refuse(denied)
    live = request.app.state.live_indexing
    roots = live.roots()
    exists = lambda root: live._exists(root, True, default=root not in live.unreachable_roots)  # noqa: E731 — cached probe, never stalls on an offline share
    return {"items": browsing.quick_access(roots, exists)}


@router.get("/list")
def list_endpoint(
    request: Request,
    path: str,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """One folder: {path, name, parent, counts: {total, folders, files}, offset, limit, has_more,
    entries: [{name, path, is_dir, size, modified, kind, indexed, file_id}]}.
    Folders first, then by name; hidden and system entries are not listed."""
    denied = _browsing_allowed(request)
    if denied:
        return _refuse(denied)
    try:
        folder = browsing.check_browse_path(path)
        entries = browsing.scan_folder(folder)
    except BrowseError as error:
        return _refuse(error)
    page = entries[offset:offset + limit]
    state = request.app.state
    records = state.indexer.file_record_store.get_by_paths([e["path"] for e in page if not e["is_dir"]])
    roots = _watched_roots(state)
    folders = sum(1 for e in entries if e["is_dir"])
    return {
        "path": str(folder),
        "name": folder.name or str(folder),
        "parent": _parent_of(folder),
        "counts": {"total": len(entries), "folders": folders, "files": len(entries) - folders},
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < len(entries),
        "entries": [browsing.describe_entry(e, records.get(e["path"]), roots) for e in page],
    }


def _parent_of(folder: Path) -> str | None:
    """The folder above, or None at a drive root or when that folder may not be browsed."""
    if folder.parent == folder:
        return None
    try:
        return str(browsing.check_browse_path(str(folder.parent)))
    except BrowseError:
        return None


def _recent_candidates(state) -> dict[str, dict]:
    """Newest open per file, from IntelliFile's own memory and, when the user
    allowed it, Windows' Recent folder (which also knows files IntelliFile
    never indexed). Keyed by normalised path."""
    found: dict[str, dict] = {}

    def add(path: str, ts: float, activity: str) -> None:
        key = os.path.normcase(os.path.normpath(path))
        if key not in found or ts > found[key]["accessed"]:
            found[key] = {"path": path, "accessed": ts, "activity": activity}

    for event in state.usage_store.recent(limit=RECENT_EVENT_WINDOW, kinds={"file_opened"}):
        if event["path"]:
            imported = (event["meta"] or {}).get("source") == windows_recent.SOURCE
            add(event["path"], event["ts"], ACTIVITY_WINDOWS if imported else ACTIVITY_OWN)
    if state.settings.get("import_windows_recent"):
        for target, ts in windows_recent.read_recent():
            add(target, ts, ACTIVITY_WINDOWS)
    return found


@router.get("/recent")
def recent_endpoint(request: Request, limit: int = Query(30, ge=1, le=200)):
    """{"windows_recent": bool, "items": [{name, path, is_dir: false, size, modified, accessed,
    kind, indexed, file_id, activity}]}, newest first. `accessed` is when the file was last
    opened (epoch seconds); `activity` says where that was seen. Files that no longer exist
    or may not be browsed are left out."""
    denied = _browsing_allowed(request)
    if denied:
        return _refuse(denied)
    state = request.app.state
    candidates = sorted(_recent_candidates(state).values(), key=lambda c: -c["accessed"])
    roots = _watched_roots(state)
    items = []
    for candidate in candidates:
        if len(items) >= limit:
            break
        entry = _recent_entry(candidate)
        if entry is not None:
            items.append(entry)
    records = state.indexer.file_record_store.get_by_paths([i["path"] for i in items])
    return {
        "windows_recent": bool(state.settings.get("import_windows_recent")),
        "items": [{**browsing.describe_entry(i, records.get(i["path"]), roots), "accessed": i["accessed"], "activity": i["activity"]} for i in items],
    }


def _recent_entry(candidate: dict) -> dict | None:
    try:
        path = browsing.check_browse_path(candidate["path"])
        stat = path.stat()
    except (BrowseError, OSError):
        return None
    if not path.is_file():
        return None
    return {
        "name": path.name, "path": str(path), "is_dir": False, "size": stat.st_size, "modified": stat.st_mtime,
        "accessed": candidate["accessed"], "activity": candidate["activity"],
    }
