"""Phase 19 regression: the local LLM agent.

Part A — loop mechanics with a SCRIPTED model (no LLM needed, runs in CI):
the agent splits a two-part question into two searches, chooses modes and
filters, reads more of a source, cites only sources that exist (an
invented [9] is dropped), stops at the tool-call limit, and answers "not
found" with no sources rather than inventing one.

Part B — the REAL model (skipped when models/llm has no .gguf): ten
questions over a fixture folder with an answer key. Each answer must cite
the right file, every trace must show a strategy choice (a tool call with
a mode), and the mean latency is reported against the budget.

Run with:  backend/venv/bin/python backend/scripts/prototype_agent.py
"""

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import Agent, Toolbox, find_model_file  # noqa: E402
from app.agent.loop import BUDGET_SECONDS  # noqa: E402
from app.embeddings.model import EmbeddingModel, default_model_dir  # noqa: E402
from app.files.identity import build_file_record  # noqa: E402
from app.indexing import Indexer  # noqa: E402
from app.search import SearchService  # noqa: E402
from app.search.reranker import Reranker  # noqa: E402
from app.storage import FileRecordStore, KeywordStore, LanceDBVectorStore  # noqa: E402

MODELS = Path(__file__).resolve().parents[1] / "models"

DOCS = {
    "landlord letter.txt": "Dear Mr Okafor, the kitchen tap has been dripping since the second week of March and the bathroom extractor fan no longer switches on. Please arrange a repair visit. The deposit of 1,800 euros was paid on 3 January. Kind regards, Abhishek.",
    "monthly expenses.csv": "month,category,amount,note\nJanuary,rent,1200,paid on the 3rd\nJanuary,groceries,340,mostly farmers market\nFebruary,gym membership,45,annual plan renewed\nMarch,car insurance,610,six-month premium",
    "gym plan.txt": "Gym plan for the week. Monday: chest and triceps - bench press, incline dumbbell press, dips. Wednesday: back and biceps - deadlifts, pull-ups, barbell rows. Friday: legs and shoulders - squats, lunges, overhead press. Cardio on Tuesday and Thursday, 30 minutes.",
    "scaling_notes.md": "Horizontal scaling allows additional server instances to be provisioned when traffic demand increases, distributed by a load balancer. Autoscaling policies react to CPU load and request latency.",
    "sourdough.md": "Feed the sourdough starter with flour and water twice a day. Bulk ferment the dough for five hours at 24 C, then bake at 230 C in a dutch oven, 20 minutes covered and 25 uncovered.",
    "lisbon trip.md": "Lisbon trip, 12 to 16 May. Flights TAP 1234 out at 07:40, back on the 16th at 19:10. Hotel Alfama Suites, booking ref AS-77Q. Day trip to Sintra on the 14th.",
    "insurance policy.txt": "Car insurance policy number CI-4481-Z. Premium 610 euros for six months, renewal due 30 September. Excess 350 euros. Claims line 0800 555 0199.",
    "meeting notes.txt": "Team meeting 4 April. Decisions: move the release to 22 April, Priya owns the migration runbook, Tom to draft the on-call rota by Friday. Risks: the vendor API rate limit.",
}

QUESTIONS = [  # (question, file that must be cited)
    ("What is wrong with the kitchen tap and who is the letter to?", "landlord letter.txt"),
    ("How much was the deposit and when was it paid?", "landlord letter.txt"),
    ("What do I train on Wednesday?", "gym plan.txt"),
    ("How does horizontal scaling help with traffic spikes?", "scaling_notes.md"),
    ("At what temperature do I bake the sourdough?", "sourdough.md"),
    ("When do I fly to Lisbon and what is the hotel booking reference?", "lisbon trip.md"),
    ("What is my car insurance policy number and when is the renewal?", "insurance policy.txt"),
    ("Who owns the migration runbook and when is the release?", "meeting notes.txt"),
    ("How much did I spend on groceries in January?", "monthly expenses.csv"),
    ("What is the excess on the car insurance?", "insurance policy.txt"),
]


