"""Office 97-2003 binary files: .doc, .ppt, .xls. Added 2026-10-05.

Best effort, pure Python, offline. Each is a compound ("OLE") file, read
with olefile; .xls goes through xlrd. Nothing is ever executed (macros
are never looked at). A file that can't be read - password-protected,
Word 95 or older, damaged - fails with a reason on the Status screen
("old .doc could not be read: ..."), never crashes or hangs the scan:
every loop below advances through a byte buffer of bounded size.

- .doc ([MS-DOC]): the text is stored in "pieces", each either 8-bit
  (cp1252) or UTF-16, listed by the piece table (CLX) in the table stream.
  Word's control characters become text: cell marks -> " | " / new row,
  field codes dropped and their results kept ("HYPERLINK ..." -> the
  link's words), paragraph/line/page breaks -> new lines. One block (no
  heading styles: those need the stylesheet, not worth parsing here).
- .ppt ([MS-PPT]): text atoms (TextCharsAtom UTF-16, TextBytesAtom cp1252)
  per slide, in presentation order through the persist directory; slide
  masters (placeholder prompts) are skipped. One block per slide, with the
  slide number. Speaker notes are not read.
- .xls ([MS-XLS]): xlrd, one block per sheet, same row cap as .xlsx.
"""

import contextlib
import io
import logging
import re
import struct
from pathlib import Path

from .blocks import ExtractedBlock
from .spreadsheet_extractor import MAX_SPREADSHEET_ROWS, _rows_to_text
from .text_extractor import extract_text_file

logger = logging.getLogger(__name__)


class _Unreadable(ValueError):
    """A reason this reader found itself ("its piece table is missing")."""


@contextlib.contextmanager
def _explained(kind: str):
    """Turn a damaged-file error into ValueError("old .doc could not be
    read: ..."). An OSError with an errno (locked, missing) stays as it is,
    so the scan retries it; olefile reports bad structure as OSError
    without one."""
    prefix = f"old {kind} could not be read"
    try:
        yield prefix
    except MemoryError:
        raise
    except OSError as e:
        if e.errno is not None:
            raise
        raise ValueError(f"{prefix}: {e}") from e
    except Exception as e:
        if isinstance(e, ValueError) and str(e).startswith(prefix):
            raise
        # A reason this reader found is shown as it is. Anything else, a
        # library's ValueError included ("Exceeds the limit (4300 digits)..."
        # from olefile, 2026-10-05), means the bytes are not a valid file;
        # the raw error stays in the text for the log.
        detail = str(e) if isinstance(e, _Unreadable) else f"it is damaged ({type(e).__name__}: {e})"
        raise ValueError(f"{prefix}: {detail}") from e


def _u16(data: bytes, at: int) -> int:
    return struct.unpack_from("<H", data, at)[0]


def _u32(data: bytes, at: int) -> int:
    return struct.unpack_from("<I", data, at)[0]


# ---------------------------------------------------------------- .doc

_FIELD_MARK = re.compile("([\x13\x14\x15])")
_DROPPED_CONTROLS = re.compile("[\x00-\x08\x0e-\x1f]")


def _clean_word_text(text: str) -> str:
    if "\x13" in text:
        # Fields: \x13 code \x14 result \x15, nestable. Keep only results.
        # (2026-10-05) A field never closed (a damaged file) hid everything
        # after it as "code": what was dropped since then is put back.
        kept, code_depth, stack, hidden = [], 0, [], []
        for token in _FIELD_MARK.split(text):
            if token == "\x13":
                stack.append(True)
                code_depth += 1
            elif token == "\x14" and stack and stack[-1]:
                stack[-1] = False
                code_depth -= 1
            elif token == "\x15" and stack:
                code_depth -= stack.pop()
            elif token in ("\x14", "\x15"):
                continue
            elif not code_depth:
                kept.append(token)
            else:
                hidden.append(token)
            if not code_depth:
                hidden = []
        text = "".join(kept + hidden)
    # Each cell ends with \x07 and each table row with one more.
    text = text.replace("\x07\x07", "\n").replace("\x07", " | ")
    text = text.translate({0x0D: "\n", 0x0B: "\n", 0x0C: "\n", 0x1E: "-", 0x1F: None})
    return _DROPPED_CONTROLS.sub("", text)


