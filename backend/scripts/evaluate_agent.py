"""Agent answer key, 30 questions (Phase 15 improvement 7, 2026-09-21).

Over the Phase 20 corpus (39 documents): 25 answerable questions with
the file that must be cited (8 of them need two files), and 5 questions
whose answer is NOT in the corpus, where the correct behaviour is to
abstain ("I couldn't find that in your files") — an agent that answers
those confidently is hallucinating. Reports, per model found in
models/llm: cited-right, grounded, correctly abstained, false answers on
unanswerables, figure warnings, mean/max latency.

Run with:  backend/venv/bin/python backend/scripts/evaluate_agent.py
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

from eval_corpus import DOCS, write_corpus  # noqa: E402

from app.agent import Agent, LocalLLM, Toolbox  # noqa: E402
from app.embeddings.model import EmbeddingModel, default_model_dir  # noqa: E402
from app.files.identity import build_file_record  # noqa: E402
from app.indexing import Indexer  # noqa: E402
from app.search import SearchService  # noqa: E402
from app.search.reranker import Reranker  # noqa: E402
from app.storage import FileRecordStore, KeywordStore, LanceDBVectorStore  # noqa: E402

MODELS = Path(__file__).resolve().parents[1] / "models"
OUT = Path(__file__).resolve().parents[1] / "data" / "eval_agent.json"

ANSWERABLE = [  # (question, files that must be cited — any one of them counts; "both" when two are required)
    ("At what temperature do I bake the sourdough, and for how long covered?", ["sourdough.md"]),
    ("How many folds does the croissant dough get and how long does it rest between them?", ["croissants.txt"]),
    ("What ratio and water temperature do I use for pour-over coffee?", ["coffee notes.txt"]),
    ("What do I train on Wednesday?", ["gym plan.txt"]),
    ("How far was my long run on 9 March?", ["running log.csv"]),
    ("When is the half marathon race day?", ["half marathon plan.md"]),
    ("Which band do I use for the clamshell exercise?", ["physio exercises.txt"]),
    ("How much did I pay for groceries in January?", ["monthly expenses.csv"]),
    ("When is the March invoice due and for how much?", ["invoices/march invoice.txt"]),
    ("What is the purchase order number on the April invoice?", ["invoices/april invoice.txt"]),
    ("What is my car insurance policy number and the excess?", ["insurance policy.txt"]),
    ("Who is my accountant and when is the tax filing deadline?", ["tax notes.md"]),
    ("How much goes into the index fund each month?", ["savings plan.txt"]),
    ("What is the hotel booking reference for Lisbon?", ["lisbon trip.md"]),
    ("Where do I check in in Kyoto and from what time?", ["kyoto itinerary.txt"]),
    ("What is the travel insurance certificate number?", ["travel insurance.md"]),
    ("Which pitch is booked at the campsite?", ["camping trip.md"]),
    ("What caused the checkout incident and what was the fix?", ["incident report.md"]),
    ("Who owns the migration runbook and when is the release?", ["meeting notes.txt"]),
    ("What percentage does the canary get and for how long?", ["deploy checklist.md"]),
    ("What is the wifi admin address?", ["wifi setup.txt"]),
    ("How many guests are coming to Rahul's party and where is it?", ["birthday party.md"]),
    ("When is the car service and what will it cost?", ["car service.txt"]),
    ("What time is the dentist appointment and with whom?", ["dentist.txt"]),
    ("When do I fly to Lisbon, and what is the deposit on my flat?", ["lisbon trip.md", "lease agreement.txt"]),
]
UNANSWERABLE = [
    "What is my passport number?",
    "How much did I spend on groceries in September?",
    "What is the name of my dog's vet?",
    "When does my gym membership expire?",
    "What was the score of last night's match?",
]


def build(workdir: Path):
    files = workdir / "files"
    write_corpus(files)
    model = EmbeddingModel(default_model_dir())
    vector_store = LanceDBVectorStore(str(workdir / "v"))
    keyword_store = KeywordStore(workdir / "k.db")
    record_store = FileRecordStore(workdir / "f.db")
    indexer = Indexer(model, vector_store, keyword_store, record_store)
    for name in DOCS:
        r = build_file_record(files / name)
        record_store.upsert(r)
        indexer.index_file(files / name, r.file_id, r.hash)
    search = SearchService(model, vector_store, keyword_store, record_store)
    if (MODELS / "ms-marco-MiniLM-L-6-v2" / "model.onnx").exists():
        search.reranker = Reranker(MODELS / "ms-marco-MiniLM-L-6-v2")
    return search, vector_store


def main() -> None:
    llm_files = sorted((MODELS / "llm").glob("*.gguf")) if (MODELS / "llm").exists() else []
    if not llm_files:
        print("No local LLM in models/llm — skipped.")
        return
    workdir = Path(tempfile.mkdtemp())
    try:
        search, vector_store = build(workdir)
        report = {}
        print(f"\n## Agent answer key — {len(ANSWERABLE)} answerable + {len(UNANSWERABLE)} unanswerable questions over {len(DOCS)} documents\n")
        print("| Model | cited right | grounded | abstained correctly | false answers | figure warnings | mean s | max s |")
        print("|---|---|---|---|---|---|---|---|")
        for llm_file in llm_files:
            llm = LocalLLM(llm_file)
            agent = Agent(llm, lambda: Toolbox(search, vector_store))
            right = grounded = warnings = 0
            times, misses = [], []
            for q, files in ANSWERABLE:
                t0 = time.perf_counter()
                events = list(agent.run(q))
                times.append(time.perf_counter() - t0)
                ans = next(e for e in events if e["type"] == "answer")
                cited = {c["filename"] for c in ans["citations"]}
                want = {Path(f).name for f in files}
                ok = want <= cited if len(want) > 1 else bool(want & cited)
                right += ok
                grounded += ans["grounded"]
                warnings += bool(ans.get("warnings"))
                if not ok:
                    misses.append((q, sorted(cited), ans["text"][:90]))
            abstained = false_answers = 0
            for q in UNANSWERABLE:
                t0 = time.perf_counter()
                events = list(agent.run(q))
                times.append(time.perf_counter() - t0)
                ans = next(e for e in events if e["type"] == "answer")
                if ans["grounded"]:
                    false_answers += 1
                    misses.append((q, [c["filename"] for c in ans["citations"]], "FALSE ANSWER: " + ans["text"][:80]))
                else:
                    abstained += 1
            n = len(ANSWERABLE)
            row = {"cited_right": right / n, "grounded": grounded / n, "abstained": abstained / len(UNANSWERABLE), "false_answers": false_answers, "figure_warnings": warnings, "mean_s": sum(times) / len(times), "max_s": max(times)}
            report[llm.name] = {**row, "misses": misses}
            print(f"| {llm.name} | {right}/{n} | {grounded}/{n} | {abstained}/{len(UNANSWERABLE)} | {false_answers} | {warnings} | {row['mean_s']:.1f} | {row['max_s']:.1f} |")
            for q, cited, text in misses:
                print(f"    - {q!r} → cited {cited}: {text!r}")
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, indent=2))
        print(f"\nWritten to {OUT}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)  # success path only: skip native teardown — onnxruntime/LanceDB worker threads once raced the C++ static destructors at exit ("recursive_mutex lock failed", run_all_phases 2026-09-21) after every result was written
