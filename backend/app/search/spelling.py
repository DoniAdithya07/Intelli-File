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
from collections.abc import Callable

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
        # Every cell in this row already exceeds the limit and rows never
        # get cheaper: the answer is "too far", no need to finish the grid.
        if min(current_row) > max_distance:
            return max_distance + 1
    return previous_row[-1]


def _closest_word(word: str, vocabulary: dict[str, int]) -> str | None:
    """Best correction, or None. Ranking: a candidate that *extends* the
    word (remind -> reminder) beats one that merely sits nearby (remind ->
    demand) — a typed word being the stem of a known word is far likelier
    than a typo landing on an unrelated word; then fewer edits; then the
    more frequent word."""
    allowed = _allowed_distance(word)
    best = None  # (is_not_extension, distance, -count, candidate)
    # Pigeonhole: with at most `allowed` edits, one of `allowed + 1` pieces of
    # the word survives untouched, so a real near-match contains it verbatim.
    # A C-speed substring test discards almost the whole vocabulary before
    # the (pure Python) edit distance runs; it is exact, not a heuristic.
    cuts = [round(i * len(word) / (allowed + 1)) for i in range(allowed + 2)]
    pieces = [word[cuts[i]:cuts[i + 1]] for i in range(allowed + 1)]
    for candidate, count in vocabulary.items():
        extension = candidate.startswith(word) or word.startswith(candidate)
        limit = MAX_EDIT_DISTANCE if extension else allowed
        if abs(len(candidate) - len(word)) > limit:
            continue
        if not extension and not any(p in candidate for p in pieces):
            continue
        distance = _levenshtein(word, candidate, limit)
        if distance > limit:
            continue
        key = (not extension, distance, -count, candidate)
        if best is None or key < best:
            best = key
    return best[3] if best else None


def _plural_of_known(word: str, vocabulary: dict[str, int]) -> bool:
    """"servers" when the files say "server", "categories" for "category"."""
    for suffix, singular_ending in (("ies", "y"), ("es", ""), ("s", "")):
        if word.endswith(suffix) and word[: -len(suffix)] + singular_ending in vocabulary:
            return True
    return False


def correct_query(text: str, vocabulary: dict[str, int], is_word: Callable[[str], bool] | None = None) -> str:
    """Replaces each unknown word with the closest known word from the
    vocabulary, when one exists within MAX_EDIT_DISTANCE. Words already in
    the vocabulary, or too short to safely correct, or with no close
    enough match, are left exactly as typed. So are the plural of a
    vocabulary word and, with `is_word` (dictionary.Dictionary.is_word), a
    common English word: until 2026-10-05 "servers" became "server" and
    "recipes" "recipe" — the extension rule below reads a plural as a typo
    of its singular — and the search page said "Showing results for server".
    """
    if not vocabulary:
        return text

    def replace(match: re.Match) -> str:
        word = match.group(0)
        lower = word.lower()
        # A token with a digit is a number or an ID (invoice 48213, PO-8812,
        # ref AS-77Q), never a typo: one swap or edit lands on a *different*
        # real number from the same files ("48213" -> "48231", 2026-10-04).
        if lower in vocabulary or any(c.isdigit() for c in lower):
            return word
        if _plural_of_known(lower, vocabulary) or (is_word is not None and is_word(lower)):
            return word
        if len(lower) < MIN_WORD_LENGTH_TO_CORRECT:
            swapped = _adjacent_swap(lower, vocabulary) if len(lower) >= SWAP_WORD_MIN_LENGTH else None
            return swapped if swapped is not None else word
        correction = _closest_word(lower, vocabulary)
        return correction if correction is not None else word

    return _TOKEN_RE.sub(replace, text)
