"""Phase 20 — personalization evaluation (Objective 2: does the profile
help, and does it ever hurt?).

Setup: the Phase 20 corpus plus a near-identical "old copy" twin of eight
documents, so eight queries become genuinely ambiguous on content alone.
A synthetic four-week activity log opens the ORIGINALS (some daily, some
on Monday mornings) and never the twins. Then every labelled query runs
with personalization off and on:

- ambiguous queries: does the habitual original outrank its twin?
- all other queries: unchanged ranking (personalization must never move
  a result across tiers or displace a better match).

Prints a Markdown table (pasted into the README) and writes
backend/data/eval_personalization.json.

Run with:  backend/venv/bin/python backend/scripts/evaluate_personalization.py
"""

import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_corpus import DOCS, QUERIES, write_corpus  # noqa: E402

from app.context import ProfileBuilder, UsageStore  # noqa: E402
from app.embeddings.model import EmbeddingModel, default_model_dir  # noqa: E402
from app.files.identity import build_file_record  # noqa: E402
from app.indexing import Indexer  # noqa: E402
from app.search import SearchService  # noqa: E402
from app.storage import FileRecordStore, KeywordStore, LanceDBVectorStore  # noqa: E402
import app.search.service as _service  # noqa: E402

SHIPPED_WEIGHT = _service.PERSONAL_NEAR_TIE

MODELS = Path(__file__).resolve().parents[1] / "models"
OUT = Path(__file__).resolve().parents[1] / "data" / "eval_personalization.json"

# original -> its twin (an "old copy" with one harmless extra line)
TWINS = {
    "scaling_notes.md": "scaling_notes_old.md",
    "sourdough.md": "sourdough_old.md",
    "gym plan.txt": "gym plan (old).txt",
    "lisbon trip.md": "lisbon trip draft.md",
    "insurance policy.txt": "insurance policy 2024.txt",
    "deploy checklist.md": "deploy checklist old.md",
    "incident report.md": "incident report draft.md",
    "savings plan.txt": "savings plan old.txt",
}
DAILY = ["scaling_notes.md", "sourdough.md", "gym plan.txt", "deploy checklist.md"]
MONDAY_MORNING = ["lisbon trip.md", "insurance policy.txt", "incident report.md", "savings plan.txt"]


def build(workdir: Path):
    files = workdir / "files"
    write_corpus(files)
    for original, twin in TWINS.items():
        (files / twin).write_text(DOCS[original] + "\n(older copy kept for reference)")
    model = EmbeddingModel(default_model_dir(MODELS))
    vector_store = LanceDBVectorStore(str(workdir / "vectors"))
    keyword_store = KeywordStore(workdir / "keyword.db")
    record_store = FileRecordStore(workdir / "files.db")
    indexer = Indexer(model, vector_store, keyword_store, record_store)
    ids = {}
    for path in sorted(files.rglob("*")):
        if path.is_file():
            record = build_file_record(path)
            record_store.upsert(record)
            indexer.index_file(path, record.file_id, record.hash)
            ids[str(path.relative_to(files))] = record.file_id
    usage = UsageStore(workdir / "usage.db")
    builder = ProfileBuilder(usage, record_store, vector_store, keyword_store)
    search = SearchService(model, vector_store, keyword_store, record_store)
    search.profile_builder = builder
    return search, usage, builder, ids, files


def synthetic_month(usage: UsageStore, ids: dict, files: Path) -> None:
    today = datetime.fromtimestamp(time.time())
    def at(days_ago: int, hour: int) -> float:
        return (today.replace(hour=hour, minute=10, second=0, microsecond=0) - timedelta(days=days_ago)).timestamp()
    for days_ago in range(28, 0, -1):
        day = today - timedelta(days=days_ago)
        for name in DAILY:
            usage.record("file_opened", file_id=ids[name], path=str(files / name), ts=at(days_ago, 14))
        if day.weekday() == 0:
            for name in MONDAY_MORNING:
                usage.record("file_opened", file_id=ids[name], path=str(files / name), ts=at(days_ago, 9))
        # a little noise: files opened once or twice, no twin involved
        if days_ago % 6 == 0:
            usage.record("file_opened", file_id=ids["coffee notes.txt"], path=str(files / "coffee notes.txt"), ts=at(days_ago, 8))


def rank_of(results: list[dict], filename: str) -> int | None:
    for i, r in enumerate(results, start=1):
        if r["filename"] == filename:
            return i
    return None


