"""Power and load awareness (Phase 10, the PRD's Resource-aware principle).

A daemon thread polls every POLL_SECONDS:
- on battery vs. plugged in, and the charge (psutil.sensors_battery);
- the OS's own low-power mode: macOS Low Power Mode (`pmset -g`),
  Windows Battery Saver (GetSystemPowerStatus.SystemStatusFlag);
- CPU: the machine's overall load and this backend's own share.

Consumers (LiveIndexing) ask `state()` and register `on_change` callbacks.
`PowerMonitor(source=...)` takes a fake source so the pause/resume logic
can be tested without unplugging anything (prototype_power.py); the real
unplug test is a human's job — bug class #2, real device I/O.
"""

import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)

POLL_SECONDS = 5.0


@dataclass(frozen=True)
class PowerState:
    has_battery: bool
    on_battery: bool
    percent: float | None
    low_power_mode: bool      # the OS's Battery Saver / Low Power Mode
    cpu_percent: float        # whole machine
    app_cpu_percent: float    # this backend process
    checked_at: float

    def as_dict(self) -> dict:
        return asdict(self)


def _os_low_power_mode() -> bool:
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["pmset", "-g"], capture_output=True, text=True, timeout=3).stdout
            for line in out.splitlines():
                if "lowpowermode" in line:
                    return line.split()[-1] == "1"
        elif sys.platform == "win32":
            import ctypes

            class SYSTEM_POWER_STATUS(ctypes.Structure):
                _fields_ = [
                    ("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte), ("BatteryLifePercent", ctypes.c_byte),
                    ("SystemStatusFlag", ctypes.c_byte), ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong),
                ]

            status = SYSTEM_POWER_STATUS()
            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
                return status.SystemStatusFlag == 1  # battery saver on
    except Exception:
        logger.debug("low power mode probe failed", exc_info=True)
    return False


def real_source() -> Callable[[], PowerState]:
    """The default source: psutil + the OS probes above."""
    import psutil

    process = psutil.Process(os.getpid())
    psutil.cpu_percent(interval=None)  # prime: the first call always returns 0
    process.cpu_percent(interval=None)

    def read() -> PowerState:
        battery = psutil.sensors_battery()
        return PowerState(
            has_battery=battery is not None,
            on_battery=bool(battery is not None and not battery.power_plugged),
            percent=float(battery.percent) if battery is not None else None,
            low_power_mode=_os_low_power_mode(),
            cpu_percent=float(psutil.cpu_percent(interval=None)),
            app_cpu_percent=float(process.cpu_percent(interval=None)) / max(1, psutil.cpu_count() or 1),
            checked_at=time.time(),
        )

    return read


class PowerMonitor:
    def __init__(self, source: Callable[[], PowerState] | None = None, poll_seconds: float = POLL_SECONDS):
        self._source = source or real_source()
        self._poll = poll_seconds
        self._state = self._source()
        self._callbacks: list[Callable[[PowerState, PowerState], None]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="power-monitor")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def state(self) -> PowerState:
        with self._lock:
            return self._state

    def on_change(self, callback: Callable[[PowerState, PowerState], None]) -> None:
        self._callbacks.append(callback)

    def refresh(self) -> PowerState:
        """Read the source now (tests drive the monitor with this)."""
        try:
            new = self._source()
        except Exception:
            logger.exception("power source failed")
            return self.state()
        with self._lock:
            old, self._state = self._state, new
        if (old.on_battery, old.low_power_mode) != (new.on_battery, new.low_power_mode):
            for callback in self._callbacks:
                try:
                    callback(old, new)
                except Exception:
                    logger.exception("power change callback failed")
        return new

    def _run(self) -> None:
        while not self._stop.wait(self._poll):
            self.refresh()
