"""Relational metadata store for tracked files (path, hash, size, mtime,
tombstone flag). LanceDB is vector-search-oriented and awkward for exact
lookups like "find the record for this path" or "list everything not
tombstoned" — SQLite is the right tool here, per the PRD's own note that
SQLite is a good fit for relational metadata even while LanceDB handles
vectors. Kept as its own small module so it can be swapped later without
touching the vector storage code.
"""

import sqlite3
import threading
from pathlib import Path

from ..files.identity import FileRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    file_id TEXT PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    size INTEGER NOT NULL,
    modified_time REAL NOT NULL,
    hash TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
CREATE INDEX IF NOT EXISTS idx_files_hash ON files(hash);
"""

# Phase 11: a record is written BEFORE its chunks (so the watcher and the
# scan can find it), which means a crash between the two leaves a record
# with the new hash and no chunks — and the next scan would skip it as
# "unchanged" forever. `indexed` is set only after the chunks are stored;
# anything still 0 at the next scan is retried. Added with ALTER TABLE so
# existing databases migrate in place.
_MIGRATIONS = [
    ("indexed", "ALTER TABLE files ADD COLUMN indexed INTEGER NOT NULL DEFAULT 1"),
]


class FileRecordStore:
    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Bumped on every write. Readers that derive something expensive
        # from the whole table (search's filename vocabulary and active
        # list, rebuilt per query until 2026-09-21) cache by this number.
        self.version = 0
        with self._lock:
            self._conn.executescript(_SCHEMA)
            columns = {r["name"] for r in self._conn.execute("PRAGMA table_info(files)").fetchall()}
            for column, statement in _MIGRATIONS:
                if column not in columns:
                    self._conn.execute(statement)
            self._conn.commit()

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> FileRecord:
        return FileRecord(
            file_id=row["file_id"],
            path=row["path"],
            size=row["size"],
            modified_time=row["modified_time"],
            hash=row["hash"],
            deleted=bool(row["deleted"]),
            indexed=bool(row["indexed"]) if "indexed" in row.keys() else True,
        )

    def mark_indexed(self, file_id: str, indexed: bool = True) -> None:
        with self._lock:
            self._conn.execute("UPDATE files SET indexed = ? WHERE file_id = ?", (int(indexed), file_id))
            self._conn.commit()
            self.version += 1

    def list_unfinished(self) -> list[FileRecord]:
        """Active records whose chunks were never confirmed stored — what a
        crash mid-index leaves behind."""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM files WHERE deleted = 0 AND indexed = 0").fetchall()
        return [self._row_to_record(r) for r in rows]

    def get_by_path(self, path: str) -> FileRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()
        return self._row_to_record(row) if row else None

    def get_by_file_id(self, file_id: str) -> FileRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM files WHERE file_id = ?", (file_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def upsert(self, record: FileRecord) -> None:
        self._vacate_path(record.path, record.file_id)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO files (file_id, path, size, modified_time, hash, deleted, indexed)
                VALUES (:file_id, :path, :size, :modified_time, :hash, :deleted, :indexed)
                ON CONFLICT(file_id) DO UPDATE SET
                    path=excluded.path,
                    size=excluded.size,
                    modified_time=excluded.modified_time,
                    hash=excluded.hash,
                    deleted=excluded.deleted,
                    indexed=excluded.indexed
                """,
                {
                    "file_id": record.file_id,
                    "path": record.path,
                    "size": record.size,
                    "modified_time": record.modified_time,
                    "hash": record.hash,
                    "deleted": int(record.deleted),
                    "indexed": int(record.indexed),
                },
            )
            self._conn.commit()
            self.version += 1

    def _vacate_path(self, path: str, keep_file_id: str) -> None:
        """Make `path` free for `keep_file_id`. `path` is UNIQUE, and a
        tombstoned row keeps its path until cleanup purges it — so renaming
        notes2.txt over a deleted notes.txt raised IntegrityError (found
        2026-09-21; the exception then killed the watchdog thread and every
        watched folder went quiet until restart). The row in the way is
        tombstoned (it is not on disk under that path any more, whatever
        its state) and parked under a path no real file can have, so
        cleanup still finds it by file_id and purges its chunks."""
        with self._lock:
            row = self._conn.execute("SELECT file_id FROM files WHERE path = ?", (path,)).fetchone()
            if row is None or row["file_id"] == keep_file_id:
                return
            parked = f"{path}\x00{row['file_id']}"
            self._conn.execute("UPDATE files SET path = ?, deleted = 1 WHERE file_id = ?", (parked, row["file_id"]))
            self._conn.commit()
            self.version += 1

    def rename(self, old_path: str, new_path: str) -> FileRecord | None:
        """Update the path for the record at old_path (used for RENAME/MOVED
        events). Does not touch the hash — a plain rename doesn't change content.
        """
        existing = self.get_by_path(old_path)
        if existing is None:
            return None
        self._vacate_path(new_path, existing.file_id)
        updated = FileRecord(
            file_id=existing.file_id,
            path=new_path,
            size=existing.size,
            modified_time=existing.modified_time,
            hash=existing.hash,
            deleted=existing.deleted,
            indexed=existing.indexed,
        )
        self.upsert(updated)
        return updated

    def mark_deleted(self, path: str) -> FileRecord | None:
        existing = self.get_by_path(path)
        if existing is None:
            return None
        tombstoned = FileRecord(
            file_id=existing.file_id,
            path=existing.path,
            size=existing.size,
            modified_time=existing.modified_time,
            hash=existing.hash,
            deleted=True,
            indexed=existing.indexed,
        )
        self.upsert(tombstoned)
        return tombstoned

    def list_active(self) -> list[FileRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM files WHERE deleted = 0").fetchall()
        return [self._row_to_record(r) for r in rows]

    def find_active_by_hash(self, file_hash: str, exclude_file_id: str | None = None) -> FileRecord | None:
        """Find another active file with identical content, so the indexer
        can reuse its chunks/embeddings instead of recomputing them (per
        the PRD's Duplicate Files section)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM files WHERE hash = ? AND deleted = 0 AND file_id != ? LIMIT 1",
                (file_hash, exclude_file_id or ""),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def list_tombstoned(self) -> list[FileRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM files WHERE deleted = 1").fetchall()
        return [self._row_to_record(r) for r in rows]

    def remove(self, file_id: str) -> None:
        """Hard-delete the metadata row itself. Only call this once the
        file's vectors/keyword entries have actually been purged (see
        indexing/cleanup.py) — otherwise a duplicate-file lookup could
        later point at chunks that no longer exist."""
        with self._lock:
            self._conn.execute("DELETE FROM files WHERE file_id = ?", (file_id,))
            self._conn.commit()
            self.version += 1

    def close(self) -> None:
        with self._lock:
            self._conn.close()
