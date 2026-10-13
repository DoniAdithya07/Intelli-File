"""Universal audio decoding via PyAV — handles WAV (the mic recording
format), plus MP3/M4A/FLAC/OGG/AIFF and effectively anything ffmpeg can
read (PyAV bundles its own codec libraries, no separate ffmpeg install
needed on the user's machine). Always resampled to mono 16kHz for
Whisper via PyAV's own resampler, which is real audio resampling
(ffmpeg's swresample), not a hand-rolled approximation.
"""

import io
from pathlib import Path

import numpy as np

WHISPER_SAMPLE_RATE = 16000


def load_audio_as_mono_16k(source: bytes | Path, max_seconds: float | None = None) -> np.ndarray:
    """`max_seconds` stops decoding after that much audio (the first minutes
    of a video's soundtrack): the rest of the file is never read."""
    # PyAV is imported here, not at module load: its ffmpeg libraries are
    # what macOS scans on the first launch of a fresh build (~18 s measured
    # in the frozen bundle), and nothing needs them until audio is decoded.
    import av

    file_like = io.BytesIO(source) if isinstance(source, bytes) else str(source)
    container = av.open(file_like)
    try:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="flt", layout="mono", rate=WHISPER_SAMPLE_RATE)

        chunks = []
        limit = None if max_seconds is None else int(max_seconds * WHISPER_SAMPLE_RATE)
        decoded = 0
        for frame in container.decode(stream):
            for resampled in resampler.resample(frame):
                chunks.append(resampled.to_ndarray())
                decoded += resampled.samples
            if limit is not None and decoded >= limit:
                break
        for resampled in resampler.resample(None):  # flush any buffered samples
            chunks.append(resampled.to_ndarray())
    finally:
        container.close()

    if not chunks:
        return np.zeros(0, dtype=np.float32)
    audio = np.concatenate(chunks, axis=1) if chunks[0].ndim > 1 else np.concatenate(chunks)
    audio = audio.reshape(-1).astype(np.float32)
    return audio if limit is None else audio[:limit]
