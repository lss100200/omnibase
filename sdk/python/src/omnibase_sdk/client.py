"""High-level P34.2 read-only capability client."""

from __future__ import annotations

import base64
import hashlib
import ssl

from omnibase_sdk.models import (
    ArtifactReadResponse,
    ArtifactWriteResponse,
    CitationReadResponse,
    DerivedChunkWrite,
    DerivedCreateResponse,
    DerivedDeleteResponse,
    JsonValue,
    RagSearchResponse,
    RowsQuery,
    RowsReadResponse,
    SchemaReadResponse,
    require_integer,
    require_logical_id,
)
from omnibase_sdk.transport import (
    HttpTransport,
    Transport,
    WorkloadCredentialProvider,
    raise_for_error,
)


class OmniBaseClient:
    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    @classmethod
    def from_http(
        cls,
        base_url: str,
        credential_provider: WorkloadCredentialProvider,
        *,
        ssl_context: ssl.SSLContext | None = None,
        timeout_seconds: float = 10.0,
        allow_insecure_localhost: bool = False,
    ) -> OmniBaseClient:
        return cls(
            HttpTransport(
                base_url,
                credential_provider,
                ssl_context=ssl_context,
                timeout_seconds=timeout_seconds,
                allow_insecure_localhost=allow_insecure_localhost,
            )
        )

    def read_schema(self, resource_id: str) -> SchemaReadResponse:
        response = self._transport.request(
            "POST",
            "/gateway/v1/data/schema/read",
            {"resource_id": require_logical_id(resource_id, "resource_id")},
        )
        raise_for_error(response)
        return SchemaReadResponse.from_dict(response.body)

    def read_rows(self, resource_id: str, query: RowsQuery) -> RowsReadResponse:
        response = self._transport.request(
            "POST",
            "/gateway/v1/data/rows/read",
            {
                "resource_id": require_logical_id(resource_id, "resource_id"),
                "query": query.to_payload(),
            },
        )
        raise_for_error(response)
        return RowsReadResponse.from_dict(response.body)

    def rag_search(
        self,
        resource_id: str,
        query: str,
        *,
        top_k: int = 10,
        max_bytes: int | None = None,
        timeout_ms: int | None = None,
    ) -> RagSearchResponse:
        if not isinstance(query, str) or not query or len(query) > 2000:
            raise ValueError("query must contain between 1 and 2000 characters")
        top_k = require_integer(top_k, "top_k", minimum=1)
        if top_k > 20:
            raise ValueError("top_k must be between 1 and 20")
        body: dict[str, JsonValue] = {
            "resource_id": require_logical_id(resource_id, "resource_id"),
            "query": query,
            "top_k": top_k,
        }
        _add_budgets(body, max_bytes=max_bytes, timeout_ms=timeout_ms)
        response = self._transport.request("POST", "/gateway/v1/rag/search", body)
        raise_for_error(response)
        return RagSearchResponse.from_dict(response.body)

    def read_citations(
        self,
        resource_id: str,
        citation_ids: list[str] | tuple[str, ...],
        *,
        max_bytes: int | None = None,
        timeout_ms: int | None = None,
    ) -> CitationReadResponse:
        if not 1 <= len(citation_ids) <= 20:
            raise ValueError("citation_ids must contain between 1 and 20 IDs")
        normalized = [require_logical_id(item, "citation_id") for item in citation_ids]
        if len(set(normalized)) != len(normalized):
            raise ValueError("citation_ids must not contain duplicates")
        citation_values: list[JsonValue] = list(normalized)
        body: dict[str, JsonValue] = {
            "resource_id": require_logical_id(resource_id, "resource_id"),
            "citation_ids": citation_values,
        }
        _add_budgets(body, max_bytes=max_bytes, timeout_ms=timeout_ms)
        response = self._transport.request("POST", "/gateway/v1/rag/citations/read", body)
        raise_for_error(response)
        return CitationReadResponse.from_dict(response.body)

    def read_artifact(
        self,
        resource_id: str,
        resource_version: int,
        *,
        max_bytes: int = 1_048_576,
    ) -> ArtifactReadResponse:
        resource_version = require_integer(resource_version, "resource_version", minimum=1)
        max_bytes = require_integer(max_bytes, "max_bytes", minimum=1)
        if max_bytes > 1_048_576:
            raise ValueError("max_bytes must be between 1 and 1048576")
        response = self._transport.request(
            "POST",
            "/gateway/v1/artifacts/read",
            {
                "resource_id": require_logical_id(resource_id, "resource_id"),
                "resource_version": resource_version,
                "max_bytes": max_bytes,
            },
        )
        raise_for_error(response)
        return ArtifactReadResponse.from_dict(response.body)

    def write_artifact(
        self,
        *,
        idempotency_key: str,
        display_name: str,
        media_type: str,
        content: bytes,
        source_resource_ids: tuple[str, ...] = (),
    ) -> ArtifactWriteResponse:
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128:
            raise ValueError("idempotency_key is invalid")
        if not isinstance(display_name, str) or not 1 <= len(display_name) <= 200:
            raise ValueError("display_name is invalid")
        if not isinstance(content, bytes) or len(content) > 1_048_576:
            raise ValueError("content must be bytes within the artifact byte limit")
        sources = [require_logical_id(item, "source_resource_id") for item in source_resource_ids]
        if len(sources) > 32 or len(set(sources)) != len(sources):
            raise ValueError("source_resource_ids are invalid")
        source_values: list[JsonValue] = list(sources)
        response = self._transport.request(
            "POST",
            "/gateway/v1/artifacts/write",
            {
                "idempotency_key": idempotency_key,
                "display_name": display_name,
                "media_type": media_type,
                "size_bytes": len(content),
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "content_base64": base64.b64encode(content).decode("ascii"),
                "source_resource_ids": source_values,
            },
        )
        raise_for_error(response)
        return ArtifactWriteResponse.from_dict(response.body)

    def create_derived(
        self,
        *,
        idempotency_key: str,
        display_name: str,
        source_resource_ids: tuple[str, ...],
        chunks: tuple[DerivedChunkWrite, ...],
    ) -> DerivedCreateResponse:
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128:
            raise ValueError("idempotency_key is invalid")
        if not isinstance(display_name, str) or not 1 <= len(display_name) <= 200:
            raise ValueError("display_name is invalid")
        sources = [require_logical_id(item, "source_resource_id") for item in source_resource_ids]
        if not 1 <= len(sources) <= 32 or len(set(sources)) != len(sources):
            raise ValueError("source_resource_ids are invalid")
        if not 1 <= len(chunks) <= 100:
            raise ValueError("chunks must contain between 1 and 100 items")
        declared_sources = frozenset(sources)
        chunk_payloads = [chunk.to_payload(declared_sources) for chunk in chunks]
        if sum(len(chunk.content.encode("utf-8")) for chunk in chunks) > 262_144:
            raise ValueError("derived content exceeds the request budget")
        source_values: list[JsonValue] = list(sources)
        chunk_values: list[JsonValue] = []
        chunk_values.extend(chunk_payloads)
        response = self._transport.request(
            "POST",
            "/gateway/v1/rag/derived/create",
            {
                "idempotency_key": idempotency_key,
                "display_name": display_name,
                "source_resource_ids": source_values,
                "chunks": chunk_values,
            },
        )
        raise_for_error(response)
        return DerivedCreateResponse.from_dict(response.body)

    def delete_derived(
        self,
        resource_id: str,
        resource_version: int,
        *,
        idempotency_key: str,
    ) -> DerivedDeleteResponse:
        resource_version = require_integer(resource_version, "resource_version", minimum=1)
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128:
            raise ValueError("idempotency_key is invalid")
        response = self._transport.request(
            "POST",
            "/gateway/v1/rag/derived/delete",
            {
                "resource_id": require_logical_id(resource_id, "resource_id"),
                "resource_version": resource_version,
                "idempotency_key": idempotency_key,
            },
        )
        raise_for_error(response)
        return DerivedDeleteResponse.from_dict(response.body)


def _add_budgets(
    body: dict[str, JsonValue], *, max_bytes: int | None, timeout_ms: int | None
) -> None:
    if max_bytes is not None:
        max_bytes = require_integer(max_bytes, "max_bytes", minimum=1)
        if max_bytes > 1_048_576:
            raise ValueError("max_bytes must be between 1 and 1048576")
        body["max_bytes"] = max_bytes
    if timeout_ms is not None:
        timeout_ms = require_integer(timeout_ms, "timeout_ms", minimum=1)
        if timeout_ms > 5000:
            raise ValueError("timeout_ms must be between 1 and 5000")
        body["timeout_ms"] = timeout_ms
