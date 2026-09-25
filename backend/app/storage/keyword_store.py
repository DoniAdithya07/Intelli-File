"""Lexical (BM25) index over chunk content, per the PRD's Keyword Index
section. Uses SQLite's built-in FTS5 extension — no extra dependency,
persists to disk automatically, and FTS5 ships a real bm25() ranking
function, not an approximation.
"""

import re
import sqlite3
import threading
from pathlib import Path

_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    file_id UNINDEXED,
    content
);
"""

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _build_match_expression(query: str, exact_phrase: bool) -> str | None:
    """FTS5's MATCH argument is its own mini query language — quotes,
    AND/OR/NOT, column filters, and punctuation like '.' or ':' are all
    syntax, not literal characters. Passing a raw user query straight
    through crashes the moment someone types an apostrophe or a period.
    This builds a MATCH expression that always treats the input as plain
    text to search for, never as FTS5 syntax.
    """
    if exact_phrase:
        escaped = query.replace('"', '""')
        return f'"{escaped}"'

    tokens = _TOKEN_RE.findall(query)
    if not tokens:
        return None
    # Each token individually quoted -> FTS5 treats every one as a literal
    # phrase, and ANDs them together (its default between phrases) — so
    # nothing in the token text (or between tokens) can be read as an
    # operator.
    return " ".join(f'"{t}"' for t in tokens)


class KeywordStore:
    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        # vocabulary() is read on every search (typo correction) but only
        # changes when chunks are written; cache it and drop the cache on
        # writes. Without this a large index re-reads its whole fts5vocab
        # table per keystroke-search. (2026-09-11 audit.)
        self._vocabulary_cache: dict[int, dict[str, int]] = {}
        self.version = 0  # bumped on every write; search caches derived data by it
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            # fts5vocab exposes the index's own vocabulary (every distinct
            # term + how many rows it appears in) — used for typo
            # correction against words that actually appear in the user's
            # own files, rather than a generic bundled English dictionary.
            self._conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vocab USING fts5vocab(chunks_fts, 'row')"
            )
            self._conn.commit()

    def add_chunks(self, chunks: list[dict]) -> None:
        """Each dict needs chunk_id, file_id, content."""
        if not chunks:
            return
        with self._lock:
            self._vocabulary_cache.clear()
            self.version += 1
            self._conn.executemany(
                "INSERT INTO chunks_fts (chunk_id, file_id, content) VALUES (:chunk_id, :file_id, :content)",
                chunks,
            )
            self._conn.commit()

    def clear(self) -> None:
        """Drop every chunk (an embedding-model change re-indexes everything)."""
        with self._lock:
            self._vocabulary_cache.clear()
            self.version += 1
            self._conn.execute("DELETE FROM chunks_fts")
            self._conn.commit()

    def delete_by_file_id(self, file_id: str) -> None:
        with self._lock:
            self._vocabulary_cache.clear()
            self.version += 1
            self._conn.execute("DELETE FROM chunks_fts WHERE file_id = ?", (file_id,))
            self._conn.commit()

    def search_terms(self, terms: list[str], top_k: int = 20, require: int | None = None) -> list[dict]:
        """OR-search over `terms`, keeping rows that contain at least
        `require` of them (default: half, rounded up). The fallback for a
        query whose filler words defeat the default AND — "find my notes
        about horizontal scaling" has no row with "find" (2026-09-21)."""
        terms = [t for t in terms if t]
        if not terms:
            return []
        require = require or max(1, (len(terms) + 1) // 2)
        expression = " OR ".join(f'"{t}"' for t in terms)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT chunk_id, file_id, content, bm25(chunks_fts) AS raw_score,
                       snippet(chunks_fts, 2, '**', '**', '...', 20) AS highlighted
                FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY raw_score LIMIT ?
                """,
                (expression, top_k * 4),
            ).fetchall()
        out = []
        for r in rows:
            words = set(_TOKEN_RE.findall(r["content"].lower()))
            if sum(1 for t in terms if t.lower() in words) >= require:
                out.append({"chunk_id": r["chunk_id"], "file_id": r["file_id"], "content": r["content"], "highlighted": r["highlighted"], "score": -r["raw_score"]})
            if len(out) >= top_k:
                break
        return out

    def search(self, query: str, top_k: int = 20, exact_phrase: bool = False) -> list[dict]:
        """Returns chunk_id/file_id/content/highlighted/score (lower bm25()
        score = better match; we negate it so higher score = better,
        matching the vector store's convention elsewhere). `highlighted`
        wraps matched terms in **double asterisks**, via FTS5's own
        snippet() function — no manual highlighting logic needed.

        `query` is always treated as plain text, never as FTS5 syntax —
        see _build_match_expression. Pass exact_phrase=True for the PRD's
        Exact Match Override behavior (the whole query as one phrase).
        """
        match_expression = _build_match_expression(query, exact_phrase)
        if match_expression is None:
            return []

        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    chunk_id, file_id, content,
                    bm25(chunks_fts) AS raw_score,
                    snippet(chunks_fts, 2, '**', '**', '...', 20) AS highlighted
                FROM chunks_fts
                WHERE chunks_fts MATCH ?
                ORDER BY raw_score
                LIMIT ?
                """,
                (match_expression, top_k),
            ).fetchall()
        return [
            {
                "chunk_id": r["chunk_id"],
                "file_id": r["file_id"],
                "content": r["content"],
                "highlighted": r["highlighted"],
                "score": -r["raw_score"],
            }
            for r in rows
        ]

    def vocabulary(self, min_length: int = 3) -> dict[str, int]:
        """Every distinct term in the index and how many rows contain it,
        via fts5vocab. Skips very short terms (rarely useful to correct
        typos against, and adds noise)."""
        with self._lock:
            cached = self._vocabulary_cache.get(min_length)
            if cached is not None:
                return cached
            rows = self._conn.execute(
                "SELECT term, cnt FROM chunks_vocab WHERE length(term) >= ?", (min_length,)
            ).fetchall()
            vocabulary = {r["term"]: r["cnt"] for r in rows}
            self._vocabulary_cache[min_length] = vocabulary
            return vocabulary

    def close(self) -> None:
        with self._lock:
            self._conn.close()
