"""Questions about the file collection itself ("how many files are there",
"are there any video files"), answered from the index's own records.

The agent answers from passages inside files; a count or an "is there any
video" is not written in any file, so the model could only guess. The index
knows the exact answer. Only clear collection questions are recognised: a
question about content ("are there any notes about zebras") still goes to
the normal Ask flow. Found from real use on 2026-09-27, when both questions
above got no useful answer.

2026-10-13: the anchored patterns missed "how many movies are theere in the
laptop" (a typo, and "in the laptop"), and the model answered "28 files"
from a screenshot. The question is now read word by word: after the words
that ask for a count, a list or a yes/no, every other word must be the thing
counted, a folder to count in, or a word about where the files are or what
IntelliFile did with them ("in the laptop", "do i have", "did you index").
Any other word means the question is about content.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..files.discovery import AUDIO_EXTENSIONS, CODE_EXTENSIONS, IMAGE_EXTENSIONS, SUPPORTED_EXTENSIONS, TEXT_EXTENSIONS, VIDEO_EXTENSIONS, VISUAL_EXTENSIONS
from ..search.dictionary import shared_dictionary

# kind -> (words people use for it, singular label, plural label, extensions).
# A word's plural with "s" is accepted too. A file-type word (png, mp3, csv)
# is not here: it counts that one type (see _type_of_word), not the kind.
KINDS = {
    "document": ("document doc note", "document", "documents", TEXT_EXTENSIONS),
    "pdf": ("pdf", "PDF", "PDFs", {".pdf"}),
    "spreadsheet": ("spreadsheet excel", "spreadsheet", "spreadsheets", {".xlsx", ".xlsm", ".xls", ".ods", ".csv", ".tsv"}),
    "presentation": ("presentation slide powerpoint", "presentation", "presentations", {".pptx", ".ppt", ".odp"}),
    "email": ("email mail", "email", "emails", {".eml", ".msg"}),
    "ebook": ("ebook", "e-book", "e-books", {".epub"}),
    "photo": ("photo picture pic image", "photo", "photos", IMAGE_EXTENSIONS),
    "video": ("video movie film clip", "video", "videos", VIDEO_EXTENSIONS),
    "audio": ("audio recording song music", "audio file", "audio files", AUDIO_EXTENSIONS),
}
# What a question counts: (singular label, plural label, extensions).
Selection = tuple[str, str, frozenset[str]]
_KIND_OF_WORD: dict[str, Selection] = {
    form: (singular, plural, frozenset(exts)) for words, singular, plural, exts in KINDS.values() for word in words.split() for form in (word, word + "s")
}
# Two words that name one thing ("voice notes" are audio, not the notes of "document").
_AUDIO = _KIND_OF_WORD["audio"]
_TEXT = ("TXT file", "TXT files", frozenset({".txt"}))
_WORD = ("Word document", "Word documents", frozenset({".docx", ".doc"}))
_PAIRS: dict[tuple[str, str], Selection] = {
    **{("voice", w): _AUDIO for w in ("note", "notes")}, **{("text", w): _TEXT for w in ("file", "files")},
    **{("word", w): _WORD for w in ("document", "documents", "doc", "docs", "file", "files")},
}
ALL_EXTENSIONS = SUPPORTED_EXTENSIONS | VISUAL_EXTENSIONS
# Two spellings of one file type.
_SAME_TYPE = [{".jpg", ".jpeg"}, {".tif", ".tiff"}, {".htm", ".html"}, {".mpg", ".mpeg"}, {".aif", ".aiff"}, {".yaml", ".yml"}]


def _type_words() -> dict[str, Selection]:
    """"png" -> PNG files. The plural ("pngs", "mp3s") too, except for code
    types: nobody says "bats" or "javas" and means files."""
    words: dict[str, Selection] = {}
    for ext in ALL_EXTENSIONS:
        name = ext[1:]
        exts = frozenset(next((group for group in _SAME_TYPE if ext in group), {ext}))
        words[name] = (f"{name.upper()} file", f"{name.upper()} files", exts)
    for name in [n for n in words if "." + n not in CODE_EXTENSIONS]:
        words.setdefault(name + "s", words[name])
    return words


_TYPE_OF_WORD = _type_words()
# "files" on its own: the whole collection.
_EVERYTHING = {"file", "files", "item", "items", "thing", "things", "kind", "kinds", "type", "types", "sort", "sorts"}
# Words about where or whose, never about what a file says.
_FILLER = set(
    "a an the my our this that these those all any some of in on at inside within across are is there do i we you have got "
    "laptop computer pc machine system device drive disk here indexed index library collection folder folders total altogether "
    "overall currently now so far right stored saved present available exist exists existing intellifile app me please exactly different".split()
)
# What IntelliFile (or the user) did with the files: "how many files did you
# index", "how many photos has it found". Only next to "files" or a kind of
# file: with any other subject the question is about content.
_ENGINE = set(
    "did does has had it was were be been to index indexes indexing know knows known scan scans scanned scanning find finds found "
    "read reads see sees seen search searches searched searching store stores contain contains hold holds keep keeps track tracks".split()
)
# The general rule (QA, 2026-10-14). Accepting more wordings one by one never
# ends: "how many files went into the index" and seven more still reached the
# model, which answered "60 files" from a report. A count whose subject is
# files (or a kind of file) is about the collection whenever the question
# points at the index, the app or the computer, whatever verb it uses, and
# also when nothing but small words is left. It stays a content question when
# it names a source ("according to the report") or has another content word
# and no such pointer ("how many photos did we take in lisbon").
# QA round 3: a pointer inside another phrase is not one. "the computer
# science syllabus", "the book index", "the pc build" and "a laptop" (any
# laptop) hijacked seven content questions. A pointer counts only when the
# words beside it are small ones, and a device needs "the", "my" or "this".
_POINTERS = {"index", "indexed", "indexing", "indexes", "intellifile", "searchable"}
_DEVICES = {"laptop", "computer", "pc"}
_OWN_DEVICE = {"the", "my", "this", "our", "your"}
# "a laptop", "each computer": any device, so the question is about what a file says.
_ANY_DEVICE = {"a", "an", "each", "every", "any", "one", "new", "old", "another"}
_SOURCE = re.compile(r"\b(?:according|report|reports|reported|say|says|said|mention|mentions|mentioned|written|wrote|state|states|stated|claim|claims|claimed|describe|describes|described)\b")
_SMALL = set(
    "can could would will shall should may might must am been being up out into onto with for by as than then just really actually "
    "end moment today tonight yet still already ever go goes going gone went get gets got getting put puts pick picks picked "
    "listed counted sitting around about roughly approximately when where what how much many and or but if its their your "
    "he she they them his her him who whom us am at after before during while since until once through able cover".split()
)
# Words before the question itself: "so how many", "can you tell me the number of".
_LEAD_IN = set("so ok okay hey hi hello please can could would will you tell me show give do know i want need to wanna just and well now then say let us see check intellifile also but "
               "wonder wondering curious roughly approximately about exactly im am quick question".split())
_COUNT_START = re.compile(r"\b(?:how many|(?:the )?(?:total |exact )?(?:number|count) of)\b")
_FOLDER_WORDS = {"folder", "folders", "directory", "directories"}
_INTO = {"in", "inside", "within", "under", "from"}
# Between "how many" and the thing counted by name, only these may stand.
_DETERMINERS = {"the", "my", "our", "all", "of", "total", "different", "these", "those"}
_FAILED = re.compile(r"\b(?:failed|fail|unreadable|(?:could not|couldnt|can not|cannot|cant|were not|was not) (?:be |get )?read)\b")
_SKIPPED = re.compile(r"\b(?:skipped|ignored|excluded|left out)\b")

_COUNT = re.compile(r"^(?:(?:(?:what is|whats|give me|show me|tell me) )?(?:the )?(?:total |exact )?(?:number|count) of|how many|count|total)\b")
_ANY = re.compile(r"^(?:are there|is there|do i have|do we have|have i got|got|any)\b")
_LIST = re.compile(r"^(?:list|show me|show|display|name|which|what)\b")

_TOKEN_RE = re.compile(r"\.?[a-z0-9]+(?:'[a-z]+)?")
# Longer words only: one edit on a short word reaches another real word too easily.
_TYPO_TARGETS = ({w: "filler" for w in _FILLER if len(w) >= 5} | {w: "engine" for w in _ENGINE if len(w) >= 5} | {w: "everything" for w in _EVERYTHING if len(w) >= 5}
                 | {w: s[0] for w, s in _KIND_OF_WORD.items() if len(w) >= 5})
LIST_NAMES = 10
LISTED_FOLDERS = 5
LISTED_REASONS = 3


@dataclass
class _Asked:
    mode: str                                                    # "count" / "any" / "list"
    about: str = "files"                                         # or "folders" / "failed" / "skipped"
    selections: list[Selection] = field(default_factory=list)    # empty: every file
    name_word: str | None = None                                 # count this word in names instead
    folder: str | None = None                                    # only inside folders with this name


def _one_edit_apart(a: str, b: str) -> bool:
    """One letter added, dropped or changed, or two neighbours swapped."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = next((n for n, (x, y) in enumerate(zip(a, b)) if x != y), len(a))
    if len(a) < len(b):
        return a[i:] == b[i + 1:]
    return a[i + 1:] == b[i + 1:] or (a[i:i + 2] == b[i:i + 2][::-1] and a[i + 2:] == b[i + 2:])


