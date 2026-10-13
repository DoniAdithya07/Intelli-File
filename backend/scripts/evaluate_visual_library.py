"""How honest are the "Strong match" labels on a real photo library?

The other visual evaluations use small, tidy collections (drawn shapes, the
26 wallpapers every Windows PC ships, 127 labelled Wikimedia photos). A real
library is different: thousands of look-alike portraits, screenshots and
album art, where a query like "couple photo" is close to hundreds of files
and the fixed distance cut-offs tuned on small sets called nearly all of
them "Strong". This script measures that on YOUR files, with labels you
wrote by looking at the pictures.

What it does, per query in the labels file:
  1. runs the search as the app runs it (query cleaned of filler words,
     per-query strong / possible decision, caps), and
  2. optionally (--legacy) runs the old rules on the same index, so one run
     gives before and after,
  3. counts the strong and possible results and checks each against the
     labels: precision of the strong tier (correct / judged), how many
     strong results a query with no true answer produced (should be 0).

Library to test, one of:
  --index DIR     a COPY of an IntelliFile data folder: DIR\\vector_index and
                  DIR\\files.db (never point this at the live folder; it is
                  opened read-write because SQLite and LanceDB open that way,
                  and a result whose file is missing is marked deleted)
  --library DIR   a folder of pictures: they are embedded into a throwaway
                  index under .local/tmp (about 40 ms per picture)

Labels file (JSON). Each entry is a full path (safest: file names such as
goal.png repeat across folders) or a bare file name. Anything a result shows
that is in neither list is reported as "unjudged" and left out of precision.
    {"queries": {
        "a dog":  {"relevant": ["C:\pets\dog.png"], "irrelevant": ["cat.png"]},
        "a giraffe": {"absent": true, "irrelevant": ["zoo.jpg"]}   # nothing in the library is a giraffe
        "an airplane": {"absent": ["photo"], "relevant": ["trip.mp4"]}   # no picture, but one video has a plane
    }}

Helper for writing labels: --show "a dog" prints the nearest pictures with
their scores (and --sheet-dir DIR writes a numbered contact sheet to open
and look at).

Run (from backend/):
    venv\\Scripts\\python.exe scripts\\evaluate_visual_library.py --index ..\\.local\\tmp\\realcopy --labels ..\\.local\\work\\photo_labels.json --legacy
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[2]
CLIP_MODEL_DIR = Path(__file__).resolve().parents[1] / "models" / "clip-vit-base-patch16"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".avif"}


def legacy_search(service, raw_query: str, top_k: int) -> list[dict]:
    """The search as it behaved before 2026-10-12: query typed as is, one
    unfiltered candidate list, fixed absolute bounds, no caps. Kept here (not
    in the service) only so a single run can print before and after."""
    from app.indexing.visual_indexer import IMAGES_TABLE, MAX_VIDEO_KEYFRAMES
    from app.visual_search import service as v

    query = raw_query.strip()
    vector = v.embed_query(service.clip_model, query).tolist()
    raw_hits = service.vector_store.query(IMAGES_TABLE, vector, top_k=top_k * (MAX_VIDEO_KEYFRAMES // 4))
    hits, seen = [], set()
    for hit in raw_hits:
        if hit["file_id"] in seen or len(hits) >= top_k:
            continue
        seen.add(hit["file_id"])
        hits.append(hit)
    best = min((h["score"] for h in hits if h["score"] is not None), default=None)
    results = []
    for hit in hits:
        score = hit["score"]
        is_video = hit["payload"].get("kind") == "video"
        always = v.VIDEO_ALWAYS_STRONG_BOUND if is_video else v.VISUAL_ALWAYS_STRONG_BOUND
        gap = v.VIDEO_STRONG_MAX_GAP_FROM_BEST if is_video else v.VISUAL_STRONG_MAX_GAP_FROM_BEST
        strong = score is None or score <= always or (
            score <= v.VISUAL_RELEVANCE_CUTOFF and best is not None and best <= v.VISUAL_ALWAYS_STRONG_BOUND and score <= best + gap
        )
        if not strong and score is not None and (score > v.VISUAL_WEAK_BOUND or (best is not None and score > best + v.VISUAL_WEAK_MAX_GAP_FROM_BEST)):
            continue
        record = service.file_record_store.get_by_file_id(hit["file_id"])
        if record is None or record.deleted or not Path(record.path).exists():
            continue
        results.append({"path": record.path, "filename": Path(record.path).name, "kind": hit["payload"].get("kind"), "score": score, "confidence": "strong" if strong else "weak"})
    return results


def build_service(index: Path | None, library: Path | None, scratch: Path):
    from app.embeddings.clip_model import ClipModel
    from app.files.identity import build_file_record
    from app.indexing import VisualIndexer
    from app.search.dictionary import DEFAULT_PATH, Dictionary
    from app.storage import FileRecordStore, LanceDBVectorStore
    from app.visual_search import VisualSearchService

    clip = ClipModel(CLIP_MODEL_DIR)
    if index is not None:
        vectors = LanceDBVectorStore(str(index / "vector_index"))
        records = FileRecordStore(index / "files.db")
    else:
        vectors = LanceDBVectorStore(str(scratch / "vector_index"))
        records = FileRecordStore(scratch / "files.db")
        indexer = VisualIndexer(clip, vectors, records)
        pictures = sorted(p for p in library.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
        print(f"Embedding {len(pictures)} pictures from {library} ...", flush=True)
        for i, path in enumerate(pictures, 1):
            record = build_file_record(path)
            if indexer.index_image_file(path, record.file_id, record.hash):
                records.upsert(record)
            if i % 200 == 0:
                print(f"  {i}/{len(pictures)}", flush=True)
    dictionary = Dictionary(DEFAULT_PATH) if DEFAULT_PATH.exists() else None
    return VisualSearchService(clip, vectors, records, dictionary=dictionary)


def judge(results: list[dict], relevant: set[str], irrelevant: set[str], tier: str) -> dict:
    rows = [r for r in results if r["confidence"] == tier]
    def mark(r: dict, names: set[str]) -> bool:
        return r["path"] in names or r["filename"] in names

    correct = sum(mark(r, relevant) for r in rows)
    wrong = sum(mark(r, irrelevant) and not mark(r, relevant) for r in rows)
    judged = correct + wrong
    return {
        "shown": len(rows),
        "judged": judged,
        "correct": correct,
        "unjudged": len(rows) - judged,
        "precision": round(correct / judged, 3) if judged else None,
        "files": [(r["filename"], r["kind"], round(r["score"], 3)) for r in rows],
    }


def contact_sheet(service, query: str, out_dir: Path, count: int = 30) -> None:
    from PIL import Image, ImageDraw
    from app.visual_search.service import clean_query

    results = service.search(clean_query(query), top_k=count, apply_cutoff=False, kind="photo")
    cols, size = 6, 200
    rows = (len(results) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * size, rows * (size + 14)), "white")
    draw = ImageDraw.Draw(sheet)
    for i, r in enumerate(results):
        try:
            image = Image.open(r["path"]).convert("RGB")
            image.thumbnail((size - 4, size - 4))
        except Exception:
            image = Image.new("RGB", (50, 50), "grey")
        x, y = (i % cols) * size, (i // cols) * (size + 14)
        sheet.paste(image, (x + 2, y + 14))
        draw.text((x + 2, y), f"{i + 1} {r['score']:.3f}", fill="black")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / (re.sub(r"\W+", "_", query) + ".jpg")
    sheet.save(target, quality=80)
    print(f"contact sheet: {target}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--index", type=Path, help="copy of an IntelliFile data folder (vector_index + files.db)")
    parser.add_argument("--library", type=Path, help="folder of pictures to embed into a throwaway index")
    parser.add_argument("--labels", type=Path, help="labels JSON (see above)")
    parser.add_argument("--top-k", type=int, default=24, help="results asked for per query (the Photos page asks for 24)")
    parser.add_argument("--legacy", action="store_true", help="also run the pre-2026-10-12 rules for comparison")
    parser.add_argument("--out", type=Path, help="write the full result as JSON here")
    parser.add_argument("--show", action="append", default=[], help="print the nearest pictures for this query (to help write labels)")
    parser.add_argument("--sheet-dir", type=Path, help="with --show: also write a numbered contact sheet here")
    args = parser.parse_args()
    if (args.index is None) == (args.library is None):
        parser.error("give exactly one of --index and --library")
    os.environ.setdefault("INTELLIFILE_OFFLINE_GUARD", "1")

    scratch = None
    if args.library is not None:
        base = REPO / ".local" / "tmp"
        base.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix="visual_library_", dir=base))
    try:
        service = build_service(args.index, args.library, scratch)
        for query in args.show:
            from app.visual_search.service import clean_query

            print(f"\n{query!r} searched as {clean_query(query)!r}")
            for i, r in enumerate(service.search(clean_query(query), top_k=30, apply_cutoff=False), 1):
                print(f"  {i:2d} {r['score']:.3f} {r['confidence']:6s} {r['kind']:5s} {r['filename']}")
            if args.sheet_dir:
                contact_sheet(service, query, args.sheet_dir)
        report: dict = {}
        if args.labels:
            labels = json.loads(args.labels.read_text(encoding="utf-8"))["queries"]
            modes = [("after", lambda q: service.search(q, top_k=args.top_k))]
            if args.legacy:
                modes.insert(0, ("before", lambda q: legacy_search(service, q, args.top_k)))
            totals = {m: {"strong": 0, "judged": 0, "correct": 0, "absent_false_strong": 0, "absent_queries": 0, "possible": 0, "possible_judged": 0, "possible_correct": 0} for m, _ in modes}
            for query, spec in labels.items():
                relevant, irrelevant = set(spec.get("relevant", [])), set(spec.get("irrelevant", []))
                absent = spec.get("absent") or False  # True, or the kinds ("photo"/"video") that have no true answer
                absent_kinds = {"photo", "video"} if absent is True else set(absent or [])
                report[query] = {}
                line = f"{query!r:34s}"
                for mode, run in modes:
                    results = run(query)
                    strong, possible = judge(results, relevant, irrelevant, "strong"), judge(results, relevant, irrelevant, "weak")
                    report[query][mode] = {"strong": strong, "possible": possible}
                    t = totals[mode]
                    t["strong"] += strong["shown"]
                    t["judged"] += strong["judged"]
                    t["correct"] += strong["correct"]
                    t["possible"] += possible["shown"]
                    t["possible_judged"] += possible["judged"]
                    t["possible_correct"] += possible["correct"]
                    if absent:
                        t["absent_queries"] += 1
                        t["absent_false_strong"] += sum(k in absent_kinds for _, k, _ in strong["files"])
                    prec = "n/a" if strong["precision"] is None else f"{strong['precision']:.2f}"
                    pp = "n/a" if possible["precision"] is None else f"{possible['precision']:.2f}"
                    line += f" | {mode}: strong {strong['shown']:2d} (prec {prec}, {strong['unjudged']} unjudged) possible {possible['shown']:2d} (prec {pp})" + ("  ABSENT" if absent else "")
                print(line)
            print()
            for mode, t in totals.items():
                prec = t["correct"] / t["judged"] if t["judged"] else float("nan")
                pprec = t["possible_correct"] / t["possible_judged"] if t["possible_judged"] else float("nan")
                print(
                    f"{mode:6s} strong results {t['strong']}, judged {t['judged']}, precision of strong tier {prec:.3f}; "
                    f"possible results {t['possible']}, precision {pprec:.3f}; "
                    f"strong results for the {t['absent_queries']} queries with no true answer: {t['absent_false_strong']}"
                )
            report["_totals"] = totals
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)  # skip native teardown: onnxruntime/LanceDB worker threads have raced C++ static destructors at exit before
