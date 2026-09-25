"""HTML → visible text. Added 2026-09-20.

Uses the stdlib parser only: nothing is rendered or executed, scripts
and styles are dropped entirely (Phase 12: document contents are
untrusted input). Block-level tags become newlines so headings and
paragraphs don't run together into one line.
"""

from html.parser import HTMLParser
from pathlib import Path

from .blocks import ExtractedBlock

_SKIPPED = {"script", "style", "noscript", "template", "svg", "head"}
_BLOCK = {
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
    "tr", "td", "th", "table", "section", "article", "header", "footer",
    "blockquote", "pre", "hr", "title",
}


class _TextCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIPPED:
            self._skip_depth += 1
        elif tag in _BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIPPED and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK:
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def extract_html(path: Path) -> list[ExtractedBlock]:
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = path.read_text(encoding="latin-1")
    collector = _TextCollector()
    collector.feed(raw)
    collector.close()
    text = collector.text().strip()
    return [ExtractedBlock(text=text)] if text else []
