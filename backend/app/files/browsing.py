"""Read-only folder browsing for the Explorer-style window (2026-10-12).

Everything here only reads names, sizes and dates: nothing opens, launches,
reads the content of or changes a file. The rules for WHICH paths may be
browsed live in one place, `check_browse_path`, because the API is reachable
by anything that holds the token and a path string is the classic way past a
check: `..`, `\\\\?\\` and `\\\\.\\` device paths, UNC shares, alternate data
streams (`file.txt:stream`), drive-relative `C:foo`, reserved device names,
trailing dots/spaces (Windows ignores them, so `C:\\Windows.` is Windows) and
junctions that point out of an allowed folder (the real path is checked too).
"""

import ctypes
import os
import re
import string
import sys
import uuid
from ctypes import wintypes
from pathlib import Path

from ..routes.common import safe_path
from .discovery import (
    AUDIO_EXTENSIONS, CODE_EXTENSIONS, DOCUMENT_EXTENSIONS, HTML_EXTENSIONS, IMAGE_EXTENSIONS, MORE_DOCUMENT_EXTENSIONS,
    PRESENTATION_EXTENSIONS, SPREADSHEET_EXTENSIONS, VIDEO_EXTENSIONS,
)

WINDOWS = sys.platform == "win32"

DOCUMENT_KIND_EXTENSIONS = DOCUMENT_EXTENSIONS | SPREADSHEET_EXTENSIONS | PRESENTATION_EXTENSIONS | HTML_EXTENSIONS | MORE_DOCUMENT_EXTENSIONS | {".rtf"}
ARCHIVE_EXTENSIONS = {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz", ".iso", ".cab"}


class BrowseError(Exception):
    """A request that cannot be served, with the HTTP status to answer it with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ----- which paths may be browsed -----

_RESERVED_NAMES = {"con", "prn", "aux", "nul", "conin$", "conout$", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
# Folders at the top of any drive that hold system bookkeeping, never a person's files.
_DRIVE_ROOT_SYSTEM_NAMES = {"$recycle.bin", "system volume information", "$windows.~bt", "$windows.~ws", "config.msi", "windows.old", "recovery"}
_SEGMENT_SPLIT = re.compile(r"[\\/]+")
_HIDDEN, _SYSTEM = 0x2, 0x4  # FILE_ATTRIBUTE_HIDDEN / FILE_ATTRIBUTE_SYSTEM


def _norm(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("\\/") + os.sep)


def system_folders() -> list[str]:
    """Normalised paths of the folders that are never browsed: Windows itself,
    the program folders, ProgramData and the user's own AppData (hidden app
    data, not documents; the folder scan skips them too)."""
    env = os.environ.get
    candidates = [env("SystemRoot"), env("ProgramData"), env("ProgramFiles"), env("ProgramFiles(x86)"), env("ProgramW6432"), str(Path.home() / "AppData")]
    return [_norm(c) for c in candidates if c]


def blocked_reason(path: Path, system: list[str] | None = None) -> str | None:
    """Why this (already normalised, absolute) path may not be browsed; None
    when it may. A scan passes `system` once instead of rebuilding it per entry."""
    norm = _norm(path)
    if any(_inside(norm, folder) for folder in (system if system is not None else system_folders())):
        return "this is a Windows or program folder"
    parts = Path(norm).parts  # ('c:\\', 'users', ...)
    if len(parts) > 1 and parts[1] in _DRIVE_ROOT_SYSTEM_NAMES:
        return "this is a system folder"
    home = Path.home()
    users = _norm(home.parent)
    if _inside(norm, users) and norm != users and not _inside(norm, _norm(home)) and not _inside(norm, _norm(home.parent / "Public")):
        return "this is another user's folder"
    return None


def drive_letters() -> list[str]:
    if not WINDOWS:
        return []
    mask = ctypes.WinDLL("kernel32").GetLogicalDrives()
    return [letter for i, letter in enumerate(string.ascii_uppercase) if mask & (1 << i)]


def check_browse_path(raw: str) -> Path:
    """The path as a clean absolute Path, or BrowseError. See the module docstring for what is refused."""
    raw = (raw or "").strip()
    if not raw:
        raise BrowseError(400, "No path given.")
    if "\x00" in raw:
        raise BrowseError(400, "That is not a valid path.")
    if raw.startswith(("\\\\", "//")):
        raise BrowseError(403, "Network shares and device paths cannot be browsed here; use a mapped drive letter.")
    segments = [s for s in _SEGMENT_SPLIT.split(raw) if s]
    if any(s == ".." for s in segments):
        raise BrowseError(400, "Paths with .. are not accepted.")
    if ":" in raw[2:] or any(s != s.rstrip(" .") and s != "." for s in segments):
        raise BrowseError(400, "That is not a valid path.")
    if WINDOWS and any(s.split(".")[0].lower() in _RESERVED_NAMES for s in segments[1:]):
        raise BrowseError(400, "That is not a valid path.")
    path = safe_path(raw)  # the rule every other route applies: absolute, no NUL, normalised
    if path is None or (WINDOWS and not re.fullmatch(r"[A-Za-z]:\\.*", str(path))):
        raise BrowseError(400, "The path must be absolute, like C:\\Users.")
    if WINDOWS:
        path = Path(str(path)[0].upper() + str(path)[1:])
        if path.drive[0] not in drive_letters():
            raise BrowseError(404, "That drive does not exist.")
    elif not _inside(_norm(path), _norm(Path.home())):
        raise BrowseError(403, "Only your own folders can be browsed.")
    for candidate in (path, Path(os.path.realpath(path))):  # the real path too: a junction must not lead out
        reason = blocked_reason(candidate)
        if reason:
            raise BrowseError(403, f"This folder is not available: {reason}.")
    return path


# ----- drives and quick access -----

_DRIVE_TYPES = {2: "removable", 3: "fixed", 4: "network", 5: "cdrom", 6: "ramdisk"}
_SEM_FAILCRITICALERRORS = 1


def list_drives() -> list[dict]:
    """Every drive with its label and sizes. An empty card reader or CD tray
    is listed as not ready (sizes null) instead of raising Windows' "no disk"
    dialog; a network drive is listed without sizes, because asking an
    unreachable share can stall for many seconds."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if WINDOWS else None
    drives = []
    previous = wintypes.DWORD(0)
    if kernel32 is not None:
        kernel32.SetThreadErrorMode(_SEM_FAILCRITICALERRORS, ctypes.byref(previous))
    try:
        for letter in drive_letters():
            root = f"{letter}:\\"
            kind = _DRIVE_TYPES.get(kernel32.GetDriveTypeW(root), "unknown")
            label = ctypes.create_unicode_buffer(261)
            ready = bool(kernel32.GetVolumeInformationW(root, label, 261, None, None, None, None, 0)) if kind != "network" else True
            total, free = _disk_sizes(kernel32, root) if ready and kind != "network" else (None, None)
            drives.append({
                "letter": letter, "path": root, "label": label.value, "type": kind, "ready": ready,
                "total_bytes": total, "free_bytes": free,
            })
    finally:
        if kernel32 is not None:
            kernel32.SetThreadErrorMode(previous.value, None)
    return drives


