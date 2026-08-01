"""High-level P34.2 read-only capability client."""

from __future__ import annotations

import ssl

from omnibase_sdk.models import (
    CitationReadResponse,
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
        body: dict[str, object] = {
            "resource_id": require_logical_id(resource_id, "resource_id"),
            "query": query,
            "top_k": top_k,
        }
        _add_budgets(body, max_bytes=max_bytes, timeout_ms=timeout_ms)
        response = self._transport.request("POST", "/gateway/v1/rag/search", body)  # type: ignore[arg-type]
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
        body: dict[str, object] = {
            "resource_id": require_logical_id(resource_id, "resource_id"),
            "citation_ids": normalized,
        }
        _add_budgets(body, max_bytes=max_bytes, timeout_ms=timeout_ms)
        response = self._transport.request(  # type: ignore[arg-type]
            "POST", "/gateway/v1/rag/citations/read", body
        )
        raise_for_error(response)
        return CitationReadResponse.from_dict(response.body)


def _add_budgets(
    body: dict[str, object], *, max_bytes: int | None, timeout_ms: int | None
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
