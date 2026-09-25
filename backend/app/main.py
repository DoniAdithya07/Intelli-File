import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

import json
import os
import queue
import secrets
import threading

from fastapi import FastAPI, Request, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import logging_setup, offline_guard
from .agent import Agent, LocalLLM, Toolbox, find_model_file
from .context import EVENT_KINDS, ProfileBuilder, Settings, UsageStore, recommend
from .embeddings.clip_model import ClipModel
from .embeddings.model import EmbeddingModel, default_model_dir
from .files import discovery
from .files.access import AccessPolicy, excluded_paths, whole_computer_roots
from .indexing import Indexer, VisualIndexer
from .paths import data_dir, ensure_app_dirs, models_dir
from .power import MODES, PowerMonitor
from .search import SearchService
from .search.dictionary import DEFAULT_PATH as DEFAULT_WORDLIST_PATH, Dictionary
from .search.learned_router import MIN_TRAINING_QUERIES, LearnedRouter, label_query
from .search.reranker import Reranker
from .storage import FileRecordStore, KeywordStore, LanceDBVectorStore
from .thumbnails import generate_thumbnail
from .transcription import Transcriber
from .transcription.snap import snap_to_filenames
from .visual_search import VisualSearchService
from .watch_setup import LiveIndexing, load_watched_folders

logger = logging.getLogger(__name__)

MODEL_DIR = default_model_dir()
# base.en, chosen 2026-09-11 from the user's own recordings: tiny.en heard
# "bread recipe" as "Brit recipe" and "squats" as "squirts"; base.en got
# both right at 0.23s per sentence for +34MB; small.en added 165MB for no
# further gain on those clips. Falls back to any installed size so a dev
# checkout with only tiny.en still runs.
WHISPER_MODEL_SIZE = "base"
_MODELS = models_dir()
WHISPER_MODEL_DIR = next(
    (d for d in [_MODELS / f"whisper-{WHISPER_MODEL_SIZE}.en", _MODELS / "whisper-small.en", _MODELS / "whisper-tiny.en"] if (d / "onnx").exists()),
    _MODELS / f"whisper-{WHISPER_MODEL_SIZE}.en",
)
# ViT-B/16, chosen 2026-09-11 by measuring all three Xenova CLIP exports
# on the 124 subject-labelled demo photos (scripts/evaluate_visual_cutoff.py):
#   variant   size   CPU ms/img   best F1   ranking p@n
#   B/32     149MB        9         0.74       0.74     "airplane" -> plane wreck, airport, then the planes
#   B/16     154MB       37         0.77       0.74     real planes first; all four dogs before any cat
#   L/14     414MB      167         0.80       0.77     best, but 2.7x the download and 4.5x slower
# B/16 is the same download size as B/32 with visibly better ordering;
# L/14's gain isn't worth what it costs on an unknown grading laptop.
# Falls back to any installed variant so a dev checkout still runs.
# Cross-encoder reranker (Phase 18) — optional; the router's top tier
# reports the stage as unavailable when it is missing.
RERANKER_MODEL_DIR = _MODELS / "ms-marco-MiniLM-L-6-v2"
CLIP_VARIANT = "base-patch16"
CLIP_MODEL_DIR = next(
    (d for d in [_MODELS / f"clip-vit-{CLIP_VARIANT}", _MODELS / "clip-vit-base-patch32", _MODELS / "clip-vit-large-patch14"] if (d / "onnx").exists()),
    _MODELS / f"clip-vit-{CLIP_VARIANT}",
)


def _apply_access_exclusions(mode: str) -> None:
    """The system/app-data folders are pruned only in "all" mode — a
    "limited" user who deliberately adds a folder under ~/Library asked
    for exactly that folder."""
    discovery.EXCLUDED_PATHS.clear()
    if mode == "all":
        discovery.EXCLUDED_PATHS.update(excluded_paths())


class LazyTranscriber:
    """Transcriber that loads its model on first use; same interface."""

    def __init__(self, model_dir: Path):
        self._model_dir = model_dir
        self._real = None
        self._lock = threading.Lock()

    def _get(self):
        with self._lock:
            if self._real is None:
                t0 = time.perf_counter()
                self._real = Transcriber(self._model_dir)
                logger.info("speech model %s loaded on first use in %.1f s", self._model_dir.name, time.perf_counter() - t0)
            return self._real

    def transcribe(self, *args, **kwargs):
        return self._get().transcribe(*args, **kwargs)

    @property
    def model(self):
        return self._get().model

    @property
    def active_provider(self):
        return self._get().active_provider


