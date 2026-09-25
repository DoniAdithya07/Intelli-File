"""Dev-only (needs internet): fetch the extra embedding models the Phase 13
comparison measures against the shipped all-MiniLM-L6-v2. They live in
backend/models/eval/ and are NOT bundled — evaluate_embeddings.py reads
them, nothing in the app does.

    bge-small-en-v1.5   384-dim, CLS pooling, ~133 MB — the strongest small English retriever
    all-MiniLM-L12-v2   384-dim, mean pooling, ~130 MB — the 12-layer sibling of the shipped model

Run with:  backend/venv/bin/python backend/scripts/download_eval_models.py
"""

import shutil
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download

TARGET = Path(__file__).resolve().parents[1] / "models" / "eval"
MODELS = {
    "bge-small-en-v1.5": ("Xenova/bge-small-en-v1.5", "cls"),
    "all-MiniLM-L12-v2": ("Xenova/all-MiniLM-L12-v2", "mean"),
}


def main():
    for name, (repo, pooling) in MODELS.items():
        target = TARGET / name
        target.mkdir(parents=True, exist_ok=True)
        for remote, local in (("onnx/model.onnx", "model.onnx"), ("tokenizer.json", "tokenizer.json")):
            print(f"Downloading {repo}/{remote} ...")
            shutil.copy(hf_hub_download(repo_id=repo, filename=remote), target / local)
        (target / "pooling.txt").write_text(pooling)
        print(f"  {name}: {(target / 'model.onnx').stat().st_size / 1e6:.0f} MB, {pooling} pooling")


if __name__ == "__main__":
    sys.exit(main())
