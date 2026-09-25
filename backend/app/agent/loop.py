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

Limits: MAX_TOOL_CALLS tool calls and BUDGET_SECONDS wall-clock per
question; the model only ever sees the retrieved snippets, never files.
Every step is emitted as an event so the UI can show the trace.
"""

import json
import re
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from .tools import Toolbox

MAX_TOOL_CALLS = 4
BUDGET_SECONDS = 25.0
MAX_ANSWER_TOKENS = 350
MAX_PLAN_TOKENS = 220

_CITATION_RE = re.compile(r"\[(\d{1,2})\]")

PLANNER_SYSTEM = """You are the planning step of IntelliFile, a search assistant over the user's own files on this computer.
You cannot read files directly. You have tools:
- search: run a search. Arguments: "query" (a short search phrase — rewrite the question into the words a document would contain), "mode" ("auto" lets the router pick; "keyword" for exact words; "smart" for meaning), optional "filters" (e.g. "type:pdf", "after:2025-03", "in:invoices", "ext:csv").
- read_more: read more of a numbered source. Arguments: "source" (its number).
- answer: stop searching and write the answer from the sources found so far.
Rules: split a multi-part question into separate searches. Prefer "keyword" when the question quotes exact words or names; "smart" for concepts. Use "filters" ONLY when the question itself names a file type, a folder or a date — otherwise leave filters empty. When results contain the answer, choose "answer". After at most {max_calls} tool calls you must answer.
Respond with ONE JSON object only: {{"thought": "...", "action": "search"|"read_more"|"answer", "query": "...", "mode": "...", "filters": "...", "source": 0}}"""

ANSWER_SYSTEM = """You are IntelliFile's answer step. The numbered sources are excerpts from the user's own files; a source may be a table flattened into one line (column names first, then rows). Answer the question in one or two sentences using the facts in the sources, and end each sentence with the number of the source it came from in brackets, e.g. [2]. Copy numbers, dates and names exactly as they appear. Never answer from memory."""
# Deliberately no "say you couldn't find it" instruction: measured
# 2026-09-21, a 1.5B model reaches for that escape hatch even with the
# answer in front of it. Grounding is the harness's job (see _ground):
# an answer that no retrieved source supports is rejected, whatever the
# model claims.


class Agent:
    def __init__(self, llm, toolbox_factory: Callable[[], Toolbox], max_tool_calls: int = MAX_TOOL_CALLS, budget_seconds: float = BUDGET_SECONDS):
        self.llm = llm
        self.toolbox_factory = toolbox_factory
        self.max_tool_calls = max_tool_calls
        self.budget_seconds = budget_seconds

    def _mentions(self, word: str, text: str) -> bool:
        """One-word yes/no from the model: does this source mention or give a <word>?"""
        messages = [
            {"role": "system", "content": "You check whether a text is about something. Answer with one word: yes or no."},
            {"role": "user", "content": f"Text:\n{text[:PREMISE_JUDGE_CHARS]}\n\nDoes this text mention or give a {word}? Answer yes or no."},
        ]
        try:
            return self.llm.chat(messages, max_tokens=3).strip().lower().startswith("yes")
        except Exception:  # noqa: BLE001 — a judge failure must never block an answer
            return True

    def run(self, question: str) -> Iterator[dict]:
        """Yields trace events: {"type": ...}. Types: context, thought,
        tool_call, tool_result, answer_start, token, answer, done, error."""
        t_start = time.perf_counter()
        tools = self.toolbox_factory()
        question = question.strip()
        context = tools.context()
        yield {"type": "context", "context": context}

        messages = [
            {"role": "system", "content": PLANNER_SYSTEM.format(max_calls=self.max_tool_calls)},
            {"role": "user", "content": self._first_user_message(question, context)},
        ]
        calls = 0       # tool calls actually executed
        turns = 0       # planning turns, including ones that led nowhere
        strategies: list[dict] = []
        seen_searches: set[tuple[str, str, str]] = set()
        fallback_used = False
        while True:
            elapsed = time.perf_counter() - t_start
            if calls >= self.max_tool_calls or turns >= self.max_tool_calls + 2 or elapsed > self.budget_seconds:
                reason = "tool-call limit reached" if calls >= self.max_tool_calls else "time budget reached" if elapsed > self.budget_seconds else "planning limit reached"
                yield {"type": "thought", "text": f"{reason} — answering with what was found.", "system": True}
                break
            turns += 1
            t0 = time.perf_counter()
            try:
                raw = self.llm.chat(messages, max_tokens=MAX_PLAN_TOKENS, json_only=True)
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
                        yield {"type": "thought", "text": "the plan repeated itself — searching the whole question instead", "system": True}
                        query, mode, filters = question, "auto", ""
                        signature = (query.lower(), mode, filters)
                    else:
                        yield {"type": "thought", "text": "that exact search already ran — asking for different words or an answer", "system": True}
                        messages.append({"role": "assistant", "content": json.dumps(plan)})
                        messages.append({"role": "user", "content": f"That exact search already ran and its results are above. Either search with DIFFERENT words (or a different mode), or choose \"answer\" if the sources contain the answer. Tool calls used: {calls} of {self.max_tool_calls}. Next JSON:"})
                        continue
                calls += 1
                seen_searches.add(signature)
                yield {"type": "tool_call", "tool": "search", "args": {"query": query, "mode": mode, "filters": filters}, "call": calls}
                found, route = tools.search(query, mode=mode, filters=filters)
                if route.get("fallback_from_mode"):
                    yield {"type": "thought", "text": f"{route['fallback_from_mode']} mode found nothing — retried with auto routing ({route['tier']})", "system": True}
                if not found and filters:
                    # A filter the question did justify can still be wrong
                    # for this index (no PDFs at all, say): retry unfiltered
                    # rather than burn the whole budget on empty results.
                    yield {"type": "thought", "text": f"nothing matched with filters \u201c{filters}\u201d — retrying without them", "system": True}
                    found, route = tools.search(query, mode=mode, filters="")
                    filters = ""
                strategies.append({"query": query, "mode": mode, "filters": filters, "route": route["tier"], "results": len(found)})
                observation = tools.render(found)
                yield {"type": "tool_result", "tool": "search", "sources": [s.as_dict() for s in found], "route": route, "call": calls}
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
                yield {"type": "thought", "text": "the plan was not a valid tool call — answering with what was found.", "system": True}
            break

        if not tools.sources:
            # Nothing was ever retrieved: no model turn can be grounded.
            answer = "I couldn't find that in your files."
            yield {"type": "answer_start"}
            yield {"type": "token", "text": answer}
            yield {"type": "answer", "text": answer, "citations": [], "grounded": False}
            yield {"type": "done", "seconds": round(time.perf_counter() - t_start, 1), "tool_calls": calls, "strategies": strategies, "sources": [s.as_dict() for s in tools.sources]}
            return

        answer_messages = [
            {"role": "system", "content": ANSWER_SYSTEM},
            {"role": "user", "content": f"Question: {question}\n\nSources:\n{tools.render(tools.sources)}\n\nAnswer with citations:"},
        ]
        yield {"type": "answer_start"}
        pieces: list[str] = []
        try:
            for token in self.llm.stream(answer_messages, max_tokens=MAX_ANSWER_TOKENS):
                pieces.append(token)
                yield {"type": "token", "text": token}
                if time.perf_counter() - t_start > self.budget_seconds * 2:
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
        supported = _ground(body, tools)
        valid = [n for n in dict.fromkeys(cited_numbers) if n in supported]
        not_found = "couldn't find that" in text.lower() or len(body.split()) < 3
        inferred = False
        if not valid and not not_found and supported:
            valid = supported
            inferred = True
        if valid:
            text = f"{body} {' '.join(f'[{n}]' for n in valid)}" if inferred else _CITATION_RE.sub(lambda m: m.group(0) if int(m.group(1)) in valid else "", text)
        if not valid:
            if not not_found:
                yield {"type": "thought", "text": "the answer could not be traced to any retrieved source — rejected", "system": True}
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
        if grounded:
            seen = [s for s in tools.sources if s.number not in valid]  # retrieved but not cited, cited ones first
            problem = _unsupported_premise(question, body, [tools.get(n) for n in valid], judge=self._mentions, retrieved=seen)
            if problem:
                yield {"type": "thought", "text": f"the sources don't support the question's premise ({problem}) — declining to answer", "system": True}
                text = "I couldn't find that in your files."
                grounded, valid, not_found = False, [], True
        citations = [tools.get(n).as_dict() for n in valid]
        # Numbers are where a small model slips (measured: "610 euros" in
        # the source became "210 euros" in the answer while every other
        # word matched). Every figure in the answer must appear in a cited
        # source; otherwise the answer is shown with a warning, not as fact.
        warnings = []
        if grounded:
            missing = _unverified_numbers(body, [tools.get(n) for n in valid])
            if missing:
                warnings.append(f"Not in the cited sources — check before relying on it: {', '.join(missing)}")
                yield {"type": "thought", "text": f"figure(s) {', '.join(missing)} in the answer are not in the cited sources — flagged", "system": True}
        yield {"type": "answer", "text": text, "citations": citations, "grounded": grounded, "citations_inferred": inferred, "warnings": warnings}
        yield {"type": "done", "seconds": round(time.perf_counter() - t_start, 1), "tool_calls": calls, "strategies": strategies, "sources": [s.as_dict() for s in tools.sources]}

    @staticmethod
    def _first_user_message(question: str, context: dict) -> str:
        lines = [f"Question: {question}"]
        session = context.get("session") or {}
        if session.get("files"):
            lines.append("The user is currently working with: " + ", ".join(Path(f).name for f in session["files"][:5]))
        if context.get("topics"):
            lines.append("The user's usual topics: " + "; ".join(context["topics"]))
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


def _parse_plan(raw: str) -> dict:
    """The model is asked for one JSON object; be tolerant of stray text."""
    raw = raw.strip()
    try:
        return json.loads(raw)
    except ValueError:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except ValueError:
            pass
    return {"action": "answer", "thought": "", "malformed": True}
