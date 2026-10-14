"""Phase 4 integration test: real files -> extract -> chunk -> embed ->
store, then prove both semantic (vector) and keyword (BM25) search
actually return the right file, duplicate files reuse chunks without
re-embedding, deleting a file removes it from both indexes, and tombstone
cleanup actually purges data. Run with:

    backend/venv/bin/python backend/scripts/prototype_indexing.py
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.embeddings.model import EmbeddingModel, default_model_dir  # noqa: E402
from app.files.identity import build_file_record  # noqa: E402
from app.indexing import Indexer, cleanup_tombstones, index_folder  # noqa: E402
from app.search import SearchService  # noqa: E402
from app.storage import FileRecordStore, KeywordStore, LanceDBVectorStore  # noqa: E402

MODEL_DIR = default_model_dir(Path(__file__).resolve().parents[1] / "models")


def main():
    if not (MODEL_DIR / "model.onnx").exists():
        print("Model not found — run scripts/download_model.py first.")
        sys.exit(1)

    workdir = Path(tempfile.mkdtemp())
    call_count = {"embed": 0}

    try:
        scaling_path = workdir / "scaling_notes.txt"
        scaling_path.write_text(
            "Horizontal scaling allows additional server instances to be provisioned "
            "automatically when traffic demand increases, distributed by a load balancer."
        )

        bread_path = workdir / "bread_recipe.txt"
        bread_path.write_text(
            "A good sourdough starter needs regular feeding with flour and water to "
            "stay active before you bake fresh bread at home."
        )

        model = EmbeddingModel(MODEL_DIR)
        real_embed = model.embed_texts

        def counting_embed(texts):
            call_count["embed"] += 1
            return real_embed(texts)

        model.embed_texts = counting_embed  # so we can prove duplicate reuse skips embedding

        vector_store = LanceDBVectorStore(str(workdir / "vectors"))
        keyword_store = KeywordStore(workdir / "keyword.db")
        file_record_store = FileRecordStore(workdir / "files.db")
        indexer = Indexer(model, vector_store, keyword_store, file_record_store)

        scaling_record = build_file_record(scaling_path)
        file_record_store.upsert(scaling_record)
        n1 = indexer.index_file(scaling_path, scaling_record.file_id, scaling_record.hash)

        bread_record = build_file_record(bread_path)
        file_record_store.upsert(bread_record)
        n2 = indexer.index_file(bread_path, bread_record.file_id, bread_record.hash)

        assert n1 > 0 and n2 > 0, "Both files should produce at least one chunk"
        embed_calls_after_initial_indexing = call_count["embed"]
        print(f"1. Indexed both files: OK ({n1} + {n2} chunks, {embed_calls_after_initial_indexing} embedding calls)")

        # --- 2. Semantic search: a query with NO shared keywords should still find the right file ---
        query_vec = model.embed_texts(["How can I handle sudden increases in traffic?"])[0]
        results = vector_store.query("chunks", query_vec.tolist(), top_k=5)
        assert results, "Expected at least one vector search result"
        top_file_id = results[0]["file_id"]
        assert top_file_id == scaling_record.file_id, (
            f"Semantic search should match the scaling file, got file_id={top_file_id}"
        )
        print("2. Semantic search (no shared keywords) found the right file: OK")

        # --- 3. Keyword search: exact term match ---
        kw_results = keyword_store.search("sourdough", top_k=5)
        assert kw_results and kw_results[0]["file_id"] == bread_record.file_id, "Keyword search should find the bread file"
        print("3. Keyword (BM25) search found the right file: OK")

        # --- 4. Duplicate file: identical content should reuse chunks, not re-embed ---
        duplicate_path = workdir / "scaling_notes_copy.txt"
        duplicate_path.write_text(scaling_path.read_text())  # byte-identical content
        duplicate_record = build_file_record(duplicate_path)
        file_record_store.upsert(duplicate_record)

        embed_calls_before_duplicate = call_count["embed"]
        n3 = indexer.index_file(duplicate_path, duplicate_record.file_id, duplicate_record.hash)
        embed_calls_after_duplicate = call_count["embed"]

        assert n3 == n1, "Duplicate file should reuse the same number of chunks as the original"
        assert embed_calls_after_duplicate == embed_calls_before_duplicate, (
            "Duplicate file indexing must NOT call the embedding model again"
        )
        dup_chunks = vector_store.get_by_file_id("chunks", duplicate_record.file_id)
        assert len(dup_chunks) == n1
        print(f"4. Duplicate file reused {n3} chunks with ZERO extra embedding calls: OK")

        # --- 5. Delete: removing a file's chunks removes it from both indexes ---
        indexer.delete_file(bread_record.file_id)
        kw_results_after_delete = keyword_store.search("sourdough", top_k=5)
        assert kw_results_after_delete == [], "Deleted file's content should no longer be keyword-searchable"
        remaining_vectors = vector_store.get_by_file_id("chunks", bread_record.file_id)
        assert remaining_vectors == [], "Deleted file's vectors should be gone"
        print("5. Delete removes a file from both vector and keyword indexes: OK")

        # --- 6. Tombstone cleanup: purges chunks AND the metadata row ---
        file_record_store.mark_deleted(str(scaling_path))
        assert [d["path"] for d in file_record_store.list_deletions()] == [str(scaling_path)], "mark_deleted must log to the deletion history"
        cleaned = cleanup_tombstones(indexer)
        assert cleaned == 1, f"Expected exactly 1 tombstoned file cleaned up, got {cleaned}"
        assert vector_store.get_by_file_id("chunks", scaling_record.file_id) == []
        assert file_record_store.get_by_file_id(scaling_record.file_id) is None, "Metadata row should be gone after cleanup"
        # The duplicate is untouched — it's a separate copy, not a reference to the original
        assert vector_store.get_by_file_id("chunks", duplicate_record.file_id), (
            "The duplicate's own chunks must survive cleanup of the original it copied from"
        )
        assert [d["path"] for d in file_record_store.list_deletions()] == [str(scaling_path)], (
            "The deletion log must outlive the tombstone purge, so 'deleted in the last 24 hours' still works after cleanup"
        )
        print("6. Tombstone cleanup purges chunks + metadata, without touching unrelated duplicates; the deletion log survives the purge: OK")

        # --- 6b. Unwatching a file (or turning off access) is not a real deletion:
        # the file is untouched on disk, so it must not appear in "what files were
        # deleted". Found by review 2026-10-14: every forgotten/un-indexed file was
        # being logged as deleted. ---
        forgotten_path = workdir / "forgotten.txt"
        forgotten_path.write_text("A file the user stops watching, not deletes.")
        forgotten_record = build_file_record(forgotten_path)
        file_record_store.upsert(forgotten_record)
        file_record_store.mark_deleted(str(forgotten_path), log_as_deleted=False)
        assert str(forgotten_path) not in {d["path"] for d in file_record_store.list_deletions()}, "forgetting a file must not log it as deleted"
        assert file_record_store.get_by_path(str(forgotten_path)).deleted, "it must still be tombstoned (so it stops matching active searches)"
        print("6b. mark_deleted(..., log_as_deleted=False): still tombstoned, but not logged as a real deletion: OK")

        # --- 7. Folder re-scan reconciles renames and deletions ---
        # Regression for the 2026-09-11 live test: the user renamed files on
        # disk, re-indexed, and search still returned the OLD paths (dead
        # links). A rescan must move a renamed file's record without
        # re-embedding, and purge records for files that vanished.
        scan_dir = workdir / "rescan"
        scan_dir.mkdir()
        (scan_dir / "old_name.txt").write_text("Photosynthesis converts sunlight into chemical energy in chloroplasts.")
        (scan_dir / "doomed.txt").write_text("This file will be deleted between scans.")
        assert index_folder(indexer, str(scan_dir)) == 2
        before = file_record_store.get_by_path(str(scan_dir / "old_name.txt"))
        assert before is not None and vector_store.get_by_file_id("chunks", before.file_id)

        (scan_dir / "old_name.txt").rename(scan_dir / "new_name.txt")
        (scan_dir / "doomed.txt").unlink()
        assert index_folder(indexer, str(scan_dir)) == 1

        after = file_record_store.get_by_path(str(scan_dir / "new_name.txt"))
        assert after is not None and after.file_id == before.file_id, "Rename must keep the same file_id"
        assert file_record_store.get_by_path(str(scan_dir / "old_name.txt")) is None, "Old path must be gone"
        assert vector_store.get_by_file_id("chunks", before.file_id), "Rename must not drop the vectors"
        assert file_record_store.get_by_path(str(scan_dir / "doomed.txt")) is None, "Deleted file's record must be purged"
        assert len(file_record_store.list_tombstoned()) == 0, "Rescan should leave no tombstones behind"
        logged = {d["path"] for d in file_record_store.list_deletions()}
        assert str(scan_dir / "doomed.txt") in logged, "A rescan-detected deletion must reach the deletion log too"
        assert str(scan_dir / "old_name.txt") not in logged, "A rename is not a deletion and must not be logged as one"

        search_service = SearchService(model, vector_store, keyword_store, file_record_store)
        hits = search_service.search("photosynthesis chloroplasts", top_k=5)
        assert hits and hits[0]["filename"] == "new_name.txt", f"Search must return the NEW name, got {[h['filename'] for h in hits]}"
        print("7. Folder re-scan: renamed file keeps its vectors under the new path, deleted file purged, search returns the new name: OK")

        # --- 8. Search never returns a file that vanished since the last scan ---
        (scan_dir / "new_name.txt").unlink()
        hits = search_service.search("photosynthesis chloroplasts", top_k=5)
        assert all(h["filename"] != "new_name.txt" for h in hits), "A deleted file must not appear in results"
        assert cleanup_tombstones(indexer) == 1, "Search should have tombstoned the missing file for cleanup"
        print("8. Search hides a file deleted since the last scan and tombstones it for cleanup: OK")

        # --- 9. Every chunk fits the embedding model's window, and a sentence
        # deep inside a long document is still found by meaning (2026-09-21:
        # 512-word chunks were silently cut to 256 tokens before embedding,
        # so anything past ~190 words of a chunk never reached the model) ---
        from app.chunking import chunk_document
        from app.embeddings.model import MAX_SEQUENCE_LENGTH
        from app.extraction.blocks import ExtractedBlock
        filler = "The quarterly meeting reviewed office supplies, parking arrangements and the holiday schedule. "
        buried = "Beekeepers replace the queen when a hive turns aggressive or stops laying."
        long_doc = scan_dir / "long_minutes.txt"
        long_doc.write_text(filler * 40 + buried + " " + filler * 40)
        chunks = chunk_document([ExtractedBlock(text=long_doc.read_text())], token_spans=model.token_spans)
        assert len(chunks) > 1, "a ~1000-word document must produce several chunks"
        for chunk in chunks:
            n_tokens = len(model.tokenizer.encode(chunk.content, add_special_tokens=True).ids)
            assert n_tokens <= MAX_SEQUENCE_LENGTH, f"chunk of {n_tokens} tokens exceeds the model window"
        index_folder(indexer, str(scan_dir))
        hits = search_service.search("when should I replace the queen bee in my hive", top_k=3)
        assert hits and hits[0]["filename"] == "long_minutes.txt", f"buried sentence not found by meaning: {[h['filename'] for h in hits]}"
        assert "Beekeepers" in hits[0]["matched_chunk"], "the matched chunk must be the one holding the sentence"
        print(f"9. Chunks stay within the model's {MAX_SEQUENCE_LENGTH}-token window ({len(chunks)} chunks) and a sentence buried mid-document is found by meaning: OK")

        # --- 10. A file whose extraction FAILS keeps its previous chunks and
        # its previous hash, so it stays searchable and is retried on the
        # next scan (2026-09-21: the old chunks were deleted before
        # extraction, and the new hash was saved, so the file vanished from
        # search for good). A valid PDF is indexed, then overwritten with
        # bytes pypdf cannot parse. ---
        from fpdf import FPDF
        pdf_path = scan_dir / "quarterly.pdf"
        pdf = FPDF()
        pdf.set_font("Helvetica", size=12)
        pdf.add_page()
        pdf.multi_cell(0, 10, "The zebra migration crosses the river every October.")
        pdf.output(str(pdf_path))
        index_folder(indexer, str(scan_dir))
        good_hash = file_record_store.get_by_path(str(pdf_path)).hash
        assert search_service.search("zebra migration river", top_k=3)[0]["filename"] == "quarterly.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 this is not a real pdf, as if half-written or locked")
        index_folder(indexer, str(scan_dir))
        record = file_record_store.get_by_path(str(pdf_path))
        assert record is not None and not record.deleted and record.hash == good_hash, "failed index must keep the previous record/hash so the next scan retries"
        hits = search_service.search("zebra migration river", top_k=3)
        assert hits and hits[0]["filename"] == "quarterly.pdf", "previous chunks must survive a failed re-index"
        print("10. Failed extraction keeps the old chunks searchable and the old hash (will retry), instead of wiping the file: OK")
        check_keyword_rollback(workdir)
        check_chunk_caps(workdir, indexer)

        print(
            "\nPhase 4 indexing pipeline OK: embedding, vector search, keyword search, "
            "duplicate reuse, delete, tombstone cleanup, rescan reconciliation, token-bounded chunking "
            "and failure-safe re-indexing all work as expected."
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_keyword_rollback(workdir: Path) -> None:
    """11. (2026-10-05) A keyword write that fails half-way leaves nothing:
    before, its first rows stayed in the open transaction and the NEXT
    file's commit saved them, rows of a file whose index step had failed."""
    store = KeywordStore(workdir / "rollback.db")
    try:
        store.add_chunks([{"chunk_id": "a", "file_id": "f1", "content": "marmoset orphan row"}, {"chunk_id": "b", "file_id": "f1"}])
        raise AssertionError("a chunk without content must fail the write")
    except Exception as e:  # noqa: BLE001 - sqlite reports the missing binding
        assert not isinstance(e, AssertionError), e
    store.add_chunks([{"chunk_id": "c", "file_id": "f2", "content": "unrelated heron notes"}])
    assert store.search("marmoset orphan") == [], "rows of a failed write were committed by the next one"
    assert store.search("heron")[0]["file_id"] == "f2"
    store.close()
    print("11. A keyword write that fails half-way is rolled back; the next file's commit saves only its own rows: OK")


