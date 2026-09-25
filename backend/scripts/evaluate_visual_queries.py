"""Phase 8 accuracy check: 20 text queries against the demo folder, each
with photos a human has looked at and confirmed as the correct answer
(not the Wikimedia labels — several of those are wrong: `sushi_*` are shop
fronts, `elephant_2` is a satellite image). Prints the top-3 per query
with cosine similarity, then accuracy@1 / @3.

    backend/venv/bin/python backend/scripts/evaluate_visual_queries.py
    backend/venv/bin/python backend/scripts/evaluate_visual_queries.py --model-dir models/clip-vit-base-patch32

Embeds the photos directly with ClipModel so different model variants can
be compared on identical inputs; with no --model-dir it also cross-checks
the live index (LanceDB `_distance` vs cosine computed here) and, when
`transformers` can load the PyTorch checkpoint, our preprocessing against
the reference CLIPProcessor. Ground truth lives in GROUND_TRUTH below —
extend it when the demo folder changes.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.embeddings.clip_model import ClipModel  # noqa: E402
from app.embeddings.clip_preprocess import preprocess_image  # noqa: E402
from app.files.discovery import IMAGE_EXTENSIONS  # noqa: E402
from app.visual_search.service import embed_query  # noqa: E402

DEMO_DIR = Path.home() / "Desktop" / "IntelliFile-Search-Demo"

# query -> file names that are a correct top hit (any of them counts).
GROUND_TRUTH: dict[str, set[str]] = {
    "a cup of coffee": {"demo_coffee_cup_2.jpg"},
    "a pizza": {"demo_pizza_1.jpg", "demo_pizza_2.jpg", "demo_pizza_3.jpg"},
    "a white horse": {"demo_horse_4.jpg", "demo_horse_1.jpg"},
    # the three "sailing_boat" photos are of Mumbles lighthouse on its islet (checked by eye)
    "a lighthouse": {"demo_lighthouse_1.jpg", "demo_lighthouse_2.jpg", "demo_lighthouse_3.jpg",
                     "demo_sailing_boat_1.jpg", "demo_sailing_boat_2.jpg", "demo_sailing_boat_3.jpg"},
    "a waterfall": {"demo_waterfall_1.jpg", "demo_waterfall_2.jpg", "demo_waterfall_3.jpg"},
    "a herd of elephants": {"demo_elephant_1.jpg"},
    "a train at night": {"demo_train_3.jpg"},
    "a bride and groom waving from a carriage": {"demo_wedding_1.jpg", "demo_wedding_3.jpg"},
    "a sunset over the ocean": {"demo_sunset_2.jpg", "night.jpg", "demo_sunset_1.jpg"},
    "a bamboo forest": {"demo_forest_1.jpg"},
    "a blue fish underwater": {"demo_fish_underwater_1.jpg"},
    "a family riding a scooter": {"demo_motorcycle_1.jpg"},
    "a cat in the snow": {"dog.jpg", "demo_cat_2.jpg"},
    "a person wearing a red shirt using a laptop": {"demo_laptop_on_a_desk_2.jpg"},
    "a black and white photo of a derailed train": {"demo_train_1.jpg"},
    "a woman in a pink headscarf": {"snow.jpg"},
    "the headstock of a guitar": {"demo_guitar_3.jpg"},
    "a birthday cake with a red bow": {"demo_birthday_cake_1.jpg"},
    "a robin perched on a wire": {"demo_bird_3.jpg"},
    "an old stone castle": {"demo_old_castle_1.jpg", "demo_old_castle_2.jpg"},
}


# The same subjects the way people actually type them: one or two bare words.
# Ground truth = every photo above that shows the thing (so "castle" accepts
# the third, ruined castle too), plus a few photos checked separately.
SHORT_QUERIES: dict[str, set[str]] = {
    "airplane": {"demo_airplane_2.jpg", "demo_airplane_4.jpg", "demo_airplane_5.jpg", "demo_airplane_6.jpg"},
    "castle": {"demo_old_castle_1.jpg", "demo_old_castle_2.jpg", "demo_old_castle_3.jpg"},
    "red shirt": {"demo_laptop_on_a_desk_2.jpg"},
    "pizza": {"demo_pizza_1.jpg", "demo_pizza_2.jpg", "demo_pizza_3.jpg"},
    "lighthouse": {"demo_lighthouse_1.jpg", "demo_lighthouse_2.jpg", "demo_lighthouse_3.jpg",
                   "demo_sailing_boat_1.jpg", "demo_sailing_boat_2.jpg", "demo_sailing_boat_3.jpg"},
    "horse": {"demo_horse_1.jpg", "demo_horse_3.jpg", "demo_horse_4.jpg"},
    "elephant": {"demo_elephant_1.jpg"},
    "waterfall": {"demo_waterfall_1.jpg", "demo_waterfall_2.jpg", "demo_waterfall_3.jpg"},
    "guitar": {"demo_guitar_1.jpg", "demo_guitar_3.jpg"},
    "cake": {"demo_birthday_cake_1.jpg", "demo_birthday_cake_2.jpg", "demo_birthday_cake_3.jpg"},
    "cat": {"dog.jpg", "demo_cat_1.jpg", "demo_cat_2.jpg", "demo_cat_4.jpg", "demo_cat_6.jpg"},
    "wedding": {"demo_wedding_1.jpg", "demo_wedding_2.jpg", "demo_wedding_3.jpg"},
    "coffee": {"demo_coffee_cup_2.jpg", "demo_coffee_cup_1.jpg", "demo_coffee_cup_3.jpg"},
    "train": {"demo_train_1.jpg", "demo_train_2.jpg", "demo_train_3.jpg"},
    "fish": {"demo_fish_underwater_1.jpg", "demo_fish_underwater_2.jpg", "demo_fish_underwater_3.jpg"},
    # bridge_2 = Sydney Harbour Bridge at night, desert_3 = yurts under the moon; city_skyline_at_night_4 is DAYTIME (mislabelled)
    "night": {"demo_bridge_2.jpg", "demo_desert_3.jpg", "demo_train_3.jpg",
              "demo_city_skyline_at_night_1.jpg", "demo_city_skyline_at_night_2.jpg", "demo_city_skyline_at_night_3.jpg"},
    "sunset": {"demo_sunset_1.jpg", "demo_sunset_2.jpg", "night.jpg"},
    "snow": {"dog.jpg", "demo_cat_2.jpg", "demo_horse_3.jpg", "demo_flowers_1.jpg"},
}


def evaluate(clip: ClipModel, image_vectors: np.ndarray, names: list[str], queries: dict[str, set[str]], title: str) -> None:
    print(f"--- {title} ({len(queries)} queries) ---")
    hits_at_1 = hits_at_3 = 0
    for query, correct in queries.items():
        text_vector = embed_query(clip, query)
        cosine = image_vectors @ text_vector
        order = np.argsort(-cosine)[:3]
        top = [names[i] for i in order]
        at1 = top[0] in correct
        at3 = any(n in correct for n in top)
        hits_at_1 += at1
        hits_at_3 += at3
        mark = "OK " if at1 else ("~3 " if at3 else "MISS")
        ranked = ", ".join(f"{names[i].replace('demo_', '')}={cosine[i]:.3f}" for i in order)
        print(f"[{mark}] {query:44s} -> {ranked}")
    n = len(queries)
    print(f"accuracy@1 = {hits_at_1}/{n} = {hits_at_1 / n:.0%}    accuracy@3 = {hits_at_3}/{n} = {hits_at_3 / n:.0%}\n")


def load_photos(folder: Path) -> tuple[list[Path], list[Image.Image]]:
    paths = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS)
    images = [ImageOps.exif_transpose(Image.open(p)).convert("RGB") for p in paths]
    return paths, images


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", default=None, help="CLIP model directory (default: the one the app uses)")
    parser.add_argument("--folder", default=str(DEMO_DIR))
    args = parser.parse_args()

    if args.model_dir:
        model_dir = Path(args.model_dir)
    else:
        from app.main import CLIP_MODEL_DIR

        model_dir = CLIP_MODEL_DIR
    clip = ClipModel(model_dir)
    print(f"model: {model_dir.name}  precision={clip.precision}  provider={clip.active_provider}  dim={clip.dimension}")

    paths, images = load_photos(Path(args.folder))
    missing = {n for q in (GROUND_TRUTH, SHORT_QUERIES) for names in q.values() for n in names} - {p.name for p in paths}
    if missing:
        print(f"WARNING: ground-truth files not in folder (renamed/deleted?): {sorted(missing)}")
    print(f"{len(paths)} photos in {args.folder}\n")

    image_vectors = clip.embed_images(images)  # (N, dim), unit length
    names = [p.name for p in paths]

    evaluate(clip, image_vectors, names, GROUND_TRUTH, "descriptive phrases")
    evaluate(clip, image_vectors, names, SHORT_QUERIES, "bare one/two-word queries")

    if args.model_dir:
        return 0

    # --- Cross-checks against the live index and the reference pipeline ---
    print("\ncross-checks:")
    from app.indexing import IMAGES_TABLE
    from app.paths import ensure_app_dirs
    from app.storage import LanceDBVectorStore

    store = LanceDBVectorStore(str(ensure_app_dirs()["vector_index"]))
    query_vector = embed_query(clip, "a cup of coffee")
    stored = {Path(json.loads(r["payload"])["path"]).name: np.array(r["vector"], dtype=np.float32)
              for r in store.db.open_table(IMAGES_TABLE).search().limit(100_000).to_list()}
    common = [n for n in names if n in stored]
    if common:
        fresh = {n: image_vectors[names.index(n)] for n in common}
        agreement = min(float(stored[n] @ fresh[n]) for n in common)
        print(f"  index vs fresh embeddings: {len(common)} photos, min cosine agreement = {agreement:.4f}  ({'OK' if agreement > 0.999 else 'STALE INDEX'})")
        lance = store.query(IMAGES_TABLE, query_vector.tolist(), top_k=5)
        worst = max(abs(h["score"] - 2 * (1 - float(stored_vec @ query_vector)))
                    for h in lance if (stored_vec := stored.get(Path(h["payload"]["path"]).name)) is not None)
        print(f"  LanceDB _distance vs 2*(1-cosine): max |diff| over top-5 = {worst:.6f}  ({'OK' if worst < 1e-4 else 'METRIC MISMATCH'})")
    else:
        print("  (demo folder is not in the live index — index it in the app to enable this check)")

    try:
        import torch
        from transformers import CLIPModel, CLIPProcessor

        checkpoint = {"clip-vit-base-patch16": "openai/clip-vit-base-patch16", "clip-vit-base-patch32": "openai/clip-vit-base-patch32"}[model_dir.name]
        hf = CLIPModel.from_pretrained(checkpoint, local_files_only=True).eval()
        processor = CLIPProcessor.from_pretrained(checkpoint, local_files_only=True)
        sample = images[:8]
        with torch.no_grad():
            reference = hf.get_image_features(pixel_values=processor(images=sample, return_tensors="pt")["pixel_values"])
            reference = (reference / reference.norm(dim=-1, keepdim=True)).numpy()
            ours_pre = hf.get_image_features(pixel_values=torch.tensor(np.stack([preprocess_image(im) for im in sample])))
            ours_pre = (ours_pre / ours_pre.norm(dim=-1, keepdim=True)).numpy()
        pre_agreement = float((reference * ours_pre).sum(1).min())
        onnx_agreement = float((reference * image_vectors[:8]).sum(1).min())
        print(f"  our preprocessing vs CLIPProcessor (same PyTorch weights): min cosine = {pre_agreement:.4f}  ({'OK' if pre_agreement > 0.99 else 'PREPROCESSING DRIFT'})")
        print(f"  our ONNX pipeline vs PyTorch reference:                  min cosine = {onnx_agreement:.4f}  ({'OK' if onnx_agreement > 0.99 else 'WEIGHT/RUNTIME DRIFT'})")
    except Exception as e:  # torch/transformers/checkpoint not available offline — informational only
        print(f"  (reference PyTorch check skipped: {type(e).__name__})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
