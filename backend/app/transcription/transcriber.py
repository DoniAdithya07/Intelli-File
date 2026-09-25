"""Local speech-to-text: audio bytes/file in, text out. Runs fully
offline once the model files exist locally (see
scripts/download_whisper_model.py) — no network calls happen here.

Runs on CPU on purpose. Measured 2026-09-11 on the user's own mic
recordings: with the CoreML provider, whisper-base.en returned a single
word per clip and whisper-small.en returned "I!!!!!!..." for every clip,
while tiny.en was merely *slower* than CPU (0.31s vs 0.14s per clip). On
CPU all three transcribe correctly. Base.en on CPU is ~0.2s per spoken
sentence — already instant from the user's point of view.

Since Phase 14 (2026-09-21) the model runs on onnxruntime alone
(whisper_onnx.py) instead of optimum + PyTorch: the same two ONNX files,
the same greedy decoding, verified identical on 30 of 30 clip/prompt
pairs — and 600 MB of torch no longer ships.
"""

import logging
from pathlib import Path

import numpy as np

from .audio_io import WHISPER_SAMPLE_RATE, load_audio_as_mono_16k
from .whisper_onnx import WhisperOnnx

logger = logging.getLogger(__name__)


# Whisper allows up to 224 prompt tokens; well under that keeps decoding
# fast and leaves the model's attention on the audio.
MAX_PROMPT_TOKENS = 160

# Whisper hears exactly 30 s at a time: its feature extractor pads or
# truncates every input to that window. Found 2026-09-21: a 67 s recording
# came back as its first 30 s (84 words) and a word spoken at 55 s never
# appeared — every voice memo or meeting recording longer than 30 s was
# indexed by its opening only. Longer audio is now transcribed window by
# window. Each cut is moved to the quietest moment in the last
# CUT_SEARCH_SECONDS of the window so a word is not split across two
# windows; a window that is effectively silent is skipped, because Whisper
# hallucinates text ("Thank you.") on silence.
WINDOW_SECONDS = 30
CUT_SEARCH_SECONDS = 3.0
CUT_FRAME_SECONDS = 0.1
SILENCE_RMS = 1e-4


class Transcriber:
    def __init__(self, model_dir: Path):
        if not (model_dir / "onnx").exists():
            raise FileNotFoundError(
                f"Whisper model files not found in {model_dir}. Run scripts/download_whisper_model.py first."
            )
        self.model = WhisperOnnx(model_dir)
        self.active_provider = self.model.active_provider

    def transcribe(self, audio_source: bytes | Path, vocabulary_hint: list[str] | None = None) -> str:
        """`vocabulary_hint`: names Whisper should be able to spell — in
        practice the user's own file names. Whisper decodes conditioned on
        a text prompt, and words in that prompt become far more likely
        outputs. Found necessary in the live test (2026-09-11): "open
        abhisek plan" came back as "happy shake clan" — no speech model
        knows a person's name unprompted. Measured on synthesized speech:
        "gurtucheyatam" went from "good 2 chair tomorrow" to the right
        word, "Abhisek" from "Abhishek" to the user's spelling, with no
        change on ordinary sentences. A natural-sentence prompt works; a
        bare comma list made the model emit comma-separated fragments."""
        audio = load_audio_as_mono_16k(audio_source)
        if audio.size == 0:
            return ""
        prompt_ids = self._prompt_ids(vocabulary_hint)
        window = WINDOW_SECONDS * WHISPER_SAMPLE_RATE
        if audio.size <= window:
            # A recording that fits one window (every voice-search clip) is
            # transcribed exactly as before.
            return self._transcribe_window(audio, prompt_ids)

        pieces: list[str] = []
        start = 0
        while start < audio.size:
            end = min(start + window, audio.size)
            if end < audio.size:
                end = _quietest_cut(audio, end - int(CUT_SEARCH_SECONDS * WHISPER_SAMPLE_RATE), end)
            piece = audio[start:end]
            if _rms(piece) >= SILENCE_RMS:
                text = self._transcribe_window(piece, prompt_ids)
                if text:
                    pieces.append(text)
            start = end
        return " ".join(pieces)

    def _prompt_ids(self, vocabulary_hint: list[str] | None):
        if not vocabulary_hint:
            return None
        # Fit as many hints as the budget allows, most-recent first, instead
        # of all-or-nothing: 40 real file names came to 183 tokens, and the
        # previous check then dropped every hint, silently disabling
        # prompting for anyone with a normal number of files (2026-09-18:
        # "gym plan.txt" indexed, "gym plan" spoken → "JimPlan").
        hints = list(vocabulary_hint)
        while hints:
            prompt = "Search my files such as " + ", ".join(hints) + "."
            prompt_ids = self.model.prompt_ids(prompt)
            if len(prompt_ids) <= MAX_PROMPT_TOKENS:
                return prompt_ids
            hints.pop()
        return None

    def _transcribe_window(self, audio: np.ndarray, prompt_ids) -> str:
        return self.model.transcribe_window(audio, prompt_ids)


def _rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0


def _quietest_cut(audio: np.ndarray, lo: int, hi: int) -> int:
    """Sample index of the quietest CUT_FRAME_SECONDS frame in audio[lo:hi]
    (its midpoint), so a window boundary lands in a pause, not a word."""
    frame = int(CUT_FRAME_SECONDS * WHISPER_SAMPLE_RATE)
    lo = max(0, lo)
    if hi - lo <= frame:
        return hi
    best_start, best_rms = hi - frame, float("inf")
    for start in range(lo, hi - frame + 1, frame // 2):
        rms = _rms(audio[start : start + frame])
        if rms < best_rms:
            best_start, best_rms = start, rms
    return best_start + frame // 2
