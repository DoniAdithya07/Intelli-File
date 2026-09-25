"""Resource usage modes (Phase 10), per the PRD's Resource Usage Modes.

Only knobs that measurably change behaviour are here; the job queue keeps
one worker in every mode because a file's index step holds the write lock
for its whole extract → embed → write sequence (Phase 18 audit), so a
second worker would mostly wait — the honest lever for CPU is the pause
between files.

    balanced       index promptly, no throttle                       (default)
    performance    as balanced, and never pause for battery/low-power
    battery_saver  200 ms pause between files, defer live re-index
                   jobs while on battery (they run when power returns)
"""

from dataclasses import dataclass

MODES = ("balanced", "performance", "battery_saver")


@dataclass(frozen=True)
class ResourceMode:
    name: str
    inter_file_sleep: float      # seconds between files in a scan / between watcher jobs
    defer_live_jobs_on_battery: bool
    ignore_power: bool           # performance: keep going regardless of battery / low-power


TABLE = {
    "balanced": ResourceMode("balanced", 0.0, False, False),
    "performance": ResourceMode("performance", 0.0, False, True),
    "battery_saver": ResourceMode("battery_saver", 0.2, True, False),
}


def get(name: str) -> ResourceMode:
    return TABLE.get(name, TABLE["balanced"])
