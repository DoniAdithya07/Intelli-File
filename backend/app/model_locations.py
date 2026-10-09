"""Where each model lives on disk. Re-imported by main.py, and by the
evaluation scripts (`from app.main import CLIP_MODEL_DIR`)."""
from .embeddings.model import default_model_dir
from .paths import models_dir

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
