"""The local language model behind the agent (Phase 19): a GGUF model run
by llama.cpp, fully offline, on this machine. The agent code talks to the
small interface here — `chat()` for a JSON planning turn, `stream()` for
the answer — so the model file can be swapped (1.5B ↔ 3B, another family)
without touching the loop, and so tests can substitute a scripted stand-in.
"""

import logging
import threading
import time
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

from ..paths import models_dir

MODEL_DIR = models_dir() / "llm"
CONTEXT_TOKENS = 4096


def find_model_file(model_dir: Path | None = None) -> Path | None:
    model_dir = model_dir or (models_dir() / "llm")
    if not model_dir.exists():
        return None
    candidates = sorted(model_dir.glob("*.gguf"))
    return candidates[0] if candidates else None


class LocalLLM:
    """llama.cpp wrapper. One model instance, one request at a time (the
    lock) — llama.cpp contexts are not re-entrant, and a laptop has no
    spare cores for two answers at once anyway."""

    def __init__(self, model_path: Path, n_threads: int | None = None):
        from llama_cpp import Llama

        t0 = time.perf_counter()
        self.model_path = model_path
        self.llm = Llama(
            model_path=str(model_path),
            n_ctx=CONTEXT_TOKENS,
            n_threads=n_threads,
            n_gpu_layers=0,  # CPU on purpose: predictable on any grading laptop; Metal/DirectML are Phase 14 experiments
            verbose=False,
        )
        self._lock = threading.Lock()
        self.name = model_path.stem
        self.load_seconds = round(time.perf_counter() - t0, 1)

    def chat(self, messages: list[dict], max_tokens: int = 300, json_only: bool = False, temperature: float = 0.0) -> str:
        kwargs = {"response_format": {"type": "json_object"}} if json_only else {}
        with self._lock:
            out = self.llm.create_chat_completion(messages=messages, max_tokens=max_tokens, temperature=temperature, **kwargs)
        return out["choices"][0]["message"]["content"] or ""

    def stream(self, messages: list[dict], max_tokens: int = 400, temperature: float = 0.0) -> Iterator[str]:
        with self._lock:
            for chunk in self.llm.create_chat_completion(messages=messages, max_tokens=max_tokens, temperature=temperature, stream=True):
                delta = chunk["choices"][0].get("delta", {})
                if delta.get("content"):
                    yield delta["content"]

    def benchmark(self, prompt: str = "Write one sentence about file search.", max_tokens: int = 48) -> dict:
        t0 = time.perf_counter()
        n = 0
        for _ in self.stream([{"role": "user", "content": prompt}], max_tokens=max_tokens):
            n += 1
        seconds = time.perf_counter() - t0
        return {"tokens": n, "seconds": round(seconds, 2), "tokens_per_second": round(n / seconds, 1) if seconds else None}
