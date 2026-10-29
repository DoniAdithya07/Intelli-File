"""Pure-logic regression for the Ask answer's not_found/grounded decision in
agent/loop.py (2026-10-29 review): a short citation-stripped body ("No .",
"250 euros.") used to be marked not_found by word count alone, which forced
grounded = False even when the citation was genuinely valid -- undermining
the "grounded" accuracy metric the project's own benchmarks report. Fixed by
only treating a short body as not_found when nothing actually grounds it.
No model/backend needed: imports the real helper functions, mirrors the
boolean expression at loop.py's Agent._answer (kept in sync by hand, since
that expression lives inline in a generator method, not its own function).
Exits non-zero on failure."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.loop import _answer_incomplete, says_no_answer


def not_found(body: str, valid: list[int], no_answer: bool = False) -> bool:
    return "couldn't find that" in body.lower() or (len(body.split()) < 3 and not valid) or no_answer or _answer_incomplete(body)


def main() -> None:
    # A short answer backed by a real citation must be kept.
    assert not not_found("No .", valid=[1]), "short grounded answer wrongly marked not_found"
    assert not not_found("Yes.", valid=[2]), "short grounded answer wrongly marked not_found"
    # A short answer with nothing grounding it is still a degenerate output.
    assert not_found("No .", valid=[]), "degenerate short ungrounded answer should still be caught"
    assert not_found(".", valid=[]), "empty body should still be caught"
    # A longer answer's word count never triggers not_found either way (an
    # ungrounded longer answer is caught downstream by the empty-valid check
    # in loop.py's Agent._answer, outside the scope of this expression).
    assert not not_found("The rent is 250 euros.", valid=[1])
    assert not not_found("The rent is 250 euros.", valid=[])
    # says_no_answer / _answer_incomplete still gate independent of length.
    assert not_found("The source does not provide a direct answer to this.", valid=[1], no_answer=says_no_answer("The source does not provide a direct answer to this."))
    assert not_found("The date VMBox was added to the laptop is .", valid=[1])  # _answer_incomplete

    print("check_not_found_logic: OK")


if __name__ == "__main__":
    main()
