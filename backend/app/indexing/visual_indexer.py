"""Photo indexing: image -> CLIP vision embedding -> its own vector table.

A sibling to Indexer rather than a method on it, because the two share
almost nothing: text files get extracted, chunked, embedded AND written
to the BM25 keyword index, while a photo has no text at all — there's
nothing to chunk and nothing for BM25 to match on. Keeping them separate
means neither pipeline grows branches for a case it doesn't handle.

The vector table is separate too (different model, different dimension:
CLIP's 512 vs MiniLM's 384), which the storage layer already supported
without changes — every VectorStore method takes a table name.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from ..embeddings.clip_model import ClipModel
from ..extraction.image_extractor import extract_captured_at
from ..files.discovery import IMAGE_EXTENSIONS, VISUAL_EXTENSIONS
from ..storage.lancedb_store import LanceDBVectorStore
from ..storage.sqlite_store import FileRecordStore

IMAGES_TABLE = "images"

# Video (Phase 8b): only the codec's own keyframes are decoded — the
# encoder already placed one at every scene change or every few seconds,
# and skipping the frames in between is 10-50x faster than decoding them
# all. A long recording is then sampled evenly down to this many, so one
# film can't dominate the index or the indexing time (CLIP ~40 ms/frame).
MAX_VIDEO_KEYFRAMES = 120

# Images whose longest side is below this are skipped. Found via live
# test (2026-09-11): indexing a project folder swept in the desktop app's
# own icon set (30x30 ... 310x310 PNGs), which then filled the results
# for vague queries like "anime" — the index-pollution bug class again,
# in image form. A directory-name exclusion is the wrong tool here (an
# "icons" folder can hold real pictures; app assets can live anywhere),
# but no photo anyone would search by description is this small, while
# icons, favicons, emoji, UI sprites and thumbnails almost always are.
# Deliberately below common "small" real images (WhatsApp forwards
# ~800px, web images ~500px, the IIIT logo tested at 500x154).
MIN_IMAGE_LONGEST_SIDE = 320


class VisualIndexer:
    def __init__(
        self,
        clip_model: ClipModel,
        vector_store: LanceDBVectorStore,
        file_record_store: FileRecordStore,
    ):
        self.clip_model = clip_model
        self.vector_store = vector_store
        self.file_record_store = file_record_store
        self.vector_store.create_table(IMAGES_TABLE, dimension=clip_model.dimension)

    def reset_if_model_changed(self, config_dir: Path) -> bool:
        """Drop every photo embedding if the CLIP variant differs from the
        one that produced them. Two CLIP exports of the same width look
        interchangeable to the vector store but are NOT comparable — after
        the 2026-09-11 switch from ViT-B/32 to ViT-B/16, stale B/32 vectors
        would have silently scored garbage against B/16 queries. Photo file
        records are removed too, so the next folder scan (startup rescan of
        watched folders, or the user re-picking a folder) re-embeds them.
        Returns True if a reset happened."""
        marker = config_dir / "visual_model.json"
        previous = None
        if marker.exists():
            try:
                previous = json.loads(marker.read_text()).get("model_id")
            except (OSError, ValueError):
                previous = None
        if previous == self.clip_model.model_id:
            return False
        # No marker but an already-populated table = vectors of unknown
        # provenance (indexes built before this check existed) — reset too.
        changed = previous is not None or self._image_vector_count() > 0
        if changed:
            self.vector_store.drop_table(IMAGES_TABLE)
            self.vector_store.create_table(IMAGES_TABLE, dimension=self.clip_model.dimension)
            for record in self.file_record_store.list_active():
                if Path(record.path).suffix.lower() in VISUAL_EXTENSIONS:
                    self.file_record_store.remove(record.file_id)
        marker.write_text(json.dumps({"model_id": self.clip_model.model_id, "dimension": self.clip_model.dimension}))
        return changed

    def _image_vector_count(self) -> int:
        try:
            return self.vector_store.db.open_table(IMAGES_TABLE).count_rows()
        except Exception:
            return 0

    def index_image_file(self, path: Path, file_id: str, file_hash: str) -> int:
        """(Re-)index one photo. Returns 1 on success, 0 if the file
        couldn't be decoded as an image or is too small to be a photo
        (see MIN_IMAGE_LONGEST_SIDE). Replaces any previous record for
        this file_id — after the new embedding exists, so a file that is
        momentarily unreadable keeps its old one (2026-09-21)."""
        try:
            with Image.open(path) as opened:
                # Phones record orientation as EXIF metadata rather than
                # rotating the pixels, so without this a portrait photo
                # reaches CLIP sideways and embeds as the wrong thing.
                image = ImageOps.exif_transpose(opened)
                image.load()
                width, height = image.size
                if max(width, height) < MIN_IMAGE_LONGEST_SIDE:
                    return 0
                vector = self.clip_model.embed_images([image])[0]
        except Exception:
            # A corrupt/truncated/not-really-an-image file must not kill
            # the indexing job, per the PRD's Reliability section.
            return 0

        self.delete_file(file_id)
        self.vector_store.upsert(
            IMAGES_TABLE,
            [
                {
                    "id": str(uuid.uuid4()),
                    "file_id": file_id,
                    "vector": vector.tolist(),
                    "payload": {
                        "kind": "photo",
                        "path": str(path),
                        "captured_at": extract_captured_at(path),
                        # Populated by video keyframes in Phase 8b; always
                        # None for a still photo.
                        "timestamp_offset_seconds": None,
                        "width": width,
                        "height": height,
                    },
                }
            ],
        )
        return 1

    def index_video_file(self, path: Path, file_id: str, file_hash: str) -> int:
        """(Re-)index one video as its keyframes: one CLIP vector per kept
        keyframe, each carrying its timestamp. Returns the number of
        frames embedded; 0 if the file can't be decoded or is too small."""
        try:
            frames = extract_keyframes(path)
        except Exception:
            return 0
        frames = [(t, f) for t, f in frames if max(f.size) >= MIN_IMAGE_LONGEST_SIDE and not is_blank_frame(f)]
        if not frames:
            return 0
        captured_at = video_captured_at(path)
        vectors = self.clip_model.embed_images([f for _, f in frames])
        self.delete_file(file_id)
        self.vector_store.upsert(
            IMAGES_TABLE,
            [
                {
                    "id": str(uuid.uuid4()),
                    "file_id": file_id,
                    "vector": vector.tolist(),
                    "payload": {
                        "kind": "video",
                        "path": str(path),
                        "captured_at": captured_at,
                        "timestamp_offset_seconds": round(t, 2),
                        "width": frame.size[0],
                        "height": frame.size[1],
                    },
                }
                for (t, frame), vector in zip(frames, vectors)
            ],
        )
        return len(frames)

    def index_visual_file(self, path: Path, file_id: str, file_hash: str) -> int:
        """Route a photo or a video to the right method (callers only know it's visual)."""
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            return self.index_image_file(path, file_id, file_hash)
        return self.index_video_file(path, file_id, file_hash)

    def delete_file(self, file_id: str) -> None:
        self.vector_store.delete_by_file_id(IMAGES_TABLE, file_id)


