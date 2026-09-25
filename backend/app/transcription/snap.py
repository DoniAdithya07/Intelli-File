"""Snap a voice transcript to a file name it *sounds like*. Added 2026-09-20.

Whisper base.en, given a bare two-word utterance with an uncommon compound
word, picks the common-word spelling: "landlord letter" → "Learn Lord
letter", "gym plan" → "Chimp plan". The file-name prompt (see
Transcriber.transcribe) biases it but cannot force it — measured: even
with "landlord letter" as the ONLY prompt word the output was unchanged,
and beam search made no difference. Search then gets a query that matches
nothing, because the filename matcher needs at least half the words.

So this runs AFTER Whisper, on text only: every window of the transcript
is compared with every indexed file name on two similarity measures —
plain characters, and a phonetic key that ignores vowels and merges
voiced/unvoiced consonant pairs (the errors are acoustic, so "learn lord"
and "landlord" share almost the same key). A window that is close enough
to a name is replaced by that name. The caller gets both the snapped and
the raw text so the UI can show what happened and let the user undo it.

Thresholds were set by measurement on scripts/prototype_transcription.py's
positive set and the demo voice-note sentences (which must never change) —
see the README's Phase 7 entry for the numbers.
"""

import re
from difflib import SequenceMatcher

_WORD_RE = re.compile(r"[A-Za-z0-9']+")

# A name shorter than this can't carry enough signal to snap safely —
# "no" vs "go" is one letter and any short window would match something.
MIN_NAME_LETTERS = 5
# Accept a snap when the phonetic keys are close and the letters are not
# wildly different (measured: "chin plan"→"gym plan" is 0.80/0.53, "plan
# lot later"→"landlord letter" 0.82/0.69; the nearest ordinary phrase,
# "plan" vs "gym plan", is 0.75/0.73), or when either measure alone is
# near-identical.
PHONETIC_THRESHOLD = 0.80
CHAR_THRESHOLD = 0.50
STRONG_THRESHOLD = 0.90
# The replaced words on their own must also sound like their replacement:
# "notes about" vs "notes budget" scores 0.83 as a whole because "notes"
# matches, but "about"/"budget" is 0.57. "learn lord"/"landlord" is 0.83.
RESIDUAL_THRESHOLD = 0.75
# If two different names both fit, the utterance is ambiguous — leave it.
AMBIGUITY_MARGIN = 0.05

_PAIRS = str.maketrans({"b": "p", "d": "t", "g": "k", "v": "f", "z": "s"})


def phonetic_key(text: str) -> str:
    """Consonant skeleton: lowercase letters only, digraphs collapsed,
    voiced/unvoiced pairs merged, doubled letters collapsed, vowels dropped
    except a leading one. Not Metaphone — just enough to make acoustic
    near-misses collide."""
    # Digits are kept: "demo wedding 3" must not collide with "meeting".
    s = re.sub(r"[^a-z0-9]", "", text.lower())
    if not s:
        return ""
    for src, dst in (("ph", "f"), ("ck", "k"), ("sh", "x"), ("ch", "j"), ("th", "0"), ("wr", "r"), ("kn", "n"), ("gh", "")):
        s = s.replace(src, dst)
    s = re.sub(r"g(?=[eiy])", "j", s)
    s = re.sub(r"c(?=[eiy])", "s", s).replace("c", "k").replace("q", "k").replace("x", "ks")
    s = s.translate(_PAIRS)
    lead = s[0] if s[0] in "aeiou" else ""
    s = lead + re.sub(r"[aeiouyhw]", "", s[len(lead):])
    if not s:
        return lead
    return re.sub(r"(.)\1+", r"\1", s)


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _search_already_matches(window_words: list[str], name_words: list[str]) -> bool:
    """Mirror of search/filenames.py's rule: if at least half the name's
    words are already present (exact or prefix) and at least two of them
    (or the name is one word), the filename matcher will find the file from
    the raw transcript — so "video of the steam train" must NOT be rewritten
    to "demo video steam train 1". Snapping is only for what search would
    otherwise miss."""
    matched = sum(
        1 for n in name_words
        if any(w == n or (len(n) >= 3 and (w.startswith(n) or n.startswith(w)) and min(len(w), len(n)) >= 3) for w in window_words)
    )
    return matched * 2 >= len(name_words) and (matched >= 2 or len(name_words) == 1)


