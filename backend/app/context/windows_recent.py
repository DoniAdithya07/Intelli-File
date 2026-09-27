"""Personalization from day one (next-round improvement 1, 2026-09-26).

Objective 2 learns from file-access history, but a fresh install has none:
the profile stays in cold start until COLD_START_EVENTS events. Windows
already keeps that history. Every file opened from Explorer or an app's
Open dialog gets a shortcut in %APPDATA%\\Microsoft\\Windows\\Recent; the
shortcut is created the first time the file is opened (while it stays in
the list) and rewritten every time it is opened again. So each shortcut
gives one or two real "opened" events: its creation time and its last
write time.

Only with the user's consent (setting `import_windows_recent`, off by
default); read-only; nothing leaves the machine. Only files IntelliFile
has indexed become events, tagged meta.source = "windows_recent" so they
can be told apart, cleared, and never imported twice.

The .lnk reader follows Microsoft's [MS-SHLLINK] format — header, optional
target ID list, then LinkInfo with the local base path (or a network share
name) plus a common path suffix. Pure Python: nothing new to ship.
"""

import os
import struct
import sys
from pathlib import Path

SOURCE = "windows_recent"
SAME_OPEN_SECONDS = 60      # creation and last write this close = one open, not two
SESSION_GAP_SECONDS = 30 * 60

_HEADER_SIZE = 0x4C
_HAS_ID_LIST = 0x01
_HAS_LINK_INFO = 0x02
_VOLUME_AND_LOCAL_PATH = 0x01
_NETWORK_AND_SUFFIX = 0x02


def recent_dir() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if sys.platform != "win32" or not appdata:
        return None
    path = Path(appdata) / "Microsoft" / "Windows" / "Recent"
    return path if path.is_dir() else None


def _cstring(data: bytes, offset: int, unicode: bool) -> str:
    if offset <= 0 or offset >= len(data):
        return ""
    if unicode:
        end = offset
        while end + 1 < len(data) and data[end:end + 2] != b"\x00\x00":
            end += 2
        return data[offset:end].decode("utf-16-le", errors="replace")
    end = data.find(b"\x00", offset)
    raw = data[offset:end if end != -1 else len(data)]
    try:
        return raw.decode("mbcs")  # the ANSI code page the shortcut was written with
    except LookupError:  # not Windows
        return raw.decode("latin-1")


def lnk_target(data: bytes) -> str | None:
    """The target path stored in a shell link, or None if it has no
    LinkInfo (shortcuts to virtual folders, control panel items…)."""
    if len(data) < _HEADER_SIZE or struct.unpack_from("<I", data, 0)[0] != _HEADER_SIZE:
        return None
    flags = struct.unpack_from("<I", data, 0x14)[0]
    pos = _HEADER_SIZE
    if flags & _HAS_ID_LIST:
        if pos + 2 > len(data):
            return None
        pos += 2 + struct.unpack_from("<H", data, pos)[0]
    if not flags & _HAS_LINK_INFO or pos + 28 > len(data):
        return None
    info = data[pos:pos + struct.unpack_from("<I", data, pos)[0]]
    if len(info) < 28:
        return None
    header_size, info_flags, _volume, local_base, network, suffix = struct.unpack_from("<IIIIII", info, 4)
    local_base_u = suffix_u = 0
    if header_size >= 0x24 and len(info) >= 0x24:
        local_base_u, suffix_u = struct.unpack_from("<II", info, 0x1C)
    tail = _cstring(info, suffix_u, True) if suffix_u else _cstring(info, suffix, False)
    if info_flags & _VOLUME_AND_LOCAL_PATH:
        base = _cstring(info, local_base_u, True) if local_base_u else _cstring(info, local_base, False)
    elif info_flags & _NETWORK_AND_SUFFIX and network:
        # CommonNetworkRelativeLink: size, flags, NetNameOffset (from its own start)
        net = info[network:]
        base = _cstring(net, struct.unpack_from("<I", net, 8)[0], False) if len(net) >= 12 else ""
        if base and tail:
            base += "\\"
    else:
        return None
    path = (base + tail).strip()
    return path or None


def read_recent(folder: Path | None = None) -> list[tuple[str, float]]:
    """(target path, time opened) for every shortcut in the Recent folder:
    one entry at the shortcut's creation and, if it was opened again later,
    one at its last write. Unreadable shortcuts are skipped."""
    folder = folder or recent_dir()
    if folder is None:
        return []
    opens: list[tuple[str, float]] = []
    for lnk in folder.glob("*.lnk"):
        try:
            stat = lnk.stat()
            target = lnk_target(lnk.read_bytes()[:65536])
        except OSError:
            continue
        if not target:
            continue
        first, last = stat.st_ctime, stat.st_mtime
        opens.append((target, first))
        if last - first > SAME_OPEN_SECONDS:
            opens.append((target, last))
    return opens


def import_recent(usage_store, file_record_store, folder: Path | None = None) -> dict:
    """Turn Recent-folder opens of indexed files into usage events. Safe to
    run again: an open already imported (same file, same time) is skipped.
    Returns counts for the UI and the log."""
    opens = read_recent(folder)
    by_path = {os.path.normcase(os.path.normpath(r.path)): r for r in file_record_store.list_active()}
    seen = usage_store.imported_keys(SOURCE)
    events = []
    matched_files = set()
    for target, ts in opens:
        record = by_path.get(os.path.normcase(os.path.normpath(target)))
        if record is None:
            continue
        matched_files.add(record.file_id)
        key = (record.path, round(ts, 3))
        if key in seen:
            continue
        seen.add(key)
        events.append({"ts": round(ts, 3), "file_id": record.file_id, "path": record.path})
    added = usage_store.import_events("file_opened", events, source=SOURCE, session_gap_seconds=SESSION_GAP_SECONDS)
    return {"shortcuts": len({t for t, _ in opens}), "indexed_files": len(matched_files), "events_added": added}
