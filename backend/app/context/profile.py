"""The user profile — Objective 2 of the assignment, built from Phase 16's
usage events plus what the index already knows about each file.

Five signals, each one named in the brief:

- **frequently accessed files** — a per-file score from opens, reveals,
  clicks and appearances in results, each decayed by age (14-day
  half-life) so last month's project fades and this week's grows;
- **preferred file types** — the decayed share of opens/clicks per
  extension;
- **topics of interest** — the files the user opened, each represented
  by the mean of its chunk embeddings, grouped by k-means into a few
  clusters; each cluster is labelled by the terms most specific to it
  (tf within the cluster × rarity across the whole index);
- **temporal patterns** — a weekday × 4-hour-slot histogram of activity,
  overall and per file, so "what do I usually touch on Monday morning"
  has an answer;
- **recurring work patterns** — files that are touched in the same
  session, so opening A can suggest B.

Everything is computed from local data only and rebuilt lazily when the
event store changes. With fewer than COLD_START_EVENTS events the
profile says so (`cold_start`) and downstream code falls back to recency
and the index's own type mix.
"""

import math
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

from ..indexing import CHUNKS_TABLE
from ..search.explain import _STOPWORDS
from ..storage import FileRecordStore, KeywordStore, LanceDBVectorStore
from .usage_store import UsageStore

HALF_LIFE_DAYS = 14.0
COLD_START_EVENTS = 20
MAX_TOPICS = 5
MIN_FILES_PER_TOPIC = 2
TOPIC_LABEL_TERMS = 3
SLOT_HOURS = 4  # 6 slots a day
SLOTS_PER_DAY = 24 // SLOT_HOURS
REBUILD_MIN_INTERVAL = 5.0  # s — never rebuild more often than this while events stream in

# How much each kind of event says about a file mattering to the user.
EVENT_WEIGHT = {
    "file_opened": 3.0,
    "recommendation_clicked": 3.0,
    "file_revealed": 2.0,
    "result_clicked": 1.0,
    "shown": 0.15,  # appeared in a query's top-5 (from the query event's meta)
}

_TOKEN_RE = re.compile(r"[a-z][a-z0-9]{2,}")


def time_slot(ts: float) -> tuple[int, int]:
    """(weekday 0=Mon..6, slot 0..5) in local time."""
    dt = datetime.fromtimestamp(ts)
    return dt.weekday(), dt.hour // SLOT_HOURS


def slot_label(weekday: int, slot: int) -> str:
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    start = slot * SLOT_HOURS
    return f"{days[weekday]} {start:02d}:00–{start + SLOT_HOURS:02d}:00"


def _decay(age_seconds: float) -> float:
    return 0.5 ** (age_seconds / (HALF_LIFE_DAYS * 86400))


class Profile:
    """One built profile. Plain data plus a few lookups used by ranking."""

    def __init__(self):
        self.built_at = 0.0
        self.events = 0
        self.sessions = 0
        self.cold_start = True
        self.file_scores: dict[str, float] = {}       # file_id -> decayed activity score (0..1 after normalisation)
        self.file_paths: dict[str, str] = {}          # file_id -> last known path
        self.file_last_touched: dict[str, float] = {}
        self.type_shares: dict[str, float] = {}       # ext -> share of decayed opens/clicks (sums to 1)
        self.topics: list[dict] = []                  # {"id", "label", "terms", "files", "weight"}
        self.topic_centroids: np.ndarray | None = None
        self.slot_counts = np.zeros((7, SLOTS_PER_DAY))            # all activity
        self.file_slot_counts: dict[str, np.ndarray] = {}          # per file
        self.type_slot_counts: dict[str, np.ndarray] = {}          # per extension
        self.cooccurrence: dict[str, Counter] = {}    # file_id -> Counter(other file_id -> sessions together)
        self.top_files: list[dict] = []

    # ----- lookups used by ranking / recommendations -----

    def frequency(self, file_id: str) -> float:
        return self.file_scores.get(file_id, 0.0)

    def recency(self, file_id: str, now: float | None = None) -> float:
        last = self.file_last_touched.get(file_id)
        if last is None:
            return 0.0
        return _decay((now or time.time()) - last)

    def type_preference(self, path: str) -> float:
        return self.type_shares.get(Path(path).suffix.lower().lstrip("."), 0.0)

    def time_affinity(self, file_id: str, now: float | None = None) -> float:
        """How much of this file's activity happens in the current weekday
        slot (weighted) or the same time of day on any weekday."""
        counts = self.file_slot_counts.get(file_id)
        if counts is None or counts.sum() == 0:
            return 0.0
        weekday, slot = time_slot(now or time.time())
        same_slot_any_day = counts[:, slot].sum() / counts.sum()
        same_slot_same_day = counts[weekday, slot] / counts.sum()
        return min(1.0, 0.6 * same_slot_any_day + 0.4 * same_slot_same_day * 7)

    def topic_affinity(self, vector: np.ndarray | None) -> tuple[float, str | None]:
        """Cosine similarity of a file's centroid to the closest topic of
        interest (weighted by that topic's share), and the topic's label."""
        if vector is None or self.topic_centroids is None or not self.topics:
            return 0.0, None
        norms = np.linalg.norm(self.topic_centroids, axis=1) * (np.linalg.norm(vector) or 1.0)
        sims = (self.topic_centroids @ vector) / np.where(norms == 0, 1.0, norms)
        best = int(np.argmax(sims))
        return float(max(0.0, sims[best])), self.topics[best]["label"]

    def as_dict(self) -> dict:
        weekday, slot = time_slot(time.time())
        return {
            "built_at": self.built_at,
            "events": self.events,
            "sessions": self.sessions,
            "cold_start": self.cold_start,
            "cold_start_threshold": COLD_START_EVENTS,
            "top_files": self.top_files,
            "type_shares": dict(sorted(self.type_shares.items(), key=lambda kv: -kv[1])),
            "topics": [{k: v for k, v in t.items() if k != "vector"} for t in self.topics],
            "heatmap": self.slot_counts.astype(int).tolist(),
            "slot_hours": SLOT_HOURS,
            "now_slot": {"weekday": weekday, "slot": slot, "label": slot_label(weekday, slot)},
        }