def _is_real_word(word: str) -> bool:
    dictionary = shared_dictionary()
    return dictionary is not None and dictionary.is_word(word)


def _fix_typo(word: str) -> str:
    """A misspelled collection word ("theere", "vidoes") as the word meant.
    Left alone: a real English word ("miles" is not "files"), and a word that
    is one edit from two different meanings."""
    known = word in _FILLER or word in _ENGINE or word in _EVERYTHING or word in _KIND_OF_WORD or word in _TYPE_OF_WORD
    if known or len(word) < 4 or not word.isalpha() or _is_real_word(word):
        return word
    close = {target: meaning for target, meaning in _TYPO_TARGETS.items() if _one_edit_apart(word, target)}
    return min(close) if len(set(close.values())) == 1 else word


def _words(question: str) -> list[str]:
    text = re.sub(r"\be-(mail|book)", r"e\1", question.lower())
    words = [w.replace("'", "") for w in _TOKEN_RE.findall(text)]
    # "hw many", "how mnay": only as the first two words, where nothing else fits.
    for at, meant in enumerate(("how", "many")):
        if len(words) > at and not _is_real_word(words[at]) and _one_edit_apart(words[at], meant):
            words[at] = meant
    return [_fix_typo(w) for w in words]


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("zzes"):
        return word[:-3]
    if word.endswith(("sses", "xes", "ches", "shes")):
        return word[:-2]
    return word[:-1] if word.endswith("s") and not word.endswith("ss") and len(word) > 3 else word


