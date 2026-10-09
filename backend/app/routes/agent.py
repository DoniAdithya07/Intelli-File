import json
import logging
import queue
import threading

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from ..services.llm_helpers import find_llm_file, get_llm, load_error_message, make_agent
from .common import remember

logger = logging.getLogger(__name__)

router = APIRouter()

# How often a waiting stream checks that the page is still open (see generate()).
DISCONNECT_POLL_SECONDS = 1.0
# Reading the previous question's cancel flag and installing the new one is
# one step: two questions arriving together both read the same "previous",
# and one run was never told to stop (2026-10-05).
_ask_cancel_lock = threading.Lock()


def _stop_previous_question(state) -> threading.Event:
    """Stops the running question, if any, and returns the new one's flag."""
    cancel = threading.Event()
    with _ask_cancel_lock:
        previous = getattr(state, "ask_cancel", None)
        if previous is not None:
            previous.set()
        state.ask_cancel = cancel
    return cancel


# ----- Phase 19: the agent -----


@router.get("/ask")
def ask_endpoint(q: str, request: Request):
    """Ask mode: the agent plans, calls search as a tool, reads the
    results and answers with citations — streamed as server-sent events
    (one JSON event per line: context, thought, tool_call, tool_result,
    answer_start, token, answer, done, error) so the UI can show the
    trace as it happens."""
    state = request.app.state
    question = q.strip()

    def sse(event: dict) -> str:
        return f"data: {json.dumps(event)}\n\n"

    if not question:
        return StreamingResponse(iter([sse({"type": "error", "message": "Ask a question first."})]), media_type="text/event-stream")
    # Cancellation (2026-10-05): until now a question kept the CPU-bound
    # model running to its 50 s cap after the user left the page or asked
    # again, and the next question waited for the model lock behind it. A
    # new question now stops the previous one, and so does closing the
    # stream; the agent stops at its next step or answer token. The previous
    # question is stopped BEFORE the model loads: it used to keep the CPU
    # busy (and the load slower) for the whole load.
    cancel = _stop_previous_question(state)
    try:
        agent = make_agent(state)  # loads the model on the first question
    except Exception as e:  # noqa: BLE001 — a damaged file or an unsupported CPU must reach the UI as a sentence, not an HTTP 500
        logger.exception("local LLM failed to load from %s", state.llm_file)
        return StreamingResponse(
            iter([sse({"type": "error", "message": load_error_message(e)})]),
            media_type="text/event-stream",
        )
    if agent is None:
        return StreamingResponse(
            iter([sse({"type": "error", "message": "Ask mode needs its local language model, the models\\llm folder inside the IntelliFile folder, and it is missing. Extract IntelliFile-windows.zip again, completely, then ask again. No restart needed."})]),
            media_type="text/event-stream",
        )

    # The agent runs in a worker thread and hands events over a queue, so
    # the response streams while the model is still generating.
    events: "queue.Queue[dict | None]" = queue.Queue()

    def work() -> None:
        run = agent.run(question)
        try:
            for event in run:
                if cancel.is_set():
                    run.close()
                    break
                events.put(event)
        except Exception as e:
            logger.exception("agent failed")
            events.put({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    async def generate():
        # Waits with a timeout and asks whether the page is still open: with
        # a plain blocking get() a page closed while the model read a long
        # prompt in silence (no event for many seconds) was only noticed at
        # the first token, and the run went on until then (2026-10-05). As
        # an async generator it also sees the server cancel the stream.
        final = None
        try:
            while True:
                try:
                    event = await run_in_threadpool(events.get, True, DISCONNECT_POLL_SECONDS)
                except queue.Empty:
                    if await request.is_disconnected():
                        break
                    continue
                if event is None:
                    break
                if event["type"] == "done":
                    final = event
                yield sse(event)
        finally:
            cancel.set()  # the client went away (or the run ended): stop the worker
        if final is not None:
            await run_in_threadpool(remember, state, "query", query=question, meta={
                "mode": "ask", "results": [s["file_id"] for s in final.get("sources", [])[:5]], "count": len(final.get("sources", [])),
                "route": "agent", "tool_calls": final.get("tool_calls"), "total_ms": round(final.get("seconds", 0) * 1000),
            })

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/ask/status")
def ask_status_endpoint(request: Request, preload: bool = False):
    """Whether Ask can run. Only `?preload=1` (the Ask page, when it opens)
    loads the model in the background, so the first question does not wait
    for it (~1.1 GB, plus its speed calibration, ~11 s on a slow laptop).
    Until 2026-10-04 every call preloaded, and the Index and Settings pages
    poll this too: opening them filled the laptop's memory with a model
    nobody had asked for. `loading`: a preload is running. `error`: the
    last load failure as a sentence, else None. A failed preload is not
    retried on its own; asking a question (/ask) tries again."""
    state = request.app.state
    find_llm_file(state)
    llm = state.llm
    failed = getattr(state, "llm_error", None) is not None
    if preload and llm is None and state.llm_file is not None and not failed and not getattr(state, "llm_preloading", False):
        state.llm_preloading = True

        def preload_model() -> None:
            try:
                get_llm(state)  # a failure is kept in state.llm_error
            except Exception:  # noqa: BLE001
                logger.exception("preloading the local LLM failed")
            finally:
                state.llm_preloading = False

        threading.Thread(target=preload_model, daemon=True).start()
    return {
        "available": state.llm_file is not None,
        "model": state.llm_file.stem if state.llm_file is not None else None,
        "loaded": llm is not None,
        "loading": bool(getattr(state, "llm_preloading", False)),
        "error": getattr(state, "llm_error", None),
        # Measured when the model loaded (LocalLLM.speed); the old 48-token
        # benchmark here waited for the model lock behind a running answer.
        "tokens_per_second": round(llm.tokens_per_second, 1) if llm is not None else None,
        "prompt_chars_per_second": round(llm.prefill_chars_per_second) if llm is not None else None,
    }
