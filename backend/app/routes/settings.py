import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..context import windows_recent
from ..files.access import whole_computer_roots
from ..power import MODES
from ..services.access_exclusions import apply_access_exclusions
from ..services.windows_recent_import import import_windows_recent_async

logger = logging.getLogger(__name__)

router = APIRouter()


class AccessRequest(BaseModel):
    mode: str
    remove_index: bool = False


@router.get("/access")
def access_endpoint(request: Request):
    """The file-access policy (Phase 12): unset on first run until the
    user chooses all / limited / denied."""
    return request.app.state.access.as_dict()


@router.post("/access")
def set_access_endpoint(body: AccessRequest, request: Request):
    state = request.app.state
    access = state.access
    previous = access.mode
    try:
        access.set(body.mode)
    except ValueError as e:
        return {"error": str(e)}
    live = state.live_indexing
    apply_access_exclusions(body.mode)
    removed = 0
    if body.remove_index and body.mode != "all":
        removed = live.forget_everything()
    if body.mode == "all":
        for root in whole_computer_roots():
            live.watch(root)
            live.enqueue(root)
    elif previous == "all" and body.mode != "all":
        # Leaving "all": the whole-computer roots stop being watched
        # (their records stay unless remove_index asked otherwise).
        for root in whole_computer_roots():
            live.watcher.remove_root(root)
        from ..watch_setup import save_watched_folders
        save_watched_folders(live.config_dir, live.roots())
    return {**access.as_dict(), "previous": previous, "removed": removed}


@router.get("/settings")
def get_settings_endpoint(request: Request):
    return request.app.state.settings.all()


@router.get("/windows-recent")
def windows_recent_endpoint(request: Request):
    """Whether Windows' Recent items can be read here, and the last import."""
    state = request.app.state
    return {"available": windows_recent.recent_dir() is not None, "enabled": state.settings.get("import_windows_recent"), **state.recent_import}


class SettingsRequest(BaseModel):
    remember_activity: bool | None = None
    personalize: bool | None = None
    import_windows_recent: bool | None = None
    pause_on_battery: bool | None = None
    pause_on_low_power: bool | None = None
    resource_mode: str | None = None


@router.post("/settings")
def update_settings_endpoint(body: SettingsRequest, request: Request):
    state = request.app.state
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    if "resource_mode" in changes and changes["resource_mode"] not in MODES:
        return {"error": f"resource_mode must be one of {', '.join(MODES)}"}
    before = state.settings.get("import_windows_recent")
    updated = state.settings.update(changes)
    state.live_indexing.apply_power()  # a toggle takes effect immediately
    if changes.get("import_windows_recent") is True and not before:
        import_windows_recent_async(state)
    elif changes.get("import_windows_recent") is False and before:
        removed = state.usage_store.clear_source(windows_recent.SOURCE)
        state.recent_import = {"state": "off", "last": None}
        logger.info("Windows recent items switched off: %d imported events removed", removed)
    return updated


@router.get("/power")
def power_endpoint(request: Request):
    """Battery / low-power / CPU state and whether indexing is paused for it."""
    return request.app.state.live_indexing.power_snapshot()
