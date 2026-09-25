"""Phase 2 integration test: prove the full discovery -> hash -> debounce ->
record store -> job queue pipeline actually works against a real folder on
disk, not just in theory. Run with:

    backend/venv/bin/python backend/scripts/prototype_file_watch.py

Uses short debounce/sleep windows for speed; production default is 30s
(see app/files/debounce.py).
"""

import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.files.jobs import Job, JobQueue  # noqa: E402
from app.files.service import FileWatchService  # noqa: E402
from app.storage.sqlite_store import FileRecordStore  # noqa: E402

TEST_DEBOUNCE_SECONDS = 0.5
SETTLE_SECONDS = TEST_DEBOUNCE_SECONDS + 1.5  # debounce window + macOS FSEvents latency margin


class JobRecorder:
    """Stand-in for LiveIndexing._handle_job: records the jobs and, like the
    real handler, marks an index job's record as indexed once "stored"
    (Phase 11 — a record stays `indexed = 0` until then)."""

    def __init__(self, record_store=None):
        self._lock = threading.Lock()
        self.jobs: list[Job] = []
        self._record_store = record_store

    def __call__(self, job: Job) -> None:
        with self._lock:
            self.jobs.append(job)
        if job.kind == "index" and self._record_store is not None and job.file_id:
            self._record_store.mark_indexed(job.file_id)

    def jobs_for(self, path: str) -> list[Job]:
        with self._lock:
            return [j for j in self.jobs if j.path == path]


