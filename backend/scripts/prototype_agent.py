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

        # --- A8b. The same questions however they are worded (2026-10-13, from
        # real use on a 2,019-file index): "how many movies are theere in the
        # laptop" missed the anchored patterns (the typo, "in the laptop"),
        # went to the model and came back as "28 files ... according to the
        # TEST report". Typos, no question mark, any location phrase. ---
        videos = [f"C:/movies/{n}" for n in ("a.mp4", "b.mkv", "c.mov")] + [f"C:/movies/d{i}.mp4" for i in range(1, 10)]
        quizzes = ["C:/school/Quiz1.pdf", "C:/school/quizzes/week2.docx", "C:/school/math quiz.txt", "C:/school/Quizlet export.csv"]
        big = [f"C:/docs/report {i}.pdf" for i in range(2000)] + videos + quizzes + ["C:/music/song.mp3", "C:/work/invoices/march.txt", "C:/work/april invoice.txt"]
        assert len(big) == 2019
        movies = library_answer("how many movies are theere in the laptop", big)
        assert movies == "12 videos (a.mp4, b.mkv, c.mov and others), out of 2,019 indexed files.", movies
        assert library_answer("how many files are theere in the laptop", big).startswith("IntelliFile has indexed 2,019 files: ")
        for wording in ("hw many vidoes do i have", "number of videos", "count the videos", "count of movies", "total videos in my system", "how many movies on the computer",
                        "how many video files in this pc", "how many movies here", "how many videos in my files", "how many movies have i got", "How many MOVIES are there ??",
                        "what is the total number of videos in my laptop", "how many moives are there"):
            assert library_answer(wording, big) == movies, wording
        assert library_answer("number of pdfs", big).startswith("2,001 PDFs (report 0.pdf, "), library_answer("number of pdfs", big)
        for wording in ("any songs on this pc?", "is there any music in my laptop", "do i have songs", "have i got any songs here"):
            assert library_answer(wording, big) == "Yes: 1 audio file (song.mp3), out of 2,019 indexed files.", wording
        assert library_answer("how many photos are there in the laptop", big) == "None of the 2,019 indexed files is a photo."
        listed = library_answer("list my videos", big)
        assert listed == "12 videos, out of 2,019 indexed files: a.mp4, b.mkv, c.mov, d1.mp4, d2.mp4, d3.mp4, d4.mp4, d5.mp4, d6.mp4, d7.mp4 and 2 others.", listed
        which = library_answer("which pdfs do i have", big)
        assert which.startswith("2,001 PDFs, out of 2,019 indexed files: report 0.pdf, ") and which.endswith(" and 1,991 others."), which
        # "What ARE the movies I have" lists, same as "which movies do i have" (2026-10-14,
        # from real use): the "aare"/"latop" typos and the plural copula before the subject
        # ("what ARE the X", not "what X do i have") made it fall through to the model, which
        # answered from an unrelated file instead of the real video files. "what IS a pdf"
        # (singular, a definition) must still go to the model, unaffected.
        for wording in ("what are the movies i have", "what aare the movies i have", "what aare the movies i have in my latop", "which movies do i have"):
            assert library_answer(wording, big) == listed, wording
        assert library_answer("what is a pdf", big) is None, "a definition, not a list"
        assert library_answer("show all spreadsheets", big) == "1 spreadsheet, out of 2,019 indexed files: Quizlet export.csv."
        # A subject that is not a kind of file is counted in file and folder
        # names, and the answer says that is all it counted.
        quiz = library_answer("how many quizs are there in the laptop", big)
        assert quiz == ('4 files have "quiz" in their name or folder name (Quiz1.pdf, week2.docx, math quiz.txt and 1 other), out of 2,019 indexed files. '
                        "This counts names, not what is written inside the files. Search looks inside."), quiz
        for wording in ("how many quizzes do i have", "how many quiz files are there", "number of quizs in my pc"):
            assert library_answer(wording, big) == quiz, wording
        assert library_answer("how many invoices do i have", big).startswith('2 files have "invoice" in their name or folder name (march.txt, april invoice.txt), out of 2,019 indexed files. This counts names')
        nothing = library_answer("how many zebras are there in the laptop", big)
        assert nothing == 'No indexed file has "zebra" in its name or folder name (2,019 files checked). This counts names, not what is written inside the files. Search looks inside.', nothing
        import evaluate_agent
        content_questions = ["how many euros was the march invoice", "how many pages is the lease", "how many days until the dentist appointment", "are there any notes about zebras",
                             "what is the wifi password", "when is the march invoice due, and how much is it", "how many miles did I run in March", "what is the invoice total",
                             "how may i find my files", "which band do I use for the clamshell exercise", "list the steps for the sourdough", "where is my resume.docx", "any news about the lease?",
                             "too many files are in the lease folder, which one is signed"]
        content_questions += [q for q, _ in evaluate_agent.ANSWERABLE] + evaluate_agent.UNANSWERABLE + [q for q, _ in QUESTIONS]
        for content_question in content_questions:
            assert library_answer(content_question, big) is None, (content_question, library_answer(content_question, big))
        print(f"A8b. Collection questions however worded (typos, 'in the laptop', 'number of', 'count the', list, any) are answered from the index: {movies!r}; "
              f"a subject that is not a file kind is counted in names and says so: {quiz!r}; {len(content_questions)} content questions (the whole Ask benchmark among them) still go to the agent: OK")

        # --- A8c. A count must be about what was asked (2026-10-13). The same
        # question, once it reached the model, was answered "There are 28 files
        # listed ..." from a screenshot that never mentions movies. The word
        # rule only looked at question words the answer repeats, and the answer
        # had swapped the subject, so "movies" was never checked. ---
        from app.agent.loop import _unsupported_premise
        shot = Source(1, "s", "TEST report.png", "TEST report.png", None, "TEST report. Index status: 28 files listed in the laptop's index, 0 failed.", "strong")
        yes = lambda word, text: True  # noqa: E731 (a judge that agrees with everything)
        wrong_subject = _unsupported_premise("how many movies are theere in the laptop", "There are 28 files listed in the laptop's index, according to the TEST report", [shot], judge=yes)
        assert wrong_subject and "movies" in wrong_subject, wrong_subject
        assert _unsupported_premise("number of movies in the laptop", "28 files are listed in the index", [shot], judge=None), "the count rule needs no judge"
        assert _unsupported_premise("how many files are listed in the index", "28 files are listed in the laptop's index", [shot], judge=yes) is None
        film = Source(2, "f", "watchlist.txt", "watchlist.txt", None, "Watchlist: 14 movie nights planned, one movie each.", "strong")
        assert _unsupported_premise("how many movies are on the watchlist", "14 are planned", [film], judge=yes) is None, "a close form of the word (movie) counts as a mention"
        assert _unsupported_premise("how many movies are on the watchlist", "28 files are listed", [shot], judge=yes, retrieved=[film]) is None, "a strong retrieved source that names the subject counts"
        llm = ScriptedLLM(plans=[{"thought": "look", "action": "search", "query": "sourdough starter flour", "mode": "auto", "filters": ""}], answer="The dough ferments for five hours at 24 C [1].")
        events, answer, done = collect(Agent(llm, toolbox, first_look=False), "how many movies does the sourdough need")
        assert not answer["grounded"] and answer["text"] == "I couldn't find that in your files." and any("movies" in e.get("text", "") for e in events if e.get("system")), answer
        llm = ScriptedLLM(plans=[{"thought": "look", "action": "search", "query": "sourdough bulk ferment hours", "mode": "auto", "filters": ""}], answer="The dough ferments for five hours at 24 C [1].")
        events, answer, done = collect(Agent(llm, toolbox, first_look=False), "how many hours does the sourdough ferment")
        assert answer["grounded"] and answer["citations"][0]["filename"] == "sourdough.md", answer
        print("A8c. A 'how many <thing>' answer drawn from sources that never mention <thing> (28 'files' for 'movies') is 'couldn't find'; a close form of the word or a real subject still answers: OK")

        # --- A8d. "Why is this file missing": answered from what the engine
        # already knows, the first reason that applies, never by the model. ---
        from app.agent.missing import Known, missing_answer, why_missing
        known = Known(
            active=["C:/docs/Annual Report.pdf", "C:/docs/budget.xlsx", "C:/a/notes.txt", "C:/b/notes.txt", "C:/docs/march_invoice.txt"],
            failures=[{"path": "C:/docs/locked.pdf", "reason": "The file is password-protected."}],
            skips=[{"path": "C:/video/holiday.mp4", "reason": "larger than 50 MB, the limit for .pdf files"}, {"path": "C:/cloud/thesis.docx", "reason": "online-only OneDrive file; make it available offline to index it"}],
            deleted=[{"path": "C:/old/resume.docx"}, {"path": "C:/old/cv.txt", "moved_to": "C:/docs/cv 2026.txt"}],
            folders=[f"C:/folder{i}" for i in range(1, 8)],
        )
        def status(name, k=known):
            return why_missing(name, k)["status"]
        got = why_missing("annual report.PDF", known)
        assert got == {"name": "annual report.PDF", "status": "indexed", "reason": '"Annual Report.pdf" is indexed, so it is not missing. It is in C:/docs.', "path": "C:/docs/Annual Report.pdf"}, got
        assert why_missing("annual report", known)["path"] == "C:/docs/Annual Report.pdf" and why_missing("anual report", known)["path"] == "C:/docs/Annual Report.pdf", "without the extension, and one letter off"
        assert why_missing("march invoice", known)["path"] == "C:/docs/march_invoice.txt" and why_missing("C:\\docs\\BUDGET.xlsx", known)["status"] == "indexed"
        assert why_missing("notes.txt", known)["reason"] == "2 indexed files have that name, so it is not missing: C:/a/notes.txt, C:/b/notes.txt." and status("notes") == "indexed"
        assert status("motes.txt") == "unknown", "a typo that fits two known files is not guessed"
        got = why_missing("locked.pdf", known)
        assert got["status"] == "failed" and got["reason"] == '"locked.pdf" was found but could not be read: The file is password-protected.' and got["path"] == "C:/docs/locked.pdf", got
        got = why_missing("holiday", known)
        assert got["status"] == "skipped" and "larger than 50 MB" in got["reason"], got
        assert "online-only OneDrive file" in why_missing("thesis.docx", known)["reason"]
        got = why_missing("resume.docx", known)
        assert got["status"] == "deleted" and "deleted, moved or renamed" in got["reason"] and "in this run of the app" in got["reason"], got
        assert why_missing("cv.txt", known)["reason"].endswith("It was in C:/old. It is now indexed as C:/docs/cv 2026.txt."), why_missing("cv.txt", known)
        got = why_missing("setup.exe", known)
        assert got["status"] == "unsupported" and ".exe" in got["reason"] and "path" not in got, got
        busy = Known(active=known.active, folders=known.folders, indexing=True, waiting=1234)
        got = why_missing("holiday.mp4", busy)
        assert got["status"] == "indexing" and "1,234 files" in got["reason"], got
        assert why_missing("holiday.mp4", Known(active=[], indexing=True))["reason"].startswith("Indexing is still running, so")
        assert why_missing("draft.docx", Known(active=[], unfinished=["C:/docs/draft.docx"], indexing=True))["path"] == "C:/docs/draft.docx"
        got = why_missing("holiday photos.docx", Known(active=known.active, folders=known.folders))
        assert got["status"] == "unknown" and "C:/folder5" in got["reason"] and "C:/folder6" not in got["reason"] and "2 more" in got["reason"] and "spelled differently" in got["reason"], got
        assert "no folder" in why_missing("x.pdf", Known(active=[])).get("reason").lower()
        assert status("") == "invalid" and status("   ") == "invalid" and status("a\x00b") == "invalid" and status("x" * 500) == "invalid"
        # A full path: checked on disk (read-only) only inside an indexed folder.
        real = workdir / "why"
        (real / "node_modules").mkdir(parents=True)
        (real / "node_modules" / "readme.txt").write_text("x")
        (real / "fresh.txt").write_text("x")
        on_disk = Known(active=[], folders=[str(real)])
        assert status(str(real / "node_modules" / "readme.txt"), on_disk) == "skipped" and status(str(real / "fresh.txt"), on_disk) == "not_indexed_yet"
        assert status(str(real / "gone.txt"), on_disk) == "not_on_disk" and status(str(workdir / "elsewhere" / "a.txt"), on_disk) == "outside_folders"
        # Ask: these wordings get that answer through the from_index path.
        for wording, part in (("why is Annual Report.pdf missing", "is indexed, so it is not missing"), ("why can't i find budget.xlsx", '"budget.xlsx" is indexed'),
                              ("where is my resume.docx", "deleted, moved or renamed"), ("why is march invoice not showing", '"march_invoice.txt" is indexed'),
                              ("why isn't locked.pdf indexed?", "password-protected"), ("why does holiday.mp4 not show up", "larger than 50 MB"),
                              ("where is Annual Report.pdf", "It is in C:/docs"), ("Why is setup.exe not in the results?", "does not read .exe files")):
            got = missing_answer(wording, known)
            assert got and part in got, (wording, got)
        for wording in ("where is the lisbon hotel", "where is the dentist appointment", "why is the sky blue", "why can't i find anything about zebras", "what is missing from the lease",
                        "where is the annual report", "why is the march invoice due in april", *(q for q in content_questions if "resume.docx" not in q)):
            assert missing_answer(wording, known) is None, (wording, missing_answer(wording, known))
        agent = Agent(ScriptedLLM(plans=[], answer="x"), toolbox, known=lambda: known)
        events, answer, done = collect(agent, "why is locked.pdf missing")
        assert answer["from_index"] and answer["citations"] == [] and "password-protected" in answer["text"] and agent.llm.calls == 0 and done["tool_calls"] == 0, answer
        events, answer, done = collect(Agent(ScriptedLLM(plans=[], answer="x"), toolbox), "where is sourdough.md")
        assert answer.get("from_index") and "is indexed, so it is not missing" in answer["text"], answer
        print("A8d. Why is a file missing: indexed / failed / skipped / deleted / unsupported type / still indexing / unknown (with the indexed folders), matched without the extension, "
              "any case, one letter off when only one file fits; a full path is checked on disk only inside an indexed folder; Ask answers these wordings from the index with no model call, "
              "and 'where is <not a file>' still goes to the agent: OK")

        # --- A8d2. "What files were deleted in the last 24 hours": a list
        # question, not one named file; answered from the deletion log,
        # which outlives the tombstone purge that clears Known.deleted. ---
        from app.agent.missing import deleted_recently_answer, index_answer
        now = time.time()
        log_known = Known(active=[], deleted_log=[
            {"path": "C:/docs/old_report.pdf", "deleted_at": now - 3600},
            {"path": "C:/docs/draft.docx", "deleted_at": now - 23 * 3600},
            {"path": "C:/docs/ancient.txt", "deleted_at": now - 10 * 86400},
        ])
        for wording in ("what are previous files that are deleted in the last 24 hours", "what files were deleted in the last 24 hours",
                        "which files have been deleted in the last 24 hours", "what files did i delete in the last 24 hours", "show deleted files from the last 24 hours",
                        "list removed files from the last 24 hours", "what did i delete in the last 24 hours",
                        "how many files are deleted in the last 24 hours", "how many files were deleted in last 24 hours", "count of files deleted in the last 24 hours",
                        "are there any files deleted in the last 24 hours", "so how many files got deleted in the last 24 hours"):
            got = deleted_recently_answer(wording, log_known, now)
            assert got and "old_report.pdf" in got and "draft.docx" in got and "ancient.txt" not in got and "2 files deleted" in got, (wording, got)
        assert deleted_recently_answer("what files were deleted today", Known(active=[], deleted_log=[]), now) == "No files were deleted today."
        assert deleted_recently_answer("how many pdfs do i have", log_known, now) is None, "a collection question, not a deletion list"
        assert deleted_recently_answer("why is report.pdf missing", log_known, now) is None, "a single named file, not a list"
        # Cleanup advice ("what should/can I delete") asks what to do, not what already
        # happened: found by review 2026-10-14, it was wrongly answered "no files were deleted".
        for advice in ("what should i delete", "what can i delete to free up space", "which files should i remove",
                       "how many files should be deleted", "what could i delete"):
            assert deleted_recently_answer(advice, log_known, now) is None, advice
        assert index_answer("what files were deleted in the last 24 hours", log_known, now) == deleted_recently_answer("what files were deleted in the last 24 hours", log_known, now)
        # "Yesterday" has an upper bound too (today's start): found by review 2026-10-14, a file
        # deleted today was wrongly counted as "deleted yesterday" when only the lower bound was applied.
        from datetime import datetime
        fixed_now = datetime(2026, 10, 14, 15, 0, 0).timestamp()
        today_start = datetime(2026, 10, 14, 0, 0, 0).timestamp()
        yesterday_start = datetime(2026, 10, 13, 0, 0, 0).timestamp()
        dated_known = Known(active=[], deleted_log=[
            {"path": "C:/docs/today_file.png", "deleted_at": today_start + 3600},
            {"path": "C:/docs/yesterday_file.png", "deleted_at": yesterday_start + 3600},
            {"path": "C:/docs/two_days_ago.png", "deleted_at": yesterday_start - 3600},
        ])
        got = deleted_recently_answer("how many files were deleted yesterday", dated_known, fixed_now)
        assert got and "1 file deleted yesterday" in got and "yesterday_file.png" in got and "today_file.png" not in got and "two_days_ago.png" not in got, got
        # "Oldest" must sort ascending and respect the parsed count, not always newest-first/capped-at-10.
        order_known = Known(active=[], deleted_log=[
            {"path": "C:/docs/newest.png", "deleted_at": now - 3600},
            {"path": "C:/docs/middle.png", "deleted_at": now - 7200},
            {"path": "C:/docs/oldest.png", "deleted_at": now - 10800},
        ])
        got = deleted_recently_answer("what are the 2 oldest files deleted", order_known, now)
        assert got and "newest.png" not in got and got.index("oldest.png") < got.index("middle.png"), got
        print("A8d2. What files were deleted recently: answered from the persisted deletion log, with the usual time windows (last N hours/days, today, yesterday — with its upper bound "
              "applied too) and oldest/newest ordering, as a list rather than a single-file lookup, and left to the normal Ask flow when it's a collection or single-file question instead: OK")

        # --- A8e. QA round 1 (2026-10-13), on an index of 20 files: 14
        # documents (8 .txt), 4 photos (3 .png, no .jpg), 1 video, 1 audio
        # (.wav). Every input below gave a wrong or misleading answer. ---
        docs = [f"D:/qa/documents/{n}" for n in ("test report.txt", "quiz 1.txt", "march invoice.txt", "april invoice.txt", "notes a.txt", "notes b.txt", "sourdough.txt", "roadmap.txt",
                                                 "turbine manual.pdf", "cake recipe.docx", "watchlist.md", "budget.csv", "roadmap.pptx")]
        qa = docs + ["D:/qa/other/readme.md", "D:/qa/photos/red circle.png", "D:/qa/photos/blue square.png", "D:/qa/photos/Screenshot 2026.png", "D:/qa/photos/old.bmp",
                     "D:/qa/media/clip.mp4", "D:/qa/media/meeting note.wav"]
        qa_known = Known(active=qa, folders=["D:/qa", "E:/more"], deleted=[{"path": "D:/qa/documents/quiz 2.txt", "moved_to": "D:/qa/documents/quiz two.txt"}],
                         failures=[{"path": "D:/qa/documents/locked.pdf", "reason": "The file is password-protected."}, {"path": "D:/qa/documents/torn.docx", "reason": "The file is damaged or not a valid Word file."}],
                         skips=[{"path": "D:/qa/media/film.mkv", "reason": "larger than 50 MB, the limit for .pdf files"}])
        def said(question, k=qa_known):
            return index_answer(question, k)
        # 1. What IntelliFile did with the files is still a question about the collection.
        everything = "IntelliFile has indexed 20 files: 14 documents, 4 photos, 1 video and 1 audio file. The most common types are 8 TXT, 3 PNG, 2 MD, 1 PDF."
        for wording in ("how many files did i index", "how many files did intellifile find", "how many files have you indexed so far", "how many files does intellifile know",
                        "how many files did you scan", "how many files are in the index right now", "how many file", "list files", "how many files has it read"):
            assert said(wording) == everything, (wording, said(wording))
        assert said("how many photos has it found") == "4 photos (red circle.png, blue square.png, Screenshot 2026.png and 1 other), out of 20 indexed files."
        assert said("how many documents are indexed in total").startswith("14 documents (test report.txt, quiz 1.txt, march invoice.txt and others), out of 20")
        # 3. A file-type word counts that type; a folder counts what is inside it.
        assert said("how many png files are there") == "3 PNG files (red circle.png, blue square.png, Screenshot 2026.png), out of 20 indexed files."
        assert said("how many jpg files do i have") == "None of the 20 indexed files is a JPG file." and said("how many mp3 are there") == "None of the 20 indexed files is an MP3 file."
        assert said("how many mp4 files are there") == "1 MP4 file (clip.mp4), out of 20 indexed files." and said("how many csv files are there") == "1 CSV file (budget.csv), out of 20 indexed files."
        for wording in ("how many text files are there", "how many txt files are there", "how many .txt files do i have", "number of txt"):
            assert said(wording) == "8 TXT files (test report.txt, quiz 1.txt, march invoice.txt and others), out of 20 indexed files.", (wording, said(wording))
        assert said("how many word documents are there") == "1 Word document (cake recipe.docx), out of 20 indexed files." and said("how many docx files are there").startswith("1 DOCX file (cake recipe.docx)")
        assert said("how many md files are there").startswith("2 MD files (watchlist.md, readme.md)") and said("how many wavs do i have").startswith("1 WAV file (meeting note.wav)")
        assert said("how many screenshots do i have") == ('1 file has "screenshot" in its name or folder name (Screenshot 2026.png), out of 20 indexed files. '
                                                          "This counts names, not what is written inside the files. Search looks inside."), said("how many screenshots do i have")
        assert said("how many files are there in the documents folder") == '13 files are in the "documents" folder (test report.txt, quiz 1.txt, march invoice.txt and others), out of 20 indexed files.'
        assert said("how many pdfs are in the documents folder") == '1 PDF in the "documents" folder (turbine manual.pdf), out of 20 indexed files.'
        assert said("how many pngs are inside photos") == '3 PNG files in the "photos" folder (red circle.png, blue square.png, Screenshot 2026.png), out of 20 indexed files.'
        assert said("how many videos are in the photos folder") == 'None of the 4 files in the "photos" folder is a video.'
        assert said("how many invoices are in the documents folder").startswith('2 files in the "documents" folder have "invoice" in their name or folder name (march invoice.txt, april invoice.txt)')
        assert said("how many files in the holiday folder") == 'No indexed folder is named "holiday" (20 files checked).'
        assert said("how many songs in the media folder") == '1 audio file in the "media" folder (meeting note.wav), out of 20 indexed files.'
        # 6. Two kinds at once, the folders, and what failed or was left out.
        assert said("how many videos and photos do i have") == "1 video (clip.mp4) and 4 photos (red circle.png, blue square.png, Screenshot 2026.png and 1 other), out of 20 indexed files."
        assert said("how many folders are there") == "IntelliFile searches 2 folders: D:/qa, E:/more." and said("which folders are indexed") == said("how many folders are there")
        assert said("how many folders are there", Known(active=[])) == "No folder has been added yet. Add one on the Index page."
        failed = "2 files could not be read in this run of the app: locked.pdf (The file is password-protected), torn.docx (The file is damaged or not a valid Word file). The Index page lists them with the reasons."
        assert said("how many files failed") == failed and said("how many files could not be read") == failed, said("how many files failed")
        assert said("how many files were skipped") == "1 file was left out on purpose in this run of the app: film.mkv (larger than 50 MB, the limit for .pdf files). The Index page lists them with the reasons."
        assert said("how many files failed", Known(active=qa)) == "No file has failed to be read in this run of the app. The Index page lists any that are."
        assert said("how many pdf, docx and png files are there").startswith("1 PDF (turbine manual.pdf), 1 DOCX file (cake recipe.docx) and 3 PNG files (")
        assert said("why are my files not showing up").startswith("20 files are indexed (test report.txt, ") and said("how many euros are inside the march invoice") is None
        # 7. One file is "its", one more is "1 other".
        assert said("how many screenshots do i have").startswith('1 file has "screenshot" in its name') and "and 1 other)" in said("how many photos are there")
        # 2. Another number is another file, never a typo; and 4. deleted is remembered for this run.
        for name in ("quiz 3.txt", "quiz 9", "IMG_0002.png"):
            got = why_missing(name, Known(active=qa + ["D:/qa/photos/IMG_0001.png"], folders=["D:/qa"]))
            assert got["status"] in ("unknown", "similar") and "path" not in got and "is indexed, so" not in got["reason"], (name, got)
        got = why_missing("quiz 2.txt", qa_known)
        assert got["status"] == "deleted" and got["reason"] == ('"quiz 2.txt" was in the index earlier in this run of the app, but it has since been deleted, moved or renamed, so it was taken out. '
                                                              "It was in D:/qa/documents. It is now indexed as D:/qa/documents/quiz two.txt."), got
        assert why_missing("turbin manual", qa_known)["path"] == "D:/qa/documents/turbine manual.pdf" and why_missing("roadmaps.pptx", qa_known)["path"] == "D:/qa/documents/roadmap.pptx"
        assert why_missing("quiz", qa_known)["status"] == "similar", "four letters: too short for a typo, but a name contains it"
        # 5. Part of a name, and a kind of file instead of a name.
        assert said("why is the invoice missing") == 'No indexed file is named exactly "invoice". These indexed names are close: march invoice.txt, april invoice.txt.', said("why is the invoice missing")
        assert why_missing("invoice", qa_known)["status"] == "similar" and "path" not in why_missing("invoice", qa_known)
        assert said("why are my photos not showing up") == ("4 photos are indexed (red circle.png, blue square.png, Screenshot 2026.png and 1 other), out of 20 indexed files. "
                                                            "If one is missing, ask about it by its name."), said("why are my photos not showing up")
        no_photos = Known(active=docs, folders=["D:/qa"], extensions=frozenset({".txt", ".pdf", ".mp3"}))
        assert said("why are my photos not showing up", no_photos) == ("No photos are indexed. The likely reason: photo and video search is off on this computer (its model folder is missing). "
                                                                       "IntelliFile searches: D:/qa."), said("why are my photos not showing up", no_photos)
        assert said("why are my mp3 files not showing", Known(active=docs, folders=["D:/qa"])) == "No MP3 files are indexed. The likely reason: the folder they are in is not one IntelliFile searches. IntelliFile searches: D:/qa."
        # 7. Not a name at all.
        for name in ("...", ".", "*.txt", "report?.pdf", "***", '"', "<>"):
            assert why_missing(name, qa_known)["status"] == "invalid", (name, why_missing(name, qa_known))
        # None of it takes a question about content away from the agent.
        qa_content = ["how many hours of consulting are on the march invoice", "list the ingredients for sourdough bread", "how many documentaries are on the watchlist",
                      "how many euros is the march invoice", "are there any notes about horizontal scaling", "where is the quarterly planning meeting", "what is the zephyr turbine maintenance schedule",
                      "when is the march invoice due", "are there any files about lemon drizzle cake", "list the milestones in the roadmap", "where is the load balancer mentioned",
                      "how many checks passed in the test report", "is there a recipe with poppy seeds", "which document mentions payment terms", "how many files are in the index according to the test report",
                      "show me the invoice from march", "do i have a file about hydroponic basil", "any notes on sourdough", "how many photos are there of the red circle", "where is my recipe for sourdough",
                      "why can't i find anything about turbines", "where is the capital of Portugal mentioned", "list everything about glaciers", "how many degrees does water boil at",
                      "what is the total of the march invoice",
                      "what is a pdf", "what is a spreadsheet", "how many files are mentioned in the report", "how many photos did we take in lisbon", "where is the dentist",
                      "what does the pdf contain", "which files mention the deposit", "how many files did i send to the accountant", "what is an mp3", "how many checks failed in the test report",
                      "what type of document is the lease", "is the roadmap in the documents folder up to date", "how many photos were taken inside the cathedral"]
        for content_question in qa_content + [q for q in content_questions if "resume.docx" not in q]:
            assert library_answer(content_question, qa) is None and missing_answer(content_question, qa_known) is None, (content_question, said(content_question))
        print(f"A8e. QA round 1: what IntelliFile indexed/found/scanned is counted from the index ({everything[:34]}...); a type word counts that type and a folder what is inside it; two kinds, the folders, "
              f"failed and skipped files are answered; another number is never a typo; deleted is known for this run; part of a name lists the names that contain it; "
              f"{len(qa_content)} more content questions still go to the agent: OK")

        # --- A8f. QA round 2 (2026-10-14): eight more wordings of "how many
        # files are indexed" still reached the model, which answered "60
        # files" from a report. The general rule: a count of files (or of a
        # kind of file) that points at the index, the app or the computer is
        # about the collection, whatever verb it uses. ---
        from app.agent.library import counts_files_from_text
        for q in ["how many files got indexed in the end", "so how many files did it index", "how many files are in my index at the moment", "how many files went into the index",
                  "can you tell me the number of files in the index", "how many files are listed in the index", "how many files can you search", "tell me how many files are indexed",
                  "how many files has intellifile picked up", "ok so how many files are on this laptop", "do you know how many files are in the index", "how many files are still in the index"]:
            assert library_answer(q, qa) == everything, (q, library_answer(q, qa))
        assert library_answer("how many photos went into the index", qa).startswith("4 photos"), library_answer("how many photos went into the index", qa)
        assert library_answer("so how many videos did it index", qa).startswith("1 video"), library_answer("so how many videos did it index", qa)
        from_a_file = ["how many files are mentioned in the report", "how many files does the report say are in the index", "how many files did the backup script copy",
                       "how many photos did we take in lisbon", "how many songs are on the playlist for the party", "how many notes did i take about scaling"]
        for q in from_a_file:
            assert library_answer(q, qa) is None, (q, library_answer(q, qa))
            assert counts_files_from_text(q), f"an answer to {q!r} must say its number comes from a file"
        assert not counts_files_from_text("how many euros was the march invoice") and not counts_files_from_text("when is the rent due")
        # QA round 3: a pointer word inside another phrase is not a pointer.
        # These seven were answered from the index; four real ones were missed.
        for q in ["how many photos does the computer science syllabus ask for", "how many documents of warranty paperwork were scanned for the pc build",
                  "how many photos does the appendix of the book index name", "how many photos in the computer science portfolio", "how many files does a laptop hold",
                  "how many photos of roses does the gardening book index name", "how many videos does the computer science course want"]:
            assert library_answer(q, qa) is None, (q, library_answer(q, qa))
        for q in ["how many files ended up in the index after the scan", "how many files has intellifile gone through", "how many files is the index holding",
                  "how many files did the indexing cover", "i wonder how many files are indexed", "just curious, how many files are in the index", "roughly how many files did intellifile index"]:
            assert library_answer(q, qa) == everything, (q, library_answer(q, qa))
        print(f"A8f. QA round 2: 14 more wordings that point at the index, the app or the laptop are counted from the index; {len(from_a_file)} counts that a file states "
              "still go to the agent and are marked as a file's number: OK")

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
        from app.files.discovery import SUPPORTED_EXTENSIONS
        from app.watch_setup import IndexJob
        live = SimpleNamespace(job=IndexJob(), job_queue=SimpleNamespace(pending=0), roots=lambda: [str(workdir / "files")], watcher=SimpleNamespace(watched_files=[]), extensions=SUPPORTED_EXTENSIONS)

        def app_state():
            """The parts of the running app's state that Ask reads."""
            return SimpleNamespace(llm=None, llm_lock=threading.Lock(), llm_file=junk, llm_error=None, search_service=search, live_indexing=live,
                                   indexer=SimpleNamespace(file_record_store=search.file_record_store, vector_store=vector_store), access=SimpleNamespace(allows_indexing=True),
                                   usage_store=None, profile_builder=None, settings=SimpleNamespace(get=lambda key: False))
        import asyncio

        async def still_open():
            return False

        def asked(question, state):
            response = ask_endpoint(question, SimpleNamespace(app=SimpleNamespace(state=state), is_disconnected=still_open))

            async def read_body():
                return "".join([c if isinstance(c, str) else c.decode() async for c in response.body_iterator])
            return [json.loads(line[len("data: "):]) for line in asyncio.run(read_body()).split("\n\n") if line.startswith("data: ")]
        state = app_state()
        event = asked("what do I train on wednesday", state)[0]
        assert event["type"] == "error" and "could not be loaded" in event["message"] and "Failed to load model" in event["message"], event
        print("A14. A model file that fails to load is reported in Ask as an error message, not a server error: OK")

        # A question the index answers never loads the model: it is answered
        # at once, also when the model is damaged or missing.
        for model_file in (junk, None):
            state = app_state()
            state.llm_file = model_file
            import app.services.llm_helpers as llm_helpers
            found_model, llm_helpers.find_model_file = llm_helpers.find_model_file, lambda: None  # "missing" must stay missing on a dev machine
            try:
                events = asked("how many files are theere in the laptop", state)
                missing = asked("why is sourdough.md missing", state)
            finally:
                llm_helpers.find_model_file = found_model
            answer = next(e for e in events if e["type"] == "answer")
            assert answer["from_index"] and answer["text"].startswith("IntelliFile has indexed 8 files: ") and not any(e["type"] == "error" for e in events) and state.llm is None, events
            assert '"sourdough.md" is indexed, so it is not missing.' in next(e for e in missing if e["type"] == "answer")["text"], missing
        print("A14b. A question the index answers ('how many files', 'why is x missing') is answered without loading the model, also with a damaged or missing model: OK")

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
