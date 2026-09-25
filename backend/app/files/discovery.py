"""Recursive discovery of files under user-selected folders."""

import os
from collections.abc import Iterator
from pathlib import Path

# MVP file types per the PRD's Supported File Types section.
DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}

# Added 2026-09-20 (user request: "it should identify all types, not only
# txt"). Each group has its own extractor; see extraction/extractor.py.
# Deliberately NOT here: .xls / .doc (binary Office 97 formats — no pure
# Python offline parser worth bundling; would need LibreOffice), and
# archives/binaries, which have no text to index.
SPREADSHEET_EXTENSIONS = {".csv", ".tsv", ".xlsx", ".xlsm"}
PRESENTATION_EXTENSIONS = {".pptx"}
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".css", ".json",
    ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sh", ".bat", ".ps1",
    ".sql", ".rtf",
}
HTML_EXTENSIONS = {".html", ".htm"}

TEXT_EXTENSIONS = DOCUMENT_EXTENSIONS | SPREADSHEET_EXTENSIONS | PRESENTATION_EXTENSIONS | CODE_EXTENSIONS | HTML_EXTENSIONS

# Size cap for the "plain text" family (code, data, html). A 5 MB source
# file is not something a person reads; a 5 MB .csv/.json/.log-like file
# is a data dump or a bundle, and chunking it would flood the index with
# thousands of near-identical rows (bug class #1 at laptop scale). PDFs,
# DOCX and PPTX are exempt — their size is mostly embedded images.
MAX_TEXT_FILE_BYTES = 5 * 1024 * 1024
SIZE_CAPPED_EXTENSIONS = SPREADSHEET_EXTENSIONS | CODE_EXTENSIONS | HTML_EXTENSIONS | {".txt", ".md"}

# Audio files, transcribed via the same local Whisper model built for
# voice search (Phase 7) — spoken content becomes searchable text, same
# as a PDF's written content. .m4a matters most in practice (the default
# format for iPhone/Android voice memos and many meeting recordings).
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aiff", ".aif"}

SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | AUDIO_EXTENSIONS

# Photos (Phase 8), searched by visual content via CLIP rather than by
# extracted text. Deliberately NOT folded into SUPPORTED_EXTENSIONS:
# that set means "files extract_document() can turn into text blocks",
# and an image isn't one. folder_scan.py unions this in explicitly, and
# only when the CLIP model is actually available.
# .avif added after a live test (2026-09-11): a real downloaded image in
# the user's demo folder was silently never indexed. Pillow >= 11 decodes
# AVIF natively, so it costs nothing. HEIC (iPhone default) is NOT here —
# this Pillow build has no HEIF codec; revisit in Phase 14 if it matters.
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".avif"}

# Video (Phase 8b): keyframes are embedded with the same CLIP model into
# the same table as photos, one vector per keyframe. Decoded by PyAV,
# which bundles its own codecs — no ffmpeg install needed on the user's
# machine. Same "only when the CLIP model is available" rule as photos.
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
VISUAL_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

# Development/package-management directories that are never real user
# documents but can be enormous (thousands of files) and easy to
# accidentally point the app at, e.g. picking a project folder that
# contains a Python venv or node_modules. Found via real testing: a
# folder scan picked up package metadata .txt files from backend/venv
# and returned them as if they were genuine search results. Any
# dot-directory (.git, .cache, .venv, ...) is skipped too.
#
# "models" is IntelliFile's own bundled ML model directory (embedding +
# Whisper weights/tokenizer files, e.g. backend/models/whisper-tiny.en/).
# Found via live re-test: indexing a folder containing it picked up
# merges.txt — a 50k-line BPE tokenizer merge-rules file, not a document —
# whose embedding scored highly against unrelated real queries and
# polluted top search results with gibberish.
EXCLUDED_DIR_NAMES = {
    "node_modules", "__pycache__", "venv", "env", "site-packages",
    "dist", "build", "target", "vendor", "models",
    # PyInstaller's onedir bundle (Phase 14): thousands of third-party .py
    # files under <exe>/_internal. Found 2026-09-21 when the assembled
    # resources folder inside the watched project turned a 150-file scan
    # into 2,143 files of pypdf source (bug class #1, fifth instance).
    "_internal",
}

# Individual well-known non-document filenames that can sit OUTSIDE any
# excluded directory (so EXCLUDED_DIR_NAMES can't catch them) but still
# aren't real user documents. Found via live re-test: indexing a project
# folder picked up backend/requirements.txt and requirements-dev.txt — a
# plain package dependency list, not prose — because they match the
# `.txt` filter and live directly in a normal (non-excluded) directory.
EXCLUDED_FILE_NAMES = {
    "requirements.txt", "requirements-dev.txt", "requirements-test.txt",
    # Lockfiles: machine-generated dependency graphs, often megabytes of
    # hashes and URLs. Matched the new .json/.yaml/.toml filters (2026-09-20).
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "cargo.lock", "pipfile.lock", "composer.lock", "gemfile.lock",
    "tsconfig.tsbuildinfo",
    # IntelliFile's own bundled word list (backend/data/, Phase 8 spell
    # check): 100k lines of "word count", matched .txt and ranked "strong"
    # for almost any query once the user indexed the project folder
    # (found 2026-09-20 during the voice-snap live test). Same shape as
    # the models/ tokenizer file — our own asset, not the user's document.
    "english_words.txt",
}

