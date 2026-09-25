"""Phase 13 — retrieval, chunking and resource benchmarks on the Phase 20
corpus (eval_corpus.py: 39 documents, 60 labelled queries).

Retrieval experiments (the PRD's list): BM25-only, vector-only,
BM25 + vector + RRF (hybrid), hybrid + reranker (promotion). Metrics:
Recall@5, Recall@10, MRR, NDCG@5, latency P50 / P95 against the PRD's
targets (P50 < 300 ms, P95 < 1 s), and the process's RSS.

Chunking experiments: the model's window is 256 tokens, so the configs are
128/16, 224/32 (shipped) and 254/32 tokens — measured on hybrid.

Prints Markdown for the README and writes backend/data/eval_retrieval.json.

Run with:  backend/venv/bin/python backend/scripts/evaluate_retrieval.py
"""

import json
import math
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import psutil  # noqa: E402
from eval_corpus import DOCS, QUERIES, write_corpus  # noqa: E402

from app.embeddings.model import EmbeddingModel, default_model_dir  # noqa: E402
from app.files.identity import build_file_record  # noqa: E402
from app.indexing import Indexer  # noqa: E402
from app.search import SearchService  # noqa: E402
from app.search.query_parsing import parse_query  # noqa: E402
from app.search.reranker import Reranker  # noqa: E402
from app.search.router import Plan  # noqa: E402
from app.storage import FileRecordStore, KeywordStore, LanceDBVectorStore  # noqa: E402

MODELS = Path(__file__).resolve().parents[1] / "models"
OUT = Path(__file__).resolve().parents[1] / "data" / "eval_retrieval.json"
REPEATS = 3

PIPELINES = {
    "BM25 only": Plan("bm25", keyword=True),
    "vector only": Plan("vector", semantic=True),
    "BM25 + vector + RRF": Plan("hybrid", keyword=True, semantic=True),
    "+ filename matching (shipped hybrid)": Plan("hybrid", names=True, keyword=True, semantic=True),
    "+ reranker (promotion)": Plan("hybrid+rerank", names=True, keyword=True, semantic=True, rerank=True),
}
CHUNK_CONFIGS = [(128, 16), (224, 32), (254, 32)]


# Neutral filler for the long-document variant: the real content of each
# document is buried after ~700 words of generic sentences about nothing in
# particular, so a chunk that mixes filler with content embeds worse than
# one that isolates it — the effect chunk size has on real long documents.
_FILLER = [
    "The committee reviewed the agenda and agreed to circulate the minutes before the next session.",
    "Attendance was slightly lower than last quarter, which the chair attributed to the holiday period.",
    "A short discussion followed about the format of future updates and the length of the reports.",
    "Members were reminded that the room booking must be confirmed a week in advance.",
    "The general feeling was that communication had improved but could still be more regular.",
    "Several people noted that the shared calendar is now the reference for all dates.",
    "No further business was raised and the session closed a few minutes early.",
    "The next update will summarise progress in the usual format and highlight open questions.",
]


def long_corpus(files: Path, words: int = 700) -> None:
    import random

    rng = random.Random(13)
    for name, text in DOCS.items():
        filler = []
        while sum(len(f.split()) for f in filler) < words:
            filler.append(rng.choice(_FILLER))
        path = files / name
        path.parent.mkdir(parents=True, exist_ok=True)
        # CSV rows must stay rows: filler goes in a note column instead.
        if name.endswith(".csv"):
            head, *rows = text.split("\n")
            path.write_text("\n".join([head] + [row + " " + " ".join(filler[:3]) for row in rows]))
        else:
            path.write_text(" ".join(filler) + "\n\n" + text)


def build(workdir: Path, model_dir: Path = default_model_dir(MODELS), chunk: tuple[int, int] | None = None, long: bool = False):
    files = workdir / ("files_long" if long else "files")
    if not files.exists():
        (long_corpus if long else write_corpus)(files)
    model = EmbeddingModel(model_dir)
    tag = f"{'long' if long else 'short'}_{chunk or 'default'}"
    vector_store = LanceDBVectorStore(str(workdir / f"vectors_{tag}"))
    keyword_store = KeywordStore(workdir / f"keyword_{tag}.db")
    record_store = FileRecordStore(workdir / f"files_{tag}.db")
    indexer = Indexer(model, vector_store, keyword_store, record_store)
    if chunk:
        indexer.chunk_tokens, indexer.chunk_overlap = chunk
    t0 = time.perf_counter()
    n_chunks = 0
    for name in DOCS:
        record = build_file_record(files / name)
        record_store.upsert(record)
        n_chunks += indexer.index_file(files / name, record.file_id, record.hash)
    index_seconds = time.perf_counter() - t0
    search = SearchService(model, vector_store, keyword_store, record_store)
    if (MODELS / "ms-marco-MiniLM-L-6-v2" / "model.onnx").exists():
        search.reranker = Reranker(MODELS / "ms-marco-MiniLM-L-6-v2")
    return search, {"chunks": n_chunks, "index_seconds": round(index_seconds, 1)}


def run_plan(search: SearchService, plan: Plan, query: str, top_k: int = 10) -> list[dict]:
    parsed = parse_query(query)
    active = search._active_records()
    if parsed.filters.any():
        active = [r for r in active if parsed.filters.matches(r.path, r.size, r.modified_time)]
    text = parsed.text
    if not text and parsed.filters.any():
        return search._metadata_results(parsed, active, top_k, [])
    return search._run_plan(plan, text, text, parsed, active, top_k, [])


