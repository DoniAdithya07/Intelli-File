"""Phase 7 integration test: real synthesized speech -> local transcription
-> correct text, then feed that text into the actual search pipeline and
confirm the right file surfaces (the full voice-search flow end to end).

Uses macOS's built-in `say` + `afconvert` to generate real speech audio,
so this specific test only runs on macOS (dev-time only — the feature
itself has nothing macOS-specific, only this test's audio generation).

Run with:
    backend/venv/bin/python backend/scripts/prototype_transcription.py
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.embeddings.model import EmbeddingModel, default_model_dir  # noqa: E402
from app.files.identity import build_file_record  # noqa: E402
from app.indexing import Indexer  # noqa: E402
from app.search import SearchService  # noqa: E402
from app.storage import FileRecordStore, KeywordStore, LanceDBVectorStore  # noqa: E402
from app.transcription import Transcriber  # noqa: E402

MODEL_DIR = default_model_dir(Path(__file__).resolve().parents[1] / "models")
WHISPER_DIR = Path(__file__).resolve().parents[1] / "models" / "whisper-base.en"


def synthesize_speech_wav(text: str, out_path: Path) -> None:
    aiff_path = out_path.with_suffix(".aiff")
    subprocess.run(["say", "-o", str(aiff_path), text], check=True)
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff_path), str(out_path)],
        check=True,
    )


def main():
    if shutil.which("say") is None or shutil.which("afconvert") is None:
        print("This test needs macOS's `say`/`afconvert` to generate speech audio — skipping (not a failure).")
        sys.exit(0)
    if not (WHISPER_DIR / "onnx").exists():
        print("Whisper model not found — run scripts/download_whisper_model.py first.")
        sys.exit(1)
    if not (MODEL_DIR / "model.onnx").exists():
        print("Embedding model not found — run scripts/download_model.py first.")
        sys.exit(1)

    workdir = Path(tempfile.mkdtemp())
    try:
        transcriber = Transcriber(WHISPER_DIR)

        # --- 1. Basic transcription accuracy on real speech audio ---
        test_cases = {
            "find my notes about horizontal scaling": "horizontal",
            "notes about my bread recipe": "bread",
            "working out at the gym": "gym",
        }
        transcriptions = {}
        for spoken_text, expected_word in test_cases.items():
            wav_path = workdir / f"{expected_word}.wav"
            synthesize_speech_wav(spoken_text, wav_path)
            transcribed = transcriber.transcribe(wav_path.read_bytes())
            transcriptions[expected_word] = transcribed
            assert expected_word in transcribed.lower(), (
                f"Expected '{expected_word}' in transcription of {spoken_text!r}, got: {transcribed!r}"
            )
        print("1. Real speech transcribed accurately:")
        for expected_word, transcribed in transcriptions.items():
            print(f"   -> {transcribed!r}")

        # --- 2. Full voice-search flow: transcribed text feeds real search ---
        cloud_path = workdir / "cloud_computing.txt"
        cloud_path.write_text(
            "Horizontal scaling allows additional server instances to be provisioned when "
            "traffic demand increases, distributed by a load balancer."
        )
        bread_path = workdir / "baking_recipe.txt"
        bread_path.write_text("A good sourdough starter needs regular feeding with flour and water.")
        gym_path = workdir / "fitness_routine.txt"
        gym_path.write_text("A balanced strength routine includes squats, deadlifts, and bench presses.")

        model = EmbeddingModel(MODEL_DIR)
        vector_store = LanceDBVectorStore(str(workdir / "vectors"))
        keyword_store = KeywordStore(workdir / "keyword.db")
        file_record_store = FileRecordStore(workdir / "files.db")
        indexer = Indexer(model, vector_store, keyword_store, file_record_store, transcriber=transcriber)
        search_service = SearchService(model, vector_store, keyword_store, file_record_store)

        for path in (cloud_path, bread_path, gym_path):
            record = build_file_record(path)
            file_record_store.upsert(record)
            indexer.index_file(path, record.file_id, record.hash)

        expected_files = {
            "horizontal": "cloud_computing.txt",
            "bread": "baking_recipe.txt",
            "gym": "fitness_routine.txt",
        }
        for expected_word, expected_file in expected_files.items():
            results = search_service.search(transcriptions[expected_word])
            assert results, f"No results for transcribed query: {transcriptions[expected_word]!r}"
            assert results[0]["filename"] == expected_file, (
                f"Voice query {transcriptions[expected_word]!r} -> expected {expected_file}, "
                f"got {results[0]['filename']}"
            )
        print("2. Full voice-search flow (speech -> text -> search) found the right file every time: OK")

        # --- 3. Audio FILES get discovered and indexed by transcribing their spoken content ---
        from app.files.discovery import discover_files

        voice_memo_path = workdir / "meeting_notes.m4a"
        synthesize_speech_wav(
            "reminder to review the kubernetes deployment configuration before the release",
            voice_memo_path.with_suffix(".wav"),
        )
        subprocess.run(
            [
                "afconvert", "-f", "mp4f", "-d", "aac",
                str(voice_memo_path.with_suffix(".wav")), str(voice_memo_path),
            ],
            check=True,
        )

        discovered = list(discover_files([str(workdir)]))
        assert voice_memo_path in discovered, "discover_files should find .m4a audio files now"

        audio_record = build_file_record(voice_memo_path)
        file_record_store.upsert(audio_record)
        n_chunks = indexer.index_file(voice_memo_path, audio_record.file_id, audio_record.hash)
        assert n_chunks > 0, "Audio file should produce at least one chunk from its transcript"

        audio_results = search_service.search("kubernetes deployment configuration")
        assert audio_results and audio_results[0]["filename"] == "meeting_notes.m4a", (
            f"Expected the voice memo to surface for a query about its spoken content, got: {audio_results}"
        )
        print("3. Audio files (.m4a) are discovered, transcribed, and become searchable by spoken content: OK")

        # --- Regression, live test 2026-09-11: names Whisper has never seen.
        # "open abhisek plan" came back as "happy shake clan". With the
        # user's file names passed as a vocabulary hint the model can
        # spell them. ---
        name_wav = workdir / "name.wav"
        synthesize_speech_wav("find gurtucheyatam audio", name_wav)
        without_hint = transcriber.transcribe(name_wav.read_bytes())
        with_hint = transcriber.transcribe(name_wav.read_bytes(), vocabulary_hint=["gurtucheyatam", "abhisek plan", "rasmalai"])
        assert "gurtucheyatam" in with_hint.lower(), f"Hinted transcription should contain the name, got {with_hint!r} (unhinted: {without_hint!r})"
        plain_wav = workdir / "plain.wav"
        synthesize_speech_wav("find my notes about horizontal scaling", plain_wav)
        hinted_plain = transcriber.transcribe(plain_wav.read_bytes(), vocabulary_hint=["gurtucheyatam", "abhisek plan", "rasmalai"])
        assert "horizontal scaling" in hinted_plain.lower(), f"Hint must not damage ordinary sentences, got {hinted_plain!r}"
        print(f"4. Filename vocabulary hint: {without_hint!r} -> {with_hint!r}; ordinary sentence unaffected: OK")

        # --- 2026-09-20, live test: "landlord letter" spoken bare came back
        # as "Plan lot later" / "Learn Lord letter" even with the name in
        # the prompt (measured: prompting cannot force a bare two-word
        # utterance; beam search no better). snap_to_filenames() runs on
        # the TEXT afterwards. Table = must-snap cases from the live test
        # and synthesized clips, plus legitimate queries that must never be
        # rewritten (a rewrite of a real query is worse than a missed fix). ---
        from app.transcription.snap import snap_to_filenames

        names = ["landlord letter", "gym plan", "monthly expenses", "lisbon trip", "demo wedding 3", "demo video steam train 1",
                 "voice note 10", "demo airplane 6", "abhisek plan", "backup photos"]
        must_snap = {"Learn Lord letter": "landlord letter", "Plan lot later": "landlord letter", "Chimp plan": "gym plan",
                     "Jim plan": "gym plan", "Search for learn lord letter": "Search for landlord letter", "open the chimp plan": "open the gym plan"}
        for heard, want in must_snap.items():
            got, raw = snap_to_filenames(heard, names)
            assert got == want and raw == heard, f"{heard!r} -> {got!r}, wanted {want!r}"
        # "chin plan" is NOT in the table on purpose: "chin"/"gym" share too
        # little (0.50) to tell a mishearing from a real word, and a wrong
        # rewrite costs more than a missed one — the prompt path handles it.
        must_not = ["Lisbon trip", "my plan", "my expenses", "the meeting with the client about the dashboard", "the demo people",
                    "video of the steam train", "my voice notes from last week", "letter to the landlord", "a plan for the weekend trip",
                    "Packing list for the trek: rain jacket, headlamp, two litres of water, sunscreen, and the first aid kit from the garage.",
                    "Budget check. Rent is twelve thousand, electricity was about nine hundred, and I still owe Rahul six hundred for the concert tickets."]
        for text in must_not:
            got, raw = snap_to_filenames(text, names)
            assert raw is None and got == text, f"legitimate query rewritten: {text!r} -> {got!r}"
        # End to end on real (synthesized) speech: whatever Whisper hears for
        # the bare name, the text that reaches search must be the file name.
        bare_wav = workdir / "bare_name.wav"
        synthesize_speech_wav("landlord letter", bare_wav)
        heard = transcriber.transcribe(bare_wav.read_bytes(), vocabulary_hint=names)
        snapped, _ = snap_to_filenames(heard, names)
        assert "landlord letter" in snapped.lower(), f"heard {heard!r}, snapped {snapped!r}"
        print(f"5. Sound-alike snap to file names: {len(must_snap)} misheard names fixed, {len(must_not)} real queries untouched; spoken bare name {heard!r} -> {snapped!r}: OK")

        # --- 6. Audio longer than Whisper's 30 s window is transcribed in
        # full (2026-09-21: a 67 s clip used to come back as its first 30 s;
        # a word at 55 s was never heard). Twelve repeats of one sentence
        # plus a closing sentence: every repeat must be heard exactly once
        # (nothing dropped or doubled at a window cut) and the closing
        # words must be present. ---
        filler = "We talked about the weather, the schedule, and the parking situation for a while. "
        long_wav = workdir / "long_memo.wav"
        synthesize_speech_wav(filler * 12 + "The secret word is pineapple and the budget is nine thousand dollars.", long_wav)
        duration = len(long_wav.read_bytes()) / (2 * 16000)
        assert duration > 45, f"long clip is only {duration:.0f}s — test needs > 30 s"
        long_text = transcriber.transcribe(long_wav.read_bytes()).lower()
        assert "pineapple" in long_text, f"word past 30 s not heard: {long_text[-200:]!r}"
        assert long_text.count("parking") == 12, f"expected 12 'parking', heard {long_text.count('parking')} — words lost or doubled at a cut"
        print(f"6. {duration:.0f} s recording transcribed in full across 30 s windows ({len(long_text.split())} words, nothing lost at the cuts): OK")

        print("\nPhase 7 voice search OK: real speech transcribed accurately, the transcribed text correctly drives search end to end, and audio FILES are now indexed by their spoken content too.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
    # PyAV/onnxruntime's native threads intermittently race Python's own
    # interpreter-shutdown sequence on macOS, crashing with
    # "recursive_mutex lock failed" AFTER all real work above has already
    # succeeded (confirmed via repeated runs: same success output every
    # time, only the exit code sometimes flips). This has no bearing on
    # the actual app — a long-lived server process never goes through
    # this teardown between requests — it only ever corrupted this
    # script's own exit code. Skip Python's normal cleanup/atexit/GC
    # entirely once real work is done; nothing meaningful is left to
    # clean up at this point.
    import os
    import sys
    # os._exit skips Python's buffer flush: when this script's output is
    # piped (run_all_phases.py in CI, `> log`) every line above vanished
    # while the exit code still said pass (found 2026-09-21).
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
