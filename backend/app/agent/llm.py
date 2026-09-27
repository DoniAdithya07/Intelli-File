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


def _llm_dirs() -> list[Path]:
    """Where the model can be after unzipping. The app ships as two zips
    (IntelliFile-part1/-part2, before the single zip of 2026-09-27) that
    both hold IntelliFile/…. Unzipped to the same place they merge into
    models/llm; but Windows' "Extract All" defaults each zip to its own
    folder (Downloads\\IntelliFile-part2\\IntelliFile\\models\\llm), and a
    user may unzip part 2 *inside* the app folder — look there too."""
    models = models_dir()
    root = models.parent  # the IntelliFile folder in the packaged app
    dirs = [models / "llm", root / "IntelliFile" / "models" / "llm"]
    for base in (root.parent, root.parent.parent):
        dirs += [p / "IntelliFile" / "models" / "llm" for p in sorted(base.glob("IntelliFile-part2*"))]
    return dirs


def find_model_file(model_dir: Path | None = None) -> Path | None:
    for d in [model_dir] if model_dir else _llm_dirs():
        candidates = sorted(d.glob("*.gguf")) if d.is_dir() else []
        if candidates:
            return candidates[0]
    return None


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
        # One entry per model call ({"kind", "seconds", "prompt_tokens",
        # "completion_tokens"}) so the evaluation can see where Ask's time
        # goes; the caller clears it.
        self.calls: list[dict] = []

    def chat(self, messages: list[dict], max_tokens: int = 300, json_only: bool = False, temperature: float = 0.0, deadline: float | None = None, label: str | None = None) -> str:
        """One completion. With `deadline` (a time.perf_counter() value) the
        call streams internally and stops generating once it passes, so a
        starved CPU cannot hold Ask past its cap (the live Windows run hit
        67 s against a 50 s cap); the partial text is returned and the
        caller treats unparseable output as "answer now"."""
        kwargs = {"response_format": {"type": "json_object"}} if json_only else {}
        t0 = time.perf_counter()
        usage: dict = {}
        with self._lock:
            if deadline is None:
                out = self.llm.create_chat_completion(messages=messages, max_tokens=max_tokens, temperature=temperature, **kwargs)
                usage = out.get("usage") or {}
                text = out["choices"][0]["message"]["content"] or ""
            else:
                pieces: list[str] = []
                for chunk in self.llm.create_chat_completion(messages=messages, max_tokens=max_tokens, temperature=temperature, stream=True, **kwargs):
                    pieces.append(chunk["choices"][0].get("delta", {}).get("content") or "")
                    if time.perf_counter() > deadline:
                        usage["stopped_at_deadline"] = True
                        break
                text = "".join(pieces)
        self.calls.append({"kind": label or ("plan" if json_only else "chat"), "seconds": time.perf_counter() - t0,
                           "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
                           "stopped_at_deadline": bool(usage.get("stopped_at_deadline"))})
        return text

    def stream(self, messages: list[dict], max_tokens: int = 400, temperature: float = 0.0) -> Iterator[str]:
        t0 = time.perf_counter()
        n = 0
        try:
            with self._lock:
                for chunk in self.llm.create_chat_completion(messages=messages, max_tokens=max_tokens, temperature=temperature, stream=True):
                    delta = chunk["choices"][0].get("delta", {})
                    if delta.get("content"):
                        n += 1
                        yield delta["content"]
        finally:
            self.calls.append({"kind": "stream", "seconds": time.perf_counter() - t0, "prompt_tokens": None, "completion_tokens": n})

    def benchmark(self, prompt: str = "Write one sentence about file search.", max_tokens: int = 48) -> dict:
        t0 = time.perf_counter()
        n = 0
        for _ in self.stream([{"role": "user", "content": prompt}], max_tokens=max_tokens):
            n += 1
        seconds = time.perf_counter() - t0
        return {"tokens": n, "seconds": round(seconds, 2), "tokens_per_second": round(n / seconds, 1) if seconds else None}
