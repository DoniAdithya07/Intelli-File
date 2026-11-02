"""File-access permission (Phase 12, user request 2026-09-20).

On first launch the app asks before touching anything:

    all      — index everything the user can read: the home folder plus
               mounted drives (macOS/Linux) or the user profile plus every
               non-system drive (Windows), pruning system and app-data
               folders that are never someone's documents;
    limited  — only folders and single files the user picks;
    denied   — nothing is scanned; search is disabled until changed.

Stored in config/access.json as {"mode": ...}; `unset` until the user
answers. Downgrading (all → limited/denied) can also drop the index.
"""

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

ACCESS_FILE = "access.json"
MODES = ("all", "limited", "denied")

# Absolute folders never walked in "all" mode: system, applications,
# app-data and caches — bug class #1 at whole-computer scale.
_MAC_HOME_SKIP = {"Library", "Applications", ".Trash", "Music/Music", "Pictures/Photos Library.photoslibrary"}
_WIN_HOME_SKIP = {"AppData", "Application Data", "Local Settings", "NTUSER.DAT"}
_LINUX_HOME_SKIP = {".cache", ".local", ".config", "snap"}


def home() -> Path:
    return Path.home()


def whole_computer_roots() -> list[str]:
    """What "Allow all" indexes: the real home directory and real drives,
    always — there is no isolation override for this (see home(), which
    calls Path.home() directly), because a real user choosing "whole
    computer" must get their actual computer, not a sandboxed stand-in.

    Guard (2026-11-02, post-incident): that same fact makes "all" mode
    unsafe to ever reach on a machine running an isolated/test instance
    (INTELLIFILE_APP_DATA_DIR set) — two incidents on 2026-11-02 did
    exactly that, once via a real app resuming a pre-existing "all"
    config at startup, once via a still-unexplained "all" selection in a
    supposedly isolated instance. Both actually read real personal files
    because this function doesn't know or care that it was running
    somewhere "isolated" was only ever true for the app's OWN storage.
    So: when INTELLIFILE_APP_DATA_DIR is set (a real packaged app never
    sets this itself — only a developer, tester or script deliberately
    does), whole-computer enumeration additionally requires
    INTELLIFILE_ALLOW_WHOLE_COMPUTER=1. Refusing returns an empty list
    (fail safe: callers already treat an empty root list as "nothing to
    watch", never as an error) rather than raising, so a backend that
    inherited a stale "all" config from a shared real app-data directory
    still starts up cleanly with zero folders watched, not a crash.
    A real, unmodified end-user install is unaffected: it never sets
    INTELLIFILE_APP_DATA_DIR, so this check never applies to it.
    """
    if os.environ.get("INTELLIFILE_APP_DATA_DIR") and os.environ.get("INTELLIFILE_ALLOW_WHOLE_COMPUTER") != "1":
        logger.warning(
            "whole-computer access ('all' mode) refused: INTELLIFILE_APP_DATA_DIR is set (an isolated/test "
            "instance) without INTELLIFILE_ALLOW_WHOLE_COMPUTER=1. Returning zero roots instead of the real "
            "home directory and drives. Set INTELLIFILE_ALLOW_WHOLE_COMPUTER=1 if this instance genuinely "
            "needs to test whole-computer access against a disposable test profile."
        )
        return []
    roots = [str(home())]
    if sys.platform == "darwin":
        volumes = Path("/Volumes")
        if volumes.exists():
            boot = Path("/").resolve()
            for vol in sorted(volumes.iterdir()):
                try:
                    if vol.is_dir() and vol.resolve() != boot and not vol.name.startswith("."):
                        roots.append(str(vol))
                except OSError:
                    continue
    elif sys.platform == "win32":
        import string

        system_drive = (os.environ.get("SystemDrive") or "C:").rstrip("\\").upper()
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive) and f"{letter}:" != system_drive:
                roots.append(drive)
    else:
        for mount in ("/mnt", "/media"):
            m = Path(mount)
            if m.exists():
                roots += [str(p) for p in sorted(m.iterdir()) if p.is_dir()]
    return roots


def excluded_paths() -> set[str]:
    """Absolute paths pruned inside the whole-computer roots."""
    h = home()
    skip = _MAC_HOME_SKIP if sys.platform == "darwin" else _WIN_HOME_SKIP if sys.platform == "win32" else _LINUX_HOME_SKIP
    return {str(h / rel) for rel in skip}


class AccessPolicy:
    def __init__(self, config_dir: Path):
        self._path = config_dir / ACCESS_FILE
        self.mode: str = "unset"
        try:
            stored = json.loads(self._path.read_text())
            if stored.get("mode") in MODES:
                self.mode = stored["mode"]
        except (OSError, ValueError, AttributeError):
            pass

    def set(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.mode = mode
        self._path.write_text(json.dumps({"mode": mode}, indent=2))

    @property
    def allows_indexing(self) -> bool:
        return self.mode in ("all", "limited")

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "allows_indexing": self.allows_indexing,
            "whole_computer_roots": whole_computer_roots() if self.mode == "all" else [],
            "excluded_paths": sorted(excluded_paths()),
        }
