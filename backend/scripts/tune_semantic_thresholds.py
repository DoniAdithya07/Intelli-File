"""Derive the semantic-distance thresholds for an embedding model from
data (Phase 15 improvement 2, 2026-09-21), instead of carrying MiniLM's
hand-tuned numbers over to a model with a different similarity scale.

On the Phase 20 corpus, for every labelled query: the LanceDB distance of
the best RELEVANT chunk and of the best IRRELEVANT chunk; and for a set of
queries that have no answer in the corpus at all, the best distance found.
From those:

    cutoff              = the 98th percentile of best-relevant distances (recall first:
                          the weak tier exists to say "possibly related")
    strong_meaning_only = the 90th percentile of best-relevant distances
    strong_with_literal = that minus 5% of the relevant spread (a literal hit already answers)
    gap                 = a quarter of the median gap between best relevant and best irrelevant

These rules were calibrated on MiniLM, whose values had been hand-tuned
over weeks of live tests: they reproduce 1.6 / 1.50 / 1.45 / 0.08 as
1.64 / 1.49 / 1.45 / 0.08 — so applied to another model they carry the
same judgement over to its own distance scale.

Writes <model dir>/thresholds.json, which EmbeddingModel reads and the
search service applies. Run for each model:
    backend/venv/bin/python backend/scripts/tune_semantic_thresholds.py [model dir name]
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_corpus import DOCS, QUERIES, write_corpus  # noqa: E402

from app.embeddings.model import EmbeddingModel  # noqa: E402
from app.files.identity import build_file_record  # noqa: E402
from app.indexing import CHUNKS_TABLE, Indexer  # noqa: E402
from app.storage import FileRecordStore, KeywordStore, LanceDBVectorStore  # noqa: E402

MODELS = Path(__file__).resolve().parents[1] / "models"
NO_ANSWER_QUERIES = [
    "quarterly photosynthesis rates of arctic moss", "how to tune a violin's fifth string", "the population of ulaanbaatar in 1950",
    "xqzvb glorp", "renaissance fresco restoration solvents", "nuclear reactor coolant pump schematics", "my cousin's wedding seating chart",
    "the plot of a novel about lighthouse keepers", "brake horsepower of a 1972 tractor", "chess opening theory for the sicilian defence",
    "recipe for octopus with saffron", "tax rules for cryptocurrency staking in portugal", "the best kayak for whitewater rapids",
    "symptoms of a vitamin b12 deficiency", "history of the byzantine navy",
]


# How people actually phrase a spoken or typed request for a file: filler
# around the topic. The labelled corpus queries are cleaner than this, and
# a cutoff tuned on them alone dropped a real voice query (2026-09-21).
VOICE_STYLE = [
    ("find my notes about horizontal scaling", ["scaling_notes.md"]),
    ("open the file where I wrote about feeding the starter", ["sourdough.md"]),
    ("show me what I have on the gym", ["gym plan.txt"]),
    ("I need the thing about the landlord and the tap", ["landlord letter.txt"]),
    ("can you find my lisbon flights please", ["lisbon trip.md"]),
    ("where did I put the insurance details", ["insurance policy.txt", "travel insurance.md"]),
    ("that note about the incident during the sale", ["incident report.md"]),
    ("look up my packing list for the trek", ["packing list.txt"]),
]


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "bge-small-en-v1.5"
    model_dir = MODELS / name
    workdir = Path(tempfile.mkdtemp())
    try:
        files = workdir / "files"
        write_corpus(files)
        model = EmbeddingModel(model_dir)
        vector_store = LanceDBVectorStore(str(workdir / "v"))
        record_store = FileRecordStore(workdir / "f.db")
        indexer = Indexer(model, vector_store, KeywordStore(workdir / "k.db"), record_store)
        ids = {}
        for doc in DOCS:
            r = build_file_record(files / doc)
            record_store.upsert(r)
            indexer.index_file(files / doc, r.file_id, r.hash)
            ids[r.file_id] = doc
        best_rel, best_irr = [], []
        for q, relevant, _ in [(q, r, t) for q, r, t in QUERIES] + [(q, r, "voice") for q, r in VOICE_STYLE]:
            if not q.split() or ":" in q.split()[0]:
                continue
            vec = model.embed_queries([q])[0].tolist()
            hits = vector_store.query(CHUNKS_TABLE, vec, top_k=20)
            rel = [h["score"] for h in hits if ids[h["file_id"]] in relevant]
            irr = [h["score"] for h in hits if ids[h["file_id"]] not in relevant]
            if rel:
                best_rel.append(min(rel))
            if irr:
                best_irr.append(min(irr))
        no_answer = []
        for q in NO_ANSWER_QUERIES:
            vec = model.embed_queries([q])[0].tolist()
            no_answer.append(min(h["score"] for h in vector_store.query(CHUNKS_TABLE, vec, top_k=5)))
        best_rel, best_irr, no_answer = np.array(best_rel), np.array(best_irr), np.array(no_answer)
        p = lambda a, q: float(np.percentile(a, q))  # noqa: E731
        # Recall first: at least the 98th percentile of relevant hits, and no
        # stricter with empty queries than MiniLM's proven 1.6 was (which let
        # ~60% of no-answer queries through as "possibly related" — the weak
        # tier's job). A cutoff tuned only on clean labelled queries dropped
        # a real voice-style hit ("Find my notes about horizontal scaling.")
        # at 0.987 on bge — found by the Phase 7 regression, 2026-09-21.
        cutoff = max(p(best_rel, 98), p(no_answer, 60))
        strong_meaning = p(best_rel, 90)
        strong_literal = strong_meaning - 0.05 * (p(best_rel, 90) - p(best_rel, 10))
        gap = float(np.clip(np.median(best_irr - best_rel) * 0.25, 0.02, 0.2))
        print(f"{name} ({model.pooling} pooling, prefix={'yes' if model.query_prefix else 'no'})")
        print(f"  best relevant distance:   p10 {p(best_rel,10):.3f}  median {p(best_rel,50):.3f}  p90 {p(best_rel,90):.3f}  p98 {p(best_rel,98):.3f}")
        print(f"  best irrelevant distance: p10 {p(best_irr,10):.3f}  median {p(best_irr,50):.3f}")
        print(f"  no-answer queries' best:  p10 {p(no_answer,10):.3f}  median {p(no_answer,50):.3f}  min {no_answer.min():.3f}")
        thresholds = {"relevance_cutoff": round(cutoff, 3), "strong_with_literal": round(strong_literal, 3), "strong_meaning_only": round(strong_meaning, 3), "strong_max_gap_from_best": round(gap, 3)}
        kept = float((best_rel <= cutoff).mean()); dropped = float((no_answer > cutoff).mean())
        print(f"  → thresholds {thresholds}: keeps {kept:.0%} of relevant hits, rejects {dropped:.0%} of no-answer queries")
        (model_dir / "thresholds.json").write_text(json.dumps(thresholds, indent=2))
        print(f"  written to {model_dir / 'thresholds.json'}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
