"""Questions about the file collection itself ("how many files are there",
"are there any video files"), answered from the index's own records.

The agent answers from passages inside files; a count or an "is there any
video" is not written in any file, so the model could only guess. The index
knows the exact answer. Only clear collection questions are recognised: a
question about content ("are there any notes about zebras") still goes to
the normal Ask flow. Found from real use on 2026-09-27, when both questions
above got no useful answer.
"""

import re
from collections import Counter
from pathlib import Path

from ..files.discovery import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, TEXT_EXTENSIONS, VIDEO_EXTENSIONS

# kind -> (words people use for it, singular label, plural label, extensions)
KINDS = {
    "document": (r"documents?|docs?|text files?|notes", "document", "documents", TEXT_EXTENSIONS),
    "pdf": (r"pdfs?|pdf files?", "PDF", "PDFs", {".pdf"}),
    "spreadsheet": (r"spreadsheets?|excel files?|csv files?", "spreadsheet", "spreadsheets", {".xlsx", ".xlsm", ".xls", ".ods", ".csv", ".tsv"}),
    "presentation": (r"presentations?|slides?|powerpoints?|pptx files?", "presentation", "presentations", {".pptx", ".ppt", ".odp"}),
    "email": (r"e-?mails?|saved e-?mails?", "email", "emails", {".eml", ".msg"}),
    "ebook": (r"e-?books?|epubs?", "e-book", "e-books", {".epub"}),
    "photo": (r"photos?|pictures?|images?|screenshots?|jpgs?|pngs?", "photo", "photos", IMAGE_EXTENSIONS),
    "video": (r"videos?|video files?|movies?|clips?|mp4s?", "video", "videos", VIDEO_EXTENSIONS),
    "audio": (r"audio(?: files?)?|recordings?|voice notes?|songs?|music|mp3s?", "audio file", "audio files", AUDIO_EXTENSIONS),
}
_KIND_WORDS = "|".join(f"(?P<{k}>{words})" for k, (words, *_rest) in KINDS.items())
_TAIL = r"(?:\s+files?)?(?:\s+(?:here|indexed|in (?:my|the) (?:files|index|folders?)|on (?:this|my) (?:computer|pc|laptop)))?\s*[?.!]*\s*$"

_COUNT_ALL = re.compile(r"^\s*how many (?:files|items|things)(?: are there| do i have| are indexed| have you indexed| in (?:my|the) index)?" + _TAIL, re.I)
_COUNT_KIND = re.compile(r"^\s*how many (?:" + _KIND_WORDS + r")(?: are there| do i have| are indexed| have you indexed)?" + _TAIL, re.I)
_ANY_KIND = re.compile(r"^\s*(?:are there|is there|do i have|have i got|any)(?: any)? (?:" + _KIND_WORDS + r")" + _TAIL, re.I)
_BREAKDOWN = re.compile(r"^\s*what (?:kinds?|types?|sorts?) of files(?: are there| do i have| are indexed)?" + _TAIL, re.I)


def _kind_of(path: str) -> set[str]:
    ext = Path(path).suffix.lower()
    return {k for k, (*_rest, exts) in KINDS.items() if ext in exts}


def _match_kind(m: re.Match) -> str | None:
    return next((k for k in KINDS if m.groupdict().get(k)), None)


def _examples(paths: list[str], n: int = 3) -> str:
    names = [Path(p).name for p in paths[:n]]
    return ", ".join(names) + (" and others" if len(paths) > n else "")


def library_answer(question: str, paths: list[str]) -> str | None:
    """The answer to a question about the collection itself, or None when
    the question is about the files' content."""
    q = question.strip()
    total = len(paths)
    by_kind: dict[str, list[str]] = {k: [] for k in KINDS}
    for p in paths:
        for k in _kind_of(p):
            by_kind[k].append(p)

    if _COUNT_ALL.match(q) or _BREAKDOWN.match(q):
        parts = [f"{len(by_kind[k])} {KINDS[k][2] if len(by_kind[k]) != 1 else KINDS[k][1]}" for k in ("document", "photo", "video", "audio")]
        exts = Counter(Path(p).suffix.lower().lstrip(".") or "no extension" for p in paths)
        top = ", ".join(f"{n} {e.upper()}" for e, n in exts.most_common(4))
        return (f"IntelliFile has indexed {total} file{'s' if total != 1 else ''}: {', '.join(parts[:-1])} and {parts[-1]}."
                + (f" The most common types are {top}." if total else " Add a folder on the Index page to start."))
    for pattern, mode in ((_COUNT_KIND, "count"), (_ANY_KIND, "any")):
        m = pattern.match(q)
        if not m:
            continue
        kind = _match_kind(m)
        if kind is None:
            return None
        found = by_kind[kind]
        singular, plural = KINDS[kind][1], KINDS[kind][2]
        if not found:
            return f"No. None of the {total} indexed file{'s' if total != 1 else ''} is {'an' if singular[0] in 'aeiouAEIOU' else 'a'} {singular}."
        label = singular if len(found) == 1 else plural
        lead = "Yes: " if mode == "any" else ""
        return f"{lead}{len(found)} {label} ({_examples(found)}), out of {total} indexed files."
    return None