@asynccontextmanager
async def lifespan(app: FastAPI):
    t_start = time.perf_counter()
    dirs = ensure_app_dirs()
    app.state.dirs = dirs
    log_path = logging_setup.configure(dirs["root"])
    logger.info("IntelliFile backend starting; log file %s; models %s; data %s", log_path, models_dir(), data_dir())
    if offline_guard.wanted():
        offline_guard.install()
        logger.warning("Offline guard ON: every non-loopback connection or DNS lookup will be refused and counted (/offline-guard).")

    model = EmbeddingModel(MODEL_DIR)
    vector_store = LanceDBVectorStore(str(dirs["vector_index"]))
    keyword_store = KeywordStore(dirs["keyword_index"] / "keyword.db")
    file_record_store = FileRecordStore(dirs["database"] / "files.db")
    # Whisper (and transformers' feature extractor behind it) loads on the
    # first audio file or voice search, not at startup — the first launch of
    # a fresh build pays a per-library scan on macOS, and text search must
    # not wait for the speech model.
    transcriber = LazyTranscriber(WHISPER_MODEL_DIR) if (WHISPER_MODEL_DIR / "onnx").exists() else None
    indexer = Indexer(model, vector_store, keyword_store, file_record_store, transcriber=transcriber)
    if indexer.reset_if_model_changed(dirs["config"]):
        logger.warning("Text embedding model changed to %s — the text index was cleared; watched folders will be re-embedded on the startup scan.", model.model_id)

    # Visual search degrades gracefully, same as voice: with no CLIP model
    # installed the app still runs, just text-only — photos aren't
    # discovered and /search-visual reports what's missing.
    clip_model = ClipModel(CLIP_MODEL_DIR) if (CLIP_MODEL_DIR / "onnx").exists() else None
    visual_indexer = VisualIndexer(clip_model, vector_store, file_record_store) if clip_model else None
    if visual_indexer is not None and visual_indexer.reset_if_model_changed(dirs["config"]):
        logger.warning("Visual search model changed — photo index cleared; watched folders will be re-indexed.")

    app.state.indexer = indexer
    app.state.search_service = SearchService(model, vector_store, keyword_store, file_record_store)
    if (RERANKER_MODEL_DIR / "model.onnx").exists():
        app.state.search_service.reranker = Reranker(RERANKER_MODEL_DIR)
    else:
        logger.warning("Reranker model not found (scripts/download_reranker_model.py) — the hybrid+rerank tier runs without it.")
    app.state.transcriber = transcriber
    app.state.visual_indexer = visual_indexer
    dictionary = Dictionary() if DEFAULT_WORDLIST_PATH.exists() else None
    if dictionary is None:
        logger.warning("Word list not found (scripts/download_wordlist.py) — photo queries will run unchecked.")
    app.state.visual_search_service = (
        VisualSearchService(clip_model, vector_store, file_record_store, dictionary=dictionary, keyword_store=keyword_store)
        if clip_model else None
    )

    # Live watching of every folder the user has indexed, resumed across
    # launches — see watch_setup.py for why this exists.
    live = LiveIndexing(indexer, visual_indexer, dirs["config"])
    # Phase 12: the file-access policy decided on first run. Nothing is
    # scanned while it is unset or denied; "all" adds the whole-computer
    # roots (with system folders pruned) on top of any picked folders.
    access = AccessPolicy(dirs["config"])
    if access.mode == "unset" and load_watched_folders(dirs["config"]):
        # An install from before the permission screen already has folders
        # the user picked by hand: that is "limited" access, not "unset".
        access.set("limited")
    app.state.access = access
    live.indexing_allowed = lambda: access.allows_indexing
    _apply_access_exclusions(access.mode)

    # Phase 16: what the user does, remembered locally (Objective 2's raw
    # material). Honours the "remember my activity" setting.
    app.state.settings = Settings(dirs["config"])
    usage_store = UsageStore(dirs["database"] / "usage.db")
    app.state.usage_store = usage_store

    # Phase 10: power-aware indexing — attached BEFORE the first scans are
    # queued so a laptop already on battery never starts them.
    live.settings_reader = app.state.settings.all
    power = PowerMonitor()
    live.attach_power(power)
    power.start()
    app.state.power = power

    live.start()
    app.state.live_indexing = live
    if access.mode == "all":
        for root in whole_computer_roots():
            live.watch(root)
            live.enqueue(root)

    # Phase 17: the profile built from those events, feeding search
    # ranking and the recommendations. Honours the "personalize" setting.
    profile_builder = ProfileBuilder(usage_store, file_record_store, vector_store, keyword_store)
    app.state.profile_builder = profile_builder
    app.state.search_service.profile_builder = profile_builder
    app.state.search_service.personalize_enabled = lambda: app.state.settings.get("personalize")
    live.on_index_changed = profile_builder.forget_vectors

    # Phase 19: the local LLM agent. The model (~1.1 GB in RAM) is loaded
    # on the first question, not at startup, so search stays light for
    # people who never ask one; `/ask` reports when it is missing.
    app.state.llm = None
    app.state.llm_lock = threading.Lock()
    app.state.llm_file = find_model_file()
    if app.state.llm_file is None:
        logger.warning("Local LLM not found (scripts/download_llm_model.py) — Ask mode is unavailable.")
    # Phase 15 improvement 6: the learned router. Loads the model trained
    # from this user's own queries if there is one; (re)trains in the
    # background at startup once enough queries have been remembered.
    app.state.learned_router = LearnedRouter(dirs["config"] / "router_model.json")
    app.state.search_service.learned_router = app.state.learned_router
    threading.Thread(target=_train_router_if_ready, daemon=True, name="router-train").start()
    logger.info("startup ready in %.1f s (models + index + watchers)", time.perf_counter() - t_start)

    yield

    live.stop()
    usage_store.close()
    file_record_store.close()
    keyword_store.close()


