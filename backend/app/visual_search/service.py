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

import json
import math
import time
from pathlib import Path

import numpy as np

from ..embeddings.clip_model import ClipModel
from ..indexing.visual_indexer import IMAGES_TABLE, MAX_VIDEO_KEYFRAMES
from ..search.dictionary import Dictionary, QueryCheck
from ..search.filenames import filename_vocabulary
from ..search.service import hide_if_missing
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

# ---------------------------------------------------------------------------
# Adaptive strong / possible decision (2026-10-12).
#
# Why the fixed bounds above are not enough on their own. They were measured
# on ~130 photos of mixed subjects. The user's real library (1,266 photos and
# 43 videos: classroom and group shots, movie-poster album art, screenshots)
# behaves differently: with ten times the photos, a concept that is NOT there
# still finds an incidental look-alike close to the query, and a people query
# sits near hundreds of similar people photos. Measured on a copy of that index
# (squared L2, best score per file):
#   concept absent, best photo:  baby 1.479, bicycle 1.488, airplane 1.514,
#                                car 1.510, pizza 1.519, giraffe 1.533, wedding 1.538
#   concept present, best photo: screenshot of code 1.366, penguin 1.368, cat 1.403,
#                                dog 1.429, group of friends 1.441, beach 1.455
# so with 1.51 as "always strong" every absent concept got a "Strong match"
# ("a bicycle": 7 strong, 1 of them right), and "couple photo" returned 24
# strong results, 2 of the 16 judged right. Labelled check
# (scripts/evaluate_visual_library.py, 24 queries judged by eye from contact
# sheets of the real files; 16 used while tuning, 8 held out):
#                                     strong results  precision of strong tier
#   old rules, tuning set                  226              0.50  (93 of 185 judged)
#   old rules, held-out set                103              0.49  (46 of 93)
#   new rules, tuning set                   31              0.90  (27 of 30)
#   new rules, held-out set                 18              0.89  (16 of 18)
#   strong results for concepts that are not in the photos: old 15, new 0.
#
# The decision is made per query, from that query's own scores (tier_limits):
#   1. The strong line shrinks with the number of photos. The best of N
#      unrelated photos creeps closer to any query as N grows, by about
#      sigma * sqrt(2 ln N) (sigma ~ 0.05 here), so the old 1.51 is lowered by
#      PHOTO_BOUND_SLOPE * (sqrt(2 ln N) - sqrt(2 ln N0)); N0 = 130 is the size
#      of the corpus 1.51 was tuned on. At 1,266 photos the line is 1.477,
#      which sits between the present (<= 1.465 above) and the absent (>= 1.479).
#   2. Strong must be within STRONG_MAX_GAP of THIS query's best photo, so a
#      smooth tail of "about as close" photos is not all strong (the old gap was
#      0.08 and let ~24 through).
#   3. Strong must stand clear of the library's typical score for the query:
#      STRONG_MIN_LEAD below the LEAD_REFERENCE_RANK-th best photo. This is what
#      keeps "animated" (whose top 100 photos are all equally mediocre) from
#      calling a portrait strong. Cost: a very broad true category (the 380
#      movie-poster covers) is never "strong" either, because its top 100 are
#      all close; it returns 8 correct "possible" results instead.
# Possible ("weak" in the API, "Weaker match" in the UI) uses looser versions of
# the same tests, never looser than 0.03 above the strong line, and is capped;
# the long tail is dropped. Libraries under ADAPTIVE_MIN_FILES photos keep the
# fixed bounds, because there is no "typical score" to compare with.
#
# Videos keep a stricter rule of their own. A film is searched through up to 120
# frames and some frame looks like anything: on 30 videos the best frame for
# "an airplane" (not in any video) was a ceiling fan at 1.447, "a cat" a fan at
# 1.474, while genuine best frames scored 1.39 (code), 1.407 (wedding), 1.433
# (beach), 1.449 (dog, bicycle). VIDEO_STRONG_BOUND = 1.44 therefore keeps every
# false best frame out of "strong" and loses only the 1.449 true ones (shown as
# possible). Nine queries; provisional, like the number it replaces.
PHOTO_BOUND_REFERENCE_FILES = 130
PHOTO_BOUND_SLOPE = 0.05
PHOTO_STRONG_FLOOR = 1.40
STRONG_MAX_GAP = 0.04
STRONG_MIN_LEAD = 0.07
POSSIBLE_MAX_GAP = 0.08
POSSIBLE_MIN_LEAD = 0.04
LEAD_REFERENCE_RANK = 100
ADAPTIVE_MIN_FILES = 100  # below this the library has no "typical" score: the fixed bounds above apply
MAX_STRONG_RESULTS = 16
MAX_POSSIBLE_RESULTS = 12
POSSIBLE_BOUND_ABOVE_STRONG = 0.03  # a possible match is at most this much looser than the strong line, so a query nothing answers returns nothing