def metrics(rows: list[dict]) -> dict:
    n = len(rows)
    lat = sorted(r["ms"] for r in rows)
    return {
        "n": n,
        "recall@5": sum(r["recall5"] for r in rows) / n,
        "recall@10": sum(r["recall10"] for r in rows) / n,
        "MRR": sum(r["rr"] for r in rows) / n,
        "NDCG@5": sum(r["ndcg5"] for r in rows) / n,
        "p50_ms": lat[len(lat) // 2],
        "p95_ms": lat[min(len(lat) - 1, int(math.ceil(0.95 * len(lat))) - 1)],
        "mean_ms": sum(lat) / n,
    }


def evaluate(search: SearchService, plan: Plan) -> list[dict]:
    rows = []
    for query, relevant, _tier in QUERIES:
        names = {Path(f).name for f in relevant}
        latencies = []
        for _ in range(REPEATS):
            t0 = time.perf_counter()
            results = run_plan(search, plan, query)
            latencies.append((time.perf_counter() - t0) * 1000)
        got = [r["filename"] for r in results]
        first = next((i for i, g in enumerate(got, start=1) if g in names), None)
        found5 = len(names & set(got[:5])) / len(names)
        found10 = len(names & set(got[:10])) / len(names)
        dcg = sum(1 / math.log2(i + 1) for i, g in enumerate(got[:5], start=1) if g in names)
        idcg = sum(1 / math.log2(i + 1) for i in range(1, min(len(names), 5) + 1))
        rows.append({"query": query, "rank": first, "rr": 1 / first if first else 0.0, "recall5": found5, "recall10": found10, "ndcg5": dcg / idcg if idcg else 0.0, "ms": statistics.median(latencies)})
    return rows


def pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def main() -> None:
    workdir = Path(tempfile.mkdtemp())
    process = psutil.Process(os.getpid())
    try:
        search, build_info = build(workdir)
        search.search("warm up")
        rss_after_load = process.memory_info().rss / 1e6

        print(f"\n## Retrieval experiments — {len(QUERIES)} queries, {len(DOCS)} documents ({build_info['chunks']} chunks), median of {REPEATS} runs\n")
        print("| Pipeline | Recall@5 | Recall@10 | MRR | NDCG@5 | P50 ms | P95 ms |")
        print("|---|---|---|---|---|---|---|")
        report = {"retrieval": {}, "chunking": {}, "resources": {}}
        for label, plan in PIPELINES.items():
            if plan.rerank and search.reranker is None:
                continue
            m = metrics(evaluate(search, plan))
            report["retrieval"][label] = m
            print(f"| {label} | {pct(m['recall@5'])} | {pct(m['recall@10'])} | {m['MRR']:.3f} | {m['NDCG@5']:.3f} | {m['p50_ms']:.1f} | {m['p95_ms']:.1f} |")
        hybrid = report["retrieval"]["+ filename matching (shipped hybrid)"]
        print(f"\nPRD targets: P50 < 300 ms → **{hybrid['p50_ms']:.1f} ms**; P95 < 1 s → **{hybrid['p95_ms']:.1f} ms** (shipped hybrid).")
        rss_peak = process.memory_info().rss / 1e6
        report["resources"] = {"rss_after_models_mb": round(rss_after_load), "rss_after_benchmark_mb": round(rss_peak), "cpu_count": psutil.cpu_count()}
        print(f"Process RSS: {rss_after_load:.0f} MB after loading MiniLM + reranker + index, {rss_peak:.0f} MB after the benchmark (CPU only, {psutil.cpu_count()} cores).")

        print("\n## Chunking experiments (hybrid, tokens/overlap; the model window is 256 tokens)\n")
        print("| Chunk config | chunks | index s | Recall@5 | MRR | NDCG@5 | P50 ms |")
        print("|---|---|---|---|---|---|---|")
        for chunk in CHUNK_CONFIGS:
            s2, info = build(workdir, chunk=chunk)
            m = metrics(evaluate(s2, PIPELINES["+ filename matching (shipped hybrid)"]))
            report["chunking"][f"{chunk[0]}/{chunk[1]}"] = {**m, **info}
            shipped = " (shipped)" if chunk == (224, 32) else ""
            print(f"| {chunk[0]}/{chunk[1]}{shipped} | {info['chunks']} | {info['index_seconds']} | {pct(m['recall@5'])} | {m['MRR']:.3f} | {m['NDCG@5']:.3f} | {m['p50_ms']:.1f} |")
        print("\n(Short documents fit one chunk at any config, so this table is flat by construction.)")

        print("\n## Chunking experiments on LONG documents (each document = ~700 words of neutral filler, then its content)\n")
        print("| Chunk config | chunks | index s | Recall@5 | MRR | NDCG@5 | P50 ms |")
        print("|---|---|---|---|---|---|---|")
        report["chunking_long"] = {}
        for chunk in CHUNK_CONFIGS:
            s3, info = build(workdir, chunk=chunk, long=True)
            m = metrics(evaluate(s3, PIPELINES["+ filename matching (shipped hybrid)"]))
            report["chunking_long"][f"{chunk[0]}/{chunk[1]}"] = {**m, **info}
            shipped = " (shipped)" if chunk == (224, 32) else ""
            print(f"| {chunk[0]}/{chunk[1]}{shipped} | {info['chunks']} | {info['index_seconds']} | {pct(m['recall@5'])} | {m['MRR']:.3f} | {m['NDCG@5']:.3f} | {m['p50_ms']:.1f} |")

        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, indent=2))
        print(f"\nWritten to {OUT}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)  # success path only: skip native teardown — onnxruntime/LanceDB worker threads once raced the C++ static destructors at exit ("recursive_mutex lock failed", run_all_phases 2026-09-21) after every result was written
