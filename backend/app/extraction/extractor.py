"""Top-level dispatcher: pick the right extractor by file extension, then
normalize every block's text the same way regardless of source format.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from ..files.discovery import AUDIO_EXTENSIONS, CODE_EXTENSIONS, HTML_EXTENSIONS
from .audio_extractor import extract_audio
from .blocks import ExtractedBlock
from .docx_extractor import extract_docx
from .html_extractor import extract_html
from .normalize import normalize_text
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
    **{ext: extract_html for ext in HTML_EXTENSIONS},
    **{ext: extract_text_file for ext in CODE_EXTENSIONS},
}

# AUDIO_EXTENSIONS comes from discovery.py — one list for the scan, the
# watcher and this dispatcher (it was duplicated here until 2026-09-21).

def extract_document(path: Path, transcriber: "Transcriber | None" = None) -> list[ExtractedBlock]:
    suffix = path.suffix.lower()

    if suffix in AUDIO_EXTENSIONS:
        if transcriber is None:
            raise UnsupportedFileType(
                f"Cannot extract {suffix} — voice/audio model not loaded (run scripts/download_whisper_model.py)"
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