app = FastAPI(title="IntelliFile Backend", lifespan=lifespan)

# Phase 12 — restrict local API access. The port is loopback-only, but any
# program on the machine could still read the index through it. When the
# desktop shell starts the backend it passes a per-launch secret in
# INTELLIFILE_API_TOKEN; every request must carry it (header, or `token`
# query parameter for <img> thumbnails). The dev server started by hand
# has no token and stays open — documented, and only on the dev machine.
API_TOKEN = os.environ.get("INTELLIFILE_API_TOKEN") or None


@app.middleware("http")
async def require_api_token(request, call_next):
    if API_TOKEN and request.method != "OPTIONS" and request.url.path != "/health":
        provided = request.headers.get("x-intellifile-token") or request.query_params.get("token")
        if not provided or not secrets.compare_digest(provided, API_TOKEN):
            return JSONResponse({"error": "unauthorized: missing or wrong API token"}, status_code=401)
    return await call_next(request)

# The desktop shell's webview origin differs from this backend's origin
# (different port in dev, a tauri://localhost-style origin once packaged).
# Without explicit CORS rules, the browser can silently discard successful
# responses before the frontend ever sees them.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/offline-guard")
def offline_guard_endpoint():
    """Whether the offline guard is on and how many network attempts it
    has refused since startup — zero is the number the live test wants."""
    return offline_guard.status()


@app.get("/health")
def health(request: Request):
    """Open so the shell and the UI can poll for readiness; the data
    directory is only revealed to a caller that has the token."""
    trusted = not API_TOKEN or secrets.compare_digest(request.headers.get("x-intellifile-token", ""), API_TOKEN)
    return {"status": "ok", "app_data_dir": str(app.state.dirs["root"]) if trusted else None, "token_required": bool(API_TOKEN)}


class IndexFolderRequest(BaseModel):
    folder: str


def _safe_path(raw: str) -> Path | None:
    """Phase 12 path validation: absolute, no NUL bytes, `..` resolved. The
    user picks paths through a native dialog, so anything else is not a
    real request."""
    raw = (raw or "").strip()
    if not raw or "\x00" in raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return None
    # normpath folds `..` and `.` without resolving symlinks — the path the
    # user picked (e.g. /var/…) stays the path they see, and it still
    # cannot climb anywhere the raw string did not already name.
    return Path(os.path.normpath(str(path)))


# Bounds for user-supplied sizes. Found in the 2026-09-11 audit: top_k=0
# reached LanceDB and raised ("Limit is required"), a 500 for the user.
MAX_TOP_K = 100
MIN_THUMBNAIL, MAX_THUMBNAIL = 16, 1024
# 16 kHz mono 16-bit = 32 KB/s; below ~0.3 s there is no speech to find.
MIN_AUDIO_BYTES = 10_000


def _clamp_top_k(top_k: int) -> int:
    return max(1, min(top_k, MAX_TOP_K))


@app.post("/index-folder")
def index_folder_endpoint(body: IndexFolderRequest):
    raw = body.folder.strip()
    # Path("").expanduser() is ".", which is_dir() accepts — so an empty
    # request used to index AND start watching the backend's own working
    # directory (found in the 2026-09-11 audit). Only absolute paths are
    # meaningful coming from a folder picker.
    if not raw:
        return {"error": "No folder given."}
    folder = _safe_path(raw)
    if folder is None:
        return {"error": f"Folder path must be absolute: {raw}"}
    if not folder.is_dir():
        return {"error": f"Not a folder: {folder}"}
    if not app.state.access.allows_indexing:
        return {"error": "File access is switched off — allow it in Settings → File access first."}
    # Indexing runs in the background so a large folder never blocks the UI;
    # progress is read from /status. The folder is watched from now on.
    app.state.live_indexing.watch(str(folder))
    app.state.live_indexing.enqueue(str(folder))
    return {"queued": str(folder)}


