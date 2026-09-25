"""Interactive Phase 2 demo: point this at a real folder, then create/edit/
delete/rename files yourself (in Finder or a terminal) and watch this
print what it detects, live. Ctrl+C to stop.

Usage:
    backend/venv/bin/python backend/scripts/watch_demo.py /path/to/a/test/folder

Uses a 5-second debounce (instead of the production default of 30s) so you
don't have to wait as long while testing.
"""

import sys
import time
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.files.jobs import Job, JobQueue  # noqa: E402
from app.files.service import FileWatchService  # noqa: E402
from app.storage.sqlite_store import FileRecordStore  # noqa: E402

DEMO_DEBOUNCE_SECONDS = 5.0


def print_job(job: Job) -> None:
    print(f"  -> job: {job.kind:6s} {job.path}")


def main():
    if len(sys.argv) != 2:
        print("Usage: watch_demo.py /path/to/a/test/folder")
        sys.exit(1)

    folder = Path(sys.argv[1]).expanduser().resolve()
    if not folder.is_dir():
        print(f"Not a folder: {folder}")
        sys.exit(1)

    db_path = folder / ".intellifile_demo.db"
    record_store = FileRecordStore(db_path)
    job_queue = JobQueue(handler=print_job, num_workers=2)
    job_queue.start()

    service = FileWatchService(record_store, job_queue, debounce_seconds=DEMO_DEBOUNCE_SECONDS)

    print(f"Scanning {folder} ...")
    service.initial_scan([str(folder)])
    job_queue.join()

    active = record_store.list_active()
    print(f"Found {len(active)} supported file(s) already there (.pdf, .docx, .txt, .md):")
    for r in active:
        print(f"  - {r.path}")

    print(f"\nNow watching {folder} for changes (create/edit/delete/rename a .txt/.md/.pdf/.docx file to test).")
    print(f"Debounce is {DEMO_DEBOUNCE_SECONDS:.0f}s, so edits take a few seconds to show up. Ctrl+C to stop.\n")

    service.start_watching([str(folder)])
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        service.stop()
        job_queue.stop()
        record_store.close()


if __name__ == "__main__":
    main()
