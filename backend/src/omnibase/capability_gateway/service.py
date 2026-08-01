"""Application service enforcing auth, policy, budgets, adapters, and audit."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from omnibase.capability_gateway.adapters import (
    AdapterError,
    DataReadAdapter,
    RagReadAdapter,
    ResultBudgetExceeded,
)
from omnibase.capability_gateway.audit import GatewayAuditRecord, GatewayAuditSink
from omnibase.capability_gateway.contracts import (
    CitationReadRequest,
    CitationReadResponse,
    DataRowsRequest,
    DataRowsResponse,
    DataSchemaResponse,
    GatewayAction,
    RagSearchRequest,
    RagSearchResponse,
    ResourceRequest,
    VerifiedCapability,
    WorkloadCredential,
)
from omnibase.capability_gateway.policy import PolicyDenial, authorize_resource
from omnibase.capability_gateway.query import QueryContractError
from omnibase.capability_gateway.resolver import ResourceResolutionError, ResourceResolver
from omnibase.capability_gateway.security import (
    CapabilityBudgetError,
    CapabilityScopeError,
    CapabilityVerificationError,
    CapabilityVerifier,
)
from omnibase.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class GatewayFailure(Exception):
    status_code: int
    code: str
    message: str


@dataclass(frozen=True)
class GatewayComponents:
    verifier: CapabilityVerifier
    resolver: ResourceResolver
    data_adapter: DataReadAdapter
    rag_adapter: RagReadAdapter
    audit_sink: GatewayAuditSink


class GatewayService:
    def __init__(self, components: GatewayComponents) -> None:
        self._components = components

    def _authenticate(
        self,
        session: Session,
        credential: WorkloadCredential,
        *,
        action: GatewayAction,
        resource_id: str,
        request_id: str,
    ) -> VerifiedCapability:
        try:
            return self._components.verifier.verify(
                session,
                credential,
                action=action,
                resource_id=resource_id,
            )
        except CapabilityScopeError as exc:
            log.warning(
                "gateway.security_denied",
                request_id=request_id,
                action=action,
                reason_code="capability_scope_denied",
            )
            raise GatewayFailure(403, "capability_scope_denied", "Capability scope denied") from exc
        except CapabilityVerificationError as exc:
            log.warning(
                "gateway.security_denied",
                request_id=request_id,
                action=action,
                reason_code="invalid_capability",
            )
            raise GatewayFailure(
                401, "invalid_capability", "Capability authentication failed"
            ) from exc

    def _execute(
        self,
        session: Session,
        *,
        credential: WorkloadCredential,
        action: GatewayAction,
        payload: BaseModel,
        request_id: str,
        max_bytes: int,
        constraint_check: Callable[[VerifiedCapability], None],
        operation: Callable[[VerifiedCapability, Any], Any],
        row_count: Callable[[Any], int],
        bytes_out: Callable[[Any], int],
    ) -> Any:
        resource_id = str(payload.resource_id)  # type: ignore[attr-defined]
        serialized = payload.model_dump_json().encode("utf-8")
        input_hash = hashlib.sha256(serialized).hexdigest()
        started = time.monotonic()
        capability = self._authenticate(
            session,
            credential,
            action=action,
            resource_id=resource_id,
            request_id=request_id,
        )
        try:
            constraint_check(capability)
            effective_max_bytes = min(max_bytes, capability.constraints.max_bytes)
            try:
                self._components.verifier.consume_budget(
                    session,
                    capability,
                    calls=1,
                    bytes_in=len(serialized),
                    bytes_out_reserved=effective_max_bytes,
                )
                # Budget reservation is intentionally durable before adapter work.
                # Later rollback/error paths cannot turn retries into zero-cost DoS.
                session.commit()
            except CapabilityBudgetError as exc:
                session.rollback()
                raise GatewayFailure(
                    429,
                    "capability_budget_exceeded",
                    "Capability budget exceeded",
                ) from exc
            resource = self._components.resolver.resolve(
                session,
                capability=capability,
                resource_id=resource_id,
            )
            authorize_resource(capability, resource, action)
            result = operation(capability, resource)
            actual_bytes_out = bytes_out(result)
            if actual_bytes_out > effective_max_bytes:
                raise ResultBudgetExceeded
            elapsed = int((time.monotonic() - started) * 1000)
            self._components.audit_sink.append(
                session,
                capability=capability,
                record=GatewayAuditRecord(
                    request_id=request_id,
                    action=action,
                    decision="allowed",
                    status_code=200,
                    input_hash=input_hash,
                    resource_id=resource_id,
                    reason_code="allowed",
                    duration_ms=elapsed,
                    bytes_in=len(serialized),
                    bytes_out=actual_bytes_out,
                    row_count=row_count(result),
                ),
            )
            session.commit()
            return result
        except (ResourceResolutionError, QueryContractError) as exc:
            return self._deny(
                session,
                capability,
                request_id,
                action,
                input_hash,
                resource_id,
                len(serialized),
                started,
                404,
                "resource_not_found",
                "Resource not found",
                exc,
            )
        except PolicyDenial as exc:
            status_code = 404 if exc.code == "resource_not_found" else 403
            message = "Resource not found" if status_code == 404 else "Capability scope denied"
            return self._deny(
                session,
                capability,
                request_id,
                action,
                input_hash,
                resource_id,
                len(serialized),
                started,
                status_code,
                exc.code,
                message,
                exc,
            )
        except ResultBudgetExceeded as exc:
            return self._deny(
                session,
                capability,
                request_id,
                action,
                input_hash,
                resource_id,
                len(serialized),
                started,
                413,
                "result_too_large",
                "Result exceeds response budget",
                exc,
            )
        except AdapterError as exc:
            return self._deny(
                session,
                capability,
                request_id,
                action,
                input_hash,
                resource_id,
                len(serialized),
                started,
                503,
                "adapter_unavailable",
                "Read adapter unavailable",
                exc,
            )

    def _deny(
        self,
        session: Session,
        capability: VerifiedCapability,
        request_id: str,
        action: str,
        input_hash: str,
        resource_id: str,
        bytes_in: int,
        started: float,
        status_code: int,
        reason_code: str,
        message: str,
        cause: Exception,
    ) -> Any:
        session.rollback()
        self._components.audit_sink.append(
            session,
            capability=capability,
            record=GatewayAuditRecord(
                request_id=request_id,
                action=action,
                decision="denied" if status_code < 500 else "error",
                status_code=status_code,
                input_hash=input_hash,
                resource_id=resource_id,
                reason_code=reason_code,
                duration_ms=int((time.monotonic() - started) * 1000),
                bytes_in=bytes_in,
            ),
        )
        session.commit()
        raise GatewayFailure(status_code, reason_code, message) from cause

    @staticmethod
    def _check_limits(
        capability: VerifiedCapability,
        *,
        rows: int = 0,
        max_bytes: int,
        timeout_ms: int = 0,
        top_k: int = 0,
    ) -> None:
        constraints = capability.constraints
        if (
            rows > constraints.max_rows
            or max_bytes > constraints.max_bytes
            or timeout_ms > constraints.max_timeout_ms
            or top_k > constraints.max_top_k
        ):
            raise PolicyDenial("capability_constraint_denied")

    def read_schema(
        self,
        session: Session,
        credential: WorkloadCredential,
        payload: ResourceRequest,
        request_id: str,
    ) -> DataSchemaResponse:
        return self._execute(
            session,
            credential=credential,
            action="data.schema.read",
            payload=payload,
            request_id=request_id,
            max_bytes=65_536,
            constraint_check=lambda cap: self._check_limits(cap, max_bytes=0),
            operation=lambda cap, res: DataSchemaResponse(
                resource_id=payload.resource_id,
                resource_version=res.version,
                columns=self._components.data_adapter.read_schema(
                    session, capability=cap, resource=res
                ),
            ),
            row_count=lambda result: len(result.columns),
            bytes_out=lambda result: len(result.model_dump_json().encode("utf-8")),
        )

    def read_rows(
        self,
        session: Session,
        credential: WorkloadCredential,
        payload: DataRowsRequest,
        request_id: str,
    ) -> DataRowsResponse:
        query = payload.query
        return self._execute(
            session,
            credential=credential,
            action="data.rows.read",
            payload=payload,
            request_id=request_id,
            max_bytes=query.max_bytes,
            constraint_check=lambda cap: self._check_limits(
                cap, rows=query.limit, max_bytes=query.max_bytes, timeout_ms=query.timeout_ms
            ),
            operation=lambda cap, res: self._rows_response(session, cap, res, payload),
            row_count=lambda result: result.row_count,
            bytes_out=lambda result: result.bytes_out,
        )

    def _rows_response(self, session, capability, resource, payload) -> DataRowsResponse:
        result = self._components.data_adapter.read_rows(
            session, capability=capability, resource=resource, query=payload.query
        )
        return DataRowsResponse(
            resource_id=payload.resource_id,
            resource_version=resource.version,
            rows=result.rows,
            next_cursor=result.next_cursor,
            row_count=len(result.rows),
            bytes_out=result.bytes_out,
            truncated=result.truncated,
        )

    def rag_search(
        self,
        session: Session,
        credential: WorkloadCredential,
        payload: RagSearchRequest,
        request_id: str,
    ) -> RagSearchResponse:
        return self._execute(
            session,
            credential=credential,
            action="rag.search",
            payload=payload,
            request_id=request_id,
            max_bytes=payload.max_bytes,
            constraint_check=lambda cap: self._check_limits(
                cap, max_bytes=payload.max_bytes, timeout_ms=payload.timeout_ms, top_k=payload.top_k
            ),
            operation=lambda cap, res: self._search_response(session, cap, res, payload),
            row_count=lambda result: len(result.results),
            bytes_out=lambda result: result.bytes_out,
        )

    def _search_response(self, session, capability, resource, payload) -> RagSearchResponse:
        result = self._components.rag_adapter.search(
            session,
            capability=capability,
            resource=resource,
            query=payload.query,
            top_k=payload.top_k,
            timeout_ms=payload.timeout_ms,
            max_bytes=payload.max_bytes,
        )
        return RagSearchResponse(
            resource_id=payload.resource_id,
            results=result.hits,
            total_found=len(result.hits),
            bytes_out=result.bytes_out,
            truncated=result.truncated,
        )

    def read_citations(
        self,
        session: Session,
        credential: WorkloadCredential,
        payload: CitationReadRequest,
        request_id: str,
    ) -> CitationReadResponse:
        return self._execute(
            session,
            credential=credential,
            action="rag.citation.read",
            payload=payload,
            request_id=request_id,
            max_bytes=payload.max_bytes,
            constraint_check=lambda cap: self._check_limits(
                cap, max_bytes=payload.max_bytes, timeout_ms=payload.timeout_ms
            ),
            operation=lambda cap, res: self._citations_response(session, cap, res, payload),
            row_count=lambda result: len(result.citations),
            bytes_out=lambda result: result.bytes_out,
        )

    def _citations_response(self, session, capability, resource, payload) -> CitationReadResponse:
        result = self._components.rag_adapter.read_citations(
            session,
            capability=capability,
            resource=resource,
            citation_ids=[str(item) for item in payload.citation_ids],
            timeout_ms=payload.timeout_ms,
            max_bytes=payload.max_bytes,
        )
        return CitationReadResponse(
            resource_id=payload.resource_id,
            citations=result.citations,
            bytes_out=result.bytes_out,
            truncated=result.truncated,
        )


__all__ = ["GatewayComponents", "GatewayFailure", "GatewayService"]
