"""Shared shape all extractors produce: a document is a list of text
blocks, each optionally tagged with where it came from (page, heading,
section), per the PRD's Text Extraction section.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractedBlock:
    text: str
    page_number: int | None = None
    heading: str | None = None
    section: str | None = None
