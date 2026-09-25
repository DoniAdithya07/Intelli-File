"""Derives a short "why this file?" explanation from the matched chunk's
own content/metadata — no LLM needed, per the PRD's Why This File? section.
"""

import re

_WORD_RE = re.compile(r"\w+")

# Common words that would technically "match" but explain nothing about
# why a result is relevant — excluded so "why" reasons stay meaningful.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be",
    "been", "being", "to", "of", "in", "on", "at", "by", "for", "with",
    "about", "as", "into", "like", "through", "after", "over", "between",
    "out", "against", "during", "without", "before", "under", "around",
    "among", "this", "that", "these", "those", "it", "its", "i", "you",
    "he", "she", "we", "they", "them", "his", "her", "their", "our", "your",
    "do", "does", "did", "doing", "have", "has", "had", "having", "can",
    "could", "will", "would", "should", "what", "which", "who", "whom",
    "how", "not", "no", "so", "than", "too", "very",
}


def explain_match(query_text: str, chunk_info: dict, path: str | None = None) -> list[str]:
    query_terms = {t.lower() for t in _WORD_RE.findall(query_text) if len(t) > 2} - _STOPWORDS
    content_words = set(_WORD_RE.findall(chunk_info.get("content", "").lower()))
    matched_terms = sorted(query_terms & content_words)

    reasons = []
    # A chunk only has keyword_score set if it was actually retrieved via
    # BM25 — which, given FTS5's default AND-between-terms behavior, means
    # every non-stopword query term is genuinely present, not just one
    # coincidentally-shared word. Without keyword_score, this chunk came
    # from semantic search alone, so leading with "Contains: X" would be
    # misleading even if X happens to appear here too.
    if matched_terms and chunk_info.get("keyword_score") is not None:
        reasons.append(f"Contains: {', '.join(matched_terms)}")
    elif chunk_info.get("semantic_score") is not None:
        reasons.append("Matched based on meaning similarity, not exact wording")

    if chunk_info.get("section"):
        reasons.append(f"Found under section: {chunk_info['section']}")
    if chunk_info.get("heading"):
        reasons.append(f"Found under heading: {chunk_info['heading']}")
    if chunk_info.get("page_number"):
        # PPTX blocks reuse page_number for the slide index (2026-09-20).
        unit = "slide" if path and path.lower().endswith(".pptx") else "page"
        reasons.append(f"Found on {unit} {chunk_info['page_number']}")

    if not reasons:
        reasons.append("Relevant content found in this file")

    return reasons
