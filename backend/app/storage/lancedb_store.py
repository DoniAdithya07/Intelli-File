import json
import logging
from datetime import timedelta
from typing import Any

import lancedb
import pyarrow as pa

from .base import VectorStore

logger = logging.getLogger(__name__)

# How long superseded table versions are kept before optimize() drops
# them. Nothing in the app reads old versions, so this only needs to be
# longer than any in-flight query.
KEEP_OLD_VERSIONS_FOR = timedelta(minutes=1)


class LanceDBVectorStore(VectorStore):
    """LanceDB-backed implementation of VectorStore. See base.py for the
    interface contract other storage engines (SQLite+sqlite-vec, DuckDB)
    would need to satisfy if we switch later.
    """

    def __init__(self, db_path: str):
        self.db = lancedb.connect(db_path)

    def drop_table(self, name: str) -> None:
        if name in self.db.table_names():
            self.db.drop_table(name)

    def create_table(self, name: str, dimension: int) -> None:
        if name in self.db.table_names():
            return
        schema = pa.schema(
            [
                pa.field("id", pa.string()),
                pa.field("file_id", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), dimension)),
                pa.field("payload", pa.string()),  # JSON-encoded metadata
            ]
        )
        self.db.create_table(name, schema=schema)

    def optimize(self, table: str) -> None:
        """Compact small fragments and drop superseded versions. Every
        upsert here is a delete plus an add, and each of those writes a new
        table version and a new data fragment, and nothing ever merged them:
        the live index had 3,003 versions and 667 fragments for ~300 files
        (found 2026-09-21), growing without bound and read on every query.
        Called after each folder scan and each cleanup sweep."""
        if table not in self.db.table_names():
            return
        try:
            self.db.open_table(table).optimize(cleanup_older_than=KEEP_OLD_VERSIONS_FOR)
        except Exception:
            # Maintenance must never take the index down with it.
            logger.exception("optimize() failed for table %s", table)

    def upsert(self, table: str, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        tbl = self.db.open_table(table)
        ids = [r["id"] for r in records]
        if ids:
            id_list = ", ".join(f"'{i}'" for i in ids)
            tbl.delete(f"id IN ({id_list})")
        rows = [
            {
                "id": r["id"],
                "file_id": r["file_id"],
                "vector": r["vector"],
                "payload": json.dumps(r.get("payload", {})),
            }
            for r in records
        ]
        tbl.add(rows)

    def query(self, table: str, vector: list[float], top_k: int = 20) -> list[dict[str, Any]]:
        tbl = self.db.open_table(table)
        results = tbl.search(vector).limit(top_k).to_list()
        return [
            {
                "id": r["id"],
                "file_id": r["file_id"],
                "score": r.get("_distance"),
                "payload": json.loads(r["payload"]) if r.get("payload") else {},
            }
            for r in results
        ]

    def delete(self, table: str, ids: list[str]) -> None:
        if not ids:
            return
        tbl = self.db.open_table(table)
        id_list = ", ".join(f"'{i}'" for i in ids)
        tbl.delete(f"id IN ({id_list})")

    def delete_by_file_id(self, table: str, file_id: str) -> None:
        tbl = self.db.open_table(table)
        escaped = file_id.replace("'", "''")
        tbl.delete(f"file_id = '{escaped}'")

    def get_payloads(self, table: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        """payload per record id, for the ids that exist. Lets search fill
        in page/heading/section for chunks that only BM25 retrieved — the
        keyword table stores no metadata (2026-09-21)."""
        if not ids:
            return {}
        tbl = self.db.open_table(table)
        id_list = ", ".join("'" + i.replace("'", "''") + "'" for i in ids)
        rows = tbl.search().where(f"id IN ({id_list})").select(["id", "payload"]).to_list()
        return {r["id"]: (json.loads(r["payload"]) if r.get("payload") else {}) for r in rows}

    def get_by_file_id(self, table: str, file_id: str) -> list[dict[str, Any]]:
        tbl = self.db.open_table(table)
        escaped = file_id.replace("'", "''")
        rows = tbl.search().where(f"file_id = '{escaped}'").to_list()
        return [
            {
                "id": r["id"],
                "file_id": r["file_id"],
                "vector": r["vector"],
                "payload": json.loads(r["payload"]) if r.get("payload") else {},
            }
            for r in rows
        ]
