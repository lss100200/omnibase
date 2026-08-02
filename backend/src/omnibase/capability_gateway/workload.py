"""P34.5 workload identity and short-lived Gateway credential boundaries.

The Gateway is the only data-plane surface exposed to a Sandbox.  This module
keeps certificate evidence, live P34.4 Run lease facts, and P34.2 read tokens
bound without giving the Runner database or signing-key access.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy.orm import Session
from starlette.types import Scope

from omnibase.capabilities.service import TrustedIssuerContext
from omnibase.capability_gateway.contracts import TrustedWorkloadContext
from omnibase.capability_gateway.security import CapabilityVerificationError, WorkloadAttestor
from omnibase.capability_gateway.thumbprints import certificate_thumbprint_to_x5t_s256

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_IDENTITY_RE = re.compile(r"^spiffe://omnibase/runtime/[0-9a-f-]{36}$")
_MAX_CREDENTIAL_TTL = timedelta(minutes=5)


class GatewayCredentialUnavailable(RuntimeError):
    """Stable fail-closed result for a credential vending failure."""


def _uuid(value: str, name: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{name} must be a UUID") from exc


def _digest(value: str, name: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _positive_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _aware_utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class TrustedGatewayPeerEvidence:
    """mTLS evidence injected by a trusted Runner/Broker transport.

    HTTP headers must never be converted into this object.  The ASGI ingress
    owns its construction after authenticating the client certificate.
    """

    peer_kind: Literal["runner", "network_broker"]
    opaque_identity: str
    tenant_id: str
    workspace_id: str
    run_id: str
    runtime_instance_id: str
    node_id: str
    lease_id: str
    workspace_generation: int
    run_fencing_token: int
    node_fencing_token: int
    certificate_thumbprint: str
    evidence_digest: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.peer_kind not in {"runner", "network_broker"}:
            raise ValueError("peer_kind must be runner or network_broker")
        if (
            not isinstance(self.opaque_identity, str)
            or _OPAQUE_IDENTITY_RE.fullmatch(self.opaque_identity) is None
            or self.opaque_identity.rsplit("/", 1)[-1] != self.runtime_instance_id
        ):
            raise ValueError("opaque_identity must bind the runtime instance")
        for name in (
            "tenant_id",
            "workspace_id",
            "run_id",
            "runtime_instance_id",
            "node_id",
            "lease_id",
        ):
            object.__setattr__(self, name, _uuid(getattr(self, name), name))
        for name in (
            "workspace_generation",
            "run_fencing_token",
            "node_fencing_token",
        ):
            _positive_int(getattr(self, name), name)
        _digest(self.certificate_thumbprint, "certificate_thumbprint")
        _digest(self.evidence_digest, "evidence_digest")
        object.__setattr__(self, "expires_at", _aware_utc(self.expires_at, "expires_at"))


class SqlAlchemyRunLeaseWorkloadAttestor(WorkloadAttestor):
    """Revalidate an mTLS peer against current P34.4 lease/fencing facts."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def attest(self, scope: Scope, opaque_identity: str) -> TrustedWorkloadContext:
        evidence = scope.get("omnibase.trusted_gateway_peer")  # type: ignore[typeddict-item]
        mtls_verified = scope.get("omnibase.mtls_verified")  # type: ignore[typeddict-item]
        if mtls_verified is not True or not isinstance(evidence, TrustedGatewayPeerEvidence):
            raise CapabilityVerificationError
        now = _aware_utc(self._clock(), "clock")
        if evidence.opaque_identity != opaque_identity or evidence.expires_at <= now:
            raise CapabilityVerificationError

        from omnibase.workspaces.service import LeaseRejected, verify_run_lease_for_sandbox

        session = self._session_factory()
        try:
            facts = verify_run_lease_for_sandbox(
                session,
                tenant_id=evidence.tenant_id,
                run_id=evidence.run_id,
                runtime_instance_id=evidence.runtime_instance_id,
                lease_id=evidence.lease_id,
                node_id=evidence.node_id,
                generation=evidence.workspace_generation,
                fencing_token=evidence.run_fencing_token,
                workload_identity_digest=evidence.certificate_thumbprint,
            )
            expected = (
                evidence.tenant_id,
                evidence.workspace_id,
                evidence.run_id,
                evidence.runtime_instance_id,
                evidence.node_id,
                evidence.lease_id,
                evidence.workspace_generation,
                evidence.run_fencing_token,
                evidence.node_fencing_token,
                evidence.certificate_thumbprint,
            )
            actual = (
                facts.tenant_id,
                facts.workspace_id,
                facts.run_id,
                facts.runtime_instance_id,
                facts.node_id,
                facts.lease_id,
                facts.workspace_generation,
                facts.run_fencing_token,
                facts.node_fencing_token,
                facts.workload_identity_digest,
            )
            if actual != expected or _aware_utc(facts.expires_at, "lease expiry") <= now:
                raise CapabilityVerificationError
        except (LeaseRejected, ValueError, TypeError, CapabilityVerificationError) as exc:
            session.rollback()
            raise CapabilityVerificationError from exc
        except Exception as exc:
            session.rollback()
            raise CapabilityVerificationError from exc
        finally:
            session.close()

        return TrustedWorkloadContext(
            opaque_identity=evidence.opaque_identity,
            tenant_id=evidence.tenant_id,
            workspace_id=evidence.workspace_id,
            runtime_instance_id=evidence.runtime_instance_id,
            certificate_thumbprint=evidence.certificate_thumbprint,
        )


