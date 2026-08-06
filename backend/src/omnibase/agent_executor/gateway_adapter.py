"""Capability-Gateway adapter for the P5.4A knowledge-search port.

The adapter is an explicitly injected engineering dependency.  It accepts
only a server-owned workload credential and a separately revalidated runtime
authority.  Browser JWTs, physical locators, provider credentials and retry
loops never enter this boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import Any, Protocol, cast
from uuid import UUID

from sqlalchemy.orm import Session

from omnibase.agent_executor.contracts import (
    ExecutorInvocationContext,
    KnowledgeSearchHit,
    KnowledgeSearchPort,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
)


class GatewayAdapterError(RuntimeError):
    """A capability-gateway invocation failed without a safe result."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class GatewayAdapterUnavailable(GatewayAdapterError):
    """The explicitly injected engineering dependency is unavailable."""


class GatewayAdapterDenied(GatewayAdapterError):
    """The runtime authority or workload credential is not admissible."""


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


class ServerWorkloadCredentialProvider(Protocol):
    """Issue a short-lived credential from server-owned runtime state."""

    def __call__(self, *, context: ExecutorInvocationContext) -> Any: ...


class RuntimeAuthorityValidator(Protocol):
    """Revalidate live runtime identity, lease and fencing before each call."""

    def validate(
        self,
        *,
        context: ExecutorInvocationContext,
        credential: Any,
    ) -> None: ...


class GatewayRagService(Protocol):
    """Structural seam implemented by the independent GatewayService."""

    def rag_search(
        self,
        session: Session,
        credential: Any,
        payload: Any,
        request_id: str,
    ) -> Any: ...


RequestIdFactory = Callable[[ExecutorInvocationContext, KnowledgeSearchRequest], str]


class CapabilityGatewayKnowledgeSearchPort(KnowledgeSearchPort):
    """Call the independent ``GatewayService.rag_search`` boundary.

    No automatic retry is performed.  A timeout, disconnect or unknown result
    is surfaced as an adapter error so a future durable reconciliation layer
    can decide what to do instead of replaying an effect implicitly.
    """

    def __init__(
        self,
        *,
        gateway: GatewayRagService,
        session_factory: SessionFactory,
        credential_provider: ServerWorkloadCredentialProvider,
        authority_validator: RuntimeAuthorityValidator,
        request_id_factory: RequestIdFactory | None = None,
    ) -> None:
        self._gateway = gateway
        self._session_factory = session_factory
        self._credential_provider = credential_provider
        self._authority_validator = authority_validator
        self._request_id_factory = request_id_factory or _default_request_id

    def search(
        self,
        *,
        context: ExecutorInvocationContext,
        request: KnowledgeSearchRequest,
    ) -> KnowledgeSearchResult:
        resource_uuid = _parse_logical_resource_id(request.resource_id)
        try:
            credential = self._credential_provider(context=context)
        except GatewayAdapterError:
            raise
        except Exception as exc:
            raise GatewayAdapterUnavailable("workload_credential_unavailable") from exc

        _validate_server_credential(context=context, credential=credential)
        try:
            self._authority_validator.validate(context=context, credential=credential)
        except GatewayAdapterError:
            raise
        except Exception as exc:
            raise GatewayAdapterDenied("runtime_authority_denied") from exc

        rag_request_type, _, _ = _gateway_contract_types()
        payload = rag_request_type(
            resource_id=resource_uuid,
            query=request.query,
            top_k=request.top_k,
            timeout_ms=request.timeout_ms,
            max_bytes=request.max_bytes,
        )
        request_id = self._request_id_factory(context, request)
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            raise GatewayAdapterError("invalid_request_id")

        session = self._session_factory()
        try:
            response = self._gateway.rag_search(session, credential, payload, request_id)
        except Exception as exc:
            gateway_code = _gateway_failure_code(exc)
            if gateway_code is not None:
                raise GatewayAdapterError(f"gateway_{gateway_code}") from exc
            # Do not expose gateway, database, locator or provider details and
            # never retry an unknown outcome here.
            raise GatewayAdapterError("gateway_invocation_failed") from exc
        finally:
            session.close()

        return _convert_response(resource_uuid=resource_uuid, response=response)


def _parse_logical_resource_id(value: str) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError) as exc:
        raise GatewayAdapterDenied("resource_id_not_logical_uuid") from exc


def _validate_server_credential(
    *,
    context: ExecutorInvocationContext,
    credential: Any,
) -> None:
    _, _, workload_credential_type = _gateway_contract_types()
    if not isinstance(credential, workload_credential_type):
        raise GatewayAdapterDenied("workload_credential_type_invalid")
    if not credential.authorization.startswith("Capability "):
        raise GatewayAdapterDenied("server_workload_credential_required")
    trusted = credential.trusted_context
    if trusted.tenant_id != context.tenant_id:
        raise GatewayAdapterDenied("workload_tenant_mismatch")
    if trusted.workspace_id != context.workspace_id:
        raise GatewayAdapterDenied("workload_workspace_mismatch")
    if not trusted.runtime_instance_id or not trusted.opaque_identity:
        raise GatewayAdapterDenied("workload_runtime_identity_missing")


def _convert_response(*, resource_uuid: UUID, response: Any) -> KnowledgeSearchResult:
    _, rag_response_type, _ = _gateway_contract_types()
    if not isinstance(response, rag_response_type):
        raise GatewayAdapterError("gateway_response_type_invalid")
    if response.resource_id != resource_uuid:
        raise GatewayAdapterError("gateway_resource_scope_mismatch")
    hits = tuple(
        KnowledgeSearchHit(
            citation_id=str(item.citation_id),
            document_id=str(item.document_id),
            score=float(item.score),
            snippet=item.snippet,
            page_number=item.page_number,
        )
        for item in response.results
    )
    return KnowledgeSearchResult(
        resource_id=str(response.resource_id),
        results=hits,
        bytes_out=response.bytes_out,
        truncated=response.truncated,
    )


def _gateway_failure_code(exc: Exception) -> str | None:
    """Extract only the stable public code from a GatewayFailure-like error."""

    if type(exc).__name__ != "GatewayFailure":
        return None
    code = getattr(exc, "code", None)
    return code if isinstance(code, str) and code else None


def _gateway_contract_types() -> tuple[type[Any], type[Any], type[Any]]:
    """Load Gateway DTOs lazily to keep the typed seam's checks lightweight."""

    module = import_module("omnibase.capability_gateway.contracts")
    return (
        cast(type[Any], module.RagSearchRequest),
        cast(type[Any], module.RagSearchResponse),
        cast(type[Any], module.WorkloadCredential),
    )


def _default_request_id(
    context: ExecutorInvocationContext,
    request: KnowledgeSearchRequest,
) -> str:
    del request
    return f"p54a-{context.run_id}-{context.task_id}-{context.node_id}"


__all__ = [
    "CapabilityGatewayKnowledgeSearchPort",
    "GatewayAdapterDenied",
    "GatewayAdapterError",
    "GatewayAdapterUnavailable",
    "RuntimeAuthorityValidator",
    "ServerWorkloadCredentialProvider",
    "SessionFactory",
]
