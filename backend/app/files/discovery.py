"""Recursive discovery of files under user-selected folders."""

import functools
import os
from collections.abc import Iterator
from pathlib import Path

# MVP file types per the PRD's Supported File Types section.
DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}

# Added 2026-09-20 (user request: "it should identify all types, not only
# txt"). Each group has its own extractor; see extraction/extractor.py.
# Deliberately NOT here: archives/binaries, which have no text to index.
SPREADSHEET_EXTENSIONS = {".csv", ".tsv", ".xlsx", ".xlsm"}
PRESENTATION_EXTENSIONS = {".pptx"}
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".css", ".json",
    ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sh", ".bat", ".ps1",
    ".sql", ".rtf",
}
HTML_EXTENSIONS = {".html", ".htm"}

# Added 2026-10-05: OpenDocument (LibreOffice), e-books, saved e-mails and
# the old binary Office 97-2003 formats. Read with the stdlib, olefile and
# xlrd (all pure Python). Their own group because they share the 50 MB
# document cap, not the 5 MB one of the plain-text family.
MORE_DOCUMENT_EXTENSIONS = {".odt", ".ods", ".odp", ".epub", ".eml", ".msg", ".doc", ".xls", ".ppt"}

TEXT_EXTENSIONS = DOCUMENT_EXTENSIONS | SPREADSHEET_EXTENSIONS | PRESENTATION_EXTENSIONS | CODE_EXTENSIONS | HTML_EXTENSIONS | MORE_DOCUMENT_EXTENSIONS

# Size cap for the "plain text" family (code, data, html). A 5 MB source
# file is not something a person reads; a 5 MB .csv/.json/.log-like file
# is a data dump or a bundle, and chunking it would flood the index with
# thousands of near-identical rows (bug class #1 at laptop scale). PDFs,
# DOCX and PPTX get a far higher cap — their size is mostly embedded images.
MAX_TEXT_FILE_BYTES = 5 * 1024 * 1024
SIZE_CAPPED_EXTENSIONS = SPREADSHEET_EXTENSIONS | CODE_EXTENSIONS | HTML_EXTENSIONS | {".txt", ".md"}
# Documents over this are not opened at all (2026-10-04): a 300 MB PDF or a
# hostile .docx held the single indexing lane for minutes and could run a
# laptop out of memory in the parser. The folder scan lists them as skipped.
MAX_DOCUMENT_FILE_BYTES = 50 * 1024 * 1024
DOCUMENT_SIZE_CAPPED_EXTENSIONS = {".pdf", ".docx", ".pptx"} | MORE_DOCUMENT_EXTENSIONS


def size_cap(suffix: str) -> int | None:
    """Largest file of this type that is indexed, in bytes; None = no cap."""
    if suffix in SIZE_CAPPED_EXTENSIONS:
        return MAX_TEXT_FILE_BYTES
    if suffix in DOCUMENT_SIZE_CAPPED_EXTENSIONS:
        return MAX_DOCUMENT_FILE_BYTES
    return None

# Audio files, transcribed via the same local Whisper model built for
# voice search (Phase 7) — spoken content becomes searchable text, same
# as a PDF's written content. .m4a matters most in practice (the default
# format for iPhone/Android voice memos and many meeting recordings).
# .wma/.aac/.opus added 2026-10-05 (Windows Media, raw AAC, WhatsApp/Telegram
# voice notes): PyAV's bundled FFmpeg decodes them like the rest.
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aiff", ".aif", ".wma", ".aac", ".opus"}

SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | AUDIO_EXTENSIONS

# Photos (Phase 8), searched by visual content via CLIP rather than by
# extracted text. Deliberately NOT folded into SUPPORTED_EXTENSIONS:
# that set means "files extract_document() can turn into text blocks",
# and an image isn't one. folder_scan.py unions this in explicitly, and
# only when the CLIP model is actually available.
# .avif added after a live test (2026-09-11): a real downloaded image in
# the user's demo folder was silently never indexed. Pillow >= 11 decodes
# AVIF natively, so it costs nothing. HEIC/HEIF (the iPhone default) added
# 2026-10-05 through pi-heif, registered with Pillow in extraction/__init__.py;
# TIFF (scanners, faxes) is native to Pillow, first page for the photo index.
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".avif", ".heic", ".heif", ".tif", ".tiff"}

# Video (Phase 8b): keyframes are embedded with the same CLIP model into
# the same table as photos, one vector per keyframe. Decoded by PyAV,
# which bundles its own codecs — no ffmpeg install needed on the user's
# machine. Same "only when the CLIP model is available" rule as photos.
# Added 2026-10-05: .3gp (old phones), .wmv, .mts/.m2ts (camcorders, AVCHD),
# .mpg/.mpeg. NOT .ts: in a user's folders that is TypeScript (CODE_EXTENSIONS).
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".3gp", ".wmv", ".mts", ".m2ts", ".mpg", ".mpeg"}
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
    # Installed software, toolchains and app data (2026-10-06). On the dev
    # laptop "whole computer" found 220,582 files, ~170,000 of them inside
    # AppData (67,863), a Rust toolchain (65,279), Anaconda (30,372) and a
    # Cargo registry (9,032): about 7 of the 8 hours a first full scan took,
    # for files nobody searches. Compared case-insensitively.
    "appdata", "programdata", "program files", "program files (x86)", "windowsapps", "wpsystem",
    "$recycle.bin", "system volume information",
    "anaconda3", "miniconda3", "anaconda", "miniconda", "rustup", "conda-meta",
}
EXCLUDED_DIR_NAMES = {name.lower() for name in EXCLUDED_DIR_NAMES}

