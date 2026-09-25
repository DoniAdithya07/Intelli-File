"""Typo/spelling correction for the keyword (BM25) search path, per the
PRD's Query Understanding goals. Corrects against the vocabulary of words
that actually appear in the user's own indexed files (via KeywordStore's
fts5vocab-backed `vocabulary()`), not a generic bundled English
dictionary — so domain-specific and technical terms the user actually has
(e.g. "Kubernetes", "sourdough") are exactly what corrections target,
rather than being unrecognized.

Only the keyword path gets corrected — the semantic path already
tolerates grammar/spelling mistakes reasonably well since it matches
meaning, not exact characters (see prototype_typo_tolerance.py).
"""

import re

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
MAX_EDIT_DISTANCE = 2
# Below this length a single edit reaches an unrelated real word too easily
# ("plane" -> "plant", "cat" -> "hat") and the corrector has no dictionary
# to know the typed word was fine — it only knows the words in the user's
# files. Raised from 4 to 6 on 2026-09-11 after "remind me" -> "demand me".
MIN_WORD_LENGTH_TO_CORRECT = 6
# Two edits on a short word reaches a *different* word far too easily.
# Found live (2026-09-11): "remind me" became "demand me" — "remind" isn't
# in the user's files (only "reminder" is) and "demand" sits two edits
# away in the README — and the rewritten query then matched nothing.
# One edit for words up to this length, two only for longer ones.
LONG_WORD_LENGTH = 7
# Words shorter than MIN_WORD_LENGTH_TO_CORRECT still get one narrow fix: two
# adjacent letters swapped ("braed" -> "bread", found live 2026-09-19 when
# "braed making" returned nothing). A swap keeps exactly the same letters, so
# unlike a substitution it cannot turn "cat" into "hat" or "plane" into
# "plant" — the false positives the length floor exists to prevent.
SWAP_WORD_MIN_LENGTH = 4


def _allowed_distance(word: str) -> int:
    return MAX_EDIT_DISTANCE if len(word) >= LONG_WORD_LENGTH else 1


def _adjacent_swap(word: str, vocabulary: dict[str, int]) -> str | None:
    """The known word equal to `word` with one adjacent pair swapped, if any
    (the most frequent one when several exist)."""
    best = None
    for i in range(len(word) - 1):
        if word[i] == word[i + 1]:
            continue
        candidate = word[:i] + word[i + 1] + word[i] + word[i + 2:]
        count = vocabulary.get(candidate)
        if count is not None and (best is None or count > best[0]):
            best = (count, candidate)
    return best[1] if best else None


def _levenshtein(a: str, b: str, max_distance: int) -> int:
    """Standard edit distance, with early exit once it's clear the result
    would exceed max_distance (the two strings' length difference alone
    already tells us that in most cases)."""
    if abs(len(a) - len(b)) > max_distance:
        return max_distance + 1

    previous_row = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current_row = [i] + [0] * len(b)
        for j, char_b in enumerate(b, start=1):
            cost = 0 if char_a == char_b else 1
            current_row[j] = min(
                previous_row[j] + 1,  # deletion
                current_row[j - 1] + 1,  # insertion
                previous_row[j - 1] + cost,  # substitution
            )
        previous_row = current_row
    return previous_row[-1]


def _closest_word(word: str, vocabulary: dict[str, int]) -> str | None:
    """Best correction, or None. Ranking: a candidate that *extends* the
    word (remind -> reminder) beats one that merely sits nearby (remind ->
    demand) — a typed word being the stem of a known word is far likelier
    than a typo landing on an unrelated word; then fewer edits; then the
    more frequent word."""
    allowed = _allowed_distance(word)
    best = None  # (is_not_extension, distance, -count, candidate)
    for candidate, count in vocabulary.items():
        extension = candidate.startswith(word) or word.startswith(candidate)
        limit = MAX_EDIT_DISTANCE if extension else allowed
        if abs(len(candidate) - len(word)) > limit:
            continue
        distance = _levenshtein(word, candidate, limit)
        if distance > limit:
            continue
        key = (not extension, distance, -count, candidate)
        if best is None or key < best:
            best = key
    return best[3] if best else None


def correct_query(text: str, vocabulary: dict[str, int]) -> str:
    """Replaces each unknown word with the closest known word from the
    vocabulary, when one exists within MAX_EDIT_DISTANCE. Words already in
    the vocabulary, or too short to safely correct, or with no close
    enough match, are left exactly as typed.
    """
    if not vocabulary:
        return text

    def replace(match: re.Match) -> str:
        word = match.group(0)
        lower = word.lower()
        if lower in vocabulary:
            return word
        if len(lower) < MIN_WORD_LENGTH_TO_CORRECT:
            swapped = _adjacent_swap(lower, vocabulary) if len(lower) >= SWAP_WORD_MIN_LENGTH else None
            return swapped if swapped is not None else word
        correction = _closest_word(lower, vocabulary)
        return correction if correction is not None else word

    return _TOKEN_RE.sub(replace, text)
