"""Relational metadata store for tracked files (path, hash, size, mtime,
tombstone flag). LanceDB is vector-search-oriented and awkward for exact
lookups like "find the record for this path" or "list everything not
tombstoned" — SQLite is the right tool here, per the PRD's own note that
SQLite is a good fit for relational metadata even while LanceDB handles
vectors. Kept as its own small module so it can be swapped later without
touching the vector storage code.
"""

import dataclasses
import sqlite3
import threading
import time
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
CREATE INDEX IF NOT EXISTS idx_files_path_nocase ON files(path COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_files_hash ON files(hash);
CREATE TABLE IF NOT EXISTS deletions (
    path TEXT NOT NULL,
    deleted_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deletions_deleted_at ON deletions(deleted_at);
"""

# "What files were deleted in the last 24 hours": kept separately from
# `files` because a tombstoned row there is purged almost immediately
# (indexing/cleanup.py runs after every scan) and never carries a timestamp.
# This log outlives that purge; DELETION_RETENTION_DAYS bounds its growth.
DELETION_RETENTION_DAYS = 30

# Phase 11: a record is written BEFORE its chunks (so the watcher and the
# scan can find it), which means a crash between the two leaves a record
# with the new hash and no chunks — and the next scan would skip it as
# "unchanged" forever. `indexed` is set only after the chunks are stored;
# anything still 0 at the next scan is retried. Added with ALTER TABLE so
# existing databases migrate in place.
_MIGRATIONS = [
    ("indexed", "ALTER TABLE files ADD COLUMN indexed INTEGER NOT NULL DEFAULT 1"),
    # 2026-10-12: NULL = a video-extension file not probed yet (see files/media.py).
    ("audio_only", "ALTER TABLE files ADD COLUMN audio_only INTEGER"),
    ("speech_indexed", "ALTER TABLE files ADD COLUMN speech_indexed INTEGER NOT NULL DEFAULT 0"),
]


_PATHS_PER_QUERY = 500  # SQLite allows 999 bound parameters in older builds


class FileRecordStore:
    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL + NORMAL: a commit no longer waits on two fsyncs (one commit per file indexed).
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
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
            audio_only=None if row["audio_only"] is None else bool(row["audio_only"]),
            speech_indexed=bool(row["speech_indexed"]),
        )

    def mark_indexed(self, file_id: str, indexed: bool = True) -> None:
        with self._lock:
            self._conn.execute("UPDATE files SET indexed = ? WHERE file_id = ?", (int(indexed), file_id))
            self._conn.commit()
            self.version += 1

    def set_audio_only(self, file_id: str, audio_only: bool) -> None:
        self._set_flag(file_id, "audio_only", int(audio_only))

    def set_speech_indexed(self, file_id: str, done: bool = True) -> None:
        self._set_flag(file_id, "speech_indexed", int(done))

    def _set_flag(self, file_id: str, column: str, value: int) -> None:
        with self._lock:
            self._conn.execute(f"UPDATE files SET {column} = ? WHERE file_id = ?", (value, file_id))  # column is ours, never user input
            self._conn.commit()
            self.version += 1

    def list_unfinished(self) -> list[FileRecord]:
        """Active records whose chunks were never confirmed stored — what a
        crash mid-index leaves behind."""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM files WHERE deleted = 0 AND indexed = 0").fetchall()
        return [self._row_to_record(r) for r in rows]

    def get_by_path(self, path: str, ignore_case: bool = False) -> FileRecord | None:
        """ignore_case: Windows paths are case-insensitive, so a typed
        address in another letter case must still find the indexed record. Only the
        read-side lookups ask for it; the UNIQUE path itself stays exact."""
        collate = " COLLATE NOCASE" if ignore_case else ""
        # ORDER BY deleted: with ignore_case, two rows can differ only by
        # letter case (not UNIQUE under NOCASE) - the live one must win.
        order = " ORDER BY deleted" if ignore_case else ""
        with self._lock:
            row = self._conn.execute(f"SELECT * FROM files WHERE path = ?{collate}{order}", (path,)).fetchone()
        return self._row_to_record(row) if row else None

    def get_by_file_id(self, file_id: str) -> FileRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM files WHERE file_id = ?", (file_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def get_by_paths(self, paths: list[str]) -> dict[str, FileRecord]:
        """Active records for these paths (case-insensitive, like Windows)
        in a few queries, not one per path (a folder listing asks for up to
        a thousand). Keyed by the path as the caller asked for it."""
        found: dict[str, FileRecord] = {}
        for start in range(0, len(paths), _PATHS_PER_QUERY):
            batch = paths[start:start + _PATHS_PER_QUERY]
            with self._lock:
                rows = self._conn.execute(
                    f"SELECT * FROM files WHERE deleted = 0 AND path COLLATE NOCASE IN ({', '.join('?' for _ in batch)})", batch
                ).fetchall()
            exact = {r["path"]: r for r in rows}
            by_lower = {r["path"].lower(): r for r in rows}
            for path in batch:
                row = exact.get(path) or by_lower.get(path.lower())
                if row is not None:
                    found[path] = self._row_to_record(row)
        return found

    def upsert(self, record: FileRecord) -> None:
        self._vacate_path(record.path, record.file_id)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO files (file_id, path, size, modified_time, hash, deleted, indexed, audio_only, speech_indexed)
                VALUES (:file_id, :path, :size, :modified_time, :hash, :deleted, :indexed, :audio_only, :speech_indexed)
                ON CONFLICT(file_id) DO UPDATE SET
                    path=excluded.path,
                    size=excluded.size,
                    modified_time=excluded.modified_time,
                    hash=excluded.hash,
                    deleted=excluded.deleted,
                    indexed=excluded.indexed,
                    audio_only=excluded.audio_only,
                    speech_indexed=excluded.speech_indexed
                """,
                {
                    "file_id": record.file_id,
                    "path": record.path,
                    "size": record.size,
                    "modified_time": record.modified_time,
                    "hash": record.hash,
                    "deleted": int(record.deleted),
                    "indexed": int(record.indexed),
                    "audio_only": None if record.audio_only is None else int(record.audio_only),
                    "speech_indexed": int(record.speech_indexed),
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
        updated = dataclasses.replace(existing, path=new_path)
        self.upsert(updated)
        return updated

    def mark_deleted(self, path: str, *, log_as_deleted: bool = True) -> FileRecord | None:
        """Tombstone the record at `path`. `log_as_deleted` is False for the
        "forget this folder/file" and "access turned off" paths and for a
        brand-new file that failed to index: the file itself is untouched
        on disk, so it must not appear in "what files were deleted" (found
        2026-10-14 review: every unwatched file was being reported as
        deleted)."""
        existing = self.get_by_path(path)
        if existing is None:
            return None
        tombstoned = dataclasses.replace(existing, deleted=True)
        self.upsert(tombstoned)
        if log_as_deleted:
            self._log_deletion(path)
        return tombstoned

    def _log_deletion(self, path: str) -> None:
        cutoff = time.time() - DELETION_RETENTION_DAYS * 86400
        with self._lock:
            self._conn.execute("INSERT INTO deletions (path, deleted_at) VALUES (?, ?)", (path, time.time()))
            self._conn.execute("DELETE FROM deletions WHERE deleted_at < ?", (cutoff,))
            self._conn.commit()

    def list_deletions(self, since: float = 0.0) -> list[dict]:
        """[{"path", "deleted_at"}, ...], newest first, for files deleted at
        or after `since` (kept up to DELETION_RETENTION_DAYS)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT path, deleted_at FROM deletions WHERE deleted_at >= ? ORDER BY deleted_at DESC", (since,)
            ).fetchall()
        return [{"path": r["path"], "deleted_at": r["deleted_at"]} for r in rows]

    def list_active(self) -> list[FileRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM files WHERE deleted = 0").fetchall()
        return [self._row_to_record(r) for r in rows]

    def find_active_by_hash(self, file_hash: str, exclude_file_id: str | None = None) -> FileRecord | None:
        """Find another active file with identical content, so the indexer
        can reuse its chunks/embeddings instead of recomputing them (per
        the PRD's Duplicate Files section). A record still `indexed = 0` has
        the new hash but possibly the old (or no) chunks, so it is no source."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM files WHERE hash = ? AND deleted = 0 AND indexed = 1 AND file_id != ? LIMIT 1",
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
