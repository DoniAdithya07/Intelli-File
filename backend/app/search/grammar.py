"""Rule-based query tidying — the cheap, safe part of "grammar correction".

Deliberately not a model: keyword search ignores grammar, and the meaning
path tolerates it (prototype_typo_tolerance.py), so a local grammar model
would add hundreds of MB to the installer for no measurable search gain.
Capitalisation is also left alone on purpose — it never changes results,
and "correcting" it would make the Did-you-mean chip fire on nearly every
query.
"""

import re

_DOUBLED_WORD_RE = re.compile(r"\b(\w+)(\s+\1\b)+", re.IGNORECASE)
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;!?])")
# Only after a comma/semicolon and only before a letter: colons stay put
# ("type:pdf", "10:30") and so do decimals ("3.5").
_PUNCT_NO_SPACE_RE = re.compile(r"([,;])(?=[^\W\d_])")
_MULTI_SPACE_RE = re.compile(r"\s{2,}")


def tidy_query(text: str) -> str:
    """Collapse doubled words ("the the bread" -> "the bread"), fix spacing
    around punctuation ("bread ,making" -> "bread, making"), and squeeze
    repeated spaces. Returns the text unchanged if nothing applies."""
    tidied = _DOUBLED_WORD_RE.sub(r"\1", text)
    tidied = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", tidied)
    tidied = _PUNCT_NO_SPACE_RE.sub(r"\1 ", tidied)
    tidied = _MULTI_SPACE_RE.sub(" ", tidied)
    return tidied.strip()