@app.post("/forget-folder")
def forget_folder_endpoint(body: IndexFolderRequest):
    folder = _safe_path(body.folder)
    if folder is None:
        return {"error": "Folder path must be absolute."}
    if str(folder) in app.state.live_indexing.watcher.watched_files:
        return {"removed": 1 if app.state.live_indexing.forget_file(str(folder)) else 0}
    removed = app.state.live_indexing.forget(str(folder))
    return {"removed": removed}


@app.post("/reindex-folder")
def reindex_folder_endpoint(body: IndexFolderRequest):
    folder = _safe_path(body.folder)
    if folder is None or not folder.is_dir():
        return {"error": f"Not a folder: {body.folder}"}
    if not app.state.access.allows_indexing:
        return {"error": "File access is switched off — allow it in Settings → File access first."}
    app.state.live_indexing.watch(str(folder))
    app.state.live_indexing.reindex(str(folder))
    return {"queued": str(folder)}


@app.post("/index-file")
def index_file_endpoint(body: IndexFolderRequest):
    """Phase 12 "limited" access: one file, indexed and watched for changes."""
    path = _safe_path(body.folder)
    if path is None or not path.is_file():
        return {"error": f"Not a file: {body.folder}"}
    if not app.state.access.allows_indexing:
        return {"error": "File access is switched off — allow it in Settings → File access first."}
    if not discovery.is_indexable(path, app.state.live_indexing.extensions):  # photos/videos count when CLIP is installed
        return {"error": f"IntelliFile does not index this kind of file: {path.name}"}
    app.state.live_indexing.watch_file(str(path))
    app.state.live_indexing.index_single_file(str(path))
    return {"queued": str(path)}


class AccessRequest(BaseModel):
    mode: str
    remove_index: bool = False


@app.get("/access")
def access_endpoint():
    """The file-access policy (Phase 12): unset on first run until the
    user chooses all / limited / denied."""
    return app.state.access.as_dict()


@app.post("/access")
def set_access_endpoint(body: AccessRequest):
    access = app.state.access
    previous = access.mode
    try:
        access.set(body.mode)
    except ValueError as e:
        return {"error": str(e)}
    live = app.state.live_indexing
    _apply_access_exclusions(body.mode)
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
        from .watch_setup import save_watched_folders
        save_watched_folders(live.config_dir, live.watcher.watched_roots)
    return {**access.as_dict(), "previous": previous, "removed": removed}


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


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


@app.get("/status")
def status_endpoint():
    """Everything the Status and Folders screens show. All real numbers."""
    live = app.state.live_indexing
    folders = live.folder_stats()
    totals = {k: sum(f[k] for f in folders) for k in ("documents", "photos", "videos", "audio")}
    dirs = app.state.dirs
    return {
        "engine": "online",
        "job": live.job_snapshot(),
        "folders": folders,
        "totals": {**totals, "files": sum(totals.values())},
        "index_size_bytes": _index_size_bytes(dirs),
        "power": live.power_snapshot(),
        "access": app.state.access.as_dict(),
        "activity": {
            "enabled": app.state.settings.get("remember_activity"),
            "events": app.state.usage_store.count(),
            "session": app.state.usage_store.current_session(),
        },
        "models": {
            "text": {"name": app.state.search_service.model.name, "dimension": 384, "provider": app.state.search_service.model.active_provider},
            "photos": (
                {"name": CLIP_MODEL_DIR.name, "precision": app.state.visual_indexer.clip_model.precision, "dimension": app.state.visual_indexer.clip_model.dimension}
                if app.state.visual_indexer is not None else None
            ),
            "speech": {"name": WHISPER_MODEL_DIR.name} if app.state.transcriber is not None else None,
            "reranker": {"name": RERANKER_MODEL_DIR.name} if app.state.search_service.reranker is not None else None,
        },
    }


def _remember(kind: str, **fields) -> None:
    """Record a usage event unless the user switched activity memory off.
    Never lets a bookkeeping failure break the request it rides on."""
    if not app.state.settings.get("remember_activity"):
        return
    try:
        app.state.usage_store.record(kind, **fields)
    except Exception:
        logger.exception("could not record usage event %s", kind)


