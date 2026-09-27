from pathlib import Path

from pypdf import PdfReader

from . import ocr
from .blocks import ExtractedBlock

# A page with fewer characters than this in its text layer is treated as
# scanned (an image of text) and read with Windows' OCR instead.
SCANNED_PAGE_MAX_CHARS = 20


def extract_pdf(path: Path) -> list[ExtractedBlock]:
    """One block per page, so page_number survives into search results
    (per the PRD's Text Extraction section). Pages without a text layer —
    scans — are read with Windows' offline OCR when it is available
    (next-round improvement 4)."""
    reader = PdfReader(str(path))
    texts: dict[int, str] = {}
    scanned: list[int] = []
    for i, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        texts[i + 1] = text
        if len(text) < SCANNED_PAGE_MAX_CHARS:
            scanned.append(i + 1)
    if scanned:
        for number, text in ocr.ocr_pdf_pages(path, scanned).items():
            if len(text.strip()) > len(texts[number]):
                texts[number] = text.strip()
    return [ExtractedBlock(text=text, page_number=number) for number, text in texts.items() if text]
