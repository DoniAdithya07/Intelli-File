"""Package the Windows build as one setup .exe (the installer).

Wraps the same folder the portable zip holds, after package_windows.py has
gathered it:

    desktop/src-tauri/target/release/IntelliFile/             the tested app folder
    desktop/src-tauri/target/release/IntelliFile-Setup.exe    the download

The user runs the one .exe; it unpacks the app into
%LOCALAPPDATA%\\Programs\\IntelliFile (no administrator password), adds
shortcuts and opens IntelliFile. Nothing is downloaded: the search engine,
the models and the sample folder are inside. Script: desktop/installer/IntelliFile.nsi.

NSIS cannot make an installer of 2 GB or more, and GitHub takes at most
2 GB per release file. The first try (2026-09-25, 2.52 GB of files) failed
on that limit, and so does solid LZMA ("error mmapping file ... out of
range", 2026-10-13). Per-file LZMA works: 1.64 GB, in about 55 minutes.
The build fails here, loudly, if the files ever stop fitting.

makensis is looked for in NSIS_HOME, then D:\\DevTools\\tauri\\NSIS (where the
Tauri tools put it on the development laptop), then on PATH.

Run with:  backend\\venv\\Scripts\\python.exe backend\\scripts\\package_installer.py
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "desktop" / "src-tauri" / "target" / "release"
APP_DIR = RELEASE / "IntelliFile"
SETUP = RELEASE / "IntelliFile-Setup.exe"
SCRIPT = ROOT / "desktop" / "installer" / "IntelliFile.nsi"
LIMIT = 2 * 1024**3  # NSIS's own limit, and GitHub's per release file
MUST_HAVE = ["IntelliFile.exe", "backend/intellifile-backend.exe", "models", "data", "sample-folder", "HOW_TO_RUN.md", "LICENSE"]


def find_makensis() -> Path | None:
    places = [os.environ.get("NSIS_HOME"), r"D:\DevTools\tauri\NSIS"]
    for place in places:
        if place and (Path(place) / "makensis.exe").exists():
            return Path(place) / "makensis.exe"
    found = shutil.which("makensis")
    return Path(found) if found else None


def main() -> int:
    missing = [name for name in MUST_HAVE if not (APP_DIR / name).exists()]
    if missing:
        print(f"{APP_DIR} is incomplete (missing: {', '.join(missing)}). Run package_windows.py first.")
        return 1
    makensis = find_makensis()
    if makensis is None:
        print("makensis.exe was not found. Set NSIS_HOME to the NSIS folder, or install NSIS 3.")
        return 1
    version = json.loads((ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))["version"]
    size = sum(f.stat().st_size for f in APP_DIR.rglob("*") if f.is_file())
    SETUP.unlink(missing_ok=True)
    started = time.time()
    done = subprocess.run(
        [str(makensis), "/V2", f"/DSRC={APP_DIR}", f"/DOUT={SETUP}", f"/DVERSION={version}", f"/DSIZE_KB={size // 1024}", str(SCRIPT)],
        cwd=SCRIPT.parent, capture_output=True, text=True, errors="replace",
    )
    tail = "\n".join((done.stdout + done.stderr).strip().splitlines()[-12:])
    if done.returncode != 0 or not SETUP.exists():
        print(tail)
        print(f"makensis failed (exit {done.returncode}). No installer was made; the portable zip is unaffected.")
        return 1
    made = SETUP.stat().st_size
    if made >= LIMIT:
        print(f"{SETUP.name} is {made:,} bytes: over the 2 GB limit of NSIS installers and of GitHub release files.")
        return 1
    digest = hashlib.sha256()
    with open(SETUP, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            digest.update(block)
    print(f"app folder: {APP_DIR} ({size / 1024**3:.2f} GB)")
    print(f"{SETUP.name}: {SETUP} ({made / 1024**3:.2f} GB, {made:,} bytes) in {time.time() - started:.0f} s")
    print(f"SHA-256: {digest.hexdigest().upper()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
