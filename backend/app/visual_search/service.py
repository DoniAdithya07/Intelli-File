"""Visual search: typed query -> CLIP text embedding -> nearest photo
embeddings, per the PRD's Phase 8 "text query -> embed with matching text
encoder -> search against image/video vectors".

Kept separate from search/service.py on purpose. That pipeline is hybrid
BM25 + semantic + RRF fusion with snippets, keyword highlighting and a
"why this file" explanation built from matched terms — none of which has
meaning for a photo. There's no text to match, no snippet to show, no
term to highlight. Forcing photos through it would mean special-casing
almost every step, so this is a plain vector lookup instead.
"""

from pathlib import Path

import numpy as np

from ..embeddings.clip_model import ClipModel
from ..indexing.visual_indexer import IMAGES_TABLE, MAX_VIDEO_KEYFRAMES
from ..search.dictionary import Dictionary, QueryCheck
from ..search.filenames import filename_vocabulary
from ..storage import FileRecordStore, KeywordStore, LanceDBVectorStore

# LanceDB always hands back its nearest neighbours however unrelated they
# are — the same trap the text path hit (see SEMANTIC_RELEVANCE_CUTOFF in
# search/service.py), where an irrelevant query still produced a
# confident-looking top result.
#
# The score here is SQUARED L2 distance (verified empirically, not
# assumed). Both towers emit L2-normalized vectors, so it maps directly
# to cosine similarity: d^2 = 2 * (1 - cos). Measured against real CLIP
# output on generated fixtures: a correct match lands at 1.35-1.40
# (cos ~0.30-0.33), the same query against the wrong image at 1.50-1.51,
# and an unrelated query's best hit at ~1.48.
#
# Re-derived from REAL photographs (2026-09-11 live test: 20 phone
# photos from a birthday, 3 illustrated character cards, a logo, a QR
# code, 3 camera-calibration checkerboards). The synthetic-fixture
# assumption — "real photos carry more detail so they separate more
# cleanly" — turned out backwards: real scenes sit further from their
# query than flat shapes do. Measured squared-L2 for correct matches:
#   checkerboard 1.35, QR code 1.41, character card 1.46,
#   "birthday cake with candles" 1.49, "birthday party" 1.50-1.53,
#   "a group of friends" 1.51-1.54, "university logo" 1.54.
# Unrelated queries' best hits: "a dog running on the beach" 1.51,
# "car" 1.54, "cat" 1.54, "mountain landscape" 1.58.
#
# So correct and unrelated overlap around 1.51-1.54 and no absolute
# number separates them perfectly. 1.5 dropped every group photo; the
# first re-tune to 1.55 fixed that but let colour-coincidences through
# ("a person in red shirt" -> the person, plus a red pizza).
#
# Re-measured (2026-09-11) on a LABELLED set: the 127 Wikimedia photos
# from scripts/download_demo_photos.py, named by subject, queried both by
# full subject phrase and by its last word (people type "castle", not
# "old castle"), precision/recall over all of them
# (scripts/evaluate_visual_cutoff.py — re-run after any model change).
# Final configuration: ViT-B/16, fp16 weights, CPU, one image per run.
#
# A result is STRONG when it is either very close in absolute terms, or
# within a gap of the best hit while under a looser absolute bound. The
# user's "Castle" query showed why the gap matters: the two real castles
# scored 1.44/1.49, an aerial photo 1.529 — under any absolute bound loose
# enough to keep the third castle elsewhere, yet clearly a different
# league from the top hits. The tight absolute clause exists so a photo
# that plainly matches is never hidden just because another photo matches
# even better ("a cat in the snow": one cat at 1.375, the others at 1.51).
# Re-swept after query templating (embed_query) pulled every correct
# match ~0.02 closer:
#   strong rule                              precision  recall   F1
#   abs<=1.51 OR (abs<=1.55 & gap<=0.08)       0.66      0.86   0.75  (pre-templating choice)
#   abs<=1.51 OR (abs<=1.53 & gap<=0.08)       0.70      0.85   0.77  <- chosen
#   abs<=1.51 OR (abs<=1.55 & gap<=0.06)       0.71      0.82   0.76
# Prompt templating ("a photo of …" averaged) and subtracting a generic
# "a photo" baseline were both tested and did not beat this.
# Still a measured heuristic on one collection; Phase 13 formalises it.
# 2026-09-19: the gap clause now applies only when the best hit is itself
# confident (<= VISUAL_ALWAYS_STRONG_BOUND). It exists so a runner-up near a
# confident best isn't hidden — but a lone mediocre best hit (user's "one
# piece" -> a cake at 1.529) is trivially within the gap of *itself* and
# was promoted to strong. Re-measured on the 37 subjects + bare words:
# precision 0.70 -> 0.72, recall 0.84 unchanged.
VISUAL_ALWAYS_STRONG_BOUND = 1.51
VISUAL_RELEVANCE_CUTOFF = 1.53
VISUAL_STRONG_MAX_GAP_FROM_BEST = 0.08

