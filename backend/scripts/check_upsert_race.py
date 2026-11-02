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
import sqlite3
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

    check_vacate_insert_atomicity()


def check_vacate_insert_atomicity() -> None:
    """DEFECT-006 (2026-11-02, found in QA): upsert()'s vacate-then-insert
    used to commit as two separate SQLite transactions, not one -- a crash
    between them could leave a path with zero active owners. Fixed by
    having _vacate_path_locked() never commit on its own; upsert() commits
    once after both statements. Verifies an external connection never
    observes the in-between state: either the old owner still has the
    path, or the new owner does -- never neither."""
    work = Path(tempfile.mkdtemp())
    db_path = work / "atomic.db"
    store = FileRecordStore(db_path)
    path = r"C:\watched\atomic.txt"
    store.upsert(FileRecord(file_id="A", path=path, size=1, modified_time=1.0, hash="a" * 64))

    external = sqlite3.connect(str(db_path), timeout=1.0)
    try:
        with store._lock:
            vacated = store._vacate_path_locked(path, "B")
            assert vacated is True
            row = external.execute("SELECT file_id FROM files WHERE path = ?", (path,)).fetchone()
            assert row is not None and row[0] == "A", f"an external reader observed an uncommitted half-done vacate: {row}"
        store._conn.rollback()  # undo the incomplete call above, as a crash would leave it
        row = external.execute("SELECT file_id FROM files WHERE path = ?", (path,)).fetchone()
        assert row is not None and row[0] == "A", f"rollback should restore the pre-vacate state exactly: {row}"

        store.upsert(FileRecord(file_id="B", path=path, size=2, modified_time=2.0, hash="b" * 64))
        row = external.execute("SELECT file_id FROM files WHERE path = ?", (path,)).fetchone()
        assert row is not None and row[0] == "B", f"a completed upsert() must leave the new owner visible: {row}"
    finally:
        external.close()

    print("check_vacate_insert_atomicity: OK (vacate+insert is one transaction; no half-done state observable or left behind by a rollback)")


if __name__ == "__main__":
    main()
