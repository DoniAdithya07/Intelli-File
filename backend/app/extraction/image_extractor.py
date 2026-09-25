"""Image metadata extraction for visual search.

Deliberately NOT part of extractor.py's dispatch: that pipeline exists to
turn documents into text blocks for chunking/embedding/BM25, and a photo
has no text to extract. Images go through the CLIP vision tower instead
(see indexing/visual_indexer.py); the only thing needed here is the
"when was this taken" metadata the PRD asks results to display.
"""

from datetime import datetime
from pathlib import Path

from PIL import ExifTags, Image

_EXIF_DATE_TIME_ORIGINAL = 36867  # when the shot was actually taken
_EXIF_DATE_TIME = 306  # file/image last-modified per EXIF, a weaker fallback
_EXIF_DATE_FORMAT = "%Y:%m:%d %H:%M:%S"


def extract_captured_at(path: Path) -> str:
    """When the photo was taken (EXIF), falling back to the filesystem
    mtime. Returns an ISO-8601 string. Never raises — a photo with
    corrupt or absent EXIF is still perfectly searchable, so metadata
    problems must not fail the whole indexing job."""
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            raw = exif.get_ifd(ExifTags.IFD.Exif).get(_EXIF_DATE_TIME_ORIGINAL) or exif.get(_EXIF_DATE_TIME)
            if raw:
                return datetime.strptime(str(raw).strip(), _EXIF_DATE_FORMAT).isoformat()
    except Exception:
        pass
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat()
