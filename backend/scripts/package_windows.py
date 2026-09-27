"""Package the Windows build as one portable zip, the Windows deliverable.

After `npm run tauri build -- --no-bundle`, the release folder holds
IntelliFile.exe with its resources copied next to it (backend/, models/,
data/, sample-folder/, HOW_TO_RUN.md, the layout the shell looks for at
runtime). This gathers exactly those into IntelliFile/ and zips it:

    desktop/src-tauri/target/release/IntelliFile/               the unzipped app
    desktop/src-tauri/target/release/IntelliFile-windows.zip    the download

Until 2026-09-27 the app came as two zips (the Ask model separately),
because stored-uncompressed it came to 2.52 GB, over GitHub's 2 GB limit
per release file. Compressing the native libraries and models brought it
to 1.87 GB, so one zip fits; the check below fails the build if it ever
stops fitting.

No installer: at ~2.5 GB unzipped (1.7 GB of models) the app is past the
2 GB limit of both NSIS and WiX/MSI (makensis: "error mmapping file ...
out of range", 2026-09-25). A portable folder also runs without admin rights.

Run with:  backend\\venv\\Scripts\\python.exe backend\\scripts\\package_windows.py
"""

import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from assemble_resources import MODELS_TO_SHIP  # noqa: E402  the one list of shipped models

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "desktop" / "src-tauri" / "target" / "release"
APP_DIR = RELEASE / "IntelliFile"
ZIP = RELEASE / "IntelliFile-windows.zip"
OLD_ZIPS = ["IntelliFile-part1.zip", "IntelliFile-part2.zip"]  # the two-part download before 2026-09-27
LLM_DIR = Path("models") / "llm"  # relative to APP_DIR
GITHUB_ASSET_LIMIT = 2 * 1024**3
RESOURCES = ["backend", "models", "data", "sample-folder", "HOW_TO_RUN.md"]
LEGAL_FILES = ["LICENSE", "THIRD_PARTY_NOTICES.md", "PRIVACY.md", "TERMS.md"]  # from the repository root
# Only files that are already compressed are stored. Native libraries and
# ONNX models were stored too until 2026-09-27; deflated, _lancedb.pyd goes
# from 327 to 103 MB and the download shrinks by about 650 MB in total, with
# byte-identical files once unzipped.
STORED_SUFFIXES = {".zip", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov", ".m4a", ".mp3"}
COMPRESS_LEVEL = 9


def main() -> int:
    exe = RELEASE / "IntelliFile.exe"
    missing = [str(p) for p in [exe, *(RELEASE / r for r in RESOURCES)] if not p.exists()]
    if missing:
        print("Missing (run assemble_resources.py and `npm run tauri build -- --no-bundle` first):")
        print("\n".join(f"  {m}" for m in missing))
        return 1
    if not (RELEASE / "backend" / "intellifile-backend.exe").exists():
        print("backend/ holds no intellifile-backend.exe — it is not a Windows build (rerun PyInstaller on Windows).")
        return 1

    if APP_DIR.exists():
        shutil.rmtree(APP_DIR)
    APP_DIR.mkdir()
    shutil.copy2(exe, APP_DIR / exe.name)
    for name in LEGAL_FILES:  # the licence and policies travel with the app
        shutil.copy2(ROOT / name, APP_DIR / name)
    for name in RESOURCES:
        src = RELEASE / name
        if name == "models":  # only the shipped models, never a stale one left in the release folder
            for model in MODELS_TO_SHIP:
                if (src / model).is_dir():
                    shutil.copytree(src / model, APP_DIR / name / model)
        elif src.is_dir():
            shutil.copytree(src, APP_DIR / name)
        else:
            shutil.copy2(src, APP_DIR / name)

    for old in OLD_ZIPS:
        (RELEASE / old).unlink(missing_ok=True)
    files = [p for p in sorted(APP_DIR.rglob("*")) if p.is_file()]
    if not any(p.relative_to(APP_DIR).is_relative_to(LLM_DIR) for p in files):
        print(f"No language model under {APP_DIR / LLM_DIR}; Ask mode would be missing.")
        return 1
    ZIP.unlink(missing_ok=True)
    with zipfile.ZipFile(ZIP, "w", allowZip64=True) as zf:
        for path in files:
            method = zipfile.ZIP_STORED if path.suffix.lower() in STORED_SUFFIXES else zipfile.ZIP_DEFLATED
            zf.write(path, path.relative_to(APP_DIR.parent), compress_type=method, compresslevel=COMPRESS_LEVEL)

    size = sum(p.stat().st_size for p in files)
    zip_size = ZIP.stat().st_size
    print(f"app folder: {APP_DIR} ({size / 1e9:.2f} GB)")
    print(f"{ZIP.name}: {ZIP} ({zip_size / 1e9:.2f} GB, {zip_size:,} bytes)")
    if zip_size >= GITHUB_ASSET_LIMIT:
        print("The zip is over GitHub's 2 GB release-file limit.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