@dataclass(frozen=True, slots=True)
class GatewayCredentialIssueRequest:
    tenant_id: str
    workspace_id: str
    run_id: str
    runtime_instance_id: str
    node_id: str
    lease_id: str
    grant_id: str
    expected_profile: Literal["read", "workspace_data"]
    key_id: str
    opaque_identity: str
    workspace_generation: int
    run_fencing_token: int
    node_fencing_token: int
    certificate_thumbprint: str

    def __post_init__(self) -> None:
        for name in (
            "tenant_id",
            "workspace_id",
            "run_id",
            "runtime_instance_id",
            "node_id",
            "lease_id",
            "grant_id",
        ):
            object.__setattr__(self, name, _uuid(getattr(self, name), name))
        if self.expected_profile not in {"read", "workspace_data"}:
            raise ValueError("expected_profile is invalid")
        if (
            not isinstance(self.key_id, str)
            or not 1 <= len(self.key_id) <= 64
            or re.fullmatch(r"[A-Za-z0-9._-]+", self.key_id) is None
        ):
            raise ValueError("key_id is invalid")
        if (
            not isinstance(self.opaque_identity, str)
            or _OPAQUE_IDENTITY_RE.fullmatch(self.opaque_identity) is None
            or self.opaque_identity.rsplit("/", 1)[-1] != self.runtime_instance_id
        ):
            raise ValueError("opaque_identity must bind the runtime instance")
        for name in (
            "workspace_generation",
            "run_fencing_token",
            "node_fencing_token",
        ):
            _positive_int(getattr(self, name), name)
        _digest(self.certificate_thumbprint, "certificate_thumbprint")


@dataclass(frozen=True, slots=True)
class EphemeralGatewayCredential:
    """Short-lived material delivered only over the authenticated Runner channel."""

    token: str = field(repr=False)
    opaque_identity: str
    certificate_thumbprint: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.token, str)
            or not self.token
            or any(ch.isspace() for ch in self.token)
        ):
            raise ValueError("token is malformed")
        if not isinstance(self.opaque_identity, str) or not self.opaque_identity:
            raise ValueError("opaque_identity is required")
        _digest(self.certificate_thumbprint, "certificate_thumbprint")
        object.__setattr__(self, "expires_at", _aware_utc(self.expires_at, "expires_at"))


class CapabilityPrivateKeyProvider(Protocol):
    """Core-only secure key loader.  Runner and Sandbox never implement it."""

    def load_private_key(self, key_id: str) -> str | bytes: ...


class RejectingCapabilityPrivateKeyProvider:
    def load_private_key(self, key_id: str) -> str | bytes:
        del key_id
        raise GatewayCredentialUnavailable("gateway_signing_key_unavailable")


class GatewayCredentialIssuer(Protocol):
    def issue(
        self,
        request: GatewayCredentialIssueRequest,
        *,
        issuer_context: TrustedIssuerContext,
        ttl: timedelta = _MAX_CREDENTIAL_TTL,
    ) -> EphemeralGatewayCredential: ...


class RejectingGatewayCredentialIssuer:
    def issue(
        self,
        request: GatewayCredentialIssueRequest,
        *,
        issuer_context: TrustedIssuerContext,
        ttl: timedelta = _MAX_CREDENTIAL_TTL,
    ) -> EphemeralGatewayCredential:
        del request, issuer_context, ttl
        raise GatewayCredentialUnavailable("gateway_credential_issuer_unavailable")


