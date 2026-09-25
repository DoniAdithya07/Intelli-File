"""Recommendations without a query — the "Recommendation" half of the
assignment's title. Three lists, each with the reason it was picked:

- **likely_next** — files that share sessions with what is open right now
  (recurring work pattern: "when you open A you usually open B");
- **usual_now** — files whose activity clusters in the current weekday /
  time slot (temporal pattern);
- **recent** — the highest-scoring recently touched files (frequency ×
  recency).

Cold start (fewer than COLD_START_EVENTS events): the lists that need
history are empty and `recent` falls back to the most recently modified
files in the index, flagged as such.
"""

import time
from pathlib import Path

from ..storage import FileRecordStore
from .profile import Profile, slot_label, time_slot
from .usage_store import UsageStore

LIST_SIZE = 5
MIN_SLOT_EVENTS = 2


def _entry(profile: Profile, file_id: str, reason: str, path: str | None = None) -> dict:
    path = path or profile.file_paths.get(file_id, "")
    return {"file_id": file_id, "path": path, "filename": Path(path).name, "reason": reason}


def recommend(profile: Profile, usage_store: UsageStore, file_record_store: FileRecordStore, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    weekday, slot = time_slot(now)
    active = {r.file_id: r for r in file_record_store.list_active()}
    exists = lambda fid: fid in active and Path(active[fid].path).exists()  # noqa: E731

    result = {"cold_start": profile.cold_start, "now": slot_label(weekday, slot), "likely_next": [], "usual_now": [], "recent": []}

    if profile.cold_start:
        newest = sorted(active.values(), key=lambda r: -r.modified_time)[:LIST_SIZE]
        result["recent"] = [_entry(profile, r.file_id, "Recently modified — the app is still learning what you use", r.path) for r in newest]
        return result

    # likely next: co-occurrence with the current session's files
    session = usage_store.current_session(now=now)
    session_ids = {fid for fid, path in profile.file_paths.items() if path in session["files"]}
    votes: dict[str, tuple[int, str]] = {}
    for fid in session_ids:
        for other, count in profile.cooccurrence.get(fid, {}).items():
            if other in session_ids or not exists(other):
                continue
            if other not in votes or count > votes[other][0]:
                votes[other] = (count, Path(profile.file_paths.get(fid, "")).name)
    result["likely_next"] = [
        _entry(profile, fid, f"Usually open together with {with_name} ({count} session{'s' if count != 1 else ''})")
        for fid, (count, with_name) in sorted(votes.items(), key=lambda kv: (-kv[1][0], -profile.frequency(kv[0])))[:LIST_SIZE]
    ]

    # usual now: files whose activity concentrates in this slot
    scored = []
    for fid, counts in profile.file_slot_counts.items():
        if not exists(fid):
            continue
        same_slot = counts[:, slot].sum()
        if same_slot < MIN_SLOT_EVENTS:
            continue
        share = same_slot / counts.sum()
        if share >= 0.4:
            scored.append((share * profile.frequency(fid), fid, int(same_slot)))
    scored.sort(reverse=True)
    result["usual_now"] = [
        _entry(profile, fid, f"You usually open this around this time ({n} of your visits)")
        for _, fid, n in scored[:LIST_SIZE]
    ]

    # recent: frequency × recency, skipping what the other lists already hold
    taken = {e["file_id"] for e in result["likely_next"] + result["usual_now"]}
    ranked = sorted(
        ((profile.frequency(fid) * (0.5 + 0.5 * profile.recency(fid, now)), fid) for fid in profile.file_scores if fid not in taken and exists(fid)),
        reverse=True,
    )
    result["recent"] = [_entry(profile, fid, "One of the files you use most") for _, fid in ranked[:LIST_SIZE]]
    return result
