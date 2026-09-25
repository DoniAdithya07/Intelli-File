"""Phase 8 integration test: real generated images -> CLIP vision
embedding -> vector table, then prove a typed description actually finds
the right picture, an unrelated query doesn't win, EXIF/mtime capture
dates land, thumbnails render, and deleting a photo removes it. Run with:

    backend/venv/bin/python backend/scripts/prototype_visual_search.py

Fixtures are generated here rather than committed as binary files, the
same way the PDF/DOCX/speech fixtures are in the other phase scripts —
the test stays self-contained and needs no network.
"""

import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from app.embeddings.clip_model import ClipModel  # noqa: E402
from app.files.discovery import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, discover_files  # noqa: E402
from app.indexing import IMAGES_TABLE, Indexer, VisualIndexer, cleanup_tombstones, index_folder  # noqa: E402
from app.embeddings.model import EmbeddingModel, default_model_dir  # noqa: E402
from app.search.dictionary import DEFAULT_PATH as DEFAULT_WORDLIST_PATH, Dictionary  # noqa: E402
from app.storage import FileRecordStore, KeywordStore, LanceDBVectorStore  # noqa: E402
from app.thumbnails import generate_thumbnail  # noqa: E402
from app.visual_search import VisualSearchService  # noqa: E402

CLIP_MODEL_DIR = Path(__file__).resolve().parents[1] / "models" / "clip-vit-base-patch16"
TEXT_MODEL_DIR = default_model_dir(Path(__file__).resolve().parents[1] / "models")


# Fixtures are 512px — above MIN_IMAGE_LONGEST_SIDE (320), which skips
# icon-sized images; app_icon.png below is the deliberately-too-small case.
def make_red_circle(path: Path) -> None:
    image = Image.new("RGB", (512, 512), "white")
    ImageDraw.Draw(image).ellipse([80, 80, 432, 432], fill="red")
    image.save(path)


def make_blue_square(path: Path) -> None:
    image = Image.new("RGB", (512, 512), "white")
    ImageDraw.Draw(image).rectangle([100, 100, 412, 412], fill="blue")
    image.save(path)


def make_two_scene_video(path: Path, seconds_per_scene: int = 3) -> None:
    """A real MP4 (H.264 via PyAV's bundled codecs): a red circle for the
    first scene, a blue square for the second, 640x360 at 2 fps with every
    frame a keyframe so keyframe-only decoding sees both scenes."""
    import av

    def frame(draw_fn):
        image = Image.new("RGB", (640, 360), "white")
        draw_fn(ImageDraw.Draw(image))
        return image

    scenes = [
        frame(lambda d: d.ellipse([200, 40, 440, 320], fill="red")),
        frame(lambda d: d.rectangle([200, 40, 440, 320], fill="blue")),
    ]
    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("libx264", rate=2)
        stream.width, stream.height, stream.pix_fmt = 640, 360, "yuv420p"
        stream.options = {"g": "1"}  # keyframe every frame
        for image in scenes:
            for _ in range(seconds_per_scene * 2):
                for packet in stream.encode(av.VideoFrame.from_image(image)):
                    container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


def make_sunset_gradient(path: Path) -> None:
    image = Image.new("RGB", (512, 512))
    draw = ImageDraw.Draw(image)
    for y in range(512):
        t = y / 511
        colour = (
            int(255 * (1 - t) + 90 * t),
            int(140 * (1 - t) + 30 * t),
            int(30 * (1 - t) + 130 * t),
        )
        draw.line([(0, y), (512, y)], fill=colour)
    image.save(path)


