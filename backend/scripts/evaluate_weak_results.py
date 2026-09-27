"""Improvement 2 (2026-09-26) — how many "possibly related" results to show.

The live Windows run showed 10 weak matches under a server-capacity
question, camping-trip notes among them. This measures, on the labelled
corpus (eval_corpus.py, the same 60 queries as the routing evaluation):

  - how many weak results each query shows today, and how many of them
    are actually relevant (noise = weak and not relevant);
  - for a sweep of rules — hide a weak result whose meaning distance is
    more than GAP behind the best strong result, then show at most CAP —
    how much noise is removed and whether any relevant file is lost from
    what the user sees (a relevant file lost = a regression).

Prints a Markdown table (pasted into the README) and writes
backend/data/eval_weak_results.json.

Run with:  backend/venv/Scripts/python backend/scripts/evaluate_weak_results.py
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_corpus import QUERIES  # noqa: E402
from evaluate_routing import build  # noqa: E402

from app.search.service import WEAK_MAX_GAP_FROM_STRONG, WEAK_MAX_SHOWN  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data" / "eval_weak_results.json"
GAPS = [None, 0.30, 0.20, 0.15, 0.10, 0.05]  # None = no gap rule
CAPS = [None, 5, 3]  # None = no cap


def collect(search, raw: bool) -> list[dict]:
    """Per query: every result with its tier, meaning distance and
    relevance. raw=True switches the new pruning off to see what it hides."""
    saved = search.weak_pruning
    search.weak_pruning = not raw
    rows = []
    try:
        for query, relevant, _tier in QUERIES:
            names = {Path(f).name for f in relevant}
            results, _ = search.search_routed(query, top_k=10, mode="auto")
            rows.append({
                "query": query,
                "results": [
                    {"name": r["filename"], "tier": r["confidence"], "distance": r["semantic_score"], "relevant": r["filename"] in names}
                    for r in results
                ],
            })
    finally:
        search.weak_pruning = saved
    return rows


def apply_rule(results: list[dict], gap: float | None, cap: int | None) -> list[dict]:
    """The rule under test, applied offline to one query's raw results.
    Mirrors SearchService._prune_weak."""
    strong = [r for r in results if r["tier"] == "strong"]
    weak = [r for r in results if r["tier"] == "weak"]
    strong_distances = [r["distance"] for r in strong if r["distance"] is not None]
    if gap is not None and strong:
        best = min(strong_distances) if strong_distances else None
        # A strong literal hit with no meaning distance is "far ahead" of any
        # meaning-only extra; measured against the best strong distance otherwise.
        weak = [r for r in weak if best is not None and r["distance"] is not None and r["distance"] <= best + gap]
    if cap is not None:
        weak = weak[:cap]
    return strong + weak


def score(rows: list[dict], gap, cap) -> dict:
    shown_weak = noise = lost = 0
    for row in rows:
        before = row["results"]
        after = apply_rule(before, gap, cap)
        shown_weak += sum(r["tier"] == "weak" for r in after)
        noise += sum(r["tier"] == "weak" and not r["relevant"] for r in after)
        lost += any(r["relevant"] for r in before) and not any(r["relevant"] for r in after)
    n = len(rows)
    return {"gap": gap, "cap": cap, "weak_per_query": shown_weak / n, "noise_per_query": noise / n, "relevant_lost": lost}


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="intellifile_weak_"))
    try:
        search = build(workdir)
        rows = collect(search, raw=True)
        shipped = collect(search, raw=False)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    n = len(rows)
    weak_relevant = sum(r["tier"] == "weak" and r["relevant"] for row in rows for r in row["results"])
    print(f"{n} labelled queries; relevant files found only in the weak tier today: {weak_relevant}\n")
    print("| Hide weak if > GAP behind best strong | Show at most | Weak shown / query | Noise / query | Relevant files lost |")
    print("|---|---|---|---|---|")
    sweep = []
    for gap in GAPS:
        for cap in CAPS:
            s = score(rows, gap, cap)
            sweep.append(s)
            print(f"| {'off' if gap is None else gap} | {'all' if cap is None else cap} | {s['weak_per_query']:.2f} | {s['noise_per_query']:.2f} | {s['relevant_lost']} |")

    live = score(shipped, None, None)
    print(f"\nShipped rule (gap {WEAK_MAX_GAP_FROM_STRONG}, at most {WEAK_MAX_SHOWN}) through the real search path: "
          f"{live['weak_per_query']:.2f} weak shown / query, {live['noise_per_query']:.2f} noise / query, "
          f"{live['relevant_lost']} relevant lost")
    worst = sorted(rows, key=lambda row: -sum(r["tier"] == "weak" for r in row["results"]))[:5]
    print("\nNoisiest queries before:")
    for row in worst:
        print(f"  {row['query']!r}: " + ", ".join(f"{r['name']} ({r['tier']}, {r['distance']})" for r in row["results"][:6]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"sweep": sweep, "shipped": live, "queries": rows}, indent=2))
    ok = live["relevant_lost"] == 0
    print(f"\n{'OK' if ok else 'FAIL'}: shipped rule loses {live['relevant_lost']} relevant files")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
