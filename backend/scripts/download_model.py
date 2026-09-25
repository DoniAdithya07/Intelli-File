"""One-time setup step (dev machine only, needs internet): fetches the
ONNX embedding model + tokenizer from Hugging Face into backend/models/,
so the app itself never needs internet access at runtime (per the PRD's
Offline Requirement). These files get bundled into the installer at
packaging time (Phase 14) rather than downloaded by end users.

Run with:
    backend/venv/bin/python backend/scripts/download_model.py
"""

import shutil
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download

REPO_ID = "sentence-transformers/all-MiniLM-L6-v2"
TARGET_DIR = Path(__file__).resolve().parents[1] / "models" / "all-MiniLM-L6-v2"


def main():
    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    files = {
        "onnx/model.onnx": "model.onnx",
        "tokenizer.json": "tokenizer.json",
    }

    for remote_path, local_name in files.items():
        print(f"Downloading {remote_path} ...")
        downloaded = hf_hub_download(repo_id=REPO_ID, filename=remote_path)
        shutil.copy(downloaded, TARGET_DIR / local_name)

    print(f"\nModel files ready at: {TARGET_DIR}")
    for f in sorted(TARGET_DIR.iterdir()):
        print(f"  - {f.name} ({f.stat().st_size / 1_000_000:.1f} MB)")


if __name__ == "__main__":
    sys.exit(main())
