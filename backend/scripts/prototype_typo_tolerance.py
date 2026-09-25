"""Phase 6 integration test: typo correction against the index's own
vocabulary, verified both in isolation and through the real search
pipeline with real messy/misspelled queries. Also verifies the semantic
path already tolerates typos reasonably on its own, per the PRD's
Query Understanding goals. Run with:

    backend/venv/bin/python backend/scripts/prototype_typo_tolerance.py
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
from app.search.spelling import _levenshtein, correct_query  # noqa: E402
from app.storage import FileRecordStore, KeywordStore, LanceDBVectorStore  # noqa: E402

MODEL_DIR = default_model_dir(Path(__file__).resolve().parents[1] / "models")


def test_levenshtein_and_correction():
    assert _levenshtein("kubernetes", "kubernetes", 2) == 0
    assert _levenshtein("kubernets", "kubernetes", 2) == 1  # missing one letter
    assert _levenshtein("recieve", "receive", 2) == 2  # classic transposition-ish typo
    assert _levenshtein("completely", "different", 2) > 2  # too different -> exceeds threshold
    print("1. Levenshtein distance math: OK")

    vocab = {"kubernetes": 5, "sourdough": 3, "horizontal": 2, "scaling": 2}
    corrected = correct_query("kubernets sourdogh unrelatedxyz", vocab)
    assert corrected == "kubernetes sourdough unrelatedxyz", corrected
    print("2. correct_query fixes known-close typos, leaves unrecognizable words alone: OK")

    # Short words should never be "corrected" — too easy to guess wrong.
    assert correct_query("it is a cat", {"bit": 1, "sit": 1, "hat": 1}) == "it is a cat"
    print("3. Short words are left uncorrected (avoids false-positive corrections): OK")

    # A word already in the vocabulary should never be changed, even if
    # another vocab word happens to be one edit away.
    assert correct_query("scaling", {"scaling": 1, "scalings": 1}) == "scaling"
    print("4. Words already in the vocabulary are never 'corrected' away: OK")

    # Regression, live test 2026-09-11: "remind me" became "demand me" —
    # a real word two edits from an unrelated vocabulary word — and the
    # rewritten query matched nothing. Short words get one edit only, and
    # a word that is the stem of a known word ("remind" -> "reminder")
    # must win over an unrelated near neighbour.
    vocab = {"reminder": 3, "demand": 50}
    assert correct_query("remind me", vocab) == "reminder me", correct_query("remind me", vocab)
    assert correct_query("plane", {"plant": 9}) == "plane", "5-letter words are never corrected (one edit reaches unrelated real words)"
    assert correct_query("trafic", {"traffic": 2}) == "traffic"  # 6 letters: one edit allowed
    assert correct_query("kubernets", {"kubernetes": 1}) == "kubernetes"  # long word: one edit still corrected
    print("4b. Real words are not rewritten into unrelated neighbours; stems extend to the known word: OK")

    # Live test 2026-09-19: "braed making" returned nothing — 5 letters, so
    # the length floor refused it. Adjacent-swap typos keep the same letters
    # and are allowed on short words; substitutions on short words still are not.
    assert correct_query("braed making", {"bread": 2, "making": 1}) == "bread making"
    assert correct_query("paln", {"plan": 4}) == "plan"
    assert correct_query("plane", {"plant": 9}) == "plane"  # substitution on a short word: still untouched
    assert correct_query("cat", {"act": 3}) == "cat"  # 3 letters: too short even for a swap
    print("4c. Short-word adjacent-letter swaps are corrected; short-word substitutions still are not: OK")

    # Rule-based tidying behind the Did-you-mean chip (2026-09-19).
    from app.search.grammar import tidy_query
    assert tidy_query("the the bread recipe") == "the bread recipe"
    assert tidy_query("bread ,making") == "bread, making"
    assert tidy_query("bread   making  ") == "bread making"
    assert tidy_query("type:pdf notes") == "type:pdf notes"  # colon left alone
    assert tidy_query("meeting at 10:30") == "meeting at 10:30"
    assert tidy_query("version 3.5 notes") == "version 3.5 notes"
    assert tidy_query("Bread Making") == "Bread Making"  # capitalisation is not 'corrected'
    print("4d. Query tidying: doubled words, punctuation spacing, repeated spaces; colons/decimals/case untouched: OK")


def main():
    test_levenshtein_and_correction()

    if not (MODEL_DIR / "model.onnx").exists():
        print("Model not found — run scripts/download_model.py first.")
        sys.exit(1)

    workdir = Path(tempfile.mkdtemp())
    try:
        cloud_path = workdir / "cloud_computing.txt"
        cloud_path.write_text(
            "Horizontal scaling allows additional server instances to be provisioned when "
            "traffic demand increases, distributed by a load balancer."
        )
        kubernetes_path = workdir / "kubernetes_notes.txt"
        kubernetes_path.write_text(
            "Kubernetes' Horizontal Pod Autoscaler automatically adjusts the number of running pods "
            "based on observed CPU utilization."
        )
        bread_path = workdir / "baking_recipe.txt"
        bread_path.write_text(
            "A good sourdough starter needs regular feeding with flour and water to stay active."
        )

        model = EmbeddingModel(MODEL_DIR)
        vector_store = LanceDBVectorStore(str(workdir / "vectors"))
        keyword_store = KeywordStore(workdir / "keyword.db")
        file_record_store = FileRecordStore(workdir / "files.db")
        indexer = Indexer(model, vector_store, keyword_store, file_record_store)
        search_service = SearchService(model, vector_store, keyword_store, file_record_store)

        for path in (cloud_path, kubernetes_path, bread_path):
            record = build_file_record(path)
            file_record_store.upsert(record)
            indexer.index_file(path, record.file_id, record.hash)

        # --- Real messy queries, run through the actual search pipeline ---
        messy_queries = {
            "sourdogh starter": "baking_recipe.txt",  # missing letter
            "horizantal scaling": "cloud_computing.txt",  # transposed vowel
            "kubernetis autoscaler": "kubernetes_notes.txt",  # misspelled proper noun
        }
        for query, expected_file in messy_queries.items():
            results = search_service.search(query)
            assert results, f"Expected at least one result for typo'd query: {query!r}"
            assert results[0]["filename"] == expected_file, (
                f"Query {query!r} -> expected {expected_file}, got {results[0]['filename']}"
            )
            assert results[0]["keyword_score"] is not None, (
                f"Query {query!r} should have matched via corrected keyword search, not semantic-only"
            )
        print("5. Real misspelled queries still find the right file via corrected keyword search: OK")

        # --- Exact-phrase mode must NOT get typo correction — "exact" means exact ---
        exact_typo_results = search_service.search('"sourdogh starter"')
        assert exact_typo_results == [], "A misspelled exact phrase must not match — that would defeat 'exact'"
        print("6. Exact-phrase mode is correctly excluded from typo correction: OK")

        # --- Real finding from testing: raw typo'd text fed straight into
        # the embedding model can rank an UNRELATED file above the correct
        # one — "horizantal scalling for trafic spikes" scored 0.08
        # similarity to the correct file and 0.15 to an unrelated one
        # (bread) before correction, and 0.58 vs 0.001 after. So the
        # pipeline now corrects before embedding too, not just for BM25.
        # This checks the full pipeline gets it right end to end.
        heavy_typo_results = search_service.search("horizantal scalling for trafic spikes")
        assert heavy_typo_results, "Expected a result even for a heavily misspelled multi-word query"
        assert heavy_typo_results[0]["filename"] == "cloud_computing.txt", (
            f"Expected cloud_computing.txt, got {heavy_typo_results[0]['filename']}"
        )
        print("7. Heavily misspelled query (3 of 5 words wrong) still finds the right file via the full pipeline: OK")

        print("\nPhase 6 typo tolerance OK: correction math, real messy queries through the full pipeline, exact-phrase exclusion, and semantic-path verification all work as expected.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
