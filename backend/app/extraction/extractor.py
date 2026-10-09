"""Top-level dispatcher: pick the right extractor by file extension, then
normalize every block's text the same way regardless of source format.
"""

import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from ..files.discovery import AUDIO_EXTENSIONS, CODE_EXTENSIONS, HTML_EXTENSIONS
from .audio_extractor import extract_audio
from .blocks import ExtractedBlock
from .docx_extractor import extract_docx
from .email_extractor import extract_eml, extract_msg
from .epub_extractor import extract_epub
from .html_extractor import extract_html
from .legacy_office_extractor import extract_doc, extract_ppt, extract_xls
from .normalize import normalize_text
from .odf_extractor import extract_odp, extract_ods, extract_odt
from .pdf_extractor import extract_pdf
from .pptx_extractor import extract_pptx
from .spreadsheet_extractor import extract_delimited, extract_xlsx
from .text_extractor import extract_text_file

if TYPE_CHECKING:
    from ..transcription.transcriber import Transcriber

UnsupportedFileType = ValueError

_EXTRACTORS = {
    ".pdf": extract_pdf,
    ".docx": extract_docx,
    ".txt": extract_text_file,
    ".md": extract_text_file,
    # Added 2026-09-20: spreadsheets, slides, web pages, code/config/RTF.
    ".csv": extract_delimited,
    ".tsv": extract_delimited,
    ".xlsx": extract_xlsx,
    ".xlsm": extract_xlsx,
    ".pptx": extract_pptx,
    # Added 2026-10-05: OpenDocument, e-books, saved e-mails, Office 97-2003.
    ".odt": extract_odt,
    ".ods": extract_ods,
    ".odp": extract_odp,
    ".epub": extract_epub,
    ".eml": extract_eml,
    ".msg": extract_msg,
    ".doc": extract_doc,
    ".xls": extract_xls,
    ".ppt": extract_ppt,
    **{ext: extract_html for ext in HTML_EXTENSIONS},
    **{ext: extract_text_file for ext in CODE_EXTENSIONS},
}

# AUDIO_EXTENSIONS comes from discovery.py — one list for the scan, the
# watcher and this dispatcher (it was duplicated here until 2026-09-21).

# Office files are zip archives. A "zip bomb" packs gigabytes of repeated
# bytes into a few kilobytes, and the Office parsers unpack whole members
# into memory: one such .docx could run the laptop out of memory and take
# the backend down (2026-10-04). The archive's own table of contents says
# how big every member unpacks to (zipfile never yields more than that), so
# it is checked before any parser opens the file. The ratio applies only to
# members bigger than MIN_RATIO_CHECK_BYTES: small XML parts of a real
# spreadsheet can compress better than 100:1.
# OpenDocument and EPUB files are zips too (2026-10-05).
OFFICE_ZIP_EXTENSIONS = {".docx", ".pptx", ".xlsx", ".xlsm", ".odt", ".ods", ".odp", ".epub"}
MAX_ZIP_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_ZIP_RATIO = 100
MIN_RATIO_CHECK_BYTES = 1024 * 1024


def check_office_zip(path: Path) -> None:
    """Raise ValueError (a failed file with this reason on the Status
    screen) when the archive would unpack too big or one member is packed
    too tightly. A file that is not a zip at all is left to the parser."""
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
    except zipfile.BadZipFile:
        return
    total = sum(m.file_size for m in members)
    if total > MAX_ZIP_UNCOMPRESSED_BYTES:
        raise ValueError(f"refused: it would unpack to {total // (1024 * 1024)} MB, over the {MAX_ZIP_UNCOMPRESSED_BYTES // (1024 * 1024)} MB limit (a zip bomb, or an unusually huge document)")
    for m in members:
        if m.file_size > MIN_RATIO_CHECK_BYTES and m.file_size > MAX_ZIP_RATIO * max(m.compress_size, 1):
            raise ValueError(f"refused: part {m.filename} unpacks {m.file_size // max(m.compress_size, 1)} times its packed size (a zip bomb)")


def extract_document(path: Path, transcriber: "Transcriber | None" = None) -> list[ExtractedBlock]:
    suffix = path.suffix.lower()
    if suffix in OFFICE_ZIP_EXTENSIONS:
        check_office_zip(path)

    if suffix in AUDIO_EXTENSIONS:
        if transcriber is None:
            raise UnsupportedFileType(
                f"Cannot extract {suffix}: the speech model is not installed (run scripts/download_whisper_model.py)"
            )
        blocks = extract_audio(path, transcriber)
    else:
        extractor = _EXTRACTORS.get(suffix)
        if extractor is None:
            raise UnsupportedFileType(f"No extractor for file type: {suffix}")
        blocks = extractor(path)

    return [
        ExtractedBlock(
            text=normalize_text(block.text),
            page_number=block.page_number,
            heading=block.heading,
            section=block.section,
        )
        for block in blocks
        if block.text.strip()
    ]
