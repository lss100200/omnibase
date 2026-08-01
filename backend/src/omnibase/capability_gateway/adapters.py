"""Read-only PostgreSQL and canonical-RAG domain adapters."""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy import String, bindparam, cast, column, func, select, table, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import BindParameter

from omnibase.capability_gateway.contracts import (
    CitationRead,
    CitationReadResponse,
    CitationResult,
    ColumnRead,
    DataRowsResponse,
    DataRowsResult,
    RagSearchResponse,
    RagSearchResult,
    ReadQuery,
    ResourceDescriptor,
    SearchHitRead,
    VerifiedCapability,
)
from omnibase.capability_gateway.query import (
    CursorCodec,
    CursorScope,
    QueryContractError,
    compile_select,
    parse_postgres_binding,
    query_hash,
)
from omnibase.capability_gateway.resolver import PhysicalLocatorStore
from omnibase.rag.index_metadata import IndexVersion, get_index_lane
from omnibase.rag.reranker import rerank
from omnibase.rag.retriever import hybrid_search_detailed
from omnibase.rag.store import SearchMode

_SCHEMA = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_RAG_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gateway-rag")
_RAG_ADMISSION = threading.BoundedSemaphore(4)


class AdapterError(Exception):
    """Safe adapter failure; underlying exceptions are never returned."""


class ResultBudgetExceeded(AdapterError):
    """The first result cannot fit in the caller's response budget."""


class UnavailableDataReadAdapter:
    """Fail closed when no independently keyed cursor/data adapter is configured."""

    def read_schema(self, *args, **kwargs):
        del args, kwargs
        raise AdapterError

    def read_rows(self, *args, **kwargs):
        del args, kwargs
        raise AdapterError


class UnavailableRagReadAdapter:
    def search(self, *args, **kwargs):
        del args, kwargs
        raise AdapterError

    def read_citations(self, *args, **kwargs):
        del args, kwargs
        raise AdapterError


@runtime_checkable
class DataReadAdapter(Protocol):
    def read_schema(
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        resource: ResourceDescriptor,
    ) -> list[ColumnRead]: ...

    def read_rows(
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        resource: ResourceDescriptor,
        query: ReadQuery,
    ) -> DataRowsResult: ...


@runtime_checkable
class RagReadAdapter(Protocol):
    def search(
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        resource: ResourceDescriptor,
        query: str,
        top_k: int,
        timeout_ms: int,
        max_bytes: int,
    ) -> RagSearchResult: ...

    def read_citations(
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        resource: ResourceDescriptor,
        citation_ids: list[str],
        timeout_ms: int,
        max_bytes: int,
    ) -> CitationResult: ...


class PostgresDataReadAdapter:
    def __init__(self, locator_store: PhysicalLocatorStore, cursor_codec: CursorCodec) -> None:
        self._locator_store = locator_store
        self._cursor_codec = cursor_codec

    def _binding(
        self,
        session: Session,
        capability: VerifiedCapability,
        resource: ResourceDescriptor,
    ):
        locator = self._locator_store.get_locator(
            session,
            capability=capability,
            resource=resource,
        )
        return parse_postgres_binding(locator)

    def read_schema(
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        resource: ResourceDescriptor,
    ) -> list[ColumnRead]:
        try:
            binding = self._binding(session, capability, resource)
            return [
                ColumnRead(
                    id=UUID(item.logical_id),
                    display_name=item.display_name,
                    type=item.data_type,
                    nullable=item.nullable,
                )
                for item in binding.columns.values()
            ]
        except QueryContractError:
            raise
        except Exception as exc:
            raise AdapterError from exc

    def read_rows(
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        resource: ResourceDescriptor,
        query: ReadQuery,
    ) -> DataRowsResult:
        try:
            binding = self._binding(session, capability, resource)
            cursor_scope = CursorScope(
                tenant_id=capability.tenant_id,
                resource_id=resource.id,
                resource_version=resource.version,
                query_hash=query_hash(query),
            )
            offset = self._cursor_codec.decode(query.cursor, cursor_scope)
            session.execute(
                text("SELECT set_config('statement_timeout', :timeout, true)"),
                {"timeout": f"{query.timeout_ms}ms"},
            )
            sizes = list(
                session.execute(compile_select(binding, query, offset=offset, size_only=True))
                .scalars()
                .fetchmany(query.limit + 1)
            )
            safe_count = 0
            physical_bytes = 0
            for raw_size in sizes[: query.limit]:
                size = int(raw_size or 0)
                if size < 0 or size > query.max_bytes:
                    if safe_count == 0:
                        raise ResultBudgetExceeded
                    break
                if physical_bytes + size > query.max_bytes:
                    break
                physical_bytes += size
                safe_count += 1
            if sizes and safe_count == 0:
                raise ResultBudgetExceeded
            raw_rows = (
                session.execute(
                    compile_select(
                        binding,
                        query,
                        offset=offset,
                        limit_override=safe_count,
                    )
                ).fetchmany(safe_count)
                if safe_count
                else []
            )
        except (QueryContractError, ResultBudgetExceeded, AdapterError):
            raise
        except Exception as exc:
            raise AdapterError from exc
        has_more = len(sizes) > safe_count
        selected = raw_rows
        logical_ids = [str(item) for item in query.columns]
        rows: list[dict[str, object]] = []
        byte_truncated = False
        for row in selected:
            item = {
                logical_id: _json_scalar(value)
                for logical_id, value in zip(logical_ids, row, strict=True)
            }
            candidate_rows = [*rows, item]
            candidate_truncated = has_more or len(candidate_rows) < len(selected)
            candidate_cursor = (
                self._cursor_codec.encode(offset + len(candidate_rows), cursor_scope)
                if candidate_truncated
                else None
            )
            candidate = DataRowsResponse(
                resource_id=UUID(resource.id),
                resource_version=resource.version,
                rows=candidate_rows,  # type: ignore[arg-type]
                next_cursor=candidate_cursor,
                row_count=len(candidate_rows),
                bytes_out=0,
                truncated=candidate_truncated,
            )
            if _response_size(candidate) > query.max_bytes:
                if not rows:
                    raise ResultBudgetExceeded
                byte_truncated = True
                break
            rows.append(item)
        truncated = has_more or byte_truncated
        next_cursor = (
            self._cursor_codec.encode(offset + len(rows), cursor_scope)
            if truncated and rows
            else None
        )
        final = DataRowsResponse(
            resource_id=UUID(resource.id),
            resource_version=resource.version,
            rows=rows,  # type: ignore[arg-type]
            next_cursor=next_cursor,
            row_count=len(rows),
            bytes_out=0,
            truncated=truncated,
        )
        bytes_out = _response_size(final)
        if bytes_out > query.max_bytes:
            raise ResultBudgetExceeded
        return DataRowsResult(
            rows=rows,  # type: ignore[arg-type]
            next_cursor=next_cursor,
            bytes_out=bytes_out,
            truncated=truncated,
        )


