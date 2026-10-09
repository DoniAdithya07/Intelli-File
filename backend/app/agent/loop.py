"""The agent (Phase 19 — the assignment's Objective 1 "agent" and the LLM
reasoning of Objective 3): a bounded plan → tool → observe loop over
IntelliFile's own search, ending in a short answer that cites its sources.

Two kinds of model turn:
- **planning turns** — the model answers in JSON only: a thought, and
  either a tool call (`search` with a query, a mode and optional filters —
  this is where it *chooses the retrieval strategy* — or `read_more`) or
  `answer` when it has enough;
- **the answer turn** — free text, streamed token by token, citing sources
  as [n]. Citations are verified against the source table; a number that
  does not exist is dropped, and an answer with no valid citation is
  reported as "not found in your files" rather than trusted.

Before the first planning turn the whole question is searched once with
the router choosing the tier ("first look"), and the closest sentence of
the top strong result is sent as a quick answer while the model works.

Limits: MAX_TOOL_CALLS tool calls and BUDGET_SECONDS wall-clock per
question; the model only ever sees the retrieved snippets, never files.
Every step is emitted as an event so the UI can show the trace.
"""

import json
import logging
import re
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from .library import library_answer
from .llm import CONTEXT_TOKENS
from .tools import Toolbox

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = 4
BUDGET_SECONDS = 25.0
MAX_ANSWER_TOKENS = 350
MAX_PLAN_TOKENS = 220
PLAN_DEADLINE_FACTOR = 1.3  # planning stops at 1.3 x budget, leaving time for the answer inside 2 x budget
ANSWER_MAX_SOURCES = 6            # sources the answering model reads (strong matches first)
ANSWER_SOURCE_CHARS = 700         # per source, normally (= SNIPPET_CHARS)
ANSWER_SOURCE_CHARS_TIGHT = 350   # per source when little time is left
ANSWER_TIGHT_SECONDS = 22.0       # "little time left": under this many seconds before the 2 x budget cap
# Speed-aware limits (2026-10-04, "Ask is not working at all" on a slower
# laptop). With a model that reports its measured speed (LocalLLM.
# estimate_seconds), a planning turn runs only if the answer still fits
# after it, and the answer reads only as many source characters as this
# computer can read before the cap. Typical sizes: a plan is 40-80 tokens,
# an answer 15-40.
PLAN_EST_TOKENS = 60
ANSWER_EST_TOKENS = 40
# A first planning turn estimated to take longer than this is skipped when
# the first look already found strong matches: on a fast laptop a turn is
# 3-6 s and the model may still refine; on a slow one it was 15-25 s, mostly
# to say "answer" (measured 2026-10-04).
SLOW_PLAN_SECONDS = 12.0
# (sources, characters per source) the answer may read, largest first.
ANSWER_SIZES = ((ANSWER_MAX_SOURCES, ANSWER_SOURCE_CHARS), (ANSWER_MAX_SOURCES, ANSWER_SOURCE_CHARS_TIGHT),
                (ANSWER_MAX_SOURCES // 2, ANSWER_SOURCE_CHARS_TIGHT), (2, 250), (1, 250))
# An answer that has started is finished, not cut at the cap and then
# rejected as ungrounded: past the cap it may write this many more tokens,
# and stops at the first sentence end once it has cited a source.
ANSWER_GRACE_TOKENS = 48
_ANSWER_END_RE = re.compile(r"(\[\d{1,2}\]\s*[.!?]?|[.!?])\s*$")
# The context window (llm.CONTEXT_TOKENS) holds the prompt AND the reply.
# Each search adds its results to the planning prompt, and with long
# passages the fourth planning turn or a six-source answer passed 4,096
# tokens: llama.cpp raised "Requested tokens exceed context window" and Ask
# ended in an error (2026-10-04). Before every model call the prompt is
# measured with the model's own tokenizer and shrunk to leave room for the
# reply plus this margin. MESSAGE_OVERHEAD_TOKENS covers the chat template's
# role markers around each message.
CONTEXT_MARGIN_TOKENS = 64
MESSAGE_OVERHEAD_TOKENS = 6


def _prompt_tokens(llm, messages: list[dict]) -> int | None:
    """Prompt size in the model's tokens; None for a model without a
    tokenizer (the scripted stand-ins in the tests)."""
    tokenize = getattr(llm, "tokenize", None)
    if tokenize is None:
        return None
    return sum(len(tokenize(m["content"])) + MESSAGE_OVERHEAD_TOKENS for m in messages) + MESSAGE_OVERHEAD_TOKENS


def _fits(llm, messages: list[dict], reply_tokens: int) -> bool:
    used = _prompt_tokens(llm, messages)
    return used is None or used + reply_tokens + CONTEXT_MARGIN_TOKENS <= getattr(llm, "n_ctx", CONTEXT_TOKENS)


def _answer_sources(sources: list, remaining_seconds: float, limit: int | None = None) -> list:
    """The sources the answer is written from: strong matches before weak
    ones, in the order they were found, at most ANSWER_MAX_SOURCES, and
    half as many when time is short (or `limit`)."""
    if limit is None:
        limit = ANSWER_MAX_SOURCES if remaining_seconds > ANSWER_TIGHT_SECONDS else ANSWER_MAX_SOURCES // 2
    ranked = sorted(sources, key=lambda s: (s.confidence != "strong", s.number))
    return sorted(ranked[:limit], key=lambda s: s.number)

_CITATION_RE = re.compile(r"\[(\d{1,2})\]")

PLANNER_SYSTEM = """You are the planning step of IntelliFile, a search assistant over the user's own files on this computer.
You cannot read files directly. You have tools:
- search: run a search. Arguments: "query" (a short search phrase — rewrite the question into the words a document would contain), "mode" ("auto" lets the router pick; "keyword" for exact words; "smart" for meaning), optional "filters" (e.g. "type:pdf", "after:2025-03", "in:invoices", "ext:csv").
- read_more: read more of a numbered source. Arguments: "source" (its number).
- answer: stop searching and write the answer from the sources found so far.
Rules: split a multi-part question into separate searches. Prefer "keyword" when the question quotes exact words or names; "smart" for concepts. Use "filters" ONLY when the question itself names a file type, a folder or a date — otherwise leave filters empty. When results contain the answer, choose "answer". After at most {max_calls} tool calls you must answer.
Keep "thought" under 12 words.
Respond with ONE JSON object only: {{"thought": "...", "action": "search"|"read_more"|"answer", "query": "...", "mode": "...", "filters": "...", "source": 0}}"""

ANSWER_SYSTEM = """You are IntelliFile's answer step. The numbered sources are excerpts from the user's own files; a source may be a table flattened into one line (column names first, then rows). Answer the question in one or two sentences using the facts in the sources, and end each sentence with the number of the source it came from in brackets, e.g. [2]. Copy numbers, dates and names exactly as they appear. Never answer from memory."""
# Deliberately no "say you couldn't find it" instruction: measured
# 2026-09-21, a 1.5B model reaches for that escape hatch even with the
# answer in front of it. Grounding is the harness's job (see _ground):
# an answer that no retrieved source supports is rejected, whatever the
# model claims.


class Agent:
    def __init__(self, llm, toolbox_factory: Callable[[], Toolbox], max_tool_calls: int = MAX_TOOL_CALLS, budget_seconds: float = BUDGET_SECONDS, first_look: bool = True):
        self.llm = llm
        self.toolbox_factory = toolbox_factory
        self.max_tool_calls = max_tool_calls
        self.budget_seconds = budget_seconds
        self.first_look = first_look  # search the whole question before the first planning turn

    def _mentions(self, word: str, text: str) -> bool:
        """One-word yes/no from the model: does this source mention or give a <word>?"""
        messages = [
            {"role": "system", "content": "You check whether a text is about something. Answer with one word: yes or no."},
            {"role": "user", "content": f"Text:\n{text[:PREMISE_JUDGE_CHARS]}\n\nDoes this text mention or give a {word}? Answer yes or no."},
        ]
        # Past the hard cap (2 x budget) the judge is not asked at all: the
        # premise check runs after the answer, and up to 12 of these calls
        # used to run with no deadline, pushing Ask past 50 s (code review
        # 2026-09-27). Like a judge failure, that keeps the answer.
        deadline = getattr(self, "_hard_deadline", None)
        if deadline is not None and time.perf_counter() >= deadline:
            return True
        try:
            return self.llm.chat(messages, max_tokens=3, deadline=deadline).strip().lower().startswith("yes")
        except Exception:  # noqa: BLE001 — a judge failure must never block an answer...
            # ...but it must not pass silently either (2026-10-05: it was
            # swallowed and counted as "yes"). Logged, and run() tells the
            # user the check could not run (PREMISE_UNCHECKED_WARNING).
            logger.warning("premise judge failed on %r; the answer is kept unverified", word, exc_info=True)
            self._judge_failed = True
            return True

    def _answer_messages(self, question: str, tools: Toolbox, remaining: float) -> tuple[list[dict], set[int]]:
        """The answer prompt, and the source numbers it shows. With a measured
        speed: the largest of ANSWER_SIZES this computer can read and answer
        in `remaining` seconds (the smallest if none fits). Without one: the
        fixed rule, shorter passages when little time is left. Either way a
        smaller size is taken while the prompt would not fit the context
        window with the answer (see CONTEXT_MARGIN_TOKENS)."""
        estimate = getattr(self.llm, "estimate_seconds", None)
        if estimate is None:
            tight = remaining <= ANSWER_TIGHT_SECONDS
            fixed = (ANSWER_MAX_SOURCES // 2 if tight else ANSWER_MAX_SOURCES, ANSWER_SOURCE_CHARS_TIGHT if tight else ANSWER_SOURCE_CHARS)
            sizes = [fixed] + [size for size in ANSWER_SIZES if size[0] * size[1] < fixed[0] * fixed[1]]
        else:
            sizes = ANSWER_SIZES
        for count, chars in sizes:
            shown = _answer_sources(tools.sources, remaining, limit=count)
            messages = [
                {"role": "system", "content": ANSWER_SYSTEM},
                {"role": "user", "content": f"Question: {question}\n\nSources:\n{tools.render(shown, chars)}\n\nAnswer with citations:"},
            ]
            if (estimate is None or estimate(messages, ANSWER_EST_TOKENS) <= remaining) and _fits(self.llm, messages, MAX_ANSWER_TOKENS):
                break
        return messages, {s.number for s in shown}

    def _answer_reserve(self, question: str, tools: Toolbox) -> float:
        """Seconds to keep for the answer: the estimate for a mid-size answer
        prompt (0 when nothing was found: no answer turn runs then)."""
        estimate = getattr(self.llm, "estimate_seconds", None)
        if estimate is None or not tools.sources:
            return 0.0
        count, chars = ANSWER_SIZES[2]
        shown = _answer_sources(tools.sources, 0, limit=count)
        return estimate([{"role": "system", "content": ANSWER_SYSTEM}, {"role": "user", "content": f"Question: {question}\n\nSources:\n{tools.render(shown, chars)}"}], ANSWER_EST_TOKENS)

    def run(self, question: str) -> Iterator[dict]:
        """Yields trace events: {"type": ...}. Types: context, thought,
        tool_call, tool_result, answer_start, token, answer, done, error."""
        t_start = time.perf_counter()
        self._hard_deadline = t_start + self.budget_seconds * 2  # nothing after this asks the model
        tools = self.toolbox_factory()
        question = question.strip()
        context = tools.context()
        yield {"type": "context", "context": context}

        # "How many files are there?", "Are there any video files?": the
        # answer is a count the index already has, not text inside a file.
        records = tools.search_service.file_record_store.list_active()
        facts = library_answer(question, [r.path for r in records])
        if facts is not None:
            yield {"type": "thought", "text": "counted the files in the index: this question is about the collection, not the contents of a file", "system": True}
            yield {"type": "answer_start"}
            yield {"type": "token", "text": facts}
            yield {"type": "answer", "text": facts, "citations": [], "grounded": True, "from_index": True}
            yield {"type": "done", "seconds": round(time.perf_counter() - t_start, 1), "tool_calls": 0, "strategies": [], "sources": []}
            return

        calls = 0       # tool calls actually executed
        turns = 0       # planning turns, including ones that led nowhere
        strategies: list[dict] = []
        seen_searches: set[tuple[str, str, str]] = set()
        fallback_used = False
        quick_sent = False

        # First look (improvement 3, 2026-09-26): the whole question goes
        # straight to search with the router choosing the tier — the step it
        # already gets right (hit@5 100% on the labelled corpus). Measured
        # before this: the model's first JSON plan alone took ~7.7 s of a
        # 20.5 s mean, planning 18.1 s in all. The model now plans from real
        # results: search again with other words, mode or filters, read more,
        # or answer — the strategy choice stays the model's.
        first_found: list = []
        if question and self.first_look and self.budget_seconds > 0:
            calls += 1
            seen_searches.add((question.lower(), "auto", ""))
            yield {"type": "tool_call", "tool": "search", "args": {"query": question, "mode": "auto", "filters": ""}, "call": calls, "first_look": True}
            first_found, route = tools.search(question, mode="auto")
            strategies.append({"query": question, "mode": "auto", "filters": "", "route": route["tier"], "results": len(first_found)})
            yield {"type": "tool_result", "tool": "search", "sources": [s.as_dict() for s in first_found], "route": route, "call": calls}
            quick = _quick_answer(question, first_found)
            if quick is not None:
                quick_sent = True
                yield {"type": "quick_answer", "text": quick[0], "source": quick[1].as_dict()}
        messages = [
            {"role": "system", "content": PLANNER_SYSTEM.format(max_calls=self.max_tool_calls)},
            {"role": "user", "content": self._first_user_message(question, context, tools.render(first_found) if first_found else None, calls, self.max_tool_calls)},
        ]
        while True:
            elapsed = time.perf_counter() - t_start
            if calls >= self.max_tool_calls or turns >= self.max_tool_calls + 2 or elapsed > self.budget_seconds:
                reason = "tool-call limit reached" if calls >= self.max_tool_calls else "time budget reached" if elapsed > self.budget_seconds else "planning limit reached"
                yield {"type": "thought", "text": f"{reason}, answering with what was found.", "system": True}
                break
            # Keep the planning prompt inside the context window: drop the
            # oldest search round (its plan + its results). Its sources stay
            # in the toolbox, so the answer can still use and cite them.
            dropped = 0
            while len(messages) > 2 and not _fits(self.llm, messages, MAX_PLAN_TOKENS):
                del messages[2:4]
                dropped += 1
            if dropped:
                yield {"type": "thought", "text": f"the planning prompt outgrew the model's context window, so the oldest search results ({dropped} round{'s' if dropped != 1 else ''}) were left out of it; the answer still reads them.", "system": True}
            if not _fits(self.llm, messages, MAX_PLAN_TOKENS):
                yield {"type": "thought", "text": "the question is too long to plan with, answering with what was found.", "system": True}
                break
            # On a slow or busy computer one planning turn can take 15-30 s
            # (measured 2026-10-04). Plan only if the answer still fits
            # inside the hard cap afterwards, by this computer's measured speed.
            estimate = getattr(self.llm, "estimate_seconds", None)
            reserve = self._answer_reserve(question, tools)
            plan_deadline = t_start + self.budget_seconds * PLAN_DEADLINE_FACTOR
            if estimate is not None:
                plan_seconds = estimate(messages, PLAN_EST_TOKENS)
                if turns == 0 and plan_seconds > SLOW_PLAN_SECONDS and any(s.confidence == "strong" for s in first_found):
                    yield {"type": "thought", "text": "this computer is slow and the first search found strong matches, so they are answered from directly.", "system": True}
                    break
                if elapsed + plan_seconds + reserve > self.budget_seconds * 2:
                    yield {"type": "thought", "text": "another planning turn would not leave time for the answer on this computer, answering with what was found.", "system": True}
                    break
                plan_deadline = min(plan_deadline, self._hard_deadline - reserve)
            turns += 1
            t0 = time.perf_counter()
            try:
                # A planning turn may run at most until PLAN_DEADLINE_FACTOR x
                # the budget; past that the agent answers with what it has, so
                # the answer still fits inside the 2 x budget hard cap.
                # No JSON grammar (json_only=False): measured 2026-09-26 on 5
                # questions, llama.cpp's grammar sampling made each planning
                # turn 3-4x slower (9.1 s vs 2.0 s, 4.3 s vs 1.3 s) for the
                # same action and valid JSON every time; _parse_plan tolerates
                # stray text and anything unparseable means "answer now".
                raw = self.llm.chat(messages, max_tokens=MAX_PLAN_TOKENS, json_only=False, deadline=plan_deadline, label="plan")
            except Exception as e:  # the model failing must surface, not hang the UI
                yield {"type": "error", "message": f"The local model failed: {type(e).__name__}: {e}"}
                return
            plan = _normalize_plan(_parse_plan(raw))
            plan_ms = round((time.perf_counter() - t0) * 1000)
            if plan.get("thought"):
                yield {"type": "thought", "text": plan["thought"], "ms": plan_ms}
            action = plan.get("action")
            if action == "search" and plan.get("query"):
                mode = plan.get("mode") if plan.get("mode") in ("auto", "smart", "keyword", "exact") else "auto"
                filters, dropped = _sanitize_filters(plan.get("filters") or "", question)
                if dropped:
                    yield {"type": "thought", "text": f"ignored filters the question never asked for: {dropped}", "system": True}
                query = plan["query"].strip()
                signature = (query.lower(), mode, filters)
                if signature in seen_searches:
                    # Re-running the same search cannot find anything new.
                    # First time: search the whole question ourselves in
                    # auto mode (a stuck small model rarely finds better
                    # words on its own). After that: nudge and let it answer.
                    if not fallback_used and (question.lower(), "auto", "") not in seen_searches:
                        fallback_used = True
                        yield {"type": "thought", "text": "the plan repeated itself, so the whole question is searched instead", "system": True}
                        query, mode, filters = question, "auto", ""
                        signature = (query.lower(), mode, filters)
                    else:
                        yield {"type": "thought", "text": "that exact search already ran; asking for different words or an answer", "system": True}
                        messages.append({"role": "assistant", "content": json.dumps(plan)})
                        messages.append({"role": "user", "content": f"That exact search already ran and its results are above. Either search with DIFFERENT words (or a different mode), or choose \"answer\" if the sources contain the answer. Tool calls used: {calls} of {self.max_tool_calls}. Next JSON:"})
                        continue
                calls += 1
                seen_searches.add(signature)
                yield {"type": "tool_call", "tool": "search", "args": {"query": query, "mode": mode, "filters": filters}, "call": calls}
                found, route = tools.search(query, mode=mode, filters=filters)
                if route.get("fallback_from_mode"):
                    yield {"type": "thought", "text": f"{route['fallback_from_mode']} mode found nothing, retried with automatic routing ({route['tier']})", "system": True}
                if not found and filters:
                    # A filter the question did justify can still be wrong
                    # for this index (no PDFs at all, say): retry unfiltered
                    # rather than burn the whole budget on empty results.
                    yield {"type": "thought", "text": f"nothing matched with filters \u201c{filters}\u201d, retrying without them", "system": True}
                    found, route = tools.search(query, mode=mode, filters="")
                    filters = ""
                strategies.append({"query": query, "mode": mode, "filters": filters, "route": route["tier"], "results": len(found)})
                observation = tools.render(found)
                yield {"type": "tool_result", "tool": "search", "sources": [s.as_dict() for s in found], "route": route, "call": calls}
                if not quick_sent:
                    quick = _quick_answer(question, found)
                    if quick is not None:
                        quick_sent = True
                        yield {"type": "quick_answer", "text": quick[0], "source": quick[1].as_dict()}
                messages.append({"role": "assistant", "content": json.dumps(plan)})
                messages.append({"role": "user", "content": f"Search results:\n{observation}\n\nSources so far: {len(tools.sources)}. Tool calls used: {calls} of {self.max_tool_calls}. Next JSON:"})
                continue
            if action == "read_more" and plan.get("source"):
                calls += 1
                yield {"type": "tool_call", "tool": "read_more", "args": {"source": plan["source"]}, "call": calls}
                source = tools.read_more(int(plan["source"]))
                observation = tools.render([source]) if source else "No such source."
                yield {"type": "tool_result", "tool": "read_more", "sources": [source.as_dict()] if source else [], "call": calls}
                messages.append({"role": "assistant", "content": json.dumps(plan)})
                messages.append({"role": "user", "content": f"{observation}\n\nTool calls used: {calls} of {self.max_tool_calls}. Next JSON:"})
                continue
            # "answer", or anything malformed: stop planning.
            if action != "answer" or plan.get("malformed"):
                yield {"type": "thought", "text": "the plan was not a valid tool call, answering with what was found.", "system": True}
            break

        if not tools.sources:
            # Nothing was ever retrieved: no model turn can be grounded.
            answer = "I couldn't find that in your files."
            yield {"type": "answer_start"}
            yield {"type": "token", "text": answer}
            yield {"type": "answer", "text": answer, "citations": [], "grounded": False}
            yield {"type": "done", "seconds": round(time.perf_counter() - t_start, 1), "tool_calls": calls, "strategies": strategies, "sources": [s.as_dict() for s in tools.sources]}
            return

        # The model reads every source before it writes a word, and that
        # reading is not interruptible. With up to 20 sources (4 searches x
        # 5) it took over 25 s on a busy laptop, and Ask ran past its 50 s
        # cap (56.3 s and 51.7 s, 2026-09-27). So the answer gets the best
        # sources only, strong matches first, and shorter passages when
        # little time is left.
        # With a measured speed, the prompt is sized so this computer can read
        # it and answer before the cap (_answer_messages).
        remaining = self.budget_seconds * 2 - (time.perf_counter() - t_start)
        answer_messages, shown_numbers = self._answer_messages(question, tools, remaining)
        if not _fits(self.llm, answer_messages, MAX_ANSWER_TOKENS):
            # Even one short source does not fit: the question itself fills the window.
            yield {"type": "error", "message": "The question is too long for the local language model. Please ask it in fewer words."}
            return
        yield {"type": "answer_start"}
        pieces: list[str] = []
        past_cap = 0
        try:
            for token in self.llm.stream(answer_messages, max_tokens=MAX_ANSWER_TOKENS):
                pieces.append(token)
                yield {"type": "token", "text": token}
                if time.perf_counter() > self._hard_deadline:
                    # Reading the prompt cannot be interrupted, so the first
                    # token may itself arrive past the cap. Cutting there left
                    # 1-3 tokens that grounding rejected: "I couldn't find
                    # that" with the answer in the sources (slow laptop,
                    # 2026-10-04). Finish the sentence instead, bounded.
                    past_cap += 1
                    so_far = "".join(pieces).rstrip()
                    if past_cap >= ANSWER_GRACE_TOKENS or (_ANSWER_END_RE.search(so_far) and _CITATION_RE.search(so_far)):
                        break
        except Exception as e:
            yield {"type": "error", "message": f"The local model failed while answering: {type(e).__name__}: {e}"}
            return
        text = "".join(pieces).strip()
        cited_numbers = [int(n) for n in _CITATION_RE.findall(text)]
        body = _CITATION_RE.sub("", text).strip()
        # A citation is only worth keeping if the answer actually draws on
        # that source: a hallucination can still write "[1]", and a small
        # model writing a correct answer cites the wrong source about one
        # time in ten (measured: "excess 350 euros" cited the expenses
        # sheet, which merely also says "insurance"). The sources the
        # answer's own words trace to (_ground) are the judge; the model's
        # numbers are kept when they agree, replaced when they do not.
        supported = [n for n in _ground(body, tools) if n in shown_numbers]  # only what the model was shown
        valid = [n for n in dict.fromkeys(cited_numbers) if n in supported]
        not_found = "couldn't find that" in text.lower() or len(body.split()) < 3
        inferred = False
        if not valid and not not_found and supported:
            valid = supported
            inferred = True
        trimmed = _trim_citations(body, valid, tools)
        if trimmed != valid:
            dropped_names = ", ".join(tools.get(n).filename for n in valid if n not in trimmed)
            yield {"type": "thought", "text": f"dropped a citation the answer does not need ({dropped_names})", "system": True}
            valid = trimmed
        if valid:
            text = f"{body} {' '.join(f'[{n}]' for n in valid)}" if inferred else _CITATION_RE.sub(lambda m: m.group(0) if int(m.group(1)) in valid else "", text)
        if not valid:
            if not not_found:
                yield {"type": "thought", "text": "the answer could not be traced to any retrieved source, so it was rejected", "system": True}
            text = "I couldn't find that in your files."
            not_found = True
        grounded = bool(valid) and not not_found
        # Premise check (2026-09-21, from the 30-question key): lexical
        # grounding passed three confident false answers — "groceries in
        # September" answered with January's figure, "my dog's vet" with the
        # dentist. Two precise rules catch that class without touching real
        # answers: a month / weekday / year the question names must appear
        # in a cited source in some form, and a distinctive question word
        # that the answer repeats but no cited source contains means the
        # answer restated the question's premise as fact.
        self._judge_failed = False
        if grounded:
            seen = [s for s in tools.sources if s.number not in valid]  # retrieved but not cited, cited ones first
            problem = _unsupported_premise(question, body, [tools.get(n) for n in valid], judge=self._mentions, retrieved=seen)
            if problem:
                yield {"type": "thought", "text": f"the sources don't support the question's premise ({problem}), so no answer is given", "system": True}
                text = "I couldn't find that in your files."
                grounded, valid, not_found = False, [], True
        citations = [tools.get(n).as_dict() for n in valid]
        # Numbers are where a small model slips (measured: "610 euros" in
        # the source became "210 euros" in the answer while every other
        # word matched). Every figure in the answer must appear in a cited
        # source; otherwise the answer is shown with a warning, not as fact.
        warnings = []
        if grounded and self._judge_failed:
            warnings.append(PREMISE_UNCHECKED_WARNING)
        if grounded:
            missing = _unverified_numbers(body, [tools.get(n) for n in valid])
            # When EVERY figure of the answer is unverified, the figure is the
            # answer: a warning under it is not enough. Found 2026-10-05 on the
            # built-in example "when is the rent due": "The rent is due on
            # February 14th." came back grounded with the 14 flagged — the
            # words matched a rent line, the date was invented. Such an answer
            # is "not found"; one wrong figure among right ones is flagged.
            # Only for dates and amounts (a figure of 2+ digits): a small
            # count such as "5 invoices" can be right without the digit
            # appearing in any snippet (code review 2026-10-06).
            if missing and len(missing) == len(set(_NUMBER_RE.findall(body))) and any(sum(c.isdigit() for c in m) >= 2 for m in missing):
                yield {"type": "thought", "text": f"the answer's figure(s) {', '.join(missing)} are in none of the cited sources, so no answer is given", "system": True}
                text = "I couldn't find that in your files."
                grounded, valid, not_found, warnings, citations = False, [], True, [], []
            elif missing:
                warnings.append(f"Not in the cited sources, check before relying on it: {', '.join(missing)}")
                yield {"type": "thought", "text": f"figure(s) {', '.join(missing)} in the answer are not in the cited sources, flagged", "system": True}
        yield {"type": "answer", "text": text, "citations": citations, "grounded": grounded, "citations_inferred": inferred, "warnings": warnings}
        yield {"type": "done", "seconds": round(time.perf_counter() - t_start, 1), "tool_calls": calls, "strategies": strategies, "sources": [s.as_dict() for s in tools.sources]}

    @staticmethod
    def _first_user_message(question: str, context: dict, first_results: str | None = None, calls: int = 0, max_calls: int = MAX_TOOL_CALLS) -> str:
        lines = [f"Question: {question}"]
        session = context.get("session") or {}
        if session.get("files"):
            lines.append("The user is currently working with: " + ", ".join(Path(f).name for f in session["files"][:5]))
        if context.get("topics"):
            lines.append("The user's usual topics: " + "; ".join(context["topics"]))
        if first_results is not None:
            lines.append(f"A first search of the whole question returned:\n{first_results}\n\nTool calls used: {calls} of {max_calls}.")
            lines.append("If these sources answer every part of the question, choose \"answer\"; otherwise search with different words, mode or filters. JSON only:")
        elif calls:
            lines.append(f"A first search of the whole question found nothing. Tool calls used: {calls} of {max_calls}. Search with different words, mode or filters. JSON only:")
        else:
            lines.append("Plan the first tool call. JSON only:")
        return "\n".join(lines)


_ACTION_ALIASES = {"search": "search", "find": "search", "lookup": "search", "query": "search", "read_more": "read_more", "read": "read_more", "answer": "answer", "respond": "answer", "reply": "answer", "final": "answer", "finish": "answer", "provide": "answer", "done": "answer"}
_FILTER_RE = re.compile(r"\b(type|ext|after|before|size|in):(\S+)", re.IGNORECASE)
_GROUND_STOPWORDS = {"the", "this", "that", "with", "from", "your", "have", "will", "were", "been", "there", "their", "which", "about", "into", "also", "than", "then", "when", "what", "where", "they", "them", "files", "file", "found", "could", "couldn", "sources", "source"}


def _normalize_plan(plan: dict) -> dict:
    action = str(plan.get("action", "")).strip().lower()
    plan["action"] = _ACTION_ALIASES.get(action, "search" if plan.get("query") and not action else action)
    return plan


def _sanitize_filters(filters: str, question: str) -> tuple[str, str]:
    """Keep only filters the question itself justifies: a type it names, a
    folder word it contains, a year it mentions. Returns (kept, dropped)."""
    q = question.lower()
    kept, dropped = [], []
    for m in _FILTER_RE.finditer(filters):
        key, value = m.group(1).lower(), m.group(2).strip("\"'")
        v = value.lower().lstrip(".")
        ok = False
        if key in ("type", "ext"):
            ok = re.search(rf"\b{re.escape(v)}\b", q) is not None or (v in ("xlsx", "csv") and "spreadsheet" in q) or (v == "pptx" and ("slides" in q or "presentation" in q))
        elif key in ("after", "before"):
            ok = re.search(r"\b(19|20)\d{2}\b", q) is not None and v[:4] in q
        elif key == "in":
            ok = v in q
        elif key == "size":
            ok = any(w in q for w in ("large", "small", "big", "mb", "kb", "gb", "size"))
        (kept if ok else dropped).append(f"{key}:{value}")
    stray = _FILTER_RE.sub("", filters).strip()
    if stray:
        dropped.append(stray)
    return " ".join(kept), ", ".join(dropped)


def _distinctive(text: str) -> set[str]:
    words = {w.strip(".,") for w in re.findall(r"[a-z0-9][a-z0-9,.-]*[a-z0-9]|[a-z0-9]", text.lower())}
    return {w for w in words if (len(w) >= 4 and w not in _GROUND_STOPWORDS) or any(ch.isdigit() for ch in w)}


_NUMBER_RE = re.compile(r"\d[\d,.]*\d|\d")


def _unverified_numbers(text: str, sources: list) -> list[str]:
    """Figures in the answer that appear in none of the cited sources
    (commas ignored, so 1,200 and 1200 agree; source numbers [n] and the
    ordinals of dates like '3rd' are checked like any other figure)."""
    hay = " ".join(s.text for s in sources if s is not None).replace(",", "")
    missing = []
    for raw in _NUMBER_RE.findall(text):
        plain = raw.replace(",", "").strip(".")
        if plain and plain not in hay:
            missing.append(raw)
    return list(dict.fromkeys(missing))


_MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12}
_WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
_PREMISE_FILLER = _GROUND_STOPWORDS | {
    "much", "many", "when", "what", "where", "which", "does", "did", "name", "number", "time", "long", "cost", "pay", "paid", "spend",
    "spent", "goes", "come", "coming", "going", "have", "need", "want", "know", "tell", "find", "show", "give", "last", "next", "each",
    "every", "some", "more", "most", "very", "just", "only", "also", "into", "onto", "over", "under", "after", "before", "with", "without",
    "will", "would", "should", "could", "must", "there", "here", "your", "mine", "ours", "them", "they", "please", "about", "again",
    "and", "for", "the", "how", "who", "why", "are", "was", "any", "all", "one", "two", "our", "you", "its", "not", "but", "can", "get",
    "got", "put", "use", "see", "say", "let", "may", "own", "yet", "far", "big", "old", "new", "ago", "per", "via",
}
PREMISE_JUDGE_MAX_WORDS = 3
PREMISE_JUDGE_CHARS = 900
PREMISE_JUDGE_MAX_SOURCES = 4
PREMISE_UNCHECKED_WARNING = "The premise check could not run; check the answer against the sources"


_IRREGULAR_FORMS = {
    "fly": ("flight", "flights", "flew", "flying"), "buy": ("bought",), "pay": ("paid",), "spend": ("spent",), "leave": ("left",),
    "go": ("went",), "take": ("took",), "get": ("got",), "meet": ("met",), "bring": ("brought",), "sleep": ("slept",), "eat": ("ate",),
    "drive": ("drove",), "run": ("ran",), "write": ("wrote",), "sell": ("sold",), "send": ("sent",), "teach": ("taught",),
    "owe": ("owed", "owes"), "cost": ("costs",), "due": ("dues",),
}


def _premise_haystack(sources: list) -> str:
    return " ".join(f"{s.filename} {s.text or ''}" for s in sources if s is not None).lower()


def _mentioned(word: str, hay: str, tokens: set[str]) -> bool:
    """Cheap morphology: the word, an inflection of it, or a token sharing its stem."""
    if word in tokens or word in hay:
        return True
    if any(f in tokens for f in _IRREGULAR_FORMS.get(word, ())):
        return True
    stem = word
    for suffix in ("ies", "ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            stem = word[: -len(suffix)]
            break
    if len(stem) >= 4:
        return any(t.startswith(stem[:5] if len(stem) >= 5 else stem) for t in tokens)
    return any(t == stem or t == stem + "s" for t in tokens)


def _unsupported_premise(question: str, answer: str, sources: list, judge: Callable[[str, str], bool] | None = None, retrieved: list | None = None) -> str | None:
    """Return why the cited sources cannot support the question's premise, or None.

    Deterministic part: a month, weekday or year the question names must appear in a
    cited source (name or text) in some form. Judged part: a distinctive question word
    the answer repeats but no cited source contains is handed to ``judge(word, text)``
    — "does this text mention or give a <word>?" — and only when every cited source
    says no is the answer treated as restating the question's premise as fact.
    Without a judge the word rule is not applied at all (a source that says "94 °C"
    answers "temperature" without containing the word). The word rule looks at every
    source the agent retrieved (``retrieved``), cited ones first: a correct answer whose
    citation step kept only one of two files must not be thrown away because the other
    file is the one that names the subject.
    """
    srcs = [s for s in sources if s is not None]
    hay = _premise_haystack(srcs)
    tokens = set(re.findall(r"[a-z0-9']+", hay))
    q_words = [w.removesuffix("'s") for w in re.findall(r"[a-z0-9']+", question.lower())]
    for w in q_words:
        if w in _MONTHS:
            n = _MONTHS[w]
            if w not in hay and w[:3] not in hay and not re.search(rf"[-/](0?{n})[-/]", hay) and not re.search(rf"\b(0?{n})[./]\d", hay):
                return f"the sources never mention {w}"
        elif (w in _WEEKDAYS and w[:3] not in hay) or (re.fullmatch(r"(19|20)\d\d", w) and w not in hay):
            return f"the sources never mention {w}"
    if judge is None or not srcs:
        return None
    seen = srcs + [s for s in (retrieved or []) if s is not None and s.confidence == "strong"]
    hay = _premise_haystack(seen)
    tokens = set(re.findall(r"[a-z0-9']+", hay))
    a_words = {w.removesuffix("'s") for w in re.findall(r"[a-z][a-z']+", answer.lower())}
    dated = set(_MONTHS) | _WEEKDAYS
    candidates: list[str] = []
    for w in q_words:
        if len(w) < 3 or w in _PREMISE_FILLER or w in dated or w.isdigit() or w in candidates:
            continue
        if w in a_words and not _mentioned(w, hay, tokens):
            candidates.append(w)
    for w in candidates[:PREMISE_JUDGE_MAX_WORDS]:
        if not any(judge(w, f"{s.filename}\n{s.text or ''}") for s in seen[:PREMISE_JUDGE_MAX_SOURCES]):
            return f"\u201c{w}\u201d is in the question and the answer but no source mentions it"
    return None


def _ground(text: str, tools: Toolbox) -> list[int]:
    """Sources whose text the answer clearly draws on: at least three
    distinctive answer words (≥ 4 letters, or a number) appear in the
    source, or most of them do. Numbers count double — an amount, a date
    or a reference copied from a source is the strongest evidence."""
    distinctive = _distinctive(text)
    if not distinctive:
        return []
    scored = []
    for source in tools.sources:
        hay = source.text.lower()
        hits = [w for w in distinctive if w in hay]
        score = sum(2 if any(ch.isdigit() for ch in w) else 1 for w in hits)
        if score >= 3 or (hits and len(hits) / len(distinctive) >= 0.5):
            scored.append((score, source.number))
    scored.sort(reverse=True)
    return [n for _, n in scored[:2]]


def _answer_words(text: str, source) -> dict[str, int]:
    """Distinctive answer words this source contains, weighted like _ground
    (a number counts double)."""
    hay = source.text.lower() if source is not None else ""
    return {w: 2 if any(ch.isdigit() for ch in w) else 1 for w in _distinctive(text) if w in hay}


def _trim_citations(text: str, numbers: list[int], tools: Toolbox) -> list[int]:
    """Keep a citation only if its source adds something to the answer
    (improvement 3, 2026-09-26). The live Windows run cited the April
    invoice next to the March one for a March-invoice question: both share
    "invoice", "due", "euros" and the answer's April due date, so both pass
    _ground. Greedy cover: the source covering the most answer words is
    kept, then each further one only if it adds weight >= MIN_CITATION_GAIN
    of answer words the kept ones lack — a two-part answer drawing on two
    files keeps both. Order follows the answer's own citations."""
    if len(numbers) < 2:
        return numbers
    covers = {n: _answer_words(text, tools.get(n)) for n in numbers}
    ranked = sorted(numbers, key=lambda n: -sum(covers[n].values()))
    kept, covered = [ranked[0]], set(covers[ranked[0]])
    for n in ranked[1:]:
        gain = sum(weight for w, weight in covers[n].items() if w not in covered)
        if gain >= MIN_CITATION_GAIN:
            kept.append(n)
            covered |= set(covers[n])
    return [n for n in numbers if n in kept]


MIN_CITATION_GAIN = 2

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD_RE = re.compile(r"[a-z0-9]+")
QUICK_MIN_OVERLAP = 2  # question words a sentence must share to be shown as the quick answer
QUICK_SOURCES = 3      # strong sources whose sentences compete for the quick answer


def _quick_answer(question: str, found: list) -> tuple[str, object] | None:
    """The sentence of the best strong source that shares the most words
    with the question, shown while the model is still thinking (improvement
    3). No model involved: it is retrieval, labelled as such in the UI, and
    replaced by the real answer. None when nothing is close enough."""
    q = {w for w in _WORD_RE.findall(question.lower()) if len(w) >= 3 and w not in _PREMISE_FILLER}  # 3 letters: "due", "fee"
    q |= {w[:-1] for w in q if w.endswith("s") and len(w) > 4}  # "invoices" ~ "invoice"
    if not q:
        return None

    def covered_by_name(source) -> set[str]:
        named = {n for n in _WORD_RE.findall(Path(source.filename).stem.lower()) if len(n) >= 3}
        return {w for w in q if any(n.startswith(w) or w.startswith(n) for n in named)}

    # The top strong sources each offer their best sentence; the winner is
    # the best sentence plus half a point per question word the file's name
    # covers. Measured 2026-09-26 on the 30-question key: taking only the
    # first strong source quoted the April invoice for "the March invoice";
    # sorting by name alone let "monthly expenses.csv" ("month") push out the
    # savings plan. Weak sources are never quoted: they would mislead.
    dated = {w for w in q if w in _MONTHS or w in _WEEKDAYS}
    need = min(QUICK_MIN_OVERLAP, len(q))  # "how much was the deposit" has one distinctive word
    choice, choice_score = None, 0.0
    for source in [s for s in found if s.confidence == "strong" and s.text][:QUICK_SOURCES]:
        # Words the file's name already answers ("march" for march invoice.txt)
        # need not be repeated in the sentence (packaged-app check 2026-09-26:
        # "when is the march invoice due" got no quick answer because the
        # invoice text never says "March"); inside the sentence they count
        # half, since the name already says them.
        by_name = covered_by_name(source)
        for sentence in _SENTENCE_RE.split(source.text):
            sentence = sentence.strip(" -•\t")
            if not 3 <= len(sentence.split()) <= 60:
                continue
            words = set(_WORD_RE.findall(sentence.lower()))
            hits = {w for w in q if w in words or any(t.startswith(w) for t in words)}
            if not hits or len(hits | by_name) < need:  # the sentence itself must say something asked
                continue
            if any(d not in hits and d not in by_name for d in dated):
                continue  # the question's month/weekday ("in September") is not what this sentence is about
            score = (len(hits - by_name) + 0.5 * len(hits & by_name)
                     + 0.5 * bool(_NUMBER_RE.search(sentence))  # facts people ask for are mostly figures and dates
                     + 0.5 * len(by_name))
            if score > choice_score:
                choice, choice_score = (sentence, source), score
    return choice


def _json_object(text: str) -> dict | None:
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _as_text(value) -> str:
    """A plan field as text: a list becomes its words, an object or null nothing."""
    if isinstance(value, list):
        return " ".join(_as_text(v) for v in value).strip()
    if value is None or isinstance(value, dict):
        return ""
    return str(value)


def _parse_plan(raw: str) -> dict:
    """The model is asked for one JSON object; be tolerant of stray text.
    Only an object is a plan, and its fields are coerced to what the loop
    expects (2026-10-04): JSON that parsed to a list or a number, a query
    written as a list and a source written as "[2]" each raised inside the
    loop and ended Ask with a server error. A read_more whose source holds
    no number is malformed, like anything unparseable: answer now."""
    raw = raw.strip()
    plan = _json_object(raw)
    if plan is None:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        plan = _json_object(match.group(0)) if match else None
    malformed = {"action": "answer", "thought": "", "malformed": True}
    if plan is None:
        return malformed
    for key in ("thought", "query", "filters"):
        plan[key] = _as_text(plan.get(key))
    number = re.search(r"\d+", _as_text(plan.get("source")))
    plan["source"] = int(number.group(0)) if number else None
    if plan["source"] is None and _ACTION_ALIASES.get(str(plan.get("action", "")).strip().lower()) == "read_more":
        return malformed
    return plan