# (2026-10-05): pieces may overlap, so 4,000 pieces naming the same 4 KB of
# text decoded to 16 M characters (and a 50 MB file to gigabytes). A real
# document's pieces never add up to more than its WordDocument stream holds;
# twice that, and never over MAX_DOC_CHARS, is refused as damaged.
MAX_DOC_CHARS = 50_000_000


def _doc_text(word: bytes, table: bytes, ccp_text: int, fc_clx: int, lcb_clx: int) -> str:
    limit = min(2 * len(word), MAX_DOC_CHARS)
    total = 0
    clx = table[fc_clx:fc_clx + lcb_clx]
    i = 0
    while i < len(clx) and clx[i] == 0x01:  # Prc: formatting, skipped
        i += 3 + _u16(clx, i + 1)
    if i >= len(clx) or clx[i] != 0x02:
        raise _Unreadable("its piece table is missing")
    plc = clx[i + 5:i + 5 + _u32(clx, i + 1)]
    pieces = (len(plc) - 4) // 12
    cps = struct.unpack_from(f"<{pieces + 1}I", plc, 0)
    parts = []
    for n in range(pieces):
        start, end = cps[n], min(cps[n + 1], ccp_text)
        if start >= ccp_text:
            break
        if end <= start:
            continue
        fc = _u32(plc, 4 * (pieces + 1) + 8 * n + 2)
        if fc & 0x40000000:  # 8-bit piece
            at = (fc & 0x3FFFFFFF) // 2
            parts.append(word[at:at + end - start].decode("cp1252", "replace"))
        else:
            parts.append(word[fc:fc + 2 * (end - start)].decode("utf-16-le", "replace"))
        total += len(parts[-1])
        if total > limit:
            raise _Unreadable("it is damaged: its piece table repeats the text far beyond the document's size")
    return "".join(parts)


def extract_doc(path: Path) -> list[ExtractedBlock]:
    import olefile

    with path.open("rb") as f:
        head = f.read(8)
    if head.startswith(b"{\\rtf"):  # Word and WordPad also save RTF under a .doc name
        return extract_text_file(path)
    with _explained(".doc") as prefix:
        if not olefile.isOleFile(str(path)):
            raise ValueError(f"{prefix}: it is not a Word 97-2003 document")
        with olefile.OleFileIO(str(path)) as ole:
            if not ole.exists("WordDocument"):
                raise ValueError(f"{prefix}: it is not a Word 97-2003 document")
            word = ole.openstream("WordDocument").read()
            ident, flags = _u16(word, 0), _u16(word, 0x0A)
            if ident == 0xA5DC:
                raise ValueError(f"{prefix}: it was saved by Word 95 or older, a format IntelliFile does not read")
            if ident != 0xA5EC:
                raise ValueError(f"{prefix}: it is not a Word 97-2003 document")
            if flags & 0x0100 or flags & 0x8000:
                raise ValueError(f"{prefix}: it is password-protected")
            table_name = "1Table" if flags & 0x0200 else "0Table"
            if not ole.exists(table_name):
                raise ValueError(f"{prefix}: its {table_name} stream is missing")
            table = ole.openstream(table_name).read()
        # FIB: a fixed 32-byte base, then three arrays whose lengths are stored in front of them.
        at = 32 + 2 + 2 * _u16(word, 32)
        long_values = at + 2
        at = long_values + 4 * _u16(word, at)
        if _u16(word, at) <= 33:
            raise ValueError(f"{prefix}: its header is too short")
        fc_clx, lcb_clx = struct.unpack_from("<II", word, at + 2 + 33 * 8)
        text = _doc_text(word, table, _u32(word, long_values + 12), fc_clx, lcb_clx)
    text = _clean_word_text(text).strip()
    return [ExtractedBlock(text=text)] if text else []


# ---------------------------------------------------------------- .ppt

_DOCUMENT, _SLIDE_LIST, _SLIDE_PERSIST, _SLIDE = 0x03E8, 0x0FF0, 0x03F3, 0x03EE
_TEXT_CHARS, _TEXT_BYTES, _USER_EDIT, _PERSIST_DIRECTORY = 0x0FA0, 0x0FA8, 0x0FF5, 0x1772


