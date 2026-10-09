import logging
from pathlib import Path

from fastapi import APIRouter, Request, Response

from ..indexing import CHUNKS_TABLE
from ..paths import sample_folder
from ..thumbnails import generate_thumbnail
from .common import MAX_THUMBNAIL, MIN_THUMBNAIL, safe_path

router = APIRouter()
logger = logging.getLogger(__name__)

# The preview pane shows real indexed text only (docs/UI_DESIGN.md section 2).
MAX_PASSAGE_CHARS = 2000
MAX_FILE_TEXT_CHARS = 20_000


def _file_chunks(state, file_id: str) -> list[dict]:
    rows = state.indexer.vector_store.get_by_file_id(CHUNKS_TABLE, file_id)
    rows.sort(key=lambda r: r["payload"].get("chunk_index") or 0)
    return rows


def _passage(row: dict) -> dict:
    payload = row["payload"]
    return {
        "chunk_id": row["id"],
        "chunk_index": payload.get("chunk_index"),
        "page": payload.get("page_number"),
        "heading": payload.get("heading"),
        "text": (payload.get("content") or "")[:MAX_PASSAGE_CHARS],
    }


@router.get("/passages")
def passages_endpoint(file_id: str, request: Request, chunk_id: str | None = None):
    """The matched passage of a file and the ones on either side of it.
    Without a chunk_id (a file-name match) the file's first passage is the
    match. A file that is no longer indexed says so instead of guessing."""
    state = request.app.state
    record = state.indexer.file_record_store.get_by_file_id(file_id)
    if record is None or record.deleted:
        return {"error": "not_indexed", "match": None, "previous": None, "next": None, "total": 0}
    rows = _file_chunks(state, file_id)
    if not rows:
        return {"match": None, "previous": None, "next": None, "total": 0, "path": record.path}
    at = next((i for i, r in enumerate(rows) if r["id"] == chunk_id), 0)
    return {
        "path": record.path,
        "total": len(rows),
        "match": _passage(rows[at]),
        "previous": _passage(rows[at - 1]) if at > 0 else None,
        "next": _passage(rows[at + 1]) if at + 1 < len(rows) else None,
    }


@router.get("/file-text")
def file_text_endpoint(file_id: str, request: Request):
    """All the text IntelliFile holds for one file, in order: for a photo or
    a screenshot this is its Windows OCR text, for audio the transcript.
    Empty when the file has none (a photo without words)."""
    state = request.app.state
    record = state.indexer.file_record_store.get_by_file_id(file_id)
    if record is None or record.deleted:
        return {"error": "not_indexed", "text": "", "truncated": False}
    text = "\n\n".join((r["payload"].get("content") or "") for r in _file_chunks(state, file_id)).strip()
    return {"path": record.path, "text": text[:MAX_FILE_TEXT_CHARS], "truncated": len(text) > MAX_FILE_TEXT_CHARS}


@router.get("/sample-folder")
def sample_folder_endpoint():
    """First run and Index offer "Try the sample folder": this is where it is."""
    path = sample_folder()
    return {"path": str(path) if path else None}


def _is_indexed_file(state, file_path: Path) -> bool:
    record = state.indexer.file_record_store.get_by_path(str(file_path))
    return record is not None and not record.deleted and file_path.is_file()


@router.get("/is-indexed")
def is_indexed_endpoint(path: str, request: Request):
    """Used by the desktop shell before it opens a file: only files the
    user let IntelliFile index may be opened from the app."""
    safe = safe_path(path)
    return {"indexed": safe is not None and _is_indexed_file(request.app.state, safe)}


@router.get("/thumbnail")
def thumbnail_endpoint(path: str, request: Request, t: float | None = None, size: int = 256):
    file_path = Path(path)
    # Only files IntelliFile has indexed get thumbnails. An <img> tag on any
    # web page can point at this local port (img loads aren't CORS-gated),
    # so without this the endpoint would render any readable image on the
    # machine on request. Indexed files are exactly the set the user chose
    # to expose to the app. (2026-09-11 audit.)
    safe = safe_path(path)
    if safe is None:
        return Response(status_code=404)
    file_path = safe
    if not _is_indexed_file(request.app.state, file_path):
        return Response(status_code=404)
    try:
        jpeg_bytes = generate_thumbnail(file_path, timestamp_offset_seconds=t, max_size=max(MIN_THUMBNAIL, min(size, MAX_THUMBNAIL)))
    except Exception:
        # Unreadable/corrupt image: a broken thumbnail shouldn't read as
        # a server fault, and the result itself is still usable. Logged
        # (2026-10-05): a thumbnail that never shows used to leave no trace.
        logger.warning("Thumbnail failed for %s", file_path, exc_info=True)
        return Response(status_code=422)
    return Response(content=jpeg_bytes, media_type="image/jpeg")
