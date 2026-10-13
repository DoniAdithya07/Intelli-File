"""Why a file the user names is not in the results (2026-10-13).

Answered from what the engine already knows, never by the model: the index's
records, the reasons the scan stored for files it could not read or left
alone, the files taken out of the index in this run, the indexed folders and
the file types IntelliFile reads. The first reason that applies is reported,
in this order: indexed, could not be read, skipped, deleted, type not read,
still indexing, not known.

Nothing here looks around the disk. Only when the user typed a full path
inside an indexed folder is that one path checked (read-only).
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..files import discovery
from ..files.identity import ONLINE_ONLY_REASON, is_online_only
from .library import _and_others, _examples, _one_edit_apart, _words, library_answer, same_name, selection_at

MAX_NAME_CHARS = 400
MAX_FOLDERS_LISTED = 5
MAX_SIMILAR_LISTED = 5
# A shorter name is one edit from too many other names.
MIN_TYPO_LETTERS = 5
_EXTENSION_RE = re.compile(r"\.(?=[a-z0-9]*[a-z])[a-z0-9]{1,6}$", re.I)  # ".pdf", ".mp4"; not the ".2" of "report v1.2"
_WILDCARDS = set('*?<>|"')


@dataclass
class Known:
    """What the engine knows, gathered by the caller (services/missing_files.py)."""

    active: list[str]                                    # paths of indexed files
    unfinished: list[str] = field(default_factory=list)  # found, their text not stored yet
    failures: list[dict] = field(default_factory=list)   # {"path", "reason"}: could not be read
    skips: list[dict] = field(default_factory=list)      # {"path", "reason"}: left alone on purpose
    deleted: list[dict] = field(default_factory=list)    # {"path", "moved_to"?}: taken out of the index in this run
    folders: list[str] = field(default_factory=list)     # the folders IntelliFile searches
    extensions: frozenset[str] = frozenset(discovery.SUPPORTED_EXTENSIONS | discovery.VISUAL_EXTENSIONS)
    indexing: bool = False
    waiting: int | None = None                           # files still to read, when known
    access_allowed: bool = True


def _full_path(typed: str) -> Path | None:
    if not os.path.isabs(typed) or not re.search(r"[\\/]", typed):
        return None
    return Path(os.path.normpath(typed))


def _typo_of(a: str, b: str) -> bool:
    """One LETTER off. "quiz 3" is another file than "quiz 1", not a typo of
    it (IMG_0001 / IMG_0002): the digits and signs must be the same."""
    letters_a, letters_b = re.sub(r"[^a-z]", "", a), re.sub(r"[^a-z]", "", b)
    same_rest = re.sub(r"[a-z]", "", a) == re.sub(r"[a-z]", "", b)
    return same_rest and min(len(letters_a), len(letters_b)) >= MIN_TYPO_LETTERS and _one_edit_apart(letters_a, letters_b)


def _find(typed: str, tiers: list[tuple[str, list[str]]]) -> tuple[str, list[str]] | None:
    """(status, matching paths) for the first tier that knows the file. A
    full path must match exactly; a name matches with or without its
    extension, and one letter off when only one known file is that close."""
    full = _full_path(typed)
    key = same_name(str(full) if full is not None else typed)
    if full is not None:
        for status, paths in tiers:
            hits = [p for p in paths if same_name(p) == key]
            if hits:
                return status, hits
        return None
    with_extension = bool(_EXTENSION_RE.search(typed))

    def name_of(path: str) -> str:
        name = os.path.basename(path)
        return same_name(name if with_extension else os.path.splitext(name)[0])

    for status, paths in tiers:
        hits = [p for p in paths if name_of(p) == key]
        if hits:
            return status, hits
    close = {p: status for status, paths in reversed(tiers) for p in paths if _typo_of(name_of(p), key)}
    if len(close) == 1:
        path, status = next(iter(close.items()))
        return status, [path]
    return None


def _stored_reason(path: str, entries: list[dict]) -> str:
    reason = next((e.get("reason") or e.get("error") or "" for e in reversed(entries) if e.get("path") == path), "")
    return reason.strip().rstrip(".")


def _folders_sentence(known: Known) -> str:
    if not known.access_allowed:
        return "File access is switched off, so nothing is indexed. Allow it in Settings, under File access."
    if not known.folders:
        return "No folder has been added yet. Add one on the Index page."
    shown = ", ".join(known.folders[:MAX_FOLDERS_LISTED])
    more = len(known.folders) - MAX_FOLDERS_LISTED
    return f"IntelliFile searches: {shown}{f' and {more:,} more' if more > 0 else ''}."


def _indexing_sentence(known: Known) -> str:
    waiting = f" ({known.waiting:,} file{'s' if known.waiting != 1 else ''} waiting)" if known.waiting else ""
    return f"Indexing is still running{waiting}"


def _check_on_disk(path: Path, known: Known) -> tuple[str, str]:
    """(status, reason) for a full path nothing is stored about."""
    roots = [Path(f) for f in known.folders if path.is_relative_to(Path(f))]
    if not roots:
        return "outside_folders", f"That file is not in a folder IntelliFile is allowed to search. {_folders_sentence(known)}"
    root = max(roots, key=lambda r: len(r.parts))
    if not path.is_file():
        return "not_on_disk", "There is no file at that path now. It may have been deleted, moved or renamed."
    if discovery.under_excluded_directory(path, root):
        return "skipped", f'"{path.name}" is in a folder IntelliFile leaves out on purpose (installed programs, packages and hidden folders).'
    reasons: list[str] = []
    if not discovery.is_indexable(path, set(known.extensions), root=root, on_skip=lambda _path, reason: reasons.append(reason)):
        why = reasons[0] if reasons else "it is a hidden or generated file, which IntelliFile leaves out"
        return "skipped", f'"{path.name}" was left out on purpose: {why}.'
    try:
        if is_online_only(path):
            return "skipped", f'"{path.name}" was left out on purpose: {ONLINE_ONLY_REASON}.'
    except OSError:
        pass
    if known.indexing:
        return "indexing", f'{_indexing_sentence(known)}, so "{path.name}" may not have been read yet. Ask again when it has finished.'
    return "not_indexed_yet", f'"{path.name}" is in an indexed folder but has not been read yet. Press Scan now on the Index page.'


def _containing(typed: str, paths: list[str]) -> list[str]:
    """Indexed files whose name contains every word typed ("invoice" for
    "march invoice.txt")."""
    words = same_name(os.path.splitext(typed)[0] if _EXTENSION_RE.search(typed) else typed).split()
    if not any(len(w) >= 3 for w in words):
        return []
    return [p for p in paths if all(w in same_name(os.path.basename(p)) for w in words)]


def why_missing(name: str, known: Known) -> dict:
    """{"name", "status", "reason"} and, when a known file matched, "path"."""
    typed = (name or "").strip().strip("\"'").strip()
    if not typed:
        return {"name": "", "status": "invalid", "reason": "Type a file name first."}
    # "...", "*.txt": nothing to look up (no pattern matching here).
    if "\x00" in typed or len(typed) > MAX_NAME_CHARS or not any(c.isalnum() for c in typed) or _WILDCARDS & set(typed):
        return {"name": typed[:MAX_NAME_CHARS], "status": "invalid", "reason": "That is not a file name. Type one name, without * or ?."}

    def result(status: str, reason: str, path: str | None = None) -> dict:
        return {"name": typed, "status": status, "reason": reason, **({"path": path} if path else {})}

    unfinished = set(known.unfinished)
    tiers = [
        ("indexed", [p for p in known.active if p not in unfinished]),
        ("failed", [e["path"] for e in known.failures]),
        ("skipped", [e["path"] for e in known.skips]),
        ("deleted", [e["path"] for e in known.deleted]),
        ("waiting", list(known.unfinished)),
    ]
    found = _find(typed, tiers)
    if found is not None:
        status, paths = found
        path = paths[0]
        shown, folder = os.path.basename(path), os.path.dirname(path)
        if status == "indexed":
            if len(paths) == 1:
                return result(status, f'"{shown}" is indexed, so it is not missing. It is in {folder}.', path)
            return result(status, f"{len(paths):,} indexed files have that name, so it is not missing: {', '.join(paths[:3])}{_and_others(len(paths) - 3)}.", path)
        if status == "failed":
            return result(status, f'"{shown}" was found but could not be read: {_stored_reason(path, known.failures) or "the reason was not kept"}.', path)
        if status == "skipped":
            return result(status, f'"{shown}" was left out on purpose: {_stored_reason(path, known.skips) or "the reason was not kept"}.', path)
        if status == "deleted":
            moved_to = next((e.get("moved_to") for e in reversed(known.deleted) if e["path"] == path and e.get("moved_to")), None)
            now = f" It is now indexed as {moved_to}." if moved_to else ""
            return result(status, f'"{shown}" was in the index earlier in this run of the app, but it has since been deleted, moved or renamed, so it was taken out. It was in {folder}.{now}', path)
        if known.indexing:
            return result("indexing", f'"{shown}" was found and is waiting to be read. {_indexing_sentence(known)}.', path)
        return result("failed", f'"{shown}" was found but its text was never stored. Re-index its folder on the Index page to try again.', path)

    extension = (_EXTENSION_RE.search(typed) or [""])[0].lower()
    if extension and extension not in known.extensions:
        if extension in discovery.VISUAL_EXTENSIONS:
            return result("unsupported", f"Photo and video search is off on this computer (its model folder is missing), so {extension} files are not indexed.")
        return result("unsupported", f'IntelliFile does not read {extension} files, so "{os.path.basename(typed)}" is not in the index.')
    full = _full_path(typed)
    if full is not None:
        return result(*_check_on_disk(full, known))
    similar = _containing(typed, known.active)
    if similar:
        names = ", ".join(os.path.basename(p) for p in similar[:MAX_SIMILAR_LISTED])
        more = len(similar) - MAX_SIMILAR_LISTED
        return result("similar", f'No indexed file is named exactly "{typed}". {"These indexed names are close" if len(similar) > 1 else "This indexed name is close"}: '
                                 f"{names}{f' and {more:,} more' if more > 0 else ''}.")
    if known.indexing:
        return result("indexing", f'{_indexing_sentence(known)}, so "{typed}" may not have been read yet. Ask again when it has finished.')
    return result("unknown", f'IntelliFile knows no file named "{typed}". The usual reasons: its folder is not one IntelliFile is allowed to search, '
                             f"or the name is spelled differently. {_folders_sentence(known)}")


def _why_no_kind(name: str, known: Known) -> str | None:
    """"Why are my photos not showing up": a kind of file, not a file name.
    How many of them are indexed and, when none, the likely reasons."""
    words = _words(name)
    all_files = bool(words) and all(w in ("file", "files") for w in words)
    while words and words[-1] in ("file", "files"):
        words.pop()
    selected = selection_at(words, 0) if words else None
    if not all_files and (selected is None or selected[1] != len(words)):
        return None
    singular, plural, exts = ("file", "files", known.extensions) if all_files else selected[0]
    hits = [p for p in known.active if all_files or Path(p).suffix.lower() in exts]
    if hits:
        are = "is" if len(hits) == 1 else "are"
        return (f"{len(hits):,} {singular if len(hits) == 1 else plural} {are} indexed ({_examples(hits)}), out of {len(known.active):,} indexed files. "
                "If one is missing, ask about it by its name.")
    reasons = ["the folder they are in is not one IntelliFile searches"]
    if not exts & known.extensions:
        visual = exts <= discovery.VISUAL_EXTENSIONS
        reasons = ["photo and video search is off on this computer (its model folder is missing)" if visual else "IntelliFile does not read this type of file"]
    elif known.indexing:
        reasons.append("indexing has not reached them yet")
    return f"No {plural} are indexed. The likely reason: {' or '.join(reasons)}. {_folders_sentence(known)}"


# ----- Ask: is this question about a missing file, and which one? -----

_NOT_THERE = (r"(?:missing|absent|gone|not (?:showing(?: up)?|there|here|indexed|found|listed|appearing|searchable|coming up"
              r"|in (?:the |my )?(?:index|results|search results|search|list)))")
_SHOW = r"(?:showing(?: up)?|indexed|found|there|listed|appearing|searchable|coming up|in (?:the |my )?(?:index|results|search results|search|list))"
_LEAD = r"(?:my |the |this |that )?(?:file (?:called |named )?)?"
# These wordings say a file is missing: always answered here.
_EXPLICIT = [re.compile(p, re.I) for p in (
    rf"^why (?:is|are|was) {_LEAD}(?P<name>.+?) {_NOT_THERE}$",
    rf"^why (?:isn'?t|is not|aren'?t|are not|wasn'?t|was not) {_LEAD}(?P<name>.+?) {_SHOW}$",
    rf"^why (?:doesn'?t|does not|don'?t|do not|didn'?t|did not|won'?t|will not) {_LEAD}(?P<name>.+?) (?:show(?: up)?|appear|come up|get indexed)$",
    rf"^why (?:does|do|did) {_LEAD}(?P<name>.+?) not (?:show(?: up)?|appear|come up|get indexed)$",
)]
# These could be about anything ("where is the dentist"): answered here only
# when the words end in a file extension.
_MAYBE = [re.compile(p, re.I) for p in (
    rf"^why (?:can'?t|cannot|can not|couldn'?t|could not) (?:i|you|we|it|intellifile) (?:find|see|locate|open) {_LEAD}(?P<name>.+)$",
    rf"^(?:where is|where'?s|where are|where did|what happened to) {_LEAD}(?P<name>.+?)(?: go| gone)?$",
)]


def missing_answer(question: str, known: Known) -> str | None:
    """The answer to "why is <file> missing" / "where is <file.ext>", or
    None when the question is not about a file."""
    text = re.sub(r"\s+", " ", question).strip().rstrip("?.! ").strip()
    for patterns, explicit in ((_EXPLICIT, True), (_MAYBE, False)):
        for pattern in patterns:
            asked = pattern.match(text)
            if asked is None:
                continue
            name = asked.group("name")
            if not explicit and not _EXTENSION_RE.search(name):
                return None
            found = why_missing(name, known)
            if found["status"] == "invalid":
                return None
            # "why are my photos not showing up": a kind of file, unless a file is called that.
            about_kind = _why_no_kind(name, known) if explicit and "path" not in found else None
            return about_kind or found["reason"]
    return None


def index_answer(question: str, known: Known) -> str | None:
    """A question the engine's own records answer, about the collection or
    about one missing file; None for everything else (the normal Ask flow)."""
    return library_answer(question, known.active, known.failures, known.skips, known.folders) or missing_answer(question, known)
