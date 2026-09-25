"""On-demand thumbnails for visual search results.

Generated per request rather than stored: a saved thumbnail would
duplicate the user's own data into the app directory and then need
invalidating whenever the source file changes, moves or is deleted.
Re-encoding a small JPEG is cheap enough that none of that complexity
earns its place.
"""

import io
from pathlib import Path

from PIL import Image, ImageOps

from .files.discovery import VIDEO_EXTENSIONS
from .indexing.visual_indexer import decode_frame_at

DEFAULT_MAX_SIZE = 256
JPEG_QUALITY = 85


def generate_thumbnail(
    path: Path,
    timestamp_offset_seconds: float | None = None,
    max_size: int = DEFAULT_MAX_SIZE,
) -> bytes:
    """Return JPEG bytes for a thumbnail of the image at `path`,
    preserving aspect ratio within a max_size box.

    For a video, `timestamp_offset_seconds` is the moment to show (the
    keyframe that matched); for a photo it is ignored.
    """
    if path.suffix.lower() in VIDEO_EXTENSIONS:
        image = decode_frame_at(path, timestamp_offset_seconds or 0.0).convert("RGB")
        image.thumbnail((max_size, max_size), Image.BICUBIC)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
        return buffer.getvalue()
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        image.thumbnail((max_size, max_size), Image.BICUBIC)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()
