"""One logging setup for the backend (2026-09-21 macOS pass).

The packaged app runs the backend without a terminal, so anything that
only went to stdout — the "offline guard ON", "model not found", the
"cannot list ~/Documents" warnings — vanished. Everything now also lands
in <app data>/logs/backend.log (rotating, 3 × 5 MB), which is where to
look when "the engine stays offline" on someone else's machine.
"""

import logging
import logging.handlers
from pathlib import Path

_configured = False


def configure(app_data_dir: Path) -> Path:
    global _configured
    logs = app_data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "backend.log"
    if _configured:
        return path
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in root.handlers):
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        root.addHandler(stream)
    # Quiet the libraries that log every request/partition at INFO.
    for noisy in ("onnxruntime", "lancedb", "httpx", "PIL", "watchdog"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True
    return path
