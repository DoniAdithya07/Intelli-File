"""Sliding-window chunking over extracted document blocks, per the PRD's
Chunking section.

Windows are measured in the embedding model's own tokens when a
`token_spans` function is supplied (the indexer passes the MiniLM
tokenizer), and in whitespace-delimited words otherwise (tests, scripts).

Why tokens, found 2026-09-21: the old default was 512 *words*, but
all-MiniLM-L6-v2 truncates its input at 256 *tokens* (~190 English words),
so the second half of every chunk never reached the model — a chunk whose
key sentence sat at its end embedded identically to filler (cosine 0.043
vs 0.043 for the query it should have matched; the same sentence at the
start of the chunk scored 0.232). BM25 still found those words, which is
why semantic search looked fine. Now a chunk can never exceed the model's
window, and windows only ever cut between words (a WordPiece "##"
continuation token stays with its word), so a chunk's text tokenizes the
same on its own as it did inside the document.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from ..extraction.blocks import ExtractedBlock

# Word-based defaults, used only when no tokenizer is supplied.
DEFAULT_CHUNK_SIZE = 512
DEFAULT_OVERLAP = 50
# Token-based defaults, used when `token_spans` is supplied. 256 is the
# model's window including [CLS]/[SEP]; 224 leaves room for both plus a
# margin for any tokenizer difference between a chunk on its own and the
# same text inside the whole document. 32 tokens of overlap keeps a
# sentence split across two windows whole in one of them.
DEFAULT_CHUNK_TOKENS = 224
DEFAULT_OVERLAP_TOKENS = 32

_WORD_RE = re.compile(r"\S+")

# (start, end, is_continuation): character span of one model token and
# whether it continues the previous token's word (WordPiece "##").
TokenSpans = Callable[[str], list[tuple[int, int, bool]]]


@dataclass(frozen=True)
class Chunk:
    chunk_index: int
    content: str
    start_offset: int
    end_offset: int
    page_number: int | None = None
    heading: str | None = None
    section: str | None = None


@dataclass(frozen=True)
class _BlockSpan:
    start: int
    end: int
    page_number: int | None
    heading: str | None
    section: str | None


def _flatten_blocks(blocks: list[ExtractedBlock], separator: str = "\n\n") -> tuple[str, list[_BlockSpan]]:
    parts: list[str] = []
    spans: list[_BlockSpan] = []
    offset = 0
    for block in blocks:
        start = offset
        parts.append(block.text)
        offset += len(block.text)
        spans.append(
            _BlockSpan(
                start=start,
                end=offset,
                page_number=block.page_number,
                heading=block.heading,
                section=block.section,
            )
        )
        parts.append(separator)
        offset += len(separator)
    return "".join(parts), spans


def _span_for_offset(spans: list[_BlockSpan], offset: int) -> _BlockSpan | None:
    for span in spans:
        if span.start <= offset < span.end:
            return span
    candidates = [s for s in spans if s.start <= offset]
    if candidates:
        return candidates[-1]
    return spans[0] if spans else None


def _page_groups(blocks: list[ExtractedBlock]) -> list[list[ExtractedBlock]]:
    """Consecutive blocks that share a page (or all have no page) form one
    group. A chunk is built within a group, never across two pages: a
    chunk's page_number is taken from where it starts, so a chunk that
    ran from page 1 into page 2 reported every page-2 match as "page 1".
    Found 2026-09-20 with a 3-slide deck that fit in one chunk. Blocks
    without a page (DOCX, plain text) keep the old single-stream
    behaviour and still overlap across paragraphs.
    """
    groups: list[list[ExtractedBlock]] = []
    for block in blocks:
        if groups and groups[-1][-1].page_number == block.page_number:
            groups[-1].append(block)
        else:
            groups.append([block])
    return groups


def chunk_document(
    blocks: list[ExtractedBlock],
    chunk_size: int | None = None,
    overlap: int | None = None,
    token_spans: TokenSpans | None = None,
) -> list[Chunk]:
    """`chunk_size`/`overlap` count model tokens when `token_spans` is given,
    words otherwise; each defaults to the matching constant above."""
    if chunk_size is None:
        chunk_size = DEFAULT_CHUNK_TOKENS if token_spans is not None else DEFAULT_CHUNK_SIZE
    if overlap is None:
        overlap = DEFAULT_OVERLAP_TOKENS if token_spans is not None else DEFAULT_OVERLAP
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    chunks: list[Chunk] = []
    offset_base = 0
    for group in _page_groups(blocks):
        group_chunks, consumed = _chunk_stream(group, chunk_size, overlap, len(chunks), offset_base, token_spans)
        chunks.extend(group_chunks)
        offset_base += consumed
    return chunks


@dataclass(frozen=True)
class _Unit:
    """One word of the flattened stream: where it is and how many model
    tokens it costs (1 when counting words)."""

    start: int
    end: int
    tokens: int


def _units(full_text: str, token_spans: TokenSpans | None) -> list[_Unit]:
    if token_spans is None:
        return [_Unit(m.start(), m.end(), 1) for m in _WORD_RE.finditer(full_text)]
    units: list[_Unit] = []
    for start, end, continues in token_spans(full_text):
        if continues and units and start <= units[-1].end:
            previous = units[-1]
            units[-1] = _Unit(previous.start, max(previous.end, end), previous.tokens + 1)
        else:
            units.append(_Unit(start, end, 1))
    return units


def _chunk_stream(
    blocks: list[ExtractedBlock],
    chunk_size: int,
    overlap: int,
    first_index: int,
    offset_base: int,
    token_spans: TokenSpans | None = None,
) -> tuple[list[Chunk], int]:
    """Sliding window over one flattened stream of blocks. Returns the
    chunks and the stream's character length so offsets stay document-
    global across page groups. A window takes whole words until the next
    word would push it past `chunk_size`; the next window starts far
    enough back to repeat about `overlap` units' worth of tokens."""
    full_text, spans = _flatten_blocks(blocks)
    if not full_text.strip():
        return [], len(full_text)

    units = _units(full_text, token_spans)
    if not units:
        return [], len(full_text)

    chunks: list[Chunk] = []
    chunk_index = first_index
    i = 0
    while i < len(units):
        # Grow the window word by word within the token budget. A single
        # word longer than the whole budget (a URL, a hash) still gets its
        # own chunk rather than stalling the loop.
        j = i
        used = 0
        while j < len(units) and (used + units[j].tokens <= chunk_size or j == i):
            used += units[j].tokens
            j += 1
        start_offset = units[i].start
        end_offset = units[j - 1].end
        content = full_text[start_offset:end_offset]
        span = _span_for_offset(spans, start_offset)
        chunks.append(
            Chunk(
                chunk_index=chunk_index,
                content=content,
                start_offset=offset_base + start_offset,
                end_offset=offset_base + end_offset,
                page_number=span.page_number if span else None,
                heading=span.heading if span else None,
                section=span.section if span else None,
            )
        )
        chunk_index += 1
        if j >= len(units):
            break
        # Step back from the window's end until `overlap` tokens are
        # covered, but always advance by at least one word.
        back = j
        covered = 0
        while back > i + 1 and covered + units[back - 1].tokens <= overlap:
            back -= 1
            covered += units[back].tokens
        i = back

    return chunks, len(full_text)
