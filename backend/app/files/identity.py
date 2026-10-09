"""File identity: a stable id + content hash per file, per the PRD's
File Identity section. The hash is what lets us skip re-indexing a file
whose mtime changed but content didn't (e.g. a touch, or a save that
rewrote identical bytes).
"""

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

_HASH_CHUNK_SIZE = 1024 * 1024  # 1 MB, so hashing never loads a whole large file into memory

# Windows file attributes of a cloud placeholder (OneDrive "Files On-Demand",
# and other cloud-sync clients): the bytes are not on this PC, and opening
# or reading the file makes Windows download it. A scan of a OneDrive
# folder used to pull the user's whole cloud library down (2026-10-04).
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
ONLINE_ONLY_ATTRIBUTES = FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS


# The skip reason shown on the Status screen for a placeholder (scan and watcher alike).
ONLINE_ONLY_REASON = "online-only OneDrive file; make it available offline to index it"


class OnlineOnlyFile(OSError):
    """Raised instead of reading a cloud placeholder: callers that treat any
    OSError as "unreadable right now" skip it; the folder scan counts it."""


def is_online_only(path: Path) -> bool:
    """True for a placeholder whose content lives only in the cloud.
    st_file_attributes exists on Windows only; elsewhere this is False."""
    return bool(getattr(os.stat(path), "st_file_attributes", 0) & ONLINE_ONLY_ATTRIBUTES)


def compute_file_hash(path: Path) -> str:
    """SHA-256 of the file's bytes. Every path that would read a new file
    (scan, watcher, single file) hashes it first, so the placeholder check
    lives here."""
    if is_online_only(path):
        raise OnlineOnlyFile(f"online-only (OneDrive): {path.name} is not downloaded to this PC, so it is not read")
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_HASH_CHUNK_SIZE):
            hasher.update(chunk)
    return hasher.hexdigest()


def new_file_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class FileRecord:
    file_id: str
    path: str
    size: int
    modified_time: float
    hash: str
    deleted: bool = False
    indexed: bool = True  # False from record creation until the chunks are stored (Phase 11 crash recovery)


def build_file_record(path: Path, file_id: str | None = None, file_hash: str | None = None, indexed: bool = True) -> FileRecord:
    stat = path.stat()
    return FileRecord(
        file_id=file_id or new_file_id(),
        path=str(path),
        size=stat.st_size,
        modified_time=stat.st_mtime,
        hash=file_hash if file_hash is not None else compute_file_hash(path),
        indexed=indexed,
    )