def _disk_sizes(kernel32, root: str) -> tuple[int | None, int | None]:
    free, total, total_free = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
    if kernel32.GetDiskFreeSpaceExW(root, ctypes.byref(free), ctypes.byref(total), ctypes.byref(total_free)):
        return total.value, free.value
    return None, None


# Windows "known folders" (they follow OneDrive's folder backup, unlike ~/Documents).
KNOWN_FOLDERS = {
    "Desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "Documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "Downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "Pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "Videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
    "Music": "4BD8D571-6D19-48D3-BE97-422220080E43",
}


def known_folder(name: str) -> Path | None:
    """Where Windows keeps the user's Desktop, Documents, … (None when it cannot say)."""
    if WINDOWS:
        shell32 = ctypes.WinDLL("shell32")
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_char_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        folder_id = uuid.UUID(KNOWN_FOLDERS[name]).bytes_le  # a GUID is laid out in memory as these 16 bytes
        out = ctypes.c_void_p()
        try:
            if shell32.SHGetKnownFolderPath(folder_id, 0, None, ctypes.byref(out)) == 0 and out.value:
                return Path(ctypes.wstring_at(out.value))
        finally:
            if out.value:
                ctypes.WinDLL("ole32").CoTaskMemFree(ctypes.c_void_p(out.value))
    fallback = Path.home() / name
    return fallback if fallback.is_dir() else None


