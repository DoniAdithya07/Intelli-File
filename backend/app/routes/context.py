from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..context import EVENT_KINDS, recommend
from ..context.sample_history import has_sample_history, load_sample_history, remove_sample_history
from ..paths import sample_folder

router = APIRouter()


# ----- Phase 16: activity memory & settings -----


class EventRequest(BaseModel):
    kind: str
    file_id: str | None = None
    path: str | None = None
    query: str | None = None
    meta: dict | None = None


@router.post("/events")
def record_event_endpoint(body: EventRequest, request: Request):
    """The UI reports what the user did with a result (open, reveal, click).
    Searches are recorded by /search itself."""
    state = request.app.state
    if body.kind not in EVENT_KINDS or body.kind == "query":
        return {"error": f"unknown event kind: {body.kind}"}
    if not state.settings.get("remember_activity"):
        return {"recorded": False, "reason": "activity memory is off"}
    event = state.usage_store.record(body.kind, file_id=body.file_id, path=body.path, query=body.query, meta=body.meta)
    return {"recorded": True, "event": event}


@router.get("/events")
def list_events_endpoint(request: Request, limit: int = 50, kind: str | None = None):
    state = request.app.state
    kinds = {kind} if kind in EVENT_KINDS else None
    return {
        "events": state.usage_store.recent(limit=max(1, min(limit, 500)), kinds=kinds),
        "total": state.usage_store.count(),
    }


@router.delete("/events")
def clear_events_endpoint(request: Request):
    """The Settings page's *Clear activity*."""
    return {"cleared": request.app.state.usage_store.clear()}


@router.get("/context")
def context_endpoint(request: Request):
    """The current working context (this session's queries, files and
    types) — what Phase 17's ranking and Phase 19's agent read."""
    return request.app.state.usage_store.current_session()


# ----- Phase 17: profile & recommendations -----


@router.get("/profile")
def profile_endpoint(request: Request, rebuild: bool = False):
    """What the app has learned: frequently used files, type mix, topics
    of interest, the weekday × time-of-day heatmap. The Insights screen.
    `sample_history`: the made-up history of the sample folder is loaded."""
    state = request.app.state
    return {**state.profile_builder.get(force=rebuild).as_dict(), "sample_history": has_sample_history(state.usage_store)}


@router.post("/profile/sample-history")
def load_sample_history_endpoint(request: Request):
    """For You's "Load sample history": four made-up weeks of opens and
    searches over the SAMPLE folder's files, labelled as such, so a fresh
    install can show what personalization does. 409 with a sentence when
    the sample folder is missing or not indexed yet."""
    state = request.app.state
    folder = sample_folder()
    if folder is None:
        raise HTTPException(status_code=409, detail="This copy of IntelliFile has no sample folder, so there is no sample history to load.")
    events = load_sample_history(state.usage_store, state.indexer.file_record_store, folder)
    if not events:
        raise HTTPException(status_code=409, detail="Index the sample folder first (Index, then Try the sample folder), then load its sample history.")
    state.profile_builder.get(force=True)  # For You shows it on its next read
    return {"events": events}


@router.delete("/profile/sample-history")
def delete_sample_history_endpoint(request: Request):
    """Removes only the made-up sample events; the user's own stay."""
    state = request.app.state
    removed = remove_sample_history(state.usage_store)
    state.profile_builder.get(force=True)
    return {"removed": removed}


@router.get("/recommendations")
def recommendations_endpoint(request: Request):
    """Files to suggest before any query is typed, each with its reason.
    Empty lists (and `enabled: false`) while personalization is off."""
    state = request.app.state
    if not state.settings.get("personalize"):
        return {"enabled": False, "cold_start": None, "now": None, "likely_next": [], "usual_now": [], "recent": []}
    profile = state.profile_builder.get()
    return {"enabled": True, **recommend(profile, state.usage_store, state.indexer.file_record_store)}
