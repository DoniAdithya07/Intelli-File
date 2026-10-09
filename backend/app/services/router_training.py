import logging
import time

from ..search.learned_router import MIN_TRAINING_QUERIES, LearnedRouter, label_query

logger = logging.getLogger(__name__)


def training_queries(state) -> list[str]:
    """Distinct text queries this user has run (auto/smart/keyword modes;
    not visual, not Ask), newest first."""
    seen, out = set(), []
    for e in state.usage_store.recent(limit=2000, kinds={"query"}):
        meta = e.get("meta") or {}
        q = (e.get("query") or "").strip()
        # meta.source = imported or made-up (the sample history): not this user's own query
        if not q or meta.get("source") or meta.get("mode") in ("visual", "ask", "exact") or q.lower() in seen:
            continue
        seen.add(q.lower())
        out.append(q)
    return out


def train_router(state, min_queries: int = MIN_TRAINING_QUERIES) -> dict:
    """Replay the user's own queries through the tiers against the live
    index, label each with the cheapest tier that gave a confident hit,
    fit the model, and report how often it would have picked the right
    tier compared with the rules (on the same queries)."""
    from ..search.router import route as rule_route

    queries = training_queries(state)
    if len(queries) < min_queries:
        return {"trained": False, "queries": len(queries), "needed": min_queries}
    svc = state.search_service
    examples = []
    for q in queries:
        from ..search.query_parsing import parse_query

        parsed = parse_query(q)
        if not parsed.text or parsed.exact_phrase is not None:
            continue
        active = svc._active_records()
        decision = rule_route(parsed.text, parsed.filters.any(), False, active, svc._correction_vocabulary(active))
        examples.append((decision, parsed.filters.any(), label_query(svc, q)))
    if len(examples) < min_queries:
        return {"trained": False, "queries": len(examples), "needed": min_queries}
    # Honest accuracy: train on 80%, score on the held-out 20%, then fit on all.
    cut = int(len(examples) * 0.8)
    holdout = examples[cut:]
    trial = LearnedRouter(state.dirs["config"] / "router_model.tmp.json")
    trial.train(examples[:cut])
    rank = {"filename": 0, "keyword": 1, "hybrid": 2, "hybrid+rerank": 3, "none": 2}
    def scored(picker):
        ok = 0
        for d, hf, label in holdout:
            chosen = picker(d, hf)
            ok += 1 if rank[chosen] == min(rank[label], 2) else 0
        return ok / max(1, len(holdout))
    acc_rules = scored(lambda d, hf: d.tier)
    from ..search.learned_router import CONFIDENCE

    acc_learned = scored(lambda d, hf: next((t for t in ("filename", "keyword") if trial.probabilities(d, hf)[t] >= CONFIDENCE), "hybrid"))
    try:
        trial.path.unlink()
    except OSError:
        pass
    accuracy = {"holdout": len(holdout), "rules": round(acc_rules, 3), "learned": round(acc_learned, 3)}
    # The learned model takes over only when it beats the rules on this
    # user's own held-out queries; otherwise it is stored (for Insights)
    # and the rules stay in charge.
    activate = acc_learned > acc_rules
    state.learned_router.train(examples, accuracy=accuracy, active=activate)
    logger.info("learned router trained on %d queries: holdout tier accuracy rules %.0f%% vs learned %.0f%% — %s", len(examples), 100 * acc_rules, 100 * acc_learned, "ACTIVE" if activate else "rules kept")
    return {"trained": True, "active": activate, "queries": len(examples), **accuracy}


def train_router_if_ready(state) -> None:
    try:
        time.sleep(20)  # after the startup scans have settled
        result = train_router(state)
        if not result.get("trained"):
            logger.info("learned router: %s of %s queries remembered — rules stay in charge", result.get("queries"), result.get("needed"))
    except Exception:
        logger.exception("learned router training failed")