def quick_access(watched_roots: list[str], root_exists) -> list[dict]:
    """Desktop, Documents, … that exist and may be browsed, then the folders
    the user told IntelliFile to index (`root_exists(path)` is a cheap answer,
    never a stall on an unplugged share). Drive roots are left to the drive list."""
    items, seen = [], set()
    for name in KNOWN_FOLDERS:
        path = known_folder(name)
        if path is None or not path.is_dir():
            continue
        try:
            path = check_browse_path(str(path))
        except BrowseError:
            continue
        seen.add(_norm(path))
        items.append({"name": name, "path": str(path), "kind": "known", "icon": name.lower()})
    for root in watched_roots:
        norm = _norm(root)
        if norm in seen or Path(root).parent == Path(root) or not root_exists(root):
            continue
        try:
            path = check_browse_path(root)
        except BrowseError:
            continue
        seen.add(norm)
        items.append({"name": path.name, "path": str(path), "kind": "pinned", "icon": "folder"})
    return items


# ----- listing a folder -----

def kind_of(name: str, is_dir: bool, audio_only: bool = False) -> str:
    """The group the UI icon and the type filter use."""
    if is_dir:
        return "folder"
    suffix = Path(name).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if audio_only or suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in DOCUMENT_KIND_EXTENSIONS:
        return "document"
    if suffix in CODE_EXTENSIONS:
        return "code"
    return "archive" if suffix in ARCHIVE_EXTENSIONS else "other"


def is_hidden_or_system(entry: os.DirEntry) -> bool:
    try:
        attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", None)
    except OSError:
        return True  # cannot even stat it: Explorer would not show it usefully either
    if attributes is None:  # not Windows: dotfiles
        return entry.name.startswith(".")
    return bool(attributes & (_HIDDEN | _SYSTEM))


def scan_folder(path: Path) -> list[dict]:
    """Every visible entry of a folder, folders first then by name (case
    ignored). Hidden and system entries, and folders that may not be browsed,
    are left out so the UI never offers a dead link."""
    try:
        with os.scandir(path) as scanner:
            listing = list(scanner)
    except PermissionError:
        raise BrowseError(403, "Windows refused access to this folder.") from None
    except FileNotFoundError:
        raise BrowseError(404, "That folder does not exist.") from None
    except NotADirectoryError:
        raise BrowseError(400, "That is not a folder.") from None
    except OSError as e:
        raise BrowseError(400, f"This folder cannot be read ({e.strerror or type(e).__name__}).") from None
    entries = []
    system = system_folders()
    for entry in listing:
        try:
            if is_hidden_or_system(entry):
                continue
            is_dir = entry.is_dir()
            if is_dir and blocked_reason(Path(entry.path), system):
                continue
            stat = entry.stat()
        except OSError:
            continue
        entries.append({
            "name": entry.name, "path": entry.path, "is_dir": is_dir,
            "size": None if is_dir else stat.st_size, "modified": stat.st_mtime,
        })
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return entries


def covered_by(path: str, roots: list[str]) -> bool:
    """True when `path` is, or is inside, one of the (normalised) watched `roots`."""
    norm = _norm(path)
    return any(_inside(norm, root) for root in roots)


def describe_entry(entry: dict, record, roots: list[str]) -> dict:
    """Adds kind, indexed and file_id to a scanned entry. A file is indexed
    when it has a finished record; a folder when it sits inside a watched folder."""
    is_dir = entry["is_dir"]
    audio_only = bool(record is not None and record.audio_only)
    indexed = covered_by(entry["path"], roots) if is_dir else record is not None and record.indexed
    return {**entry, "kind": kind_of(entry["name"], is_dir, audio_only), "indexed": bool(indexed), "file_id": record.file_id if record is not None and not is_dir else None}
