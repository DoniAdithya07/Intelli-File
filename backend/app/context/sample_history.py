""""Load sample history" (2026-10-04): a made-up four weeks of use of the
sample folder, so the For You page — personalization, the assignment's
Objective 2 — has something to show on a fresh install instead of "still
learning". The same habits scripts/evaluate_personalization.py measures:
habits spread over the day and the week (frequency, files used together,
time patterns; see HABITS), and an occasional search.

Every event is labelled — meta {"source": "sample_history", "sample": true}
— so the Activity page can tell it apart and remove_sample_history() takes
out exactly these events and nothing the user did. Only the SAMPLE folder's
files are used: the user's own files never get invented history.
"""

import os
from datetime import datetime, timedelta
from pathlib import Path

from ..files.discovery import VISUAL_EXTENSIONS
from ..storage import FileRecordStore
from .usage_store import UsageStore

SOURCE = "sample_history"
DAYS = 28
EVERY_DAY = range(7)
WEEKDAYS = range(5)
WEEKEND = (5, 6)
# (files opened together, hour, minute, weekdays). The sample folder is the
# Phase 20 corpus (scripts/eval_corpus.py); a habit whose files are missing
# (another sample folder) takes the next unused document instead.
# Until 2026-10-05 every habit sat at 14:xx or Monday 09:xx, so For You's
# "For now" (usual_now: a file opened mostly in the current 4-hour slot of
# profile.time_slot) was empty for most of the day right after loading. Now
# each of the six slots has its own habit, morning to night, weekdays and
# weekend, and each minute sits mid-slot so a daylight-saving hour cannot
# move it into the next one. A file in two habits stays at >= 40 % in each
# slot (recommendations' share rule) while the counts are balanced.
HABITS = (
    (("gym plan.txt",), 6, 0, EVERY_DAY),                                 # early morning workout
    (("incident report.md", "savings plan.txt"), 10, 0, (0,)),            # the Monday-morning review
    (("scaling_notes.md", "deploy checklist.md"), 14, 10, WEEKDAYS),      # afternoon work, opened together
    (("sourdough.md",), 18, 0, EVERY_DAY),                                # evening baking
    (("lisbon trip.md", "insurance policy.txt"), 22, 0, WEEKEND),         # weekend trip planning...
    (("lisbon trip.md", "insurance policy.txt"), 2, 0, WEEKEND),          # ...that runs past midnight
)
SEARCH_EVERY_DAYS = 3


def _sample_records(file_record_store: FileRecordStore, folder: Path) -> list:
    prefix = os.path.normcase(str(folder)) + os.sep
    records = [r for r in file_record_store.list_active() if r.indexed and os.path.normcase(r.path).startswith(prefix)]
    documents = [r for r in records if Path(r.path).suffix.lower() not in VISUAL_EXTENSIONS]
    return sorted(documents or records, key=lambda r: r.path)


def _assign(records: list) -> list[list]:
    """The files of each habit: by name, else the next document no habit
    uses yet (cycling when there are fewer documents than habits)."""
    by_name = {Path(r.path).name: r for r in records}
    picked = [[by_name[n] for n in names if n in by_name] for names, *_ in HABITS]
    used = {r.file_id for files in picked for r in files}
    spare = [r for r in records if r.file_id not in used] or records
    stand_ins: dict[tuple, list] = {}
    for i, (names, *_) in enumerate(HABITS):
        if not picked[i]:
            # The same names get the same stand-in (the midnight half of a habit).
            if names not in stand_ins:
                stand_ins[names] = [spare[len(stand_ins) % len(spare)]]
            picked[i] = stand_ins[names]
    return picked


def load_sample_history(usage_store: UsageStore, file_record_store: FileRecordStore, folder: Path, now: float | None = None) -> int:
    """Replace any earlier sample history with a fresh one ending yesterday,
    in one transaction (a failure leaves the old one whole). Returns how
    many events were written; 0 when nothing in `folder` is indexed yet
    (the caller asks the user to index it first)."""
    records = _sample_records(file_record_store, folder)
    if not records:
        return 0
    habits = _assign(records)
    today = datetime.fromtimestamp(now) if now is not None else datetime.now()
    label = {"sample": True}

    def at(day: datetime, hour: int, minute: int) -> float:
        return day.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp()

    opens, searches = [], []
    for days_ago in range(DAYS, 0, -1):
        day = today - timedelta(days=days_ago)
        for files, (_, hour, minute, weekdays) in zip(habits, HABITS):
            if day.weekday() in weekdays:
                for i, record in enumerate(files):
                    opens.append({"ts": at(day, hour, minute + 2 * i), "file_id": record.file_id, "path": record.path, "meta": label})
        if days_ago % SEARCH_EVERY_DAYS == 0:
            targets = habits[days_ago // SEARCH_EVERY_DAYS % len(habits)]
            target = targets[0]
            query = Path(target.path).stem.replace("_", " ")
            searches.append({"ts": at(day, 14, 5), "query": query, "meta": {**label, "mode": "auto", "results": [target.file_id], "count": 1}})
    return usage_store.replace_source(SOURCE, [("file_opened", opens), ("query", searches)])


def remove_sample_history(usage_store: UsageStore) -> int:
    return usage_store.clear_source(SOURCE)


def has_sample_history(usage_store: UsageStore) -> bool:
    return usage_store.count_source(SOURCE) > 0
