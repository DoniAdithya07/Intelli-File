"""User settings that the backend honours, kept in config/settings.json.

Phase 16 adds the first one: `remember_activity`. Phase 10's power toggles
and Phase 17's `personalize` join it here; the Settings page reads and
writes the whole document through GET/POST /settings.
"""

import json
import threading
from pathlib import Path

SETTINGS_FILE = "settings.json"

DEFAULTS: dict = {
    # Phase 16 — record searches/opens/reveals locally so Phase 17 can
    # personalize. Off = nothing is written and the store stays as it is.
    "remember_activity": True,
    # Phase 17 — let the profile nudge search ranking and drive
    # recommendations. Off = pure retrieval order, no recommendations.
    "personalize": True,
    # Phase 10 — power-aware indexing (the PRD's "pauses on battery by default").
    "pause_on_battery": True,
    "pause_on_low_power": True,
    "resource_mode": "balanced",  # balanced | performance | battery_saver
}


class Settings:
    def __init__(self, config_dir: Path):
        self._path = config_dir / SETTINGS_FILE
        self._lock = threading.Lock()
        self._values = dict(DEFAULTS)
        self._load()

    def _load(self) -> None:
        try:
            stored = json.loads(self._path.read_text())
        except (OSError, ValueError):
            return
        if isinstance(stored, dict):
            for key in DEFAULTS:
                if key in stored and isinstance(stored[key], type(DEFAULTS[key])):
                    self._values[key] = stored[key]

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._values, indent=2))

    def get(self, key: str):
        with self._lock:
            return self._values[key]

    def all(self) -> dict:
        with self._lock:
            return dict(self._values)

    def update(self, changes: dict) -> dict:
        """Apply the known keys of `changes` (unknown keys and wrong types
        are ignored, not errors) and persist. Returns the full document."""
        with self._lock:
            for key, value in changes.items():
                if key in DEFAULTS and isinstance(value, type(DEFAULTS[key])):
                    self._values[key] = value
            self._save()
            return dict(self._values)
