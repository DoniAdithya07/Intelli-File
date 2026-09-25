"""One-time setup step (dev machine only, needs internet): fetches the
local language model behind Phase 19's agent into backend/models/, so the
app never needs internet at runtime. Bundled into the installer in Phase 14.

Qwen2.5-1.5B-Instruct, GGUF Q4_K_M (~1.1 GB): small enough to run on a
laptop CPU at usable speed, and reliable at the one thing the agent needs
from it — following a tool-calling protocol and writing a short cited
answer. Pass `--size 3b` for the 3B build (~2 GB) if the grading laptop
turns out to have the headroom; the agent code does not care which.

Run with:
    backend/venv/bin/python backend/scripts/download_llm_model.py [--size 1.5b|3b]
"""

import argparse
import shutil
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download

CHOICES = {
    "1.5b": ("Qwen/Qwen2.5-1.5B-Instruct-GGUF", "qwen2.5-1.5b-instruct-q4_k_m.gguf"),
    "3b": ("Qwen/Qwen2.5-3B-Instruct-GGUF", "qwen2.5-3b-instruct-q4_k_m.gguf"),
}
TARGET_DIR = Path(__file__).resolve().parents[1] / "models" / "llm"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", choices=CHOICES, default="1.5b")
    args = parser.parse_args()
    repo, filename = CHOICES[args.size]
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {repo}/{filename} ...")
    downloaded = hf_hub_download(repo_id=repo, filename=filename)
    shutil.copy(downloaded, TARGET_DIR / filename)
    print(f"\nLLM ready at: {TARGET_DIR / filename} ({(TARGET_DIR / filename).stat().st_size / 1e9:.2f} GB)")


if __name__ == "__main__":
    sys.exit(main())