@app.get("/search")
def search_endpoint(q: str, top_k: int = 10, mode: str = "auto"):
    """`mode=auto` (default since Phase 18) lets the router choose the
    tier; smart / exact / keyword are the manual overrides. The `route`
    block reports what ran and what it cost — Objective 3's evidence."""
    if not q.strip():
        return {"results": [], "route": None}
    if mode not in ("auto", "smart", "exact", "keyword"):
        mode = "auto"
    results, route = app.state.search_service.search_routed(q, top_k=_clamp_top_k(top_k), mode=mode)
    # Queries are remembered here rather than by the UI so every entry
    # point (main window, overlay, tests) counts, with what they returned
    # and which route answered them (Phase 20 evaluates the router from this).
    _remember("query", query=q.strip(), meta={
        "mode": mode, "results": [r["file_id"] for r in results[:5]], "count": len(results),
        "route": route["tier"], "requested_tier": route["requested_tier"], "escalated": route["escalated"],
        "complexity": route["complexity"], "total_ms": route["total_ms"],
    })
    return {"results": results, "route": route}


@app.get("/suggest")
def suggest_endpoint(q: str):
    """The 'Did you mean' chip: the query as `/search` would correct it, or null."""
    if not q.strip():
        return {"suggestion": None}
    return {"suggestion": app.state.search_service.suggest(q)}


@app.get("/search-visual")
def search_visual_endpoint(q: str, top_k: int = 10, kind: str | None = None):
    if app.state.visual_search_service is None:
        return {"error": "Visual search model not installed. Run scripts/download_clip_model.py."}
    if not q.strip():
        return {"results": []}
    check = app.state.visual_search_service.check_query(q)
    if check.unrecognized:
        # A word that is neither English nor one of the user's own: CLIP
        # would still embed it and return "strong" matches for gibberish.
        return {"results": [], "unrecognized": check.unrecognized}
    if kind not in (None, "photo", "video"):
        kind = None
    results = app.state.visual_search_service.search(check.text, top_k=_clamp_top_k(top_k), kind=kind)
    _remember("query", query=q.strip(), meta={"mode": "visual", "kind": kind, "results": [r["file_id"] for r in results[:5]], "count": len(results)})
    return {
        "results": results,
        "corrected_query": check.text if check.corrected else None,
    }


@app.get("/suggest-visual")
def suggest_visual_endpoint(q: str):
    if app.state.visual_search_service is None or not q.strip():
        return {"suggestion": None}
    return {"suggestion": app.state.visual_search_service.suggest(q)}


@app.get("/thumbnail")
def thumbnail_endpoint(path: str, t: float | None = None, size: int = 256):
    file_path = Path(path)
    # Only files IntelliFile has indexed get thumbnails. An <img> tag on any
    # web page can point at this local port (img loads aren't CORS-gated),
    # so without this the endpoint would render any readable image on the
    # machine on request. Indexed files are exactly the set the user chose
    # to expose to the app. (2026-09-11 audit.)
    safe = _safe_path(path)
    if safe is None:
        return Response(status_code=404)
    file_path = safe
    record = app.state.indexer.file_record_store.get_by_path(str(file_path))
    if record is None or record.deleted or not file_path.is_file():
        return Response(status_code=404)
    try:
        jpeg_bytes = generate_thumbnail(file_path, timestamp_offset_seconds=t, max_size=max(MIN_THUMBNAIL, min(size, MAX_THUMBNAIL)))
    except Exception:
        # Unreadable/corrupt image: a broken thumbnail shouldn't read as
        # a server fault, and the result itself is still usable.
        return Response(status_code=422)
    return Response(content=jpeg_bytes, media_type="image/jpeg")


# The prompt is limited by Whisper's token budget (~33 names fit); the
# text-only snap step is not, so it sees more — but not all: the chance
# that an ordinary phrase coincidentally sounds like SOME name grows with
# the pool (measured: 12% false rewrites of short phrases against 1500
# random names, ~0 against 400). Recently touched files are the ones
# people ask for by name, so the most recent 400 is the pool.
SNAP_NAME_LIMIT = 400


def filename_hints(record_store: FileRecordStore, limit: int = 40) -> list[str]:
    """The user's most recently modified file names, as words Whisper
    should be able to spell (see Transcriber.transcribe). Capped so the
    prompt stays short; the most recently touched files are the ones
    most likely to be asked for by name."""
    records = sorted(record_store.list_active(), key=lambda r: r.modified_time, reverse=True)
    hints: list[str] = []
    for record in records:
        name = re.sub(r"[_\-.]+", " ", Path(record.path).stem).strip()
        if name and name.lower() not in {h.lower() for h in hints}:
            hints.append(name)
        if len(hints) >= limit:
            break
    return hints


