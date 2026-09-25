"""Phase 14: gather everything the installer ships into
desktop/src-tauri/resources/ so `npm run tauri build` can bundle it.

    resources/backend/        the PyInstaller onedir bundle (backend/dist/intellifile-backend)
    resources/models/         embedding, Whisper, CLIP, reranker, LLM
    resources/data/           english_words.txt (photo-query spell check)
    resources/sample-folder/  a small folder the grader can index in under a minute
    resources/HOW_TO_RUN.md   the one-page guide

Run AFTER pyinstaller (see intellifile-backend.spec):
    backend/venv-build/bin/python backend/scripts/assemble_resources.py
Pass --no-models to skip the 1.5 GB copy when only the backend changed.
"""

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
RESOURCES = ROOT / "desktop" / "src-tauri" / "resources"
DIST = BACKEND / "dist" / "intellifile-backend"
MODELS_TO_SHIP = ["bge-small-en-v1.5", "whisper-base.en", "clip-vit-base-patch16", "ms-marco-MiniLM-L-6-v2", "llm"]  # bge replaced MiniLM 2026-09-21 (Phase 13 measurement)
# Inside model folders, only what inference reads (no .cache, no unused variants).
MODEL_SKIP_DIRS = {".cache"}


def copy_tree(src: Path, dst: Path, skip_dirs: set[str] = frozenset()) -> int:
    if dst.exists():
        shutil.rmtree(dst)
    total = 0
    for path in src.rglob("*"):
        if any(part in skip_dirs for part in path.relative_to(src).parts):
            continue
        if path.is_file():
            target = dst / path.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            total += path.stat().st_size
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-models", action="store_true")
    args = parser.parse_args()
    RESOURCES.mkdir(parents=True, exist_ok=True)

    if not DIST.exists():
        print(f"No PyInstaller bundle at {DIST} — run pyinstaller first.")
        return 1
    size = copy_tree(DIST, RESOURCES / "backend")
    print(f"backend bundle: {size / 1e6:.0f} MB")

    if not args.no_models:
        for name in MODELS_TO_SHIP:
            src = BACKEND / "models" / name
            if not src.exists():
                print(f"  missing model: {name} (download script not run?)")
                continue
            size = copy_tree(src, RESOURCES / "models" / name, MODEL_SKIP_DIRS)
            print(f"  model {name}: {size / 1e6:.0f} MB")
    size = copy_tree(BACKEND / "data", RESOURCES / "data")
    print(f"data: {size / 1e6:.1f} MB")

    from eval_corpus import DOCS, write_corpus  # the Phase 20 corpus doubles as the sample folder
    sample = RESOURCES / "sample-folder"
    if sample.exists():
        shutil.rmtree(sample)
    write_corpus(sample)
    try:
        from PIL import Image, ImageDraw
        for name, color, shape in (("red circle.png", (220, 60, 60), "circle"), ("blue square.png", (60, 90, 220), "square"), ("green triangle.png", (60, 170, 90), "triangle")):
            img = Image.new("RGB", (640, 480), (245, 245, 240))
            d = ImageDraw.Draw(img)
            if shape == "circle":
                d.ellipse((170, 90, 470, 390), fill=color)
            elif shape == "square":
                d.rectangle((170, 90, 470, 390), fill=color)
            else:
                d.polygon([(320, 80), (120, 400), (520, 400)], fill=color)
            img.save(sample / "photos" / name) if (sample / "photos").mkdir(parents=True, exist_ok=True) is None else None
    except ImportError:
        pass
    print(f"sample folder: {len(DOCS)} documents + 3 photos")

    shutil.copy2(ROOT / "docs" / "HOW_TO_RUN.md", RESOURCES / "HOW_TO_RUN.md")
    total = sum(p.stat().st_size for p in RESOURCES.rglob("*") if p.is_file())
    print(f"\nresources ready at {RESOURCES}: {total / 1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
