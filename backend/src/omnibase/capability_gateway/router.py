"""Workload-only HTTP surface for P34.2 read capabilities."""

from __future__ import annotations

import re
from collections.abc import Iterator
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from omnibase.capability_gateway.contracts import (
    CitationReadRequest,
    CitationReadResponse,
    DataRowsRequest,
    DataRowsResponse,
    DataSchemaResponse,
    ErrorEnvelope,
    RagSearchRequest,
    RagSearchResponse,
    ResourceRequest,
    WorkloadCredential,
)
from omnibase.capability_gateway.security import get_workload_credential
from omnibase.capability_gateway.service import GatewayService
from omnibase.core.db import get_session_factory

router = APIRouter(prefix="/gateway/v1", tags=["capability-gateway"])
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_REQUEST_ID_HEADER: dict[str, dict[str, object]] = {
    "X-Request-Id": {
        "description": "Safe request correlation identifier",
        "schema": {"type": "string", "maxLength": 64},
    }
}
_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {
        "model": ErrorEnvelope,
        "description": "Capability authentication failed",
        "headers": _REQUEST_ID_HEADER,
    },
    403: {
        "model": ErrorEnvelope,
        "description": "Capability scope denied",
        "headers": _REQUEST_ID_HEADER,
    },
    404: {
        "model": ErrorEnvelope,
        "description": "Logical resource not found",
        "headers": _REQUEST_ID_HEADER,
    },
    413: {
        "model": ErrorEnvelope,
        "description": "Response budget exceeded",
        "headers": _REQUEST_ID_HEADER,
    },
    422: {
        "model": ErrorEnvelope,
        "description": "Request validation failed",
        "headers": _REQUEST_ID_HEADER,
    },
    429: {
        "model": ErrorEnvelope,
        "description": "Capability budget exceeded",
        "headers": _REQUEST_ID_HEADER,
    },
    503: {
        "model": ErrorEnvelope,
        "description": "Read adapter unavailable",
        "headers": _REQUEST_ID_HEADER,
    },
}
_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {"headers": _REQUEST_ID_HEADER},
    **_ERROR_RESPONSES,
}


def get_gateway_db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_gateway_service(request: Request) -> GatewayService:
    return request.app.state.gateway_service


def get_request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    return (
        request_id
        if isinstance(request_id, str) and _REQUEST_ID.fullmatch(request_id)
        else str(uuid4())
    )


@router.post(
    "/data/schema/read",
    response_model=DataSchemaResponse,
    operation_id="gateway_data_schema_read",
    responses=_RESPONSES,
)
def read_data_schema(
    payload: ResourceRequest,
    credential: WorkloadCredential = Depends(get_workload_credential),
    session: Session = Depends(get_gateway_db),
    service: GatewayService = Depends(get_gateway_service),
    request_id: str = Depends(get_request_id),
) -> DataSchemaResponse:
    return service.read_schema(session, credential, payload, request_id)


@router.post(
    "/data/rows/read",
    response_model=DataRowsResponse,
    operation_id="gateway_data_rows_read",
    responses=_RESPONSES,
)
def read_data_rows(
    payload: DataRowsRequest,
    credential: WorkloadCredential = Depends(get_workload_credential),
    session: Session = Depends(get_gateway_db),
    service: GatewayService = Depends(get_gateway_service),
    request_id: str = Depends(get_request_id),
) -> DataRowsResponse:
    return service.read_rows(session, credential, payload, request_id)


@router.post(
    "/rag/search",
    response_model=RagSearchResponse,
    operation_id="gateway_rag_search",
    responses=_RESPONSES,
)
def search_rag(
    payload: RagSearchRequest,
    credential: WorkloadCredential = Depends(get_workload_credential),
    session: Session = Depends(get_gateway_db),
    service: GatewayService = Depends(get_gateway_service),
    request_id: str = Depends(get_request_id),
) -> RagSearchResponse:
    return service.rag_search(session, credential, payload, request_id)


@router.post(
    "/rag/citations/read",
    response_model=CitationReadResponse,
    operation_id="gateway_rag_citations_read",
    responses=_RESPONSES,
)
def read_rag_citations(
    payload: CitationReadRequest,
    credential: WorkloadCredential = Depends(get_workload_credential),
    session: Session = Depends(get_gateway_db),
    service: GatewayService = Depends(get_gateway_service),
    request_id: str = Depends(get_request_id),
) -> CitationReadResponse:
    return service.read_citations(session, credential, payload, request_id)


__all__ = ["get_gateway_db", "router"]