@app.post("/transcribe")
def transcribe_endpoint(audio: UploadFile):
    # A plain `def`, like every other endpoint: FastAPI runs it in the
    # threadpool. As `async def` it ran Whisper on the event loop and
    # froze the whole server for the length of the transcription —
    # measured 2026-09-21: /health took 0.88 s during a transcription
    # against 0.6 ms idle, so the status poll and any search stalled too.
    if app.state.transcriber is None:
        return {"error": "Voice search model not installed. Run scripts/download_whisper_model.py."}
    audio_bytes = audio.file.read()
    if len(audio_bytes) < MIN_AUDIO_BYTES:
        return {"error": "Recording too short — hold the microphone button and speak, then click it again to stop."}
    hints = filename_hints(app.state.indexer.file_record_store)
    try:
        heard = app.state.transcriber.transcribe(audio_bytes, vocabulary_hint=hints)
    except Exception as e:
        # PyAV's decode errors ("Invalid data found when processing input:
        # '<none>'", "tuple index out of range" for a non-audio upload) mean
        # nothing to a user; the actionable fact is the same for all of them.
        return {"error": f"Couldn't read that recording as audio — please try again. ({type(e).__name__})"}
    # A bare file name Whisper misheard ("Learn Lord letter") is snapped to
    # the name it sounds like — but only APPLIED when what was heard finds
    # nothing on its own, so a coincidental sound-alike can never replace a
    # transcript that already works; then it is offered as a "Did you mean"
    # chip instead. `heard` is set only when the snap was applied, so the
    # UI can show the correction and offer the raw words back.
    names = filename_hints(app.state.indexer.file_record_store, limit=SNAP_NAME_LIMIT)
    snapped, raw = snap_to_filenames(heard, names)
    if raw is None:
        return {"text": heard, "heard": None, "suggestion": None}
    search = app.state.search_service.search
    raw_results = search(heard, top_k=5)
    raw_finds_something = any(r["confidence"] != "weak" for r in raw_results)
    # Second way in (2026-09-21): the raw words may "find something" only
    # by coincidence — with the project folder indexed, "Learn Lord
    # Letter" found snap.py and this README, which quote that very
    # phrase — while the snapped text is exactly one of the user's file
    # names. A whole utterance that is a file name, when what was heard
    # matches no file name at all, is the file name.
    bare_name = _normalized(snapped) in {_normalized(n) for n in names}
    if raw_finds_something and not (bare_name and not _has_filename_match(raw_results) and _has_filename_match(search(snapped, top_k=5))):
        return {"text": heard, "heard": None, "suggestion": snapped}
    return {"text": snapped, "heard": heard, "suggestion": None}