# Videos keep their own, stricter rule (see VIDEO_ALWAYS_STRONG_BOUND).
VIDEO_STRONG_BOUND = 1.44
VIDEO_STRONG_MAX_GAP = 0.03
VIDEO_STRONG_MIN_LEAD = 0.10  # below the median video; only with >= VIDEO_ADAPTIVE_MIN_FILES videos
VIDEO_POSSIBLE_BOUND = 1.50
VIDEO_POSSIBLE_MAX_GAP = 0.06
VIDEO_ADAPTIVE_MIN_FILES = 20
MAX_POSSIBLE_VIDEOS = 3

# Candidates fetched per kind to know the library's typical score. One row
# per photo, so this reaches the LEAD_REFERENCE_RANK-th photo with room.
PHOTO_POOL_ROWS = 400
VIDEO_POOL_ROWS = 3000  # keyframes: reaches every video of a library with ~25 clips
LIBRARY_SIZE_CACHE_SECONDS = 30.0


# Words that name the KIND of file rather than what is in it. "couple photo"
# means "a couple", and "animated photos" means "animated": left in, CLIP
# reads the word too (it pulls toward picture-of-pictures, banners and
# collages) and the user's own file names (day-photos.png) drift in. Stripped
# before embedding; kept when nothing else would be left ("photos").
_GENERIC_QUERY_WORDS = {
    "photo", "photos", "photograph", "photographs", "picture", "pictures", "pic", "pics",
    "image", "images", "img", "imgs", "snap", "snaps", "snapshot", "snapshots", "shot", "shots",
    "video", "videos", "footage",
}
# Request framing, harmless to drop next to real words.
_QUERY_FILLER_WORDS = {"show", "find", "search", "look", "display", "give", "get", "me", "my", "all", "any", "some", "please", "pls"}
_QUERY_ARTICLES = {"a", "an", "the"}
_QUERY_CONNECTORS = {"of", "with", "showing", "from", "in", "on", "for", "to", "and", "or"}
_WORD_STRIP = ".,;:!?\"'()[]"


def _bare(word: str) -> str:
    return word.strip(_WORD_STRIP).lower()


def clean_query(raw_query: str) -> str:
    """The query without generic filler ("couple photo" -> "couple", "a photo
    of a dog" -> "a dog"). Returns the typed text unchanged when nothing
    generic was in it or nothing would be left."""
    text = raw_query.strip()
    words = text.split()
    keep = [w for w in words if _bare(w) not in _GENERIC_QUERY_WORDS and _bare(w) not in _QUERY_FILLER_WORDS]
    if len(keep) == len(words):
        return text
    # Orphans the removal leaves at the edges: "a [photo] of a dog" -> "a of a dog" -> "a dog".
    while keep:
        if _bare(keep[0]) in _QUERY_CONNECTORS or (len(keep) > 1 and _bare(keep[0]) in _QUERY_ARTICLES and _bare(keep[1]) in _QUERY_CONNECTORS):
            keep.pop(0)
        elif _bare(keep[-1]) in _QUERY_CONNECTORS or _bare(keep[-1]) in _QUERY_ARTICLES:
            keep.pop()
        else:
            break
    if not any(_bare(w) not in _QUERY_ARTICLES and _bare(w) not in _QUERY_CONNECTORS for w in keep):
        return text
    return " ".join(keep)


def photo_strong_bound(n_photos: int) -> float:
    """The absolute strong line for a library of n_photos: the old 1.51, lowered
    as the library grows (see the block comment above)."""
    if n_photos <= PHOTO_BOUND_REFERENCE_FILES:
        return VISUAL_ALWAYS_STRONG_BOUND
    shrink = PHOTO_BOUND_SLOPE * (math.sqrt(2 * math.log(n_photos)) - math.sqrt(2 * math.log(PHOTO_BOUND_REFERENCE_FILES)))
    return max(PHOTO_STRONG_FLOOR, VISUAL_ALWAYS_STRONG_BOUND - shrink)


