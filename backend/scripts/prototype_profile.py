"""Phase 17 regression: the user profile (Objective 2) and what it drives.

A small index (two topics: distributed systems, baking) and a synthetic
four-week event log with planted habits:
- `weekly_report.txt` opened every Monday morning (temporal pattern);
- the scaling notes opened far more than anything else (frequency);
- `.md` files opened much more than `.txt` (type preference);
- the two scaling files always opened in the same session (co-occurrence).

Asserts the profile recovers each habit, that personalized ranking moves
the habitual file above an equally relevant stranger (and does NOT when
personalization is off or the profile is cold), and that the three
recommendation lists come out with reasons.

Run with:  backend/venv/bin/python backend/scripts/prototype_profile.py
"""

import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.context import COLD_START_EVENTS, ProfileBuilder, UsageStore, recommend  # noqa: E402
from app.embeddings.model import EmbeddingModel, default_model_dir  # noqa: E402
from app.files.identity import build_file_record  # noqa: E402
from app.indexing import Indexer  # noqa: E402
from app.search import SearchService  # noqa: E402
from app.storage import FileRecordStore, KeywordStore, LanceDBVectorStore  # noqa: E402

MODEL_DIR = default_model_dir(Path(__file__).resolve().parents[1] / "models")


def main() -> None:
    if not (MODEL_DIR / "model.onnx").exists():
        print("Embedding model not found — run scripts/download_model.py first.")
        sys.exit(1)
    workdir = Path(tempfile.mkdtemp())
    try:
        docs = {
            "scaling_notes.md": "Horizontal scaling adds server instances behind a load balancer when traffic increases. Autoscaling policies react to CPU and request latency.",
            "scaling_notes_old.txt": "Horizontal scaling adds server instances behind a load balancer when traffic increases. Autoscaling policies react to CPU and request latency. (older copy with extra notes)",
            "queue_design.md": "A message queue decouples producers from consumers; consumer groups scale horizontally and partitions preserve ordering.",
            "sourdough.md": "Feed the sourdough starter with flour and water twice a day; bulk ferment the dough until it doubles.",
            "croissants.txt": "Laminate the butter into the dough with three folds; proof the croissants until puffy before baking.",
            "weekly_report.txt": "Weekly status report: incidents, deployments, on-call handover and next week's priorities.",
        }
        files = workdir / "files"
        files.mkdir()
        for name, text in docs.items():
            (files / name).write_text(text)

        model = EmbeddingModel(MODEL_DIR)
        vector_store = LanceDBVectorStore(str(workdir / "vectors"))
        keyword_store = KeywordStore(workdir / "keyword.db")
        record_store = FileRecordStore(workdir / "files.db")
        indexer = Indexer(model, vector_store, keyword_store, record_store)
        ids = {}
        for name in docs:
            record = build_file_record(files / name)
            record_store.upsert(record)
            indexer.index_file(files / name, record.file_id, record.hash)
            ids[name] = record.file_id

        usage = UsageStore(workdir / "usage.db")
        builder = ProfileBuilder(usage, record_store, vector_store, keyword_store)
        search = SearchService(model, vector_store, keyword_store, record_store)
        search.profile_builder = builder

        # --- 1. Cold start: no events → no personalization, honest flag ---
        profile = builder.get()
        assert profile.cold_start and profile.events == 0
        plain = search.search("adding servers when traffic grows", top_k=5)
        assert plain and all(r["personal"] is None for r in plain)
        cold = recommend(profile, usage, record_store)
        assert cold["cold_start"] and cold["recent"] and "learning" in cold["recent"][0]["reason"] and not cold["likely_next"]
        print("1. Cold start: profile flagged, ranking untouched, recommendations fall back to recently modified: OK")

        # --- synthetic 4 weeks of habits, ending "now" ---
        now = time.time()
        today = datetime.fromtimestamp(now)
        def at(days_ago: int, hour: int) -> float:
            return (today.replace(hour=hour, minute=5, second=0, microsecond=0) - timedelta(days=days_ago)).timestamp()
        def open_file(name: str, ts: float) -> None:
            usage.record("file_opened", file_id=ids[name], path=str(files / name), ts=ts)
        for days_ago in range(28, 0, -1):
            day = today - timedelta(days=days_ago)
            if day.weekday() == 0:  # every Monday 09:05 — the report, then the scaling pair together
                open_file("weekly_report.txt", at(days_ago, 9))
                open_file("scaling_notes.md", at(days_ago, 9) + 60)
                open_file("queue_design.md", at(days_ago, 9) + 120)
            if days_ago % 2 == 0:  # most afternoons: the scaling notes again (frequency), plus baking (.md)
                open_file("scaling_notes.md", at(days_ago, 15))
                open_file("sourdough.md", at(days_ago, 15) + 300)
            if days_ago % 9 == 0:  # rarely: a .txt recipe
                open_file("croissants.txt", at(days_ago, 20))
        assert usage.count() >= COLD_START_EVENTS

        # --- 2. Frequency + type preference recovered ---
        profile = builder.get(force=True)
        assert not profile.cold_start
        top = profile.top_files[0]
        assert top["filename"] == "scaling_notes.md", [f["filename"] for f in profile.top_files]
        assert profile.type_shares["md"] > profile.type_shares["txt"], profile.type_shares
        print(f"2. Frequently accessed file = {top['filename']} (score 1.0); preferred type md {profile.type_shares['md']:.0%} > txt {profile.type_shares['txt']:.0%}: OK")

        # --- 3. Temporal pattern: the report's usual slot is Monday morning ---
        report = next(f for f in profile.top_files if f["filename"] == "weekly_report.txt")
        assert report["usual_slot"].startswith("Monday 08:00"), report["usual_slot"]
        monday_9 = (today - timedelta(days=(today.weekday() - 0) % 7 or 7)).replace(hour=9, minute=30).timestamp()
        assert profile.time_affinity(ids["weekly_report.txt"], now=monday_9) > 0.9
        assert profile.time_affinity(ids["weekly_report.txt"], now=monday_9 + 6 * 3600) < 0.2  # Monday afternoon
        print(f"3. Temporal pattern: weekly_report.txt usual slot = {report['usual_slot']}; affinity high Monday 09:30, low Monday 15:30: OK")

        # --- 4. Topics of interest: two clusters, labelled from their own words ---
        labels = [t["label"] for t in profile.topics]
        assert len(profile.topics) >= 2, labels
        joined = " ".join(labels)
        assert any(w in joined for w in ("scaling", "horizontal", "queue", "instances", "autoscaling")), labels
        assert any(w in joined for w in ("sourdough", "dough", "starter", "flour", "croissants")), labels
        first_topic_files = {f["filename"] for f in profile.topics[0]["files"]}
        assert "scaling_notes.md" in first_topic_files, profile.topics[0]
        print(f"4. Topics of interest ({len(profile.topics)}): {labels}; the heaviest holds the scaling notes: OK")

        # --- 5. Recurring pattern: the scaling notes co-occur with the queue design ---
        co = profile.cooccurrence[ids["scaling_notes.md"]]
        assert co.most_common(1)[0][0] in (ids["queue_design.md"], ids["sourdough.md"])
        assert co[ids["queue_design.md"]] >= 3, co
        print(f"5. Recurring work pattern: scaling_notes.md shares {co[ids['queue_design.md']]} sessions with queue_design.md: OK")

        # --- 6. Personalized ranking: the habitual copy outranks its near-identical twin; off/cold leaves order alone ---
        personalized = search.search("adding servers when traffic grows", top_k=5)
        names = [r["filename"] for r in personalized]
        assert names[0] == "scaling_notes.md", names
        assert personalized[0]["personal"]["boost"] > personalized[1]["personal"]["boost"]
        assert "You use this often" in personalized[0]["why"], personalized[0]["why"]
        search.personalize_enabled = lambda: False
        off = search.search("adding servers when traffic grows", top_k=5)
        assert all(r["personal"] is None for r in off)
        search.personalize_enabled = lambda: True
        strong_before = [r["file_id"] for r in off if r["confidence"] == "strong"]
        strong_after = [r["file_id"] for r in personalized if r["confidence"] == "strong"]
        assert set(strong_before) == set(strong_after), "personalization must not move results across tiers"
        print(f"6. Personalized ranking: {names[0]} first with reason {personalized[0]['personal']['reasons']}; off → no boost; tiers unchanged: OK")

        # --- 7. Recommendations with reasons, on a Monday morning, mid-session ---
        usage.record("file_opened", file_id=ids["weekly_report.txt"], path=str(files / "weekly_report.txt"), ts=monday_9 + 28 * 86400 if monday_9 + 28 * 86400 <= now else now)
        profile = builder.get(force=True)
        recs = recommend(profile, usage, record_store, now=usage.recent(limit=1)[0]["ts"] + 30)
        assert not recs["cold_start"]
        likely = [e["filename"] for e in recs["likely_next"]]
        assert "scaling_notes.md" in likely and "queue_design.md" in likely, recs["likely_next"]
        assert all(e["reason"] for e in recs["likely_next"] + recs["usual_now"] + recs["recent"])
        recent = [e["filename"] for e in recs["recent"]]
        assert "scaling_notes.md" in recent or "scaling_notes.md" in likely
        assert not any(e["file_id"] in {x["file_id"] for x in recs["likely_next"]} for e in recs["recent"]), "lists must not repeat a file"
        print(f"7. Recommendations: likely next {likely} (co-occurrence), usual now {[e['filename'] for e in recs['usual_now']]}, recent {recent}: OK")

        # --- 8. A deleted file drops out of every list ---
        record_store.mark_deleted(str(files / "queue_design.md"))
        profile = builder.get(force=True)
        assert ids["queue_design.md"] not in profile.file_scores
        recs = recommend(profile, usage, record_store, now=usage.recent(limit=1)[0]["ts"] + 30)
        assert all(e["filename"] != "queue_design.md" for e in recs["likely_next"] + recs["recent"] + recs["usual_now"])
        print("8. A file that left the index is never recommended or scored: OK")

        print("\nPhase 17 profile OK: frequency, type preference, temporal patterns, topics, co-occurrence, personalized ranking and recommendations all work as expected.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
