import logging
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import logging_setup, offline_guard
from .agent import find_model_file
from .context import ProfileBuilder, Settings, UsageStore
from .embeddings.clip_model import ClipModel
from .embeddings.model import EmbeddingModel
from .files.access import AccessPolicy, whole_computer_roots
from .indexing import Indexer, VisualIndexer
from .model_locations import (  # noqa: F401  (re-exported: scripts import CLIP_MODEL_DIR from app.main)
    CLIP_MODEL_DIR,
    CLIP_VARIANT,
    MODEL_DIR,
    RERANKER_MODEL_DIR,
    WHISPER_MODEL_DIR,
    WHISPER_MODEL_SIZE,
)
from .paths import data_dir, ensure_app_dirs, models_dir
from .power import PowerMonitor
from .extraction import ocr
from .routes import agent, context, files, index, search, settings, system
from .routes import router as router_routes
from .routes.system import feature_problems
from .search import SearchService
from .search.dictionary import shared_dictionary
from .search.learned_router import LearnedRouter
from .search.reranker import Reranker
from .services.access_exclusions import apply_access_exclusions
from .services.lazy_transcriber import LazyTranscriber
from .services.router_training import train_router_if_ready
from .services.windows_recent_import import import_windows_recent_async
from .storage import FileRecordStore, KeywordStore, LanceDBVectorStore
from .visual_search import VisualSearchService
from .watch_setup import LiveIndexing, load_watched_folders

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    t_start = time.perf_counter()
    dirs = ensure_app_dirs()
    app.state.dirs = dirs
    log_path = logging_setup.configure(dirs["root"])
    if DEV_TOKEN_GENERATED:
        # Printed to the console only, never to the log file.
        print(f"IntelliFile dev API token (send as X-IntelliFile-Token): {API_TOKEN}", flush=True)
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
    dictionary = shared_dictionary()  # the same copy text search uses (about 20 MB, loaded once)
    if dictionary is None:
        logger.warning("Word list not found (scripts/download_wordlist.py) — photo queries will run unchecked.")
    app.state.visual_search_service = (
        VisualSearchService(clip_model, vector_store, file_record_store, dictionary=dictionary, keyword_store=keyword_store)
        if clip_model else None
    )
    # Optional parts that are missing, said once in the log and kept for
    # /status (features + startup_problems) so the UI can say why.
    app.state.features = {"photos_videos": visual_indexer is not None, "voice": transcriber is not None, "ocr": ocr.available()}
    app.state.startup_problems = feature_problems(app.state.features)
    for problem in app.state.startup_problems:
        logger.warning(problem)
    ocr.clear_temp_dir()  # page images a crash left behind (see ocr.temp_dir)

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
    apply_access_exclusions(access.mode)

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
    # Next-round improvement 1: Windows' Recent items seed the activity
    # memory (consent: `import_windows_recent`). Re-run after each folder
    # finishes indexing, since only indexed files can be matched.
    app.state.recent_import = {"state": "off", "last": None}
    app.state.recent_import_lock = threading.Lock()
    live.on_folder_indexed = lambda folder: import_windows_recent_async(app.state)
    import_windows_recent_async(app.state)

    # Phase 19: the local LLM agent. The model (~1.1 GB in RAM) is loaded
    # on the first question, not at startup, so search stays light for
    # people who never ask one; `/ask` reports when it is missing.
    app.state.llm = None
    app.state.llm_lock = threading.Lock()
    app.state.llm_preloading = False  # /ask/status?preload=1 running
    app.state.llm_error = None        # the last load failure, as a sentence (/ask/status)
    app.state.llm_file = find_model_file()
    if app.state.llm_file is None:
        logger.warning("Local LLM not found (models/llm in the app folder, or scripts/download_llm_model.py in dev) — Ask mode is unavailable until it is.")
    # Phase 15 improvement 6: the learned router. Loads the model trained
    # from this user's own queries if there is one; (re)trains in the
    # background at startup once enough queries have been remembered.
    app.state.learned_router = LearnedRouter(dirs["config"] / "router_model.json")
    app.state.search_service.learned_router = app.state.learned_router
    threading.Thread(target=train_router_if_ready, args=(app.state,), daemon=True, name="router-train").start()
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
# INTELLIFILE_API_TOKEN; every request must carry it in the X-IntelliFile-Token
# header (never in the URL: URLs end up in logs and referrers). A backend
# started by hand without the variable makes its own token and prints it once
# at startup; export INTELLIFILE_API_TOKEN first to choose your own.
DEV_TOKEN_GENERATED = not os.environ.get("INTELLIFILE_API_TOKEN")
API_TOKEN = os.environ.get("INTELLIFILE_API_TOKEN") or secrets.token_urlsafe(24)


@app.middleware("http")
async def require_api_token(request, call_next):
    if request.method != "OPTIONS" and request.url.path != "/health":
        provided = request.headers.get("x-intellifile-token")
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
# Added last so it runs first: a page on another site that points its own
# hostname at 127.0.0.1 (DNS rebinding) sends that hostname in Host and is refused.
# Starlette compares the host without its port.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])


@app.get("/health")
def health(request: Request):
    """Open so the shell and the UI can poll for readiness; the data
    directory is only revealed to a caller that has the token."""
    trusted = secrets.compare_digest(request.headers.get("x-intellifile-token", ""), API_TOKEN)
    return {"status": "ok", "app_data_dir": str(app.state.dirs["root"]) if trusted else None, "token_required": True}


for _module in (system, index, files, search, context, settings, router_routes, agent):
    app.include_router(_module.router)
