"""The local language model behind the agent (Phase 19): a GGUF model run
by llama.cpp, fully offline, on this machine. The agent code talks to the
small interface here — `chat()` for a JSON planning turn, `stream()` for
the answer — so the model file can be swapped (1.5B ↔ 3B, another family)
without touching the loop, and so tests can substitute a scripted stand-in.
"""

from collections import deque
import logging
import os
import platform
import threading
import time
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

from ..paths import models_dir

MODEL_DIR = models_dir() / "llm"
CONTEXT_TOKENS = 4096
MAX_THREADS = 8  # a 1.5B model on CPU is memory-bound well before 8 cores

# A fixed ~1,200-character prompt read once at load: it measures this
# computer's prompt-reading and writing speed for the agent's time budget,
# and it pulls the memory-mapped weights in from disk so the first question
# does not pay for that (measured 2026-10-04: a 414-token prompt took 53.6 s
# on the first call after a load, 4-7 s afterwards).
CALIBRATION_PROMPT = ("Files on this computer: invoices, travel plans, recipes, training logs, insurance policies and meeting notes. " * 11).strip()
CALIBRATION_TOKENS = 16


def default_threads() -> int:
    """One llama.cpp thread per physical core, for reading the prompt as well
    as for writing. llama-cpp-python's own default reads with every logical
    CPU (n_threads_batch = cpu_count()): on a hyperthreaded or hybrid laptop
    that oversubscribes the cores, and as soon as anything else runs (the
    indexer, a browser) prompt reading stalls. Measured 2026-10-04 on an
    i5-12450H with other work running, the same 414-token prompt: 7-54 s
    with the default (6 + 12 threads), 4.1-4.6 s with 6 + 6."""
    try:
        import psutil

        cores = psutil.cpu_count(logical=False)
    except Exception:  # noqa: BLE001 — fall back to half the logical CPUs
        cores = None
    if not cores:
        cores = max(1, (os.cpu_count() or 2) // 2)
    return max(1, min(cores, MAX_THREADS))


def _blend(old: float | None, sample: float) -> float:
    return sample if old is None else 0.5 * old + 0.5 * sample


def _prompt_text(messages: list[dict]) -> str:
    return "\n".join(f"{m['role']}:{m['content']}" for m in messages)


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
        threads = n_threads or default_threads()
        self.llm = Llama(
            model_path=str(model_path),
            n_ctx=CONTEXT_TOKENS,
            n_threads=threads,
            n_threads_batch=threads,
            n_gpu_layers=0,  # CPU on purpose: predictable on any grading laptop; Metal/DirectML are Phase 14 experiments
            verbose=False,
        )
        self._lock = threading.Lock()
        self.n_ctx = CONTEXT_TOKENS  # the agent sizes every prompt to fit it (loop._fits)
        self.name = model_path.stem
        self.load_seconds = round(time.perf_counter() - t0, 1)
        # One entry per model call ({"kind", "seconds", "prompt_tokens",
        # "completion_tokens", "first_token_s"}) so the evaluation can see
        # where Ask's time goes; the caller clears it.
        self.calls: deque[dict] = deque(maxlen=200)  # bounded: a long session must not grow memory (code review 2026-09-27)
        # What llama.cpp still holds from the previous call: it re-reads only
        # the part of a new prompt after the shared beginning, so a planning
        # turn that adds one search result costs that result, not the lot.
        self._prev_prompt = ""
        self.prefill_chars_per_second: float | None = None  # measured by the calibration below
        self.tokens_per_second: float | None = None
        self.speed = self._calibrate()
        logger.info("local LLM %s loaded in %.1f s (%s threads, CPU %s), calibrated in %.1f s: reads %.0f prompt chars/s, writes %.1f tokens/s",
                    self.name, self.load_seconds, threads, platform.processor() or "unknown", self.speed["seconds"],
                    self.prefill_chars_per_second, self.tokens_per_second)

    def _calibrate(self) -> dict:
        t0 = time.perf_counter()
        # Every token uses every layer, so one tiny call pages all the weights
        # in; measured separately, so a slow disk does not pass for a slow CPU.
        with self._lock:
            self.llm.create_chat_completion(messages=[{"role": "user", "content": "Hi"}], max_tokens=1, temperature=0.0)
        warm = time.perf_counter() - t0
        for _ in self.stream([{"role": "user", "content": CALIBRATION_PROMPT}], max_tokens=CALIBRATION_TOKENS):
            pass
        self.calls.clear()
        # Forget the calibration prompt: llama.cpp would otherwise reuse its
        # cached start for the first question, which shifts the numbers enough
        # to change a greedy answer (2026-10-04: the sourdough question of the
        # answer key flipped from right to wrong).
        self.llm.reset()
        self._prev_prompt = ""
        self.prefill_chars_per_second = self.prefill_chars_per_second or 500.0  # the calibration call wrote nothing: assume a slow machine
        self.tokens_per_second = self.tokens_per_second or 5.0
        return {"seconds": round(time.perf_counter() - t0, 1), "warm_up_seconds": round(warm, 1),
                "prompt_chars_per_second": round(self.prefill_chars_per_second), "tokens_per_second": round(self.tokens_per_second, 1)}

    def tokenize(self, text: str) -> list[int]:
        """The model's own tokens for `text` (no lock: only the vocabulary is
        read, and the agent calls this between, not during, generations)."""
        return self.llm.tokenize(text.encode("utf-8"), add_bos=False, special=True)

    def _new_chars(self, text: str) -> int:
        return len(text) - len(os.path.commonprefix([text, self._prev_prompt]))

    def estimate_seconds(self, messages: list[dict], new_tokens: int) -> float:
        """How long a call would take on this computer now: reading the part
        of the prompt llama.cpp does not already hold, then writing
        `new_tokens`. The rates come from the calibration and every call
        updates them (the indexer starting up slows both)."""
        return self._new_chars(_prompt_text(messages)) / self.prefill_chars_per_second + new_tokens / self.tokens_per_second

    def _observe(self, messages: list[dict], first_token_s: float | None, total_s: float, tokens: int) -> None:
        """Fold a finished call into the speed estimates (half old, half new)."""
        text = _prompt_text(messages)
        new_chars = self._new_chars(text)
        self._prev_prompt = text
        if first_token_s and new_chars >= 300:
            self.prefill_chars_per_second = _blend(self.prefill_chars_per_second, new_chars / first_token_s)
        if first_token_s is not None and tokens >= 8 and total_s > first_token_s:
            self.tokens_per_second = _blend(self.tokens_per_second, (tokens - 1) / (total_s - first_token_s))

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
                self._prev_prompt = _prompt_text(messages)
            else:
                pieces: list[str] = []
                first = None
                for chunk in self.llm.create_chat_completion(messages=messages, max_tokens=max_tokens, temperature=temperature, stream=True, **kwargs):
                    piece = chunk["choices"][0].get("delta", {}).get("content") or ""
                    if piece:
                        first = first or time.perf_counter() - t0
                        pieces.append(piece)
                    if time.perf_counter() > deadline:
                        usage["stopped_at_deadline"] = True
                        break
                text = "".join(pieces)
                usage["first_token_s"] = first
                self._observe(messages, first, time.perf_counter() - t0, len(pieces))
        self.calls.append({"kind": label or ("plan" if json_only else "chat"), "seconds": time.perf_counter() - t0,
                           "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
                           "stopped_at_deadline": bool(usage.get("stopped_at_deadline")), "first_token_s": usage.get("first_token_s")})
        return text

    def stream(self, messages: list[dict], max_tokens: int = 400, temperature: float = 0.0) -> Iterator[str]:
        t0 = time.perf_counter()
        n = 0
        first = None
        try:
            with self._lock:
                for chunk in self.llm.create_chat_completion(messages=messages, max_tokens=max_tokens, temperature=temperature, stream=True):
                    delta = chunk["choices"][0].get("delta", {})
                    if delta.get("content"):
                        n += 1
                        first = first or time.perf_counter() - t0
                        yield delta["content"]
        finally:
            total = time.perf_counter() - t0
            self._observe(messages, first, total, n)
            self.calls.append({"kind": "stream", "seconds": total, "prompt_tokens": None, "completion_tokens": n, "first_token_s": first})

    def benchmark(self, prompt: str = "Write one sentence about file search.", max_tokens: int = 48) -> dict:
        t0 = time.perf_counter()
        n = 0
        for _ in self.stream([{"role": "user", "content": prompt}], max_tokens=max_tokens):
            n += 1
        seconds = time.perf_counter() - t0
        return {"tokens": n, "seconds": round(seconds, 2), "tokens_per_second": round(n / seconds, 1) if seconds else None}