def same_name(text: str) -> str:
    """Case, slashes and the space / _ / - between words do not matter."""
    return re.sub(r"[\s_\-]+", " ", text.replace("\\", "/").lower()).strip()


def selection_at(words: list[str], i: int) -> tuple[Selection, int] | None:
    """The kind or type of file named at words[i], and how many words name it."""
    word = words[i]
    following = words[i + 1] if i + 1 < len(words) else ""
    if (word, following) in _PAIRS:
        return _PAIRS[word, following], 2
    if word in _KIND_OF_WORD:
        return _KIND_OF_WORD[word], 1
    # A short type word is also an ordinary word ("go", "c", "md"): it needs
    # its dot or "files" after it.
    bare = word.lstrip(".")
    if bare in _TYPE_OF_WORD and (word.startswith(".") or len(bare) >= 3 or following in ("file", "files")):
        return _TYPE_OF_WORD[bare], 1
    return None


def _take_folder(rest: list[str]) -> tuple[list[str], str | None, bool]:
    """"... in the invoices folder" / "... inside invoices": the words
    without that part, the folder's name, and whether "folder" was said."""
    for i, word in enumerate(rest):
        if word in ("folder", "directory"):
            start = next((j for j in range(i - 1, -1, -1) if rest[j] in _INTO), None)
            if start is not None:
                name = [w for w in rest[start + 1:i] if w not in ("the", "my", "our")]
                if 1 <= len(name) <= 4 and not all(w in _FILLER for w in name):
                    return rest[:start] + rest[i + 1:], " ".join(name).replace(".", ""), True
        if word == "inside" and not any(w in _FOLDER_WORDS for w in rest[i:]):
            name = [w for w in rest[i + 1:] if w not in ("the", "my", "our")]
            if 1 <= len(name) <= 4 and not any(w in _FILLER for w in name):
                return rest[:i], " ".join(name).replace(".", ""), False
    return rest, None, False