def _records(data: bytes, start: int, end: int):
    """(type, instance, body start, body end, is container) of each record."""
    end = min(end, len(data))
    while start + 8 <= end:
        ver_inst, rtype, length = struct.unpack_from("<HHI", data, start)
        body = start + 8
        yield rtype, ver_inst >> 4, body, min(body + length, end), ver_inst & 0xF == 0xF
        start = body + length


def _texts(data: bytes, start: int, end: int) -> list[str]:
    """Every text atom's lines among the records in [start, end), nested ones included."""
    lines = []
    for rtype, _, body, stop, container in _records(data, start, end):
        if container:
            lines += _texts(data, body, stop)
        elif rtype in (_TEXT_CHARS, _TEXT_BYTES):
            text = data[body:stop].decode("utf-16-le" if rtype == _TEXT_CHARS else "cp1252", "replace")
            lines += [line.strip() for line in re.split("[\r\x0b]", text) if line.strip()]
    return lines


def _persist_directory(data: bytes, current_user: bytes) -> tuple[dict[int, int], int] | None:
    """{persist id: stream offset} and the document's persist id, from the
    newest edit back to the first; None if the chain can't be followed."""
    if len(current_user) < 20:
        return None
    edit_at, seen, directory, document_ref = _u32(current_user, 16), set(), {}, None
    while edit_at not in seen and edit_at + 28 <= len(data) and _u16(data, edit_at + 2) == _USER_EDIT:
        seen.add(edit_at)
        last_edit, directory_at, doc_ref = struct.unpack_from("<III", data, edit_at + 16)
        document_ref = doc_ref if document_ref is None else document_ref
        if directory_at + 8 > len(data) or _u16(data, directory_at + 2) != _PERSIST_DIRECTORY:
            return None
        at, end = directory_at + 8, min(directory_at + 8 + _u32(data, directory_at + 4), len(data))
        while at + 4 <= end:
            entry = _u32(data, at)
            at += 4
            for k in range(entry >> 20):
                if at + 4 > end:
                    break
                directory.setdefault((entry & 0xFFFFF) + k, _u32(data, at))  # newer edits win
                at += 4
        if last_edit == 0:
            break
        edit_at = last_edit
    return (directory, document_ref) if document_ref is not None else None


def _record_at(data: bytes, at: int | None, rtype: int) -> tuple[int, int] | None:
    if at is None or at + 8 > len(data) or _u16(data, at + 2) != rtype:
        return None
    return at + 8, at + 8 + _u32(data, at + 4)


# (2026-10-05): 3,000 slide-list entries naming one slide read that slide
# 3,000 times (any stream offset can be named again). Each slide container
# is read once, at most MAX_PPT_SLIDES slides, and the text read may not
# outgrow the stream it comes from (each byte yields at most one character).
MAX_PPT_SLIDES = 2000
# The Current User stream's header token when the presentation is encrypted ([MS-PPT] 2.3.2).
_ENCRYPTED_TOKEN = 0xF3D1C4DF


def _capped(slides: list[list[str]], data: bytes) -> list[list[str]]:
    if sum(len(line) for lines in slides for line in lines) > len(data):
        raise _Unreadable("it is damaged: its slides repeat the same text far beyond the file's size")
    return slides[:MAX_PPT_SLIDES]


def _slides_in_order(data: bytes, current_user: bytes) -> list[list[str]] | None:
    """Each slide's lines in presentation order: older files keep slide text
    in the document's slide list, newer ones in each slide's own drawing."""
    found = _persist_directory(data, current_user)
    document = found and _record_at(data, found[0].get(found[1]), _DOCUMENT)
    if not document:
        return None
    directory = found[0]
    slides: list[tuple[int, list[str]]] = []
    for rtype, instance, body, stop, _ in _records(data, *document):
        if rtype == _SLIDE_LIST and instance == 0:
            for atom, _, atom_body, atom_stop, _ in _records(data, body, stop):
                if atom == _SLIDE_PERSIST and atom_body + 4 <= atom_stop:
                    if len(slides) >= MAX_PPT_SLIDES:
                        break
                    slides.append((_u32(data, atom_body), []))
                elif slides:
                    slides[-1][1].extend(_texts(data, atom_body - 8, atom_stop))
    result, read = [], set()
    for ref, lines in slides:
        offset = directory.get(ref)
        if offset in read:  # this slide container is already read: a repeated entry
            continue
        read.add(offset)
        slide = _record_at(data, offset, _SLIDE)
        result.append(lines + (_texts(data, *slide) if slide else []))
    return _capped(result, data) or None


