from fastapi import APIRouter, Request

from ..search.learned_router import MIN_TRAINING_QUERIES
from ..services.router_training import train_router, training_queries

router = APIRouter()


# ----- Phase 15 improvement 6: the learned router -----


@router.get("/router/model")
def router_model_endpoint(request: Request):
    state = request.app.state
    return {**state.learned_router.as_dict(), "queries_available": len(training_queries(state)), "needed": MIN_TRAINING_QUERIES}


@router.post("/router/train")
def router_train_endpoint(request: Request):
    return train_router(request.app.state)


# ----- Phase 20: router statistics from real usage -----


@router.get("/router-stats")
def router_stats_endpoint(request: Request):
    """Route mix and mean latency per route over the remembered queries
    (each query event carries its route and total_ms since Phase 18) —
    the Insights screen's live counterpart to scripts/evaluate_routing.py."""
    per_route: dict[str, dict] = {}
    escalated = 0
    for e in request.app.state.usage_store.recent(limit=500, kinds={"query"}):
        meta = e.get("meta") or {}
        route = meta.get("route")
        if not route:
            continue
        bucket = per_route.setdefault(route, {"route": route, "queries": 0, "total_ms": 0.0, "with_results": 0})
        bucket["queries"] += 1
        bucket["total_ms"] += float(meta.get("total_ms") or 0.0)
        bucket["with_results"] += 1 if (meta.get("count") or 0) > 0 else 0
        escalated += 1 if meta.get("escalated") else 0
    total = sum(b["queries"] for b in per_route.values())
    routes = [
        {"route": b["route"], "queries": b["queries"], "share": b["queries"] / total if total else 0.0,
         "mean_ms": round(b["total_ms"] / b["queries"], 1) if b["queries"] else 0.0, "with_results": b["with_results"]}
        for b in sorted(per_route.values(), key=lambda b: -b["queries"])
    ]
    return {"total": total, "escalated": escalated, "routes": routes}
