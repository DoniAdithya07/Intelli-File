"""Live, interactive Phase 4 demo: indexes every supported file in a
folder, then lets you type search queries and see real results — both
semantic (meaning-based) and keyword (exact-word) — with scores and
snippets. Type 'exit' to quit.

Usage:
    backend/venv/bin/python backend/scripts/search_demo.py /path/to/folder
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.embeddings.model import EmbeddingModel, default_model_dir  # noqa: E402
from app.files.discovery import discover_files  # noqa: E402
from app.files.identity import build_file_record, compute_file_hash  # noqa: E402
from app.indexing import Indexer  # noqa: E402
from app.storage import FileRecordStore, KeywordStore, LanceDBVectorStore  # noqa: E402

MODEL_DIR = default_model_dir(Path(__file__).resolve().parents[1] / "models")


def snippet(text: str, length: int = 100) -> str:
    text = text.replace("\n", " ").strip()
    return text[:length] + ("..." if len(text) > length else "")


def main():
    if len(sys.argv) != 2:
        print("Usage: search_demo.py /path/to/folder")
        sys.exit(1)

    folder = Path(sys.argv[1]).expanduser().resolve()
    if not folder.is_dir():
        print(f"Not a folder: {folder}")
        sys.exit(1)

    if not (MODEL_DIR / "model.onnx").exists():
        print("Model not found — run scripts/download_model.py first.")
        sys.exit(1)

    demo_data_dir = folder / ".intellifile_search_demo"
    print("Loading the AI model (only needs to happen once at startup)...")
    model = EmbeddingModel(MODEL_DIR)
    print(f"Model ready, using: {model.active_provider}")

    vector_store = LanceDBVectorStore(str(demo_data_dir / "vectors"))
    keyword_store = KeywordStore(demo_data_dir / "keyword.db")
    file_record_store = FileRecordStore(demo_data_dir / "files.db")
    indexer = Indexer(model, vector_store, keyword_store, file_record_store)

    print(f"\nScanning and indexing {folder} ...")
    count = 0
    for path in discover_files([str(folder)]):
        if demo_data_dir in path.parents:
            continue
        existing = file_record_store.get_by_path(str(path))
        current_hash = compute_file_hash(path)
        if existing is not None and existing.hash == current_hash:
            print(f"  already indexed (unchanged): {path.name}")
            count += 1
            continue
        record = build_file_record(path, file_id=existing.file_id if existing else None, file_hash=current_hash)
        file_record_store.upsert(record)
        n_chunks = indexer.index_file(path, record.file_id, record.hash)
        print(f"  indexed: {path.name} ({n_chunks} chunk(s))")
        count += 1

    if count == 0:
        print("No supported files (.pdf, .docx, .txt, .md) found in that folder.")
        sys.exit(0)

    print(f"\nIndexed {count} file(s). Now search them for real — type a query and press Enter.")
    print("Type 'exit' to quit.\n")

    while True:
        try:
            query = input("Search: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not query or query.lower() == "exit":
            break

        query_vector = model.embed_texts([query])[0].tolist()
        semantic_results = vector_store.query("chunks", query_vector, top_k=3)
        # A rough, provisional relevance cutoff — semantic search always
        # returns its closest match even when nothing is actually relevant,
        # so without a cutoff a totally unrelated query would still get a
        # confident-looking "top result". The real threshold belongs in
        # Phase 13 (Evaluation), tuned against real Recall/MRR data, not
        # guessed from a handful of demo queries like this one is.
        RELEVANCE_CUTOFF = 1.6
        semantic_results = [r for r in semantic_results if r["score"] <= RELEVANCE_CUTOFF]
        keyword_results = keyword_store.search(query, top_k=3)

        print("\n  Semantic (meaning-based) results — LOWER distance = more similar:")
        if not semantic_results:
            print("    (none relevant enough — nothing crossed the similarity cutoff)")
        for r in semantic_results:
            record = file_record_store.get_by_file_id(r["file_id"])
            name = Path(record.path).name if record else r["file_id"]
            print(f"    {name}  (distance={r['score']:.4f})")
            print(f"      \"{snippet(r['payload'].get('content', ''))}\"")

        print("  Keyword (exact-word) results — HIGHER score = better match:")
        if not keyword_results:
            print("    (none)")
        for r in keyword_results:
            record = file_record_store.get_by_file_id(r["file_id"])
            name = Path(record.path).name if record else r["file_id"]
            print(f"    {name}  (score={r['score']:.4f})")
            print(f"      \"{snippet(r['content'])}\"")
        print()

    vector_store, keyword_store, file_record_store  # keep references alive until here
    print("Goodbye.")


if __name__ == "__main__":
    main()