def main():
    workdir = Path(tempfile.mkdtemp()).resolve()
    watched = workdir / "watched"
    watched.mkdir()
    db_path = workdir / "files.db"

    record_store = FileRecordStore(db_path)
    recorder = JobRecorder(record_store)
    job_queue = JobQueue(handler=recorder, num_workers=2)
    job_queue.start()

    service = FileWatchService(record_store, job_queue, debounce_seconds=TEST_DEBOUNCE_SECONDS)

    try:
        txt_path = watched / "notes.txt"
        pdf_path = watched / "doc.pdf"
        ignored_path = watched / "app.exe"

        txt_path.write_text("hello world")
        pdf_path.write_bytes(b"%PDF-1.4 fake content")
        ignored_path.write_text("should never be tracked")

        # Regression: a folder scan once picked up package metadata .txt
        # files from a Python venv sitting inside the scanned folder and
        # returned them as real search results. node_modules/venv/etc.
        # must be pruned entirely, not just filtered after the fact.
        fake_venv_txt = watched / "venv" / "lib" / "site-packages" / "somepkg-1.0.dist-info" / "top_level.txt"
        fake_venv_txt.parent.mkdir(parents=True)
        fake_venv_txt.write_text("somepkg")
        fake_node_modules_txt = watched / "node_modules" / "some-package" / "readme.txt"
        fake_node_modules_txt.parent.mkdir(parents=True)
        fake_node_modules_txt.write_text("not a real document")

        # Regression: live re-test found the app's own bundled ML model
        # directory (backend/models/whisper-tiny.en/merges.txt — a 50k-line
        # BPE tokenizer file, not a document) getting indexed when a folder
        # containing it was scanned, and its embedding polluted real search
        # results. "models" must be pruned like venv/node_modules.
        fake_models_txt = watched / "models" / "whisper-tiny.en" / "merges.txt"
        fake_models_txt.parent.mkdir(parents=True)
        fake_models_txt.write_text("#version: 0.2\nfake bpe merges")

        # Regression: live re-test on the real project folder found
        # backend/requirements.txt and requirements-dev.txt (plain package
        # dependency lists, not documents) getting indexed, because they
        # sit directly in a normal (non-excluded) directory — a directory
        # exclusion can't catch them, only a filename-based one can.
        fake_requirements_txt = watched / "requirements.txt"
        fake_requirements_txt.write_text("fastapi\nuvicorn\n")
        # Regression (2026-09-20): IntelliFile's own bundled word list
        # (backend/data/english_words.txt) was indexed once the user pointed
        # the app at the project folder, and outscored real documents.
        fake_wordlist = watched / "data" / "english_words.txt"
        fake_wordlist.parent.mkdir()
        fake_wordlist.write_text("the 1000000\nletter 5000\nlandlord 400\n")
        fake_lockfile = watched / "package-lock.json"
        fake_lockfile.write_text('{"lockfileVersion": 3}')

        # --- 1. Initial recursive scan ---
        service.initial_scan([str(watched)])
        job_queue.join()

        active = {r.path: r for r in record_store.list_active()}
        assert str(txt_path) in active, "Initial scan missed notes.txt"
        assert str(fake_venv_txt) not in active, "venv/ contents must never be indexed as documents"
        assert str(fake_node_modules_txt) not in active, "node_modules/ contents must never be indexed as documents"
        assert str(fake_models_txt) not in active, "models/ (bundled ML model dir) contents must never be indexed as documents"
        assert str(fake_requirements_txt) not in active, "requirements.txt must never be indexed as a document"
        assert str(fake_wordlist) not in active, "english_words.txt (our own spell-check list) must never be indexed as a document"
        assert str(fake_lockfile) not in active, "package-lock.json must never be indexed as a document"
        assert str(pdf_path) in active, "Initial scan missed doc.pdf"
        assert str(ignored_path) not in active, ".exe should never be tracked (unsupported extension)"
        assert len(recorder.jobs_for(str(txt_path))) == 1, "Expected exactly one index job from initial scan"
        print("1. Initial scan: OK — supported files tracked, unsupported ignored, venv/node_modules/models pruned")

        txt_file_id = active[str(txt_path)].file_id

        # --- 2. Live watching ---
        service.start_watching([str(watched)])

        # 2a. Re-save with IDENTICAL content -> hash unchanged -> should NOT re-index
        time.sleep(0.3)
        txt_path.write_text("hello world")
        time.sleep(SETTLE_SECONDS)
        job_queue.join()
        assert len(recorder.jobs_for(str(txt_path))) == 1, "Unchanged content should not trigger a re-index job"
        print("2. Unchanged re-save: OK — no unnecessary re-index (hash comparison works)")

        # 2b. Modify with DIFFERENT content -> should re-index, same file_id
        txt_path.write_text("hello world, this content actually changed")
        time.sleep(SETTLE_SECONDS)
        job_queue.join()
        jobs = recorder.jobs_for(str(txt_path))
        assert len(jobs) == 2, f"Expected a second index job after real content change, got {len(jobs)}"
        updated = record_store.get_by_path(str(txt_path))
        assert updated.file_id == txt_file_id, "file_id must stay stable across a content update"
        print("3. Real content change: OK — re-indexed, file_id preserved")

        # 2c. Delete -> tombstoned, not physically removed
        pdf_file_id = record_store.get_by_path(str(pdf_path)).file_id
        pdf_path.unlink()
        time.sleep(SETTLE_SECONDS)
        job_queue.join()
        tombstoned = record_store.get_by_path(str(pdf_path))
        assert tombstoned is not None and tombstoned.deleted, "Deleted file should be tombstoned, not removed"
        assert tombstoned.file_id == pdf_file_id
        delete_jobs = [j for j in recorder.jobs if j.kind == "delete" and j.path == str(pdf_path)]
        assert len(delete_jobs) == 1, "Expected exactly one delete job"
        print("4. Delete: OK — tombstoned (deleted=True), delete job emitted")

        # 2d. Rename -> path updates, same file_id, NO re-embedding job
        jobs_before_rename = len(recorder.jobs)
        renamed_path = watched / "notes_renamed.txt"
        txt_path.rename(renamed_path)
        time.sleep(SETTLE_SECONDS)
        job_queue.join()
        assert record_store.get_by_path(str(txt_path)) is None, "Old path should no longer resolve"
        moved = record_store.get_by_path(str(renamed_path))
        assert moved is not None and moved.file_id == txt_file_id, "Rename must preserve file_id"
        assert len(recorder.jobs) == jobs_before_rename, "A pure rename must not trigger a re-index job"
        print("5. Rename: OK — path updated, file_id preserved, no unnecessary re-embedding")

        # 2e. The LIVE watcher must prune the same directories the scan does.
        # Found 2026-09-21: a file created under node_modules/ after the scan
        # was indexed within 30 s although the scan had skipped that tree.
        jobs_before = len(recorder.jobs)
        for junk_dir in (watched / "node_modules" / "left-pad", watched / ".git" / "hooks", watched / "venv" / "lib", watched / "resources" / "backend" / "_internal" / "pypdf"):
            junk_dir.mkdir(parents=True, exist_ok=True)
            (junk_dir / "later.txt").write_text("created while watching")
        real_later = watched / "later_real.txt"
        real_later.write_text("a real document created while watching")
        time.sleep(SETTLE_SECONDS)
        job_queue.join()
        assert record_store.get_by_path(str(real_later)) is not None, "a real new file must still be picked up"
        for junk_dir in (watched / "node_modules" / "left-pad", watched / ".git" / "hooks", watched / "venv" / "lib", watched / "resources" / "backend" / "_internal" / "pypdf"):
            assert record_store.get_by_path(str(junk_dir / "later.txt")) is None, f"watcher indexed a file inside {junk_dir.name}/"
        assert len(recorder.jobs) == jobs_before + 1, f"expected exactly one index job (the real file), got {len(recorder.jobs) - jobs_before}"
        print("6. Live watcher prunes node_modules/.git/venv/_internal like the scan; real new files still indexed: OK")

        # 2f. Rename onto a path whose old file was deleted moments ago (its
        # row is still tombstoned). This raised IntegrityError and killed the
        # watchdog dispatcher thread — every folder went quiet (2026-09-21).
        # Afterwards the watcher must still be alive: a fresh file gets indexed.
        victim = watched / "victim.txt"
        victim.write_text("to be replaced")
        replacement = watched / "replacement.txt"
        replacement.write_text("the replacement content")
        time.sleep(SETTLE_SECONDS)
        job_queue.join()
        victim_id = record_store.get_by_path(str(victim)).file_id
        victim.unlink()
        time.sleep(0.5)
        replacement.rename(victim)
        time.sleep(SETTLE_SECONDS)
        job_queue.join()
        now_at_victim = record_store.get_by_path(str(victim))
        assert now_at_victim is not None and not now_at_victim.deleted and now_at_victim.file_id != victim_id, "renamed file must own the path now"
        assert service._observer.is_alive(), "watchdog observer thread died"
        still_alive = watched / "after_the_storm.txt"
        still_alive.write_text("the watcher must still see this")
        time.sleep(SETTLE_SECONDS)
        job_queue.join()
        assert record_store.get_by_path(str(still_alive)) is not None, "watcher stopped delivering events after the rename"
        print("7. Rename onto a just-deleted path: no crash, path re-owned, watcher still alive afterwards: OK")

        # 2g. Folder-level events and moves out of the tree (2026-09-21 macOS
        # pass): deleting a whole folder gave no per-file events, and a file
        # moved OUT of the watched tree was dropped because the delete check
        # stat()-ed a path that no longer existed. Both must tombstone.
        sub = watched / "project"
        sub.mkdir()
        (sub / "a.txt").write_text("alpha " * 20)
        (sub / "b.md").write_text("beta " * 20)
        (watched / "roaming.txt").write_text("roaming " * 20)
        time.sleep(SETTLE_SECONDS)
        job_queue.join()
        assert record_store.get_by_path(str(sub / "a.txt")) and record_store.get_by_path(str(watched / "roaming.txt"))
        shutil.rmtree(sub)
        (watched / "roaming.txt").rename(workdir / "roaming.txt")  # out of the watched tree
        time.sleep(SETTLE_SECONDS)
        job_queue.join()
        for gone in (sub / "a.txt", sub / "b.md", watched / "roaming.txt"):
            rec = record_store.get_by_path(str(gone))
            assert rec is None or rec.deleted, f"{gone.name} must be tombstoned after its folder was deleted / it was moved out"
        (workdir / "roaming.txt").rename(watched / "roaming.txt")  # and back in
        time.sleep(SETTLE_SECONDS)
        job_queue.join()
        back = record_store.get_by_path(str(watched / "roaming.txt"))
        assert back is not None and not back.deleted, "a file moved back into the tree must be active again"
        print("8. Deleting a whole folder and moving a file out of the tree both tombstone; moving it back re-activates: OK")

        print("\nPhase 2 file watch pipeline OK: discovery, hashing, debounce, tombstoning, and rename handling all work as expected.")
    finally:
        service.stop()
        job_queue.stop()
        record_store.close()
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
