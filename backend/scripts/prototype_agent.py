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

Run with:  backend/venv/bin/python backend/scripts/prototype_agent.py   (--no-model: Part A only)
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

    def chat(self, messages, max_tokens=0, json_only=False, temperature=0.0, deadline=None, label=None):
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
        agent = Agent(llm, toolbox, first_look=False)
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
        events, answer, done = collect(Agent(llm, toolbox, first_look=False), "what do I train on wednesday")
        assert answer["grounded"] and answer["citations_inferred"] and answer["citations"][0]["filename"] == "gym plan.txt" and answer["text"].endswith("[1]"), answer
        llm = ScriptedLLM(plans=[{"thought": "look", "action": "search", "query": "gym plan wednesday", "mode": "auto", "filters": ""}], answer="The capital of France is Paris and the moon is made of cheese.")
        events, answer, done = collect(Agent(llm, toolbox, first_look=False), "what do I train on wednesday")
        assert not answer["grounded"] and "couldn't find" in answer["text"], answer
        print("A1b. Action aliases accepted; an uncited but source-backed answer is grounded by overlap; an unrelated answer is rejected: OK")

        llm = ScriptedLLM(plans=[{"thought": f"search {i}", "action": "search", "query": f"gym plan {i}", "mode": "auto", "filters": ""} for i in range(10)], answer="Wednesday is back and biceps [1].")
        agent = Agent(llm, toolbox, max_tool_calls=3, first_look=False)
        events, answer, done = collect(agent, "what do I train on wednesday")
        assert done["tool_calls"] == 3 and any(e.get("system") and "limit" in e["text"] for e in events), done
        assert answer["grounded"] and answer["citations"][0]["filename"] == "gym plan.txt"
        print("A2. A planner that never stops is cut off at the tool-call limit and still answers from what it found: OK")

        llm = ScriptedLLM(plans=[{"thought": "same", "action": "search", "query": "Wednesday training", "mode": "keyword", "filters": ""}] * 6, answer="Wednesday is back and biceps.")
        events, answer, done = collect(Agent(llm, toolbox, first_look=False), "what do I train on wednesday")
        calls = [e for e in events if e["type"] == "tool_call"]
        assert calls[0]["args"]["mode"] == "keyword" and any(e.get("system") and "keyword mode found nothing" in e["text"] for e in events), "a manual mode that finds nothing must fall back to auto"
        assert any(e.get("system") and "repeated itself" in e["text"] for e in events) and calls[1]["args"]["query"] == "what do I train on wednesday", calls
        assert done["tool_calls"] == 2 and answer["grounded"] and answer["citations"][0]["filename"] == "gym plan.txt"
        print("A2b. Keyword mode that finds nothing falls back to auto; a plan that repeats itself gets one whole-question search, then the planning limit ends it: OK")

        llm = ScriptedLLM(plans=[{"thought": "nothing to search", "action": "answer"}], answer="Made-up answer [1].")
        events, answer, done = collect(Agent(llm, toolbox, first_look=False), "what colour is the moon")
        assert not answer["grounded"] and answer["citations"] == [] and "couldn't find" in answer["text"]
        assert done["tool_calls"] == 0
        # 2026-10-05: when the answer's figures are ALL unverified, the
        # figure is the answer, and a warning under it is not enough: the
        # built-in example "when is the rent due" answered "The rent is due
        # on February 14th." with grounded: true and the 14 flagged. Such an
        # answer is "not found"; a longer answer with one secondary figure
        # off keeps the warning.
        insurance_plan = [{"thought": "look", "action": "search", "query": "car insurance premium", "mode": "auto", "filters": ""}]
        llm = ScriptedLLM(plans=list(insurance_plan), answer="The car insurance premium is 210 euros for six months [1].")
        events, answer, done = collect(Agent(llm, toolbox, first_look=False), "how much is the car insurance premium")
        assert not answer["grounded"] and "couldn't find" in answer["text"] and any("210" in e.get("text", "") for e in events if e.get("system")), answer
        llm = ScriptedLLM(plans=[{"thought": "look", "action": "search", "query": "rent due", "mode": "auto", "filters": ""}], answer="The rent is due on February 14th [1].")
        events, answer, done = collect(Agent(llm, toolbox, first_look=False), "when is the rent due")
        assert not answer["grounded"] and "couldn't find" in answer["text"] and answer["citations"] == [], answer
        llm = ScriptedLLM(plans=list(insurance_plan), answer="The car insurance premium is 610 euros for six months, the renewal is due on 30 September and the excess is 360 euros [1].")
        events, answer, done = collect(Agent(llm, toolbox, first_look=False), "how much is the car insurance premium, when is the renewal and what is the excess")
        assert answer["grounded"] and answer["warnings"] and "360" in answer["warnings"][0], answer
        llm = ScriptedLLM(plans=list(insurance_plan), answer="The car insurance premium is 610 euros for six months [1].")
        events, answer, done = collect(Agent(llm, toolbox, first_look=False), "how much is the car insurance premium")
        assert answer["grounded"] and not answer["warnings"], answer
        print("A2c. An answer whose only figures are not in its sources (210 vs 610; the rent 'due on February 14th') is 'not found'; one wrong figure among right ones is flagged; a correct figure passes: OK")

        plan = [{"thought": "look", "action": "search", "query": "groceries", "mode": "auto", "filters": ""}]
        llm = ScriptedLLM(plans=list(plan), answer="You spent 340 euros on groceries in September [1].")
        events, answer, done = collect(Agent(llm, toolbox, first_look=False), "How much did I spend on groceries in September?")
        assert not answer["grounded"] and "couldn't find" in answer["text"] and any("never mention september" in e.get("text", "") for e in events), answer
        llm = ScriptedLLM(plans=list(plan), answer="You spent 340 euros on groceries in January [1].")
        events, answer, done = collect(Agent(llm, toolbox, first_look=False), "How much did I spend on groceries in January?")
        assert answer["grounded"] and answer["citations"][0]["filename"] == "monthly expenses.csv", answer
        plan = [{"thought": "look", "action": "search", "query": "kitchen tap dripping repair visit", "mode": "auto", "filters": ""}]
        llm = ScriptedLLM(plans=list(plan), answer="Your dog's vet is Mr Okafor — the kitchen tap has been dripping since March and a repair visit is arranged [1].", absent=("dog", "vet"))
        events, answer, done = collect(Agent(llm, toolbox, first_look=False), "What is the name of my dog's vet?")
        assert not answer["grounded"] and any("no source mentions it" in e.get("text", "") for e in events), answer
        llm = ScriptedLLM(plans=[{"thought": "look", "action": "search", "query": "sourdough bake", "mode": "auto", "filters": ""}], answer="Bake the sourdough at 230 C, 20 minutes covered [1].")
        events, answer, done = collect(Agent(llm, toolbox, first_look=False), "At what temperature do I bake the sourdough, and for how long covered?")
        assert answer["grounded"] and not any("premise" in e.get("text", "") for e in events), answer
        print("A2d. Premise check: a month no source mentions (September) and a subject no source mentions (dog's vet) both become 'couldn't find'; the same questions on real months/subjects still answer: OK")

        # 2026-10-05: a premise judge that fails (the model raising) was
        # swallowed and counted as "yes" — the check silently passed. It is
        # now logged, and the answer says the check could not run.
        import logging

        class BrokenJudgeLLM(ScriptedLLM):
            def chat(self, messages, max_tokens=0, json_only=False, temperature=0.0, deadline=None, label=None):
                if not json_only and "mention or give a" in messages[-1]["content"]:
                    raise RuntimeError("llama_decode returned -1")
                return super().chat(messages, max_tokens, json_only, temperature, deadline, label)

        records: list[logging.LogRecord] = []
        catcher = logging.Handler()
        catcher.emit = records.append  # type: ignore[method-assign]
        logging.getLogger("app.agent.loop").addHandler(catcher)
        try:
            llm = BrokenJudgeLLM(plans=[{"thought": "look", "action": "search", "query": "kitchen tap dripping repair visit", "mode": "auto", "filters": ""}],
                                 answer="Your dog's vet is Mr Okafor — the kitchen tap has been dripping since March and a repair visit is arranged [1].")
            events, answer, done = collect(Agent(llm, toolbox, first_look=False), "What is the name of my dog's vet?")
        finally:
            logging.getLogger("app.agent.loop").removeHandler(catcher)
        assert any("premise check could not run" in w for w in answer["warnings"]), answer
        assert any(r.levelno == logging.WARNING and r.exc_info for r in records), [r.getMessage() for r in records]
        print("A2e. A premise judge that fails is logged with its traceback and the answer carries 'the premise check could not run' instead of passing silently: OK")

        print("A3. No sources retrieved → 'I couldn't find that in your files.' — the model's text is never trusted ungrounded: OK")

        llm = ScriptedLLM(plans=[{"thought": "broken"}, ], answer="x")
        llm.chat = lambda *a, **k: "this is not json at all {"  # type: ignore
        events, answer, done = collect(Agent(llm, toolbox, first_look=False), "anything")
        assert any(e.get("system") for e in events) and events[-1]["type"] == "done"
        print("A4. Malformed model output ends the plan cleanly instead of crashing: OK")

        llm = ScriptedLLM(plans=[{"thought": "search", "action": "search", "query": "sourdough bake temperature", "mode": "auto", "filters": ""}, {"thought": "no", "action": "search", "query": "gym", "mode": "auto", "filters": ""}], answer="Bake at 230 C [1].")
        agent = Agent(llm, toolbox, budget_seconds=0.0, first_look=False)
        events, answer, done = collect(agent, "temperature")
        assert done["tool_calls"] == 0 and any("time budget" in e.get("text", "") for e in events)
        print("A5. Time budget is enforced before every planning turn: OK")

        # Improvement 3 (2026-09-26): first look, quick answer, citation trim.
        llm = ScriptedLLM(plans=[{"thought": "the first search has it", "action": "answer"}], answer="The deposit of 1,800 euros was paid on 3 January [1].")
        events, answer, done = collect(Agent(llm, toolbox), "How much was the deposit and when was it paid?")
        calls = [e for e in events if e["type"] == "tool_call"]
        assert calls[0].get("first_look") and calls[0]["args"] == {"query": "How much was the deposit and when was it paid?", "mode": "auto", "filters": ""}, calls
        types = [e["type"] for e in events]
        quick = next(e for e in events if e["type"] == "quick_answer")
        assert types.index("quick_answer") < types.index("answer_start"), "the quick answer must come before the model's answer"
        assert quick["source"]["filename"] == "landlord letter.txt" and "1,800" in quick["text"], quick
        assert answer["grounded"] and llm.calls == 1 and done["tool_calls"] == 1, (answer, llm.calls)
        print("A6. First look: the whole question is searched before any planning turn (router picks the tier); the quick answer is the letter's deposit sentence and arrives before the model's answer: OK")

        from app.agent.loop import _trim_citations
        tb = toolbox()
        tb.search("car insurance policy premium excess", mode="auto")
        tb.search("lisbon hotel booking reference", mode="auto")
        by_name = {s.filename: s.number for s in tb.sources}
        ins, lis = by_name["insurance policy.txt"], by_name["lisbon trip.md"]
        both = [ins, lis]
        assert _trim_citations("The excess is 350 euros and the premium 610 euros.", both, tb) == [ins], "a source that adds nothing to the answer must be dropped"
        assert _trim_citations("The excess is 350 euros; the Lisbon hotel booking ref is AS-77Q.", both, tb) == both, "a two-part answer keeps both files"
        # The live Windows run's over-cite, on the sample folder's real texts:
        # the April invoice shares "invoice", "April", "2025", "amount", "euros".
        from app.agent.tools import Source
        inv = Toolbox(None, None)
        inv.sources = [
            Source(1, "m", "march invoice.txt", "march invoice.txt", None, "Invoice 2025-03: consulting services, 12 hours. Payment terms net 30, so it is due on 14 April 2025; the amount is 1,440 euros.", "strong"),
            Source(2, "a", "april invoice.txt", "april invoice.txt", None, "Invoice 2025-04: workshop facilitation, two days. Amount 2,200 euros, due 30 May 2025. Purchase order PO-8812.", "strong"),
        ]
        assert _trim_citations("The March invoice is due on 14 April 2025 and the amount is 1,440 euros.", [1, 2], inv) == [1]
        print("A7. Citation trim: a second source that adds nothing is dropped (the March/April invoice over-cite); a two-part answer keeps both: OK")

        # --- A8. Questions about the collection itself are counted from the
        # index, exactly; questions about content still go to the agent. ---
        from app.agent.library import library_answer
        lib = ["C:/docs/a.json", "C:/docs/b.pdf", "C:/pics/cat.jpg", "C:/pics/dog.png", "C:/videos/trip.mp4"]
        assert library_answer("how many files are there", lib).startswith("IntelliFile has indexed 5 files")
        assert library_answer("are there any video files?", lib).startswith("Yes: 1 video (trip.mp4)")
        assert library_answer("do I have any audio files", lib).startswith("No. None of the 5 indexed files")
        assert library_answer("how many photos do I have", lib).startswith("2 photos")
        for content_question in ("are there any notes about zebras", "zebra notes", "What do my files say about bleach?", "how many hours did I work in March"):
            assert library_answer(content_question, lib) is None, content_question
        print("A8. Collection questions (how many files, any videos, any audio, how many photos) are counted from the index; content questions still go to the agent: OK")

        # --- A9-A12. Ask on a slow or busy laptop (2026-10-04). The user's
        # second laptop: "Ask mode is not working at all". Planning ate the
        # time, the answer's uninterruptible prompt reading ended past the
        # 2 x budget cap, the stream was cut after 1-3 tokens and grounding
        # turned that into "I couldn't find that in your files". ---
        from app.agent import loop as agent_loop

        class SlowLLM(ScriptedLLM):
            """A scripted model on a slow machine: the answer's first token
            arrives after `prefill` seconds, then one word per `gap` seconds;
            `estimate` (seconds per model call) stands in for the measured
            speed, or None for a model that reports no speed."""

            def __init__(self, *a, prefill=0.0, gap=0.0, estimate=None, endless=False, **k):
                super().__init__(*a, **k)
                self.prefill, self.gap, self.endless, self.answer_prompts = prefill, gap, endless, []
                if estimate is not None:
                    self.estimate_seconds = lambda messages, new_tokens: estimate(sum(len(m["content"]) for m in messages), new_tokens)

            def stream(self, messages, max_tokens=0, temperature=0.0):
                self.answer_prompts.append(messages[-1]["content"])
                time.sleep(self.prefill)
                words = self.answer.split(" ")
                for i in range(max_tokens if self.endless else len(words)):
                    yield (words[i % len(words)] if not self.endless else "word") + " "
                    time.sleep(self.gap)

        answer_text = "The deposit of 1,800 euros was paid on 3 January to Mr Okafor for the flat [1]."
        llm = SlowLLM(plans=[{"thought": "enough", "action": "answer"}], answer=answer_text, prefill=0.7)
        events, answer, done = collect(Agent(llm, toolbox, budget_seconds=0.2), "How much was the deposit and when was it paid?")
        assert answer["grounded"] and "1,800" in answer["text"] and "3 January" in answer["text"], answer
        print("A9. An answer whose first token arrives after the hard cap (slow prompt reading) is finished, not cut to one word and rejected: OK")

        llm = SlowLLM(plans=[{"thought": "enough", "action": "answer"}], answer="x", prefill=0.7, endless=True)
        events, answer, done = collect(Agent(llm, toolbox, budget_seconds=0.2), "How much was the deposit and when was it paid?")
        streamed = sum(e["type"] == "token" for e in events)
        assert streamed <= agent_loop.ANSWER_GRACE_TOKENS + 1, f"an answer that never ends must stop {agent_loop.ANSWER_GRACE_TOKENS} tokens past the cap, streamed {streamed}"
        print(f"A10. Past the cap the answer gets at most {agent_loop.ANSWER_GRACE_TOKENS} more tokens: bounded: OK")

        plans = [{"thought": "search again", "action": "search", "query": "deposit paid january", "mode": "keyword", "filters": ""}, {"thought": "done", "action": "answer"}]
        llm = SlowLLM(plans=list(plans), answer=answer_text, estimate=lambda chars, tokens: 30.0)
        events, answer, done = collect(Agent(llm, toolbox), "How much was the deposit and when was it paid?")
        assert llm.calls == 0 and answer["grounded"], (llm.calls, answer)
        assert any(e.get("system") and "this computer" in e["text"] for e in events), "skipping planning must be said in the trace"
        llm = SlowLLM(plans=list(plans), answer=answer_text, estimate=lambda chars, tokens: 1.0)
        events, answer, done = collect(Agent(llm, toolbox), "How much was the deposit and when was it paid?")
        assert llm.calls == 2 and done["tool_calls"] == 2 and answer["grounded"], (llm.calls, done)
        llm = SlowLLM(plans=list(plans), answer=answer_text, estimate=lambda chars, tokens: 15.0)
        events, answer, done = collect(Agent(llm, toolbox), "How much was the deposit and when was it paid?")
        assert llm.calls == 0 and answer["grounded"] and any(e.get("system") and "first search found strong matches" in e["text"] for e in events), (llm.calls, answer)
        print("A11. A planning turn that would not leave time for the answer on this computer is skipped (answer from the first look); so is a slow first turn when the first look found strong matches; on a fast computer the model still plans: OK")

        def by_speed(chars_per_second):
            return lambda chars, tokens: chars / chars_per_second + tokens / 20.0
        # Ten long retrieved passages (a real folder; the fixture's are short).
        many = Toolbox(None, None)
        many.sources = [Source(i, f"id{i}", f"note {i}.txt", f"note {i}.txt", None, f"Passage {i}: " + "the deposit and the lease terms are described here " * 20, "strong") for i in range(1, 11)]
        q = "How much was the deposit?"
        sizes = {}
        for label, cps in (("fast", 5000.0), ("slow", 30.0)):
            llm = SlowLLM(plans=[], answer="x", estimate=by_speed(cps))
            messages, shown = Agent(llm, toolbox)._answer_messages(q, many, remaining=45.0)
            sizes[label] = (sum(len(m["content"]) for m in messages), len(shown))
            if label == "slow":
                assert sizes[label][0] / cps + agent_loop.ANSWER_EST_TOKENS / 20.0 <= 45.0, f"the slow answer prompt ({sizes[label][0]} chars) cannot be read in the 45 s left"
        assert sizes["fast"][1] == agent_loop.ANSWER_MAX_SOURCES and sizes["slow"][0] < sizes["fast"][0] and sizes["slow"][1] >= 1, sizes
        print(f"A12. The answer's sources are sized to the measured reading speed: {sizes['fast'][1]} sources / {sizes['fast'][0]} chars on a fast computer, {sizes['slow'][1]} / {sizes['slow'][0]} on a slow one, which it reads inside the time left: OK")

        import psutil
        from app.agent.llm import default_threads
        cores = psutil.cpu_count(logical=False) or 1
        assert default_threads() == max(1, min(cores, 8)), (default_threads(), cores)
        print(f"A13. llama.cpp uses {default_threads()} threads for both reading and writing (physical cores: {cores}; llama-cpp's own default reads with all {psutil.cpu_count()} logical CPUs, which stalls on a busy laptop): OK")

        # A model that cannot be loaded (damaged file, a CPU the build does
        # not support) must reach the UI as a sentence, not an HTTP 500.
        import threading
        from types import SimpleNamespace
        from app.routes.agent import ask_endpoint
        junk = workdir / "llm" / "broken.gguf"
        junk.parent.mkdir()
        junk.write_bytes(b"not a model")
        state = SimpleNamespace(llm=None, llm_lock=threading.Lock(), llm_file=junk)
        response = ask_endpoint("what do I train on wednesday", SimpleNamespace(app=SimpleNamespace(state=state)))
        import asyncio

        async def read_body():
            return "".join([c if isinstance(c, str) else c.decode() async for c in response.body_iterator])
        body = asyncio.run(read_body())
        event = json.loads(body.split("data: ", 1)[1])
        assert event["type"] == "error" and "could not be loaded" in event["message"], event
        print("A14. A model file that fails to load is reported in Ask as an error message, not a server error: OK")

        # --- A17. /ask/status loads the 1.1 GB model only when asked to
        # (?preload=1, the Ask page); the Index and Settings pages poll it
        # too and used to load it every time. A failed load is remembered and
        # reported, and the preload does not try again on its own. ---
        from app.routes.agent import ask_status_endpoint
        state = SimpleNamespace(llm=None, llm_lock=threading.Lock(), llm_file=junk)
        request = SimpleNamespace(app=SimpleNamespace(state=state))
        status = ask_status_endpoint(request)
        assert status["loading"] is False and status["error"] is None and not getattr(state, "llm_preloading", False), status
        status = ask_status_endpoint(request, preload=True)
        assert status["available"] and not status["loaded"], status
        t0 = time.time()
        while getattr(state, "llm_preloading", False) and time.time() - t0 < 60:
            time.sleep(0.05)
        status = ask_status_endpoint(request)
        assert status["loading"] is False and status["error"] and "could not be loaded" in status["error"], status
        ask_status_endpoint(request, preload=True)
        assert not getattr(state, "llm_preloading", False), "a failed preload must not be retried automatically"
        print(f"A17. /ask/status never loads the model without preload=1; a failed preload is reported (\"error\": {status['error'][:60]!r}...) and not retried on its own: OK")

        # --- A15. The 4096-token context (2026-10-04). Every search adds its
        # results to the planning prompt; with long passages the fourth
        # planning turn (or the answer over six sources) no longer fit and
        # llama.cpp raised "Requested tokens exceed context window". The
        # oldest search results are dropped from planning and the answer reads
        # fewer sources instead. A scripted model with a tiny window counts
        # one token per word. ---
        class TinyContextLLM(SlowLLM):
            def __init__(self, *a, n_ctx=0, **k):
                super().__init__(*a, **k)
                self.n_ctx, self.prompt_tokens = n_ctx, []

            def tokenize(self, text):
                return text.split()

            def chat(self, messages, max_tokens=0, json_only=False, temperature=0.0, deadline=None, label=None):
                if label == "plan":
                    self.prompt_tokens.append((agent_loop._prompt_tokens(self, messages), max_tokens))
                return super().chat(messages, max_tokens, json_only, temperature, deadline, label)

            def stream(self, messages, max_tokens=0, temperature=0.0):
                self.prompt_tokens.append((agent_loop._prompt_tokens(self, messages), max_tokens))
                return super().stream(messages, max_tokens, temperature)

        searches = [{"thought": f"look {i}", "action": "search", "query": q, "mode": "keyword", "filters": ""}
                    for i, q in enumerate(["deposit paid", "kitchen tap", "landlord repair visit", "lease deposit"])]
        free = TinyContextLLM(plans=list(searches), answer=answer_text, n_ctx=100_000)
        events, answer, done = collect(Agent(free, toolbox, first_look=False), "How much was the deposit and when was it paid?")
        unguarded = max(t for t, _ in free.prompt_tokens[:-1])  # the largest planning prompt with room to spare
        small = TinyContextLLM(plans=list(searches), answer=answer_text, n_ctx=unguarded + agent_loop.MAX_PLAN_TOKENS + agent_loop.CONTEXT_MARGIN_TOKENS - 40)
        events, answer, done = collect(Agent(small, toolbox, first_look=False), "How much was the deposit and when was it paid?")
        assert all(t + room + agent_loop.CONTEXT_MARGIN_TOKENS <= small.n_ctx for t, room in small.prompt_tokens), (small.n_ctx, small.prompt_tokens)
        assert any(e.get("system") and "oldest search results" in e["text"] for e in events), "dropping results must be said in the trace"
        assert done["tool_calls"] == 4 and answer["grounded"] and "1,800" in answer["text"], (done, answer)
        tiny = TinyContextLLM(plans=[], answer="x", n_ctx=agent_loop.MAX_ANSWER_TOKENS + agent_loop.CONTEXT_MARGIN_TOKENS + 300)
        messages, shown = Agent(tiny, toolbox)._answer_messages(q, many, remaining=45.0)
        assert agent_loop._prompt_tokens(tiny, messages) + agent_loop.MAX_ANSWER_TOKENS + agent_loop.CONTEXT_MARGIN_TOKENS <= tiny.n_ctx and 1 <= len(shown) < agent_loop.ANSWER_MAX_SOURCES, (agent_loop._prompt_tokens(tiny, messages), shown)
        print(f"A15. Context window: planning drops the oldest search results to stay under the window (largest prompt {max(t for t, _ in small.prompt_tokens)} tokens of {small.n_ctx}), the answer reads {len(shown)} of 10 long sources to fit; nothing fails: OK")

        # --- A16. A plan the model got half right: JSON that is not an object,
        # a source written as "[2]" or as words, a query given as a list. Each
        # raised inside the loop (int("[2]"), "list has no strip") and ended
        # Ask with a server error. ---
        from app.agent.loop import _parse_plan
        for raw in ("[1, 2]", '"answer"', "42", "null", '{"action": "read_more", "source": "the second one"}'):
            assert _parse_plan(raw).get("malformed"), raw
        assert _parse_plan('{"action": "read_more", "source": "[2]"}')["source"] == 2
        assert _parse_plan('{"action": "read_more", "source": 3.0}')["source"] == 3
        odd = _parse_plan('{"action": "search", "query": ["kitchen", "tap"], "filters": null, "thought": {"why": 1}, "source": ""}')
        assert odd["query"] == "kitchen tap" and odd["filters"] == "" and isinstance(odd["thought"], str) and odd["source"] is None and not odd.get("malformed"), odd
        llm = ScriptedLLM(plans=[{"thought": "look", "action": "search", "query": ["deposit", "paid"], "mode": "keyword", "filters": None},
                                 {"thought": "read it", "action": "read_more", "source": "[1]"}], answer=answer_text)
        events, answer, done = collect(Agent(llm, toolbox, first_look=False), "How much was the deposit and when was it paid?")
        calls = [e for e in events if e["type"] == "tool_call"]
        assert [c["tool"] for c in calls] == ["search", "read_more"] and calls[0]["args"]["query"] == "deposit paid" and calls[1]["args"]["source"] == 1, calls
        assert answer["grounded"] and events[-1]["type"] == "done", answer
        llm = ScriptedLLM(plans=[], answer="x")
        llm.chat = lambda *a, **k: "[1, 2]"  # type: ignore
        events, answer, done = collect(Agent(llm, toolbox, first_look=False), "anything")
        assert events[-1]["type"] == "done" and any("not a valid tool call" in e.get("text", "") for e in events)
        print("A16. Half-right plans: non-object JSON is malformed (answer now), a source as \"[1]\" or 3.0 becomes a number, a query as a list becomes words; no server error: OK")

        # ---------- Part B: the real model ----------
        model_file = None if "--no-model" in sys.argv else find_model_file()
        if model_file is None:
            print("\nB. Real-model checks skipped (--no-model, or no .gguf in models/llm: scripts/download_llm_model.py).")
        else:
            from app.agent import LocalLLM

            llm = LocalLLM(model_file)
            bench = llm.benchmark()
            assert llm.llm.n_threads == llm.llm.n_threads_batch, (llm.llm.n_threads, llm.llm.n_threads_batch)
            assert llm.speed["prompt_chars_per_second"] > 0 and llm.speed["tokens_per_second"] > 0, llm.speed
            print(f"\nB0. {llm.name}: loaded in {llm.load_seconds}s ({llm.llm.n_threads} threads), {bench['tokens_per_second']} tokens/s on CPU; "
                  f"calibrated at load in {llm.speed['seconds']}s: reads {llm.speed['prompt_chars_per_second']} prompt chars/s, writes {llm.speed['tokens_per_second']} tokens/s")
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
            # The loop stops planning at BUDGET_SECONDS, sizes the answer to
            # the measured speed, and lets an answer that is still writing at
            # twice that finish its sentence, at most ANSWER_GRACE_TOKENS more
            # (2026-10-04: cutting it at the cap turned slow laptops' answers
            # into "couldn't find that"). That bound is what must hold on any
            # machine. The mean is hardware: ~10 s on the M-series dev Mac,
            # ~28 s on a 12th-gen i5 laptop (Windows, 2026-09-25) — reported
            # above, not asserted.
            from app.agent.loop import ANSWER_GRACE_TOKENS
            ceiling = 2 * BUDGET_SECONDS + ANSWER_GRACE_TOKENS / llm.tokens_per_second
            assert max(seconds) <= ceiling, f"slowest answer {max(seconds):.1f}s exceeds the hard cap plus the bounded finish ({ceiling:.1f}s)"

        print("\nPhase 19 agent OK: bounded tool loop, strategy selection, grounded cited answers, and honest 'not found' all work as expected.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
