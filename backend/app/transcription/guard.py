"""Clean-up of what Whisper says, for text it should never have produced.

On music, noise and long silences Whisper does not stay quiet: it loops
("I am going to say that" several hundred times, found 2026-10-12 on a real
MP3 of a song) or invents the captions it saw in its training videos
("Thank you for watching", "[Music]"). Stored as a transcript that text is
worse than nothing: it fills the keyword index and shows up for searches it
has nothing to do with. The rules here are plain text rules, so they run in
a unit test without the model:

  collapse_repeats  a phrase said many times in a row is kept once
  clean_window      one 30 s window: drop it when it is mostly repeats or
                    only filler, else return it with the repeats collapsed
  clean_transcript  the same collapse over the joined windows
"""

import re
import zlib

MAX_PHRASE_WORDS = 8
# "no no no" is speech; four of the same word, or three of the same phrase, in a row is not.
MIN_RUNS_SINGLE_WORD = 4
MIN_RUNS_PHRASE = 3
# A window that was mostly repeats is the loop itself, not speech with a refrain: even the one
# copy that survives collapsing is made up, so the whole window goes.
MAX_REPEATED_FRACTION = 0.5
MIN_WORDS_TO_JUDGE = 12
# Whisper's own test for a looping segment: it compresses far better than speech does. Whisper
# retries a segment above 2.4; here nothing retries, so only a hopeless one is dropped: five
# repeats of one sentence (a synthetic test clip) measure 4.9, a real loop measures 20 or more.
MAX_COMPRESSION_RATIO = 8.0
MIN_CHARS_FOR_COMPRESSION = 120

_BRACKETED = re.compile(r"\[[^\]]*\]|\([^)]*\)|[♪♫]+")  # [Music] (upbeat music) ♪
_NON_WORD = re.compile(r"[^a-z0-9']+")
# What Whisper writes for silence and music (from its training subtitles). Dropped from FILES only:
# a spoken search query may legitimately be "thank you".
FILLER_WINDOWS = {
    "you", "thank you", "thanks", "thanks for watching", "thank you for watching", "thank you very much",
    "please subscribe", "bye", "bye bye", "so", "oh", "okay", "uh", "um", "hmm", "music", "applause", "laughter",
}


def _key(word: str) -> str:
    return _NON_WORD.sub("", word.lower())


def collapse_repeats(text: str) -> str:
    """Keep one copy of any phrase (1 to MAX_PHRASE_WORDS words) repeated
    several times in a row. Words and punctuation of the kept copy are untouched."""
    words = text.split()
    keys = [_key(w) for w in words]
    kept: list[str] = []
    i = 0
    while i < len(words):
        best_span, best_size = 0, 0
        for size in range(1, MAX_PHRASE_WORDS + 1):
            if i + size > len(words):
                break
            runs = 1
            while keys[i + runs * size:i + (runs + 1) * size] == keys[i:i + size] and i + (runs + 1) * size <= len(words):
                runs += 1
            if runs >= (MIN_RUNS_SINGLE_WORD if size == 1 else MIN_RUNS_PHRASE) and runs * size > best_span:
                best_span, best_size = runs * size, size
        if best_span:
            kept.extend(words[i:i + best_size])
            i += best_span
        else:
            kept.append(words[i])
            i += 1
    return " ".join(kept)


def _compresses_like_a_loop(text: str) -> bool:
    raw = text.encode("utf-8")
    return len(text) >= MIN_CHARS_FOR_COMPRESSION and len(raw) / len(zlib.compress(raw)) > MAX_COMPRESSION_RATIO


def clean_window(text: str, drop_filler: bool = True) -> str:
    """The text of one window, or "" when it is a loop or only filler.
    `drop_filler` is False for a spoken search query."""
    text = _BRACKETED.sub(" ", text).strip()
    words = text.split()
    if not words:
        return ""
    collapsed = collapse_repeats(text)
    if len(words) >= MIN_WORDS_TO_JUDGE and (1 - len(collapsed.split()) / len(words) >= MAX_REPEATED_FRACTION or _compresses_like_a_loop(text)):
        return ""
    if drop_filler and _NON_WORD.sub(" ", collapsed.lower()).strip() in FILLER_WINDOWS:
        return ""
    return collapsed


def clean_transcript(text: str) -> str:
    """The whole file: windows that each looked fine can still repeat one
    another (a song's chorus decoded the same way every 30 s)."""
    return collapse_repeats(text)
