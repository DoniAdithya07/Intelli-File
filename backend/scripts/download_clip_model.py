"""One-time setup step (dev machine only, needs internet): fetches the
ONNX CLIP model into backend/models/, so the app itself never needs
internet access at runtime (per the PRD's Offline Requirement). Bundled
into the installer at packaging time (Phase 14).

Run with:
    backend/venv/bin/python backend/scripts/download_clip_model.py
"""

import sys
from pathlib import Path

from huggingface_hub import snapshot_download

# Variant is selectable so the model can be evaluated side by side
# (scripts/evaluate_visual_cutoff.py --model ...). See CLIP_VARIANT in
# app/main.py for the one the app actually loads and why.
VARIANT = sys.argv[1] if len(sys.argv) > 1 else "base-patch16"
if VARIANT not in {"base-patch32", "base-patch16", "large-patch14"}:
    sys.exit(f"Unknown variant {VARIANT!r} — use base-patch32, base-patch16 or large-patch14")
REPO_ID = f"Xenova/clip-vit-{VARIANT}"
TARGET_DIR = Path(__file__).resolve().parents[1] / "models" / f"clip-vit-{VARIANT}"

# fp16 vision/text towers only — exact to the original weights at half the
# fp32 size (see ClipModel). Skips the other precision
# variants in the repo (int8 "_quantized", bnb4, q4, fp32, the combined
# model.onnx, etc.) we don't use.
ALLOWED_PATTERNS = [
    # fp16, not the int8 "_quantized" export — see ClipModel.__init__ for the
    # measured accuracy difference (int8 distorted embeddings by up to 0.11).
    "onnx/text_model_fp16.onnx",
    "onnx/vision_model_fp16.onnx",
    "tokenizer.json",
    "preprocessor_config.json",
    "config.json",
]


def main():
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {REPO_ID} (fp16 text/vision towers only) ...")
    snapshot_download(repo_id=REPO_ID, allow_patterns=ALLOWED_PATTERNS, local_dir=str(TARGET_DIR))

    print(f"\nModel files ready at: {TARGET_DIR}")
    for f in sorted(TARGET_DIR.rglob("*")):
        if f.is_file():
            print(f"  - {f.relative_to(TARGET_DIR)} ({f.stat().st_size / 1_000_000:.1f} MB)")


if __name__ == "__main__":
    sys.exit(main())
