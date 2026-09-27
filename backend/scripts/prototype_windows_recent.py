"""Next-round improvement 1 regression: personalization from day one.

Windows writes a shortcut to %APPDATA%\\Microsoft\\Windows\\Recent for every
file the user opens. This builds a fake Recent folder with shortcuts made
by Windows itself (WScript.Shell, the same shell-link writer Explorer uses),
sets their first-open / last-open times, and checks:

1. the .lnk reader recovers every target path;
2. only indexed files become events, one per open (first + last), tagged
   source=windows_recent; folders and unindexed files are skipped;
3. running the import again adds nothing (no duplicates);
4. a fresh install with enough recent history leaves cold start at once,
   and the profile's top file is the one opened most recently and often;
5. switching the import off removes exactly the imported events.

Windows only (skips elsewhere). Also reports, read-only and without names,
how many of this PC's Recent shortcuts point at local files.

Run with:  backend\\venv\\Scripts\\python.exe backend\\scripts\\prototype_windows_recent.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.context import COLD_START_EVENTS, ProfileBuilder, UsageStore  # noqa: E402
from app.context import windows_recent  # noqa: E402
from app.embeddings.model import EmbeddingModel, default_model_dir  # noqa: E402
from app.files.identity import build_file_record  # noqa: E402
from app.indexing import Indexer  # noqa: E402
from app.storage import FileRecordStore, KeywordStore, LanceDBVectorStore  # noqa: E402

DAY = 86400


def make_shortcuts(pairs: list[tuple[Path, Path]]) -> None:
    """(shortcut path, target) — written by Windows' own shell-link code."""
    lines = ["$s = New-Object -ComObject WScript.Shell"]
    for lnk, target in pairs:
        lines.append(f"$l = $s.CreateShortcut('{lnk}'); $l.TargetPath = '{target}'; $l.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", "; ".join(lines)], check=True, capture_output=True)


def set_times(path: Path, first_open: float, last_open: float) -> None:
    """Creation time = first open, last write = last open (as Windows does)."""
    stamp = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))  # noqa: E731
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                    f"(Get-Item -LiteralPath '{path}').CreationTime = [datetime]'{stamp(first_open)}'"], check=True, capture_output=True)
    os.utime(path, (last_open, last_open))


def main() -> int:
    if sys.platform != "win32":
        print("Windows only: skipped.")
        return 0
    work = Path(tempfile.mkdtemp(prefix="intellifile_recent_"))
    try:
        docs, recent = work / "docs", work / "Recent"
        docs.mkdir()
        recent.mkdir()
        now = time.time()
        # 14 indexed files: "thesis draft" opened most (first 10 days ago, last 1 hour ago)
        names = ["thesis draft.md", "lab results.csv", "lecture 3 notes.txt", "lecture 4 notes.txt", "budget.csv", "reading notes.md",
                 "slides week 5.md", "essay outline.txt", "timetable.txt", "group project.md", "references.txt",
                 "exam revision.md", "meeting minutes.txt", "research questions.md"]
        for name in names:
            (docs / name).write_text(f"{Path(name).stem}: notes for the course, week plans and deadlines about {Path(name).stem}.")
        (docs / "not indexed.txt").write_text("never indexed")
        store = FileRecordStore(work / "files.db")
        vectors, keywords = LanceDBVectorStore(str(work / "vectors")), KeywordStore(work / "keyword.db")
        indexer = Indexer(EmbeddingModel(default_model_dir()), vectors, keywords, store)
        for name in names:
            record = build_file_record(docs / name)
            store.upsert(record)
            indexer.index_file(docs / name, record.file_id, record.hash)

        pairs = [(recent / f"{n}.lnk", docs / n) for n in names + ["not indexed.txt"]] + [(recent / "docs.lnk", docs)]
        make_shortcuts(pairs)
        for i, (lnk, target) in enumerate(pairs):
            if target.name == "thesis draft.md":  # the file in use: opened yesterday and an hour ago
                first, last = now - DAY, now - 3600
            else:  # everything else: first opened 3-10 days ago, some opened again a day or two later
                first = now - (3 + i % 8) * DAY
                last = first + (i % 3) * DAY  # i % 3 == 0 → opened once
            set_times(lnk, first, last)

        # 1. the reader
        targets = {os.path.normcase(windows_recent.lnk_target(lnk.read_bytes()) or "") for lnk, _ in pairs}
        want = {os.path.normcase(str(t)) for _, t in pairs}
        assert targets == want, (targets ^ want)
        print(f"1. .lnk reader: all {len(pairs)} shortcuts written by Windows resolve to their targets (files and a folder): OK")

        # 2. import
        usage = UsageStore(work / "usage.db")
        result = windows_recent.import_recent(usage, store, folder=recent)
        opens = windows_recent.read_recent(recent)
        indexed_opens = [t for t, _ in opens if Path(t).name in names]
        events = usage.all_events()
        assert result["indexed_files"] == len(names) and result["events_added"] == len(indexed_opens) == len(events), (result, len(indexed_opens), len(events))
        assert all(e["kind"] == "file_opened" and e["meta"] == {"source": "windows_recent"} and e["file_id"] for e in events)
        assert not any("not indexed" in (e["path"] or "") for e in events)
        twice = sum(1 for n in names if sum(1 for t in indexed_opens if Path(t).name == n) == 2)
        print(f"2. Import: {len(events)} 'opened' events from {len(names)} indexed files ({twice} opened twice: first + last open); folder and unindexed file skipped; all tagged windows_recent: OK")

        # 3. idempotent
        again = windows_recent.import_recent(usage, store, folder=recent)
        assert again["events_added"] == 0 and usage.count() == len(events), again
        print("3. Running the import again adds nothing (no duplicate history): OK")

        # 4. day one
        builder = ProfileBuilder(usage, store, vectors, keywords)
        profile = builder.get(force=True)
        assert len(events) >= COLD_START_EVENTS and not profile.cold_start, (len(events), COLD_START_EVENTS)
        top = profile.top_files[0]["filename"]
        assert top == "thesis draft.md", profile.top_files[:3]
        print(f"4. Day one: {len(events)} imported events ≥ {COLD_START_EVENTS} → the profile leaves cold start immediately; top file = {top!r} (opened most, most recently): OK")

        # 5. switch off
        (docs / "in app.txt").write_text("x")
        rec = build_file_record(docs / "in app.txt")
        store.upsert(rec)
        usage.record("file_opened", file_id=rec.file_id, path=rec.path)
        removed = usage.clear_source(windows_recent.SOURCE)
        assert removed == len(events) and usage.count() == 1, (removed, usage.count())
        print("5. Switching the import off removes exactly the imported events; the app's own activity stays: OK")
        usage.close()

        # This PC (read-only, counts only)
        real = windows_recent.recent_dir()
        if real is not None:
            shortcuts = list(real.glob("*.lnk"))
            local = [t for t in (windows_recent.lnk_target(p.read_bytes()[:65536]) for p in shortcuts) if t]
            files = [t for t in local if os.path.isfile(t)]
            print(f"   This PC: {len(shortcuts)} Recent shortcuts, {len(local)} with a local path, {len(files)} to files that still exist "
                  f"(the rest are web/cloud URIs, control-panel items and drive roots, which have no local file).")

        print("\nImprovement 1 OK: Windows' Recent items seed the activity memory on day one — consented, local, deduplicated and removable.")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
