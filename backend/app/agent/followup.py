"""Follow-up questions ("show me those", "what about the other one") in Ask
mode (2026-10-14). Each question is otherwise answered on its own, with no
memory of the one before it — found live when "can you show me those video"
(after "how many films are there") had nothing to resolve "those" against,
and was correctly rejected as ungrounded rather than guessed.

Resolving a pronoun against the previous turn is a language-understanding
job, so it goes to the same local model Ask already uses, not a hand-written
word list — but only when the question actually looks like it needs it
(`looks_like_followup`), so a plain, self-contained question never pays for
the extra model call and keeps answering as fast as before.
"""

import logging
import re
import time

logger = logging.getLogger(__name__)

_FOLLOWUP_HINT_RE = re.compile(r"\b(it|its|that|those|these|this|them|they|their|theirs|same|again|other|another|previous|else)\b", re.I)
PREV_ANSWER_CHARS = 600
REWRITE_MAX_TOKENS = 80
REWRITE_SECONDS = 10.0
REWRITE_MAX_CHARS = 300  # longer than this, the model is free-associating, not rewriting

FOLLOWUP_SYSTEM = (
    "You rewrite a follow-up question into a standalone one, using the previous question and "
    "answer only to resolve references (\"those\", \"it\", \"that one\", \"the other one\"). Keep "
    "it short and close to the user's own words. If the new question already stands on its own, "
    "reply with it unchanged. Reply with the rewritten question only, nothing else — no quotes, "
    "no explanation."
)


def looks_like_followup(question: str) -> bool:
    """Cheap, local, no model: is a reference word ("those", "it", "the
    other one") present at all? Only then is the (small, but real) cost of
    a rewrite call worth paying."""
    return bool(_FOLLOWUP_HINT_RE.search(question))


def rewrite_followup(llm, prev_question: str, prev_answer: str, question: str) -> str:
    """`question`, resolved against the previous turn if it needs to be.
    Falls back to `question` unchanged on any model failure, empty output,
    or implausibly long output — never blocks or breaks the normal flow."""
    messages = [
        {"role": "system", "content": FOLLOWUP_SYSTEM},
        {"role": "user", "content": f"Previous question: {prev_question}\nPrevious answer: {prev_answer[:PREV_ANSWER_CHARS]}\n\nNew question: {question}\n\nStandalone question:"},
    ]
    try:
        rewritten = llm.chat(messages, max_tokens=REWRITE_MAX_TOKENS, deadline=time.perf_counter() + REWRITE_SECONDS, label="followup").strip().strip('"')
    except Exception:  # noqa: BLE001 — a rewrite failure must never block the question
        logger.warning("follow-up rewrite failed; asking the question as typed", exc_info=True)
        return question
    return rewritten if rewritten and len(rewritten) <= REWRITE_MAX_CHARS else question