def tier_limits(scores: list[float], n_files: int, is_video: bool) -> tuple[float, float]:
    """(strong_limit, possible_limit): a file is strong when its score is at
    or under the first, possible when at or under the second. `scores` are
    one best score per file of this kind, ascending, for the query's nearest
    files; `n_files` is how many files of this kind the library holds."""
    if not scores:
        return float("-inf"), float("-inf")
    best = scores[0]
    if is_video:
        strong = min(VIDEO_STRONG_BOUND, best + VIDEO_STRONG_MAX_GAP)
        possible = min(VIDEO_POSSIBLE_BOUND, best + VIDEO_POSSIBLE_MAX_GAP)
        if n_files >= VIDEO_ADAPTIVE_MIN_FILES and len(scores) >= VIDEO_ADAPTIVE_MIN_FILES:
            strong = min(strong, float(np.median(scores)) - VIDEO_STRONG_MIN_LEAD)
        return strong, max(possible, strong)
    if len(scores) < ADAPTIVE_MIN_FILES or n_files < ADAPTIVE_MIN_FILES:
        # Small library: no typical score to compare with, so the fixed
        # bounds, measured on 127 labelled photos, apply.
        if best <= VISUAL_ALWAYS_STRONG_BOUND:
            strong = max(VISUAL_ALWAYS_STRONG_BOUND, min(VISUAL_RELEVANCE_CUTOFF, best + VISUAL_STRONG_MAX_GAP_FROM_BEST))
        else:
            strong = VISUAL_ALWAYS_STRONG_BOUND
        possible = min(VISUAL_WEAK_BOUND, best + VISUAL_WEAK_MAX_GAP_FROM_BEST)
        return strong, max(possible, strong)
    reference = scores[min(LEAD_REFERENCE_RANK, len(scores)) - 1]
    strong = min(photo_strong_bound(n_files), best + STRONG_MAX_GAP, reference - STRONG_MIN_LEAD)
    possible = min(VISUAL_WEAK_BOUND, photo_strong_bound(n_files) + POSSIBLE_BOUND_ABOVE_STRONG, best + POSSIBLE_MAX_GAP, reference - POSSIBLE_MIN_LEAD)
    return strong, max(possible, strong)


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
        self._size_cache: dict[str, tuple[float, int]] = {}

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

    def library_size(self, kind: str) -> int | None:
        """How many photos (or video keyframes) the index holds, cached for a
        moment: the strong line depends on it (see photo_strong_bound). None
        when the store cannot say, and the caller then uses what it fetched."""
        cached = self._size_cache.get(kind)
        now = time.monotonic()
        if cached is not None and now - cached[0] < LIBRARY_SIZE_CACHE_SECONDS:
            return cached[1]
        try:
            pair = json.dumps({"kind": kind})[1:-1]
            count = int(self.vector_store.db.open_table(IMAGES_TABLE).count_rows(filter=f"payload LIKE '%{pair}%'"))
        except Exception:
            return None
        self._size_cache[kind] = (now, count)
        return count

    def _query(self, query_vector: list[float], rows: int, kind: str) -> list[dict]:
        """The `rows` nearest vectors of one kind, best first. LanceDB's own
        query() hands every row's 512-float vector back too, which costs ~1 ms
        per 10 rows and this search reads thousands to learn what a typical
        score is (measured 2026-10-12: 277 ms for 2,363 rows, 69 ms without the
        vectors), so the table is asked for the three columns used. Falls back
        to the store's query() for a store without that table handle."""
        open_table = getattr(self.vector_store, "_open", None)
        if open_table is None:
            return self.vector_store.query(IMAGES_TABLE, query_vector, top_k=rows, payload_equals={"kind": kind})
        pair = json.dumps({"kind": kind})[1:-1].replace("'", "''")
        found = (
            open_table(IMAGES_TABLE).search(query_vector).select(["file_id", "payload", "_distance"])
            .where(f"payload LIKE '%{pair}%'", prefilter=True).limit(rows).to_list()
        )
        return [
            {"file_id": r["file_id"], "score": r.get("_distance"), "payload": json.loads(r["payload"]) if r.get("payload") else {}}
            for r in found
        ]

    def _nearest_files(self, query_vector: list[float], kind: str, top_k: int) -> tuple[list[dict], dict[str, list[dict]]]:
        """The nearest files of one kind, best first, one hit per file, with
        the best-matching moments of each video.

        A video contributes one vector per keyframe (Phase 8b), so many rows
        are fetched, then collapsed to the best moment per file: otherwise a
        120-frame clip would fill the grid with near-identical frames. The kind
        filter runs inside the vector search, before the cut (as a filter on
        the nearest candidates, a library with ~700+ photos that out-scored
        every video frame left the Videos tab empty, 2026-10-04). The rows
        also serve as the sample that says what a typical score is."""
        rows = max(PHOTO_POOL_ROWS if kind == "photo" else VIDEO_POOL_ROWS, top_k * (MAX_VIDEO_KEYFRAMES // 4))
        raw_hits = self._query(query_vector, rows, kind)
        hits: list[dict] = []
        moments: dict[str, list[dict]] = {}  # file_id -> up to VIDEO_PREVIEW_MOMENTS distinct moments
        for hit in raw_hits:  # already best-first
            file_id = hit["file_id"]
            if file_id not in moments:
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
        return hits, moments

    def search(self, raw_query: str, top_k: int = 10, apply_cutoff: bool = True, kind: str | None = None) -> list[dict]:
        """Photos and videos matching a described query, best first: the
        strong matches (at most MAX_STRONG_RESULTS), then at most
        MAX_POSSIBLE_RESULTS flagged confidence="weak". Which is which is
        decided per query from its own scores (tier_limits).
        `kind` = "photo" | "video" restricts to one (the page's filter
        chips); None searches both. `apply_cutoff` exists so the
        prototype/evaluation scripts can inspect raw distances for every
        candidate rather than only survivors."""
        query = clean_query(raw_query)
        if not query:
            return []

        query_vector = embed_query(self.clip_model, query).tolist()
        # Each kind is judged against its own scores: a video's is the best
        # of up to 120 keyframes, so the two are not comparable.
        candidates: list[tuple[dict, bool, bool]] = []  # (hit, strong, is_video)
        moments: dict[str, list[dict]] = {}
        for k in ((kind,) if kind in ("photo", "video") else ("photo", "video")):
            hits, kind_moments = self._nearest_files(query_vector, k, top_k)
            moments.update(kind_moments)
            scores = [h["score"] for h in hits if h["score"] is not None]
            n_files = max(len(scores), self.library_size(k) or 0) if k == "photo" else len(scores)
            strong_limit, possible_limit = tier_limits(scores, n_files, is_video=k == "video")
            for hit in hits:
                score = hit["score"]
                strong = score is None or score <= strong_limit
                if apply_cutoff and not strong and score > possible_limit:
                    break  # hits are best-first: nothing further qualifies
                candidates.append((hit, strong, k == "video"))
        candidates.sort(key=lambda c: (c[0]["score"] is not None, c[0]["score"] or 0.0))

        def result(hit: dict, confidence: str) -> dict | None:
            record = self.file_record_store.get_by_file_id(hit["file_id"])
            # A tombstoned file must not surface, matching the text path's
            # behaviour where deleted files disappear from results. Same rule
            # as the text path: a photo that's no longer on disk is
            # tombstoned and skipped rather than shown as a dead result.
            if record is None or record.deleted or hide_if_missing(self.file_record_store, record.path):
                return None
            payload = hit["payload"]
            return {
                "file_id": hit["file_id"],
                "path": record.path,
                "filename": Path(record.path).name if record.path else hit["file_id"],
                "score": hit["score"],
                "kind": payload.get("kind"),
                "captured_at": payload.get("captured_at"),
                "timestamp_offset_seconds": payload.get("timestamp_offset_seconds"),
                "moments": moments.get(hit["file_id"]) or None,
                "confidence": confidence,
            }

        def emit(entries: list[tuple[dict, bool, bool]], limit: int, video_limit: int | None = None, label: str | None = None) -> list[dict]:
            out: list[dict] = []
            videos = 0
            for hit, strong, is_video in entries:
                if len(out) >= limit:
                    break
                if is_video and video_limit is not None and videos >= video_limit:
                    continue
                shown = result(hit, label or ("strong" if strong else "weak"))
                if shown is not None:
                    out.append(shown)
                    videos += is_video
            return out

        if not apply_cutoff:
            return emit(candidates, top_k)
        # Strong first (capped), then the possible ones. A strong match beyond
        # the cap is shown as possible rather than dropped: it is still
        # reachable, just no longer claimed.
        strong_results = emit([c for c in candidates if c[1]], min(MAX_STRONG_RESULTS, top_k))
        shown_ids = {r["file_id"] for r in strong_results}
        rest = [c for c in candidates if c[0]["file_id"] not in shown_ids]
        possible_results = emit(rest, min(MAX_POSSIBLE_RESULTS, top_k - len(strong_results)), video_limit=MAX_POSSIBLE_VIDEOS, label="weak")
        return strong_results + possible_results
