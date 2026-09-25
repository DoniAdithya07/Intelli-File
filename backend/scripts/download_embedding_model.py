"""One-time setup step (dev machine only, needs internet): fetches the
shipped text-embedding model bge-small-en-v1.5 (ONNX, CLS pooling) into
backend/models/ and writes its pooling marker. Chosen in Phase 13 over
all-MiniLM-L6-v2: vector-only Recall@5 100% vs 93% on the labelled corpus.
The thresholds file is derived afterwards with
scripts/tune_semantic_thresholds.py (it needs the model present).

Run with:
    backend/venv/bin/python backend/scripts/download_embedding_model.py
    backend/venv/bin/python backend/scripts/tune_semantic_thresholds.py bge-small-en-v1.5
"""

import shutil
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download

REPO_ID = "Xenova/bge-small-en-v1.5"
TARGET_DIR = Path(__file__).resolve().parents[1] / "models" / "bge-small-en-v1.5"


def main():
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    for remote, local in (("onnx/model.onnx", "model.onnx"), ("tokenizer.json", "tokenizer.json")):
        print(f"Downloading {remote} ...")
        shutil.copy(hf_hub_download(repo_id=REPO_ID, filename=remote), TARGET_DIR / local)
    (TARGET_DIR / "pooling.txt").write_text("cls")
    print(f"\nModel files ready at: {TARGET_DIR}")
    for f in sorted(TARGET_DIR.iterdir()):
        print(f"  - {f.name} ({f.stat().st_size / 1_000_000:.1f} MB)")


if __name__ == "__main__":
    sys.exit(main())