def counts_files_from_text(question: str) -> bool:
    """True for "how many files / photos / PDFs ..." that the index did not
    answer, so the model did: its number is what a file says, and the answer
    carries a note saying so (loop.py)."""
    text = " ".join(_words(question))
    start = _COUNT_START.search(text)
    if not start:
        return False
    rest = text[start.end():].split()
    first = next((n for n, w in enumerate(rest) if w not in _DETERMINERS), None)
    return first is not None and (rest[first] in _EVERYTHING or selection_at(rest, first) is not None)


def _read(question: str) -> _Asked | None:
    """What a question about the collection asks; None for a question about content."""
    text = " ".join(_words(question))
    start = _COUNT_START.search(text)
    if start and start.start() and all(w in _LEAD_IN for w in text[:start.start()].split()):
        text = text[start.start():]
    for mode, pattern in (("count", _COUNT), ("any", _ANY), ("list", _LIST)):
        opening = pattern.match(text)
        if opening:
            break
    else:
        return None
    rest = text[opening.end():].split()
    if not rest:
        return None
    # "what is a pdf" defines; "what pdfs do i have" lists. After "what" or
    # "which" the thing listed comes first.
    if opening.group(0) in ("what", "which") and not (selection_at(rest, 0) or rest[0] in _EVERYTHING or rest[0] in _FOLDER_WORDS):
        return None
    state = "failed" if _FAILED.search(" ".join(rest)) else "skipped" if _SKIPPED.search(" ".join(rest)) else None
    if state:
        rest = (_FAILED if state == "failed" else _SKIPPED).sub(" ", " ".join(rest)).split()
    subject = next((w for w in rest if w not in _DETERMINERS), "")
    rest, folder, folder_said = (rest, None, False) if subject in _FOLDER_WORDS else _take_folder(rest)
    selections: list[Selection] = []
    everything = engine = False
    joiners = 0
    unknown: list[int] = []
    i = 0
    while i < len(rest):
        word = rest[i]
        selected = selection_at(rest, i)
        if selected:
            if selected[0] not in selections:
                selections.append(selected[0])
            i += selected[1] - 1
        elif word in _EVERYTHING:
            everything = True
        elif word in ("and", "or", "plus"):
            joiners += 1
        elif word in _ENGINE:
            engine = True
        elif word not in _FILLER:
            unknown.append(i)
        i += 1
    first = next((n for n, w in enumerate(rest) if w not in _DETERMINERS), None)
    counts_files = mode == "count" and first is not None and (rest[first] in _EVERYTHING or selection_at(rest, first) is not None)
    if counts_files and unknown and not state and not _SOURCE.search(text):
        def verb(word: str) -> bool:  # "holding", "covered", "searches": a form of a word about the index
            return any(word.endswith(end) and (word[:len(word) - len(end)] in _ENGINE or word[:len(word) - len(end)] in _SMALL) for end in ("ing", "ed", "es", "s"))

        loose = {n for n in unknown if rest[n] not in _SMALL and not verb(rest[n])}

        def stands_alone(n: int) -> bool:
            return n - 1 not in loose and n + 1 not in loose

        pointed = any(
            stands_alone(n) and (w in _POINTERS or (w in _DEVICES and n > 0 and rest[n - 1] in _OWN_DEVICE) or (w == "search" and ("you" in rest or "i" in rest)))
            for n, w in enumerate(rest)
        )
        if pointed or all(rest[n] in _SMALL for n in unknown):
            unknown = []
    if any(w in _DEVICES and n > 0 and rest[n - 1] in _ANY_DEVICE for n, w in enumerate(rest)):
        return None
    plain = not unknown and not joiners
    if state:
        return _Asked(mode, about=state) if plain and everything and not selections and not folder else None
    if subject in _FOLDER_WORDS:
        return _Asked(mode, about="folders") if plain and not selections and not everything else None
    if not unknown and selections and (joiners == 0 if len(selections) == 1 else 1 <= joiners < len(selections)):
        return _Asked(mode, selections=selections, folder=folder)
    if plain and not selections and everything:
        return _Asked(mode, folder=folder)
    # "how many quizs are there": one word that is not a kind of file, right
    # after the count words. Counted in file names, and the answer says so.
    explicit_count = mode == "count" and not opening.group(0).endswith("total")
    # ("inside the march invoice" is not a folder unless the question says "folder".)
    if explicit_count and not selections and not engine and not joiners and len(unknown) == 1 and (folder is None or folder_said):
        word = rest[unknown[0]]
        if len(word) >= 3 and word.isalpha() and all(w in _DETERMINERS for w in rest[:unknown[0]]):
            return _Asked(mode, name_word=word, folder=folder)
    return None