def _residual_is_evidence(window_words: list[str], name_words: list[str], vocabulary: set[str]) -> bool:
    """The words that would actually be replaced must look like a
    mishearing, not a real word: "my plan" is a legitimate query, not
    "gym plan" misheard, so a residual of one or two letters is rejected
    unless its phonetic key is identical to the name's residual ("Jim"
    vs "gym" → both `jm`). And a residual that is itself a word of ANY
    file name was heard right by definition — "monthly expenses" must not
    become "monthly explain" because explain.py exists (live, 2026-09-20).
    Only when EVERY replaced word is vocabulary, though: "plan lot later"
    → "landlord letter" replaces a vocabulary word ("plan") along with two
    that aren't, and that is a genuine mishearing."""
    residual_w = [w for w in window_words if w not in name_words]
    residual_n = [n for n in name_words if n not in window_words]
    if not residual_w or not residual_n:
        return False
    if all(w in vocabulary for w in residual_w):
        return False
    key_w, key_n = phonetic_key("".join(residual_w)), phonetic_key("".join(residual_n))
    if key_w == key_n:
        return True
    return len("".join(residual_w)) >= 4 and _ratio(key_w, key_n) >= RESIDUAL_THRESHOLD


def _score(window_words: list[str], name_words: list[str], name_key: str, vocabulary: set[str]) -> float:
    joined_w = "".join(window_words)
    joined_n = "".join(name_words)
    # A window much shorter than the name is a fragment ("plan" for "gym plan").
    if len(joined_w) * 10 < len(joined_n) * 6:
        return 0.0
    if not _residual_is_evidence(window_words, name_words, vocabulary):
        return 0.0
    window_key = phonetic_key(joined_w)
    # A one-word name has a short key where one shared consonant is a
    # big share ("expenses"/"explain" = 0.83), so it must match strongly.
    phon_needed = STRONG_THRESHOLD if len(name_words) == 1 else PHONETIC_THRESHOLD
    # quick_ratio() is an upper bound — cheap way to skip hopeless pairs
    # when the name list is long.
    if _quick(joined_w, joined_n) < CHAR_THRESHOLD:
        return 0.0
    char = _ratio(joined_w, joined_n)
    if char < CHAR_THRESHOLD:
        return 0.0
    if char >= STRONG_THRESHOLD:
        return char
    # The phonetic key is deliberately lossy ("video call" and "photo kilo"
    # both come out as `ftkl`), so it is never sufficient on its own —
    # the letters above must already agree moderately.
    if SequenceMatcher(None, window_key, name_key).quick_ratio() < phon_needed:
        return 0.0
    phon = _ratio(window_key, name_key)
    if phon >= phon_needed:
        return (phon + char) / 2
    return 0.0


def _quick(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).quick_ratio() if a and b else 0.0


def snap_to_filenames(transcript: str, names: list[str]) -> tuple[str, str | None]:
    """Returns (text_to_search, raw_transcript_if_changed). The raw text is
    None when nothing was snapped, so callers can tell the two apart."""
    words = _WORD_RE.findall(transcript)
    if not words:
        return transcript, None
    lowered = [w.lower() for w in words]

    split_names = [[w.lower() for w in _WORD_RE.findall(name)] for name in names]
    vocabulary = {w for name_words in split_names for w in name_words}

    best: tuple[float, int, int, str] | None = None  # score, start, end, name
    runner_up = 0.0  # best score from a DIFFERENT name than `best`
    for name, name_words in zip(names, split_names):
        if len("".join(name_words)) < MIN_NAME_LETTERS:
            continue
        name_key = phonetic_key("".join(name_words))
        k = len(name_words)
        # Whisper may split or merge words ("landlord" → "Learn Lord"), so
        # windows one word shorter or two longer than the name are tried.
        for size in range(max(1, k - 1), k + 3):
            # Snapping is for a spoken file name, alone or with a couple of
            # filler words — the window must be at least half the utterance.
            # A two-word phrase inside a dictated sentence is left alone,
            # which also keeps a large name pool from finding coincidences.
            if size * 2 < len(lowered):
                continue
            for start in range(0, len(lowered) - size + 1):
                window = lowered[start : start + size]
                if window == name_words or _search_already_matches(window, name_words):
                    continue  # search finds this already — never rewrite it
                score = _score(window, name_words, name_key, vocabulary)
                if not score:
                    continue
                if best is None or score > best[0]:
                    if best is not None and best[3] != name:
                        runner_up = max(runner_up, best[0])
                    best = (score, start, start + size, name)
                elif best[3] != name:
                    runner_up = max(runner_up, score)

    if best is None or best[0] - runner_up < AMBIGUITY_MARGIN:
        return transcript, None
    _, start, end, name = best
    replaced = words[:start] + [name] + words[end:]
    return " ".join(replaced), transcript
