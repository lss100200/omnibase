"""Resumable v1-to-v2 chunk backfill using durable per-document state."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text

from omnibase.core.db import get_session_factory
from omnibase.core.logging import get_logger
from omnibase.rag.embedding import embed_documents_for_version
from omnibase.rag.index_metadata import IndexVersion
from omnibase.rag.store import ChunkToInsert, read_document_chunks, upsert_chunks_v2
from omnibase.tenants.context import tenant_scope

log = get_logger(__name__)
MAX_BACKFILL_ATTEMPTS = 4
ERROR_DETAIL_MAX = 500


@dataclass(frozen=True)
class BackfillResult:
    document_id: str
    status: str
    chunks_upserted: int
    attempt_count: int


def backfill_document(schema_name: str, document_id: str) -> BackfillResult:
    """Read authoritative v1 chunks and idempotently upsert their v2 equivalents."""
    attempt = _mark_building(schema_name, document_id)
    if attempt > MAX_BACKFILL_ATTEMPTS:
        _mark_failed(schema_name, document_id, "Backfill attempts exhausted")
        return BackfillResult(document_id, "exhausted", 0, attempt)

    try:
        source = read_document_chunks(schema_name, document_id)
        if not source:
            _mark_ready(schema_name, document_id, 0)
            return BackfillResult(document_id, "ready", 0, attempt)
        vectors = embed_documents_for_version(
            [chunk.content for chunk in source], IndexVersion.V2, batch_size=32
        )
        target = [
            ChunkToInsert(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                embedding=vector,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                chunk_type=chunk.chunk_type,
                metadata=chunk.metadata,
            )
            for chunk, vector in zip(source, vectors, strict=True)
        ]
        upserted = upsert_chunks_v2(schema_name, target)
        _mark_ready(schema_name, document_id, len(target))
        return BackfillResult(document_id, "ready", upserted, attempt)
    except Exception as exc:
        _mark_failed(schema_name, document_id, type(exc).__name__)
        raise


def _mark_building(schema_name: str, document_id: str) -> int:
    factory = get_session_factory()
    with tenant_scope(schema_name):
        session = factory()
        try:
            row = session.execute(
                text(
                    """INSERT INTO rag_document_index_state
                       (document_id, index_version, readiness, attempt_count,
                        last_attempt_at, error_detail, updated_at)
                       VALUES (:doc_id, 2, 'building', 1, now(), NULL, now())
                       ON CONFLICT (document_id, index_version) DO UPDATE SET
                         readiness = 'building',
                         attempt_count = rag_document_index_state.attempt_count + 1,
                         last_attempt_at = now(), error_detail = NULL, updated_at = now()
                       RETURNING attempt_count"""
                ),
                {"doc_id": document_id},
            ).scalar_one()
            session.commit()
            return int(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _mark_ready(schema_name: str, document_id: str, chunk_count: int) -> None:
    _update_state(
        schema_name,
        document_id,
        """readiness = 'ready', chunk_count = :chunk_count, ready_at = now(),
           error_detail = NULL, updated_at = now()""",
        {"chunk_count": chunk_count},
    )


def _mark_failed(schema_name: str, document_id: str, error_type: str) -> None:
    _update_state(
        schema_name,
        document_id,
        "readiness = 'failed', error_detail = :detail, updated_at = now()",
        {"detail": f"Backfill failed ({error_type})"[:ERROR_DETAIL_MAX]},
    )


def _update_state(
    schema_name: str,
    document_id: str,
    set_clause: str,
    params: dict[str, object],
) -> None:
    factory = get_session_factory()
    with tenant_scope(schema_name):
        session = factory()
        try:
            session.execute(
                text(
                    f"""UPDATE rag_document_index_state SET {set_clause}
                        WHERE document_id = :doc_id AND index_version = 2"""
                ),
                {"doc_id": document_id, **params},
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


__all__ = ["MAX_BACKFILL_ATTEMPTS", "BackfillResult", "backfill_document"]