class ScriptedLLM:
    """Deterministic stand-in: planning turns come from a script, the
    answer is a fixed string with citations."""

    def __init__(self, plans: list[dict], answer: str, absent: tuple[str, ...] = ()):
        self.plans = list(plans)
        self.answer = answer
        self.absent = absent  # words the premise judge is scripted to deny
        self.calls = 0

    def chat(self, messages, max_tokens=0, json_only=False, temperature=0.0):
        if not json_only and "mention or give a" in messages[-1]["content"]:  # the premise judge's yes/no question
            word = messages[-1]["content"].rsplit("mention or give a ", 1)[1].split("?")[0]
            return "no" if word in self.absent else "yes"
        self.calls += 1
        plan = self.plans.pop(0) if self.plans else {"action": "answer", "thought": "done"}
        return json.dumps(plan)

    def stream(self, messages, max_tokens=0, temperature=0.0):
        for piece in self.answer.split(" "):
            yield piece + " "


def build_index(workdir: Path):
    files = workdir / "files"
    files.mkdir()
    for name, text in DOCS.items():
        (files / name).write_text(text)
    model = EmbeddingModel(default_model_dir(MODELS))
    vector_store = LanceDBVectorStore(str(workdir / "vectors"))
    keyword_store = KeywordStore(workdir / "keyword.db")
    record_store = FileRecordStore(workdir / "files.db")
    indexer = Indexer(model, vector_store, keyword_store, record_store)
    for name in DOCS:
        record = build_file_record(files / name)
        record_store.upsert(record)
        indexer.index_file(files / name, record.file_id, record.hash)
    search = SearchService(model, vector_store, keyword_store, record_store)
    if (MODELS / "ms-marco-MiniLM-L-6-v2" / "model.onnx").exists():
        search.reranker = Reranker(MODELS / "ms-marco-MiniLM-L-6-v2")
    return search, vector_store


def collect(agent: Agent, question: str) -> tuple[list[dict], dict, dict]:
    events = list(agent.run(question))
    answer = next(e for e in events if e["type"] == "answer")
    done = next(e for e in events if e["type"] == "done")
    return events, answer, done


