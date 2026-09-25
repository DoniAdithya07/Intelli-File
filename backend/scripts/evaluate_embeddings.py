"""Phase 13 — embedding model comparison on the Phase 20 corpus.

The shipped all-MiniLM-L6-v2 against the comparison models in
models/eval/ (scripts/download_eval_models.py): vector-only and hybrid
retrieval quality, embedding latency per query and per document, model
size on disk and process RSS. Vector-only isolates the model; hybrid shows
what the user would actually get.

Prints Markdown for the README and writes backend/data/eval_embeddings.json.

Run with:  backend/venv/bin/python backend/scripts/evaluate_embeddings.py
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

import psutil  # noqa: E402
from eval_corpus import DOCS, QUERIES  # noqa: E402
from evaluate_retrieval import PIPELINES, build, evaluate, metrics, pct  # noqa: E402

MODELS = Path(__file__).resolve().parents[1] / "models"
OUT = Path(__file__).resolve().parents[1] / "data" / "eval_embeddings.json"


def candidates() -> list[tuple[str, Path]]:
    found = [("all-MiniLM-L6-v2", MODELS / "all-MiniLM-L6-v2"), ("bge-small-en-v1.5 (shipped)", MODELS / "bge-small-en-v1.5")]
    eval_dir = MODELS / "eval"
    if eval_dir.exists():
        for d in sorted(eval_dir.iterdir()):
            if (d / "model.onnx").exists():
                found.append((d.name, d))
    return found


def main() -> None:
    workdir = Path(tempfile.mkdtemp())
    process = psutil.Process(os.getpid())
    try:
        report = {}
        print(f"\n## Embedding model comparison — {len(QUERIES)} queries, {len(DOCS)} documents, CPU\n")
        print("| Model | pooling | disk | vector-only Recall@5 | vector-only MRR | hybrid Recall@5 | hybrid MRR | embed query ms | index 39 docs s | RSS MB |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        for label, model_dir in candidates():
            rss0 = process.memory_info().rss / 1e6
            search, info = build(workdir, model_dir=model_dir)
            model = search.model
            # query embedding latency (median of 20)
            times = []
            for q, _, _ in QUERIES[:20]:
                t0 = time.perf_counter()
                model.embed_texts([q])
                times.append((time.perf_counter() - t0) * 1000)
            times.sort()
            vec = metrics(evaluate(search, PIPELINES["vector only"]))
            hyb = metrics(evaluate(search, PIPELINES["+ filename matching (shipped hybrid)"]))
            disk = sum(p.stat().st_size for p in model_dir.rglob("*") if p.is_file()) / 1e6
            rss = process.memory_info().rss / 1e6
            report[label] = {"pooling": model.pooling, "disk_mb": round(disk), "vector_only": vec, "hybrid": hyb, "embed_query_ms_p50": round(times[len(times) // 2], 1), "index_seconds": info["index_seconds"], "rss_mb": round(rss), "rss_delta_mb": round(rss - rss0)}
            print(f"| {label} | {model.pooling} | {disk:.0f} MB | {pct(vec['recall@5'])} | {vec['MRR']:.3f} | {pct(hyb['recall@5'])} | {hyb['MRR']:.3f} | {times[len(times)//2]:.1f} | {info['index_seconds']} | {rss:.0f} |")
            # each model gets its own stores: drop them so the next one starts clean
            for p in workdir.glob("*_short_default*"):
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink()
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, indent=2))
        print(f"\nWritten to {OUT}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)  # success path only: skip native teardown — onnxruntime/LanceDB worker threads once raced the C++ static destructors at exit ("recursive_mutex lock failed", run_all_phases 2026-09-21) after every result was written
