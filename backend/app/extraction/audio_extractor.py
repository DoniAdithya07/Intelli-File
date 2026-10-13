from pathlib import Path

from ..transcription.transcriber import Transcriber
from .blocks import ExtractedBlock


def extract_audio(path: Path, transcriber: Transcriber, max_seconds: float | None = None) -> list[ExtractedBlock]:
    """One block containing the file's full transcript — no page/heading
    concept for audio, same as plain TXT/MD. `max_seconds`: only the start
    (a video's soundtrack)."""
    text = transcriber.transcribe(path, max_seconds=max_seconds)
    if not text.strip():
        return []
    return [ExtractedBlock(text=text)]
