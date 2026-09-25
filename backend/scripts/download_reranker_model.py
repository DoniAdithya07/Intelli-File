"""One-time setup step (dev machine only, needs internet): fetches the
cross-encoder reranker (Phase 18, Objective 3) into backend/models/ so the
app never needs internet at runtime. Bundled into the installer in Phase 14.

ms-marco-MiniLM-L-6-v2 scores a (query, passage) pair directly — the two
texts attend to each other, which a bi-encoder's separate embeddings
cannot — at ~90 MB fp32. It runs only on the router's top tier, over the
top few candidates, so its cost is paid only when the query needs it.

Run with:
    backend/venv/bin/python backend/scripts/download_reranker_model.py
"""

import shutil
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download

REPO_ID = "Xenova/ms-marco-MiniLM-L-6-v2"
TARGET_DIR = Path(__file__).resolve().parents[1] / "models" / "ms-marco-MiniLM-L-6-v2"


def main():
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    files = {"onnx/model.onnx": "model.onnx", "tokenizer.json": "tokenizer.json", "config.json": "config.json"}
    for remote_path, local_name in files.items():
        print(f"Downloading {remote_path} ...")
        downloaded = hf_hub_download(repo_id=REPO_ID, filename=remote_path)
        shutil.copy(downloaded, TARGET_DIR / local_name)
    print(f"\nReranker files ready at: {TARGET_DIR}")
    for f in sorted(TARGET_DIR.iterdir()):
        print(f"  - {f.name} ({f.stat().st_size / 1_000_000:.1f} MB)")


if __name__ == "__main__":
    sys.exit(main())
