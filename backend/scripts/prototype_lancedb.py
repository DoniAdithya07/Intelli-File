"""Phase 1 smoke test: prove the LanceDB storage abstraction actually
works end to end (create table, insert vectors, query nearest neighbors,
delete). Run with:

    backend/venv/bin/python backend/scripts/prototype_lancedb.py
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.storage import LanceDBVectorStore  # noqa: E402

DIMENSION = 4
DB_PATH = Path(__file__).resolve().parent / "_prototype_db"


def main():
    if DB_PATH.exists():
        shutil.rmtree(DB_PATH)

    store = LanceDBVectorStore(str(DB_PATH))
    store.create_table("chunks", dimension=DIMENSION)

    store.upsert(
        "chunks",
        [
            {
                "id": "chunk-1",
                "file_id": "file-1",
                "vector": [0.1, 0.2, 0.3, 0.4],
                "payload": {"content": "Horizontal scaling allows additional instances..."},
            },
            {
                "id": "chunk-2",
                "file_id": "file-2",
                "vector": [0.9, 0.8, 0.7, 0.6],
                "payload": {"content": "Unrelated content about baking bread."},
            },
        ],
    )

    results = store.query("chunks", vector=[0.1, 0.2, 0.3, 0.35], top_k=2)
    print("Query results (most similar first):")
    for r in results:
        print(f"  id={r['id']} file_id={r['file_id']} score={r['score']:.4f} payload={r['payload']}")

    assert results[0]["id"] == "chunk-1", "Nearest neighbor search returned the wrong closest vector"

    store.delete("chunks", ["chunk-2"])
    remaining = store.query("chunks", vector=[0.1, 0.2, 0.3, 0.35], top_k=10)
    assert len(remaining) == 1 and remaining[0]["id"] == "chunk-1", "Delete did not remove the expected record"

    store.upsert(
        "chunks",
        [{"id": "chunk-3", "file_id": "file-1", "vector": [0.15, 0.25, 0.35, 0.4], "payload": {}}],
    )
    store.delete_by_file_id("chunks", "file-1")
    remaining_after_file_delete = store.query("chunks", vector=[0.1, 0.2, 0.3, 0.35], top_k=10)
    assert remaining_after_file_delete == [], "delete_by_file_id should remove every chunk for that file"

    shutil.rmtree(DB_PATH)
    print("\nLanceDB prototype OK: create/insert/query/delete/delete_by_file_id all work as expected.")


if __name__ == "__main__":
    main()
