"""Concurrency regression for FileRecordStore.upsert() (2026-10-29 review):
upsert() used to vacate the target path and INSERT the new row under two
separate lock acquisitions. Releasing the lock in between let two threads
(e.g. the live file watcher reconciling a brand-new file, and a folder scan
walking the same freshly-watched folder) both see "nothing to vacate" for
the same path and both INSERT, the second hitting SQLite's UNIQUE(path)
constraint with an uncaught IntegrityError -- which killed the whole scan
job on the scan side and silently dropped the record on the watcher side.
Fixed by holding one lock across vacate-then-insert. Hammers the race with
several threads writing the same path concurrently; exits non-zero if any
upsert() raises or the row count ends up wrong."""

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.files.identity import FileRecord
from app.storage.sqlite_store import FileRecordStore

THREADS = 6
UPSERTS_PER_THREAD = 200


def main() -> None:
    work = Path(tempfile.mkdtemp())
    store = FileRecordStore(work / "test.db")
    path = r"C:\watched\new_file.txt"
    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            for i in range(UPSERTS_PER_THREAD):
                store.upsert(FileRecord(file_id=f"id-{n}-{i}", path=path, size=10, modified_time=time.time(), hash="a" * 64))
        except Exception as e:  # noqa: BLE001 -- the exact failure under test
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"upsert() raised under concurrent writers to the same path: {errors[0]!r}"
    # Exactly one row for `path` must survive (whichever write landed last);
    # every other contender was correctly vacated (tombstoned), not left as
    # a second live row or a half-written one.
    rows = [r for r in store.get_by_paths([path]).values()]
    assert len(rows) == 1 and rows[0].path == path, rows

    print(f"check_upsert_race: OK ({THREADS * UPSERTS_PER_THREAD} concurrent upserts, no IntegrityError)")


if __name__ == "__main__":
    main()
