"""Hybrid search pipeline, per the PRD's Search Pipeline section:
query parsing -> BM25 + semantic retrieval -> Reciprocal Rank Fusion ->
file-level aggregation -> enriched result objects (snippet, highlighting,
"why this file"). Cross-encoder reranking is explicitly optional in the
PRD and not implemented yet — `reranker_score` is always None for now,
left as a clear extension point rather than silently omitted.
"""

import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ..embeddings.model import EmbeddingModel
from ..indexing import CHUNKS_TABLE
from ..storage import FileRecordStore, KeywordStore, LanceDBVectorStore
from .explain import explain_match
from .filenames import filename_vocabulary, match_filenames
from .grammar import tidy_query
from .fusion import aggregate_chunks_to_files, reciprocal_rank_fusion
from .query_parsing import parse_query
from .reranker import RERANK_TOP_N, Reranker
from .router import NEXT_TIER, PLANS, Plan, RouteDecision, plan_for_mode, route
from .spelling import correct_query

if TYPE_CHECKING:
    from ..context.profile import ProfileBuilder

RETRIEVAL_TOP_K = 20
# A cross-encoder logit above this means "this passage answers the query";
# below it the reranker has no opinion worth a vote (see _run_plan).
RERANK_CONFIDENT_LOGIT = 0.0
# Fused (RRF) scores live around 0.01–0.04; a promoted chunk scores above
# all of them, and promoted chunks order among themselves by logit.
RERANK_PROMOTION_BASE = 1.0


_KW_TOKEN_RE = re.compile(r"[a-z0-9']+")
# Words that carry no search intent of their own — the framing people put
# around the thing they are actually looking for.
_KW_FILLER = {
    "find", "search", "show", "open", "get", "look", "up", "me", "my", "i", "the", "a", "an", "of", "for", "to", "in", "on", "at", "about",
    "please", "can", "you", "could", "would", "want", "need", "file", "files", "note", "notes", "document", "documents", "where", "what",
    "which", "did", "do", "does", "is", "are", "was", "were", "have", "has", "that", "this", "those", "these", "it", "with", "and", "or",
    "thing", "stuff", "something", "wrote", "write", "put", "saved", "kept", "from", "by", "into", "our", "your", "his", "her", "their",
}
_FILTER_TOKEN_RE = re.compile(r"\b(?:type|ext|after|before|size|in):\S+", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", (text or "").lower()))


def _reorder_duplicates_by_boost(group: list[dict]) -> list[dict]:
    """Within a near-tie group, results whose chunks are near-duplicates of
    each other form clusters; each cluster is ordered by boost, clusters
    keep their score order."""
    if len(group) < 2:
        return group
    token_sets = [_tokens(r.get("chunk_text") or r.get("matched_chunk") or "") for r in group]
    assigned = [-1] * len(group)
    clusters: list[list[int]] = []
    for a in range(len(group)):
        if assigned[a] >= 0:
            continue
        cluster = [a]
        assigned[a] = len(clusters)
        for b in range(a + 1, len(group)):
            if assigned[b] >= 0:
                continue
            union = token_sets[a] | token_sets[b]
            if union and len(token_sets[a] & token_sets[b]) / len(union) >= PERSONAL_DUPLICATE_JACCARD:
                cluster.append(b)
                assigned[b] = assigned[a]
        clusters.append(cluster)
    out: list[dict] = []
    for cluster in clusters:
        members = [group[k] for k in cluster]
        members.sort(key=lambda x: -x["personal"]["boost"])
        out.extend(members)
    return out


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)

