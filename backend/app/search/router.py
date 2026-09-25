"""Query routing (Phase 18, Objective 3): estimate how hard a query is
from cheap surface features and pick the cheapest retrieval route that can
answer it. Until 2026-09-21 every query ran spelling correction, BM25, an
embedding, a vector scan and fusion — a file name typed into the box paid
for all of it.

Tiers, cheapest first (each is a plan of stages for SearchService):

    filename        the query names a file: match names only
    metadata        only filters, no words: list matching files
    keyword         a few plain words: BM25 (+ names), no embedding
    hybrid          natural language: BM25 + embedding + vector + fusion
    hybrid+rerank   hybrid, then the cross-encoder re-reads the top
                    candidates (promotion, not veto — see reranker.py).
                    Reached ONLY by escalation from hybrid since the
                    Phase 20 measurement (2026-09-21): chosen by query
                    length it cost +15 ms on a sixth of the queries and
                    changed no result where fusion was already confident.

Escalation: when a route returns nothing, or nothing confident, the
service re-runs one tier up — once. That keeps a wrong cheap guess from
costing the user a result, while a right cheap guess costs nothing extra.

Everything here is a heuristic on the query string plus two in-memory
lookups (file names, the index vocabulary); it never touches the models.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..files.identity import FileRecord
from .filenames import match_filenames

TIERS = ["filename", "metadata", "keyword", "hybrid", "hybrid+rerank"]
# What escalates to what. Metadata has no text to escalate with.
NEXT_TIER = {"filename": "keyword", "keyword": "hybrid", "hybrid": "hybrid+rerank"}

_WORD_RE = re.compile(r"[a-z0-9']+")
_QUESTION_WORDS = {"how", "what", "when", "where", "why", "which", "who", "whom", "whose", "did", "does", "do", "is", "are", "was", "were", "can", "could", "should", "would", "will", "explain", "find", "show", "tell"}
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "in", "on", "at", "to", "for", "with", "about", "from", "by", "as", "into",
    "my", "me", "i", "we", "our", "you", "your", "it", "its", "this", "that", "these", "those", "there", "here",
    "is", "are", "was", "were", "be", "been", "have", "has", "had", "not", "no", "so", "than", "then", "up", "out",
}
KEYWORD_MAX_WORDS = 3


@dataclass
class Plan:
    """Which stages a tier runs."""

    tier: str
    names: bool = False
    keyword: bool = False
    semantic: bool = False
    rerank: bool = False
    exact: bool = False


PLANS = {
    "filename": Plan("filename", names=True),
    "metadata": Plan("metadata"),
    "keyword": Plan("keyword", names=True, keyword=True),
    "hybrid": Plan("hybrid", names=True, keyword=True, semantic=True),
    "hybrid+rerank": Plan("hybrid+rerank", names=True, keyword=True, semantic=True, rerank=True),
}


@dataclass
class RouteDecision:
    tier: str
    complexity: int  # 0 trivial … 3 complex
    reason: str
    features: dict = field(default_factory=dict)

    @property
    def plan(self) -> Plan:
        return PLANS[self.tier]


def plan_for_mode(mode: str) -> Plan:
    """Manual overrides (the PRD's Search Modes) map onto fixed plans."""
    if mode == "exact":
        return Plan("keyword", names=False, keyword=True, exact=True)
    if mode == "keyword":
        return PLANS["keyword"]
    return PLANS["hybrid"]  # "smart"


def route(text: str, has_filters: bool, exact: bool, active_records: list[FileRecord], vocabulary: dict[str, int]) -> RouteDecision:
    words = _WORD_RE.findall(text.lower())
    features: dict = {"words": len(words), "filters": has_filters}

    if exact:
        return RouteDecision("keyword", 1, "quoted phrase — literal match only", features)
    if not words:
        if has_filters:
            return RouteDecision("metadata", 0, "filters only, no words — list matching files", features)
        return RouteDecision("keyword", 0, "empty", features)

    # A query that names a file needs no models at all. Two words or more:
    # a single word that happens to be in some file's name is a hint, not
    # intent — "sourdough" must still find the recipe whose *content* says
    # sourdough after the guide named after it (found by the live test
    # 2026-09-21), so single words take the keyword tier (names + BM25).
    name_matches = match_filenames(text, active_records)
    if name_matches and len(words) >= 2 and name_matches[0].fraction >= 0.75:
        features["filename_coverage"] = name_matches[0].fraction
        return RouteDecision("filename", 0, f"names a file ({name_matches[0].fraction:.0%} of the words match “{Path(name_matches[0].record.path).name}”)", features)

    content_words = [w for w in words if w not in _STOPWORDS and w not in _QUESTION_WORDS]
    question = any(w in _QUESTION_WORDS for w in words[:2]) or text.strip().endswith("?")
    known = sum(1 for w in content_words if w in vocabulary)
    unknown = len(content_words) - known
    stopword_ratio = 1 - len(content_words) / len(words)
    clauses = len(re.split(r",|;| and | or | but ", text.lower()))
    features.update({"content_words": len(content_words), "unknown_words": unknown, "question": question, "stopword_ratio": round(stopword_ratio, 2), "clauses": clauses})

    # A few plain words the index has seen: BM25 answers this directly.
    if len(words) <= KEYWORD_MAX_WORDS and not question and unknown == 0 and content_words:
        return RouteDecision("keyword", 1, f"{len(words)} known keyword{'s' if len(words) != 1 else ''}, no question — BM25 is enough", features)

    # Everything else is hybrid. Complexity 3 marks the long / multi-part
    # questions (reported, and what the agent is suggested for) but no
    # longer buys the reranker up front — it comes by escalation.
    complex_query = len(content_words) >= 7 or clauses >= 2 or (question and len(content_words) >= 5)
    why = "multi-part question" if clauses >= 2 else "question" if question else "unfamiliar word" if unknown else "natural-language phrase"
    return RouteDecision("hybrid", 3 if complex_query else 2, f"{why} — keyword + meaning, fused (the reranker only if nothing is confident)", features)
