"""Composable live authorization seam for P34.5 sandbox operations.

The adapter in this module deliberately owns no database session, capability
token or provider credential.  Production composition must inject trusted
lease and capability verifiers that re-read their server-owned state on every
operation.  Possession of a request or runtime handle is never authorization.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from omnibase.sandbox.contracts import (
    SandboxAction,
    SandboxAuthorizer,
    SandboxOperationRequest,
    SandboxRejected,
    SandboxUnavailable,
    VerifiedSandboxAuthorization,
    utc_now,
)
from omnibase.workspaces.service import LeaseRejected, verify_run_lease_for_sandbox

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _require_aware(value: datetime, *, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class VerifiedSandboxLease:
    """Live P34.4 Run/Node/attestation facts returned by trusted code."""

    tenant_id: UUID
    workspace_id: UUID
    run_id: UUID
    runtime_instance_id: UUID
    node_id: UUID
    lease_id: UUID
    workspace_generation: int
    run_fencing_token: int
    node_fencing_token: int
    workload_identity_thumbprint: str
    verified_at: datetime
    expires_at: datetime
    verification_digest: str

    def __post_init__(self) -> None:
        identifiers = (
            self.tenant_id,
            self.workspace_id,
            self.run_id,
            self.runtime_instance_id,
            self.node_id,
            self.lease_id,
        )
        if any(not isinstance(value, UUID) for value in identifiers):
            raise TypeError("verified lease identifiers must be UUID values")
        for name, value in (
            ("workspace_generation", self.workspace_generation),
            ("run_fencing_token", self.run_fencing_token),
            ("node_fencing_token", self.node_fencing_token),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if _SHA256_RE.fullmatch(self.workload_identity_thumbprint) is None:
            raise ValueError("workload identity thumbprint must be sha256")
        _require_aware(self.verified_at, name="verified_at")
        _require_aware(self.expires_at, name="expires_at")
        if self.expires_at <= self.verified_at:
            raise ValueError("verified lease is already expired")
        if _SHA256_RE.fullmatch(self.verification_digest) is None:
            raise ValueError("lease verification digest must be sha256")


@dataclass(frozen=True, slots=True)
class VerifiedSandboxCapability:
    """Live P34.2 capability result with no bearer token material."""

    tenant_id: UUID
    workspace_id: UUID
    run_id: UUID
    runtime_instance_id: UUID
    workload_identity_thumbprint: str
    action: SandboxAction
    verified_at: datetime
    expires_at: datetime
    verification_digest: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, UUID)
            for value in (
                self.tenant_id,
                self.workspace_id,
                self.run_id,
                self.runtime_instance_id,
            )
        ):
            raise TypeError("verified capability identifiers must be UUID values")
        if _SHA256_RE.fullmatch(self.workload_identity_thumbprint) is None:
            raise ValueError("workload identity thumbprint must be sha256")
        if not isinstance(self.action, SandboxAction):
            raise TypeError("action must be SandboxAction")
        _require_aware(self.verified_at, name="verified_at")
        _require_aware(self.expires_at, name="expires_at")
        if self.expires_at <= self.verified_at:
            raise ValueError("verified capability is already expired")
        if _SHA256_RE.fullmatch(self.verification_digest) is None:
            raise ValueError("capability verification digest must be sha256")


class SandboxLeaseVerifier(Protocol):
    """Trusted adapter for live P34.4 lease, Node and fencing verification."""

    def verify(self, request: SandboxOperationRequest) -> VerifiedSandboxLease: ...


class SandboxCapabilityVerifier(Protocol):
    """Trusted adapter for live P34.2 capability verification."""

    def verify(self, request: SandboxOperationRequest) -> VerifiedSandboxCapability: ...


class RejectingSandboxLeaseVerifier:
    def verify(self, request: SandboxOperationRequest) -> VerifiedSandboxLease:
        del request
        raise SandboxUnavailable("sandbox_lease_verifier_unavailable")


class RejectingSandboxCapabilityVerifier:
    def verify(self, request: SandboxOperationRequest) -> VerifiedSandboxCapability:
        del request
        raise SandboxUnavailable("sandbox_capability_verifier_unavailable")


class SqlAlchemySandboxLeaseVerifier:
    """Production adapter for current P34.4 Run/Node/fencing database facts."""

    def __init__(self, *, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def verify(self, request: SandboxOperationRequest) -> VerifiedSandboxLease:
        try:
            with self._session_factory() as session, session.begin():
                facts = verify_run_lease_for_sandbox(
                    session,
                    tenant_id=str(request.tenant_id),
                    run_id=str(request.run_id),
                    runtime_instance_id=str(request.runtime_instance_id),
                    lease_id=str(request.lease_id),
                    node_id=str(request.node_id),
                    generation=request.workspace_generation,
                    fencing_token=request.run_fencing_token,
                    workload_identity_digest=request.workload_identity_thumbprint,
                )
        except LeaseRejected as exc:
            raise SandboxRejected("sandbox_live_lease_rejected") from exc
        return VerifiedSandboxLease(
            tenant_id=UUID(facts.tenant_id),
            workspace_id=UUID(facts.workspace_id),
            run_id=UUID(facts.run_id),
            runtime_instance_id=UUID(facts.runtime_instance_id),
            node_id=UUID(facts.node_id),
            lease_id=UUID(facts.lease_id),
            workspace_generation=facts.workspace_generation,
            run_fencing_token=facts.run_fencing_token,
            node_fencing_token=facts.node_fencing_token,
            workload_identity_thumbprint=facts.workload_identity_digest,
            verified_at=facts.verified_at,
            expires_at=facts.expires_at,
            verification_digest=facts.verification_digest,
        )


class ComposedSandboxAuthorizer(SandboxAuthorizer):
    """Require matching live P34.4 and P34.2 results on every operation."""

    def __init__(
        self,
        *,
        lease_verifier: SandboxLeaseVerifier | None = None,
        capability_verifier: SandboxCapabilityVerifier | None = None,
        clock=utc_now,
    ) -> None:
        self._lease_verifier = lease_verifier or RejectingSandboxLeaseVerifier()
        self._capability_verifier = capability_verifier or RejectingSandboxCapabilityVerifier()
        self._clock = clock

    def authorize(self, request: SandboxOperationRequest) -> VerifiedSandboxAuthorization:
        lease = self._lease_verifier.verify(request)
        capability = self._capability_verifier.verify(request)
        now = self._clock()
        _require_aware(now, name="clock")
        lease_binding = (
            lease.tenant_id,
            lease.workspace_id,
            lease.run_id,
            lease.runtime_instance_id,
            lease.node_id,
            lease.lease_id,
            lease.workspace_generation,
            lease.run_fencing_token,
            lease.node_fencing_token,
            lease.workload_identity_thumbprint,
        )
        request_binding = (
            request.tenant_id,
            request.workspace_id,
            request.run_id,
            request.runtime_instance_id,
            request.node_id,
            request.lease_id,
            request.workspace_generation,
            request.run_fencing_token,
            request.node_fencing_token,
            request.workload_identity_thumbprint,
        )
        capability_binding = (
            capability.tenant_id,
            capability.workspace_id,
            capability.run_id,
            capability.runtime_instance_id,
            capability.workload_identity_thumbprint,
            capability.action,
        )
        expected_capability = (
            request.tenant_id,
            request.workspace_id,
            request.run_id,
            request.runtime_instance_id,
            request.workload_identity_thumbprint,
            request.action,
        )
        if lease_binding != request_binding or capability_binding != expected_capability:
            raise SandboxRejected("sandbox_live_authorization_binding_rejected")
        expires_at = min(lease.expires_at, capability.expires_at)
        if lease.verified_at > now or capability.verified_at > now or expires_at <= now:
            raise SandboxRejected("sandbox_live_authorization_expired")
        verification_digest = _digest(
            {
                "action": request.action.value,
                "capability": capability.verification_digest,
                "lease": lease.verification_digest,
                "operation_id": str(request.operation_id),
            }
        )
        return VerifiedSandboxAuthorization(
            request=request,
            verified_at=max(lease.verified_at, capability.verified_at),
            expires_at=expires_at,
            verification_digest=verification_digest,
        )


__all__ = [
    "ComposedSandboxAuthorizer",
    "RejectingSandboxCapabilityVerifier",
    "RejectingSandboxLeaseVerifier",
    "SandboxCapabilityVerifier",
    "SandboxLeaseVerifier",
    "SqlAlchemySandboxLeaseVerifier",
    "VerifiedSandboxCapability",
    "VerifiedSandboxLease",
]
