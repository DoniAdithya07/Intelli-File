"""Ties extraction -> chunking -> embedding -> storage together: this is
what actually runs behind an "index" job from Phase 2's file watcher.
Also implements the PRD's Duplicate Files behavior (reuse an identical
file's chunks/vectors instead of recomputing embeddings) and the delete
side of tombstone cleanup.
"""

import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from ..chunking import chunk_document
from ..embeddings.model import EMBEDDING_DIMENSION, EmbeddingModel
from ..extraction.extractor import extract_document
from ..files.discovery import VISUAL_EXTENSIONS
from ..storage.keyword_store import KeywordStore
from ..storage.lancedb_store import LanceDBVectorStore
from ..storage.sqlite_store import FileRecordStore

if TYPE_CHECKING:
    from ..transcription.transcriber import Transcriber

CHUNKS_TABLE = "chunks"


class Indexer:
    def __init__(
        self,
        embedding_model: EmbeddingModel,
        vector_store: LanceDBVectorStore,
        keyword_store: KeywordStore,
        file_record_store: FileRecordStore,
        transcriber: "Transcriber | None" = None,
    ):
        self.embedding_model = embedding_model
        self.vector_store = vector_store
        self.keyword_store = keyword_store
        self.file_record_store = file_record_store
        self.transcriber = transcriber
        # Phase 13 chunking experiments override these; None = chunker defaults.
        self.chunk_tokens: int | None = None
        self.chunk_overlap: int | None = None
        self.vector_store.create_table(CHUNKS_TABLE, dimension=EMBEDDING_DIMENSION)

    def reset_if_model_changed(self, config_dir: Path) -> bool:
        """Drop every text chunk if the embedding model differs from the one
        that produced them (2026-09-21, the MiniLM → bge-small swap: same
        384 dimensions, incomparable spaces — a stale index would have
        scored garbage silently). Text/audio records are marked unindexed
        so the startup scans re-embed them in place (the size + mtime fast
        path treats `indexed = 0` as changed) — nothing else is rescanned.
        Returns True if a reset happened."""
        marker = config_dir / "text_model.json"
        previous = None
        if marker.exists():
            try:
                previous = json.loads(marker.read_text()).get("model_id")
            except (OSError, ValueError):
                previous = None
        current = getattr(self.embedding_model, "model_id", "unknown")
        if previous == current:
            return False
        changed = previous is not None or self._chunk_count() > 0
        if changed:
            self.vector_store.drop_table(CHUNKS_TABLE)
            self.vector_store.create_table(CHUNKS_TABLE, dimension=EMBEDDING_DIMENSION)
            self.keyword_store.clear()
            for record in self.file_record_store.list_active():
                if Path(record.path).suffix.lower() not in VISUAL_EXTENSIONS:
                    self.file_record_store.mark_indexed(record.file_id, False)
        marker.write_text(json.dumps({"model_id": current, "dimension": EMBEDDING_DIMENSION}))
        return changed

    def _chunk_count(self) -> int:
        try:
            return self.vector_store.db.open_table(CHUNKS_TABLE).count_rows()
        except Exception:
            return 0

    def index_file(self, path: Path, file_id: str, file_hash: str) -> int:
        """(Re-)index a file. Returns the number of chunks stored. The
        previous chunks for this file_id are replaced, never left stale —
        but only once the new content is ready: extraction and embedding
        run first, the old chunks go last. Found 2026-09-21: deleting
        first meant a file whose extraction failed (locked by Word/Excel
        on Windows, half-written PDF, temporarily unreadable) vanished
        from search until the next successful index."""
        duplicate = self.file_record_store.find_active_by_hash(file_hash, exclude_file_id=file_id)
        if duplicate is not None:
            reused = self._reuse_duplicate_chunks(file_id, duplicate.file_id)
            if reused:
                return reused
            # Fall through: the duplicate's own chunks weren't found (e.g.
            # already cleaned up) — index normally instead.

        blocks = extract_document(path, transcriber=self.transcriber)
        chunks = chunk_document(blocks, chunk_size=self.chunk_tokens, overlap=self.chunk_overlap, token_spans=self.embedding_model.token_spans)
        if not chunks:
            self.delete_file(file_id)  # the file is now genuinely empty
            return 0

        texts = [c.content for c in chunks]
        vectors = self.embedding_model.embed_texts(texts)
        self.delete_file(file_id)

        vector_records = []
        keyword_records = []
        for chunk, vector in zip(chunks, vectors):
            chunk_id = str(uuid.uuid4())
            vector_records.append(
                {
                    "id": chunk_id,
                    "file_id": file_id,
                    "vector": vector.tolist(),
                    "payload": {
                        "content": chunk.content,
                        "page_number": chunk.page_number,
                        "chunk_index": chunk.chunk_index,
                        "start_offset": chunk.start_offset,
                        "end_offset": chunk.end_offset,
                        "heading": chunk.heading,
                        "section": chunk.section,
                    },
                }
            )
            keyword_records.append({"chunk_id": chunk_id, "file_id": file_id, "content": chunk.content})

        self.vector_store.upsert(CHUNKS_TABLE, vector_records)
        self.keyword_store.add_chunks(keyword_records)
        return len(chunks)

    def _reuse_duplicate_chunks(self, file_id: str, source_file_id: str) -> int:
        """Copy an identical file's existing chunks/vectors under the new
        file_id, skipping re-extraction and re-embedding entirely."""
        existing = self.vector_store.get_by_file_id(CHUNKS_TABLE, source_file_id)
        if not existing:
            return 0
        self.delete_file(file_id)

        vector_records = []
        keyword_records = []
        for row in existing:
            chunk_id = str(uuid.uuid4())
            vector_records.append(
                {
                    "id": chunk_id,
                    "file_id": file_id,
                    "vector": row["vector"],
                    "payload": row["payload"],
                }
            )
            keyword_records.append(
                {"chunk_id": chunk_id, "file_id": file_id, "content": row["payload"].get("content", "")}
            )

        self.vector_store.upsert(CHUNKS_TABLE, vector_records)
        self.keyword_store.add_chunks(keyword_records)
        return len(existing)

    def delete_file(self, file_id: str) -> None:
        self.vector_store.delete_by_file_id(CHUNKS_TABLE, file_id)
        self.keyword_store.delete_by_file_id(file_id)
