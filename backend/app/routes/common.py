"""Helpers shared by the route modules."""
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Bounds for user-supplied sizes. Found in the 2026-09-11 audit: top_k=0
# reached LanceDB and raised ("Limit is required"), a 500 for the user.
MAX_TOP_K = 100
MIN_THUMBNAIL, MAX_THUMBNAIL = 16, 1024
# 16 kHz mono 16-bit = 32 KB/s; below ~0.3 s there is no speech to find.
MIN_AUDIO_BYTES = 10_000
# A spoken query is seconds long (~1 MB a minute as webm). Bigger uploads are
# refused before Whisper decodes them into memory (2026-10-04).
MAX_AUDIO_BYTES = 25 * 1024 * 1024


def safe_path(raw: str) -> Path | None:
    """Phase 12 path validation: absolute, no NUL bytes, `..` resolved. The
    user picks paths through a native dialog, so anything else is not a
    real request."""
    raw = (raw or "").strip()
    if not raw or "\x00" in raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return None
    # normpath folds `..` and `.` without resolving symlinks — the path the
    # user picked (e.g. /var/…) stays the path they see, and it still
    # cannot climb anywhere the raw string did not already name.
    return Path(os.path.normpath(str(path)))


def clamp_top_k(top_k: int) -> int:
    return max(1, min(top_k, MAX_TOP_K))


def remember(state, kind: str, **fields) -> None:
    """Record a usage event unless the user switched activity memory off.
    Never lets a bookkeeping failure break the request it rides on."""
    if not state.settings.get("remember_activity"):
        return
    try:
        state.usage_store.record(kind, **fields)
    except Exception:
        logger.exception("could not record usage event %s", kind)