# Hits that miss the strong rule but are within these looser bounds are
# returned flagged confidence="weak", so the UI shows them dimmed under a
# "possibly related" divider. On the labelled set the band beyond the
# strong tier is mostly noise (14 correct vs 81 junk with no gap), so the
# relative gap matters here too. Beyond these they are dropped outright.
VISUAL_WEAK_BOUND = 1.55
VISUAL_WEAK_MAX_GAP_FROM_BEST = 0.10

# Video results carry a filmstrip of their best-matching moments (user's
# suggestion, 2026-09-19: "two or three photos to confirm instead of the
# whole video"). Moments closer than the gap are the same scene.
VIDEO_PREVIEW_MOMENTS = 3
VIDEO_PREVIEW_MIN_GAP_SECONDS = 3.0

# A video's score is the BEST of up to 120 keyframes, so it is biased low
# compared with a single photo: with the photo bounds, a clip has 120
# chances for one generic frame (all steam, all smoke, all blur) to land
# under the line. Measured 2026-09-19 on 7 real clips: every genuine match
# scored <= 1.43, while a steam-only frame of the train clip scored 1.494
# for "a cat" — strong under the photo rule. Video hits therefore use a
# tighter absolute line and a tighter gap. Provisional (7 clips); Phase 13
# should re-measure with more footage.
VIDEO_ALWAYS_STRONG_BOUND = 1.48
VIDEO_STRONG_MAX_GAP_FROM_BEST = 0.05


# CLIP was trained on image *captions*, so a bare word is a poor query: for
# "night" the text embedding was nearly uninformative — every photo scored a
# flat ~0.225 and the real night photos ranked 8th to 85th behind a sunlit
# cat (2026-09-11, user's live test; not a brightness bias — similarity
# correlated only +0.17 with darkness, and no pixel-level code exists in
# this path). Phrasing the same word as a caption fixed it. Measured on the
# demo folder with eye-verified ground truth:
#   query form                 bare words acc@1   descriptive phrases acc@1
#   plain                          14/18                 20/20
#   "a photo of {q}"               16/18                 20/20
#   avg(plain, "a photo of {q}")   16/18                 20/20   <- used
# The average is kept rather than the template alone so a query that is
# already a caption ("a photo taken at night") isn't double-wrapped.
QUERY_TEMPLATES = ("{}", "a photo of {}")


def embed_query(clip_model: ClipModel, query: str) -> np.ndarray:
    """Unit-length text embedding for a search query, averaged over
    QUERY_TEMPLATES. Every script that measures thresholds must embed
    queries through this function, or its numbers won't match the app."""
    vectors = clip_model.embed_texts([t.format(query) for t in QUERY_TEMPLATES])
    mean = vectors.mean(axis=0)
    return mean / max(float(np.linalg.norm(mean)), 1e-9)


