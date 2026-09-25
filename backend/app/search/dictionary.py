"""English word list for checking photo-search queries.

Text search corrects against the words in the user's own files (spelling.py)
— right for documents, useless for photos: a scene description ("a cat in
the snow") is made of ordinary English that usually appears in no text
file. CLIP has no notion of spelling either: gibberish still embeds, and
lands as close to some photos as real matches do (2026-09-19: "xqzvb"
returned ten "strong" results). So photo queries are checked against a
frequency-ranked word list (scripts/download_wordlist.py), plus the user's
own file-name/text vocabulary so names like "naruto" are always accepted.

The list is subtitle-derived and its rare tail contains junk ("cta", 43
occurrences), so membership alone can't mean "correctly spelled": a rare
word is replaced only when a far more common word sits within the same
edit rules text search uses.
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .spelling import LONG_WORD_LENGTH, MAX_EDIT_DISTANCE, MIN_WORD_LENGTH_TO_CORRECT, SWAP_WORD_MIN_LENGTH, _adjacent_swap, _levenshtein

from ..paths import data_dir

DEFAULT_PATH = data_dir() / "english_words.txt"
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)  # underscores split words, as file-name vocabulary does
# Below this count a listed word is "rare": kept unless a much commoner
# neighbour exists. "tarmac" (592) is real and kept; "cta" (43) has "cat".
RARE_COUNT = 500
# A rare word is replaced only by a neighbour at least this many times commoner.
RARE_REPLACE_RATIO = 200


@dataclass(frozen=True)
class QueryCheck:
    text: str  # the query with corrections applied
    corrected: dict[str, str]  # typed word -> replacement
    unrecognized: list[str]  # words with no plausible spelling at all


class Dictionary:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.counts: dict[str, int] = {}
        self._by_length: dict[int, list[tuple[str, int]]] = defaultdict(list)
        with path.open(encoding="utf-8") as f:
            for line in f:
                word, count = line.split()
                self.counts[word] = int(count)
                self._by_length[len(word)].append((word, int(count)))

    @lru_cache(maxsize=4096)
    def _closest(self, word: str, unlisted: bool) -> tuple[str, int] | None:
        """Nearest list word. Text search's rules apply (adjacent swap only
        for short words, one edit up to LONG_WORD_LENGTH, two beyond), with
        two relaxations the dictionary makes safe: swaps from 3 letters
        (check()'s frequency ratio guards "act"/"cat"), and one substitution
        on a 4–5-letter word that is in the list at all ("pizaa" -> "pizza"
        — an unlisted short word is a typo or a name, and names are caught
        by the user's own vocabulary first). Ties go to the commoner word."""
        if len(word) < SWAP_WORD_MIN_LENGTH - 1:
            return None
        # A swap to a *rare* word is not evidence of anything ("cak" -> "ack",
        # 379 occurrences); fall through to the other candidates instead.
        swapped = _adjacent_swap(word, self.counts)
        if swapped and self.counts[swapped] >= RARE_COUNT:
            return swapped, self.counts[swapped]
        if len(word) < MIN_WORD_LENGTH_TO_CORRECT and not (unlisted and len(word) >= SWAP_WORD_MIN_LENGTH):
            return None
        allowed = MAX_EDIT_DISTANCE if len(word) >= LONG_WORD_LENGTH else 1
        best = None  # (distance, -count, word)
        for length in range(len(word) - allowed, len(word) + allowed + 1):
            for candidate, count in self._by_length.get(length, ()):
                # A typo that changes both the first and last letter is rare
                # enough to skip; this keeps the scan to a few percent of the list.
                if candidate[0] != word[0] and candidate[-1] != word[-1]:
                    continue
                distance = _levenshtein(word, candidate, allowed)
                if distance > allowed:
                    continue
                key = (distance, -count, candidate)
                if best is None or key < best:
                    best = key
        return (best[2], -best[1]) if best else None

    def check(self, text: str, user_vocabulary: dict[str, int]) -> QueryCheck:
        corrected: dict[str, str] = {}
        unrecognized: list[str] = []

        def replace(match: re.Match) -> str:
            word = match.group(0)
            lower = word.lower()
            if len(lower) == 1 or lower.isdigit() or lower in user_vocabulary:
                return word
            count = self.counts.get(lower)
            if count is not None and count >= RARE_COUNT:
                return word
            nearest = self._closest(lower, count is None)
            if count is not None:  # rare but listed: keep unless a far commoner neighbour exists
                if nearest and nearest[0] != lower and nearest[1] >= RARE_REPLACE_RATIO * count:
                    corrected[word] = nearest[0]
                    return nearest[0]
                return word
            if nearest:
                corrected[word] = nearest[0]
                return nearest[0]
            unrecognized.append(word)
            return word

        return QueryCheck(text=_TOKEN_RE.sub(replace, text), corrected=corrected, unrecognized=unrecognized)
