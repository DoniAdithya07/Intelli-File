"""Phase 5 integration test: RRF fusion math, exact-phrase override,
type: filters, file-level aggregation, and the "why this file"
explanation — using the real search pipeline against real indexed files.
Run with:

    backend/venv/bin/python backend/scripts/prototype_search.py
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.embeddings.model import EmbeddingModel, default_model_dir  # noqa: E402
from app.files.identity import build_file_record  # noqa: E402
from app.indexing import Indexer  # noqa: E402
from app.search import SearchService  # noqa: E402
from app.search.fusion import aggregate_chunks_to_files, reciprocal_rank_fusion  # noqa: E402
from app.search.query_parsing import parse_query  # noqa: E402
from app.storage import FileRecordStore, KeywordStore, LanceDBVectorStore  # noqa: E402

MODEL_DIR = default_model_dir(Path(__file__).resolve().parents[1] / "models")


def test_query_parsing():
    p = parse_query('"blue widget factory"')
    assert p.exact_phrase == "blue widget factory" and p.file_type is None
    p2 = parse_query("kubernetes notes type:pdf")
    assert p2.file_type == "pdf" and p2.exact_phrase is None and "kubernetes notes" in p2.text
    p3 = parse_query("plain query")
    assert p3.exact_phrase is None and p3.file_type is None and p3.text == "plain query"
    print("1. Query parsing (exact phrase / type filter / plain): OK")


def test_rrf_and_aggregation():
    # Chunk A ranks #1 in both lists -> should end up with the highest fused score.
    keyword_ranked = ["chunk-A", "chunk-B", "chunk-C"]
    semantic_ranked = ["chunk-A", "chunk-C", "chunk-B"]
    fused = reciprocal_rank_fusion([keyword_ranked, semantic_ranked])
    assert fused["chunk-A"] > fused["chunk-B"]
    assert fused["chunk-A"] > fused["chunk-C"]
    print("2. RRF fusion (agreement across both signals wins): OK")

    # File "f1" has two relevant chunks, file "f2" has one much stronger chunk.
    chunk_scores = {"c1": 0.5, "c2": 0.1, "c3": 0.6}
    chunk_to_file = {"c1": "f1", "c2": "f1", "c3": "f2"}
    aggregates = aggregate_chunks_to_files(chunk_scores, chunk_to_file)
    by_file = {a.file_id: a for a in aggregates}
    assert by_file["f1"].file_score > 0.5, "Secondary chunk should add a boost, not just take the max"
    assert by_file["f1"].file_score < 0.5 + 0.1, "Secondary chunk shouldn't be weighted as much as the primary"
    # Regression (2026-09-11): many mediocre chunks must never outrank one best chunk.
    many = {f"big{i}": 0.0143 for i in range(9)} | {"small": 0.0164}
    files = {f"big{i}": "big" for i in range(9)} | {"small": "small"}
    ranked = aggregate_chunks_to_files(many, files)
    assert ranked[0].file_id == "small", "a long document's pile of weak chunks outranked the single best chunk"
    assert by_file["f1"].best_chunk_id == "c1"
    print("3. File aggregation (top chunk + secondary boost, not plain average): OK")


def main():
    test_query_parsing()
    test_rrf_and_aggregation()

    if not (MODEL_DIR / "model.onnx").exists():
        print("Model not found — run scripts/download_model.py first.")
        sys.exit(1)

    workdir = Path(tempfile.mkdtemp())
    try:
        scaling_path = workdir / "scaling.txt"
        scaling_path.write_text(
            "Horizontal scaling allows additional server instances to be provisioned when "
            "traffic demand increases, distributed by a load balancer. Our blue widget factory "
            "monitoring dashboard also runs on this infrastructure."
        )
        bread_path = workdir / "bread.md"
        bread_path.write_text("A good sourdough starter needs regular feeding with flour and water.")
        gym_path = workdir / "gym.txt"
        gym_path.write_text("A balanced strength routine includes squats, deadlifts, and bench presses.")

        model = EmbeddingModel(MODEL_DIR)
        vector_store = LanceDBVectorStore(str(workdir / "vectors"))
        keyword_store = KeywordStore(workdir / "keyword.db")
        file_record_store = FileRecordStore(workdir / "files.db")
        indexer = Indexer(model, vector_store, keyword_store, file_record_store)
        search_service = SearchService(model, vector_store, keyword_store, file_record_store)

        for path in (scaling_path, bread_path, gym_path):
            record = build_file_record(path)
            file_record_store.upsert(record)
            indexer.index_file(path, record.file_id, record.hash)

        # --- Hybrid search: no shared words, should still find the right file ---
        results = search_service.search("how do servers handle a spike in visitors")
        assert results and results[0]["filename"] == "scaling.txt"
        assert results[0]["semantic_score"] is not None
        assert "meaning similarity" in " ".join(results[0]["why"]) or results[0]["keyword_score"] is not None
        print("4. Hybrid search (no shared words) found the right file with a real explanation: OK")

        # --- Exact phrase override: only the file containing the EXACT phrase should match ---
        exact_results = search_service.search('"blue widget factory"')
        assert len(exact_results) == 1 and exact_results[0]["filename"] == "scaling.txt"
        assert exact_results[0]["semantic_score"] is None, "Exact-phrase mode must skip semantic search entirely"
        assert exact_results[0]["keyword_score"] is not None
        assert "**blue widget factory**" in exact_results[0]["matched_chunk"] or "blue widget factory" in exact_results[0]["matched_chunk"].lower()
        print("5. Exact-phrase override (quoted query, BM25-only, highlighted): OK")

        # A near-miss (not the exact phrase) should NOT match under exact mode.
        near_miss = search_service.search('"widget factory blue"')
        assert near_miss == [], "A reordered phrase must not match under exact-phrase mode"
        print("6. Exact-phrase mode correctly rejects a non-matching phrase order: OK")

        # --- type: filter ---
        typed_results = search_service.search("starter flour water type:md")
        assert typed_results and all(r["filename"].endswith(".md") for r in typed_results)
        assert typed_results[0]["filename"] == "bread.md"
        print("7. type: filter restricts results to the requested file type: OK")

        typed_results_wrong_type = search_service.search("starter flour water type:pdf")
        assert typed_results_wrong_type == [], "Filtering to a type with no matches should return nothing, not fall back"
        print("8. type: filter correctly returns nothing when no file of that type matches: OK")

        # --- 8b. A hit found ONLY by keyword still says where in the file it
        # is. Exact-phrase mode skips the meaning path entirely, so this is
        # the pure BM25 case; the PDF's second page holds the phrase.
        # (2026-09-21: the keyword table stores no page/heading, so such
        # hits had page=None and no "Found on page" reason.) ---
        from fpdf import FPDF
        pdf_path = workdir / "manual.pdf"
        pdf = FPDF()
        pdf.set_font("Helvetica", size=12)
        pdf.add_page()
        pdf.multi_cell(0, 10, "Chapter one is about setting up the workshop and the tools you need.")
        pdf.add_page()
        pdf.multi_cell(0, 10, "Chapter two: the purple lathe calibration procedure must be repeated monthly.")
        pdf.output(str(pdf_path))
        record = build_file_record(pdf_path)
        file_record_store.upsert(record)
        indexer.index_file(pdf_path, record.file_id, record.hash)
        keyword_only = search_service.search('"purple lathe calibration"')
        assert keyword_only and keyword_only[0]["filename"] == "manual.pdf" and keyword_only[0]["semantic_score"] is None
        assert keyword_only[0]["page"] == 2, f"keyword-only hit lost its page: {keyword_only[0]['page']!r}"
        assert any(w == "Found on page 2" for w in keyword_only[0]["why"]), keyword_only[0]["why"]
        print("8b. Keyword-only hit carries its page number and 'Found on page' reason: OK")

        # --- Totally unrelated query: no keyword overlap, nothing semantically close ---
        nothing_results = search_service.search("hi nanna movie")
        assert nothing_results == [], "An unrelated query should return no results, not a forced weak match"
        print("9. Unrelated query correctly returns no results: OK")

        # --- Regression: punctuation must never crash search. FTS5's MATCH
        # syntax treats quotes/colons/periods/parens as operators, not
        # literal characters — a raw query containing them used to raise
        # sqlite3.OperationalError instead of just searching for the words. ---
        for punctuation_query in [
            "working out at the gym.",
            "what's for breakfast",
            'hello: (world) "test"',
            "a-b-c & d/e",
        ]:
            punctuation_results = search_service.search(punctuation_query)  # must not raise
            assert isinstance(punctuation_results, list)
        print("10. Punctuation in a query never crashes search (FTS5 syntax characters are sanitized): OK")

        # --- Regression, live test 2026-09-11: search must match FILE NAMES.
        # "abhisek plan" returned README.md and "rasmalai" returned nothing,
        # although files with exactly those names existed. And the spelling
        # corrector must not "fix" a name that exists only as a filename
        # ("naruto" -> "auto"). ---
        plan_path = workdir / "abhisek plan.txt"
        plan_path.write_text("Monday: chest and triceps. Tuesday: back and biceps. Rest on Sunday.")
        rasmalai_path = workdir / "rasmalai.txt"
        rasmalai_path.write_text("Boil milk, add lemon juice, press the curds, soak in sweetened saffron milk.")
        for path in (plan_path, rasmalai_path):
            record = build_file_record(path)
            file_record_store.upsert(record)
            indexer.index_file(path, record.file_id, record.hash)

        for query, expected in [
            ("abhisek plan", "abhisek plan.txt"),
            ("open the abhishek plan file", "abhisek plan.txt"),  # spoken form + one-letter slip
            ("rasmalai", "rasmalai.txt"),
            ("bread", "bread.md"),  # single word = the whole query, so one match is enough
        ]:
            name_results = search_service.search(query)
            assert name_results and name_results[0]["filename"] == expected, (
                f"{query!r} should return {expected} first, got {[r['filename'] for r in name_results]}"
            )
            assert name_results[0]["why"][0].startswith("Filename contains:"), name_results[0]["why"]
        pasta = search_service.search("recipe for pasta")
        assert not any(r["why"][0].startswith("Filename contains:") for r in pasta), (
            "A single shared word ('recipe') in a multi-word query must not count as a filename match"
        )
        print("11. Filename matching: a file named in the query ranks first, spoken/typo forms included, and one shared word does not: OK")

        print("\nPhase 5 search pipeline OK: RRF fusion, file aggregation, exact-phrase override, type filtering, explanations, punctuation-safety, and filename matching all work as expected.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
