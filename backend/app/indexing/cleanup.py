"""Tombstone cleanup, per the PRD's Deleted File Handling section: deletes
are soft (tombstoned) so normal usage isn't slowed down by immediate
index rebuilding; this function is the deferred sweep that actually
removes the vectors/keyword entries and, once purged, the metadata row.

Not scheduled here — Phase 2 already tombstones on delete; wiring this up
to a weekly timer / tombstone-volume trigger is a resource-management
concern that belongs with Phase 7/10, once battery/resource-aware
scheduling exists. This module is the piece that scheduling will call.
"""

from typing import TYPE_CHECKING

from .indexer import CHUNKS_TABLE, Indexer
from .visual_indexer import IMAGES_TABLE

if TYPE_CHECKING:
    from .visual_indexer import VisualIndexer


def cleanup_tombstones(indexer: Indexer, visual_indexer: "VisualIndexer | None" = None) -> int:
    """Purge every currently tombstoned file's chunks and metadata.
    Returns how many files were cleaned up.

    A tombstoned file may be a photo, so the visual index is swept too
    when available. Both deletes run unconditionally rather than checking
    the file's type first: removing by file_id from a table holding
    nothing for it is already a no-op, exactly as it is today for a text
    file whose extraction failed and produced no chunks.
    """
    tombstoned = indexer.file_record_store.list_tombstoned()
    for record in tombstoned:
        indexer.delete_file(record.file_id)
        if visual_indexer is not None:
            visual_indexer.delete_file(record.file_id)
        indexer.file_record_store.remove(record.file_id)
    # This sweep ends every folder scan, so it is also where the vector
    # tables get compacted (see LanceDBVectorStore.optimize).
    indexer.vector_store.optimize(CHUNKS_TABLE)
    if visual_indexer is not None:
        visual_indexer.vector_store.optimize(IMAGES_TABLE)
    return len(tombstoned)
