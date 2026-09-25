"""File identity: a stable id + content hash per file, per the PRD's
File Identity section. The hash is what lets us skip re-indexing a file
whose mtime changed but content didn't (e.g. a touch, or a save that
rewrote identical bytes).
"""

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

_HASH_CHUNK_SIZE = 1024 * 1024  # 1 MB, so hashing never loads a whole large file into memory


def compute_file_hash(path: Path) -> str:
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
