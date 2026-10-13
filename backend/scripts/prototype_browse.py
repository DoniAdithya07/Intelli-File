"""Regression for the 2026-10-12 audio fixes and the read-only /browse API.

Audio:
  1. A looping Whisper transcript ("I am going to say that" x hundreds, found
     on a real MP3) and Whisper's filler on music are cleaned by plain-text
     rules (no model needed); the decoder stops a token loop early.
  2. The same rules through the real Transcriber: speech still comes through,
     a fake looping / no-speech window comes out empty, repeated windows collapse.
  3. An audio-only ".mpeg" (WhatsApp voice note) goes to the transcript
     pipeline, a real .mpeg video stays a video; found by type:audio, no
     thumbnail error; an old record from before the probe is fixed in place.
  4. The Audio tab finds a song by file name and by its folder's name (a
     document in the same folder is not matched by folder), and an empty Audio
     search carries a hint.
  5. "Search what is said in videos": off by default (nothing read); on, the
     soundtrack of an unchanged video is read once, without redoing its pictures.

Browse API (in-process, on a throwaway folder):
  6. /browse/drives, /browse/quick-access, /browse/list (order, hidden and
     system files left out, paging, indexed flag, kinds), /browse/recent.
  7. Refusals: system folders, .., UNC and \\\\?\\ paths, alternate data
     streams, reserved names, relative paths, a junction pointing into
     Windows, access switched off.
  8. Against the real backend: token and Host guard on /browse/*, audio hint on /search.

Run with:  backend\\venv\\Scripts\\python.exe backend\\scripts\\prototype_browse.py
"""

import io
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
# Scratch files stay inside the project (drive D), never in the system temp folder.
LOCAL_TMP = BACKEND.parent / ".local" / "tmp"
LOCAL_TMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(LOCAL_TMP)
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import av  # noqa: E402
import numpy as np  # noqa: E402
import requests  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

import speech_synth  # noqa: E402
from app.context import Settings, UsageStore, windows_recent  # noqa: E402
from app.embeddings.clip_model import ClipModel  # noqa: E402
from app.embeddings.model import EmbeddingModel, default_model_dir  # noqa: E402
from app.files import browsing  # noqa: E402
from app.files.identity import build_file_record  # noqa: E402
from app.files.media import has_audio_stream, is_audio_only_media  # noqa: E402
from app.indexing import Indexer, VisualIndexer, index_folder  # noqa: E402
from app.indexing.folder_scan import RECENT_FAILURES  # noqa: E402
from app.routes import browse as browse_routes  # noqa: E402
from app.routes import files as files_routes  # noqa: E402
from app.routes import search as search_routes  # noqa: E402
from app.search import SearchService  # noqa: E402
from app.storage import FileRecordStore, KeywordStore, LanceDBVectorStore  # noqa: E402
from app.thumbnails import NoThumbnail, generate_thumbnail  # noqa: E402
from app.transcription import Transcriber  # noqa: E402
from app.transcription import guard  # noqa: E402
from app.transcription.audio_io import load_audio_as_mono_16k  # noqa: E402
from app.transcription.whisper_onnx import _loop_tokens  # noqa: E402

TEXT_MODEL_DIR = default_model_dir(BACKEND / "models")
CLIP_MODEL_DIR = BACKEND / "models" / "clip-vit-base-patch16"
WHISPER_DIR = BACKEND / "models" / "whisper-base.en"
SPEECH_A = "Please remember the quarterly budget meeting on Thursday."
SPEECH_B = "The lighthouse keeper repaired the antenna before the storm."
SPEECH_C = "The submarine crew counted the tangerines carefully."
TOKEN = "browse-test-token-not-secret"


# ---------------------------------------------------------------- fixtures

def write_tone_wav(path: Path, seconds: float = 3.0, hertz: int = 440) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        samples = (8000 * np.sin(2 * np.pi * hertz * np.arange(int(16000 * seconds)) / 16000)).astype("<i2")
        w.writeframes(samples.tobytes())


def speech_wav(path: Path, text: str) -> None:
    speech_synth.synthesize_wav(text, path)


def _encode_audio(wav: Path, container, stream) -> None:
    resampler = av.AudioResampler(format="s16", layout="mono", rate=stream.rate)
    with av.open(str(wav)) as src:
        for frame in src.decode(audio=0):
            for resampled in resampler.resample(frame):
                for packet in stream.encode(resampled):
                    container.mux(packet)
    for packet in stream.encode(None):
        container.mux(packet)


def make_audio_only_mpeg(wav: Path, out: Path) -> None:
    """What WhatsApp saves for a voice note: an MPEG container, MP2/MP3 audio, no picture."""
    with av.open(str(out), mode="w", format="mpeg") as container:
        stream = container.add_stream("mp2", rate=44100)
        stream.layout = "mono"
        _encode_audio(wav, container, stream)