def extract_ppt(path: Path) -> list[ExtractedBlock]:
    import olefile

    with _explained(".ppt") as prefix:
        if not olefile.isOleFile(str(path)):
            raise ValueError(f"{prefix}: it is not a PowerPoint 97-2003 presentation")
        with olefile.OleFileIO(str(path)) as ole:
            if ole.exists("EncryptedSummary"):
                raise ValueError(f"{prefix}: it is password-protected")
            if not ole.exists("PowerPoint Document"):
                raise ValueError(f"{prefix}: it is not a PowerPoint 97-2003 presentation")
            data = ole.openstream("PowerPoint Document").read()
            current_user = ole.openstream("Current User").read() if ole.exists("Current User") else b""
        if len(current_user) >= 16 and _u32(current_user, 12) == _ENCRYPTED_TOKEN:
            raise ValueError(f"{prefix}: it is password-protected")
        slides = _slides_in_order(data, current_user)
        if slides is None:  # no usable directory: every slide container, in file order
            slides = _capped([_texts(data, body, stop) for rtype, _, body, stop, _ in _records(data, 0, len(data)) if rtype == _SLIDE], data)
    return [ExtractedBlock(text="\n".join(lines), page_number=number) for number, lines in enumerate(slides, start=1) if lines]


# ---------------------------------------------------------------- .xls

def _xls_cell(cell, datemode: int) -> str:
    import xlrd

    if cell.ctype == xlrd.XL_CELL_NUMBER:
        return str(int(cell.value)) if float(cell.value).is_integer() else str(cell.value)
    if cell.ctype == xlrd.XL_CELL_DATE:
        try:
            moment = xlrd.xldate_as_datetime(cell.value, datemode)
        except (ValueError, OverflowError):
            return ""
        return moment.date().isoformat() if moment.time() == moment.min.time() else moment.isoformat(sep=" ")
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return "TRUE" if cell.value else "FALSE"
    if cell.ctype == xlrd.XL_CELL_TEXT:
        return cell.value
    return ""  # empty, blank, error


# (2026-10-05): one cell at row 60,000 / column 200 made xlrd pad every row
# to full width, 12 M cells in memory for two values. Rows are now kept
# ragged (only real cells), and a sheet spanning more than this many cells
# is skipped with a note in the log; the workbook's other sheets are read.
MAX_XLS_SHEET_CELLS = 2_000_000


def extract_xls(path: Path) -> list[ExtractedBlock]:
    """One block per non-empty sheet; the sheet name is the block's section.
    Formulas are never evaluated: xlrd returns the values Excel cached."""
    import xlrd

    blocks = []
    with _explained(".xls"):
        # xlrd prints warnings to stdout by default; a windowed app has none.
        # It releases the file itself when opening fails; the finally below covers the rest.
        book = xlrd.open_workbook(str(path), on_demand=True, ragged_rows=True, logfile=io.StringIO())
        try:
            for index in range(book.nsheets):
                sheet = book.sheet_by_index(index)
                if sheet.nrows * sheet.ncols > MAX_XLS_SHEET_CELLS:
                    logger.warning("%s: sheet %r spans %d x %d cells, over the %d-cell limit; it is not indexed",
                                   path, sheet.name, sheet.nrows, sheet.ncols, MAX_XLS_SHEET_CELLS)
                    book.unload_sheet(index)
                    continue
                rows = ([_xls_cell(c, book.datemode) for c in sheet.row(r)] for r in range(min(sheet.nrows, MAX_SPREADSHEET_ROWS)))
                text = _rows_to_text(rows)
                if text:
                    blocks.append(ExtractedBlock(text=text, section=sheet.name))
                book.unload_sheet(index)
        finally:
            book.release_resources()
    return blocks
