"""Phase 10 regression: power-aware indexing, with a FAKE power source
(no unplugging needed — that final check is a human's, bug class #2).

- unplugging mid-scan pauses the scan between files (no half-written
  file) and plugging back in resumes from the next file — the same job,
  no rescan;
- the watcher's job worker honours the same gate;
- Performance mode ignores battery; Battery Saver throttles between
  files and defers live jobs on battery; the pause toggles take effect
  immediately; low-power mode pauses on its own;
- /status-style snapshot reports the reason.

Run with:  backend/venv/bin/python backend/scripts/prototype_power.py
"""

import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.embeddings.model import EmbeddingModel, default_model_dir  # noqa: E402
from app.indexing import Indexer  # noqa: E402
from app.power import PowerMonitor, PowerState  # noqa: E402
from app.storage import FileRecordStore, KeywordStore, LanceDBVectorStore  # noqa: E402
from app.watch_setup import LiveIndexing  # noqa: E402

MODEL_DIR = default_model_dir(Path(__file__).resolve().parents[1] / "models")


class FakePower:
    def __init__(self):
        self.on_battery = False
        self.low_power = False

    def __call__(self) -> PowerState:
        return PowerState(has_battery=True, on_battery=self.on_battery, percent=80.0, low_power_mode=self.low_power, cpu_percent=10.0, app_cpu_percent=1.0, checked_at=time.time())


def wait_until(predicate, timeout=20.0, step=0.05) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(step)
    return False


def main() -> None:
    workdir = Path(tempfile.mkdtemp())
    try:
        folder = workdir / "docs"
        folder.mkdir()
        for i in range(24):
            (folder / f"note_{i:02d}.txt").write_text(f"Note number {i}: " + " ".join(f"word{i}_{k}" for k in range(60)))
        (workdir / "config").mkdir()

        model = EmbeddingModel(MODEL_DIR)
        indexer = Indexer(model, LanceDBVectorStore(str(workdir / "vectors")), KeywordStore(workdir / "keyword.db"), FileRecordStore(workdir / "files.db"))
        live = LiveIndexing(indexer, None, workdir / "config")
        settings = {"pause_on_battery": True, "pause_on_low_power": True, "resource_mode": "balanced"}
        live.settings_reader = lambda: settings
        fake = FakePower()
        monitor = PowerMonitor(source=fake, poll_seconds=999)  # driven by refresh() here
        live.attach_power(monitor)
        live.start()

        # --- 1. Unplug mid-scan: the scan stops between files, resumes from the same job ---
        live.watch(str(folder))
        live.enqueue(str(folder))
        assert wait_until(lambda: live.job.state == "running" and live.job.done >= 3), live.job
        fake.on_battery = True
        monitor.refresh()
        assert live.paused_reason == "on battery power" and not live.gate.is_set()
        time.sleep(0.6)
        done_at_pause = live.job.done
        time.sleep(1.0)
        assert live.job.done <= done_at_pause + 1, f"scan kept going while paused: {done_at_pause} -> {live.job.done}"
        assert live.job.state == "running", "the job must stay the same job, not fail or finish"
        snapshot = live.job_snapshot()
        assert snapshot["paused_reason"] == "on battery power" and live.power_snapshot()["paused"]
        fake.on_battery = False
        monitor.refresh()
        assert live.paused_reason is None and live.gate.is_set()
        assert wait_until(lambda: live.job.state == "done", timeout=60), live.job
        assert live.job.total == 24 and live.job.done == 24 and live.job.failed == 0
        assert len(indexer.file_record_store.list_active()) == 24
        print(f"1. Unplugged at {done_at_pause}/24: scan paused between files, resumed from the same job, finished 24/24 with no rescan: OK")

        # --- 2. The watcher's worker honours the gate too ---
        fake.on_battery = True
        monitor.refresh()
        (folder / "late.txt").write_text("a file created while on battery power, with plenty of words to index here")
        from app.files.identity import build_file_record
        from app.files.jobs import Job
        record = build_file_record(folder / "late.txt")
        indexer.file_record_store.upsert(record)
        live.job_queue.submit(Job(kind="index", path=str(folder / "late.txt"), file_id=record.file_id))
        time.sleep(1.0)
        assert live.job_queue.pending == 1, "a live job must wait while paused"
        assert not indexer.vector_store.get_by_file_id("chunks", record.file_id), "the job must not have run while paused"
        fake.on_battery = False
        monitor.refresh()
        assert wait_until(lambda: live.job_queue.pending == 0, timeout=20)
        live.job_queue.join()
        assert indexer.vector_store.get_by_file_id("chunks", record.file_id), "the deferred job must run once power is back"
        print("2. A watcher job submitted on battery waits, then runs when power returns: OK")

        # --- 3. Performance mode ignores battery; Battery Saver defers and throttles; toggles apply at once ---
        def set_power(on_battery: bool, low_power: bool = False) -> None:
            fake.on_battery, fake.low_power = on_battery, low_power
            monitor.refresh()
            live.apply_power()

        settings["resource_mode"] = "performance"
        set_power(on_battery=True)
        assert live.paused_reason is None and live.gate.is_set(), "performance mode must not pause"
        settings["resource_mode"] = "battery_saver"
        settings["pause_on_battery"] = False
        live.apply_power()
        assert live.paused_reason == "battery saver mode, on battery", live.paused_reason
        assert live.job_queue.inter_job_sleep == 0.2 and live._throttle() == 0.2
        set_power(on_battery=False)
        assert live.paused_reason is None
        settings["resource_mode"] = "balanced"
        settings["pause_on_battery"] = True
        set_power(on_battery=True)
        assert live.paused_reason == "on battery power"
        settings["pause_on_battery"] = False
        live.apply_power()
        assert live.paused_reason is None, "turning the toggle off must resume immediately"
        print("3. Performance ignores battery; Battery Saver throttles (0.2 s) and defers on battery; toggles apply immediately: OK")

        # --- 4. Low-power mode pauses on its own (AC or not) ---
        set_power(on_battery=False, low_power=True)
        assert live.paused_reason == "low power mode is on", live.paused_reason
        settings["pause_on_low_power"] = False
        live.apply_power()
        assert live.paused_reason is None
        set_power(on_battery=False, low_power=False)
        print("4. OS low-power mode pauses indexing even on AC; its toggle releases it: OK")

        # --- 5. Battery Saver throttle really slows a scan ---
        settings.update({"resource_mode": "battery_saver", "pause_on_battery": True, "pause_on_low_power": True})
        set_power(on_battery=False)
        for i in range(6):
            (folder / f"extra_{i}.txt").write_text(f"extra note {i} " + " ".join(f"tok{i}{k}" for k in range(30)))
        t0 = time.time()
        live.enqueue(str(folder))
        assert wait_until(lambda: live.job.state == "done" and live.job.total == 31, timeout=60)
        elapsed = time.time() - t0
        assert elapsed >= 6 * 0.2, f"6 new files should cost at least 1.2 s of throttle, took {elapsed:.1f}s"
        print(f"5. Battery Saver throttle: 6 new files took {elapsed:.1f}s (≥ 1.2 s of deliberate pauses): OK")

        live.stop()
        print("\nPhase 10 power management OK: pause/resume between files, gated watcher jobs, resource modes, toggles and low-power mode all work as expected.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
