import re
from pathlib import Path

from ..storage import FileRecordStore
from ..transcription.snap import snap_to_filenames

# The prompt is limited by Whisper's token budget (~33 names fit); the
# text-only snap step is not, so it sees more — but not all: the chance
# that an ordinary phrase coincidentally sounds like SOME name grows with
# the pool (measured: 12% false rewrites of short phrases against 1500
# random names, ~0 against 400). Recently touched files are the ones
# people ask for by name, so the most recent 400 is the pool.
SNAP_NAME_LIMIT = 400


def filename_hints(record_store: FileRecordStore, limit: int = 40) -> list[str]:
    """The user's most recently modified file names, as words Whisper
    should be able to spell (see Transcriber.transcribe). Capped so the
    prompt stays short; the most recently touched files are the ones
    most likely to be asked for by name."""
    records = sorted(record_store.list_active(), key=lambda r: r.modified_time, reverse=True)
    hints: list[str] = []
    for record in records:
        name = re.sub(r"[_\-.]+", " ", Path(record.path).stem).strip()
        if name and name.lower() not in {h.lower() for h in hints}:
            hints.append(name)
        if len(hints) >= limit:
            break
    return hints


def _normalized(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _has_filename_match(results: list[dict]) -> bool:
    return any(r["why"] and r["why"][0].startswith("Filename contains") for r in results)


def snap_transcript(state, heard: str) -> dict:
    """The snap step of /transcribe: `heard` is the raw transcript."""
    # A bare file name Whisper misheard ("Learn Lord letter") is snapped to
    # the name it sounds like — but only APPLIED when what was heard finds
    # nothing on its own, so a coincidental sound-alike can never replace a
    # transcript that already works; then it is offered as a "Did you mean"
    # chip instead. `heard` is set only when the snap was applied, so the
    # UI can show the correction and offer the raw words back.
    names = filename_hints(state.indexer.file_record_store, limit=SNAP_NAME_LIMIT)
    snapped, raw = snap_to_filenames(heard, names)
    if raw is None:
        return {"text": heard, "heard": None, "suggestion": None}
    search = state.search_service.search
    raw_results = search(heard, top_k=5)
    raw_finds_something = any(r["confidence"] != "weak" for r in raw_results)
    # Second way in (2026-09-21): the raw words may "find something" only
    # by coincidence — with the project folder indexed, "Learn Lord
    # Letter" found snap.py and this README, which quote that very
    # phrase — while the snapped text is exactly one of the user's file
    # names. A whole utterance that is a file name, when what was heard
    # matches no file name at all, is the file name.
    bare_name = _normalized(snapped) in {_normalized(n) for n in names}
    if raw_finds_something and not (bare_name and not _has_filename_match(raw_results) and _has_filename_match(search(snapped, top_k=5))):
        return {"text": heard, "heard": None, "suggestion": snapped}
    return {"text": snapped, "heard": heard, "suggestion": None}
