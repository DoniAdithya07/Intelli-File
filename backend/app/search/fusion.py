"""Reciprocal Rank Fusion + chunk-to-file aggregation, per the PRD's
Result Fusion and File-Level Result Aggregation sections.
"""

from dataclasses import dataclass

DEFAULT_RRF_K = 60
# A second relevant chunk in the same file is a tie-breaker, nothing more.
# RRF scores are nearly flat (rank 1 = 1/61 = 0.0164, rank 10 = 0.0143), so
# at the old weight of 0.2 a long document with nine mediocre chunks in
# the top-20 collected a boost LARGER than a rank-1 score and outranked a
# short file whose single chunk was the best match by far (found live,
# 2026-09-11: "packing list for the trek" put PROJECT_SUMMARY.md above the
# voice note that literally says it). 0.01 × at most two extra chunks
# (≈ 0.0003) is smaller than one rank step at the top of the list, so it
# can only separate files whose best chunks tied.
SECONDARY_CHUNK_WEIGHT = 0.01
MAX_SECONDARY_CHUNKS = 2


def reciprocal_rank_fusion(ranked_id_lists: list[list[str]], k: int = DEFAULT_RRF_K) -> dict[str, float]:
    """Each input is an already-ranked list of ids (best first). Returns a
    fused score per id — higher is better, unlike the raw distance/bm25
    scores that fed into it."""
    scores: dict[str, float] = {}
    for ranked_ids in ranked_id_lists:
        for rank, item_id in enumerate(ranked_ids, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return scores


@dataclass(frozen=True)
class FileAggregate:
    file_id: str
    file_score: float
    best_chunk_id: str


def aggregate_chunks_to_files(chunk_scores: dict[str, float], chunk_id_to_file_id: dict[str, str]) -> list[FileAggregate]:
    """Top chunk score + a smaller contribution from other relevant chunks
    in the same file — rather than averaging, so one highly relevant
    section isn't diluted by the rest of a long document."""
    per_file_chunk_scores: dict[str, list[tuple[str, float]]] = {}
    for chunk_id, score in chunk_scores.items():
        file_id = chunk_id_to_file_id.get(chunk_id)
        if file_id is None:
            continue
        per_file_chunk_scores.setdefault(file_id, []).append((chunk_id, score))

    aggregates = []
    for file_id, chunk_score_pairs in per_file_chunk_scores.items():
        chunk_score_pairs.sort(key=lambda pair: pair[1], reverse=True)
        best_chunk_id, best_score = chunk_score_pairs[0]
        secondary_total = sum(score for _, score in chunk_score_pairs[1 : 1 + MAX_SECONDARY_CHUNKS])
        file_score = best_score + SECONDARY_CHUNK_WEIGHT * secondary_total
        aggregates.append(FileAggregate(file_id=file_id, file_score=file_score, best_chunk_id=best_chunk_id))

    aggregates.sort(key=lambda a: a.file_score, reverse=True)
    return aggregates