# Personalization (Phase 17, Objective 2). A result's boost in [0, 1] is a
# weighted mix of the profile's signals. Since 2026-09-21 it is applied as
# NEAR-TIE BREAKING rather than an additive weight: walking the fused
# results from the top, consecutive results whose fused scores lie within
# PERSONAL_NEAR_TIE of the group's best form one group, and each group is
# ordered by boost. A habit can therefore reorder near-duplicates and
# close calls, but can never leapfrog a clearly better match, and never
# moves a result across the strong/weak tiers (settled first). Filename
# matches keep their coverage order; a habit only breaks a coverage tie.
# Measured (evaluate_personalization.py): neighbouring RRF ranks differ by
# ≈0.00026 per list, so an additive weight small enough to be harmless
# (0.00025) could not even swap two neighbours — the earlier "78% lift"
# came entirely from the filename tie-break. The window below is swept in
# the same script; 0.0006 (two rank steps) is the measured no-harm choice.
PERSONAL_NEAR_TIE = 0.0006
PERSONAL_WEIGHT = 0.0  # kept for the evaluation sweep's older comparison; 0 = near-tie only
# …and only between results whose matched chunks SAY THE SAME THING
# (token Jaccard ≥ this): measured 2026-09-21, breaking every near-tie by
# habit lifted the habitual twin to first 100% of the time but pushed the
# right answer down on 6 of 42 ordinary queries (a daily-used file
# nearly tied with a never-opened one); restricting the swap to
# near-duplicates keeps the lift where it is legitimate and the harm at 0.
PERSONAL_DUPLICATE_JACCARD = 0.5
PERSONAL_MIX = {"frequency": 0.35, "recency": 0.15, "type": 0.15, "topic": 0.2, "time": 0.15}
PERSONAL_REASON_MIN = 0.25  # a "why" chip needs a signal at least this strong

# Semantic search always returns its closest match even when nothing is
# actually relevant. Without a cutoff, a totally unrelated query would
# still get a confident-looking "top result". Since the bge-small swap
# (2026-09-21) these four numbers come from the model's thresholds.json
# (scripts/tune_semantic_thresholds.py, derived from the labelled corpus);
# the constants below are MiniLM's hand-tuned values and the fallback.
SEMANTIC_RELEVANCE_CUTOFF = 1.6

# Confidence tier, mirroring photo search (user request, 2026-09-11: "if it
# shows me a weaker one it should say so"). A result is STRONG if the file
# was named in the query, or every query word is literally present
# (keyword hit), or its meaning distance is close in absolute terms, or it
# sits within a gap of the best meaning match. Anything else that passed
# the cutoff is WEAK — shown dimmed under "possibly related". Measured on
# the voice-note set: correct meaning-only hits landed at 0.71-1.48;
# plausible-but-wrong extras at 1.52-1.57 with the right answer 0.3-1.0
# ahead of them. The 1.45-1.50 band is genuinely ambiguous ("Castle voice
# note" -> the bouncy-castle memo at 1.48 is right; "abhisek plan" -> a
# birthday-party memo at 1.48 is not), so the bar is stricter when a
# literal (keyword/filename) result already answers the query — the
# meaning-only extras are then "possibly related" unless clearly close —
# and slightly looser when meaning is all there is.
SEMANTIC_STRONG_WITH_LITERAL = 1.45
SEMANTIC_STRONG_MEANING_ONLY = 1.50
SEMANTIC_STRONG_MAX_GAP_FROM_BEST = 0.08