def make_video(out: Path, wav: Path | None = None, seconds: int = 2) -> None:
    """A small real video (a blue square, 25 fps), with a soundtrack when `wav` is given."""
    with av.open(str(out), mode="w", format="mpeg") as container:
        video = container.add_stream("mpeg2video", rate=25)
        video.width, video.height, video.pix_fmt = 320, 240, "yuv420p"
        video.gop_size = 12
        audio = None
        if wav is not None:
            audio = container.add_stream("mp2", rate=44100)
            audio.layout = "mono"
        image = Image.new("RGB", (320, 240), "white")
        image.paste("blue", (80, 60, 240, 180))
        for _ in range(25 * seconds):
            for packet in video.encode(av.VideoFrame.from_image(image)):
                container.mux(packet)
        for packet in video.encode():
            container.mux(packet)
        if audio is not None:
            _encode_audio(wav, container, audio)


# ---------------------------------------------------------------- 1. text rules

def check_guard_rules() -> None:
    loop = "I am going to say that " * 300
    assert guard.clean_window(loop) == "", "a window that is a loop must be dropped"
    real_loop = "I am going to take a little bit of a bath. Oh no! " + "I am going to take a bath. " * 40
    assert guard.clean_window(real_loop) == "", "the real MP3's loop must be dropped"
    assert guard.collapse_repeats(loop) == "I am going to say that"
    assert guard.collapse_repeats("the meeting is at noon " * 4 + "see you there") == "the meeting is at noon see you there"
    assert guard.collapse_repeats("no no no") == "no no no", "three of one word is speech"
    assert guard.collapse_repeats("no no no no no") == "no", "five of one word is not"
    refrain = "We should meet on Tuesday at noon at the harbour cafe to discuss the budget, la la la la la la, and then go for lunch together."
    kept = guard.clean_window(refrain)
    assert "budget" in kept and "la la" not in kept and "lunch" in kept, kept
    plain = "The quarterly budget meeting moved to Thursday afternoon because the finance team is travelling."
    assert guard.clean_window(plain) == plain, "ordinary speech is untouched"
    assert guard.clean_window("Thank you.") == "" and guard.clean_window("Thanks for watching!") == ""
    assert guard.clean_window("Thank you.", drop_filler=False) == "Thank you.", "a spoken query may say thank you"
    assert guard.clean_window("[Music]") == "" and guard.clean_window("♪ ♪ (upbeat music)") == ""
    assert guard.clean_window("Hello [Music] there") == "Hello there"
    assert guard.clean_window("") == ""
    assert _loop_tokens([5] * 7) == 0 and _loop_tokens([5] * 8) == 7
    assert _loop_tokens([1, 2, 3] * 3) == 0 and _loop_tokens([1, 2, 3] * 4) == 9
    assert _loop_tokens(list(range(40))) == 0
    print("1. Repetition guard (text rules): a looping window and filler on music are dropped, a refrain collapses, ordinary speech and a spoken 'thank you' are kept; decoder loop detector: OK")


# ---------------------------------------------------------------- 2. real transcriber

