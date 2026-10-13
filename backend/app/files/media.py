"""Which media files are really audio (2026-10-12).

A file's extension does not say what is inside it. WhatsApp saves voice
notes as ".mpeg" (an MPEG container holding only MP3 audio), phones and
screen recorders save audio-only .mp4/.mkv/.webm/.3gp, and none of these has
a picture to look at. Sorted by extension they went to the photo/video
pipeline, which has no frame to decode (StopIteration in the thumbnail and
keyframe code), so their speech was never transcribed. The container's own
stream list answers it: opening it reads a few KB, no decoding.
"""

import functools
from pathlib import Path

from .discovery import VIDEO_EXTENSIONS


def _probe(path: Path) -> tuple[bool, bool]:
    """(has a real video stream, has an audio stream). A cover-art picture
    inside an MP3/M4A is a "video" stream to FFmpeg but not a video."""
    import av  # lazy: see transcription/audio_io.py

    with av.open(str(path)) as container:
        real_video = any(not _is_cover_art(s) for s in container.streams.video)
        return real_video, len(container.streams.audio) > 0


def _is_cover_art(stream) -> bool:
    from av.stream import Disposition

    flags = getattr(stream, "disposition", None)
    # `flags.attached_pic` would be the flag itself (always truthy); the bit has to be tested.
    return flags is not None and bool(flags & Disposition.attached_pic)


@functools.lru_cache(maxsize=4096)
def _cached_probe(path: str, size: int, mtime_ns: int) -> tuple[bool, bool] | None:
    """None when the container cannot be opened (damaged file): the caller
    keeps its extension-based guess. Keyed by size and mtime so an edited
    file is probed again."""
    try:
        return _probe(Path(path))
    except Exception:  # noqa: BLE001 — any decoder error means "unknown", never a failed scan
        return None


def probe_media(path: Path) -> tuple[bool, bool] | None:
    """(has video, has audio) of a media file, or None when unreadable."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return _cached_probe(str(path), stat.st_size, stat.st_mtime_ns)


def is_audio_only_media(path: Path) -> bool:
    """True for a file with a video extension whose container has audio and
    no video stream. A damaged file (cannot be probed) stays a video."""
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        return False
    probed = probe_media(path)
    return probed is not None and not probed[0] and probed[1]


def has_audio_stream(path: Path) -> bool:
    probed = probe_media(path)
    return probed is not None and probed[1]