class CanonicalRagReadAdapter:
    """Read V1 canonical RAG only; P34.2 does not change index authority."""

    def __init__(self, locator_store: PhysicalLocatorStore) -> None:
        self._locator_store = locator_store

    def _locator(
        self,
        session: Session,
        capability: VerifiedCapability,
        resource: ResourceDescriptor,
    ) -> tuple[str, str | None]:
        locator = self._locator_store.get_locator(
            session,
            capability=capability,
            resource=resource,
        )
        schema = locator.get("schema")
        document_id = locator.get("document_id")
        if locator.get("adapter") != "canonical_rag_v1":
            raise QueryContractError
        if not isinstance(schema, str) or not _SCHEMA.fullmatch(schema):
            raise QueryContractError
        if document_id is not None:
            try:
                document_id = str(UUID(str(document_id)))
            except (TypeError, ValueError) as exc:
                raise QueryContractError from exc
        return schema, document_id

    def search(
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        resource: ResourceDescriptor,
        query: str,
        top_k: int,
        timeout_ms: int,
        max_bytes: int,
    ) -> RagSearchResult:
        schema, document_id = self._locator(session, capability, resource)

        def execute_search():
            detailed = hybrid_search_detailed(
                schema_name=schema,
                query=query,
                top_k=min(100, top_k * 5),
                document_id_filter=document_id,
                lane=get_index_lane(IndexVersion.V1),
                mode=SearchMode.ONLINE,
            )
            return rerank(query, detailed.results, top_k=top_k)

        if not _RAG_ADMISSION.acquire(blocking=False):
            raise AdapterError
        try:
            future = _RAG_EXECUTOR.submit(execute_search)
        except Exception as exc:
            _RAG_ADMISSION.release()
            raise AdapterError from exc
        future.add_done_callback(lambda _future: _RAG_ADMISSION.release())
        try:
            ranked = future.result(timeout=timeout_ms / 1000)
        except FutureTimeoutError as exc:
            # This bounds caller latency, not a running thread's lifetime. Pending
            # work is cancelled; running work retains one of four admission slots
            # until the existing RAG call ends and closes its own database session.
            future.cancel()
            raise AdapterError from exc
        hits: list[SearchHitRead] = []
        truncated = len(ranked) > top_k
        for item in ranked[:top_k]:
            hit = SearchHitRead(
                citation_id=UUID(item.chunk.chunk_id),
                document_id=UUID(item.chunk.document_id),
                score=item.chunk.score,
                snippet=item.chunk.content[:500],
                page_number=item.chunk.metadata.get("page", 1),
            )
            candidate_hits = [*hits, hit]
            candidate = RagSearchResponse(
                resource_id=UUID(resource.id),
                results=candidate_hits,
                total_found=len(candidate_hits),
                bytes_out=0,
                truncated=truncated or len(candidate_hits) < len(ranked[:top_k]),
            )
            if _response_size(candidate) > max_bytes:
                if not hits:
                    raise ResultBudgetExceeded
                truncated = True
                break
            hits.append(hit)
        final = RagSearchResponse(
            resource_id=UUID(resource.id),
            results=hits,
            total_found=len(hits),
            bytes_out=0,
            truncated=truncated,
        )
        bytes_out = _response_size(final)
        if bytes_out > max_bytes:
            raise ResultBudgetExceeded
        return RagSearchResult(hits=hits, bytes_out=bytes_out, truncated=truncated)

    def read_citations(  # noqa: C901 - explicit fail-closed preflight/read phases
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        resource: ResourceDescriptor,
        citation_ids: list[str],
        timeout_ms: int,
        max_bytes: int,
    ) -> CitationResult:
        try:
            schema, document_id = self._locator(session, capability, resource)
            session.execute(
                text("SELECT set_config('statement_timeout', :timeout, true)"),
                {"timeout": f"{timeout_ms}ms"},
            )
            source = table(
                "embeddings",
                column("id"),
                column("document_id"),
                column("content"),
                column("metadata"),
                column("char_start"),
                column("char_end"),
                column("chunk_index"),
                schema=schema,
            )
            requested: BindParameter[object] = bindparam("citation_ids", expanding=True)
            size_statement = (
                select(
                    cast(source.c.id, String),
                    func.octet_length(source.c.content),
                )
                .where(source.c.id.in_(requested))
                .order_by(source.c.chunk_index)
            )
            if document_id is not None:
                size_statement = size_statement.where(source.c.document_id == document_id)
            size_rows = session.execute(size_statement, {"citation_ids": citation_ids}).fetchmany(
                len(citation_ids) + 1
            )
            if {row[0] for row in size_rows} != set(citation_ids):
                raise QueryContractError
            safe_ids: list[str] = []
            content_bytes = 0
            for citation_id, raw_size in size_rows:
                size = int(raw_size or 0)
                if size < 0 or size > max_bytes:
                    if not safe_ids:
                        raise ResultBudgetExceeded
                    break
                if content_bytes + size > max_bytes:
                    break
                content_bytes += size
                safe_ids.append(citation_id)
            if not safe_ids:
                raise ResultBudgetExceeded
            statement = (
                select(
                    cast(source.c.id, String),
                    cast(source.c.document_id, String),
                    source.c.content,
                    source.c.metadata,
                    source.c.char_start,
                    source.c.char_end,
                )
                .where(source.c.id.in_(bindparam("safe_ids", expanding=True)))
                .order_by(source.c.chunk_index)
            )
            if document_id is not None:
                statement = statement.where(source.c.document_id == document_id)
            rows = session.execute(statement, {"safe_ids": safe_ids}).fetchmany(len(safe_ids))
        except (QueryContractError, ResultBudgetExceeded, AdapterError):
            raise
        except Exception as exc:
            raise AdapterError from exc
        citations: list[CitationRead] = []
        truncated = len(safe_ids) < len(citation_ids)
        for row in rows:
            metadata = row[3] if isinstance(row[3], dict) else {}
            citation = CitationRead(
                citation_id=UUID(row[0]),
                document_id=UUID(row[1]),
                content=row[2],
                page_number=metadata.get("page", 1),
                char_start=row[4],
                char_end=row[5],
            )
            candidate_citations = [*citations, citation]
            candidate = CitationReadResponse(
                resource_id=UUID(resource.id),
                citations=candidate_citations,
                bytes_out=0,
                truncated=len(candidate_citations) < len(rows),
            )
            if _response_size(candidate) > max_bytes:
                if not citations:
                    raise ResultBudgetExceeded
                truncated = True
                break
            citations.append(citation)
        final = CitationReadResponse(
            resource_id=UUID(resource.id),
            citations=citations,
            bytes_out=0,
            truncated=truncated,
        )
        bytes_out = _response_size(final)
        if bytes_out > max_bytes:
            raise ResultBudgetExceeded
        return CitationResult(citations=citations, bytes_out=bytes_out, truncated=truncated)


__all__ = [
    "AdapterError",
    "CanonicalRagReadAdapter",
    "DataReadAdapter",
    "PostgresDataReadAdapter",
    "RagReadAdapter",
    "ResultBudgetExceeded",
    "UnavailableDataReadAdapter",
    "UnavailableRagReadAdapter",
]


def _json_scalar(value: object):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise AdapterError


def _response_size(model) -> int:
    """Measure the final response envelope, including its own byte count."""
    size = 0
    for _ in range(4):
        candidate = model.model_copy(update={"bytes_out": size})
        measured = len(candidate.model_dump_json().encode("utf-8"))
        if measured == size:
            return measured
        size = measured
    return size
