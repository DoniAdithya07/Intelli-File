"""Measure precision/recall of visual search against the labelled demo set.

Ground truth comes from the file names scripts/download_demo_photos.py
writes (demo_<subject>_<n>.jpg): one query per subject, a hit counts as
correct when the file's subject matches. A few Commons labels are wrong,
so absolute numbers are slightly pessimistic — compare *between* cutoffs
and scoring strategies rather than reading them as truth. Run against the
live app database after indexing the demo folder:

    backend/venv/bin/python backend/scripts/evaluate_visual_cutoff.py

Used to set VISUAL_RELEVANCE_CUTOFF on 2026-09-11; re-run after changing
the CLIP model, preprocessing, or the cutoff.
"""

import json
import os
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.embeddings.clip_model import ClipModel  # noqa: E402
from app.indexing import IMAGES_TABLE  # noqa: E402
from app.main import CLIP_MODEL_DIR  # noqa: E402
from app.paths import ensure_app_dirs  # noqa: E402
from app.storage import LanceDBVectorStore  # noqa: E402
from app.visual_search.service import VISUAL_RELEVANCE_CUTOFF, embed_query  # noqa: E402
from download_demo_photos import SUBJECTS  # noqa: E402

_LABEL_RE = re.compile(r"demo_(.+)_\d+\.jpg")


def main() -> None:
    dirs = ensure_app_dirs()
    clip = ClipModel(CLIP_MODEL_DIR)
    store = LanceDBVectorStore(str(dirs["vector_index"]))
    rows = store.db.open_table(IMAGES_TABLE).search().limit(100_000).to_list()
    names = [Path(json.loads(r["payload"])["path"]).name for r in rows]
    labels = [(m.group(1) if (m := _LABEL_RE.match(n)) else None) for n in names]
    if not any(labels):
        sys.exit("No demo_<subject>_<n>.jpg photos in the index — run download_demo_photos.py and index the folder first.")
    vectors = np.array([r["vector"] for r in rows], dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)

    # squared L2 between unit vectors = 2 * (1 - cosine); same scale as the service's score
    distances = {}
    for query in SUBJECTS:
        distances[query] = 2.0 * (1.0 - vectors @ embed_query(clip, query))

    print(f"{len(rows)} photos in index, {sum(1 for l in labels if l)} labelled, {len(SUBJECTS)} subject queries\n")
    print(f"{'cutoff':>7} {'precision':>9} {'recall':>7} {'empty':>6}")
    for cutoff in np.arange(1.48, 1.571, 0.01):
        precisions, recalls, empty = [], [], 0
        for query, n_relevant in SUBJECTS.items():
            slug = query.replace(" ", "_")
            kept = np.where(distances[query] <= cutoff)[0]
            true_positives = sum(1 for i in kept if labels[i] == slug)
            precisions.append(true_positives / len(kept) if len(kept) else 1.0)
            recalls.append(true_positives / n_relevant)
            empty += len(kept) == 0
        marker = "  <- current" if abs(cutoff - VISUAL_RELEVANCE_CUTOFF) < 0.005 else ""
        print(f"{cutoff:7.2f} {np.mean(precisions):9.2f} {np.mean(recalls):7.2f} {empty:6d}{marker}")


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)  # success path only: skip native teardown — onnxruntime/LanceDB worker threads once raced the C++ static destructors at exit ("recursive_mutex lock failed", run_all_phases 2026-09-21) after every result was written