def check_transcriber(work: Path) -> Transcriber:
    transcriber = Transcriber(WHISPER_DIR)
    speech = work / "speech.wav"
    speech_wav(speech, SPEECH_A)
    heard = transcriber.transcribe(speech).lower()
    assert "budget" in heard and "thursday" in heard, heard
    heard_speech = heard

    real = transcriber.model.transcribe_window_scored

    def fake(text, no_speech=0.1, logprob=-0.3):
        return lambda audio, prompt_ids=None: (text, no_speech, logprob)

    try:
        transcriber.model.transcribe_window_scored = fake("I am going to say that " * 300)
        assert transcriber.transcribe(speech) == "", "a looping window must not become the transcript"
        transcriber.model.transcribe_window_scored = fake("Thank you.", 0.9, -1.5)
        assert transcriber.transcribe(speech) == "", "Whisper's own no-speech rule: probably silence AND low confidence"
        transcriber.model.transcribe_window_scored = fake("hello there my friend", 0.9, -0.2)
        assert transcriber.transcribe(speech) == "hello there my friend", "a confident window is kept even when no-speech was likely"
        # Several windows that each look fine but say the same thing (a chorus decoded the same way every 30 s).
        long_audio = work / "long.wav"
        with wave.open(str(speech)) as w:
            frames = w.readframes(w.getnframes())
        with wave.open(str(long_audio), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(frames * (80 * 16000 // (len(frames) // 2) + 1))
        transcriber.model.transcribe_window_scored = fake("Please remember the budget meeting.")
        assert transcriber.transcribe(long_audio) == "Please remember the budget meeting."
    finally:
        transcriber.model.transcribe_window_scored = real
    assert transcriber.transcribe(speech).lower() == heard_speech, "restored model gives the same words"

    # Only the start of a soundtrack is decoded.
    tone = work / "tone.wav"
    write_tone_wav(tone, 3.0)
    assert load_audio_as_mono_16k(tone).size >= 47000
    assert load_audio_as_mono_16k(tone, max_seconds=1.0).size == 16000
    music_like = work / "music.wav"
    write_tone_wav(music_like, 20.0, 330)
    words = transcriber.transcribe(music_like).split()
    assert len(words) <= 6, f"a pure tone must give little or no text, got {words}"
    print(f"2. Real Whisper: speech still transcribed ({heard_speech!r}); fake loop, fake no-speech window and repeated windows cleaned; a 20 s tone gave {len(words)} words; max_seconds cuts decoding: OK")
    return transcriber


# ---------------------------------------------------------------- 3-5. pipelines

def check_pipelines(work: Path, transcriber: Transcriber) -> None:
    media = work / "media"
    album = media / "album night drive"
    album.mkdir(parents=True)
    legacy_dir = work / "legacy"
    legacy_dir.mkdir()
    video_dir = work / "videos"
    video_dir.mkdir()

    wav_a, wav_b, wav_c = work / "a.wav", work / "b.wav", work / "c.wav"
    speech_wav(wav_a, SPEECH_A)
    speech_wav(wav_b, SPEECH_B)
    speech_wav(wav_c, SPEECH_C)
    voice_note = media / "WhatsApp Audio 2026-01-01 at 10.00.00 AM.mpeg"
    make_audio_only_mpeg(wav_a, voice_note)
    video = media / "holiday clip.mpeg"
    make_video(video)
    write_tone_wav(media / "Pilla Oh Picture Perfect.wav")
    write_tone_wav(album / "track01.wav", 3.0, 523)
    (album / "lyrics.txt").write_text("Words about gardening tools and compost heaps in the autumn.")
    legacy_note = legacy_dir / "WhatsApp Audio 2026-02-02 at 09.00.00 AM.mpeg"
    make_audio_only_mpeg(wav_b, legacy_note)
    video_speech = video_dir / "boat trip.mpeg"
    make_video(video_speech, wav_c)

    assert is_audio_only_media(voice_note) and not is_audio_only_media(video) and not is_audio_only_media(video_speech)
    assert not is_audio_only_media(media / "Pilla Oh Picture Perfect.wav"), "only video extensions are probed"
    assert has_audio_stream(video_speech) and not has_audio_stream(video)
    from av.stream import Disposition
    from app.files.media import _is_cover_art

    assert _is_cover_art(SimpleNamespace(disposition=Disposition.attached_pic)) and not _is_cover_art(SimpleNamespace(disposition=Disposition(0)))
    broken = media / "broken.mpeg"
    broken.write_bytes(os.urandom(2000))
    assert not is_audio_only_media(broken), "a file that cannot be opened stays a video"
    broken.unlink()
    print("   probe: voice-note .mpeg = audio only; real .mpeg video = video; cover art is not video; a damaged file stays a video")

    text_model = EmbeddingModel(TEXT_MODEL_DIR)
    clip_model = ClipModel(CLIP_MODEL_DIR)
    vector_store = LanceDBVectorStore(str(work / "vectors"))
    keyword_store = KeywordStore(work / "keyword.db")
    record_store = FileRecordStore(work / "files.db")
    indexer = Indexer(text_model, vector_store, keyword_store, record_store, transcriber=transcriber)
    visual_indexer = VisualIndexer(clip_model, vector_store, record_store)
    search = SearchService(text_model, vector_store, keyword_store, record_store)

    # A record from before the probe existed (indexed, never probed, no chunks): fixed in place by the scan.
    old = build_file_record(legacy_note, indexed=True)
    assert old.audio_only is None
    record_store.upsert(old)
    visual_calls: list[str] = []
    real_visual = visual_indexer.index_visual_file
    visual_indexer.index_visual_file = lambda path, *a, **k: (visual_calls.append(Path(path).name), real_visual(path, *a, **k))[1]

    RECENT_FAILURES.clear()
    index_folder(indexer, str(media), visual_indexer=visual_indexer)
    index_folder(indexer, str(legacy_dir), visual_indexer=visual_indexer)
    index_folder(indexer, str(video_dir), visual_indexer=visual_indexer)
    failed = {Path(f["path"]).name: f["error"] for f in RECENT_FAILURES}
    assert not failed, f"nothing should fail (the voice note used to fail with StopIteration): {failed}"

    note_record = record_store.get_by_path(str(voice_note))
    assert note_record.audio_only is True and note_record.indexed
    assert record_store.get_by_path(str(video)).audio_only is False
    assert voice_note.name not in visual_calls and video.name in visual_calls, visual_calls
    hits = search.search("type:audio quarterly budget meeting", top_k=5)
    assert hits and hits[0]["filename"] == voice_note.name, [h["filename"] for h in hits]
    assert voice_note.name not in [h["filename"] for h in search.search("type:video quarterly budget", top_k=5)]
    assert voice_note.name in [h["filename"] for h in search.search("type:audio whatsapp audio", top_k=5)], "its name finds it too"
    assert video.name in [h["filename"] for h in search.search("type:video holiday clip", top_k=5)]
    print("3. Audio-only .mpeg: indexed through the transcript pipeline, found by its spoken words under type:audio (and not type:video); the real .mpeg video still goes through keyframes: OK")

    legacy_record = record_store.get_by_path(str(legacy_note))
    assert legacy_record.audio_only is True and legacy_record.indexed
    hits = search.search("lighthouse keeper antenna", top_k=3)
    assert hits and hits[0]["filename"] == legacy_note.name, [h["filename"] for h in hits]
    print("   an old record (indexed before the probe, listed as a video): re-read as audio on the next scan, now searchable by speech: OK")

    try:
        generate_thumbnail(voice_note)
        raise AssertionError("an audio file has no thumbnail")
    except NoThumbnail:
        pass
    try:
        generate_thumbnail(media / "Pilla Oh Picture Perfect.wav")
        raise AssertionError("an audio file has no thumbnail")
    except NoThumbnail:
        pass
    assert Image.open(io.BytesIO(generate_thumbnail(video, 0.2, 96))).format == "JPEG"
    app = FastAPI()
    app.include_router(files_routes.router)
    app.state.indexer = SimpleNamespace(file_record_store=record_store)
    log_records: list[logging.LogRecord] = []
    handler = logging.Handler(logging.WARNING)
    handler.emit = log_records.append
    logging.getLogger("app.routes.files").addHandler(handler)
    try:
        client = TestClient(app)
        assert client.get("/thumbnail", params={"path": str(voice_note)}).status_code == 404
        assert client.get("/thumbnail", params={"path": str(media / "Pilla Oh Picture Perfect.wav")}).status_code == 404
        assert client.get("/thumbnail", params={"path": str(video), "t": 0.2, "size": 96}).status_code == 200
    finally:
        logging.getLogger("app.routes.files").removeHandler(handler)
    assert not log_records, [r.getMessage() for r in log_records]
    print("   thumbnails: audio gives a quiet 404 (no error, nothing logged), the video still renders: OK")

    # --- 4. the Audio tab: file name, folder name, hint ---
    def names(query: str) -> list[str]:
        return [h["filename"] for h in search.search(query, top_k=10)]

    assert names("type:audio pilla oh picture perfect")[:1] == ["Pilla Oh Picture Perfect.wav"], names("type:audio pilla oh picture perfect")
    assert "track01.wav" in names("type:audio night drive"), "found by its folder's name"
    assert "lyrics.txt" not in names("night drive"), "a document is not matched by its folder"
    assert "track01.wav" not in names("type:document night drive")
    api = FastAPI()
    api.include_router(search_routes.router)
    api.state.search_service = search
    api.state.settings = make_settings(work / "config")
    api.state.settings.update({"remember_activity": False})
    client = TestClient(api)
    empty = client.get("/search", params={"q": "type:audio kolaveri didi"}).json()
    assert empty["results"] == [] and empty["hint"] == search_routes.AUDIO_EMPTY_HINT and "English only" in empty["hint"], empty
    found = client.get("/search", params={"q": "type:audio pilla oh picture perfect"}).json()
    assert found["results"] and found["hint"] is None
    assert client.get("/search", params={"q": "kolaveri didi"}).json()["hint"] is None, "the hint is for the Audio tab"
    assert client.get("/search", params={"q": " "}).json()["hint"] is None
    print("4. Audio tab: a song is found by its file name and by its folder's name (documents are not matched by folder); an empty Audio search says 'Speech search understands English only; try the file name': OK")

    # --- 5. Search what is said in videos ---
    before = record_store.get_by_path(str(video_speech))
    assert before.audio_only is False and not before.speech_indexed
    assert "boat trip.mpeg" not in names("submarine tangerines"), "off by default: the soundtrack is not read"
    calls_before = len(visual_calls)
    index_folder(indexer, str(video_dir), visual_indexer=visual_indexer, video_speech=lambda: False)
    assert "boat trip.mpeg" not in names("submarine tangerines") and not record_store.get_by_path(str(video_speech)).speech_indexed
    index_folder(indexer, str(video_dir), visual_indexer=visual_indexer, video_speech=lambda: True)
    assert "boat trip.mpeg" in names("submarine tangerines"), names("submarine tangerines")
    assert record_store.get_by_path(str(video_speech)).speech_indexed
    assert len(visual_calls) == calls_before, "switching it on must not redo the pictures of an unchanged video"
    index_folder(indexer, str(video_dir), visual_indexer=visual_indexer, video_speech=lambda: True)
    assert len(visual_calls) == calls_before
    assert "holiday clip.mpeg" in [h["filename"] for h in search.search("type:video holiday", top_k=5)]
    print("5. Videos: soundtrack ignored by default and while the setting is off; switched on, an unchanged video's speech becomes searchable without redoing its pictures: OK")
    assert make_settings(work / "config2").get("index_video_speech") is False
    record_store.close()  # open SQLite files could not be deleted with the work folder
    keyword_store.close()


# ---------------------------------------------------------------- 6-7. browse API

def make_browse_app(settings, usage_store, record_store, roots, mode="limited") -> TestClient:
    app = FastAPI()
    app.include_router(browse_routes.router)
    app.state.access = SimpleNamespace(mode=mode)
    app.state.settings = settings
    app.state.usage_store = usage_store
    app.state.indexer = SimpleNamespace(file_record_store=record_store)
    app.state.live_indexing = SimpleNamespace(roots=lambda: list(roots), unreachable_roots=set(), _exists=lambda path, is_dir, default: Path(path).is_dir())
    return TestClient(app)


def make_settings(folder: Path) -> Settings:
    folder.mkdir(parents=True, exist_ok=True)
    return Settings(folder)


def set_attributes(path: Path, attributes: int) -> None:
    import ctypes

    assert ctypes.windll.kernel32.SetFileAttributesW(str(path), attributes), path


def check_browse_api(work: Path) -> None:
    root = work / "browse"
    (root / "A folder").mkdir(parents=True)
    (root / "Zeta").mkdir()
    for name in ("b.txt", "a.txt", "song.mp3", "voice.mpeg", "secret.txt", "sys.dat", "live.docx", "gone.txt"):
        (root / name).write_bytes(b"x" * 10)
    set_attributes(root / "secret.txt", 0x2)  # hidden
    set_attributes(root / "sys.dat", 0x4)     # system
    record_store = FileRecordStore(work / "browse_files.db")
    record_store.upsert(build_file_record(root / "voice.mpeg", file_hash="h1", indexed=True))
    record_store.set_audio_only(record_store.get_by_path(str(root / "voice.mpeg")).file_id, True)
    record_store.upsert(build_file_record(root / "a.txt", file_hash="h2", indexed=True))
    record_store.upsert(build_file_record(root / "b.txt", file_hash="h3", indexed=False))
    settings = make_settings(work / "browse_config")
    usage = UsageStore(work / "browse_usage.db")
    client = make_browse_app(settings, usage, record_store, roots=[str(root / "A folder")])

    drives = client.get("/browse/drives").json()["drives"]
    assert drives and {"letter", "path", "label", "type", "ready", "total_bytes", "free_bytes"} <= set(drives[0]), drives
    system_drive = next(d for d in drives if d["path"].upper().startswith(os.environ.get("SystemDrive", "C:").upper()))
    assert system_drive["type"] == "fixed" and system_drive["total_bytes"] > system_drive["free_bytes"] > 0, system_drive
    quick = client.get("/browse/quick-access").json()["items"]
    names = [i["name"] for i in quick]
    assert "Downloads" in names and "Documents" in names and all(Path(i["path"]).is_dir() for i in quick), quick
    pinned = [i for i in quick if i["kind"] == "pinned"]
    assert pinned == [{"name": "A folder", "path": str(root / "A folder"), "kind": "pinned", "icon": "folder"}], pinned
    print(f"6. /browse/drives ({len(drives)} drives, sizes) and /browse/quick-access ({names}, + the watched folder pinned): OK")

    listing = client.get("/browse/list", params={"path": str(root)}).json()
    got = [e["name"] for e in listing["entries"]]
    assert got == ["A folder", "Zeta", "a.txt", "b.txt", "gone.txt", "live.docx", "song.mp3", "voice.mpeg"], got
    assert listing["counts"] == {"total": 8, "folders": 2, "files": 6} and listing["has_more"] is False
    assert "secret.txt" not in got and "sys.dat" not in got, "hidden and system files are not listed"
    by_name = {e["name"]: e for e in listing["entries"]}
    assert by_name["voice.mpeg"]["kind"] == "audio" and by_name["voice.mpeg"]["indexed"] and by_name["voice.mpeg"]["file_id"]
    assert by_name["a.txt"]["indexed"] and by_name["b.txt"]["indexed"] is False and by_name["gone.txt"]["file_id"] is None
    assert by_name["song.mp3"]["kind"] == "audio" and by_name["live.docx"]["kind"] == "document" and by_name["A folder"]["kind"] == "folder"
    assert by_name["A folder"]["indexed"] is True and by_name["Zeta"]["indexed"] is False, "a folder is indexed when it is inside a watched folder"
    assert by_name["A folder"]["size"] is None and by_name["a.txt"]["size"] == 10 and by_name["a.txt"]["modified"] > 1e9
    assert listing["path"] == str(root) and listing["name"] == "browse" and listing["parent"] == str(work)
    page = client.get("/browse/list", params={"path": str(root), "limit": 3, "offset": 2}).json()
    assert [e["name"] for e in page["entries"]] == ["a.txt", "b.txt", "gone.txt"] and page["has_more"] and page["counts"]["total"] == 8
    assert client.get("/browse/list", params={"path": str(root), "limit": 0}).status_code == 422
    assert client.get("/browse/list", params={"path": str(root), "limit": 5000}).status_code == 422
    assert client.get("/browse/list", params={"path": str(root), "offset": -1}).status_code == 422
    drive_root = client.get("/browse/list", params={"path": system_drive["path"], "limit": 1000}).json()
    drive_names = {e["name"].lower() for e in drive_root["entries"]}
    assert drive_root["parent"] is None and not drive_names & {"windows", "programdata", "program files", "$recycle.bin", "system volume information"}, drive_names
    big = root / "big"
    big.mkdir()
    for i in range(1200):
        (big / f"f{i:04d}.txt").write_bytes(b"")
    t0 = time.perf_counter()
    listing = client.get("/browse/list", params={"path": str(big), "limit": 1000}).json()
    elapsed = time.perf_counter() - t0
    assert listing["counts"]["total"] == 1200 and len(listing["entries"]) == 1000 and listing["has_more"] and elapsed < 3, (listing["counts"], elapsed)
    print(f"   /browse/list: folders first, hidden/system left out, kinds, indexed flags, paging, system folders hidden at the drive root, 1,200 files listed in {elapsed:.2f} s: OK")

    now = time.time()
    usage.record("file_opened", path=str(root / "a.txt"), ts=now - 500)
    usage.record("file_opened", path=str(root / "a.txt"), ts=now - 100)   # the same file again: one row, the newer date
    usage.record("file_opened", path=str(root / "b.txt"), ts=now - 300)
    usage.record("file_opened", path=str(root / "gone.txt"), ts=now - 50)  # deleted below
    usage.record("file_opened", path=str(root / "Zeta"), ts=now - 40)      # a folder is not a recent file
    usage.record("file_opened", path=r"C:\Windows\System32\drivers\etc\hosts", ts=now - 30)  # a system file: never listed
    usage.record("query", query="not an open", ts=now - 20)
    usage.import_events("file_opened", [{"ts": now - 400, "path": str(root / "song.mp3")}], source=windows_recent.SOURCE)
    (root / "gone.txt").unlink()
    recent = client.get("/browse/recent").json()
    assert recent["windows_recent"] is False
    items = recent["items"]
    assert [i["name"] for i in items] == ["a.txt", "b.txt", "song.mp3"], [i["name"] for i in items]
    assert [i["activity"] for i in items] == ["Opened in IntelliFile", "Opened in IntelliFile", "Recently used"]
    assert abs(items[0]["accessed"] - (now - 100)) < 1 and items[0]["indexed"] and items[1]["indexed"] is False and items[0]["size"] == 10
    assert {"name", "path", "is_dir", "size", "modified", "accessed", "kind", "indexed", "file_id", "activity"} <= set(items[0])
    live_file = root / "live.docx"
    real_read_recent = windows_recent.read_recent
    windows_recent.read_recent = lambda folder=None: [(str(live_file), now - 10), (str(root / "a.txt"), now - 5), (str(root / "vanished.docx"), now - 1)]
    try:
        assert client.get("/browse/recent").json()["items"][0]["name"] == "a.txt", "switched off: Windows' own list is not read"
        settings.update({"import_windows_recent": True})
        merged = client.get("/browse/recent", params={"limit": 2}).json()
    finally:
        windows_recent.read_recent = real_read_recent
    assert merged["windows_recent"] is True
    assert [(i["name"], i["activity"]) for i in merged["items"]] == [("a.txt", "Recently used"), ("live.docx", "Recently used")], merged["items"]
    print("   /browse/recent: your opens and (when allowed) Windows' Recent list merged, one row per file, newest first, deleted/system/folder entries left out, activity text per source: OK")

    refused = {
        r"C:\Windows": 403, r"C:\Windows\System32": 403, r"c:\windows\system32\config": 403, r"C:\ProgramData": 403,
        r"C:\Program Files": 403, r"C:\$Recycle.Bin": 403, r"C:\System Volume Information": 403,
        "\\\\?\\C:\\Users": 403, "\\\\?\\UNC\\server\\share": 403, "\\\\.\\C:": 403, r"\\server\share\folder": 403, "//server/share": 403,
        str(root) + r"\..\..\..\Windows": 400, "C:/Users/../Windows": 400, str(root / ".." / ".."): 400,
        "relative\\folder": 400, "C:folder": 400, "": 400, "C:\\x\x00y": 400,
        str(root / "a.txt") + ":hidden": 400, "C:\\Users\\CON": 400, "C:\\Windows.": 400, "C:\\Windows ": 403, "C:\\Windows \\System32": 400,
        "Q:\\anything": 404, str(root / "nope"): 404,
    }
    for raw, status in refused.items():
        r = client.get("/browse/list", params={"path": raw})
        assert r.status_code == status and r.json().get("error"), (raw, r.status_code, r.text)
    assert client.get("/browse/list", params={"path": str(root / "a.txt")}).status_code == 400, "a file is not a folder"
    link = root / "to windows"
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), os.environ.get("SystemRoot", r"C:\Windows")], capture_output=True, text=True)
    try:
        assert made.returncode == 0, made.stdout + made.stderr
        r = client.get("/browse/list", params={"path": str(link)})
        assert r.status_code == 403, (r.status_code, r.text)
        assert client.get("/browse/list", params={"path": str(link / "System32")}).status_code == 403
    finally:
        if link.exists():
            os.rmdir(link)  # a junction: removes the link only, never what it points at
    denied = make_browse_app(settings, usage, record_store, roots=[], mode="denied")
    for url in ("/browse/drives", "/browse/quick-access", "/browse/recent"):
        assert denied.get(url).status_code == 403, url
    assert denied.get("/browse/list", params={"path": str(root)}).status_code == 403
    before = {p.name for p in root.iterdir()}
    client.get("/browse/list", params={"path": str(root)})
    assert {p.name for p in root.iterdir()} == before and all(os.path.getsize(root / n) == 10 for n in ("a.txt", "b.txt")), "read only: nothing was changed"
    print(f"7. Refusals: {len(refused)} hostile or invalid paths (system folders, .., UNC, \\\\?\\ and \\\\.\\, streams, reserved names, relative, trailing dot, missing drive), a file path, a junction into Windows, access switched off: OK")
    usage.close()  # open SQLite files could not be deleted with the work folder
    record_store.close()


