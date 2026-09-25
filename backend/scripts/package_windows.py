"""Package the Windows build as a portable zip — the Windows deliverable.

After `npm run tauri build -- --no-bundle`, the release folder holds
IntelliFile.exe with its resources copied next to it (backend/, models/,
data/, sample-folder/, HOW_TO_RUN.md — the layout the shell looks for at
runtime). This gathers exactly those into IntelliFile/ and zips it:

    desktop/src-tauri/target/release/IntelliFile/          the unzipped app
    desktop/src-tauri/target/release/IntelliFile-windows.zip

No installer: at ~2.6 GB (1.7 GB of models) the app is past the 2 GB limit
of both NSIS and WiX/MSI (makensis: "error mmapping file ... out of range",
2026-09-25). A portable folder also runs without admin rights.

Run with:  backend\\venv\\Scripts\\python.exe backend\\scripts\\package_windows.py
"""

import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "desktop" / "src-tauri" / "target" / "release"
APP_DIR = RELEASE / "IntelliFile"
ZIP_PATH = RELEASE / "IntelliFile-windows.zip"
RESOURCES = ["backend", "models", "data", "sample-folder", "HOW_TO_RUN.md"]
# Model weights barely compress; storing them keeps packaging to seconds.
STORED_SUFFIXES = {".onnx", ".gguf", ".dll", ".pyd", ".zip", ".jpg", ".png", ".mp4"}


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
    for name in RESOURCES:
        src = RELEASE / name
        if src.is_dir():
            shutil.copytree(src, APP_DIR / name)
        else:
            shutil.copy2(src, APP_DIR / name)

    ZIP_PATH.unlink(missing_ok=True)
    with zipfile.ZipFile(ZIP_PATH, "w", allowZip64=True) as zf:
        for path in sorted(APP_DIR.rglob("*")):
            if path.is_file():
                method = zipfile.ZIP_STORED if path.suffix.lower() in STORED_SUFFIXES else zipfile.ZIP_DEFLATED
                zf.write(path, path.relative_to(APP_DIR.parent), compress_type=method)

    size = sum(p.stat().st_size for p in APP_DIR.rglob("*") if p.is_file())
    print(f"app folder: {APP_DIR} ({size / 1e9:.2f} GB)")
    print(f"zip:        {ZIP_PATH} ({ZIP_PATH.stat().st_size / 1e9:.2f} GB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
