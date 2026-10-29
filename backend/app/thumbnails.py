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

from .files.discovery import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
from .files.media import is_audio_only_media
from .indexing.visual_indexer import decode_frame_at

DEFAULT_MAX_SIZE = 256
JPEG_QUALITY = 85


class NoThumbnail(Exception):
    """The file has no picture to show (audio). Not a failure: the caller
    answers "no thumbnail" quietly, the UI shows its audio icon."""


def _to_rgb(image: Image.Image) -> Image.Image:
    """RGB copy of `image`. 16-bit greyscale (PNG/TIFF) is scaled down to 8 bits
    first: converting it directly clips every value above 255 to white."""
    if image.mode in ("I", "I;16", "I;16L", "I;16B"):
        image = image.point(lambda v: v * (1 / 256)).convert("L")
    return image.convert("RGB")


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
    if path.suffix.lower() in AUDIO_EXTENSIONS or is_audio_only_media(path):
        raise NoThumbnail(path.name)
    if path.suffix.lower() in VIDEO_EXTENSIONS:
        image = decode_frame_at(path, timestamp_offset_seconds or 0.0).convert("RGB")
        image.thumbnail((max_size, max_size), Image.BICUBIC)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
        return buffer.getvalue()
    with Image.open(path) as opened:
        opened.draft("RGB", (max_size, max_size))  # JPEG: cheap reduced-scale decode of big phone photos
        image = _to_rgb(ImageOps.exif_transpose(opened))
        image.thumbnail((max_size, max_size), Image.BICUBIC)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()