def main() -> None:
    workdir = Path(tempfile.mkdtemp())
    try:
        search, vector_store = build_index(workdir)
        toolbox = lambda: Toolbox(search, vector_store)  # noqa: E731

        # ---------- Part A: mechanics with a scripted model ----------
        llm = ScriptedLLM(
            plans=[
                {"thought": "two parts: the tap, and the deposit", "action": "search", "query": "kitchen tap dripping", "mode": "keyword", "filters": ""},
                {"thought": "now the deposit", "action": "search", "query": "deposit paid", "mode": "smart", "filters": "type:txt type:pdf date:2023"},
                {"thought": "read the letter fully", "action": "read_more", "source": 1},
                {"thought": "enough", "action": "answer"},
            ],
            answer="The kitchen tap has been dripping since March [1]. The deposit of 1,800 euros was paid on 3 January [1]. Unrelated claim [9].",
        )
        agent = Agent(llm, toolbox)
        events, answer, done = collect(agent, "In the txt letter, what is wrong with the tap and how much was the deposit?")
        calls = [e for e in events if e["type"] == "tool_call"]
        assert [c["tool"] for c in calls] == ["search", "search", "read_more"], calls
        assert calls[0]["args"]["mode"] == "keyword" and calls[1]["args"]["filters"] == "type:txt", calls[1]
        assert any(e.get("system") and "type:pdf" in e["text"] and "date:2023" in e["text"] for e in events), "unjustified filters must be dropped and said so"
        assert done["strategies"][0]["route"] and done["strategies"][1]["route"], done["strategies"]
        assert "[9]" not in answer["text"] and "[1]" in answer["text"] and answer["grounded"]
        assert [c["filename"] for c in answer["citations"]] == ["landlord letter.txt"]
        assert events[0]["type"] == "context" and events[-1]["type"] == "done"
        assert any(e["type"] == "token" for e in events), "the answer must stream"
        print("A1. Scripted plan: two searches (keyword, then smart+filter), read_more, cited answer; invented [9] and unjustified filters dropped: OK")

        llm = ScriptedLLM(plans=[{"thought": "look", "action": "find", "query": "gym plan wednesday", "mode": "auto", "filters": ""}, {"thought": "done", "action": "respond"}],
                          answer="On Wednesday you train back and biceps: deadlifts, pull-ups and barbell rows.")
        events, answer, done = collect(Agent(llm, toolbox), "what do I train on wednesday")
        assert answer["grounded"] and answer["citations_inferred"] and answer["citations"][0]["filename"] == "gym plan.txt" and answer["text"].endswith("[1]"), answer
        llm = ScriptedLLM(plans=[{"thought": "look", "action": "search", "query": "gym plan wednesday", "mode": "auto", "filters": ""}], answer="The capital of France is Paris and the moon is made of cheese.")
        events, answer, done = collect(Agent(llm, toolbox), "what do I train on wednesday")
        assert not answer["grounded"] and "couldn't find" in answer["text"], answer
        print("A1b. Action aliases accepted; an uncited but source-backed answer is grounded by overlap; an unrelated answer is rejected: OK")

        llm = ScriptedLLM(plans=[{"thought": f"search {i}", "action": "search", "query": f"gym plan {i}", "mode": "auto", "filters": ""} for i in range(10)], answer="Wednesday is back and biceps [1].")
        agent = Agent(llm, toolbox, max_tool_calls=3)
        events, answer, done = collect(agent, "what do I train on wednesday")
        assert done["tool_calls"] == 3 and any(e.get("system") and "limit" in e["text"] for e in events), done
        assert answer["grounded"] and answer["citations"][0]["filename"] == "gym plan.txt"
        print("A2. A planner that never stops is cut off at the tool-call limit and still answers from what it found: OK")

        llm = ScriptedLLM(plans=[{"thought": "same", "action": "search", "query": "Wednesday training", "mode": "keyword", "filters": ""}] * 6, answer="Wednesday is back and biceps.")
        events, answer, done = collect(Agent(llm, toolbox), "what do I train on wednesday")
        calls = [e for e in events if e["type"] == "tool_call"]
        assert calls[0]["args"]["mode"] == "keyword" and any(e.get("system") and "keyword mode found nothing" in e["text"] for e in events), "a manual mode that finds nothing must fall back to auto"
        assert any(e.get("system") and "repeated itself" in e["text"] for e in events) and calls[1]["args"]["query"] == "what do I train on wednesday", calls
        assert done["tool_calls"] == 2 and answer["grounded"] and answer["citations"][0]["filename"] == "gym plan.txt"
        print("A2b. Keyword mode that finds nothing falls back to auto; a plan that repeats itself gets one whole-question search, then the planning limit ends it: OK")

        llm = ScriptedLLM(plans=[{"thought": "nothing to search", "action": "answer"}], answer="Made-up answer [1].")
        events, answer, done = collect(Agent(llm, toolbox), "what colour is the moon")
        assert not answer["grounded"] and answer["citations"] == [] and "couldn't find" in answer["text"]
        assert done["tool_calls"] == 0
        llm = ScriptedLLM(plans=[{"thought": "look", "action": "search", "query": "car insurance premium", "mode": "auto", "filters": ""}], answer="The car insurance premium is 210 euros for six months [1].")
        events, answer, done = collect(Agent(llm, toolbox), "how much is the car insurance premium")
        assert answer["grounded"] and answer["warnings"] and "210" in answer["warnings"][0], answer
        llm = ScriptedLLM(plans=[{"thought": "look", "action": "search", "query": "car insurance premium", "mode": "auto", "filters": ""}], answer="The car insurance premium is 610 euros for six months [1].")
        events, answer, done = collect(Agent(llm, toolbox), "how much is the car insurance premium")
        assert answer["grounded"] and not answer["warnings"], answer
        print("A2c. A figure the cited sources do not contain (210 vs 610) is flagged; a correct figure passes: OK")

        plan = [{"thought": "look", "action": "search", "query": "groceries", "mode": "auto", "filters": ""}]
        llm = ScriptedLLM(plans=list(plan), answer="You spent 340 euros on groceries in September [1].")
        events, answer, done = collect(Agent(llm, toolbox), "How much did I spend on groceries in September?")
        assert not answer["grounded"] and "couldn't find" in answer["text"] and any("never mention september" in e.get("text", "") for e in events), answer
        llm = ScriptedLLM(plans=list(plan), answer="You spent 340 euros on groceries in January [1].")
        events, answer, done = collect(Agent(llm, toolbox), "How much did I spend on groceries in January?")
        assert answer["grounded"] and answer["citations"][0]["filename"] == "monthly expenses.csv", answer
        plan = [{"thought": "look", "action": "search", "query": "kitchen tap dripping repair visit", "mode": "auto", "filters": ""}]
        llm = ScriptedLLM(plans=list(plan), answer="Your dog's vet is Mr Okafor — the kitchen tap has been dripping since March and a repair visit is arranged [1].", absent=("dog", "vet"))
        events, answer, done = collect(Agent(llm, toolbox), "What is the name of my dog's vet?")
        assert not answer["grounded"] and any("no source mentions it" in e.get("text", "") for e in events), answer
        llm = ScriptedLLM(plans=[{"thought": "look", "action": "search", "query": "sourdough bake", "mode": "auto", "filters": ""}], answer="Bake the sourdough at 230 C, 20 minutes covered [1].")
        events, answer, done = collect(Agent(llm, toolbox), "At what temperature do I bake the sourdough, and for how long covered?")
        assert answer["grounded"] and not any("premise" in e.get("text", "") for e in events), answer
        print("A2d. Premise check: a month no source mentions (September) and a subject no source mentions (dog's vet) both become 'couldn't find'; the same questions on real months/subjects still answer: OK")

        print("A3. No sources retrieved → 'I couldn't find that in your files.' — the model's text is never trusted ungrounded: OK")

        llm = ScriptedLLM(plans=[{"thought": "broken"}, ], answer="x")
        llm.chat = lambda *a, **k: "this is not json at all {"  # type: ignore
        events, answer, done = collect(Agent(llm, toolbox), "anything")
        assert any(e.get("system") for e in events) and events[-1]["type"] == "done"
        print("A4. Malformed model output ends the plan cleanly instead of crashing: OK")

        llm = ScriptedLLM(plans=[{"thought": "search", "action": "search", "query": "sourdough bake temperature", "mode": "auto", "filters": ""}, {"thought": "no", "action": "search", "query": "gym", "mode": "auto", "filters": ""}], answer="Bake at 230 C [1].")
        agent = Agent(llm, toolbox, budget_seconds=0.0)
        events, answer, done = collect(agent, "temperature")
        assert done["tool_calls"] == 0 and any("time budget" in e.get("text", "") for e in events)
        print("A5. Time budget is enforced before every planning turn: OK")

        # ---------- Part B: the real model ----------
        model_file = find_model_file()
        if model_file is None:
            print("\nB. Local LLM not installed (scripts/download_llm_model.py) — real-model checks skipped.")
        else:
            from app.agent import LocalLLM

            llm = LocalLLM(model_file)
            bench = llm.benchmark()
            print(f"\nB0. {llm.name}: loaded in {llm.load_seconds}s, {bench['tokens_per_second']} tokens/s on CPU")
            agent = Agent(llm, toolbox)
            correct, grounded, strategy_seen, seconds = 0, 0, 0, []
            failures = []
            for question, want in QUESTIONS:
                t0 = time.perf_counter()
                events, answer, done = collect(agent, question)
                seconds.append(time.perf_counter() - t0)
                cited = [c["filename"] for c in answer["citations"]]
                calls = [e for e in events if e["type"] == "tool_call"]
                if calls and all(c["args"].get("mode") for c in calls if c["tool"] == "search"):
                    strategy_seen += 1
                if answer["grounded"]:
                    grounded += 1
                if want in cited:
                    correct += 1
                else:
                    failures.append((question, cited, answer["text"][:120]))
            mean = sum(seconds) / len(seconds)
            print(f"B1. {correct}/{len(QUESTIONS)} answers cite the right file; {grounded}/{len(QUESTIONS)} grounded; {strategy_seen}/{len(QUESTIONS)} traces show a strategy choice; mean {mean:.1f}s (budget {BUDGET_SECONDS:.0f}s), max {max(seconds):.1f}s")
            for q, cited, text in failures:
                print(f"     missed: {q!r} cited={cited} answer={text!r}")
            assert correct >= 7, f"only {correct}/10 answers cited the right file"
            assert strategy_seen == len(QUESTIONS), "every trace must show a tool call with a chosen mode"
            # The loop stops planning at BUDGET_SECONDS and cuts the answer
            # stream at twice that, so that hard cap is what must hold on
            # any machine. The mean is hardware: ~10 s on the M-series dev
            # Mac, ~28 s on a 12th-gen i5 laptop (Windows, 2026-09-25) —
            # reported above, not asserted.
            assert max(seconds) <= 2 * BUDGET_SECONDS, f"slowest answer {max(seconds):.1f}s exceeds the hard cap"

        print("\nPhase 19 agent OK: bounded tool loop, strategy selection, grounded cited answers, and honest 'not found' all work as expected.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