class VisualSearchService:
    def __init__(
        self,
        clip_model: ClipModel,
        vector_store: LanceDBVectorStore,
        file_record_store: FileRecordStore,
        dictionary: Dictionary | None = None,
        keyword_store: KeywordStore | None = None,
    ):
        self.clip_model = clip_model
        self.vector_store = vector_store
        self.file_record_store = file_record_store
        # Without the word list (data/english_words.txt missing) queries run
        # as typed, exactly as before it existed.
        self.dictionary = dictionary
        self.keyword_store = keyword_store

    def check_query(self, raw_query: str) -> QueryCheck:
        """Spelling check against the English word list plus the user's own
        file-name/text vocabulary — see search/dictionary.py for why photo
        queries need this when text queries don't."""
        text = raw_query.strip()
        if self.dictionary is None or not text:
            return QueryCheck(text=text, corrected={}, unrecognized=[])
        vocabulary = dict(self.keyword_store.vocabulary()) if self.keyword_store else {}
        for word, count in filename_vocabulary(self.file_record_store.list_active()).items():
            vocabulary[word] = vocabulary.get(word, 0) + count
        return self.dictionary.check(text, vocabulary)

    def suggest(self, raw_query: str) -> str | None:
        """The corrected query for the Did-you-mean chip, or None if it runs as typed."""
        check = self.check_query(raw_query)
        return check.text if check.corrected else None

    def search(self, raw_query: str, top_k: int = 10, apply_cutoff: bool = True, kind: str | None = None) -> list[dict]:
        """Photos and videos matching a described query, best first.
        `kind` = "photo" | "video" restricts to one (the page's filter
        chips); None searches both. `apply_cutoff` exists so the
        prototype/evaluation scripts can inspect raw distances for every
        candidate rather than only survivors."""
        query = raw_query.strip()
        if not query:
            return []

        query_vector = embed_query(self.clip_model, query).tolist()
        # A video contributes one vector per keyframe (Phase 8b), so more
        # candidates are fetched than files wanted, then collapsed to the
        # best moment per file — otherwise a 120-frame clip would fill the
        # grid with near-identical frames and crowd out every photo.
        raw_hits = self.vector_store.query(IMAGES_TABLE, query_vector, top_k=top_k * (MAX_VIDEO_KEYFRAMES // 4))
        hits: list[dict] = []
        moments: dict[str, list[dict]] = {}  # file_id -> up to VIDEO_PREVIEW_MOMENTS distinct moments
        for hit in raw_hits:  # already best-first
            if kind is not None and hit["payload"].get("kind") != kind:
                continue
            file_id = hit["file_id"]
            if file_id not in moments:
                if len(hits) >= top_k:
                    continue
                hits.append(hit)
                moments[file_id] = []
            t = hit["payload"].get("timestamp_offset_seconds")
            if t is None or len(moments[file_id]) >= VIDEO_PREVIEW_MOMENTS:
                continue
            # Keep only moments that are visibly different scenes (not the
            # same second twice) AND match nearly as well as the best one —
            # the filmstrip is there to confirm the hit, so a slot must never
            # be filled with a frame that doesn't match (the generated
            # red-then-blue clip put its blue half in slot 3 before this).
            first = moments[file_id][0]["score"] if moments[file_id] else hit["score"]
            if hit["score"] is not None and first is not None and hit["score"] > first + VISUAL_WEAK_MAX_GAP_FROM_BEST:
                continue
            if all(abs(t - m["t"]) >= VIDEO_PREVIEW_MIN_GAP_SECONDS for m in moments[file_id]):
                moments[file_id].append({"t": t, "score": hit["score"]})

        results = []
        best_score = min((h["score"] for h in hits if h["score"] is not None), default=None)
        for hit in hits:
            score = hit["score"]
            is_video = hit["payload"].get("kind") == "video"
            always_strong = VIDEO_ALWAYS_STRONG_BOUND if is_video else VISUAL_ALWAYS_STRONG_BOUND
            max_gap = VIDEO_STRONG_MAX_GAP_FROM_BEST if is_video else VISUAL_STRONG_MAX_GAP_FROM_BEST
            strong = (
                score is None
                or score <= always_strong
                or (
                    score <= VISUAL_RELEVANCE_CUTOFF
                    and best_score is not None
                    and best_score <= VISUAL_ALWAYS_STRONG_BOUND
                    and score <= best_score + max_gap
                )
            )
            if apply_cutoff and score is not None and not strong:
                if score > VISUAL_WEAK_BOUND or (best_score is not None and score > best_score + VISUAL_WEAK_MAX_GAP_FROM_BEST):
                    continue
            payload = hit["payload"]
            record = self.file_record_store.get_by_file_id(hit["file_id"])
            # A tombstoned file must not surface, matching the text path's
            # behaviour where deleted files disappear from results.
            if record is not None and record.deleted:
                continue
            # Same rule as the text path: a photo that's no longer on disk
            # is tombstoned and skipped rather than shown as a dead result.
            if record is None or not Path(record.path).exists():
                if record is not None:
                    self.file_record_store.mark_deleted(record.path)
                continue
            path = record.path
            results.append(
                {
                    "file_id": hit["file_id"],
                    "path": path,
                    "filename": Path(path).name if path else hit["file_id"],
                    "score": hit["score"],
                    "kind": payload.get("kind"),
                    "captured_at": payload.get("captured_at"),
                    "timestamp_offset_seconds": payload.get("timestamp_offset_seconds"),
                    "moments": moments.get(hit["file_id"]) or None,
                    "confidence": "strong" if strong else "weak",
                }
            )
        return results
