"""Storage abstraction so the vector DB engine (LanceDB now, possibly
SQLite+sqlite-vec or DuckDB later) can be swapped without touching the
indexing/search layers above it.
"""

from abc import ABC, abstractmethod
from typing import Any


class VectorStore(ABC):
    @abstractmethod
    def create_table(self, name: str, dimension: int) -> None:
        """Create a table for vectors of the given dimension, if it doesn't exist."""

    @abstractmethod
    def upsert(self, table: str, records: list[dict[str, Any]]) -> None:
        """Insert or update records. Each record must include 'id', 'vector', and 'file_id'."""

    @abstractmethod
    def query(self, table: str, vector: list[float], top_k: int = 20) -> list[dict[str, Any]]:
        """Return the top_k nearest records to the given vector, most similar first."""

    @abstractmethod
    def delete(self, table: str, ids: list[str]) -> None:
        """Remove records by id."""

    @abstractmethod
    def delete_by_file_id(self, table: str, file_id: str) -> None:
        """Remove every record belonging to a given file (e.g. tombstone cleanup)."""

    @abstractmethod
    def get_payloads(self, table: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        """payload (metadata dict) per record id, for the ids that exist."""

    def optimize(self, table: str) -> None:
        """Optional maintenance after a batch of writes (compaction, old
        versions). A no-op for engines that don't need it."""

    @abstractmethod
    def get_by_file_id(self, table: str, file_id: str) -> list[dict[str, Any]]:
        """Fetch every record belonging to a given file (e.g. to reuse an
        existing file's chunks/vectors for a detected duplicate)."""