def main():
    if not (CLIP_MODEL_DIR / "onnx").exists():
        print("CLIP model not found — run scripts/download_clip_model.py first.")
        sys.exit(1)

    workdir = Path(tempfile.mkdtemp())
    try:
        photos_dir = workdir / "photos"
        photos_dir.mkdir()
        fixtures = {
            "red_circle.png": make_red_circle,
            "blue_square.png": make_blue_square,
            "sunset_gradient.png": make_sunset_gradient,
        }
        for name, builder in fixtures.items():
            builder(photos_dir / name)
        # Regression fixtures from the 2026-09-11 live test (see visual_indexer.py):
        # a 32x32 app icon must be discovered but NOT indexed (too small to be a
        # photo), and an .avif must be discovered AND indexed.
        icon = Image.new("RGB", (32, 32), "red")
        icon.save(photos_dir / "app_icon.png")
        make_red_circle(photos_dir / "red_circle_copy.avif")

        # A text file in the same folder proves routing: it must go to the
        # text pipeline, not the visual one.
        (photos_dir / "notes.txt").write_text(
            "Horizontal scaling provisions more instances when traffic increases."
        )

        clip_model = ClipModel(CLIP_MODEL_DIR)
        text_model = EmbeddingModel(TEXT_MODEL_DIR)
        vector_store = LanceDBVectorStore(str(workdir / "vectors"))
        keyword_store = KeywordStore(workdir / "keyword.db")
        record_store = FileRecordStore(workdir / "files.db")
        indexer = Indexer(text_model, vector_store, keyword_store, record_store)
        visual_indexer = VisualIndexer(clip_model, vector_store, record_store)
        visual_search = VisualSearchService(clip_model, vector_store, record_store)

        # --- 1. Discovery + routing through the real folder scan ---
        discovered = {p.name for p in discover_files([str(photos_dir)], extensions=IMAGE_EXTENSIONS)}
        assert discovered == set(fixtures) | {"app_icon.png", "red_circle_copy.avif"}, f"Image discovery missed files: {discovered}"

        indexed_count = index_folder(indexer, str(photos_dir), visual_indexer=visual_indexer)
        assert indexed_count == 6, f"Expected 6 files scanned (3 photos + icon + avif + 1 text), got {indexed_count}"
        all_vectors = visual_search.search("a red circle", top_k=10, apply_cutoff=False)
        names = {r["filename"] for r in all_vectors}
        assert "app_icon.png" not in names, "32x32 icon must not be embedded as a photo"
        assert "red_circle_copy.avif" in names, ".avif must be indexed (Pillow decodes it natively)"
        print("1. Discovery + folder scan routes photos to CLIP and text to the text pipeline; tiny icon skipped, .avif indexed: OK")

        # --- 2. Each description finds its own image ---
        expectations = {
            "a red circle": "red_circle.png",
            "a blue square": "blue_square.png",
            "an orange sunset gradient": "sunset_gradient.png",
        }
        for query, expected in expectations.items():
            results = visual_search.search(query, top_k=5)
            assert results, f"Visual search returned nothing for {query!r}"
            # The .avif is the same red circle, so it may legitimately tie for first.
            if results[0]["filename"] == "red_circle_copy.avif":
                results = results[1:]
            assert results[0]["filename"] == expected, (
                f"Query {query!r} should rank {expected} first, got "
                f"{[(r['filename'], round(r['score'], 4)) for r in results]}"
            )
        print("2. Each typed description ranks its own photo first: OK")

        # --- 3. The text file never leaks into visual results ---
        all_visual = visual_search.search("a red circle", top_k=10, apply_cutoff=False)
        assert all(r["filename"] != "notes.txt" for r in all_visual), (
            "A text file must never appear in visual search results"
        )
        print("3. Text files never appear in visual search results: OK")

        # --- 3b. Query spelling check (2026-09-19): CLIP embeds gibberish
        # and returns "strong" matches for it, so photo queries are checked
        # against the English word list + the user's own file names. ---
        if DEFAULT_WORDLIST_PATH.exists():
            checked = VisualSearchService(clip_model, vector_store, record_store, dictionary=Dictionary(), keyword_store=keyword_store)
            typo = checked.check_query("a rde cricle")
            assert typo.text == "a red circle" and not typo.unrecognized, typo
            assert checked.search(typo.text, top_k=5)[0]["filename"] in ("red_circle.png", "red_circle_copy.avif")
            assert checked.suggest("a rde cricle") == "a red circle"
            assert checked.suggest("a red circle") is None
            gibberish = checked.check_query("xqzvb")
            assert gibberish.unrecognized == ["xqzvb"], gibberish
            # A word in no dictionary but in the user's own file names is accepted.
            assert not checked.check_query("sunset_gradient notes").unrecognized
            # Real-but-absent words are not "corrected" into something else.
            assert checked.check_query("giraffe").text == "giraffe"
            print("3b. Photo-query spelling: typos corrected, gibberish flagged, the user's own names accepted: OK")
        else:
            print("3b. Photo-query spelling: SKIPPED (data/english_words.txt missing — run scripts/download_wordlist.py)")

        # --- 4. Unrelated query doesn't produce a confident match ---
        # Asserted as a RELATIVE comparison rather than "returns nothing":
        # an absolute cutoff is a hand-set heuristic (see
        # VISUAL_RELEVANCE_CUTOFF) that can't be honestly tuned against
        # flat synthetic shapes, but "an unrelated query lands further
        # away than a real match" is the property that actually matters
        # and holds regardless of where the cutoff sits.
        unrelated = "a black and white checkerboard pattern"
        raw = visual_search.search(unrelated, top_k=5, apply_cutoff=False)
        filtered = visual_search.search(unrelated, top_k=5)
        closest_real = min(
            r["score"] for r in visual_search.search("a red circle", top_k=5, apply_cutoff=False)
        )
        closest_unrelated = min(r["score"] for r in raw)
        assert closest_unrelated > closest_real, (
            "An unrelated query should sit further away than a real match "
            f"(closest unrelated={closest_unrelated:.4f}, closest real={closest_real:.4f})"
        )
        print(
            f"4. Unrelated query stays measurably further away (closest real={closest_real:.4f}, "
            f"closest unrelated={closest_unrelated:.4f}; cutoff keeps {len(filtered)}/{len(raw)}): OK"
        )

        # --- 5. Capture date + kind metadata ---
        result = visual_search.search("a red circle", top_k=1)[0]
        assert result["kind"] == "photo", f"Expected kind=photo, got {result['kind']}"
        assert result["captured_at"], "captured_at should fall back to filesystem mtime"
        assert result["timestamp_offset_seconds"] is None, "Photos have no timestamp offset"
        print(f"5. Photo metadata (kind, captured_at={result['captured_at'][:19]}): OK")

        # --- 6. Thumbnail generation ---
        # Asserts dimensions and format, deliberately NOT byte size: a
        # flat-colour PNG fixture compresses better than any JPEG of it,
        # so a size comparison would fail here while being trivially true
        # for the real photographs this actually serves.
        thumb = generate_thumbnail(photos_dir / "red_circle.png", max_size=64)
        assert thumb.startswith(b"\xff\xd8\xff"), "Thumbnail should be a JPEG"
        with Image.open(io.BytesIO(thumb)) as thumb_image:
            assert max(thumb_image.size) <= 64, f"Thumbnail not resized: {thumb_image.size}"
            assert thumb_image.size == (64, 64), f"Square source should stay square: {thumb_image.size}"
        print("6. Thumbnail renders as a valid, correctly-resized JPEG: OK")

        # --- 7. Delete removes a photo from the visual index ---
        red_record = record_store.get_by_path(str(photos_dir / "red_circle.png"))
        visual_indexer.delete_file(red_record.file_id)
        after_delete = visual_search.search("a red circle", top_k=10, apply_cutoff=False)
        assert all(r["filename"] != "red_circle.png" for r in after_delete), (
            "Deleted photo still appears in visual search"
        )
        print("7. Deleting a photo removes it from the visual index: OK")

        # --- 8. Tombstone cleanup sweeps photos too ---
        blue_path = str(photos_dir / "blue_square.png")
        record_store.mark_deleted(blue_path)
        cleaned = cleanup_tombstones(indexer, visual_indexer=visual_indexer)
        assert cleaned == 1, f"Expected 1 tombstoned file cleaned, got {cleaned}"
        remaining = visual_search.search("a blue square", top_k=10, apply_cutoff=False)
        assert all(r["filename"] != "blue_square.png" for r in remaining), (
            "Tombstone cleanup left the photo's vectors behind"
        )
        print("8. Tombstone cleanup purges photo vectors too: OK")

        # --- 9. Video (Phase 8b): keyframes indexed with timestamps, one
        # result per video at its best-matching moment, thumbnail at that time ---
        videos_dir = workdir / "videos"
        videos_dir.mkdir()
        make_two_scene_video(videos_dir / "clip.mp4")
        (videos_dir / "notes2.txt").write_text("unrelated words about cooking")
        assert {p.name for p in discover_files([str(videos_dir)], extensions=VIDEO_EXTENSIONS)} == {"clip.mp4"}
        scanned = index_folder(indexer, str(videos_dir), visual_indexer=visual_indexer)
        assert scanned == 2, f"Expected the video and the text file to be scanned, got {scanned}"
        video_record = record_store.get_by_path(str(videos_dir / "clip.mp4"))
        frames = [r for r in vector_store.db.open_table(IMAGES_TABLE).search().limit(1000).to_list() if r["file_id"] == video_record.file_id]
        assert len(frames) >= 4, f"Expected several keyframes for a 6-second clip, got {len(frames)}"
        stamps = sorted(json.loads(r["payload"])["timestamp_offset_seconds"] for r in frames)
        assert stamps[0] < 1.0 and stamps[-1] > 4.0, f"Keyframes should span the clip: {stamps}"

        for query, lo, hi in (("a red circle", 0.0, 3.0), ("a blue square", 3.0, 6.5)):
            hits = visual_search.search(query, top_k=10, apply_cutoff=False)
            video_hits = [h for h in hits if h["filename"] == "clip.mp4"]
            assert len(video_hits) == 1, f"A video must appear once, at its best moment; got {len(video_hits)} for {query!r}"
            t = video_hits[0]["timestamp_offset_seconds"]
            assert video_hits[0]["kind"] == "video" and lo <= t < hi, f"{query!r} matched the wrong moment: {t}s"
        # Filmstrip: the moments offered to confirm a hit must all be from the
        # matching scene — never a slot filled with a non-matching frame.
        red_hit = next(h for h in visual_search.search("a red circle", top_k=10, apply_cutoff=False) if h["filename"] == "clip.mp4")
        assert red_hit["moments"] and all(m["t"] < 3.0 for m in red_hit["moments"]), f"Only red-scene moments belong in the strip: {red_hit['moments']}"
        stamps_m = [m["t"] for m in red_hit["moments"]]
        assert all(abs(a - b) >= 3.0 for i, a in enumerate(stamps_m) for b in stamps_m[i + 1:]), f"Moments must be distinct scenes: {stamps_m}"
        assert next(h for h in visual_search.search("a red circle", top_k=10, apply_cutoff=False) if h["filename"] == "red_circle_copy.avif")["moments"] is None, "Photos carry no filmstrip"
        # The thumbnail at the blue moment is blue, not red.
        blue_t = next(h for h in visual_search.search("a blue square", top_k=10, apply_cutoff=False) if h["filename"] == "clip.mp4")["timestamp_offset_seconds"]
        with Image.open(io.BytesIO(generate_thumbnail(videos_dir / "clip.mp4", timestamp_offset_seconds=blue_t, max_size=64))) as th:
            r, g, b = th.convert("RGB").getpixel((th.width // 2, th.height // 2))
            assert b > 150 and r < 100, f"Thumbnail at {blue_t}s should show the blue scene, got rgb {(r, g, b)}"
        visual_indexer.delete_file(video_record.file_id)
        assert all(h["filename"] != "clip.mp4" for h in visual_search.search("a red circle", top_k=10, apply_cutoff=False))
        print(f"9. Video: {len(frames)} keyframes indexed with timestamps, each description finds the right moment, one card per video, frame thumbnail at that time, delete purges all frames: OK")

        print(
            "\nPhase 8 visual search OK: photos are discovered and routed to CLIP, typed "
            "descriptions find the right image, unrelated queries stay further away, metadata "
            "and thumbnails work, and deletion/cleanup remove photo vectors."
        )
        record_store.close()
        keyword_store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
