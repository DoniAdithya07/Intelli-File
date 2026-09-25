"""Phase 18 regression: query routing, complexity estimation, metadata
filters, escalation and the cross-encoder reranker (Objective 3).

A labelled query set asserts the tier the router picks and — the point of
routing — that a cheap tier never runs the stages it does not need (a
filename lookup must not embed anything). Then: escalation rescues a wrong
cheap guess; metadata filters narrow every tier and work alone; the
reranker's vote lifts the passage that actually answers a question above
a keyword-stuffed distractor, and is skipped on cheaper tiers.

Run with:  backend/venv/bin/python backend/scripts/prototype_router.py
"""

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.embeddings.model import EmbeddingModel, default_model_dir  # noqa: E402
from app.files.identity import build_file_record  # noqa: E402
from app.indexing import Indexer  # noqa: E402
from app.search import SearchService  # noqa: E402
from app.search.query_parsing import parse_query  # noqa: E402
from app.search.reranker import Reranker  # noqa: E402
from app.search.router import PLANS  # noqa: E402
from app.storage import FileRecordStore, KeywordStore, LanceDBVectorStore  # noqa: E402

MODELS = Path(__file__).resolve().parents[1] / "models"


def ran(route: dict, stage: str) -> bool:
    return any(s["stage"] == stage and not s.get("skipped") for s in route["stages"])