class SearchService:
    def __init__(
        self,
        model: EmbeddingModel,
        vector_store: LanceDBVectorStore,
        keyword_store: KeywordStore,
        file_record_store: FileRecordStore,
    ):
        self.model = model
        self.vector_store = vector_store
        self.keyword_store = keyword_store
        self.file_record_store = file_record_store
        # (record-store version, keyword-vocabulary identity) -> derived data
        self._active_cache: tuple[int, list] | None = None
        self._vocabulary_cache: tuple[tuple[int, int], dict[str, int]] | None = None
        # Set by the app once the usage store exists; None = no personalization.
        self.profile_builder: "ProfileBuilder | None" = None
        self.personalize_enabled = lambda: True
        # Optional cross-encoder (Phase 18); None = the rerank stage reports itself unavailable.
        self.reranker: Reranker | None = None
        # Optional learned router (Phase 15 improvement 6); rules when None or untrained.
        self.learned_router = None

    def _first_chunk(self, file_id: str) -> dict:
        """Snippet for a file that matched by name only. Photos and audio
        without a transcript have no chunks — an empty snippet is honest."""
        rows = self.vector_store.get_by_file_id(CHUNKS_TABLE, file_id)
        if not rows:
            return {}
        payload = rows[0].get("payload", {})
        return {"content": payload.get("content", ""), "page_number": payload.get("page_number")}

    def _active_records(self) -> list:
        """Every active file record, reloaded only after the store changed.
        Until 2026-09-21 each search re-read the whole table twice and
        rebuilt the filename vocabulary from it."""
        version = self.file_record_store.version
        if self._active_cache is None or self._active_cache[0] != version:
            self._active_cache = (version, self.file_record_store.list_active())
        return self._active_cache[1]

    def _correction_vocabulary(self, active_records) -> dict[str, int]:
        content_vocabulary = self.keyword_store.vocabulary()  # cached by the store until chunks change
        key = (self.file_record_store.version, self.keyword_store.version)
        if self._vocabulary_cache is not None and self._vocabulary_cache[0] == key:
            return self._vocabulary_cache[1]
        vocabulary = dict(content_vocabulary)  # copy: the store caches this dict
        for word, count in filename_vocabulary(active_records).items():
            vocabulary[word] = vocabulary.get(word, 0) + count
        self._vocabulary_cache = (key, vocabulary)
        return vocabulary

    def suggest(self, raw_query: str) -> str | None:
        """What `search()` would quietly correct this query to (spelling via
        the user's own vocabulary, then rule-based tidying), or None when it
        would run as typed. Quoted exact phrases are never touched, and a
        `type:` filter is carried over untouched."""
        parsed = parse_query(raw_query)
        if parsed.exact_phrase is not None or not parsed.text:
            return None
        corrected = correct_query(parsed.text, self._correction_vocabulary(self._active_records()))
        corrected = tidy_query(corrected)
        if corrected == parsed.text:
            return None
        # Every filter the user typed rides along unchanged (until 2026-09-21
        # only `type:` did — an `after:2025 in:invoices` query lost them when
        # the chip was clicked).
        filters = " ".join(m.group(0) for m in _FILTER_TOKEN_RE.finditer(raw_query))
        return f"{filters} {corrected}".strip()

    def search(self, raw_query: str, top_k: int = 10, mode: str = "auto") -> list[dict]:
        """Results only — see search_routed() for the route report."""
        return self.search_routed(raw_query, top_k=top_k, mode=mode)[0]

    def search_routed(self, raw_query: str, top_k: int = 10, mode: str = "auto") -> tuple[list[dict], dict]:
        """`mode`: auto (the router picks the cheapest sufficient tier —
        Phase 18, Objective 3), smart (BM25 + meaning + fusion), exact (the
        whole query as a literal phrase — same as quoting it), keyword
        (BM25 only, no meaning path). The last three are the PRD's manual
        Search Modes and skip the router. Returns (results, route) where
        route says which tier ran, why, whether it escalated, and what
        each stage cost."""
        t_start = time.perf_counter()
        parsed = parse_query(raw_query)
        exact = parsed.exact_phrase is not None or mode == "exact"
        query_text = parsed.exact_phrase if parsed.exact_phrase is not None else parsed.text
        stages: list[dict] = []
        if not query_text and not parsed.filters.any():
            return [], self._route_report("keyword", "keyword", 0, "empty query", {}, False, stages, t_start, None)

        # Only files that pass the metadata filters take part in any stage.
        active_records = self._active_records()
        if parsed.filters.any():
            active_records = [r for r in active_records if parsed.filters.matches(r.path, r.size, r.modified_time)]

        # Typo correction is built against the vocabulary of words the
        # user's own files actually contain. Exact-phrase search must
        # match literally, so it's deliberately excluded.
        #
        # It turns out this needs to feed BOTH the keyword AND semantic
        # paths, not just keyword: testing showed a short query with a
        # few misspelled words (e.g. "horizantal scalling for trafic
        # spikes") can score HIGHER semantic similarity against a
        # completely unrelated file than the correct one, because the
        # embedding model turns garbled words into meaningless subword
        # fragments. Correcting first fixed it decisively in testing
        # (similarity to the right file went from 0.08 to 0.58).
        keyword_query_text = query_text
        vocabulary: dict[str, int] = {}
        if query_text and not exact:
            t0 = time.perf_counter()
            vocabulary = self._correction_vocabulary(self._active_records())
            # Same two steps as suggest(): until 2026-09-21 search skipped
            # tidy_query, so the chip could offer "bread, making" while
            # search itself would never have made that change.
            keyword_query_text = tidy_query(correct_query(query_text, vocabulary))
            stages.append({"stage": "understand", "ms": _ms(t0)})

        if mode == "auto":
            t0 = time.perf_counter()
            vocab = vocabulary or self._correction_vocabulary(self._active_records())
            if self.learned_router is not None and self.learned_router.available:
                decision = self.learned_router.decide(keyword_query_text, parsed.filters.any(), exact, active_records, vocab)
            else:
                decision = route(keyword_query_text, parsed.filters.any(), exact, active_records, vocab)
            stages.append({"stage": "route", "ms": _ms(t0), "learned": bool(decision.features.get("learned"))})
            plan = decision.plan
        else:
            plan = plan_for_mode(mode)
            decision = RouteDecision(plan.tier, -1, f"manual mode: {mode}", {})
        if exact:
            plan = Plan(plan.tier, names=False, keyword=True, exact=True)
        if not query_text and parsed.filters.any():
            plan = PLANS["metadata"]  # filters alone list files in every mode, manual ones included

        requested_tier = plan.tier
        if plan.tier == "metadata":
            results = self._metadata_results(parsed, active_records, top_k, stages)
        else:
            results = self._run_plan(plan, query_text, keyword_query_text, parsed, active_records, top_k, stages)

        # Escalate once when the cheap route came back empty or unsure.
        escalated = False
        if mode == "auto" and not exact and plan.tier in NEXT_TIER and not any(r["confidence"] == "strong" for r in results):
            plan = PLANS[NEXT_TIER[plan.tier]]
            stages.append({"stage": "escalate", "to": plan.tier})
            results = self._run_plan(plan, query_text, keyword_query_text, parsed, active_records, top_k, stages)
            escalated = True

        results = self._personalize(results)
        report = self._route_report(plan.tier, requested_tier, decision.complexity, decision.reason, decision.features, escalated, stages, t_start,
                                    keyword_query_text if keyword_query_text != query_text else None)
        # The tier above hybrid+rerank is the agent (Phase 19): suggested,
        # not run — it costs seconds, and the user decides whether a
        # question deserves them. Suggested for questions the retrieval
        # tiers could not answer confidently.
        report["suggest_ask"] = bool(decision.features.get("question")) and (not results or all(r["confidence"] != "strong" for r in results))
        return results, report

    @staticmethod
    def _route_report(tier, requested, complexity, reason, features, escalated, stages, t_start, corrected) -> dict:
        return {
            "tier": tier,
            "requested_tier": requested,
            "escalated": escalated,
            "complexity": complexity,
            "reason": reason,
            "features": features,
            "stages": stages,
            "total_ms": _ms(t_start),
            "corrected_query": corrected,
        }

    def _metadata_results(self, parsed, active_records, top_k: int, stages: list[dict]) -> list[dict]:
        """Filters only, no words: the matching files, newest first."""
        t0 = time.perf_counter()
        results = []
        for record in sorted(active_records, key=lambda r: -r.modified_time)[:top_k]:
            if not Path(record.path).exists():
                continue
            first = self._first_chunk(record.file_id)
            results.append(self._result(record, None, first, [f"Matches filters: {', '.join(parsed.filters.describe())}"], "strong"))
        stages.append({"stage": "metadata", "ms": _ms(t0), "files": len(results)})
        return results

    def _result(self, record, agg, best_chunk: dict, why: list[str], confidence: str) -> dict:
        return {
            "file_id": record.file_id,
            "path": record.path,
            "filename": Path(record.path).name,
            "score": agg.file_score if agg else None,
            "size": record.size,
            "modified_time": record.modified_time,
            "keyword_score": best_chunk.get("keyword_score"),
            "semantic_score": best_chunk.get("semantic_score"),
            "reranker_score": best_chunk.get("reranker_score"),
            "page": best_chunk.get("page_number"),
            "matched_chunk": best_chunk.get("highlighted") or best_chunk.get("content", ""),
            "chunk_text": best_chunk.get("content", ""),  # the whole chunk, for the agent (the snippet is a 20-token window)
            "why": why,
            "confidence": confidence,
        }

    def _run_plan(self, plan: Plan, query_text: str, keyword_query_text: str, parsed, active_records, top_k: int, stages: list[dict]) -> list[dict]:
        allowed_file_ids = {r.file_id for r in active_records} if parsed.filters.any() else None

        keyword_hits: list[dict] = []
        if plan.keyword and keyword_query_text:
            t0 = time.perf_counter()
            keyword_hits = self.keyword_store.search(keyword_query_text, top_k=RETRIEVAL_TOP_K, exact_phrase=plan.exact)
            relaxed = False
            if not keyword_hits and not plan.exact:
                # FTS5 ANDs every word, so the filler in "find my notes about
                # horizontal scaling" left no row. Retry on the content words
                # alone, requiring at least half of them (2026-09-21).
                content_words = [w for w in _KW_TOKEN_RE.findall(keyword_query_text.lower()) if w not in _KW_FILLER and len(w) > 1]
                if content_words and len(content_words) < len(_KW_TOKEN_RE.findall(keyword_query_text)):
                    keyword_hits = self.keyword_store.search_terms(content_words, top_k=RETRIEVAL_TOP_K)
                    relaxed = bool(keyword_hits)
            if allowed_file_ids is not None:
                keyword_hits = [h for h in keyword_hits if h["file_id"] in allowed_file_ids]
            stages.append({"stage": "keyword", "ms": _ms(t0), "hits": len(keyword_hits), **({"relaxed": True} if relaxed else {})})
        else:
            stages.append({"stage": "keyword", "skipped": True})

        semantic_hits: list[dict] = []
        if plan.semantic and keyword_query_text:
            t0 = time.perf_counter()
            query_vector = self.model.embed_queries([keyword_query_text])[0].tolist()
            raw_semantic = self.vector_store.query(CHUNKS_TABLE, query_vector, top_k=RETRIEVAL_TOP_K)
            semantic_hits = [r for r in raw_semantic if r["score"] <= self.model.thresholds["relevance_cutoff"]]
            if allowed_file_ids is not None:
                semantic_hits = [h for h in semantic_hits if h["file_id"] in allowed_file_ids]
            stages.append({"stage": "semantic", "ms": _ms(t0), "hits": len(semantic_hits)})
        else:
            stages.append({"stage": "semantic", "skipped": True})

        # Filename matches are ranked ABOVE content matches: when the user
        # names the file, that is the file — a content hit on the word
        # "plan" in some other document must not outrank `abhisek plan.txt`.
        # See filenames.py for the live-test failure that motivated this.
        name_matches = []
        if plan.names and query_text:
            t0 = time.perf_counter()
            name_matches = match_filenames(keyword_query_text, active_records)
            if keyword_query_text != query_text:
                # The spelling corrector only knows words inside file *content*;
                # a name like "abhisek" may exist only as a filename, so try the
                # user's raw words too.
                seen = {m.record.file_id for m in name_matches}
                name_matches += [m for m in match_filenames(query_text, active_records) if m.record.file_id not in seen]
            stages.append({"stage": "names", "ms": _ms(t0), "hits": len(name_matches)})
        else:
            stages.append({"stage": "names", "skipped": True})

        if not keyword_hits and not semantic_hits and not name_matches:
            return []

        chunk_info: dict[str, dict] = {}
        for h in keyword_hits:
            info = chunk_info.setdefault(h["chunk_id"], {"file_id": h["file_id"]})
            info["content"] = h["content"]
            info["highlighted"] = h["highlighted"]
            info["keyword_score"] = h["score"]
        for h in semantic_hits:
            info = chunk_info.setdefault(h["id"], {"file_id": h["file_id"]})
            payload = h["payload"]
            info.setdefault("content", payload.get("content", ""))
            info["semantic_score"] = h["score"]
            info["page_number"] = payload.get("page_number")
            info["heading"] = payload.get("heading")
            info["section"] = payload.get("section")

        # A chunk BM25 found but the meaning path did not has no page /
        # heading / section yet — the keyword table stores none — so its
        # result said nothing about where in the file it was. Fetch that
        # from the vector store's payload for exactly those chunks.
        missing_meta = [cid for cid, info in chunk_info.items() if "page_number" not in info]
        for cid, payload in self.vector_store.get_payloads(CHUNKS_TABLE, missing_meta).items():
            info = chunk_info[cid]
            info["page_number"] = payload.get("page_number")
            info["heading"] = payload.get("heading")
            info["section"] = payload.get("section")

        t0 = time.perf_counter()
        keyword_ranked_ids = [h["chunk_id"] for h in keyword_hits]
        semantic_ranked_ids = [h["id"] for h in semantic_hits]
        ranked_lists = [keyword_ranked_ids, semantic_ranked_ids]
        fused_scores = reciprocal_rank_fusion(ranked_lists)

        if plan.rerank and self.reranker is not None and chunk_info:
            # The cross-encoder re-reads the top fused candidates with the
            # query. Candidates it judges relevant (logit > 0) are PROMOTED
            # above the rest, ordered by its logit; everything else keeps
            # the fused order. Measured 2026-09-21 (prototype_router.py):
            #  - as a mere third vote in RRF it was outvoted 2–1 by BM25 +
            #    embedding exactly on the queries it exists for (a note
            #    that *echoes the question* beat the passage that answers
            #    it, although the reranker scored the answer 8.9 vs 3.8);
            #  - on this project's signature concept match ("traffic
            #    spikes" vs "horizontal scaling when demand increases") it
            #    judged the right passage NOT relevant (logit < 0), so it
            #    must never demote — only promote what it is sure about.
            t1 = time.perf_counter()
            candidates = [cid for cid, _ in sorted(fused_scores.items(), key=lambda kv: -kv[1])[:RERANK_TOP_N]]
            scores = self.reranker.score(query_text, [chunk_info[cid].get("content", "") for cid in candidates])
            promoted = 0
            for cid, score in zip(candidates, scores):
                chunk_info[cid]["reranker_score"] = score
                if score > RERANK_CONFIDENT_LOGIT:
                    fused_scores[cid] = RERANK_PROMOTION_BASE + score / 1000.0
                    promoted += 1
            stages.append({"stage": "rerank", "ms": _ms(t1), "candidates": len(candidates), "relevant": promoted})
        elif plan.rerank:
            stages.append({"stage": "rerank", "skipped": True, "reason": "reranker model not installed"})
        else:
            stages.append({"stage": "rerank", "skipped": True})

        chunk_id_to_file_id = {cid: info["file_id"] for cid, info in chunk_info.items()}
        file_aggregates = aggregate_chunks_to_files(fused_scores, chunk_id_to_file_id)
        stages.append({"stage": "fusion", "ms": _ms(t0)})

        results = []
        file_score = {agg.file_id: agg for agg in file_aggregates}
        for match in name_matches:
            if len(results) >= top_k:
                break
            record = match.record
            if not Path(record.path).exists():
                self.file_record_store.mark_deleted(record.path)
                continue
            agg = file_score.get(record.file_id)
            best_chunk = chunk_info[agg.best_chunk_id] if agg else self._first_chunk(record.file_id)
            why = [f"Filename contains: {', '.join(match.matched_words)}"] + ([] if not agg else explain_match(keyword_query_text, best_chunk, record.path))
            result = self._result(record, agg, best_chunk, why, "strong")
            result["name_coverage"] = match.fraction
            results.append(result)
        already = {r["file_id"] for r in results}

        for agg in file_aggregates:
            if len(results) >= top_k:
                break
            if agg.file_id in already:
                continue
            best_chunk = chunk_info[agg.best_chunk_id]
            record = self.file_record_store.get_by_file_id(agg.file_id)
            # Never surface a file that's gone. Found by a live test
            # (2026-09-11): renamed files came back with their old path and
            # "No such file or directory" on click. The folder scan and the
            # watcher reconcile the index, but between those a result must
            # still be honest — tombstone it here so cleanup purges it.
            if record is None or record.deleted:
                continue
            if not Path(record.path).exists():
                self.file_record_store.mark_deleted(record.path)
                continue
            why = explain_match(keyword_query_text, best_chunk, record.path)
            if best_chunk.get("reranker_score", None) is not None and best_chunk["reranker_score"] > RERANK_CONFIDENT_LOGIT:
                why.append("Re-read by the reranker: relevant")
            results.append(self._result(record, agg, best_chunk, why, "strong"))  # tier settled below

        def literal(r: dict) -> bool:
            return r["keyword_score"] is not None or r["why"][0].startswith("Filename contains")

        # The gap is measured against the best *meaning-only* result. When the
        # top result matched literally (keyword/filename) its own meaning
        # distance may be poor, and "within 0.08 of that" would promote junk.
        meaning_only = [r["semantic_score"] for r in results if not literal(r) and r["semantic_score"] is not None]
        best_semantic = min(meaning_only) if meaning_only else None
        th = self.model.thresholds
        threshold = th["strong_with_literal"] if any(literal(r) for r in results) else th["strong_meaning_only"]
        best_is_strong = best_semantic is not None and best_semantic <= threshold
        for r in results:
            if literal(r) or r["semantic_score"] is None:
                continue
            distance = r["semantic_score"]
            close = distance <= threshold or (best_is_strong and distance <= best_semantic + th["strong_max_gap_from_best"])
            r["confidence"] = "strong" if close else "weak"
        return results

    def _personalize(self, results: list[dict]) -> list[dict]:
        """Objective 2: re-order within each tier by fused score plus the
        profile's boost, and say why. No profile, switched off, or cold
        start → results unchanged, `personal` = None on every result."""
        builder = self.profile_builder
        if builder is None or not self.personalize_enabled():
            for r in results:
                r["personal"] = None
            return results
        profile = builder.get()
        if profile.cold_start:
            for r in results:
                r["personal"] = None
            return results
        for r in results:
            fid = r["file_id"]
            topic_sim, topic_label = profile.topic_affinity(builder.file_vector(fid))
            signals = {
                "frequency": profile.frequency(fid),
                "recency": profile.recency(fid),
                "type": profile.type_preference(r["path"] or ""),
                "topic": topic_sim,
                "time": profile.time_affinity(fid),
            }
            boost = sum(PERSONAL_MIX[k] * v for k, v in signals.items())
            # (strength, text) — the card shows the two strongest so the
            # chips stay readable; `personal.reasons` keeps them all.
            reasons: list[tuple[float, str]] = []
            if signals["frequency"] >= PERSONAL_REASON_MIN and signals["recency"] >= PERSONAL_REASON_MIN:
                reasons.append((signals["frequency"], "You use this often"))
            elif signals["frequency"] >= PERSONAL_REASON_MIN:
                reasons.append((signals["frequency"] * 0.8, "You used to open this a lot"))
            if signals["time"] >= PERSONAL_REASON_MIN:
                reasons.append((signals["time"], "Your usual around this time"))
            if signals["topic"] >= 0.5 and topic_label:
                reasons.append((signals["topic"], f"Your usual topic: {topic_label}"))
            if signals["type"] >= 0.6:
                reasons.append((signals["type"] * 0.5, f"Your most-used file type ({(r['path'] or '').rsplit('.', 1)[-1].lower()})"))
            reasons.sort(key=lambda item: -item[0])
            r["personal"] = {"boost": round(boost, 4), "signals": {k: round(v, 3) for k, v in signals.items()}, "reasons": [t for _, t in reasons]}
            r["why"] = r["why"] + [t for _, t in reasons[:2]]
        # Filename matches stay first in their own order; strong stays above
        # weak; only the order inside the two fused bands changes.
        def band(r: dict) -> int:
            return 0 if r["why"][0].startswith("Filename contains") else 1 if r["confidence"] == "strong" else 2

        def key(item: tuple[int, dict]) -> tuple:
            index, r = item
            if band(r) == 0:
                # Coverage first; habit only breaks a tie between names the
                # query covers equally ("gym plan.txt" vs "gym plan (old).txt").
                return (0, -r.get("name_coverage", 0.0), -r["personal"]["boost"], index)
            base = r["score"] if r["score"] is not None else 0.0
            return (band(r), -(base + PERSONAL_WEIGHT * r["personal"]["boost"]), index)

        ordered = [r for _, r in sorted(enumerate(results), key=key)]
        # Near-tie breaking within the two fused bands.
        out: list[dict] = []
        i = 0
        while i < len(ordered):
            r = ordered[i]
            if band(r) == 0:
                out.append(r)
                i += 1
                continue
            top = r["score"] or 0.0
            j = i
            while j < len(ordered) and band(ordered[j]) == band(r) and (top - (ordered[j]["score"] or 0.0)) <= PERSONAL_NEAR_TIE:
                j += 1
            group = ordered[i:j]
            out.extend(_reorder_duplicates_by_boost(group))
            i = j
        return out
