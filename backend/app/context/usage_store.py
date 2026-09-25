"""What the user does with the app, remembered locally — the raw material
for Objective 2 of the assignment (personalization from file-access
behaviour). Phase 16 only records and reports; Phase 17 turns it into a
profile, Phase 19 gives the agent the current session as context.

One row per event. `path`, `file_type` and `query` are stored on the
event itself rather than looked up later, because a file record may be
purged (deleted file, forgotten folder) long before its history stops
mattering ("you used to open this every Monday").

Sessions: events closer together than SESSION_GAP_SECONDS share a
session_id. "Working context" for the current query is the current
session — the files, types and queries touched since the last long gap.
"""

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

EVENT_KINDS = {
    "query",                  # a search ran; meta holds mode/kind + the top result file_ids
    "result_clicked",         # a result card selected with the mouse
    "file_opened",            # Enter / double-click / Open
    "file_revealed",          # Reveal in Finder / Explorer
    "recommendation_clicked", # Phase 17's home-screen recommendations
}

SESSION_GAP_SECONDS = 30 * 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    session_id TEXT NOT NULL,
    file_id TEXT,
    path TEXT,
    file_type TEXT,
    query TEXT,
    meta TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_events(ts);
CREATE INDEX IF NOT EXISTS idx_usage_kind_ts ON usage_events(kind, ts);
CREATE INDEX IF NOT EXISTS idx_usage_file ON usage_events(file_id);
"""


class UsageStore:
    def __init__(self, db_path: str | Path, session_gap_seconds: float = SESSION_GAP_SECONDS):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._gap = session_gap_seconds
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            last = self._conn.execute("SELECT ts, session_id FROM usage_events ORDER BY ts DESC LIMIT 1").fetchone()
        self._last_ts = last["ts"] if last else None
        self._session_id = last["session_id"] if last else None
        self.version = 0  # bumped on every write; the profile builder rebuilds when it moves

    # ----- writing -----

    def record(
        self,
        kind: str,
        *,
        file_id: str | None = None,
        path: str | None = None,
        query: str | None = None,
        meta: dict | None = None,
        ts: float | None = None,
    ) -> dict:
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind: {kind}")
        ts = time.time() if ts is None else ts
        file_type = Path(path).suffix.lower().lstrip(".") or None if path else None
        with self._lock:
            if self._session_id is None or self._last_ts is None or ts - self._last_ts > self._gap:
                self._session_id = str(uuid.uuid4())
            self._last_ts = max(ts, self._last_ts or ts)
            cursor = self._conn.execute(
                "INSERT INTO usage_events (ts, kind, session_id, file_id, path, file_type, query, meta) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, kind, self._session_id, file_id, path, file_type, query, json.dumps(meta) if meta else None),
            )
            self._conn.commit()
            self.version += 1
            return self._row_to_event(self._conn.execute("SELECT * FROM usage_events WHERE id = ?", (cursor.lastrowid,)).fetchone())

    def clear(self) -> int:
        with self._lock:
            n = self._conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
            self._conn.execute("DELETE FROM usage_events")
            self._conn.commit()
            self._last_ts = None
            self._session_id = None
            self.version += 1
            return n

    # ----- reading -----

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "ts": row["ts"],
            "kind": row["kind"],
            "session_id": row["session_id"],
            "file_id": row["file_id"],
            "path": row["path"],
            "file_type": row["file_type"],
            "query": row["query"],
            "meta": json.loads(row["meta"]) if row["meta"] else None,
        }

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]

    def recent(self, limit: int = 100, kinds: set[str] | None = None, since: float | None = None) -> list[dict]:
        """Newest first."""
        clauses, params = [], []
        if kinds:
            clauses.append(f"kind IN ({', '.join('?' for _ in kinds)})")
            params.extend(sorted(kinds))
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(f"SELECT * FROM usage_events {where} ORDER BY ts DESC, id DESC LIMIT ?", (*params, limit)).fetchall()
        return [self._row_to_event(r) for r in rows]

    def all_events(self) -> list[dict]:
        """Oldest first — what the profile builder (Phase 17) replays."""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM usage_events ORDER BY ts ASC, id ASC").fetchall()
        return [self._row_to_event(r) for r in rows]

    def sessions(self) -> list[list[dict]]:
        """Events grouped by session, oldest session first."""
        grouped: dict[str, list[dict]] = {}
        for event in self.all_events():
            grouped.setdefault(event["session_id"], []).append(event)
        return list(grouped.values())

    def current_session(self, now: float | None = None) -> dict:
        """The working context: what has been touched since the last long
        gap. Empty when the last event is older than the session gap."""
        now = time.time() if now is None else now
        with self._lock:
            last = self._conn.execute("SELECT ts, session_id FROM usage_events ORDER BY ts DESC LIMIT 1").fetchone()
            if last is None or now - last["ts"] > self._gap:
                return {"session_id": None, "started_at": None, "events": 0, "queries": [], "files": [], "file_types": []}
            rows = self._conn.execute("SELECT * FROM usage_events WHERE session_id = ? ORDER BY ts ASC", (last["session_id"],)).fetchall()
        events = [self._row_to_event(r) for r in rows]
        queries, files, types = [], [], []
        for e in events:
            if e["query"] and e["query"] not in queries:
                queries.append(e["query"])
            if e["path"] and e["kind"] != "query" and e["path"] not in files:
                files.append(e["path"])
            if e["file_type"] and e["kind"] != "query" and e["file_type"] not in types:
                types.append(e["file_type"])
        return {
            "session_id": last["session_id"],
            "started_at": events[0]["ts"],
            "events": len(events),
            "queries": queries,
            "files": files,
            "file_types": types,
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
