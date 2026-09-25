from pathlib import Path

from pypdf import PdfReader

from .blocks import ExtractedBlock


def extract_pdf(path: Path) -> list[ExtractedBlock]:
    """One block per page, so page_number survives into search results
    (per the PRD's Text Extraction section)."""
    reader = PdfReader(str(path))
    blocks = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        text = text.strip()
        if text:
            blocks.append(ExtractedBlock(text=text, page_number=i + 1))
    return blocks
