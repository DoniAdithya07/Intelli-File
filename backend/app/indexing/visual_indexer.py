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
import logging
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

logger = logging.getLogger(__name__)

# Video (Phase 8b): only the codec's own keyframes are decoded — the
# encoder already placed one at every scene change or every few seconds,
# and skipping the frames in between is 10-50x faster than decoding them
# all. A long recording is then sampled evenly down to this many, so one
# film can't dominate the index or the indexing time (CLIP ~40 ms/frame).
MAX_VIDEO_KEYFRAMES = 120

# Keyframes are shrunk to this longest side while decoding; CLIP and the
# blank-frame check both work at 224 px. Before (2026-10-04) every keyframe was
# kept at full size until the whole file was read: a 4K phone clip with a
# keyframe a second held 1.8 GB per minute of video, so a few minutes of
# phone footage ran a laptop out of memory and the video indexed as
# nothing. Must stay >= MIN_IMAGE_LONGEST_SIDE, so the too-small check
# still sees a small video's real size.
VIDEO_FRAME_MAX_SIDE = 448

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
        """(Re-)index one photo. Returns 1 on success, 0 if the file is not
        a decodable image or is too small to be a photo (see
        MIN_IMAGE_LONGEST_SIDE). Replaces any previous record for this
        file_id — after the new embedding exists, so a file that is
        momentarily unreadable keeps its old one (2026-09-21).

        Anything else RAISES, so the scan shows the file as failed and
        retries it: a locked or vanished file (OSError with an errno), an
        image over the pixel cap (extraction/__init__.py), running out of
        memory, a CLIP/ONNX error. Until 2026-10-04 every exception returned
        0 here, which marked the photo indexed with nothing searchable —
        silently, and never retried (the same fix as index_video_file's)."""
        try:
            with Image.open(path) as opened:
                # JPEG only (a no-op otherwise): decode at a reduced scale that
                # still exceeds CLIP's 224 px, so a 108/200 MP phone photo costs
                # a few MB instead of 300-600 MB (2026-10-05).
                opened.draft("RGB", (1024, 1024))
                # Phones record orientation as EXIF metadata rather than
                # rotating the pixels, so without this a portrait photo
                # reaches CLIP sideways and embeds as the wrong thing.
                image = ImageOps.exif_transpose(opened)
                image.load()
        except OSError as e:
            # Pillow reports "not an image" (UnidentifiedImageError) and
            # "truncated / broken data" as OSError WITHOUT an errno; the
            # operating system's errors (locked, permission, gone) carry one.
            if e.errno is not None:
                raise
            return 0
        except (SyntaxError, ValueError, EOFError):
            return 0  # other ways Pillow's decoders say "damaged file"
        width, height = image.size
        if max(width, height) < MIN_IMAGE_LONGEST_SIDE:
            return 0
        vector = self.clip_model.embed_images([image])[0]

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
        frames embedded; 0 if the video is too small or all blank.

        A file that can't be decoded RAISES, so the scan shows it as failed
        on the Status screen and leaves it un-indexed for a later retry.
        Until 2026-10-04 it returned 0 here, which marked any undecodable
        video (or one that ran out of memory) as indexed with nothing
        searchable — silently, and never retried."""
        keyframes = extract_keyframes(path)
        frames = _usable_frames(keyframes)
        # A video too small to be a photo stays too small between keyframes:
        # no fallback pass for it (2026-10-05).
        if not frames and not (keyframes and all(max(f.size) < MIN_IMAGE_LONGEST_SIDE for _, f in keyframes)):
            # Screen recordings and short clips can have ONE keyframe for
            # the whole file; when it is a black fade-in nothing is left.
            # The frames between keyframes still show the content.
            frames = _usable_frames(extract_keyframes(path, every_seconds=FALLBACK_SAMPLE_SECONDS))
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


def _usable_frames(frames: list[tuple[float, Image.Image]]) -> list[tuple[float, Image.Image]]:
    return [(t, f) for t, f in frames if max(f.size) >= MIN_IMAGE_LONGEST_SIDE and not is_blank_frame(f)]


# The long-GOP fallback (see index_video_file) keeps one frame per this many seconds.
FALLBACK_SAMPLE_SECONDS = 1.0


def frame_to_image(frame, max_side: int | None = None) -> Image.Image:
    """A decoded frame as an upright RGB image, optionally shrunk so its
    longest side is at most `max_side` — in ffmpeg's scaler, so the
    full-size picture never exists in Python memory. Phones store portrait
    video as sideways pixels plus a display-rotation tag (the video twin of
    EXIF orientation); without applying it CLIP sees the scene on its side."""
    width, height = frame.width, frame.height
    if max_side is not None and max(width, height) > max_side:
        scale = max_side / max(width, height)
        frame = frame.reformat(width=max(1, round(width * scale)), height=max(1, round(height * scale)), format="rgb24", interpolation="AREA")
    image = frame.to_image()
    if frame.rotation % 360:  # degrees counter-clockwise, as PIL's rotate() takes them
        image = image.rotate(frame.rotation, expand=True)
    return image


def _start_seconds(stream) -> float:
    """Where the stream's clock starts. MPEG program and transport streams
    (.mpg, .mts, .m2ts) start at 0.5-1.4 s or later, not 0; players count
    from that start, so stored moments do too (2026-10-05: a .mpg's blue
    square at 0:03 was reported at 0:03.5)."""
    return float(stream.start_time * stream.time_base) if stream.start_time is not None else 0.0


def extract_keyframes(path: Path, max_frames: int = MAX_VIDEO_KEYFRAMES, every_seconds: float | None = None) -> list[tuple[float, Image.Image]]:
    """(timestamp_seconds, PIL image) for the video's keyframes, evenly
    sampled down to max_frames. Decoding only keyframes is what makes this
    cheap; PyAV/ffmpeg drops the rest before they reach the decoder.
    With `every_seconds`, every frame is decoded instead and one kept per
    that many seconds (the long-GOP fallback).

    Memory stays bounded however long the file is: frames are shrunk to
    VIDEO_FRAME_MAX_SIDE as they are decoded, and when 2 x max_frames are
    held every other one is dropped (and from then on only every other
    candidate is kept), so a two-hour film never holds more than that.
    Raises if the file can't be opened or decoded; a stream that breaks
    part-way keeps the frames read so far."""
    import av  # lazy: see audio_io.py

    kept: list[tuple[float, Image.Image]] = []
    with av.open(str(path)) as container:
        stream = next((s for s in container.streams if s.type == "video"), None)
        if stream is None:
            return []
        stream.thread_type = "AUTO"
        start = _start_seconds(stream)
        duration = _duration_seconds(container, stream)
        if every_seconds is not None and duration:
            return _sampled_frames(path, container, stream, start, duration, every_seconds, max_frames)
        if every_seconds is None:
            stream.codec_context.skip_frame = "NONKEY"
        stride, candidates, last_time = 1, 0, None
        try:
            for frame in container.decode(stream):
                if frame.time is None:
                    continue
                t = frame.time - start
                if every_seconds is not None and last_time is not None and t < last_time + every_seconds:
                    continue
                last_time = t
                candidates += 1
                if (candidates - 1) % stride:
                    continue
                kept.append((float(t), frame_to_image(frame, VIDEO_FRAME_MAX_SIDE)))
                if len(kept) >= 2 * max_frames:
                    kept, stride = kept[::2], stride * 2
        except av.error.FFmpegError:
            if not kept:
                raise
            logger.warning("Video %s is damaged after %.1f s; indexing the part before it", path, kept[-1][0], exc_info=True)
    if len(kept) > max_frames:
        step = len(kept) / max_frames
        kept = [kept[int(i * step)] for i in range(max_frames)]
    return kept


def _duration_seconds(container, stream) -> float | None:
    if stream.duration is not None:
        return float(stream.duration * stream.time_base)
    if container.duration is not None:
        return container.duration / 1_000_000  # AV_TIME_BASE
    return None


# Seek only when the next sample is further ahead than this; closer ones are
# reached by decoding on.
FALLBACK_SEEK_GAP_SECONDS = 2.0


def _sampled_frames(path: Path, container, stream, start: float, duration: float, every_seconds: float, max_frames: int) -> list[tuple[float, Image.Image]]:
    """The long-GOP fallback (2026-10-05): one frame per `every_seconds`, at
    most `max_frames` spread over the whole video, reached by SEEKING to
    each sample time. Before, every frame of the file was decoded: a two-hour
    film whose keyframes are all dark decoded 180,000 frames for 120 kept.
    A file with one keyframe (a screen recording) cannot be seeked into: the
    first seek that lands back where decoding already was ends the seeking,
    and the rest is decoded straight through (at most twice the file)."""
    import av

    step = max(every_seconds, duration / max_frames)
    targets = [i * step for i in range(max_frames) if i * step < duration]
    kept: list[tuple[float, Image.Image]] = []
    frames, last, seeking = None, -1.0, True
    try:
        for target in targets:
            jumped = False
            if frames is None or (seeking and target - last > FALLBACK_SEEK_GAP_SECONDS):
                container.seek(int((target + start) / stream.time_base), stream=stream, backward=True, any_frame=False)
                frames = (f for f in container.decode(stream) if f.time is not None)
                jumped = True
            for frame in frames:
                t = frame.time - start
                if jumped:
                    jumped = False
                    if t <= last:  # landed where decoding already was: one long GOP
                        seeking = False
                last = max(last, t)
                if t >= target - 0.01:
                    kept.append((float(t), frame_to_image(frame, VIDEO_FRAME_MAX_SIDE)))
                    break
            else:
                break  # end of the stream
    except av.error.FFmpegError:
        if not kept:
            raise
        logger.warning("Video %s is damaged after %.1f s; indexing the part before it", path, kept[-1][0], exc_info=True)
    return kept


def decode_frame_at(path: Path, seconds: float) -> Image.Image:
    """The frame at (or just after) `seconds` from the video's start, for a
    video thumbnail."""
    import av  # lazy: see audio_io.py

    with av.open(str(path)) as container:
        stream = next(s for s in container.streams if s.type == "video")
        start = _start_seconds(stream)
        target = seconds + start
        # MPEG program/transport streams (.mpg, .mts) seek by byte position
        # and land on a keyframe AFTER the target, or past the end for the
        # last one (2026-10-05): seek earlier until the first frame isn't late.
        for back in (0.0, 1.0, 4.0, 16.0, 64.0):
            seek_to = max(target - back, start)
            can_go_earlier = seek_to > start and back < 64.0
            container.seek(int(seek_to / stream.time_base), stream=stream, backward=True, any_frame=False)
            frames = (f for f in container.decode(stream) if f.time is not None)
            for i, frame in enumerate(frames):
                if i == 0 and frame.time > target + 0.01 and can_go_earlier:
                    break
                if frame.time >= target - 0.01:
                    return frame_to_image(frame)
            if not can_go_earlier:
                break
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
        logger.debug("No recording date in %s; using the file's date", path, exc_info=True)
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat()