class SqlAlchemyGatewayCredentialIssuer:
    """Issue one explicitly profiled token after a fresh Run/Node lease check."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        private_key_provider: CapabilityPrivateKeyProvider,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._private_key_provider = private_key_provider
        self._clock = clock or (lambda: datetime.now(UTC))

    def issue(
        self,
        request: GatewayCredentialIssueRequest,
        *,
        issuer_context: TrustedIssuerContext,
        ttl: timedelta = _MAX_CREDENTIAL_TTL,
    ) -> EphemeralGatewayCredential:
        if not isinstance(request, GatewayCredentialIssueRequest):
            raise TypeError("request must be GatewayCredentialIssueRequest")
        if not isinstance(issuer_context, TrustedIssuerContext):
            raise TypeError("issuer_context must be TrustedIssuerContext")
        if issuer_context.tenant_id != request.tenant_id:
            raise GatewayCredentialUnavailable("gateway_credential_rejected")
        if ttl <= timedelta(0) or ttl > _MAX_CREDENTIAL_TTL:
            raise ValueError("credential ttl must be positive and at most five minutes")
        now = _aware_utc(self._clock(), "clock")

        from omnibase.capabilities.service import (
            READ_ACTIONS,
            WORKSPACE_DATA_ACTIONS,
            get_grant,
            issue_token,
        )
        from omnibase.workspaces.service import LeaseRejected, verify_run_lease_for_sandbox

        session = self._session_factory()
        try:
            facts = verify_run_lease_for_sandbox(
                session,
                tenant_id=request.tenant_id,
                run_id=request.run_id,
                runtime_instance_id=request.runtime_instance_id,
                lease_id=request.lease_id,
                node_id=request.node_id,
                generation=request.workspace_generation,
                fencing_token=request.run_fencing_token,
                workload_identity_digest=request.certificate_thumbprint,
            )
            expected = (
                request.tenant_id,
                request.workspace_id,
                request.run_id,
                request.runtime_instance_id,
                request.node_id,
                request.lease_id,
                request.workspace_generation,
                request.run_fencing_token,
                request.node_fencing_token,
                request.certificate_thumbprint,
            )
            actual = (
                facts.tenant_id,
                facts.workspace_id,
                facts.run_id,
                facts.runtime_instance_id,
                facts.node_id,
                facts.lease_id,
                facts.workspace_generation,
                facts.run_fencing_token,
                facts.node_fencing_token,
                facts.workload_identity_digest,
            )
            if actual != expected or _aware_utc(facts.expires_at, "lease expiry") <= now:
                raise GatewayCredentialUnavailable("gateway_live_lease_rejected")
            bounded_ttl = min(ttl, _aware_utc(facts.expires_at, "lease expiry") - now)
            if bounded_ttl <= timedelta(0):
                raise GatewayCredentialUnavailable("gateway_live_lease_rejected")
            grant = get_grant(
                session,
                tenant_id=request.tenant_id,
                grant_id=request.grant_id,
            )
            actions = frozenset(grant.actions)
            actual_profile = (
                "read"
                if actions and actions <= READ_ACTIONS
                else "workspace_data"
                if actions and actions <= WORKSPACE_DATA_ACTIONS
                else None
            )
            if actual_profile != request.expected_profile:
                raise GatewayCredentialUnavailable("gateway_credential_profile_rejected")
            # The Core-only key loader is deliberately reached only after the
            # complete live Run/Node/fencing binding has been revalidated.
            # Neither the request nor the returned credential contains key
            # material, and Runner/Sandbox processes never implement this seam.
            private_key = self._private_key_provider.load_private_key(request.key_id)
            token = issue_token(
                session,
                tenant_id=request.tenant_id,
                grant_id=request.grant_id,
                kid=request.key_id,
                private_key_pem=private_key,
                workload_thumbprint=certificate_thumbprint_to_x5t_s256(
                    request.certificate_thumbprint
                ),
                issuer_context=issuer_context,
                ttl=bounded_ttl,
            )
            session.commit()
        except (LeaseRejected, GatewayCredentialUnavailable) as exc:
            session.rollback()
            raise GatewayCredentialUnavailable("gateway_credential_rejected") from exc
        except Exception as exc:
            session.rollback()
            raise GatewayCredentialUnavailable("gateway_credential_rejected") from exc
        finally:
            session.close()

        return EphemeralGatewayCredential(
            token=token,
            opaque_identity=request.opaque_identity,
            certificate_thumbprint=request.certificate_thumbprint,
            expires_at=now + bounded_ttl,
        )


__all__ = [
    "CapabilityPrivateKeyProvider",
    "EphemeralGatewayCredential",
    "GatewayCredentialIssueRequest",
    "GatewayCredentialIssuer",
    "GatewayCredentialUnavailable",
    "RejectingCapabilityPrivateKeyProvider",
    "RejectingGatewayCredentialIssuer",
    "SqlAlchemyGatewayCredentialIssuer",
    "SqlAlchemyRunLeaseWorkloadAttestor",
    "TrustedGatewayPeerEvidence",
    "certificate_thumbprint_to_x5t_s256",
]