def _names(paths: list[str], n: int = 3) -> str:
    return ", ".join(Path(p).name for p in paths[:n])


def _and_others(left: int, exact: bool = False) -> str:
    if left <= 0:
        return ""
    return " and 1 other" if left == 1 else f" and {left:,} others" if exact else " and others"


def _examples(paths: list[str], n: int = 3) -> str:
    return _names(paths, n) + _and_others(len(paths) - n)


def _files(n: int) -> str:
    return f"{n:,} file{'s' if n != 1 else ''}"


def _a(label: str) -> str:
    """"a PDF", "an MP3 file", "an audio file"."""
    first = label[0]
    spelled_out = label.split()[0].isupper()  # an abbreviation is read letter by letter
    return "an" if first in ("AEFHILMNORSX" if spelled_out else "aeiouAEIOU") else "a"


def _count_by_name(word: str, scope: list[str], where: str, total: int) -> str:
    stem = _singular(word)
    forms = {word, stem}
    found = [p for p in scope if any(f in part.lower() for f in forms for part in (Path(p).name, Path(p).parent.name))]
    note = "This counts names, not what is written inside the files. Search looks inside."
    if not found:
        return f'No indexed file{where} has "{stem}" in its name or folder name ({_files(len(scope))} checked). {note}'
    have, their = ("has", "its") if len(found) == 1 else ("have", "their")
    return f'{_files(len(found))}{where} {have} "{stem}" in {their} name or folder name ({_examples(found)}), out of {total:,} indexed files. {note}'


def _summary(paths: list[str]) -> str:
    total = len(paths)
    counts = Counter(Path(p).suffix.lower() for p in paths)
    parts = []
    for kind in ("document", "photo", "video", "audio"):
        n = sum(counts[e] for e in KINDS[kind][3])
        parts.append(f"{n:,} {KINDS[kind][2] if n != 1 else KINDS[kind][1]}")
    top = ", ".join(f"{n:,} {(e.lstrip('.') or 'no extension').upper()}" for e, n in counts.most_common(4))
    return (f"IntelliFile has indexed {_files(total)}: {', '.join(parts[:-1])} and {parts[-1]}."
            + (f" The most common types are {top}." if total else " Add a folder on the Index page to start."))


