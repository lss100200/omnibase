"""Recursive text chunker for RAG.

Splits parsed documents into embedding-sized chunks (300-500 chars)
while respecting natural boundaries: paragraphs → sentences → words.

This is a Python port of WorldRAG's chunker.ts logic, adapted for:
- Chinese text (splits on 。！？ as well as .!?)
- Code blocks (preserves function boundaries)
- Citation tracking (char_start/char_end per chunk)

Algorithm (recursive character splitter):
1. Try splitting on "\n\n" (paragraphs)
2. If chunk still too big, split on "\n" (lines)
3. If still too big, split on sentence endings
4. If still too big, split on spaces/characters
5. Merge small chunks back together to approach target size
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from omnibase.core.logging import get_logger
from omnibase.rag.parser import ParsedDocument, ParsedPage

log = get_logger(__name__)


@dataclass
class TextChunk:
    """A single chunk ready for embedding."""

    content: str
    chunk_index: int
    char_start: int
    char_end: int
    page_number: int = 1
    chunk_type: str = "paragraph"
    metadata: dict = field(default_factory=dict)


# Default chunking parameters (tuned for bge-small-zh: 512 tokens max, ~400 chars optimal)
DEFAULT_CHUNK_SIZE = 400
DEFAULT_CHUNK_OVERLAP = 50
DEFAULT_SEPARATORS = [
    "\n\n\n",  # Triple newline (major section break)
    "\n\n",  # Double newline (paragraph)
    "\n",  # Single newline (line)
    "。",  # Chinese period
    "！",  # Chinese exclamation
    "？",  # Chinese question
    ". ",  # English period + space
    "! ",  # English exclamation + space
    "? ",  # English question + space
    "；",  # Chinese semicolon
    "; ",  # English semicolon + space
    "，",  # Chinese comma
    ", ",  # English comma + space
    " ",  # Space
    "",  # Character-level (last resort)
]


def chunk_document(
    doc: ParsedDocument,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    separators: list[str] | None = None,
) -> list[TextChunk]:
    """Split a parsed document into chunks suitable for embedding.

    Args:
        doc: Parsed document with pages.
        chunk_size: Target characters per chunk (400 default for bge-small-zh).
        chunk_overlap: Characters of overlap between adjacent chunks.
        separators: Custom separator hierarchy (defaults to DEFAULT_SEPARATORS).

    Returns:
        List of TextChunks with content, position, and page tracking.
    """
    if not doc.pages or not doc.full_text.strip():
        return []

    seps = separators or DEFAULT_SEPARATORS
    chunks: list[TextChunk] = []
    chunk_idx = 0

    # Track global character offset across all pages
    global_offset = 0

    for page in doc.pages:
        if not page.text.strip():
            global_offset += len(page.text) + 2
            continue

        # Split this page's text into chunks
        page_chunks = _recursive_split(
            page.text,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=seps,
        )

        for pc_text, pc_local_start in page_chunks:
            # Calculate absolute character positions (for citation backlinks)
            abs_start = page.char_offset + pc_local_start
            abs_end = abs_start + len(pc_text)

            # Detect chunk type
            chunk_type = _detect_chunk_type(pc_text)

            chunks.append(
                TextChunk(
                    content=pc_text.strip(),
                    chunk_index=chunk_idx,
                    char_start=abs_start,
                    char_end=abs_end,
                    page_number=page.page_number,
                    chunk_type=chunk_type,
                    metadata={
                        "filename": doc.filename,
                        "page": page.page_number,
                    },
                )
            )
            chunk_idx += 1

        global_offset += len(page.text) + 2

    log.info(
        "chunker.complete",
        filename=doc.filename,
        pages=len(doc.pages),
        chunks=len(chunks),
        avg_chunk_size=sum(len(c.content) for c in chunks) // max(len(chunks), 1),
    )
    return chunks


def _recursive_split(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
    separators: list[str],
) -> list[tuple[str, int]]:
    """Recursively split text using separator hierarchy.

    Returns list of (chunk_text, local_start_offset) tuples.
    """
    if len(text) <= chunk_size:
        return [(text, 0)]

    # Find the best separator that exists in the text
    sep = ""
    for s in separators:
        if s and s in text:
            sep = s
            break

    if not sep:
        # No separator found — hard split by character
        return _hard_split(text, chunk_size, chunk_overlap)

    # Split by the chosen separator
    parts = text.split(sep)
    # Reconstruct with separator included
    splits: list[tuple[str, int]] = []
    pos = 0
    for i, part in enumerate(parts):
        chunk = part if i == len(parts) - 1 else part + sep
        splits.append((chunk, pos))
        pos += len(chunk)

    # Merge small splits into target-sized chunks
    merged = _merge_splits(splits, chunk_size, chunk_overlap, sep)

    # If any merged chunk is still too big, recurse with next separator
    final: list[tuple[str, int]] = []
    remaining_seps = [s for s in separators if s != sep]
    for chunk_text, chunk_start in merged:
        if len(chunk_text) > chunk_size * 1.5 and remaining_seps:
            # Recurse with finer separators
            sub_chunks = _recursive_split(
                chunk_text, chunk_size, chunk_overlap, remaining_seps
            )
            for sub_text, sub_start in sub_chunks:
                final.append((sub_text, chunk_start + sub_start))
        else:
            final.append((chunk_text, chunk_start))

    return final


def _merge_splits(
    splits: list[tuple[str, int]],
    chunk_size: int,
    chunk_overlap: int,
    separator: str,
) -> list[tuple[str, int]]:
    """Merge small splits into chunks approaching target size.

    Handles overlap: each chunk starts with the last N chars of the previous chunk.
    """
    if not splits:
        return []

    merged: list[tuple[str, int]] = []
    current_parts: list[str] = []
    current_start = splits[0][1]
    current_len = 0

    for text, start in splits:
        if current_len + len(text) > chunk_size and current_parts:
            # Flush current chunk
            chunk_text = separator.join(current_parts) if separator else "".join(current_parts)
            # Actually we kept separator in the split text, so just concatenate
            chunk_text = "".join(current_parts)
            merged.append((chunk_text, current_start))

            # Start new chunk with overlap
            overlap_text = chunk_text[-chunk_overlap:] if chunk_overlap > 0 else ""
            current_parts = [overlap_text + text] if overlap_text else [text]
            current_start = start - len(overlap_text) if overlap_text else start
            current_len = len(current_parts[0])
        else:
            current_parts.append(text)
            current_len += len(text)

    # Flush remaining
    if current_parts:
        chunk_text = "".join(current_parts)
        merged.append((chunk_text, current_start))

    return merged


def _hard_split(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
) -> list[tuple[str, int]]:
    """Last resort: split by character count."""
    chunks: list[tuple[str, int]] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append((chunk, start))
        start = end - chunk_overlap if chunk_overlap > 0 else end
    return chunks


def _detect_chunk_type(text: str) -> str:
    """Detect whether a chunk is code, heading, or paragraph."""
    stripped = text.strip()

    # Markdown heading
    if re.match(r"^#{1,6}\s", stripped):
        return "heading"

    # Code block (fenced)
    if stripped.startswith("```") or re.search(r"^\s{4,}\w", stripped, re.MULTILINE):
        return "code"

    # Function definition patterns (Python/JS/TS/SQL)
    if re.search(r"^\s*(def |function |class |SELECT |CREATE |INSERT )", stripped, re.MULTILINE):
        return "code"

    return "paragraph"


__all__ = ["DEFAULT_CHUNK_SIZE", "TextChunk", "chunk_document"]
