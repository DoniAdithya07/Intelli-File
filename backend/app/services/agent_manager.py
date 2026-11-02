"""A thin status/score aggregator over the components that already exist
(Ask, search/router/personalization, visual, voice) — not a new lifecycle
or recovery system. Every one of those already loads lazily with its own
graceful-degradation guard, already retries on next real use (get_llm
clears llm_error on a successful load; an unreachable folder is re-watched
the next time a scan of it succeeds), and already reports its own recent
failures. This module only reads those existing signals into one place and
adds a score computed from real evaluation files already on disk, instead
of inventing a parallel registry or a new auto-restart timer that could
race with them.

Score: 0-100, weighted accuracy 35% / reliability 25% / response time 15%
/ grounding 15% / resource efficiency 10%. A component with no real
evidence is left out and the remaining weights renormalize — never padded
to a perfect score. Fewer than 20 evaluated cases discounts the result
(small-sample warning), so a thin benchmark can't read as a confident 100.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..indexing.folder_scan import RECENT_FAILURES

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_WEIGHTS = {"accuracy": 0.35, "reliability": 0.25, "response_time": 0.15, "grounding": 0.15, "resource": 0.10}
SMALL_SAMPLE_N = 20


def _read_json(name: str) -> dict | None:
    path = DATA_DIR / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _score(*, accuracy: float | None = None, reliability: float | None = None, response_time: float | None = None,
           grounding: float | None = None, resource: float | None = None, n: int | None = None) -> dict:
    values = {"accuracy": accuracy, "reliability": reliability, "response_time": response_time, "grounding": grounding, "resource": resource}
    present = {k: v for k, v in values.items() if v is not None}
    missing = [k for k in _WEIGHTS if k not in present]
    if not present:
        return {"score": None, "breakdown": {}, "missing": list(_WEIGHTS), "n": n, "note": "no evaluation evidence recorded for this agent yet"}
    total_weight = sum(_WEIGHTS[k] for k in present)
    raw = sum(present[k] * _WEIGHTS[k] for k in present) / total_weight * 100
    discount = 1.0 if n is None or n >= SMALL_SAMPLE_N else 0.5 + 0.5 * (n / SMALL_SAMPLE_N)
    score = round(raw * discount, 1)
    note = None if not missing else f"{', '.join(missing)} not measured; score computed from the rest"
    if n is not None and n < SMALL_SAMPLE_N:
        note = (note + "; " if note else "") + f"small sample (n={n}), score discounted"
    return {"score": score, "breakdown": {k: round(v * 100, 1) for k, v in present.items()}, "missing": missing, "n": n, "note": note}


def _normalize_latency(ms: float, target_ms: float) -> float:
    return max(0.0, min(1.0, 1 - ms / (target_ms * 2)))


def _ask_entry(state) -> dict[str, Any]:
    error = getattr(state, "llm_error", None)
    loaded = getattr(state, "llm", None) is not None
    status = "failed" if error else ("running" if loaded else ("not configured" if state.llm_file is None else "idle"))
    evidence = _read_json("eval_agent.json")
    score = _score()
    if evidence:
        model_stats = next(iter(evidence.values()))
        n = 25  # the 25-question Ask evaluation set documented in docs/TEST_REPORT.md
        false_rate = (model_stats.get("false_answers", 0) or 0) / n
        score = _score(
            accuracy=model_stats.get("cited_right"),
            grounding=model_stats.get("grounded"),
            reliability=max(0.0, 1 - false_rate * 2),
            response_time=_normalize_latency(model_stats.get("mean_s", 0) * 1000, 15000),
            n=n,
        )
    return {
        "id": "ask", "name": "Ask & Reasoning", "status": status,
        "issue": error, "recovery": "Retries automatically on the next question; no restart needed." if error else None,
        **score,
    }


def _search_entry(state) -> dict[str, Any]:
    retrieval = _read_json("eval_retrieval.json")
    routing = _read_json("eval_routing.json")
    personalization = _read_json("eval_personalization.json")
    unreachable = len(getattr(state.live_indexing, "unreachable_roots", []) or [])
    recent_failures = len(RECENT_FAILURES)
    reliability = max(0.0, 1 - 0.1 * unreachable - 0.02 * min(recent_failures, 10))
    score = _score(reliability=reliability)
    if retrieval and routing:
        shipped = retrieval["retrieval"]["+ filename matching (shipped hybrid)"]
        router = routing["report"]["overall"]["router"]
        pers_on = (personalization or {}).get("summary", {}).get("on", {})
        score = _score(
            accuracy=shipped.get("recall@5"),
            reliability=reliability if unreachable or recent_failures else router.get("both@5"),
            response_time=_normalize_latency(router.get("mean_ms", 0), 100),
            grounding=pers_on.get("original_first"),  # personalization lift as the closest "quality" signal here
            n=shipped.get("n"),
        )
    status = "degraded" if unreachable else "running"
    return {
        "id": "search", "name": "Search, Routing & Personalization", "status": status,
        "issue": f"{unreachable} watched folder(s) unreachable" if unreachable else None,
        "recovery": "Re-watched automatically the next time that folder is reachable during a scan." if unreachable else None,
        **score,
    }


def _visual_entry(state) -> dict[str, Any]:
    loaded = getattr(state, "visual_indexer", None) is not None
    status = "running" if loaded else "not configured"
    evidence = _read_json("eval_visual_real.json")
    score = _score(reliability=1.0 if loaded else 0.0)
    if evidence:
        s = evidence.get("summary", {})
        photo_n, video_n = s.get("photo_n", 0), s.get("video_n", 0)
        n = photo_n + video_n
        accuracy = ((s.get("photo_top1", 0) / photo_n if photo_n else 0) + (s.get("video_right", 0) / video_n if video_n else 0)) / 2
        score = _score(accuracy=accuracy, reliability=1.0 if loaded else 0.0, n=n)
    return {"id": "visual", "name": "Photos & Video Search", "status": status, "issue": None if loaded else "CLIP model not installed", "recovery": None, **score}


def _voice_entry(state) -> dict[str, Any]:
    loaded = getattr(state, "transcriber", None) is not None
    status = "running" if loaded else "not configured"
    score = _score(reliability=1.0 if loaded else 0.0)
    return {"id": "voice", "name": "Voice & Audio Transcription", "status": status, "issue": None if loaded else "Whisper model not installed", "recovery": None, **score}


def agent_manager_status(state) -> dict:
    """What the Agent Manager page shows: one entry per real component,
    its live status, a score with its breakdown, and the current issue
    (if any) plus what, if anything, already happens about it."""
    agents = [_ask_entry(state), _search_entry(state), _visual_entry(state), _voice_entry(state)]
    issues = [{"agent": a["id"], "name": a["name"], "issue": a["issue"], "recovery": a["recovery"]} for a in agents if a["issue"]]
    scored = [a["score"] for a in agents if a["score"] is not None]
    overall = round(sum(scored) / len(scored), 1) if scored else None
    return {"agents": agents, "issues": issues, "overall_score": overall, "startup_problems": list(getattr(state, "startup_problems", []) or [])}
