"""One-time setup step (dev machine only, needs internet): fetches an
ONNX Whisper English speech-to-text model into backend/models/, so the
app itself never needs internet access at runtime (per the PRD's Offline
Requirement). Bundled into the installer at packaging time (Phase 14).

Run with:
    backend/venv/bin/python backend/scripts/download_whisper_model.py [tiny|base|small]

Default is `base` — see WHISPER_MODEL_SIZE in app/main.py for why.
"""

import sys
from pathlib import Path

from huggingface_hub import snapshot_download

SIZE = sys.argv[1] if len(sys.argv) > 1 else "base"
if SIZE not in {"tiny", "base", "small"}:
    sys.exit(f"Unknown size {SIZE!r} — use tiny, base or small")
REPO_ID = f"onnx-community/whisper-{SIZE}.en"
TARGET_DIR = Path(__file__).resolve().parents[1] / "models" / f"whisper-{SIZE}.en"

# Quantized encoder/decoder only — smaller and faster on CPU, the
# realistic case on the grading laptop. Skips the other precision
# variants in the repo (fp16, bnb4, q4, unquantized, etc.) we don't use.
ALLOWED_PATTERNS = [
    "onnx/encoder_model_quantized.onnx",
    "onnx/decoder_model_merged_quantized.onnx",
    "*.json",
    "merges.txt",
    "vocab.json",
]


def main():
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {REPO_ID} (quantized encoder/decoder only) ...")
    snapshot_download(repo_id=REPO_ID, allow_patterns=ALLOWED_PATTERNS, local_dir=str(TARGET_DIR))

    print(f"\nModel files ready at: {TARGET_DIR}")
    for f in sorted(TARGET_DIR.rglob("*")):
        if f.is_file():
            print(f"  - {f.relative_to(TARGET_DIR)} ({f.stat().st_size / 1_000_000:.1f} MB)")


if __name__ == "__main__":
    sys.exit(main())
