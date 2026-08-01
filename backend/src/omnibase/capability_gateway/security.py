"""Workload/capability authentication boundary for the gateway."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from fastapi import Header, HTTPException, Request, status
from sqlalchemy.orm import Session
from starlette.types import Scope

from omnibase.capability_gateway.contracts import (
    CapabilityConstraints,
    TrustedWorkloadContext,
    VerifiedCapability,
    WorkloadCredential,
)
from omnibase.capability_gateway.thumbprints import certificate_thumbprint_to_x5t_s256
from omnibase.core.logging import get_logger

log = get_logger(__name__)


class CapabilityVerificationError(Exception):
    """Safe verification failure with no token or claim echo."""


class CapabilityScopeError(CapabilityVerificationError):
    """The token is valid but lacks the exact logical scope."""


class CapabilityBudgetError(CapabilityVerificationError):
    """The online ledger cannot reserve the request budget."""


@runtime_checkable
class WorkloadAttestor(Protocol):
    def attest(self, scope: Scope, opaque_identity: str) -> TrustedWorkloadContext: ...


class RejectingWorkloadAttestor:
    """Default is intentionally unusable until trusted runtime attestation exists."""

    def attest(self, scope: Scope, opaque_identity: str) -> TrustedWorkloadContext:
        del scope, opaque_identity
        raise CapabilityVerificationError


class TrustedScopeWorkloadAttestor:
    """Read identity injected into ASGI scope by a trusted mTLS/runner layer.

    Client headers can never populate this object. Deployments must ensure that
    only their server integration can set ``scope['omnibase.trusted_workload']``.
    """

    def attest(self, scope: Scope, opaque_identity: str) -> TrustedWorkloadContext:
        context = scope.get("omnibase.trusted_workload")  # type: ignore[typeddict-item]
        if not isinstance(context, TrustedWorkloadContext):
            raise CapabilityVerificationError
        if context.opaque_identity != opaque_identity:
            raise CapabilityVerificationError
        return context


@runtime_checkable
class CapabilityVerifier(Protocol):
    def verify(
        self,
        session: Session,
        credential: WorkloadCredential,
        *,
        action: str,
        resource_id: str,
    ) -> VerifiedCapability: ...

    def consume_budget(
        self,
        session: Session,
        capability: VerifiedCapability,
        *,
        calls: int,
        bytes_in: int,
        bytes_out_reserved: int,
    ) -> None: ...


class RejectingCapabilityVerifier:
    """Fail closed until a real issuer/ledger verifier is injected."""

    def verify(
        self,
        session: Session,
        credential: WorkloadCredential,
        *,
        action: str,
        resource_id: str,
    ) -> VerifiedCapability:
        del session, credential, action, resource_id
        raise CapabilityVerificationError("capability verifier is not configured")

    def consume_budget(
        self,
        session: Session,
        capability: VerifiedCapability,
        *,
        calls: int,
        bytes_in: int,
        bytes_out_reserved: int,
    ) -> None:
        del session, capability, calls, bytes_in, bytes_out_reserved
        raise CapabilityVerificationError("capability verifier is not configured")


class CoreCapabilityVerifier:
    """Thin adapter over ``omnibase.capabilities`` with no duplicate JWT logic."""

    def verify(
        self,
        session: Session,
        credential: WorkloadCredential,
        *,
        action: str,
        resource_id: str,
    ) -> VerifiedCapability:
        from omnibase.capabilities.service import (
            CapabilityScopeDenied,
            InvalidCapability,
            verify_capability,
        )

        try:
            identity = credential.trusted_context
            core = verify_capability(
                session,
                token=credential.authorization,
                expected_tenant_id=identity.tenant_id,
                expected_workspace_id=identity.workspace_id,
                expected_runtime_instance_id=identity.runtime_instance_id,
                expected_workload_thumbprint=certificate_thumbprint_to_x5t_s256(
                    identity.certificate_thumbprint
                ),
                action=action,
                resource_id=resource_id,
            )
        except CapabilityScopeDenied as exc:
            raise CapabilityScopeError from exc
        except (InvalidCapability, ValueError) as exc:
            raise CapabilityVerificationError from exc
        constraints = _normalize_core_constraints(core.constraints)
        safe = VerifiedCapability(
            tenant_id=str(core.tenant_id),
            workspace_id=str(core.workspace_id),
            runtime_instance_id=str(core.runtime_instance_id),
            actor_user_id=str(core.actor_user_id),
            grant_id=str(core.grant_id),
            token_jti=core.claims.jti,
            actions=frozenset({core.action}),
            resource_ids=frozenset({core.resource_id}),
            constraints=CapabilityConstraints(
                max_rows=min(constraints.get("max_rows", 100), 100),
                max_bytes=min(constraints.get("max_result_bytes", 1_048_576), 1_048_576),
                max_timeout_ms=min(constraints["timeout_ms"], 5000),
                max_top_k=min(constraints.get("rag_top_k", 20), 20),
            ),
            core_verification=core,
        )
        return safe

    def consume_budget(
        self,
        session: Session,
        capability: VerifiedCapability,
        *,
        calls: int,
        bytes_in: int,
        bytes_out_reserved: int,
    ) -> None:
        from omnibase.capabilities.service import (
            CapabilityBudgetExceeded,
            consume_budget,
        )
        from omnibase.capabilities.service import (
            VerifiedCapability as CoreVerifiedCapability,
        )

        core = capability.core_verification
        if not isinstance(core, CoreVerifiedCapability):
            raise CapabilityVerificationError
        try:
            consume_budget(
                session,
                verified=core,
                calls=calls,
                bytes_in=bytes_in,
                bytes_out=bytes_out_reserved,
                cost_units=1,
            )
        except CapabilityBudgetExceeded as exc:
            raise CapabilityBudgetError from exc


def get_workload_credential(
    request: Request,
    authorization: str | None = Header(default=None),
    x_omnibase_workload_identity: str | None = Header(default=None),
) -> WorkloadCredential:
    """Extract workload material; only the injected verifier may trust it."""
    if authorization is None or not authorization.startswith("Capability "):
        # In particular, `Bearer <user JWT>` is rejected before any JWT decoder.
        log.warning(
            "gateway.security_auth_rejected",
            request_id=getattr(request.state, "request_id", "unavailable"),
            path=request.url.path,
            reason_code="capability_scheme_required",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "invalid_capability",
                    "message": "Capability authentication required",
                }
            },
        )
    token = authorization.removeprefix("Capability ").strip()
    if not token or not x_omnibase_workload_identity:
        log.warning(
            "gateway.security_auth_rejected",
            request_id=getattr(request.state, "request_id", "unavailable"),
            path=request.url.path,
            reason_code="capability_material_missing",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "invalid_capability",
                    "message": "Capability authentication required",
                }
            },
        )
    attestor: WorkloadAttestor = request.app.state.workload_attestor
    try:
        context = attestor.attest(request.scope, x_omnibase_workload_identity)
    except CapabilityVerificationError as exc:
        log.warning(
            "gateway.security_auth_rejected",
            request_id=getattr(request.state, "request_id", "unavailable"),
            path=request.url.path,
            reason_code="workload_attestation_failed",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "invalid_capability",
                    "message": "Capability authentication failed",
                }
            },
        ) from exc
    return WorkloadCredential(
        authorization=token,
        identity=x_omnibase_workload_identity,
        trusted_context=context,
    )


def _normalize_core_constraints(raw: dict[str, object]) -> dict[str, int]:
    allowed = {"max_rows", "max_result_bytes", "rag_top_k", "timeout_ms"}
    if set(raw) - allowed or "timeout_ms" not in raw:
        raise CapabilityVerificationError
    normalized: dict[str, int] = {}
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise CapabilityVerificationError
        normalized[key] = value
    return normalized


__all__ = [
    "CapabilityBudgetError",
    "CapabilityScopeError",
    "CapabilityVerificationError",
    "CapabilityVerifier",
    "CoreCapabilityVerifier",
    "RejectingCapabilityVerifier",
    "RejectingWorkloadAttestor",
    "TrustedScopeWorkloadAttestor",
    "WorkloadAttestor",
    "get_workload_credential",
]