# ---------------------------------------------------------------- 7b. folder-scoped search

def check_folder_scope(work: Path) -> None:
    """/search?folder=...: only files inside that folder, found even when 60 better matches sit elsewhere."""
    scope = work / "scope"
    inside, sibling, other = scope / "A", scope / "AB", scope / "Other"
    for folder in (inside, sibling, other):
        folder.mkdir(parents=True)
    (inside / "club minutes.txt").write_text("Minutes of the club. The harbour parking rota was agreed at the end, after the catering budget.")
    (sibling / "sailing club.txt").write_text("Harbour parking rota for the sailing club: harbour parking rota, harbour parking rota.")
    for i in range(60):
        (other / f"timetable {i}.txt").write_text(f"Harbour parking rota {i}: the harbour parking rota lists every harbour parking slot, harbour parking rota, harbour parking rota.")
    text_model = EmbeddingModel(TEXT_MODEL_DIR)
    vector_store = LanceDBVectorStore(str(work / "scope_vectors"))
    keyword_store = KeywordStore(work / "scope_keyword.db")
    record_store = FileRecordStore(work / "scope_files.db")
    index_folder(Indexer(text_model, vector_store, keyword_store, record_store), str(scope))
    search = SearchService(text_model, vector_store, keyword_store, record_store)
    query = "harbour parking rota"

    def paths(**kw):
        return {Path(h["path"]).parent.name for h in search.search(query, top_k=10, **kw)}

    assert "A" not in paths(), "unscoped, the better matches elsewhere fill the top 10 (otherwise this test proves nothing)"
    for mode in ("keyword", "smart", "exact", "auto"):
        got = paths(mode=mode, folder=str(inside))
        assert got == {"A"}, (mode, got)
    assert paths(folder=str(sibling)) == {"AB"} and "AB" not in paths(folder=str(inside)), "folder A must not match folder AB"
    assert paths(folder=str(inside).upper()) == {"A"}, "case-insensitive"
    assert paths(folder=str(inside) + "\\") == {"A"}, "a trailing separator is fine"
    assert search.search("type:document", top_k=5, folder=str(inside))[0]["filename"] == "club minutes.txt", "filters-only query lists the folder"

    api = FastAPI()
    api.include_router(search_routes.router)
    api.state.search_service = search
    api.state.settings = make_settings(work / "scope_config")
    api.state.settings.update({"remember_activity": False})
    client = TestClient(api)
    body = client.get("/search", params={"q": query, "folder": str(inside).lower()}).json()
    assert body["folder"].lower() == str(inside).lower(), body["folder"]
    assert [r["filename"] for r in body["results"]] == ["club minutes.txt"], body["results"]
    assert "folder" not in client.get("/search", params={"q": query}).json(), "unscoped responses are unchanged"
    for raw, status in ((r"C:\Windows", 403), ("\\\\?\\C:\\Users", 403), (str(inside / ".." / "AB"), 400), ("relative", 400), (str(work / "nope"), 404),
                        (str(inside / "club minutes.txt"), 400), ("C:\\x\x00y", 400)):
        r = client.get("/search", params={"q": query, "folder": raw})
        assert r.status_code == status and r.json().get("error"), (raw, r.status_code, r.text)
    big = [client.get("/search", params={"q": query}).elapsed.total_seconds() for _ in range(3)]
    t_unscoped = min(big)
    t_scoped = min(client.get("/search", params={"q": query, "folder": str(inside)}).elapsed.total_seconds() for _ in range(3))
    print(f"7b. Folder-scoped search: only files inside the folder at keyword, smart, exact and auto tiers (a weak match inside beats 60 strong ones outside); folder A does not match folder AB; response echoes the folder; hostile paths refused; {t_unscoped * 1000:.0f} ms unscoped vs {t_scoped * 1000:.0f} ms scoped: OK")
    record_store.close()
    keyword_store.close()