# Suffix patterns (checked against the lowercased filename) for generated
# files that carry a normal extension: minified bundles, source maps.
EXCLUDED_FILE_SUFFIXES = (".min.js", ".min.css", ".map", ".bundle.js", ".chunk.js")
# Prefixes: hidden files (`.`), macOS AppleDouble sidecars (`._`), Office
# lock files (`~$`).
EXCLUDED_FILE_PREFIXES = (".", "~$")


# Absolute directories the walk never enters (Phase 12 "Allow all" mode
# fills this from files/access.py: ~/Library, AppData, caches …).
EXCLUDED_PATHS: set[str] = set()


def discover_files(root_paths: list[str | Path], extensions: set[str] | None = None, on_error=None) -> Iterator[Path]:
    """Recursively walk each root path, yielding files whose extension is
    supported. Silently skips paths that don't exist rather than raising,
    since a folder could disappear between selection and scan. Prunes
    development/package directories (see EXCLUDED_DIR_NAMES) instead of
    just filtering their files out after the fact, so a huge node_modules
    or venv tree doesn't get walked at all. Also skips individual known
    non-document filenames (see EXCLUDED_FILE_NAMES) that can appear
    outside any excluded directory.
    """
    allowed = extensions if extensions is not None else SUPPORTED_EXTENSIONS
    for root in root_paths:
        root_path = Path(root)
        if not root_path.exists():
            continue
        # A directory the OS refuses to list (macOS Privacy & Security for
        # Desktop/Documents/Downloads and external drives, Windows ACLs) is
        # reported to the caller instead of being skipped in silence — a
        # folder that indexes 0 files with no reason looked like a bug in
        # the app (2026-09-21 macOS pass).
        for dirpath, dirnames, filenames in os.walk(root_path, onerror=on_error):
            dirnames[:] = [d for d in dirnames if not is_excluded_directory_name(d) and os.path.join(dirpath, d) not in EXCLUDED_PATHS]
            for filename in filenames:
                path = Path(dirpath) / filename
                if is_indexable(path, allowed):
                    yield path


def is_excluded_directory_name(name: str) -> bool:
    return name in EXCLUDED_DIR_NAMES or name.startswith(".")


def under_excluded_directory(path: Path, root: Path) -> bool:
    """True when any directory between `root` (exclusive) and `path`
    (exclusive) is one the folder walk would have pruned. Only the part
    below the watched root counts — the root itself may legitimately sit
    inside a `build/` or a dot-directory the user chose on purpose."""
    try:
        relative_parts = path.relative_to(root).parts[:-1]
    except ValueError:
        return False
    return any(is_excluded_directory_name(part) for part in relative_parts)


def is_indexable(path: Path, extensions: set[str] | None = None, root: Path | None = None, must_exist: bool = True) -> bool:
    """The single file-level rule shared by the folder scan and the live
    watcher (which sees files one at a time and can't rely on the walk's
    pruning): supported extension, not a known generated file, under the
    size cap for the plain-text family and — when the watched `root` is
    given — not inside a directory the walk would have pruned. Found
    2026-09-21: without the last rule a file created in
    `desktop/node_modules/…` was indexed by the watcher within 30 s while
    the scan of the same folder skipped it, so every `npm install` or
    `pip install` in a watched project fed junk into the index.
    """
    allowed = extensions if extensions is not None else SUPPORTED_EXTENSIONS
    lowered = path.name.lower()
    if lowered in EXCLUDED_FILE_NAMES or lowered.endswith(EXCLUDED_FILE_SUFFIXES):
        return False
    if lowered.startswith(EXCLUDED_FILE_PREFIXES):
        # Hidden files and the junk other programs leave next to documents:
        # macOS AppleDouble sidecars (`._notes.txt` on external drives),
        # Office lock files (`~$report.docx` while Word has it open), and
        # dotfiles in general (2026-09-21 macOS pass).
        return False
    if root is not None and under_excluded_directory(path, root):
        return False
    if EXCLUDED_PATHS and any(str(path).startswith(ex + os.sep) for ex in EXCLUDED_PATHS):
        return False
    suffix = path.suffix.lower()
    if suffix not in allowed:
        return False
    if suffix in SIZE_CAPPED_EXTENSIONS and must_exist:
        try:
            if path.stat().st_size > MAX_TEXT_FILE_BYTES:
                return False
        except OSError:
            # Vanished between the event and the check; the scan/watcher
            # will see the deletion on its own.
            return False
    return True


def permission_hint(path: str) -> str:
    """What to tell the user when the OS refused access to `path`."""
    import sys

    if sys.platform == "darwin":
        home = str(Path.home())
        protected = [home + "/Desktop", home + "/Documents", home + "/Downloads", "/Volumes"]
        if any(path == p or path.startswith(p + os.sep) for p in protected):
            return "macOS blocked access — allow IntelliFile under System Settings › Privacy & Security › Files and Folders (or Full Disk Access), then Re-index"
        return "macOS blocked access to this folder — check System Settings › Privacy & Security"
    if sys.platform == "win32":
        return "Windows denied access — check the folder's permissions or run IntelliFile as the folder's owner"
    return "permission denied"
