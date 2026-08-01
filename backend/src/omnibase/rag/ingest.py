"""RAG ingest pipeline: document → parse → chunk → embed → store.

This module orchestrates the full ingest pipeline. It can run:
- Synchronously (Phase 1 default — simple, no Celery required)
- Asynchronously via Celery (Phase 1.5 — for large files)

The pipeline is idempotent: re-ingesting a document deletes old chunks first.
"""

from __future__ import annotations

from dataclasses import dataclass

from omnibase.core.config import get_settings
from omnibase.core.logging import get_logger
from omnibase.rag.chunker import chunk_document
from omnibase.rag.embedding import embed_batch, embed_documents_for_version
from omnibase.rag.index_metadata import IndexVersion, get_index_lane
from omnibase.rag.parser import parse_document
from omnibase.rag.store import (
    ChunkToInsert,
    delete_document_chunks,
    insert_chunks,
    upsert_chunks_v2,
)

log = get_logger(__name__)


@dataclass
class IngestResult:
    """Result of an ingest operation."""

    document_id: str
    chunks_created: int
    chunks_embedded: int
    parse_error: str | None = None


def ingest_document(
    schema_name: str,
    document_id: str,
    file_data: bytes,
    filename: str,
    mime_type: str,
) -> IngestResult:
    """Full ingest pipeline: parse → chunk → embed → store.

    Args:
        schema_name: Tenant schema (for DB operations).
        document_id: Document UUID.
        file_data: Raw file bytes.
        filename: Original filename.
        mime_type: MIME type for parser routing.

    Returns:
        IngestResult with counts and any parse errors.
    """
    log.info(
        "ingest.start",
        document_id=document_id,
        filename=filename,
        mime_type=mime_type,
        size_bytes=len(file_data),
    )

    # 1. Parse document → structured text
    parsed = parse_document(file_data, filename, mime_type)

    if not parsed.full_text.strip():
        log.warning("ingest.empty_text", document_id=document_id, filename=filename)
        return IngestResult(
            document_id=document_id,
            chunks_created=0,
            chunks_embedded=0,
            parse_error="No extractable text content",
        )

    # 2. Chunk → embedding-sized pieces
    chunks = chunk_document(parsed)
    if not chunks:
        return IngestResult(
            document_id=document_id,
            chunks_created=0,
            chunks_embedded=0,
            parse_error="Chunking produced no chunks",
        )

    log.info(
        "ingest.chunked",
        document_id=document_id,
        chunks=len(chunks),
        avg_size=sum(len(c.content) for c in chunks) // len(chunks),
    )

    # 3. Embed all chunks (batch)
    texts = [c.content for c in chunks]
    vectors = embed_batch(texts, batch_size=32)
    embedded_count = sum(1 for v in vectors if v is not None)

    if embedded_count == 0:
        log.error(
            "ingest.embed_failed_all",
            document_id=document_id,
            msg="No chunks were embedded (model unavailable?)",
        )
        return IngestResult(
            document_id=document_id,
            chunks_created=0,
            chunks_embedded=0,
            parse_error="Embedding model unavailable",
        )

    # 4. Delete old chunks (idempotent re-ingest)
    delete_document_chunks(schema_name, document_id)

    # 5. Insert chunks + vectors into pgvector
    chunks_to_insert = [
        ChunkToInsert(
            document_id=document_id,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            embedding=vec,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            chunk_type=chunk.chunk_type,
            metadata=chunk.metadata,
        )
        for chunk, vec in zip(chunks, vectors, strict=False)
    ]

    inserted = insert_chunks(schema_name, chunks_to_insert)

    # During migration v1 remains authoritative. The optional v2 shadow write is
    # best-effort and never changes the v1 ingest outcome.
    settings = get_settings()
    shadow_inserted = 0
    if settings.embedding_shadow_index_version is IndexVersion.V2:
        try:
            v2_vectors = embed_documents_for_version(texts, IndexVersion.V2, batch_size=32)
            v2_chunks = [
                ChunkToInsert(
                    document_id=document_id,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    embedding=vector,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    chunk_type=chunk.chunk_type,
                    metadata=chunk.metadata,
                )
                for chunk, vector in zip(chunks, v2_vectors, strict=True)
            ]
            shadow_inserted = upsert_chunks_v2(schema_name, v2_chunks)
        except Exception as exc:
            log.warning(
                "ingest.shadow_write_failed",
                document_id=document_id,
                lane=str(get_index_lane(IndexVersion.V2).version),
                error_type=type(exc).__name__,
            )

    log.info(
        "ingest.complete",
        document_id=document_id,
        filename=filename,
        chunks_inserted=inserted,
        chunks_embedded=embedded_count,
        shadow_chunks=shadow_inserted,
    )

    return IngestResult(
        document_id=document_id,
        chunks_created=inserted,
        chunks_embedded=embedded_count,
        parse_error=None,
    )


__all__ = ["IngestResult", "ingest_document"]
