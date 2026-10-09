from fastapi import APIRouter, Request, UploadFile

from ..services.lazy_transcriber import SpeechModelUnavailable
from ..services.transcript_snap import filename_hints, snap_transcript
from .common import MAX_AUDIO_BYTES, MIN_AUDIO_BYTES, clamp_top_k
from .common import remember as _remember

router = APIRouter()


@router.get("/search")
def search_endpoint(q: str, request: Request, top_k: int = 10, mode: str = "auto", remember: bool = True):
    """`mode=auto` (default since Phase 18) lets the router choose the
    tier; smart / exact / keyword are the manual overrides. The `route`
    block reports what ran and what it cost — Objective 3's evidence."""
    state = request.app.state
    if not q.strip():
        return {"results": [], "route": None}
    if mode not in ("auto", "smart", "exact", "keyword"):
        mode = "auto"
    results, route = state.search_service.search_routed(q, top_k=clamp_top_k(top_k), mode=mode)
    # Queries are remembered here rather than by the UI so every entry
    # point (main window, overlay, tests) counts, with what they returned
    # and which route answered them (Phase 20 evaluates the router from this).
    # remember=false is for listings the user did not type, such as the
    # Photos page's newest-first grid.
    if remember:
        _remember(state, "query", query=q.strip(), meta={
            "mode": mode, "results": [r["file_id"] for r in results[:5]], "count": len(results),
            "route": route["tier"], "requested_tier": route["requested_tier"], "escalated": route["escalated"],
            "complexity": route["complexity"], "total_ms": route["total_ms"],
        })
    return {"results": results, "route": route}


@router.get("/suggest")
def suggest_endpoint(q: str, request: Request):
    """The 'Did you mean' chip: the query as `/search` would correct it, or null."""
    if not q.strip():
        return {"suggestion": None}
    return {"suggestion": request.app.state.search_service.suggest(q)}


@router.get("/search-visual")
def search_visual_endpoint(q: str, request: Request, top_k: int = 10, kind: str | None = None):
    state = request.app.state
    if state.visual_search_service is None:
        return {"error": "Visual search model not installed. Run scripts/download_clip_model.py."}
    if not q.strip():
        return {"results": []}
    check = state.visual_search_service.check_query(q)
    if check.unrecognized:
        # A word that is neither English nor one of the user's own: CLIP
        # would still embed it and return "strong" matches for gibberish.
        return {"results": [], "unrecognized": check.unrecognized}
    if kind not in (None, "photo", "video"):
        kind = None
    results = state.visual_search_service.search(check.text, top_k=clamp_top_k(top_k), kind=kind)
    _remember(state, "query", query=q.strip(), meta={"mode": "visual", "kind": kind, "results": [r["file_id"] for r in results[:5]], "count": len(results)})
    return {
        "results": results,
        "corrected_query": check.text if check.corrected else None,
    }


@router.get("/suggest-visual")
def suggest_visual_endpoint(q: str, request: Request):
    state = request.app.state
    if state.visual_search_service is None or not q.strip():
        return {"suggestion": None}
    return {"suggestion": state.visual_search_service.suggest(q)}


@router.post("/transcribe")
def transcribe_endpoint(audio: UploadFile, request: Request):
    # A plain `def`, like every other endpoint: FastAPI runs it in the
    # threadpool. As `async def` it ran Whisper on the event loop and
    # froze the whole server for the length of the transcription —
    # measured 2026-09-21: /health took 0.88 s during a transcription
    # against 0.6 ms idle, so the status poll and any search stalled too.
    state = request.app.state
    if state.transcriber is None:
        return {"error": "Voice search model not installed. Run scripts/download_whisper_model.py."}
    audio_bytes = audio.file.read(MAX_AUDIO_BYTES + 1)  # never more than the cap into memory
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        return {"error": f"That recording is too long for voice search (over {MAX_AUDIO_BYTES // (1024 * 1024)} MB). Click the microphone, say a short query, then click it again."}
    if len(audio_bytes) < MIN_AUDIO_BYTES:
        return {"error": "The recording was too short. Click the microphone, speak, then click it again to stop."}
    hints = filename_hints(state.indexer.file_record_store)
    try:
        heard = state.transcriber.transcribe(audio_bytes, vocabulary_hint=hints)
    except SpeechModelUnavailable as e:
        return {"error": f"Voice search is unavailable: {e}"}
    except Exception as e:
        # PyAV's decode errors ("Invalid data found when processing input:
        # '<none>'", "tuple index out of range" for a non-audio upload) mean
        # nothing to a user; the actionable fact is the same for all of them.
        return {"error": f"That recording could not be read as audio. Please try again. ({type(e).__name__})"}
    return snap_transcript(state, heard)