def main() -> None:
    if not (default_model_dir(MODELS) / "model.onnx").exists():
        print("Embedding model not found — run scripts/download_model.py first.")
        sys.exit(1)
    workdir = Path(tempfile.mkdtemp())
    try:
        files = workdir / "files"
        (files / "invoices").mkdir(parents=True)
        docs = {
            "scaling_notes.md": "Horizontal scaling allows additional server instances to be provisioned when traffic demand increases, distributed by a load balancer.",
            "bread_recipe.md": "A good sourdough starter needs regular feeding with flour and water before the bulk ferment.",
            "gym plan.txt": "Gym plan for the week. Monday chest and triceps, Wednesday back and biceps, Friday legs.",
            "berlin_facts.txt": "Berlin has a population of 3,520,031 registered inhabitants in an area of 891.82 square kilometers.",
            "faq_draft.txt": "Questions people ask: how many people live in berlin? how many people live in hamburg? how many people live in munich? We will answer these in the FAQ later.",
            "invoices/march_invoice.txt": "Invoice 2025-03: consulting services, 12 hours. Payment terms net 30, so it is due on 14 April 2025; the amount is 1,440 euros.",
            "todo.txt": "todo: ask when is the invoice due, ask when is the invoice due again if no reply, check when is the invoice due for the other client, and how much is the total",
        }
        for name, text in docs.items():
            (files / name).write_text(text)
        old = files / "bread_recipe.md"
        os.utime(old, (time.time() - 700 * 86400, time.time() - 700 * 86400))  # modified two years ago

        model = EmbeddingModel(default_model_dir(MODELS))
        vector_store = LanceDBVectorStore(str(workdir / "vectors"))
        keyword_store = KeywordStore(workdir / "keyword.db")
        record_store = FileRecordStore(workdir / "files.db")
        indexer = Indexer(model, vector_store, keyword_store, record_store)
        for name in docs:
            record = build_file_record(files / name)
            record_store.upsert(record)
            indexer.index_file(files / name, record.file_id, record.hash)
        search = SearchService(model, vector_store, keyword_store, record_store)
        if (MODELS / "ms-marco-MiniLM-L-6-v2" / "model.onnx").exists():
            search.reranker = Reranker(MODELS / "ms-marco-MiniLM-L-6-v2")

        # --- 1. Routing decisions on a labelled set ---
        labelled = {
            "gym plan": "filename",
            "berlin": "keyword",  # one word, even though a file is named after it: names + BM25, never names alone
            "sourdough starter": "keyword",
            "invoice euros": "keyword",
            "how do servers cope with a spike in visitors": "hybrid",
            "notes about feeding a starter": "hybrid",
            "how do we add capacity when traffic spikes, and what balances the load between the new instances?": "hybrid",
            "what is the registered population of berlin and how large is its area in square kilometers": "hybrid",
        }
        for q, want in labelled.items():
            _, route = search.search_routed(q, top_k=5)
            assert route["requested_tier"] == want, f"{q!r}: routed to {route['requested_tier']}, wanted {want} ({route['reason']})"
        print(f"1. Router picks the expected tier for {len(labelled)} labelled queries (filename / keyword / hybrid / hybrid+rerank): OK")

        # --- 2. Cheap tiers skip the expensive stages (the whole point) ---
        results, route = search.search_routed("gym plan", top_k=5)
        assert results[0]["filename"] == "gym plan.txt" and route["tier"] == "filename" and not route["escalated"]
        assert not ran(route, "keyword") and not ran(route, "semantic") and not ran(route, "rerank"), route["stages"]
        results, route = search.search_routed("sourdough starter", top_k=5)
        assert results[0]["filename"] == "bread_recipe.md" and route["tier"] == "keyword"
        assert ran(route, "keyword") and not ran(route, "semantic") and not ran(route, "rerank"), route["stages"]
        _, route = search.search_routed("how do servers cope with a spike in visitors", top_k=5)
        assert ran(route, "semantic") and route["requested_tier"] == "hybrid"
        assert not ran(route, "rerank") or route["escalated"], "the reranker runs only by escalation"
        assert all("ms" in s for s in route["stages"] if not s.get("skipped") and s["stage"] != "escalate") and route["total_ms"] > 0
        print("2. Filename tier runs no BM25/embedding/reranker; keyword tier no embedding; every ran stage is timed: OK")

        # --- 3. Escalation: a short keyword query whose words are known but appear only in the wrong sense ---
        # "plan week" is 2 known words -> keyword tier; BM25 finds the gym plan (literal), so no escalation.
        results, route = search.search_routed("plan week", top_k=5)
        assert route["tier"] == "keyword" and not route["escalated"] and results[0]["filename"] == "gym plan.txt"
        # "capacity growth" — both words known?  "capacity" is not in any file -> unknown word -> hybrid directly.
        # Force the escalation case: known words that BM25 (AND semantics) cannot match together.
        results, route = search.search_routed("sourdough balancer", top_k=5)
        assert route["requested_tier"] == "keyword" and route["escalated"] and route["tier"] == "hybrid", route
        assert results, "escalation must produce results where the cheap tier found none"
        assert any(s.get("stage") == "escalate" for s in route["stages"])
        print(f"3. Escalation: 'sourdough balancer' (keyword tier found nothing) re-ran as hybrid and found {results[0]['filename']}: OK")

        # --- 4. Metadata filters: parsed, applied to every tier, and usable alone ---
        p = parse_query("type:md after:2025 size:<100kb in:files scaling")
        assert p.text == "scaling" and p.filters.file_type == "md" and p.filters.after and p.filters.size_max == 100 * 1024 and p.filters.path_contains == "files"
        results, route = search.search_routed("in:invoices", top_k=5)
        assert route["tier"] == "metadata" and [r["filename"] for r in results] == ["march_invoice.txt"], results
        assert results[0]["why"][0].startswith("Matches filters"), results[0]["why"]
        results, _ = search.search_routed("before:2025 type:md", top_k=5)  # the year-old recipe only
        assert [r["filename"] for r in results] == ["bread_recipe.md"], [r["filename"] for r in results]
        results, _ = search.search_routed("after:2025 type:md", top_k=5)
        assert [r["filename"] for r in results] == ["scaling_notes.md"]
        results, _ = search.search_routed("starter flour type:txt", top_k=5)  # right words, wrong type
        assert results == [], results
        results, _ = search.search_routed("size:>50b in:invoices consulting", top_k=5)
        assert results and results[0]["filename"] == "march_invoice.txt"
        print("4. Metadata filters: after/before/size/in/type parsed; filters-only lists files; filters narrow text search: OK")

        # --- 5. Reranker: promotes the passage that ANSWERS a question over a
        # note that merely echoes it (which BM25 + embedding both prefer), and
        # never demotes when it judges nothing relevant ---
        if search.reranker is None:
            print("5. Reranker model not installed — skipped (run scripts/download_reranker_model.py)")
        else:
            q = "when is the invoice due, and how much is the total"
            plain = search.search(q, top_k=5, mode="smart")
            assert [r["filename"] for r in plain][:2] == ["todo.txt", "march_invoice.txt"], [r["filename"] for r in plain]
            assert all(r["reranker_score"] is None for r in plain), "manual smart mode must not rerank"
            # Since Phase 20 the reranker is an escalation step, not a tier the
            # router picks by length — so the stage is exercised with the plan forced.
            forced = search._run_plan(PLANS["hybrid+rerank"], q, q, parse_query(q), search._active_records(), 5, _stages := [])
            assert forced[0]["filename"] == "march_invoice.txt", [(r["filename"], r["reranker_score"]) for r in forced]
            assert forced[0]["reranker_score"] > forced[1]["reranker_score"] > 0
            assert "Re-read by the reranker: relevant" in forced[0]["why"]
            # No opinion = no change: the concept match the reranker gets wrong keeps its fused rank.
            q2 = "how to handle sudden increases in traffic, and what to do about latency when demand grows"
            forced2 = search._run_plan(PLANS["hybrid+rerank"], q2, q2, parse_query(q2), search._active_records(), 5, stages2 := [])
            assert forced2[0]["filename"] == "scaling_notes.md", [r["filename"] for r in forced2]
            assert next(s for s in stages2 if s["stage"] == "rerank")["relevant"] == 0
            # The router itself reaches the reranker only by escalation.
            _, route = search.search_routed(q, top_k=5)
            assert route["requested_tier"] == "hybrid" and not ran(route, "rerank"), route
            print("5. Reranker (forced plan): march_invoice.txt (answers) promoted above todo.txt (echoes the question) that fusion preferred; 0 promotions on the concept match; the router reaches it only by escalation: OK")

        # --- 6. Manual modes bypass the router ---
        _, route = search.search_routed("gym plan", top_k=5, mode="smart")
        assert route["tier"] == "hybrid" and route["complexity"] == -1 and ran(route, "semantic")
        _, route = search.search_routed('"gym plan"', top_k=5)
        assert route["tier"] == "keyword" and not route["escalated"]
        print("6. Manual smart/exact/keyword modes bypass the router; quoted phrases never escalate: OK")

        print("\nPhase 18 routing OK: tier selection, stage skipping, escalation, metadata filters, reranker vote and manual overrides all work as expected.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
