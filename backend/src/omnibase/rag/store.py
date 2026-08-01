"""Tenant-scoped, version-aware RAG storage and search adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import text
from sqlalchemy.sql.elements import TextClause

from omnibase.core.db import get_session_factory
from omnibase.core.logging import get_logger
from omnibase.rag.index_metadata import (
    DimensionMismatchError,
    IndexLane,
    IndexVersion,
    get_index_lane,
    validate_dimension,
)
from omnibase.tenants.context import tenant_scope

log = get_logger(__name__)
MAX_BATCH_SIZE = 200
_DELETE_DOCUMENT_BY_VERSION: dict[IndexVersion, TextClause] = {
    IndexVersion.V1: text("DELETE FROM embeddings WHERE document_id = :doc_id"),
    IndexVersion.V2: text("DELETE FROM embeddings_v2 WHERE document_id = :doc_id"),
}


class SearchMode(StrEnum):
    """Failure behavior for one retrieval stage."""

    ONLINE = "online"
    STRICT = "strict"


class SearchStageError(RuntimeError):
    """Raised by strict search when a retrieval stage cannot execute."""

    def __init__(self, stage: str, lane: IndexLane, cause: BaseException) -> None:
        self.stage = stage
        self.lane = lane
        self.cause_type = type(cause).__name__
        super().__init__(f"{stage} search failed for {lane.version}")


@dataclass(frozen=True)
class SearchResult:
    """A single search hit."""

    chunk_id: str
    document_id: str
    content: str
    score: float
    chunk_index: int
    char_start: int | None
    char_end: int | None
    chunk_type: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SearchStageResult:
    """Results plus a safe success/failure signal for one search stage."""

    hits: list[SearchResult]
    failed: bool = False
    error_type: str | None = None


@dataclass(frozen=True)
class ChunkToInsert:
    """A chunk ready for insertion into an index lane."""

    document_id: str
    chunk_index: int
    content: str
    embedding: list[float] | None
    char_start: int | None
    char_end: int | None
    chunk_type: str = "paragraph"
    metadata: dict[str, Any] | None = None
    chunk_id: str | None = None


def _table_for_lane(lane: IndexLane) -> str:
    # Closed mapping: table identifiers never come from user input.
    return {
        IndexVersion.V1: "embeddings",
        IndexVersion.V2: "embeddings_v2",
    }[lane.version]


def _validate_chunks(chunks: list[ChunkToInsert], lane: IndexLane) -> None:
    for chunk in chunks:
        if chunk.embedding is not None:
            validate_dimension(len(chunk.embedding), lane.version)


def write_chunks(
    schema_name: str,
    chunks: list[ChunkToInsert],
    *,
    lane: IndexLane,
) -> int:
    """Write chunks to exactly one lane; v2 is idempotent by document/chunk."""
    if not chunks:
        return 0
    _validate_chunks(chunks, lane)
    table = _table_for_lane(lane)
    factory = get_session_factory()
    total = 0

    with tenant_scope(schema_name):
        for batch_start in range(0, len(chunks), MAX_BATCH_SIZE):
            batch = chunks[batch_start : batch_start + MAX_BATCH_SIZE]
            session = factory()
            try:
                values_sql: list[str] = []
                params: dict[str, Any] = {}
                for local_idx, chunk in enumerate(batch):
                    pfx = f"c{batch_start + local_idx}"
                    id_expr = f"CAST(:{pfx}_id AS uuid)" if chunk.chunk_id else "gen_random_uuid()"
                    if chunk.chunk_id:
                        params[f"{pfx}_id"] = chunk.chunk_id
                    if chunk.embedding is None:
                        embedding_expr = "NULL"
                    else:
                        embedding_expr = f"CAST(:{pfx}_emb AS vector)"
                        params[f"{pfx}_emb"] = "[" + ",".join(str(v) for v in chunk.embedding) + "]"
                    values_sql.append(
                        f"({id_expr}, :{pfx}_doc, :{pfx}_idx, :{pfx}_content, "
                        f"{embedding_expr}, :{pfx}_cs, :{pfx}_ce, :{pfx}_ct, "
                        f"CAST(:{pfx}_meta AS jsonb))"
                    )
                    params.update(
                        {
                            f"{pfx}_doc": chunk.document_id,
                            f"{pfx}_idx": chunk.chunk_index,
                            f"{pfx}_content": chunk.content,
                            f"{pfx}_cs": chunk.char_start,
                            f"{pfx}_ce": chunk.char_end,
                            f"{pfx}_ct": chunk.chunk_type,
                            f"{pfx}_meta": _dict_to_json(chunk.metadata or {}),
                        }
                    )

                conflict_sql = ""
                if lane.version is IndexVersion.V2:
                    conflict_sql = """
                    ON CONFLICT (document_id, chunk_index) DO UPDATE SET
                        content = EXCLUDED.content,
                        embedding = EXCLUDED.embedding,
                        char_start = EXCLUDED.char_start,
                        char_end = EXCLUDED.char_end,
                        chunk_type = EXCLUDED.chunk_type,
                        metadata = EXCLUDED.metadata
                    """
                statement = text(
                    f"""
                    INSERT INTO {table}
                        (id, document_id, chunk_index, content, embedding,
                         char_start, char_end, chunk_type, metadata)
                    VALUES {", ".join(values_sql)}
                    {conflict_sql}
                    """
                )
                result = session.execute(statement, params)
                total += _result_rowcount(result)
                session.commit()
            except Exception:
                session.rollback()
                log.error(
                    "rag.store.write_failed",
                    lane=str(lane.version),
                    batch_start=batch_start,
                    batch_size=len(batch),
                    exc_info=True,
                )
                raise
            finally:
                session.close()
    return total


def insert_chunks(schema_name: str, chunks: list[ChunkToInsert]) -> int:
    """Backwards-compatible v1 insert wrapper."""
    return write_chunks(schema_name, chunks, lane=get_index_lane(IndexVersion.V1))


def upsert_chunks_v2(schema_name: str, chunks: list[ChunkToInsert]) -> int:
    """Idempotently upsert v2 chunks by ``document_id + chunk_index``."""
    return write_chunks(schema_name, chunks, lane=get_index_lane(IndexVersion.V2))


def read_document_chunks(
    schema_name: str,
    document_id: str,
    *,
    lane: IndexLane | None = None,
) -> list[ChunkToInsert]:
    """Read stored chunks for backfill without exposing document bodies to tasks."""
    resolved = lane or get_index_lane(IndexVersion.V1)
    table = _table_for_lane(resolved)
    factory = get_session_factory()
    with tenant_scope(schema_name):
        session = factory()
        try:
            rows = session.execute(
                text(
                    f"""SELECT id::text, document_id::text, chunk_index, content,
                               char_start, char_end, chunk_type, metadata
                        FROM {table}
                        WHERE document_id = :doc_id
                        ORDER BY chunk_index"""
                ),
                {"doc_id": document_id},
            ).fetchall()
            return [
                ChunkToInsert(
                    chunk_id=row[0],
                    document_id=row[1],
                    chunk_index=row[2],
                    content=row[3],
                    embedding=None,
                    char_start=row[4],
                    char_end=row[5],
                    chunk_type=row[6],
                    metadata=row[7] or {},
                )
                for row in rows
            ]
        finally:
            session.close()


def search_vector_lane(
    schema_name: str,
    query_vector: list[float],
    *,
    lane: IndexLane,
    top_k: int = 100,
    document_id_filter: str | None = None,
    mode: SearchMode = SearchMode.STRICT,
    validate_vector: bool = True,
) -> SearchStageResult:
    """Search one declared vector lane with explicit failure semantics."""
    if validate_vector:
        validate_dimension(len(query_vector), lane.version)
    table = _table_for_lane(lane)
    params: dict[str, Any] = {
        "query_vec": "[" + ",".join(str(v) for v in query_vector) + "]",
        "top_k": top_k,
    }
    filter_clause = ""
    if document_id_filter is not None:
        filter_clause = "AND document_id = :doc_id"
        params["doc_id"] = document_id_filter
    sql = text(
        f"""SELECT id::text, document_id::text, content,
                   1 - (embedding <=> CAST(:query_vec AS vector)) AS similarity,
                   chunk_index, char_start, char_end, chunk_type, metadata
            FROM {table}
            WHERE embedding IS NOT NULL {filter_clause}
            ORDER BY embedding <=> CAST(:query_vec AS vector)
            LIMIT :top_k"""
    )
    return _run_search(schema_name, lane, "vector", sql, params, mode)


def search_bm25_lane(
    schema_name: str,
    query: str,
    *,
    lane: IndexLane,
    top_k: int = 100,
    document_id_filter: str | None = None,
    mode: SearchMode = SearchMode.STRICT,
) -> SearchStageResult:
    """Search BM25 in the same declared lane used by vector search."""
    table = _table_for_lane(lane)
    params: dict[str, Any] = {"query": query, "top_k": top_k}
    filter_clause = ""
    if document_id_filter:
        filter_clause = "AND document_id = :doc_id"
        params["doc_id"] = document_id_filter
    sql = text(
        f"""SELECT id::text, document_id::text, content,
                   ts_rank(tsv, plainto_tsquery('pg_catalog.simple', :query)) AS rank,
                   chunk_index, char_start, char_end, chunk_type, metadata
            FROM {table}
            WHERE tsv @@ plainto_tsquery('pg_catalog.simple', :query) {filter_clause}
            ORDER BY rank DESC LIMIT :top_k"""
    )
    return _run_search(schema_name, lane, "bm25", sql, params, mode)


def _run_search(
    schema_name: str,
    lane: IndexLane,
    stage: str,
    sql: Any,
    params: dict[str, Any],
    mode: SearchMode,
) -> SearchStageResult:
    factory = get_session_factory()
    with tenant_scope(schema_name):
        session = factory()
        try:
            rows = session.execute(sql, params).fetchall()
            hits = [
                SearchResult(
                    chunk_id=row[0],
                    document_id=row[1],
                    content=row[2],
                    score=float(row[3]) if row[3] is not None else 0.0,
                    chunk_index=row[4],
                    char_start=row[5],
                    char_end=row[6],
                    chunk_type=row[7],
                    metadata=row[8] or {},
                )
                for row in rows
            ]
            return SearchStageResult(hits=hits)
        except Exception as exc:
            log.error(
                "rag.store.search_failed",
                stage=stage,
                lane=str(lane.version),
                error_type=type(exc).__name__,
            )
            if mode is SearchMode.STRICT:
                raise SearchStageError(stage, lane, exc) from exc
            return SearchStageResult(hits=[], failed=True, error_type=type(exc).__name__)
        finally:
            session.close()


def vector_search(
    schema_name: str,
    query_vector: list[float],
    top_k: int = 100,
    document_id_filter: str | None = None,
) -> list[SearchResult]:
    """Legacy online v1 vector-search wrapper."""
    return search_vector_lane(
        schema_name,
        query_vector,
        lane=get_index_lane(IndexVersion.V1),
        top_k=top_k,
        document_id_filter=document_id_filter,
        mode=SearchMode.ONLINE,
        validate_vector=False,
    ).hits


def bm25_search(
    schema_name: str,
    query: str,
    top_k: int = 100,
    document_id_filter: str | None = None,
) -> list[SearchResult]:
    """Legacy online v1 BM25-search wrapper."""
    return search_bm25_lane(
        schema_name,
        query,
        lane=get_index_lane(IndexVersion.V1),
        top_k=top_k,
        document_id_filter=document_id_filter,
        mode=SearchMode.ONLINE,
    ).hits


def delete_document_chunks(
    schema_name: str,
    document_id: str,
    *,
    lane: IndexLane | None = None,
) -> int:
    """Delete document chunks from one lane; the default remains v1."""
    resolved = lane or get_index_lane(IndexVersion.V1)
    statement = _DELETE_DOCUMENT_BY_VERSION[resolved.version]
    factory = get_session_factory()
    with tenant_scope(schema_name):
        session = factory()
        try:
            result = session.execute(
                statement,
                {"doc_id": document_id},
            )
            session.commit()
            return getattr(result, "rowcount", 0) or 0
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _dict_to_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _result_rowcount(result: object) -> int:
    """Read DML rowcount without assuming every SQLAlchemy Result is cursor-backed."""
    rowcount = getattr(result, "rowcount", None)
    return rowcount if isinstance(rowcount, int) else 0


__all__ = [
    "ChunkToInsert",
    "DimensionMismatchError",
    "SearchMode",
    "SearchResult",
    "SearchStageError",
    "SearchStageResult",
    "bm25_search",
    "delete_document_chunks",
    "insert_chunks",
    "read_document_chunks",
    "search_bm25_lane",
    "search_vector_lane",
    "upsert_chunks_v2",
    "vector_search",
    "write_chunks",
]
