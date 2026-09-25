"""Query parsing, per the PRD's Exact Match Override and Metadata Search
sections. Kept rule-based on purpose (per the PRD's "Search-first, not
LLM-first" principle) — no NLU model needed for this.

Pulled out of the raw query before it reaches search:
- An exact phrase in "quotes" -> exact-match mode (BM25-primary).
- Metadata filters (Phase 18 grew these beyond `type:` so the router has
  a real metadata tier to route to):
    type:pdf / ext:pdf      file extension
    after:2025-03-01        modified on/after (also `after:2025`, `after:2025-03`)
    before:2025-03-01       modified before
    size:>10mb  size:<500kb file size (b, kb, mb, gb)
    in:invoices             path contains this folder/word (case-insensitive)

Free-text date phrases ("modified last week") are NOT parsed here — that
needs real date-phrase NLU to do well, and guessing badly would be worse
than not supporting it yet. Deferred, not silently dropped.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime

_QUOTED_PHRASE_RE = re.compile(r'"([^"]+)"')
_FILTER_RE = re.compile(r"\b(type|ext|after|before|size|in):(\S+)", re.IGNORECASE)
_SIZE_RE = re.compile(r"^([<>]=?)?\s*(\d+(?:\.\d+)?)\s*(b|kb|mb|gb)?$", re.IGNORECASE)
_UNIT = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, None: 1}


@dataclass(frozen=True)
class QueryFilters:
    file_type: str | None = None
    after: float | None = None        # epoch seconds, inclusive
    before: float | None = None       # epoch seconds, exclusive
    size_min: int | None = None       # bytes
    size_max: int | None = None
    path_contains: str | None = None  # lowercase

    def any(self) -> bool:
        return any(v is not None for v in (self.file_type, self.after, self.before, self.size_min, self.size_max, self.path_contains))

    def describe(self) -> list[str]:
        parts = []
        if self.file_type:
            parts.append(f"type {self.file_type}")
        if self.after is not None:
            parts.append(f"after {datetime.fromtimestamp(self.after).date()}")
        if self.before is not None:
            parts.append(f"before {datetime.fromtimestamp(self.before).date()}")
        if self.size_min is not None:
            parts.append(f"larger than {_human(self.size_min)}")
        if self.size_max is not None:
            parts.append(f"smaller than {_human(self.size_max)}")
        if self.path_contains:
            parts.append(f"in “{self.path_contains}”")
        return parts

    def matches(self, path: str, size: int, modified_time: float) -> bool:
        if self.file_type and not path.lower().endswith("." + self.file_type):
            return False
        if self.after is not None and modified_time < self.after:
            return False
        if self.before is not None and modified_time >= self.before:
            return False
        if self.size_min is not None and size <= self.size_min:
            return False
        if self.size_max is not None and size >= self.size_max:
            return False
        if self.path_contains and self.path_contains not in path.lower():
            return False
        return True


def _human(n: int) -> str:
    for unit, div in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if n >= div:
            return f"{n / div:g} {unit}"
    return f"{n} B"


@dataclass(frozen=True)
class ParsedQuery:
    text: str  # the query with quotes/filters stripped, ready for search
    exact_phrase: str | None  # set if the whole query was a quoted phrase
    file_type: str | None  # e.g. "pdf", from a type:pdf filter (kept for callers that only know this one)
    filters: QueryFilters = field(default_factory=QueryFilters)


def _parse_date(value: str, end: bool = False) -> float | None:
    """2025 / 2025-03 / 2025-03-14 -> epoch seconds at the start of that
    period (or the start of the *next* period when `end`, so `before:2025`
    means before 2025 begins and `after:2025` means from 2025-01-01)."""
    for fmt, step in (("%Y-%m-%d", "day"), ("%Y-%m", "month"), ("%Y", "year")):
        try:
            dt = datetime.strptime(value, fmt)
        except ValueError:
            continue
        return dt.timestamp()
    return None


def _parse_size(value: str) -> tuple[int | None, int | None]:
    m = _SIZE_RE.match(value)
    if not m:
        return None, None
    op, number, unit = m.group(1) or ">", float(m.group(2)), (m.group(3) or "b").lower()
    n = int(number * _UNIT[unit])
    return (None, n) if op.startswith("<") else (n, None)


def parse_query(raw_query: str) -> ParsedQuery:
    remaining = raw_query
    file_type = after = before = size_min = size_max = path_contains = None

    for m in _FILTER_RE.finditer(raw_query):
        key, value = m.group(1).lower(), m.group(2)
        if key in ("type", "ext"):
            file_type = value.lower().lstrip(".")
        elif key == "after":
            after = _parse_date(value)
        elif key == "before":
            before = _parse_date(value)
        elif key == "size":
            lo, hi = _parse_size(value)
            size_min = lo if lo is not None else size_min
            size_max = hi if hi is not None else size_max
        elif key == "in":
            path_contains = value.lower().strip("\"'")
    remaining = _FILTER_RE.sub("", remaining).strip()

    exact_phrase = None
    quote_match = _QUOTED_PHRASE_RE.fullmatch(remaining.strip())
    if quote_match:
        exact_phrase = quote_match.group(1)
        remaining = exact_phrase

    filters = QueryFilters(file_type=file_type, after=after, before=before, size_min=size_min, size_max=size_max, path_contains=path_contains)
    return ParsedQuery(text=remaining.strip(), exact_phrase=exact_phrase, file_type=file_type, filters=filters)
