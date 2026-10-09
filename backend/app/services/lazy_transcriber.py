import logging
import threading
import time
from pathlib import Path

from ..transcription import Transcriber

logger = logging.getLogger(__name__)


class SpeechModelUnavailable(RuntimeError):
    """The speech model could not be loaded: audio cannot be transcribed."""


class LazyTranscriber:
    """Transcriber that loads its model on first use; same interface."""

    def __init__(self, model_dir: Path):
        self._model_dir = model_dir
        self._real = None
        self._failure: SpeechModelUnavailable | None = None
        self._lock = threading.Lock()

    def _get(self):
        with self._lock:
            # (2026-10-05) A load that failed is not retried for every audio
            # file (each try is slow and logged nothing): it is logged once
            # and every audio file then fails with this one clear reason.
            if self._failure is not None:
                raise self._failure
            if self._real is None:
                t0 = time.perf_counter()
                try:
                    self._real = Transcriber(self._model_dir)
                except Exception as e:
                    logger.error("speech model %s could not be loaded", self._model_dir.name, exc_info=True)
                    self._failure = SpeechModelUnavailable(f"the speech model could not be loaded ({e}), so audio files cannot be transcribed")
                    raise self._failure from e
                logger.info("speech model %s loaded on first use in %.1f s", self._model_dir.name, time.perf_counter() - t0)
            return self._real

    def transcribe(self, *args, **kwargs):
        return self._get().transcribe(*args, **kwargs)

    @property
    def model(self):
        return self._get().model

    @property
    def active_provider(self):
        return self._get().active_provider