def main() -> None:
    workdir = Path(tempfile.mkdtemp())
    try:
        search, usage, builder, ids, files = build(workdir)
        synthetic_month(usage, ids, files)
        profile = builder.get(force=True)
        assert not profile.cold_start

        ambiguous = [(q, rel[0]) for q, rel, _ in QUERIES if len(rel) == 1 and rel[0] in TWINS]
        others = [(q, rel) for q, rel, _ in QUERIES if not (len(rel) == 1 and rel[0] in TWINS)]

        def run(personalize: bool):
            search.personalize_enabled = lambda: personalize
            amb, oth = [], []
            for q, original in ambiguous:
                results = search.search(q, top_k=10)
                r_orig, r_twin = rank_of(results, Path(original).name), rank_of(results, Path(TWINS[original]).name)
                amb.append({"query": q, "original_rank": r_orig, "twin_rank": r_twin, "original_first": r_orig == 1,
                            "original_above_twin": r_orig is not None and (r_twin is None or r_orig < r_twin)})
            for q, rel in others:
                results = search.search(q, top_k=10)
                names = {Path(f).name for f in rel}
                first = next((i for i, r in enumerate(results, start=1) if r["filename"] in names), None)
                oth.append({"query": q, "rank": first, "top5": [r["filename"] for r in results[:5]], "tiers": [r["confidence"] for r in results[:5]]})
            return amb, oth

        import app.search.service as service_module

        amb_off, oth_off = run(False)
        # Sweep the boost weight: the lift on ambiguous queries against the
        # harm on the rest, so the shipped value is measured, not guessed.
        print("\n## Near-tie window sweep (PERSONAL_NEAR_TIE): lift on ambiguous vs harm on others\n")
        print("| window | original first | MRR of original | others worse | others MRR |")
        print("|---|---|---|---|---|")
        sweep = {}
        for weight in (0.003, 0.0015, 0.001, 0.0006, 0.0003):
            service_module.PERSONAL_NEAR_TIE = weight
            amb_w, oth_w = run(True)
            worse = sum(1 for a, b in zip(oth_off, oth_w) if (b["rank"] or 99) > (a["rank"] or 99))
            sweep[weight] = {"original_first": sum(r["original_first"] for r in amb_w) / len(amb_w), "mrr": sum(1 / r["original_rank"] if r["original_rank"] else 0 for r in amb_w) / len(amb_w), "others_worse": worse, "others_mrr": sum(1 / r["rank"] if r["rank"] else 0 for r in oth_w) / len(oth_w)}
            print(f"| {weight} | {100 * sweep[weight]['original_first']:.0f}% | {sweep[weight]['mrr']:.3f} | {worse}/{len(oth_w)} | {sweep[weight]['others_mrr']:.3f} |")
        service_module.PERSONAL_NEAR_TIE = SHIPPED_WEIGHT
        amb_on, oth_on = run(True)

        def mrr(rows, key):
            return sum(1 / r[key] if r[key] else 0 for r in rows) / len(rows)

        print(f"\n## Personalization evaluation — {len(ambiguous)} ambiguous queries (original vs. near-identical old copy) + {len(others)} others; synthetic 4-week log opens the originals only\n")
        print("| Personalization | original first | original above twin | MRR of original | others: MRR | others: same top-5 set & tiers |")
        print("|---|---|---|---|---|---|")
        same_sets = sum(1 for a, b in zip(oth_off, oth_on) if set(a["top5"]) == set(b["top5"]) and a["tiers"] == b["tiers"])
        rows_out = {}
        for label, amb, oth in (("off", amb_off, oth_off), ("on", amb_on, oth_on)):
            first = sum(r["original_first"] for r in amb) / len(amb)
            above = sum(r["original_above_twin"] for r in amb) / len(amb)
            m = mrr(amb, "original_rank")
            mo = mrr(oth, "rank")
            rows_out[label] = {"original_first": first, "original_above_twin": above, "mrr_original": m, "others_mrr": mo}
            print(f"| {label} | {100 * first:.0f}% | {100 * above:.0f}% | {m:.3f} | {mo:.3f} | {'—' if label == 'off' else f'{same_sets}/{len(oth)}'} |")
        moved = [(r["query"], a["original_rank"], r["original_rank"]) for a, r in zip(amb_off, amb_on) if a["original_rank"] != r["original_rank"]]
        print("\nAmbiguous queries where personalization changed the original's rank (off → on):")
        for q, before, after in moved:
            print(f"  - {q!r}: {before} → {after}")
        harmed = [(a["query"], a["rank"], b["rank"]) for a, b in zip(oth_off, oth_on) if (b["rank"] or 99) > (a["rank"] or 99)]
        print(f"\nOther queries whose relevant file ranked WORSE with personalization on: {len(harmed)}")
        for q, before, after in harmed:
            print(f"  - {q!r}: {before} → {after}")
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({"summary": rows_out, "sweep": {str(k): v for k, v in sweep.items()}, "shipped_weight": SHIPPED_WEIGHT, "ambiguous_off": amb_off, "ambiguous_on": amb_on, "others_off": oth_off, "others_on": oth_on, "same_top5_and_tiers": same_sets}, indent=2))
        print(f"\nWritten to {OUT}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)  # success path only: skip native teardown — onnxruntime/LanceDB worker threads once raced the C++ static destructors at exit ("recursive_mutex lock failed", run_all_phases 2026-09-21) after every result was written
