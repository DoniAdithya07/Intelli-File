"""Filename matching for search.

Found by the user's live test (2026-09-11): "abhisek plan" returned
README.md and "rasmalai" returned nothing at all, although files named
exactly `abhisek plan.txt` and `rasmalai.txt` were indexed. Search only
ever looked at *content*, so a file whose name is the whole point of the
query was invisible — the one thing the old File Explorer could do that
this couldn't. Names carry meaning the content often doesn't (rasmalai.txt
is a sourdough recipe inside).
"""

import re
import unicodedata
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path

from ..files.discovery import AUDIO_EXTENSIONS
from ..files.identity import FileRecord
from .spelling import _levenshtein

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_TYPED_EXTENSION_RE = re.compile(r"[a-z0-9]\.([a-z0-9]{1,5})(?![a-z0-9])")


def _fold(text: str) -> str:
    """Without accents, matching how search/service.py folds the content
    index ("café" -> "cafe") — otherwise the regex below splits "café" into
    "caf" and a query for "cafe resume" never finds "Café résumé...".
    """
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))

# Words that carry no filename signal in a spoken/typed request:
# "open the abhisek plan file" should match on "abhisek" and "plan" only.
_QUERY_NOISE = {
    "a", "an", "the", "my", "me", "of", "for", "to", "in", "on", "and", "or", "with", "about",
    "open", "show", "find", "get", "search", "look", "up", "please",
}
# Words that name a KIND of file. Next to real words they are framing ("notes
# about scaling servers" must not promote every *notes* file above the
# answer), but on their own they are the name the user remembers: until
# 2026-10-05 they were plain noise, and "notes" found neither `meeting
# notes.txt` nor `notes.odt` by name.
_GENERIC_NAME_WORDS = {
    "file", "files", "document", "documents", "note", "notes", "folder", "photo", "photos", "picture", "pictures", "image",
    # Added 2026-10-12 with the photo-search fix: "animated photos" put `day-photos.png` and `night-photos.png`
    # above every picture, because a file name carrying the filler word counted as a hit for it.
    "images", "photograph", "photographs", "pic", "pics", "snap", "snaps", "shot", "shots",
}


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(_fold(text).lower())


@lru_cache(maxsize=262144)
def _name_tokens(path: str) -> tuple[list[str], str]:
    """(stem tokens, extension). Re-parsing every path on every query was
    O(files) pathlib work per search."""
    name = Path(path)
    return _tokens(name.stem), name.suffix.lower().lstrip(".")


_AUDIO_EXTENSION_NAMES = {e.lstrip(".") for e in AUDIO_EXTENSIONS}


@lru_cache(maxsize=262144)
def _folder_tokens(path: str) -> list[str]:
    return _tokens(Path(path).parent.name)


def _word_in_name(word: str, name_tokens: list[str]) -> bool:
    for token in name_tokens:
        if token == word or (len(word) >= 4 and token.startswith(word)):
            return True
        # One-letter slips on real words ("abhisek" vs "abhishek"), never on
        # short tokens where one edit changes the word entirely ("plane" ->
        # "plan" matched `abhisek plan.txt` in the 2026-09-11 live test).
        if len(word) >= 6 and abs(len(token) - len(word)) <= 1 and _levenshtein(word, token, 1) <= 1:
            return True
    return False


@dataclass
class FilenameMatch:
    record: FileRecord
    matched_words: list[str]
    fraction: float  # matched / meaningful query words


def filename_vocabulary(records: list[FileRecord]) -> dict[str, int]:
    """Words from file names, in the corrector's vocabulary shape. Merged
    into the content vocabulary so a name that exists only as a filename
    ("naruto") isn't "corrected" into an unrelated content word ("auto") —
    which happened in the live test and sent the query to the wrong file."""
    vocab: dict[str, int] = {}
    for record in records:
        for token in _tokens(Path(record.path).stem):
            if len(token) >= 3:
                vocab[token] = vocab.get(token, 0) + 1
    return vocab


def match_filenames(query_text: str, records: list[FileRecord]) -> list[FilenameMatch]:
    """Files whose name covers the query. A match needs at least half the
    meaningful query words present in the name AND either two matched
    words or a single-word query — so "recipe for pasta" does not promote
    `bread_recipe.txt` on the strength of "recipe" alone, while "rasmalai"
    does find `rasmalai.txt`. Best coverage first."""
    words = [w for w in _tokens(query_text) if w not in _QUERY_NOISE and len(w) >= 2]
    typed_extensions = set(_TYPED_EXTENSION_RE.findall(query_text.lower()))  # "odt" in "notes.odt"
    specific = [w for w in words if w not in _GENERIC_NAME_WORDS and w not in typed_extensions]
    if specific:  # generic words count only when they are all there is ("notes", "notes.odt")
        words = specific + [w for w in words if w in typed_extensions]
    if not words:
        return []
    matches = []
    for record in records:
        name_tokens, extension = _name_tokens(record.path)
        if record.audio_only or extension in _AUDIO_EXTENSION_NAMES:
            # A song or a voice note is found by its title or the album/folder it
            # sits in (2026-10-12): its text is a transcript that, for music, is
            # nothing. Documents keep matching on their own name only.
            name_tokens = name_tokens + _folder_tokens(record.path)
        matched = [w for w in words if _word_in_name(w, name_tokens)]
        # The extension counts only next to a matched name word: "notes.odt"
        # puts notes.odt above `meeting notes.txt`, while "pdf" alone still
        # does not turn every PDF into a name match.
        if matched and extension in words and extension not in matched:
            matched.append(extension)
        fraction = len(matched) / len(words)
        if fraction >= 0.5 and (len(matched) >= 2 or len(words) == 1):
            matches.append(FilenameMatch(record, matched, fraction))
    matches.sort(key=lambda m: (-m.fraction, -len(m.matched_words), Path(m.record.path).name.lower()))
    return matches
