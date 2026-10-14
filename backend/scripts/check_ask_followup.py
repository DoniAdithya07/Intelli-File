"""Ask follow-up questions (2026-10-14): "show me those video" after "how
many films are there" has nothing to resolve "those" against on its own —
the UI sends the previous question and answer so it can be resolved before
the agent ever sees it. Mirrors check_ask_cancel.py's style: route.ask_endpoint
called directly (it is a plain sync function returning a StreamingResponse),
make_agent/get_llm faked so no real model or index is needed.
"""
import asyncio, sys, types
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from app.routes import agent as route


class FakeAgent:
    def __init__(self, question):
        self.question = question

    def run(self, q):
        yield {"type": "done", "seconds": 0.1, "tool_calls": 0, "sources": []}


captured = {}


def fake_make_agent(state, question=""):
    captured["question"] = question
    return FakeAgent(question)


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        return self.reply


route.make_agent = fake_make_agent
route.remember = lambda *a, **k: captured.__setitem__("remembered", k.get("query"))
state = types.SimpleNamespace()


async def is_disconnected():
    return False


def ask(q, prev_q="", prev_a=""):
    captured.clear()
    req = types.SimpleNamespace(app=types.SimpleNamespace(state=state), is_disconnected=is_disconnected)
    resp = route.ask_endpoint(q, req, prev_q=prev_q, prev_a=prev_a)

    async def drain():
        events = []
        async for chunk in resp.body_iterator:
            events.append(chunk)
        return events
    return asyncio.new_event_loop().run_until_complete(drain())


# 1. A follow-up ("those") with a previous turn is resolved before the agent runs.
route.get_llm = lambda state: FakeLLM("show me spirited_away.mp4, ex_machina.mkv, drive.mov")
events = ask("can you show me those video", prev_q="how many films are there", prev_a="3 videos: spirited_away.mp4, ex_machina.mkv, drive.mov.")
assert captured["question"] == "show me spirited_away.mp4, ex_machina.mkv, drive.mov", captured["question"]
assert any('"type": "rewritten"' in e for e in events), "a rewritten question must be told to the UI"
assert captured["remembered"] == "can you show me those video", "activity history keeps what the user actually typed, not the rewrite"
print("1. A follow-up with a previous turn is rewritten before the agent runs, and the rewrite is reported to the UI: OK")

# 2. A plain question with no reference word is never sent to the model for
# rewriting, even with a previous turn available — no extra cost for the common case.
def must_not_be_called(state):
    raise AssertionError("get_llm must not be called for a question with no follow-up reference")
route.get_llm = must_not_be_called
events = ask("how many pdfs do i have", prev_q="how many films are there", prev_a="3 videos: a.mp4, b.mkv, c.mov.")
assert captured["question"] == "how many pdfs do i have"
assert not any('"type": "rewritten"' in e for e in events)
print("2. A plain question is never rewritten, even with a previous turn available (no model call paid for it): OK")

# 3. No previous turn at all: behaves exactly as before, no rewrite attempted.
events = ask("can you show me those video")
assert captured["question"] == "can you show me those video", "with no previous turn, the question is asked exactly as typed"
assert not any('"type": "rewritten"' in e for e in events)
print("3. With no previous turn (prev_q/prev_a empty), the question goes through unchanged: OK")

# 4. A rewrite that comes back empty or implausibly long falls back to the question as typed.
route.get_llm = lambda state: FakeLLM("")
events = ask("show me those", prev_q="q", prev_a="a")
assert captured["question"] == "show me those", "an empty rewrite falls back to the original question"
route.get_llm = lambda state: FakeLLM("x" * 500)
events = ask("show me those", prev_q="q", prev_a="a")
assert captured["question"] == "show me those", "an implausibly long rewrite falls back to the original question"
print("4. A failed/empty/too-long rewrite falls back to the question as typed, never blocks the answer: OK")

print("\nAsk follow-up checks OK.")