class ProfileBuilder:
    def __init__(self, usage_store: UsageStore, file_record_store: FileRecordStore, vector_store: LanceDBVectorStore, keyword_store: KeywordStore):
        self.usage_store = usage_store
        self.file_record_store = file_record_store
        self.vector_store = vector_store
        self.keyword_store = keyword_store
        self._lock = threading.Lock()
        self._profile: Profile | None = None
        self._built_for_version = -1
        self._file_vectors: dict[str, np.ndarray | None] = {}  # cache: file_id -> centroid

    def get(self, force: bool = False) -> Profile:
        with self._lock:
            version = self.usage_store.version
            stale = self._profile is None or self._built_for_version != version
            recent = self._profile is not None and time.time() - self._profile.built_at < REBUILD_MIN_INTERVAL
            if force or (stale and not recent):
                self._profile = self._build()
                self._built_for_version = version
            return self._profile

    def file_vector(self, file_id: str) -> np.ndarray | None:
        """Mean of a file's chunk embeddings (its 'topic'), cached."""
        if file_id in self._file_vectors:
            return self._file_vectors[file_id]
        rows = self.vector_store.get_by_file_id(CHUNKS_TABLE, file_id)
        vector = None
        if rows:
            matrix = np.array([r["vector"] for r in rows], dtype=np.float32)
            vector = matrix.mean(axis=0)
            norm = np.linalg.norm(vector)
            vector = vector / norm if norm else None
        self._file_vectors[file_id] = vector
        return vector

    def forget_vectors(self) -> None:
        self._file_vectors.clear()

    # ----- building -----

    def _build(self) -> Profile:
        now = time.time()
        profile = Profile()
        profile.built_at = now
        events = self.usage_store.all_events()
        profile.events = len(events)
        profile.sessions = len({e["session_id"] for e in events})
        profile.cold_start = len(events) < COLD_START_EVENTS

        raw_scores: dict[str, float] = defaultdict(float)
        type_scores: dict[str, float] = defaultdict(float)
        session_files: dict[str, set[str]] = defaultdict(set)
        for e in events:
            age = max(0.0, now - e["ts"])
            weight = EVENT_WEIGHT.get(e["kind"])
            if e["kind"] == "query":
                for shown in (e["meta"] or {}).get("results", [])[:5]:
                    raw_scores[shown] += EVENT_WEIGHT["shown"] * _decay(age)
                continue
            if weight is None or not e["file_id"]:
                continue
            decayed = weight * _decay(age)
            fid = e["file_id"]
            raw_scores[fid] += decayed
            profile.file_last_touched[fid] = max(profile.file_last_touched.get(fid, 0.0), e["ts"])
            if e["path"]:
                profile.file_paths[fid] = e["path"]
            if e["file_type"]:
                type_scores[e["file_type"]] += decayed
                profile.type_slot_counts.setdefault(e["file_type"], np.zeros((7, SLOTS_PER_DAY)))
                weekday, slot = time_slot(e["ts"])
                profile.type_slot_counts[e["file_type"]][weekday, slot] += 1
            weekday, slot = time_slot(e["ts"])
            profile.slot_counts[weekday, slot] += 1
            profile.file_slot_counts.setdefault(fid, np.zeros((7, SLOTS_PER_DAY)))[weekday, slot] += 1
            session_files[e["session_id"]].add(fid)

        # Files that no longer exist in the index drop out of everything
        # that recommends them; their history still shaped the type mix.
        active = {r.file_id: r for r in self.file_record_store.list_active()}
        for fid in list(raw_scores):
            if fid not in active:
                raw_scores.pop(fid)
        for fid, record in active.items():
            if fid in raw_scores:
                profile.file_paths[fid] = record.path

        top = max(raw_scores.values(), default=0.0)
        profile.file_scores = {fid: s / top for fid, s in raw_scores.items()} if top > 0 else {}
        total_type = sum(type_scores.values())
        profile.type_shares = {t: s / total_type for t, s in type_scores.items()} if total_type > 0 else {}

        for files in session_files.values():
            for fid in files:
                if fid not in active:
                    continue
                counter = profile.cooccurrence.setdefault(fid, Counter())
                for other in files:
                    if other != fid and other in active:
                        counter[other] += 1

        self._build_topics(profile, active)

        profile.top_files = [
            {
                "file_id": fid,
                "path": profile.file_paths.get(fid, ""),
                "filename": Path(profile.file_paths.get(fid, "")).name,
                "score": round(score, 3),
                "last_touched": profile.file_last_touched.get(fid),
                "usual_slot": _usual_slot(profile.file_slot_counts.get(fid)),
            }
            for fid, score in sorted(profile.file_scores.items(), key=lambda kv: -kv[1])[:10]
        ]
        return profile

    def _build_topics(self, profile: Profile, active: dict) -> None:
        """k-means over the centroids of files the user actually opened or
        clicked (not merely saw), weighted by their activity score."""
        candidates = [fid for fid, score in profile.file_scores.items() if fid in profile.file_last_touched and fid in active]
        vectors, ids = [], []
        for fid in candidates:
            vector = self.file_vector(fid)
            if vector is not None:
                vectors.append(vector)
                ids.append(fid)
        if len(ids) < MIN_FILES_PER_TOPIC:
            return
        matrix = np.stack(vectors)
        weights = np.array([profile.file_scores[fid] for fid in ids])
        k = max(1, min(MAX_TOPICS, len(ids) // MIN_FILES_PER_TOPIC))
        labels, centroids = _kmeans(matrix, k)
        topics = []
        for cluster in range(k):
            members = [ids[i] for i in range(len(ids)) if labels[i] == cluster]
            if not members:
                continue
            weight = float(sum(profile.file_scores[m] for m in members) / weights.sum())
            terms = self._label_terms(members, {m: profile.file_scores[m] for m in members})
            topics.append(
                {
                    "id": cluster,
                    "label": ", ".join(terms) if terms else Path(profile.file_paths.get(members[0], "topic")).stem,
                    "terms": terms,
                    "files": [{"file_id": m, "filename": Path(profile.file_paths.get(m, "")).name} for m in sorted(members, key=lambda m: -profile.file_scores[m])[:5]],
                    "file_count": len(members),
                    "weight": round(weight, 3),
                    "vector": centroids[cluster],
                }
            )
        topics.sort(key=lambda t: -t["weight"])
        profile.topics = topics
        profile.topic_centroids = np.stack([t["vector"] for t in topics]) if topics else None

    def _label_terms(self, file_ids: list[str], weights: dict[str, float]) -> list[str]:
        """The terms most specific to these files: term frequency inside
        the cluster (each file's words weighted by how much the user uses
        it) × inverse document frequency over the whole index (fts5vocab's
        per-term row counts; the commonest term's count is a fair stand-in
        for the number of rows)."""
        vocabulary = self.keyword_store.vocabulary()
        total_rows = max(vocabulary.values(), default=1)
        tf: Counter = Counter()
        for fid in file_ids:
            weight = 0.25 + weights.get(fid, 0.0)
            for row in self.vector_store.get_by_file_id(CHUNKS_TABLE, fid):
                for token in _TOKEN_RE.findall((row["payload"].get("content") or "").lower()):
                    if token not in _STOPWORDS:
                        tf[token] += weight
            record = self.file_record_store.get_by_file_id(fid)
            if record is not None:
                for token in _TOKEN_RE.findall(Path(record.path).stem.lower()):
                    tf[token] += 3 * weight  # a file's own name is a strong hint of its topic
        scored = {term: count * math.log(1 + total_rows / vocabulary.get(term, 1)) for term, count in tf.items()}
        return [t for t, _ in sorted(scored.items(), key=lambda kv: -kv[1])[:TOPIC_LABEL_TERMS]]


def _usual_slot(counts: np.ndarray | None) -> str | None:
    if counts is None or counts.sum() == 0:
        return None
    weekday, slot = np.unravel_index(int(np.argmax(counts)), counts.shape)
    return slot_label(int(weekday), int(slot))


def _kmeans(matrix: np.ndarray, k: int, iterations: int = 25, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """Plain k-means on unit vectors (cosine geometry). Deterministic."""
    rng = np.random.default_rng(seed)
    n = matrix.shape[0]
    k = min(k, n)
    centroids = matrix[rng.choice(n, size=k, replace=False)].copy()
    labels = np.zeros(n, dtype=int)
    for _ in range(iterations):
        sims = matrix @ centroids.T
        new_labels = np.argmax(sims, axis=1)
        if np.array_equal(new_labels, labels) and _ > 0:
            break
        labels = new_labels
        for c in range(k):
            members = matrix[labels == c]
            if len(members):
                centroid = members.mean(axis=0)
                norm = np.linalg.norm(centroid)
                centroids[c] = centroid / norm if norm else centroid
    return labels, centroids
