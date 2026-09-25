"""Application data directory layout.

Shipping target is Windows (%LOCALAPPDATA%\\IntelliFile). macOS/Linux
equivalents exist only so the backend can be developed and tested on a
non-Windows dev machine.
"""

import os
import sys
from pathlib import Path


def get_app_data_dir() -> Path:
    override = os.environ.get("INTELLIFILE_APP_DATA_DIR")
    if override:
        return Path(override).expanduser()  # tests and portable installs
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "IntelliFile"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "IntelliFile"
    return Path.home() / ".local" / "share" / "IntelliFile"


def ensure_app_dirs() -> dict[str, Path]:
    root = get_app_data_dir()
    subdirs = {
        "root": root,
        "database": root / "database",
        "vector_index": root / "vector_index",
        "keyword_index": root / "keyword_index",
        "cache": root / "cache",
        "logs": root / "logs",
        "config": root / "config",
    }
    for path in subdirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return subdirs


# ----- bundled assets (Phase 14) -----
#
# In development the models and data live in backend/models and
# backend/data. In the packaged app the backend is a PyInstaller bundle
# inside the Tauri resources folder, next to `models/` and `data/`, and
# the desktop shell tells it where via environment variables. Resolution
# order: explicit env var → next to the frozen executable → the repo.


def _repo_backend_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _frozen_dir() -> Path | None:
    if getattr(sys, "frozen", False):
        # onedir bundle: <dist>/intellifile-backend/intellifile-backend → look in <dist>/intellifile-backend/.. too
        return Path(sys.executable).resolve().parent
    return None


def _resolve(env_var: str, name: str) -> Path:
    override = os.environ.get(env_var)
    if override:
        return Path(override).expanduser()
    frozen = _frozen_dir()
    if frozen is not None:
        for candidate in (frozen / name, frozen.parent / name):
            if candidate.exists():
                return candidate
        return frozen.parent / name
    return _repo_backend_dir() / name


def models_dir() -> Path:
    return _resolve("INTELLIFILE_MODELS_DIR", "models")


def data_dir() -> Path:
    return _resolve("INTELLIFILE_DATA_DIR", "data")