def _folders_answer(folders: list[str]) -> str:
    if not folders:
        return "No folder has been added yet. Add one on the Index page."
    more = len(folders) - LISTED_FOLDERS
    return f"IntelliFile searches {len(folders):,} folder{'s' if len(folders) != 1 else ''}: {', '.join(folders[:LISTED_FOLDERS])}{f' and {more:,} more' if more > 0 else ''}."


def _state_answer(about: str, entries: list[dict]) -> str:
    """Files that could not be read, or were left out, in this run of the app."""
    latest = {e["path"]: (e.get("reason") or e.get("error") or "").strip().rstrip(".") for e in entries}
    what = "could not be read" if about == "failed" else "left out on purpose"
    if not latest:
        return (f"No file {'has failed to be read' if about == 'failed' else 'has been left out on purpose'} in this run of the app. "
                "The Index page lists any that are.")
    shown = ", ".join(f"{Path(p).name} ({reason or 'the reason was not kept'})" for p, reason in list(latest.items())[-LISTED_REASONS:])
    was = ("" if about == "failed" else "was " if len(latest) == 1 else "were ")
    return (f"{_files(len(latest))} {was}{what} in this run of the app: {shown}{_and_others(len(latest) - LISTED_REASONS, exact=True)}. "
            "The Index page lists them with the reasons.")


def library_answer(question: str, paths: list[str], failures: list[dict] | None = None, skips: list[dict] | None = None, folders: list[str] | None = None) -> str | None:
    """The answer to a question about the collection itself, or None when
    the question is about the files' content. `failures` and `skips` are the
    scan's stored reasons ({"path", "reason"}), `folders` the indexed folders."""
    asked = _read(question)
    if asked is None:
        return None
    if asked.about == "folders":
        return _folders_answer(folders or [])
    if asked.about != "files":
        return _state_answer(asked.about, (failures if asked.about == "failed" else skips) or [])
    total = len(paths)
    scope, where = paths, ""
    if asked.folder:
        scope = [p for p in paths if asked.folder in {same_name(part) for part in Path(p).parts[:-1]}]
        if not scope:
            return f'No indexed folder is named "{asked.folder}" ({_files(total)} checked).'
        where = f' in the "{asked.folder}" folder'
    if asked.name_word is not None:
        return _count_by_name(asked.name_word, scope, where, total)
    if not asked.selections:
        if not where:
            return _summary(paths)
        return f"{_files(len(scope))} {'is' if len(scope) == 1 else 'are'}{where} ({_examples(scope)}), out of {total:,} indexed files."
    found = [(singular, plural, [p for p in scope if Path(p).suffix.lower() in exts]) for singular, plural, exts in asked.selections]
    if len(found) > 1:
        parts = [f"{len(hits):,} {singular if len(hits) == 1 else plural}" + (f" ({_examples(hits)})" if hits else "") for singular, plural, hits in found]
        return f"{', '.join(parts[:-1])} and {parts[-1]}{where}, out of {total:,} indexed files."
    singular, plural, hits = found[0]
    if not hits:
        if not scope and not where:  # "None of the 0 indexed files" reads as a mistake
            return "Nothing is indexed yet. Add a folder on the Index page to start."
        none = f"None of the {len(scope):,} {'indexed ' if not where else ''}file{'s' if len(scope) != 1 else ''}{where} is {_a(singular)} {singular}."
        return f"No. {none}" if asked.mode == "any" else none
    label = singular if len(hits) == 1 else plural
    if asked.mode == "list":
        return f"{len(hits):,} {label}{where}, out of {total:,} indexed files: {_names(hits, LIST_NAMES)}{_and_others(len(hits) - LIST_NAMES, exact=True)}."
    lead = "Yes: " if asked.mode == "any" else ""
    return f"{lead}{len(hits):,} {label}{where} ({_examples(hits)}), out of {total:,} indexed files."