# ---------------------------------------------------------------- 8. the real backend

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def check_backend(work: Path) -> None:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    auth = {"X-IntelliFile-Token": TOKEN}
    env = {**os.environ, "INTELLIFILE_APP_DATA_DIR": str(work / "appdata"), "INTELLIFILE_API_TOKEN": TOKEN, "INTELLIFILE_OFFLINE_GUARD": "1",
           "TMP": str(LOCAL_TMP), "TEMP": str(LOCAL_TMP)}
    proc = subprocess.Popen([sys.executable, str(BACKEND / "run_backend.py"), "--port", str(port)], cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        end = time.time() + 180
        while True:
            assert proc.poll() is None and time.time() < end, "backend did not start"
            try:
                if requests.get(f"{base}/health", timeout=2).ok:
                    break
            except requests.RequestException:
                time.sleep(1)
        for url in ("/browse/drives", "/browse/quick-access", "/browse/recent"):
            assert requests.get(base + url, timeout=10).status_code == 401, url
            assert requests.get(base + url, params={"token": TOKEN}, timeout=10).status_code == 401, "the URL form of the token is refused"
            assert requests.get(base + url, headers={**auth, "Host": "evil.example"}, timeout=10).status_code == 400, "wrong Host"
        assert requests.get(f"{base}/browse/list", params={"path": str(work)}, timeout=10).status_code == 401
        drives = requests.get(f"{base}/browse/drives", headers=auth, timeout=20).json()["drives"]
        assert drives and requests.get(f"{base}/browse/quick-access", headers=auth, timeout=20).json()["items"]
        assert requests.get(f"{base}/browse/recent", headers=auth, timeout=20).json()["items"] == []
        listing = requests.get(f"{base}/browse/list", params={"path": str(work)}, headers=auth, timeout=20).json()
        assert listing["counts"]["total"] >= 1 and listing["entries"][0]["is_dir"], listing["counts"]
        for bad in (r"C:\Windows", "\\\\?\\C:\\", r"C:\Users\..\Windows", r"\\server\share"):
            r = requests.get(f"{base}/browse/list", params={"path": bad}, headers=auth, timeout=20)
            assert r.status_code in (400, 403) and "error" in r.json(), (bad, r.status_code)
        r = requests.get(f"{base}/search", params={"q": "type:audio kolaveri didi"}, headers=auth, timeout=30).json()
        assert r["results"] == [] and "English only" in r["hint"], r
        assert requests.get(f"{base}/settings", headers=auth, timeout=10).json()["index_video_speech"] is False
        updated = requests.post(f"{base}/settings", json={"index_video_speech": True}, headers=auth, timeout=10).json()
        assert updated["index_video_speech"] is True
        assert requests.get(f"{base}/offline-guard", headers=auth, timeout=10).json()["blocked_attempts"] == 0
        print("8. Real backend: /browse/* needs the header token (401 without it or with it in the URL), refuses a wrong Host (400) and hostile paths; /search carries the Audio hint; the video-speech setting round-trips; 0 network attempts: OK")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="intellifile_browse_"))
    try:
        check_guard_rules()
        if not (WHISPER_DIR / "onnx").exists() or not (CLIP_MODEL_DIR / "onnx").exists() or not (TEXT_MODEL_DIR / "model.onnx").exists():
            print("A model is missing (run the scripts/download_*.py scripts first).")
            return 1
        if not speech_synth.available():
            print("No OS speech engine to make recordings: the pipeline checks are skipped (not a failure).")
        else:
            transcriber = check_transcriber(work)
            check_pipelines(work, transcriber)
        check_browse_api(work)
        check_folder_scope(work)
        check_backend(work)
        print("\nBrowse and audio checks OK.")
        return 0
    finally:
        for _ in range(10):  # the backend started in step 8 releases its database files a moment after it exits
            shutil.rmtree(work, ignore_errors=True)
            if not work.exists():
                break
            time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