# A folder that holds one of these is an installed program or toolchain,
# whatever it is called: its whole tree is skipped. Checked from the
# directory listing the walk already has, so it costs nothing extra.
INSTALL_MARKER_FILES = {"pyvenv.cfg", "unins000.exe", ".cargo-ok", "cargo.toml.orig"}
INSTALL_MARKER_PATHS = (("lib", "rustlib"), ("bin", "internal", "engine.version"), ("resources", "app", "product.json"))


def is_software_install(dirpath: str, dirnames: list[str], filenames: list[str]) -> bool:
    """A Python virtual environment (pyvenv.cfg), a conda environment
    (conda-meta, by name), an installed program (Inno Setup's unins000.exe),
    a crate in Cargo's registry, a Rust toolchain (lib/rustlib), the
    Flutter SDK (bin/internal/engine.version) or an installed VS Code /
    Electron app (resources/app/product.json)."""
    if any(f.lower() in INSTALL_MARKER_FILES for f in filenames):
        return True
    lower_dirs = {d.lower() for d in dirnames}
    return any(parts[0] in lower_dirs and os.path.exists(os.path.join(dirpath, *parts)) for parts in INSTALL_MARKER_PATHS)

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


def discover_files(root_paths: list[str | Path], extensions: set[str] | None = None, on_error=None, on_skip=None) -> Iterator[Path]:
    """Recursively walk each root path, yielding files whose extension is
    supported. Silently skips paths that don't exist rather than raising,
    since a folder could disappear between selection and scan (the folder
    scan checks the root itself first). Prunes
    development/package directories (see EXCLUDED_DIR_NAMES) instead of
    just filtering their files out after the fact, so a huge node_modules
    or venv tree doesn't get walked at all. Also skips individual known
    non-document filenames (see EXCLUDED_FILE_NAMES) that can appear
    outside any excluded directory. `on_skip(path, reason)` hears about
    supported files left out for their size or a path too long for Windows.
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
            if Path(dirpath) != root_path and is_software_install(dirpath, dirnames, filenames):
                dirnames[:] = []  # an installed program or toolchain: none of its tree is the user's
                continue
            dirnames[:] = [d for d in dirnames if not is_excluded_directory_name(d) and os.path.join(dirpath, d) not in EXCLUDED_PATHS]
            for filename in filenames:
                path = Path(dirpath) / filename
                if is_indexable(path, allowed, on_skip=on_skip):
                    yield path


# Windows' 260-character path limit (2026-10-05). The backend exe is
# long-path aware, but Windows only honours that when "long paths" is on
# (LongPathsEnabled, off by default). With it off, a file in a very deep
# folder failed with a cryptic "cannot find the path"; now it is skipped
# with a reason that says what to do.
MAX_PATH = 260
LONG_PATH_REASON = ("its path is longer than 260 characters and Windows long paths are off; "
                    "move it to a shorter folder, or turn on \"Enable Win32 long paths\" in Windows and scan again")


@functools.lru_cache(maxsize=1)
def long_paths_enabled() -> bool:
    if os.name != "nt":
        return True
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            return winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
    except OSError:
        return False


def path_too_long(path: str | Path) -> bool:
    return len(str(path)) >= MAX_PATH and not long_paths_enabled()


def is_excluded_directory_name(name: str) -> bool:
    return name.lower() in EXCLUDED_DIR_NAMES or name.startswith(".")


def under_excluded_directory(path: Path, root: Path) -> bool:
    """True when any directory between `root` (exclusive) and `path`
    (exclusive) is one the folder walk would have pruned. Only the part
    below the watched root counts — the root itself may legitimately sit
    inside a `build/` or a dot-directory the user chose on purpose."""
    try:
        relative_parts = path.relative_to(root).parts[:-1]
    except ValueError:
        return False
    if any(is_excluded_directory_name(part) for part in relative_parts):
        return True
    # The live watcher sees single files: check each folder between the root
    # and the file for an install marker (a few stats per event).
    folder = Path(root)
    for part in relative_parts:
        folder = folder / part
        if any((folder / name).exists() for name in INSTALL_MARKER_FILES) or any(folder.joinpath(*parts).exists() for parts in INSTALL_MARKER_PATHS):
            return True
    return False


def is_indexable(path: Path, extensions: set[str] | None = None, root: Path | None = None, must_exist: bool = True, on_skip=None) -> bool:
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
    if path_too_long(path):
        # Here, not only in the walk (2026-10-05): the live watcher saw such
        # files too and failed them with a cryptic "cannot find the path".
        if on_skip is not None and must_exist:
            on_skip(path, LONG_PATH_REASON)
        return False
    cap = size_cap(suffix)
    if cap is not None and must_exist:
        try:
            if path.stat().st_size > cap:
                if on_skip is not None:
                    on_skip(path, f"larger than {cap // (1024 * 1024)} MB, the limit for {suffix} files")
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
            return "macOS blocked access. Allow IntelliFile under System Settings › Privacy & Security › Files and Folders (or Full Disk Access), then Re-index"
        return "macOS blocked access to this folder. Check System Settings › Privacy & Security"
    if sys.platform == "win32":
        return "Windows denied access. Check the folder's permissions, or run IntelliFile as the folder's owner"
    return "permission denied"