# A fade-to-black keyframe is the visual twin of gibberish text: CLIP embeds
# it to a generic point ~1.50 from *every* query, which is inside the
# strong tier. Found 2026-09-19 with real Commons footage: "a steam train"
# returned a city-traffic clip via its fade-in frame (0.0-0.4 s, luminance
# 99th percentile 0-29), and a fireworks clip's near-black frames matched
# "a cat" and "a red circle" alike. Measured at CLIP's working resolution:
# fade frames have p99 < 30 and nothing bright (max <= 41); dark-but-real
# frames (a firework burst) keep bright pixels (max 89-122). Both conditions
# are required so a dark scene with content is never thrown away.
BLANK_FRAME_P99 = 16
BLANK_FRAME_MAX = 64


def is_blank_frame(image: Image.Image) -> bool:
    gray = np.asarray(image.convert("L").resize((224, 126)), dtype=np.float32)
    return float(np.percentile(gray, 99)) < BLANK_FRAME_P99 and float(gray.max()) < BLANK_FRAME_MAX


def extract_keyframes(path: Path, max_frames: int = MAX_VIDEO_KEYFRAMES) -> list[tuple[float, Image.Image]]:
    """(timestamp_seconds, PIL image) for the video's keyframes, evenly
    sampled down to max_frames. Decoding only keyframes is what makes this
    cheap; PyAV/ffmpeg drops the rest before they reach the decoder."""
    import av  # lazy: see audio_io.py

    with av.open(str(path)) as container:
        stream = next((s for s in container.streams if s.type == "video"), None)
        if stream is None:
            return []
        stream.thread_type = "AUTO"
        stream.codec_context.skip_frame = "NONKEY"
        keyframes: list[tuple[float, Image.Image]] = []
        for frame in container.decode(stream):
            if frame.time is None:
                continue
            keyframes.append((float(frame.time), frame.to_image()))
    if len(keyframes) > max_frames:
        step = len(keyframes) / max_frames
        keyframes = [keyframes[int(i * step)] for i in range(max_frames)]
    return keyframes


def decode_frame_at(path: Path, seconds: float) -> Image.Image:
    """The frame at (or just after) `seconds`, for a video thumbnail."""
    import av  # lazy: see audio_io.py

    with av.open(str(path)) as container:
        stream = next(s for s in container.streams if s.type == "video")
        container.seek(int(seconds / stream.time_base), stream=stream, backward=True, any_frame=False)
        for frame in container.decode(stream):
            if frame.time is not None and frame.time >= seconds - 0.01:
                return frame.to_image()
        raise ValueError("no frame at that time")


def video_captured_at(path: Path) -> str | None:
    """Recording time from the container's own metadata, else file mtime —
    the video counterpart of extract_captured_at()'s EXIF fallback."""
    import av  # lazy: see audio_io.py

    try:
        with av.open(str(path)) as container:
            stamp = container.metadata.get("creation_time")
            if stamp:
                return datetime.fromisoformat(stamp.replace("Z", "+00:00")).replace(tzinfo=None).isoformat()
    except Exception:
        pass
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat()
