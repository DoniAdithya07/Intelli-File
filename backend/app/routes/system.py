import time

from fastapi import APIRouter, Request

from .. import offline_guard
from ..extraction import ocr
from ..model_locations import CLIP_MODEL_DIR, RERANKER_MODEL_DIR, WHISPER_MODEL_DIR

router = APIRouter()


@router.get("/offline-guard")
def offline_guard_endpoint():
    """Whether the offline guard is on and how many network attempts it
    has refused since startup — zero is the number the live test wants."""
    return offline_guard.status()


def _dir_size(path) -> int:
    # LanceDB writes and removes temporary manifest files while indexing, so a
    # file listed by rglob can be gone by the time it is stat'ed; that raced
    # /status into a 500 during a 1,000-file scan (2026-10-05). Skip it.
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


# The index size is a full directory walk (14 k files on the dev index
# before compaction) and the UI polls /status every 700 ms while indexing
# — measured 80–200 ms per poll, contending with the indexer's own writes
# (2026-09-21). It changes slowly, so it is recomputed at most every
# INDEX_SIZE_TTL seconds.
INDEX_SIZE_TTL = 15.0
_index_size_cache: tuple[float, int] | None = None


def _index_size_bytes(dirs: dict) -> int:
    global _index_size_cache
    now = time.monotonic()
    if _index_size_cache is None or now - _index_size_cache[0] > INDEX_SIZE_TTL:
        size = _dir_size(dirs["vector_index"]) + _dir_size(dirs["keyword_index"]) + _dir_size(dirs["database"])
        _index_size_cache = (now, size)
    return _index_size_cache[1]


# What a missing optional part costs the user, in their words — logged at
# startup and listed on /status (2026-10-04: a missing CLIP or Whisper
# folder, or no Windows OCR, used to be silent; photo or voice search just
# did nothing).
FEATURE_PROBLEMS = {
    "photos_videos": f"Photo and video search is off: the image model folder (models\\{CLIP_MODEL_DIR.name}) is missing. Extract IntelliFile-windows.zip again, completely, then restart IntelliFile.",
    "voice": f"Voice search and audio transcription are off: the speech model folder (models\\{WHISPER_MODEL_DIR.name}) is missing. Extract IntelliFile-windows.zip again, completely, then restart IntelliFile.",
    "ocr": "Text in scans and screenshots is not read: Windows OCR is not available on this computer (it needs a Windows display language with OCR installed).",
}


def feature_problems(features: dict[str, bool]) -> list[str]:
    """One sentence per feature that is off, in FEATURE_PROBLEMS order."""
    return [sentence for name, sentence in FEATURE_PROBLEMS.items() if not features.get(name, True)]


@router.get("/status")
def status_endpoint(request: Request):
    """Everything the Status and Folders screens show. All real numbers."""
    state = request.app.state
    live = state.live_indexing
    folders = live.folder_stats()
    totals = {k: sum(f[k] for f in folders) for k in ("documents", "photos", "videos", "audio")}
    dirs = state.dirs
    return {
        "engine": "online",
        "features": getattr(state, "features", {}),
        "startup_problems": getattr(state, "startup_problems", []),
        "job": live.job_snapshot(),
        "folders": folders,
        "totals": {**totals, "files": sum(totals.values())},
        "index_size_bytes": _index_size_bytes(dirs),
        "power": live.power_snapshot(),
        "access": state.access.as_dict(),
        "activity": {
            "enabled": state.settings.get("remember_activity"),
            "events": state.usage_store.count(),
            "session": state.usage_store.current_session(),
        },
        "models": {
            "text": {"name": state.search_service.model.name, "dimension": 384, "provider": state.search_service.model.active_provider},
            "photos": (
                {"name": CLIP_MODEL_DIR.name, "precision": state.visual_indexer.clip_model.precision, "dimension": state.visual_indexer.clip_model.dimension}
                if state.visual_indexer is not None else None
            ),
            "speech": {"name": WHISPER_MODEL_DIR.name} if state.transcriber is not None else None,
            "reranker": {"name": RERANKER_MODEL_DIR.name} if state.search_service.reranker is not None else None,
            "ocr": {"name": "Windows OCR", "language": ocr_language} if (ocr_language := ocr.language()) else None,
        },
    }
