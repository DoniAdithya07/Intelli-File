"""Ask cancellation: a new question stops the previous run; closing the stream stops its run.

2026-10-05 additions: a page closed during a long SILENT step (the model
reading its prompt, no events for many seconds) stops the run within about
a second; concurrent questions never leave two runs alive (the read-then-
swap of state.ask_cancel is locked); and the previous question is stopped
BEFORE the new one waits for the model to load."""
import asyncio, itertools, threading, time, types, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from app.routes import agent as route

steps = {}
class FakeAgent:
    def __init__(self, name, silent=0.0): self.name = name; self.silent = silent
    def run(self, q):
        steps[self.name] = 0
        time.sleep(self.silent)  # a long prompt read: no event at all
        for i in range(200):
            time.sleep(0.02); steps[self.name] = i
            yield {"type": "token", "text": str(i)}
        yield {"type": "done", "seconds": 4, "sources": []}

names = iter(["first", "second", "third"])
route.make_agent = lambda state: FakeAgent(next(names))
route.remember = lambda *a, **k: None
state = types.SimpleNamespace()
disconnected = {"value": False}
async def is_disconnected(): return disconnected["value"]
req = types.SimpleNamespace(app=types.SimpleNamespace(state=state), is_disconnected=is_disconnected)

async def first_chunks(resp, n):
    it = resp.body_iterator; out = []
    async for c in it:
        out.append(c)
        if len(out) >= n: break
    return it

loop = asyncio.new_event_loop()
r1 = route.ask_endpoint("q1", req); loop.run_until_complete(first_chunks(r1, 3))
r2 = route.ask_endpoint("q2", req)          # a new question
time.sleep(0.5); a = steps["first"]; time.sleep(0.5)
assert steps["first"] == a and a < 60, ("first run kept going", a, steps["first"])
it = loop.run_until_complete(first_chunks(r2, 3))
loop.run_until_complete(it.aclose())        # client closes the stream
time.sleep(0.5); b = steps["second"]; time.sleep(0.5)
assert steps["second"] == b and b < 80, ("second run kept going after disconnect", b, steps["second"])
r3 = route.ask_endpoint("q3", req)
chunks = []
async def all_chunks():
    async for c in r3.body_iterator: chunks.append(c)
loop.run_until_complete(all_chunks())
assert '"done"' in chunks[-1], "an uninterrupted question still finishes"
print(f"Ask cancel: new question stopped run 1 at step {a}; closed stream stopped run 2 at step {b}; run 3 finished: OK")

# (a) The page closes while the model reads a long prompt in silence. The
# stream waits for an event with a timeout and asks whether the client is
# still there, so the run is told to stop within ~1 s, not when the first
# token finally arrives (until 2026-10-05 the wait had no timeout).
route.make_agent = lambda state: FakeAgent("silent", silent=4.0)
r4 = route.ask_endpoint("q4", req)
cancel4 = state.ask_cancel
async def disconnect_during_prefill():
    reader = asyncio.ensure_future(r4.body_iterator.__anext__())  # the "context"-less silent run: nothing arrives
    await asyncio.sleep(0.2)
    disconnected["value"] = True
    t0 = time.perf_counter()
    while not cancel4.is_set() and time.perf_counter() - t0 < 2.5:
        await asyncio.sleep(0.05)
    reader.cancel()
    try:
        await reader
    except (asyncio.CancelledError, StopAsyncIteration):
        pass
    return time.perf_counter() - t0
waited = loop.run_until_complete(disconnect_during_prefill())
assert cancel4.is_set() and waited <= 1.6, f"a page closed during a silent prefill was noticed after {waited:.1f} s (or never)"
disconnected["value"] = False
print(f"(a) Page closed during a silent 4 s prefill: the run was told to stop after {waited:.1f} s: OK")

# (b) Questions arriving together: reading the previous run's cancel flag
# and installing the new one is one locked step, so exactly one run is
# left alive. A slow attribute widens the race window the lock closes.
class SlowState(types.SimpleNamespace):
    def __getattribute__(self, name):
        value = super().__getattribute__(name)  # read now, hand it over late: the window between read and swap
        if name == "ask_cancel":
            time.sleep(0.05)
        return value
race_state = SlowState()
race_req = types.SimpleNamespace(app=types.SimpleNamespace(state=race_state), is_disconnected=is_disconnected)
counter = itertools.count()
route.make_agent = lambda state: FakeAgent(f"race{next(counter)}")
barrier = threading.Barrier(5)
def ask_together():
    barrier.wait()
    route.ask_endpoint("same time", race_req)
threads = [threading.Thread(target=ask_together) for _ in range(5)]
for t in threads: t.start()
for t in threads: t.join()
time.sleep(0.6); first_look = {n: s for n, s in steps.items() if n.startswith("race")}; time.sleep(0.6)
alive = [n for n, s in steps.items() if n.startswith("race") and s != first_look[n]]
assert len(alive) == 1, f"{len(alive)} runs left alive by questions asked together: {alive}"
race_state.ask_cancel.set()
print("(b) Five questions at once: exactly one run is left alive: OK")

# (c) The previous question is stopped before the new one loads the model
# (seconds on the first question): until 2026-10-05 it kept the CPU busy,
# and the load slower, for the whole load.
route.make_agent = lambda state: FakeAgent("long")
route.ask_endpoint("q5", req)
previous = state.ask_cancel
seen = {}
def slow_load(state):
    seen["previous_stopped"] = previous.is_set()
    return FakeAgent("after load")
route.make_agent = slow_load
route.ask_endpoint("q6", req)
assert seen["previous_stopped"], "the previous question was still running while the model loaded"
state.ask_cancel.set()
print("(c) The previous question is stopped before the model loads for the next one: OK")
