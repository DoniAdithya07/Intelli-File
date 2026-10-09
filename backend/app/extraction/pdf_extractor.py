import logging
from pathlib import Path

from pypdf import PdfReader

from . import ocr
from .blocks import ExtractedBlock

# A page with fewer characters than this in its text layer is treated as
# scanned (an image of text) and read with Windows' OCR instead.
logger = logging.getLogger(__name__)

SCANNED_PAGE_MAX_CHARS = 20
# Only the first this-many pages are read (2026-10-04). A 5,000-page PDF
# (a scanned archive, a generated report) held the indexer for many minutes
# and filled the index with one file; a book's first 500 pages say what it is.
MAX_PDF_PAGES = 500


def extract_pdf(path: Path) -> list[ExtractedBlock]:
    """One block per page, so page_number survives into search results
    (per the PRD's Text Extraction section). Pages without a text layer —
    scans — are read with Windows' offline OCR when it is available
    (next-round improvement 4)."""
    reader = PdfReader(str(path))
    texts: dict[int, str] = {}
    scanned: list[int] = []
    if len(reader.pages) > MAX_PDF_PAGES:
        logger.warning("%s has %d pages; only the first %d are indexed", path, len(reader.pages), MAX_PDF_PAGES)
    for i, page in enumerate(reader.pages):
        if i >= MAX_PDF_PAGES:
            break
        text = (page.extract_text() or "").strip()
        texts[i + 1] = text
        if len(text) < SCANNED_PAGE_MAX_CHARS:
            scanned.append(i + 1)
    if scanned:
        for number, text in ocr.ocr_pdf_pages(path, scanned).items():
            if len(text.strip()) > len(texts[number]):
                texts[number] = text.strip()
    return [ExtractedBlock(text=text, page_number=number) for number, text in texts.items() if text]
