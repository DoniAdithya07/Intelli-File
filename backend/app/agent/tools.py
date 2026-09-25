"""The agent's tools are IntelliFile's own retrieval system — the model
never touches files, it only ever sees what search already returns. That
keeps the PRD's "search-first" principle intact: the LLM sits on top of
the index and reasons over its results; it does not replace it.

    search(query, mode, filters)   -> numbered sources (snippets)
    read_more(source)              -> more of that source's file
    context()                      -> the user's working context (Phase 17)

Sources are numbered as they are found so the answer can cite [n]; every
citation is checked against this table before it reaches the UI.
"""

from dataclasses import dataclass, field
from pathlib import Path

from ..indexing import CHUNKS_TABLE

SNIPPET_CHARS = 700    # what the model sees per source; a 224-token chunk is ~900 chars
READ_MORE_CHARS = 1600
MAX_SOURCES_PER_SEARCH = 5


@dataclass
class Source:
    number: int
    file_id: str
    path: str
    filename: str
    page: int | None
    text: str
    confidence: str
    why: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"number": self.number, "file_id": self.file_id, "path": self.path, "filename": self.filename, "page": self.page, "snippet": self.text[:SNIPPET_CHARS], "confidence": self.confidence, "why": self.why}


class Toolbox:
    def __init__(self, search_service, vector_store, usage_store=None, profile_builder=None):
        self.search_service = search_service
        self.vector_store = vector_store
        self.usage_store = usage_store
        self.profile_builder = profile_builder
        self.sources: list[Source] = []
        self._by_file: dict[str, Source] = {}

    # ----- tools -----

    def search(self, query: str, mode: str = "auto", filters: str = "") -> tuple[list[Source], dict]:
        """Run IntelliFile search; new files become new numbered sources,
        files already seen keep their number (so [n] stays stable)."""
        if mode not in ("auto", "smart", "keyword", "exact"):
            mode = "auto"
        full_query = f"{filters.strip()} {query.strip()}".strip()
        results, route = self.search_service.search_routed(full_query, top_k=MAX_SOURCES_PER_SEARCH, mode=mode)
        if not results and mode != "auto":
            # The model's mode preference is advice, not a veto: "keyword"
            # ANDs every word, so one word the file lacks empties the
            # result. Manual modes never escalate, so escalate here.
            results, route = self.search_service.search_routed(full_query, top_k=MAX_SOURCES_PER_SEARCH, mode="auto")
            route = dict(route, fallback_from_mode=mode)
        found: list[Source] = []
        for r in results:
            text = _plain(r.get("chunk_text") or r["matched_chunk"] or "")
            existing = self._by_file.get(r["file_id"])
            if existing is not None:
                if text and text not in existing.text:
                    existing.text = (existing.text + "\n…\n" + text)[:READ_MORE_CHARS]
                found.append(existing)
                continue
            source = Source(
                number=len(self.sources) + 1,
                file_id=r["file_id"],
                path=r["path"] or "",
                filename=r["filename"],
                page=r.get("page"),
                text=text,
                confidence=r["confidence"],
                why=[w for w in r["why"] if not w.startswith("You") and not w.startswith("Your")],
            )
            self.sources.append(source)
            self._by_file[source.file_id] = source
            found.append(source)
        return found, route

    def read_more(self, number: int) -> Source | None:
        """More of a source's text: its first chunks, in order."""
        source = self.get(number)
        if source is None:
            return None
        rows = self.vector_store.get_by_file_id(CHUNKS_TABLE, source.file_id)
        rows.sort(key=lambda r: r["payload"].get("chunk_index", 0))
        text = "\n".join(r["payload"].get("content", "") for r in rows[:3])
        if text:
            source.text = text[:READ_MORE_CHARS]
        return source

    def context(self) -> dict:
        """The working context and profile highlights the agent may use to
        pick a strategy (Objective 2 feeding Objective 1)."""
        out: dict = {"session": None, "topics": [], "top_files": []}
        if self.usage_store is not None:
            out["session"] = self.usage_store.current_session()
        if self.profile_builder is not None:
            profile = self.profile_builder.get()
            if not profile.cold_start:
                out["topics"] = [t["label"] for t in profile.topics[:3]]
                out["top_files"] = [f["filename"] for f in profile.top_files[:5]]
        return out

    # ----- helpers -----

    def get(self, number: int) -> Source | None:
        return self.sources[number - 1] if 1 <= number <= len(self.sources) else None

    def render(self, sources: list[Source]) -> str:
        """How sources are shown to the model."""
        if not sources:
            return "No matching files."
        lines = []
        for s in sources:
            where = f", page {s.page}" if s.page else ""
            lines.append(f"[{s.number}] {s.filename}{where} ({s.confidence} match): {s.text[:SNIPPET_CHARS]}")
        return "\n".join(lines)


def _plain(highlighted: str) -> str:
    return highlighted.replace("**", "").replace("\n", " ").strip()


def file_display(path: str) -> str:
    return Path(path).name if path else ""
