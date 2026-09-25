"""A learned query router (Phase 15 improvement 6, 2026-09-21).

The rule-based router (router.py) decides the tier from hand-set
thresholds. This module learns the same decision from evidence: for a
query, did the FILENAME tier suffice (a confident hit), did the KEYWORD
tier, or was HYBRID needed? Evidence comes from replaying queries
through the tiers against the real index (`label_query`) — the labelled
corpus of Phase 20 plus the user's own remembered queries — so the model
adapts to how this person searches and what their index contains.

Two tiny logistic regressions on the router's own features (no sklearn:
numpy gradient descent, weights stored as JSON in config/router_model.json):
    P(filename tier suffices | features)
    P(keyword tier suffices  | features)
The cheapest tier whose probability clears CONFIDENCE is chosen; below
that, the rules decide. Escalation (one tier up on a non-confident
result) still applies, so a wrong cheap guess costs one extra pass, never
a missing result.
"""

import json
import math
import re
import time
from pathlib import Path

import numpy as np

from .router import PLANS, RouteDecision, route as rule_route

CONFIDENCE = 0.6  # swept 2026-09-21 on the corpus under 5-fold CV: 0.5–0.6 tie the rules (93%), 0.65 → 83%, 0.75 → 72%
MIN_TRAINING_QUERIES = 40
FEATURE_NAMES = ["bias", "words", "content_words", "unknown_words", "question", "stopword_ratio", "clauses", "filename_coverage", "has_filters", "long"]

_WORD_RE = re.compile(r"[a-z0-9']+")


def features_of(decision: RouteDecision, has_filters: bool) -> np.ndarray:
    f = decision.features
    words = f.get("words", 0)
    return np.array([
        1.0,
        min(words, 12) / 12.0,
        min(f.get("content_words", 0), 10) / 10.0,
        min(f.get("unknown_words", 0), 5) / 5.0,
        1.0 if f.get("question") else 0.0,
        float(f.get("stopword_ratio", 0.0)),
        min(f.get("clauses", 1), 4) / 4.0,
        float(f.get("filename_coverage", 0.0)),
        1.0 if has_filters else 0.0,
        1.0 if words >= 7 else 0.0,
    ])


def _fit(X: np.ndarray, y: np.ndarray, l2: float = 0.05, steps: int = 3000, lr: float = 0.3) -> np.ndarray:
    w = np.zeros(X.shape[1])
    for _ in range(steps):
        p = 1 / (1 + np.exp(-(X @ w)))
        grad = X.T @ (p - y) / len(y) + l2 * np.r_[0, w[1:]]
        w -= lr * grad
    return w


class LearnedRouter:
    def __init__(self, path: Path):
        self.path = path
        self.weights: dict[str, list[float]] | None = None
        self.stored_weights: dict[str, list[float]] | None = None
        self.active = False
        self.trained_on = 0
        self.trained_at: float | None = None
        self.accuracy: dict | None = None
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            if data.get("features") == FEATURE_NAMES:
                self.active = bool(data.get("active", True))
                self.stored_weights = data["weights"]
                self.weights = data["weights"] if self.active else None
                self.trained_on = data.get("trained_on", 0)
                self.trained_at = data.get("trained_at")
                self.accuracy = data.get("accuracy")
        except (OSError, ValueError, KeyError):
            self.weights = None

    @property
    def available(self) -> bool:
        return self.weights is not None

    def probabilities(self, decision: RouteDecision, has_filters: bool) -> dict[str, float]:
        x = features_of(decision, has_filters)
        return {tier: float(1 / (1 + math.exp(-float(x @ np.array(w))))) for tier, w in self.weights.items()}

    def decide(self, text: str, has_filters: bool, exact: bool, active_records, vocabulary) -> RouteDecision:
        """Rules first for the cases that need no learning (exact phrase,
        filters-only); then the cheapest tier the model is confident about;
        else the rules' answer, annotated."""
        rules = rule_route(text, has_filters, exact, active_records, vocabulary)
        if not self.available or exact or rules.tier == "metadata" or not text.strip():
            return rules
        probs = self.probabilities(rules, has_filters)
        for tier in ("filename", "keyword"):
            if probs.get(tier, 0.0) >= CONFIDENCE:
                if tier == rules.tier:
                    rules.reason += f" · learned model agrees ({probs[tier]:.0%})"
                    rules.features["learned"] = probs
                    return rules
                return RouteDecision(tier, rules.complexity, f"learned from {self.trained_on} queries: {tier} tier suffices ({probs[tier]:.0%})", {**rules.features, "learned": probs, "rules_said": rules.tier})
        if rules.tier in ("filename", "keyword"):
            # The rules would go cheap but the model has seen that fail for
            # queries like this: take hybrid (escalation would get there anyway,
            # at the cost of a wasted cheap pass).
            return RouteDecision("hybrid", max(rules.complexity, 2), f"learned from {self.trained_on} queries: {rules.tier} tier tends not to suffice here ({probs[rules.tier]:.0%}) — hybrid", {**rules.features, "learned": probs, "rules_said": rules.tier})
        rules.features["learned"] = probs
        return rules

    def train(self, examples: list[tuple[RouteDecision, bool, str]], accuracy: dict | None = None, active: bool = True) -> None:
        """examples: (rules decision, has_filters, cheapest sufficient tier).
        `active=False` stores the model and its score for the Insights
        screen but leaves the rules in charge (it did not beat them)."""
        X = np.stack([features_of(d, hf) for d, hf, _ in examples])
        weights = {}
        for tier in ("filename", "keyword"):
            rank = {"filename": 0, "keyword": 1, "hybrid": 2, "hybrid+rerank": 3, "none": 9}
            y = np.array([1.0 if rank[label] <= rank[tier] else 0.0 for _, _, label in examples])
            weights[tier] = _fit(X, y).tolist()
        self.weights = weights if active else None
        self.stored_weights = weights
        self.trained_on = len(examples)
        self.trained_at = time.time()
        self.accuracy = accuracy
        self.active = active
        self.path.write_text(json.dumps({"features": FEATURE_NAMES, "weights": weights, "active": active, "trained_on": self.trained_on, "trained_at": self.trained_at, "accuracy": accuracy}, indent=2))

    def as_dict(self) -> dict:
        return {"available": self.available, "active": self.active, "trained_on": self.trained_on, "trained_at": self.trained_at, "accuracy": self.accuracy, "confidence": CONFIDENCE}


def label_query(search_service, query: str) -> str:
    """The cheapest tier that returns a confident (strong) result for this
    query against the current index: filename → keyword → hybrid; "none"
    when even hybrid finds nothing strong."""
    from .query_parsing import parse_query

    parsed = parse_query(query)
    text = parsed.text
    active = search_service._active_records()
    if parsed.filters.any():
        active = [r for r in active if parsed.filters.matches(r.path, r.size, r.modified_time)]
    for tier in ("filename", "keyword", "hybrid"):
        results = search_service._run_plan(PLANS[tier], text, text, parsed, active, 5, [])
        if any(r["confidence"] == "strong" for r in results):
            return tier
    return "none"
