"""Learned router vs rules on the Phase 20 corpus (improvement 6).

Every labelled query is replayed through the tiers against the corpus
index to find the cheapest tier that yields a confident hit (the training
label). Five-fold cross-validation: train on 48, judge on 12, rotate.
Reported per router: how often the chosen tier is the cheapest sufficient
one ("tier accuracy"), how often it is too cheap (needs escalation) or
too expensive (wasted embedding), and the resulting hit@1 / mean latency
when the chosen tier is actually run with escalation.

Run with:  backend/venv/bin/python backend/scripts/evaluate_learned_router.py
"""

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_corpus import DOCS, QUERIES  # noqa: E402
from evaluate_routing import build, relevant_rank  # noqa: E402

from app.search.learned_router import CONFIDENCE, LearnedRouter, label_query  # noqa: E402
from app.search.query_parsing import parse_query  # noqa: E402
from app.search.router import route as rule_route  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data" / "eval_learned_router.json"
RANK = {"filename": 0, "keyword": 1, "hybrid": 2, "hybrid+rerank": 3, "none": 2}


def main() -> None:
    workdir = Path(tempfile.mkdtemp())
    try:
        search = build(workdir)
        search.search("warm up")
        examples = []
        for q, relevant, _ in QUERIES:
            parsed = parse_query(q)
            if not parsed.text or parsed.exact_phrase is not None:
                continue
            active = search._active_records()
            decision = rule_route(parsed.text, parsed.filters.any(), False, active, search._correction_vocabulary(active))
            examples.append((q, relevant, decision, parsed.filters.any(), label_query(search, q)))
        n = len(examples)
        folds = [examples[i::5] for i in range(5)]
        stats = {"rules": {"right": 0, "too_cheap": 0, "too_expensive": 0}, "learned": {"right": 0, "too_cheap": 0, "too_expensive": 0}}
        runs = {"rules": [], "learned": []}
        for k in range(5):
            test = folds[k]
            train = [e for i, f in enumerate(folds) if i != k for e in f]
            model = LearnedRouter(workdir / f"model_{k}.json")
            model.train([(d, hf, label) for _, _, d, hf, label in train])
            for q, relevant, d, hf, label in test:
                want = min(RANK[label], 2)
                picks = {"rules": d.tier, "learned": model.decide(parse_query(q).text, hf, False, search._active_records(), search._correction_vocabulary(search._active_records())).tier}
                for who, tier in picks.items():
                    got = RANK[tier]
                    stats[who]["right" if got == want else "too_cheap" if got < want else "too_expensive"] += 1
                # run each choice for real (with escalation), for hit@1 and latency
                for who in ("rules", "learned"):
                    search.learned_router = model if who == "learned" else None
                    t0 = time.perf_counter()
                    results, route = search.search_routed(q, top_k=10, mode="auto")
                    ms = (time.perf_counter() - t0) * 1000
                    rank = relevant_rank(results, relevant)
                    runs[who].append({"hit1": rank == 1, "ms": ms, "escalated": route["escalated"], "embedded": any(s["stage"] == "semantic" and not s.get("skipped") for s in route["stages"])})
        search.learned_router = None
        print(f"\n## Learned router vs rules — {n} queries, 5-fold cross-validation (labels = cheapest tier with a confident hit, by replay)\n")
        print("| Router | picked the cheapest sufficient tier | too cheap (escalated) | too expensive | hit@1 | mean ms | embedded |")
        print("|---|---|---|---|---|---|---|")
        report = {}
        for who in ("rules", "learned"):
            st, rs = stats[who], runs[who]
            row = {"right": st["right"] / n, "too_cheap": st["too_cheap"] / n, "too_expensive": st["too_expensive"] / n, "hit1": sum(r["hit1"] for r in rs) / len(rs), "mean_ms": sum(r["ms"] for r in rs) / len(rs), "embedded": sum(r["embedded"] for r in rs) / len(rs)}
            report[who] = row
            print(f"| {who} | {100*row['right']:.0f}% | {100*row['too_cheap']:.0f}% | {100*row['too_expensive']:.0f}% | {100*row['hit1']:.0f}% | {row['mean_ms']:.1f} | {100*row['embedded']:.0f}% |")
        print(f"\n(confidence threshold {CONFIDENCE}; a learned model ships only when it beats the rules on the user's own held-out queries — the app checks this at training time.)")
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, indent=2))
        print(f"\nWritten to {OUT}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)  # success path only: skip native teardown — onnxruntime/LanceDB worker threads once raced the C++ static destructors at exit ("recursive_mutex lock failed", run_all_phases 2026-09-21) after every result was written
