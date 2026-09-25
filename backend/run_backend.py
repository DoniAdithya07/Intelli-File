"""Entry point of the packaged backend (Phase 14). The desktop shell
spawns this — as `intellifile-backend --port N` — and points it at the
bundled models/data through INTELLIFILE_MODELS_DIR / INTELLIFILE_DATA_DIR.
Also usable in development: `venv/bin/python run_backend.py --port 8756`.

Binds to 127.0.0.1 only: the API is for this machine's own desktop shell.
"""

import argparse
import os
import sys
import time

_T0 = time.perf_counter()


def _phase(name: str) -> None:
    """Startup-phase timing to stderr (and backend-console.log in the app):
    where a slow first launch actually goes — bootloader, imports, models."""
    print(f"[startup] {name}: {time.perf_counter() - _T0:5.1f}s since process start", file=sys.stderr, flush=True)


import uvicorn  # noqa: E402

_phase("uvicorn imported")


def _exit_with_parent() -> None:
    """Exit when the desktop shell that spawned us is gone. The shell kills
    the backend on a normal quit, but on Windows a crashed or force-closed
    parent leaves its children running, and the orphan kept port 8756 so the
    next launch showed "Engine Offline" (found in the Windows port,
    2026-09-25). psutil's is_running() compares creation time, so a reused
    PID is not mistaken for the shell."""
    pid = os.environ.get("INTELLIFILE_PARENT_PID")
    if not pid:
        return
    import threading

    import psutil

    try:
        parent = psutil.Process(int(pid))
    except (ValueError, psutil.Error):
        return

    def watch() -> None:
        while parent.is_running():
            time.sleep(2)
        print("[shutdown] desktop shell exited — stopping the backend", file=sys.stderr, flush=True)
        os._exit(0)

    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("INTELLIFILE_PORT", "8756")))
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    # A frozen bundle must not depend on the working directory it was launched from.
    if getattr(sys, "frozen", False):
        os.chdir(os.path.dirname(sys.executable))
    _exit_with_parent()
    if os.environ.get("INTELLIFILE_IMPORT_TRACE"):
        # Which library's import is slow in the frozen bundle — one at a time.
        import importlib

        for name in ("numpy", "onnxruntime", "tokenizers", "pyarrow", "lancedb", "av", "PIL", "transformers", "transformers.models.whisper.feature_extraction_whisper", "llama_cpp", "watchdog.observers", "fastapi", "psutil", "app.main"):
            t = time.perf_counter()
            try:
                importlib.import_module(name)
                _phase(f"import {name} took {time.perf_counter() - t:4.1f}s —")
            except Exception as e:
                _phase(f"import {name} FAILED {type(e).__name__}: {str(e)[:60]} —")

    from app.main import app  # imported here so --help stays instant

    _phase("app imported (onnxruntime, lancedb, transformers, llama_cpp …)")

    # log_config=None: uvicorn's loggers propagate to the root logger the app
    # configures, so requests and errors reach <app data>/logs/backend.log too.
    uvicorn.run(app, host=args.host, port=args.port, log_level="info", log_config=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
