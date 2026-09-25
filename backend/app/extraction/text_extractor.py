import re
from pathlib import Path

from .blocks import ExtractedBlock

# RTF is plain text wrapped in control words: {\rtf1\ansi ... \par Hello}.
# A small regex pass is enough to leave the prose behind — no library,
# nothing executed. Order matters: drop header/destination groups (font
# and colour tables, stylesheet, metadata, pictures — any group starting
# with \* is an optional destination), then \'hh hex escapes, then
# control words, then the braces themselves.
_RTF_HEADER_GROUPS = {
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "header",
    "footer", "listtable", "listoverridetable", "generator", "themedata",
    "colorschememapping", "latentstyles", "datastore", "xmlnstbl",
}
_RTF_GROUP_START = re.compile(r"\{\\(\*\\)?([a-zA-Z]+)")
_RTF_HEX = re.compile(r"\\'[0-9a-fA-F]{2}")
_RTF_PARAGRAPH = re.compile(r"\\(?:par|line|tab)\b")
_RTF_CONTROL = re.compile(r"\\[a-zA-Z]+-?\d* ?")


def _drop_rtf_groups(content: str) -> str:
    """Remove {...} groups that hold no prose, honouring nested braces."""
    out = []
    i = 0
    while i < len(content):
        match = _RTF_GROUP_START.match(content, i)
        if match and (match.group(1) or match.group(2).lower() in _RTF_HEADER_GROUPS):
            depth = 0
            j = i
            while j < len(content):
                if content[j] == "{":
                    depth += 1
                elif content[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            i = j + 1
            continue
        out.append(content[i])
        i += 1
    return "".join(out)


def _strip_rtf(content: str) -> str:
    content = _drop_rtf_groups(content)
    content = _RTF_HEX.sub(" ", content)
    content = _RTF_PARAGRAPH.sub("\n", content)
    content = _RTF_CONTROL.sub("", content)
    return content.replace("{", "").replace("}", "")


def extract_text_file(path: Path) -> list[ExtractedBlock]:
    """Plain TXT/Markdown — and, since 2026-09-20, source code, config
    files and RTF: no page numbers or headings to preserve, so the whole
    file is one block. Falls back to latin-1 if a file isn't valid UTF-8
    rather than crashing the whole indexing job over one bad file.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = path.read_text(encoding="latin-1")

    if path.suffix.lower() == ".rtf":
        content = _strip_rtf(content)

    content = content.strip()
    if not content:
        return []
    return [ExtractedBlock(text=content)]
