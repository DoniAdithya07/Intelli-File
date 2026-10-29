"""Image metadata extraction for visual search.

Deliberately NOT part of extractor.py's dispatch: that pipeline exists to
turn documents into text blocks for chunking/embedding/BM25, and a photo
has no text to extract. Images go through the CLIP vision tower instead
(see indexing/visual_indexer.py); the only thing needed here is the
"when was this taken" metadata the PRD asks results to display.
"""

import logging
from datetime import datetime
from pathlib import Path

from PIL import ExifTags, Image

_EXIF_DATE_TIME_ORIGINAL = 36867  # when the shot was actually taken
_EXIF_DATE_TIME = 306  # file/image last-modified per EXIF, a weaker fallback
_EXIF_DATE_FORMAT = "%Y:%m:%d %H:%M:%S"

logger = logging.getLogger(__name__)


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
        # (2026-10-05) Was silent: the reason is now in the debug log.
        logger.debug("No EXIF capture date in %s; using the file's date", path, exc_info=True)
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    except OSError:
        # The file vanished or moved between discovery and this read (the
        # live watcher runs concurrently with scans; a cloud-only file can
        # also be evicted mid-scan) -- this function is documented to never
        # raise, so the fallback must have its own fallback (found in review
        # 2026-10-29).
        logger.debug("%s is gone by the time its date was read; using now", path, exc_info=True)
        return datetime.now().isoformat()