def check_chunk_caps(workdir: Path, indexer) -> None:
    """12. (2026-10-12) A .json is embedded only up to DATA_CODE_CHUNK_CAP chunks, a .txt up to
    PLAIN_TEXT_CHUNK_CAP; the first scan is faster and documents are scanned before data files."""
    from app.files.discovery import DATA_CODE_CHUNK_CAP, PLAIN_TEXT_CHUNK_CAP, chunk_cap
    from app.indexing.folder_scan import _scan_order

    body = " ".join(f"word{i} alpha beta gamma delta" for i in range(6000))
    for name, cap in (("big.json", DATA_CODE_CHUNK_CAP), ("big.txt", PLAIN_TEXT_CHUNK_CAP)):
        path = workdir / name
        path.write_text(name + " " + body)  # distinct bytes: identical files reuse each other's chunks
        record = build_file_record(path)
        indexer.file_record_store.upsert(record)
        stored = indexer.index_file(path, record.file_id, record.hash)
        assert stored == cap, f"{name}: {stored} chunks stored, cap is {cap}"
    assert chunk_cap(".mp3") is None and chunk_cap(".rtf") > DATA_CODE_CHUNK_CAP
    order = sorted([Path("a.mp4"), Path("a.json"), Path("a.jpg"), Path("a.pdf")], key=_scan_order)
    assert [p.suffix for p in order] == [".pdf", ".json", ".jpg", ".mp4"], order
    print(f"12. Long files stop at their chunk cap ({DATA_CODE_CHUNK_CAP} data/code, {PLAIN_TEXT_CHUNK_CAP} text); scan order is documents, data, photos, video: OK")


if __name__ == "__main__":
    main()