def _normalized(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _has_filename_match(results: list[dict]) -> bool:
    return any(r["why"] and r["why"][0].startswith("Filename contains") for r in results)


# ----- Phase 16: activity memory & settings -----


class EventRequest(BaseModel):
    kind: str
    file_id: str | None = None
    path: str | None = None
    query: str | None = None
    meta: dict | None = None


@app.post("/events")
def record_event_endpoint(body: EventRequest):
    """The UI reports what the user did with a result (open, reveal, click).
    Searches are recorded by /search itself."""
    if body.kind not in EVENT_KINDS or body.kind == "query":
        return {"error": f"unknown event kind: {body.kind}"}
    if not app.state.settings.get("remember_activity"):
        return {"recorded": False, "reason": "activity memory is off"}
    event = app.state.usage_store.record(body.kind, file_id=body.file_id, path=body.path, query=body.query, meta=body.meta)
    return {"recorded": True, "event": event}


@app.get("/events")
def list_events_endpoint(limit: int = 50, kind: str | None = None):
    kinds = {kind} if kind in EVENT_KINDS else None
    return {
        "events": app.state.usage_store.recent(limit=max(1, min(limit, 500)), kinds=kinds),
        "total": app.state.usage_store.count(),
    }


@app.delete("/events")
def clear_events_endpoint():
    """The Settings page's *Clear activity*."""
    return {"cleared": app.state.usage_store.clear()}


@app.get("/context")
def context_endpoint():
    """The current working context (this session's queries, files and
    types) — what Phase 17's ranking and Phase 19's agent read."""
    return app.state.usage_store.current_session()


@app.get("/settings")
def get_settings_endpoint():
    return app.state.settings.all()


class SettingsRequest(BaseModel):
    remember_activity: bool | None = None
    personalize: bool | None = None
    pause_on_battery: bool | None = None
    pause_on_low_power: bool | None = None
    resource_mode: str | None = None


@app.post("/settings")
def update_settings_endpoint(body: SettingsRequest):
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    if "resource_mode" in changes and changes["resource_mode"] not in MODES:
        return {"error": f"resource_mode must be one of {', '.join(MODES)}"}
    updated = app.state.settings.update(changes)
    app.state.live_indexing.apply_power()  # a toggle takes effect immediately
    return updated


@app.get("/power")
def power_endpoint():
    """Battery / low-power / CPU state and whether indexing is paused for it."""
    return app.state.live_indexing.power_snapshot()


# ----- Phase 17: profile & recommendations -----


@app.get("/profile")
def profile_endpoint(rebuild: bool = False):
    """What the app has learned: frequently used files, type mix, topics
    of interest, the weekday × time-of-day heatmap. The Insights screen."""
    return app.state.profile_builder.get(force=rebuild).as_dict()


@app.get("/recommendations")
def recommendations_endpoint():
    """Files to suggest before any query is typed, each with its reason.
    Empty lists (and `enabled: false`) while personalization is off."""
    if not app.state.settings.get("personalize"):
        return {"enabled": False, "cold_start": None, "now": None, "likely_next": [], "usual_now": [], "recent": []}
    profile = app.state.profile_builder.get()
    return {"enabled": True, **recommend(profile, app.state.usage_store, app.state.indexer.file_record_store)}


# ----- Phase 15 improvement 6: the learned router -----


def _training_queries() -> list[str]:
    """Distinct text queries this user has run (auto/smart/keyword modes;
    not visual, not Ask), newest first."""
    seen, out = set(), []
    for e in app.state.usage_store.recent(limit=2000, kinds={"query"}):
        meta = e.get("meta") or {}
        q = (e.get("query") or "").strip()
        if not q or meta.get("mode") in ("visual", "ask", "exact") or q.lower() in seen:
            continue
        seen.add(q.lower())
        out.append(q)
    return out


def train_router(min_queries: int = MIN_TRAINING_QUERIES) -> dict:
    """Replay the user's own queries through the tiers against the live
    index, label each with the cheapest tier that gave a confident hit,
    fit the model, and report how often it would have picked the right
    tier compared with the rules (on the same queries)."""
    from .search.router import route as rule_route

    queries = _training_queries()
    if len(queries) < min_queries:
        return {"trained": False, "queries": len(queries), "needed": min_queries}
    svc = app.state.search_service
    examples = []
    for q in queries:
        from .search.query_parsing import parse_query

        parsed = parse_query(q)
        if not parsed.text or parsed.exact_phrase is not None:
            continue
        active = svc._active_records()
        decision = rule_route(parsed.text, parsed.filters.any(), False, active, svc._correction_vocabulary(active))
        examples.append((decision, parsed.filters.any(), label_query(svc, q)))
    if len(examples) < min_queries:
        return {"trained": False, "queries": len(examples), "needed": min_queries}
    # Honest accuracy: train on 80%, score on the held-out 20%, then fit on all.
    cut = int(len(examples) * 0.8)
    holdout = examples[cut:]
    trial = LearnedRouter(app.state.dirs["config"] / "router_model.tmp.json")
    trial.train(examples[:cut])
    rank = {"filename": 0, "keyword": 1, "hybrid": 2, "hybrid+rerank": 3, "none": 2}
    def scored(picker):
        ok = 0
        for d, hf, label in holdout:
            chosen = picker(d, hf)
            ok += 1 if rank[chosen] == min(rank[label], 2) else 0
        return ok / max(1, len(holdout))
    acc_rules = scored(lambda d, hf: d.tier)
    from .search.learned_router import CONFIDENCE

    acc_learned = scored(lambda d, hf: next((t for t in ("filename", "keyword") if trial.probabilities(d, hf)[t] >= CONFIDENCE), "hybrid"))
    try:
        trial.path.unlink()
    except OSError:
        pass
    accuracy = {"holdout": len(holdout), "rules": round(acc_rules, 3), "learned": round(acc_learned, 3)}
    # The learned model takes over only when it beats the rules on this
    # user's own held-out queries; otherwise it is stored (for Insights)
    # and the rules stay in charge.
    activate = acc_learned > acc_rules
    app.state.learned_router.train(examples, accuracy=accuracy, active=activate)
    logger.info("learned router trained on %d queries: holdout tier accuracy rules %.0f%% vs learned %.0f%% — %s", len(examples), 100 * acc_rules, 100 * acc_learned, "ACTIVE" if activate else "rules kept")
    return {"trained": True, "active": activate, "queries": len(examples), **accuracy}


def _train_router_if_ready() -> None:
    try:
        time.sleep(20)  # after the startup scans have settled
        result = train_router()
        if not result.get("trained"):
            logger.info("learned router: %s of %s queries remembered — rules stay in charge", result.get("queries"), result.get("needed"))
    except Exception:
        logger.exception("learned router training failed")


@app.get("/router/model")
def router_model_endpoint():
    return {**app.state.learned_router.as_dict(), "queries_available": len(_training_queries()), "needed": MIN_TRAINING_QUERIES}


@app.post("/router/train")
def router_train_endpoint():
    return train_router()


# ----- Phase 20: router statistics from real usage -----


@app.get("/router-stats")
def router_stats_endpoint():
    """Route mix and mean latency per route over the remembered queries
    (each query event carries its route and total_ms since Phase 18) —
    the Insights screen's live counterpart to scripts/evaluate_routing.py."""
    per_route: dict[str, dict] = {}
    escalated = 0
    for e in app.state.usage_store.recent(limit=500, kinds={"query"}):
        meta = e.get("meta") or {}
        route = meta.get("route")
        if not route:
            continue
        bucket = per_route.setdefault(route, {"route": route, "queries": 0, "total_ms": 0.0, "with_results": 0})
        bucket["queries"] += 1
        bucket["total_ms"] += float(meta.get("total_ms") or 0.0)
        bucket["with_results"] += 1 if (meta.get("count") or 0) > 0 else 0
        escalated += 1 if meta.get("escalated") else 0
    total = sum(b["queries"] for b in per_route.values())
    routes = [
        {"route": b["route"], "queries": b["queries"], "share": b["queries"] / total if total else 0.0,
         "mean_ms": round(b["total_ms"] / b["queries"], 1) if b["queries"] else 0.0, "with_results": b["with_results"]}
        for b in sorted(per_route.values(), key=lambda b: -b["queries"])
    ]
    return {"total": total, "escalated": escalated, "routes": routes}


# ----- Phase 19: the agent -----


def _get_llm():
    with app.state.llm_lock:
        if app.state.llm is None and app.state.llm_file is not None:
            app.state.llm = LocalLLM(app.state.llm_file)
        return app.state.llm


def _make_agent() -> Agent | None:
    llm = _get_llm()
    if llm is None:
        return None
    return Agent(
        llm,
        lambda: Toolbox(app.state.search_service, app.state.indexer.vector_store, app.state.usage_store, app.state.profile_builder),
    )


@app.get("/ask")
def ask_endpoint(q: str):
    """Ask mode: the agent plans, calls search as a tool, reads the
    results and answers with citations — streamed as server-sent events
    (one JSON event per line: context, thought, tool_call, tool_result,
    answer_start, token, answer, done, error) so the UI can show the
    trace as it happens."""
    question = q.strip()

    def sse(event: dict) -> str:
        return f"data: {json.dumps(event)}\n\n"

    if not question:
        return StreamingResponse(iter([sse({"type": "error", "message": "Ask a question first."})]), media_type="text/event-stream")
    agent = _make_agent()
    if agent is None:
        return StreamingResponse(
            iter([sse({"type": "error", "message": "The local language model is not installed (backend/models/llm). Run scripts/download_llm_model.py."})]),
            media_type="text/event-stream",
        )

    # The agent runs in a worker thread and hands events over a queue, so
    # the response streams while the model is still generating.
    events: "queue.Queue[dict | None]" = queue.Queue()

    def work() -> None:
        try:
            for event in agent.run(question):
                events.put(event)
        except Exception as e:
            logger.exception("agent failed")
            events.put({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def generate():
        final = None
        while True:
            event = events.get()
            if event is None:
                break
            if event["type"] == "done":
                final = event
            yield sse(event)
        if final is not None:
            _remember("query", query=question, meta={
                "mode": "ask", "results": [s["file_id"] for s in final.get("sources", [])[:5]], "count": len(final.get("sources", [])),
                "route": "agent", "tool_calls": final.get("tool_calls"), "total_ms": round(final.get("seconds", 0) * 1000),
            })

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/ask/status")
def ask_status_endpoint():
    llm = app.state.llm
    if llm is not None and getattr(llm, "last_benchmark", None) is None:
        llm.last_benchmark = llm.benchmark()  # once per process, not per call (it generates 48 tokens)
    return {
        "available": app.state.llm_file is not None,
        "model": app.state.llm_file.stem if app.state.llm_file is not None else None,
        "loaded": llm is not None,
        "tokens_per_second": llm.last_benchmark["tokens_per_second"] if llm is not None else None,
    }
