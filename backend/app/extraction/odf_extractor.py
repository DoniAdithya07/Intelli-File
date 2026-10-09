"""OpenDocument (LibreOffice / OpenOffice) extraction. Added 2026-10-05.

An .odt/.ods/.odp file is a zip whose text lives in content.xml; the
stdlib reads both (zipfile + ElementTree), nothing is rendered or run.
The zip-bomb check in extractor.py has already looked at the archive.

- .odt: one block per paragraph and one per table, with the heading
  structure tracked the way the .docx reader does (outline level 1 is the
  section, deeper levels the heading).
- .ods: one block per sheet, rows as in the Excel reader (same row cap).
  LibreOffice pads a sheet to its full size with "repeated" empty rows and
  cells (a million rows in one element); those cost nothing here.
- .odp: one block per slide with the slide number and its title, speaker
  notes appended (as for .pptx).
"""

import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .blocks import ExtractedBlock
from .spreadsheet_extractor import MAX_SPREADSHEET_ROWS, _rows_to_text

_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_DRAW = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"
_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
_PRESENTATION = "{urn:oasis:names:tc:opendocument:xmlns:presentation:1.0}"

# ElementTree holds the whole content.xml in memory (several times its
# size). A real document's content.xml is a few MB; a bigger one is refused.
# ponytail: whole-tree parse; switch .ods to iterparse if huge sheets matter.
MAX_CONTENT_XML_BYTES = 50 * 1024 * 1024
# A cell repeated across 16,384 columns is padding; real repeats are a few.
MAX_CELL_REPEAT = 50
# (2026-10-05) The manifest is a list of parts, a few KB; it was read whole
# before any size check. And a DOCTYPE has no place in an ODF part: it is
# only there for entity tricks, so such a file is refused, never parsed.
MAX_MANIFEST_BYTES = 1024 * 1024


def _content(path: Path) -> ET.Element:
    with zipfile.ZipFile(path) as archive:
        if "META-INF/manifest.xml" in archive.namelist():
            if archive.getinfo("META-INF/manifest.xml").file_size > MAX_MANIFEST_BYTES:
                raise ValueError("refused: its manifest is over 1 MB, which no real document has")
            if b"encryption-data" in archive.read("META-INF/manifest.xml"):
                raise ValueError("this file is password-protected")
        size = archive.getinfo("content.xml").file_size
        if size > MAX_CONTENT_XML_BYTES:
            raise ValueError(f"refused: its text part is {size // (1024 * 1024)} MB, over the {MAX_CONTENT_XML_BYTES // (1024 * 1024)} MB limit")
        data = archive.read("content.xml")
        if b"<!DOCTYPE" in data:
            raise ValueError("refused: its text part declares a DOCTYPE (entity definitions), which real documents do not")
        return ET.fromstring(data)


def _own_rows(element: ET.Element):
    """A table's own rows (also inside header-rows / row-groups), never the
    rows of a table nested in one of its cells (2026-10-05: a sub-table's
    text was indexed twice, once in its parent and once on its own)."""
    for child in element:
        if child.tag == _TABLE + "table-row":
            yield child
        elif child.tag != _TABLE + "table":
            yield from _own_rows(child)


def _inline_text(element: ET.Element) -> str:
    """The text of a paragraph-like element, with ODF's space, tab and
    line-break elements turned back into characters."""
    parts = [element.text or ""]
    for child in element:
        if child.tag == _TEXT + "s":
            parts.append(" ")
        elif child.tag == _TEXT + "tab":
            parts.append("\t")
        elif child.tag == _TEXT + "line-break":
            parts.append("\n")
        else:
            parts.append(_inline_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _cell_text(cell: ET.Element) -> str:
    return " ".join(t for t in (_inline_text(p).strip() for p in cell.iter(_TEXT + "p")) if t)


def _table_text(table: ET.Element) -> str:
    """One line per row, cells separated by ' | ' (as in the .docx reader)."""
    lines = []
    for row in _own_rows(table):
        cells = [t for t in (_cell_text(c) for c in row if c.tag == _TABLE + "table-cell") if t]
        if cells:
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def extract_odt(path: Path) -> list[ExtractedBlock]:
    body = _content(path).find(f"{_OFFICE}body/{_OFFICE}text")
    blocks: list[ExtractedBlock] = []
    section: str | None = None
    heading: str | None = None

    def walk(parent: ET.Element) -> None:
        nonlocal section, heading
        for element in parent:
            if element.tag == _TEXT + "h":
                text = _inline_text(element).strip()
                if not text:
                    continue
                if element.get(_TEXT + "outline-level", "1") == "1":
                    section, heading = text, None
                else:
                    heading = text
            elif element.tag == _TEXT + "p":
                text = _inline_text(element).strip()
                if text:
                    blocks.append(ExtractedBlock(text=text, heading=heading, section=section))
            elif element.tag == _TABLE + "table":
                text = _table_text(element)
                if text:
                    blocks.append(ExtractedBlock(text=text, heading=heading, section=section))
            else:  # lists, sections, frames: their paragraphs, in order
                walk(element)

    if body is not None:
        walk(body)
    return blocks


def _sheet_rows(table: ET.Element):
    for row in _own_rows(table):
        cells: list[str] = []
        for cell in row:
            if cell.tag not in (_TABLE + "table-cell", _TABLE + "covered-table-cell"):
                continue
            text = _cell_text(cell)
            if text:
                cells.extend([text] * min(int(cell.get(_TABLE + "number-columns-repeated", "1")), MAX_CELL_REPEAT))
        if cells:  # empty rows (and the million-row padding) are skipped, not expanded
            for _ in range(min(int(row.get(_TABLE + "number-rows-repeated", "1")), MAX_SPREADSHEET_ROWS)):
                yield cells


def extract_ods(path: Path) -> list[ExtractedBlock]:
    """One block per non-empty sheet; the sheet name is the block's section."""
    blocks = []
    for table in _content(path).iterfind(f"{_OFFICE}body/{_OFFICE}spreadsheet/{_TABLE}table"):  # top-level sheets only
        text = _rows_to_text(_sheet_rows(table))
        if text:
            blocks.append(ExtractedBlock(text=text, section=table.get(_TABLE + "name")))
    return blocks


def extract_odp(path: Path) -> list[ExtractedBlock]:
    blocks = []
    for number, page in enumerate(_content(path).iter(_DRAW + "page"), start=1):
        title = None
        lines: list[str] = []
        notes: list[str] = []
        for child in page:
            target = notes if child.tag == _PRESENTATION + "notes" else lines
            texts = [t for t in (_inline_text(p).strip() for p in child.iter(_TEXT + "p")) if t]
            if title is None and child.get(_PRESENTATION + "class") == "title" and texts:
                title = texts[0]
            target.extend(texts)
        text = "\n".join(lines + notes)
        if text:
            blocks.append(ExtractedBlock(text=text, page_number=number, heading=title))
    return blocks
