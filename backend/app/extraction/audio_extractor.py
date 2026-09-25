from pathlib import Path

from ..transcription.transcriber import Transcriber
from .blocks import ExtractedBlock


def extract_audio(path: Path, transcriber: Transcriber) -> list[ExtractedBlock]:
    """One block containing the file's full transcript — no page/heading
    concept for audio, same as plain TXT/MD."""
    text = transcriber.transcribe(path)
    if not text.strip():
        return []
    return [ExtractedBlock(text=text)]
