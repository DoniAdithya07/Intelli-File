"""Phase 16 regression: the activity memory that Objective 2 is built on.
Events round-trip with their denormalized path/type, sessions split on the
30-minute gap, the current working context is derived from the latest
session only, clearing empties everything, and the settings document
persists the "remember my activity" switch.

Run with:  backend/venv/bin/python backend/scripts/prototype_usage.py
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.context import SESSION_GAP_SECONDS, Settings, UsageStore  # noqa: E402


def main() -> None:
    workdir = Path(tempfile.mkdtemp())
    try:
        store = UsageStore(workdir / "usage.db")
        t0 = 1_800_000_000.0  # a fixed "now" so sessions are deterministic

        # --- 1. Round trip: the event keeps path, derived file type, query, meta ---
        e = store.record("file_opened", file_id="f1", path="/docs/gym plan.txt", ts=t0)
        assert e["file_type"] == "txt" and e["path"] == "/docs/gym plan.txt" and e["kind"] == "file_opened"
        q = store.record("query", query="gym", meta={"mode": "smart", "results": ["f1"]}, ts=t0 + 1)
        assert q["meta"]["results"] == ["f1"] and q["file_type"] is None
        assert store.count() == 2
        try:
            store.record("something_else", ts=t0 + 2)
            raise AssertionError("unknown kinds must be rejected")
        except ValueError:
            pass
        print("1. Events round-trip with path, derived file type, query and meta; unknown kinds rejected: OK")

        # --- 2. Sessions: within the gap = same session, beyond it = new ---
        store.record("file_revealed", file_id="f2", path="/docs/landlord letter.pdf", ts=t0 + 600)
        store.record("query", query="expenses", ts=t0 + SESSION_GAP_SECONDS + 700)  # 30 min + after the last one
        store.record("file_opened", file_id="f3", path="/docs/monthly expenses.csv", ts=t0 + SESSION_GAP_SECONDS + 760)
        sessions = store.sessions()
        assert len(sessions) == 2, f"expected 2 sessions, got {len(sessions)}"
        assert [len(s) for s in sessions] == [3, 2], [len(s) for s in sessions]
        assert len({e["session_id"] for e in sessions[0]}) == 1
        print("2. Sessions split on the 30-minute gap (3 events, then 2): OK")

        # --- 3. Current working context = the latest session only, and only while it is live ---
        ctx = store.current_session(now=t0 + SESSION_GAP_SECONDS + 800)
        assert ctx["events"] == 2 and ctx["queries"] == ["expenses"] and ctx["files"] == ["/docs/monthly expenses.csv"] and ctx["file_types"] == ["csv"], ctx
        stale = store.current_session(now=t0 + 3 * SESSION_GAP_SECONDS)
        assert stale["session_id"] is None and stale["events"] == 0, stale
        print("3. Current context reflects the live session only (files, types, queries), empty once it has lapsed: OK")

        # --- 4. recent() filters and orders newest first; a reopened store resumes the same session ---
        opened = store.recent(kinds={"file_opened"})
        assert [e["file_id"] for e in opened] == ["f3", "f1"], opened
        store.close()
        reopened = UsageStore(workdir / "usage.db")
        reopened.record("result_clicked", file_id="f3", path="/docs/monthly expenses.csv", ts=t0 + SESSION_GAP_SECONDS + 900)
        assert len(reopened.sessions()) == 2, "an event shortly after the last one must join its session even across a restart"
        print("4. recent() filters/orders correctly; sessions survive a restart: OK")

        # --- 5. Clear empties everything ---
        assert reopened.clear() == 6 and reopened.count() == 0 and reopened.current_session()["events"] == 0
        print("5. Clear activity removes every event: OK")

        # --- 6. Settings: default on, persisted across instances, unknown keys ignored ---
        settings = Settings(workdir)
        assert settings.get("remember_activity") is True
        settings.update({"remember_activity": False, "bogus": 1, "remember_activity_typo": "no"})
        assert Settings(workdir).get("remember_activity") is False
        assert "bogus" not in Settings(workdir).all()
        print("6. Settings persist the 'remember my activity' switch and ignore unknown keys: OK")

        print("\nPhase 16 activity memory OK: events, sessions, working context, clearing and the privacy switch all work as expected.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
