from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..files import discovery
from .common import safe_path

router = APIRouter()


class IndexFolderRequest(BaseModel):
    folder: str


@router.post("/index-folder")
def index_folder_endpoint(body: IndexFolderRequest, request: Request):
    state = request.app.state
    raw = body.folder.strip()
    # Path("").expanduser() is ".", which is_dir() accepts — so an empty
    # request used to index AND start watching the backend's own working
    # directory (found in the 2026-09-11 audit). Only absolute paths are
    # meaningful coming from a folder picker.
    if not raw:
        return {"error": "No folder given."}
    folder = safe_path(raw)
    if folder is None:
        return {"error": f"Folder path must be absolute: {raw}"}
    if not folder.is_dir():
        return {"error": f"Not a folder: {folder}"}
    if not state.access.allows_indexing:
        return {"error": "File access is switched off. Allow it first in Settings, under File access."}
    # Indexing runs in the background so a large folder never blocks the UI;
    # progress is read from /status. The folder is watched from now on.
    state.live_indexing.watch(str(folder))
    state.live_indexing.enqueue(str(folder))
    return {"queued": str(folder)}


@router.post("/forget-folder")
def forget_folder_endpoint(body: IndexFolderRequest, request: Request):
    state = request.app.state
    folder = safe_path(body.folder)
    if folder is None:
        return {"error": "Folder path must be absolute."}
    if str(folder) in state.live_indexing.watcher.watched_files:
        return {"removed": 1 if state.live_indexing.forget_file(str(folder)) else 0}
    removed = state.live_indexing.forget(str(folder))
    return {"removed": removed}


@router.post("/reindex-folder")
def reindex_folder_endpoint(body: IndexFolderRequest, request: Request):
    state = request.app.state
    folder = safe_path(body.folder)
    if folder is None or not folder.is_dir():
        return {"error": f"Not a folder: {body.folder}"}
    if not state.access.allows_indexing:
        return {"error": "File access is switched off. Allow it first in Settings, under File access."}
    state.live_indexing.watch(str(folder))
    state.live_indexing.reindex(str(folder))
    return {"queued": str(folder)}


@router.post("/index-file")
def index_file_endpoint(body: IndexFolderRequest, request: Request):
    """Phase 12 "limited" access: one file, indexed and watched for changes."""
    state = request.app.state
    path = safe_path(body.folder)
    if path is None or not path.is_file():
        return {"error": f"Not a file: {body.folder}"}
    if not state.access.allows_indexing:
        return {"error": "File access is switched off. Allow it first in Settings, under File access."}
    if not discovery.is_indexable(path, state.live_indexing.extensions):  # photos/videos count when CLIP is installed
        return {"error": f"IntelliFile does not index this kind of file: {path.name}"}
    state.live_indexing.watch_file(str(path))
    state.live_indexing.index_single_file(str(path))
    return {"queued": str(path)}


@router.post("/scan-all")
def scan_all_endpoint(request: Request):
    """Index > Scan now: every watched folder and file is checked again.
    Unchanged files are skipped by the usual change detection, so this is
    cheap when nothing moved."""
    state = request.app.state
    if not state.access.allows_indexing:
        return {"error": "File access is switched off. Allow it first in Settings, under File access."}
    live = state.live_indexing
    folders = live.roots()  # unreachable ones too: a drive plugged back in is picked up
    files = list(live.watcher.watched_files)
    for folder in folders:
        live.enqueue(folder)
    for file in files:
        live.index_single_file(file)
    return {"folders": len(folders), "files": len(files)}
