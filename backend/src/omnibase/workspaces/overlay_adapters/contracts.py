"""Provider-neutral contracts for trusted member-node Overlay reconciliation.

These contracts deliberately carry logical Workspace control-plane bindings
and opaque, short-lived credential references.  They never carry a provider
auth key, an IP/route, a provider handle, or a Sandbox runtime identity.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
_LOGICAL_NAME_RE = re.compile(r"^[a-z][a-z0-9.-]{0,126}[a-z0-9]$|^[a-z]$")
_SERVICE_PROTOCOLS = frozenset({"https", "git", "artifact", "event"})
_TRANSPORT_PROTOCOLS = frozenset({"tcp", "udp"})


class OverlayAdapterError(RuntimeError):
    """Base error for the P34.5 Overlay adapter seam."""


class OverlayUnavailable(OverlayAdapterError):
    """A required trusted verifier, daemon, credential broker, or transport is absent."""


class OverlayRejected(OverlayAdapterError):
    """The requested binding is stale, unsafe, or not authorized."""


class OverlayOutcomeUnknown(OverlayAdapterError):
    """The daemon boundary may have been crossed; automatic replay is forbidden."""


class OverlayAction(StrEnum):
    ACTIVATE = "activate"
    ROTATE = "rotate"
    REVOKE = "revoke"
    STATUS = "status"


class OverlayState(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class OverlaySubjectKind(StrEnum):
    TRUSTED_NODE_DAEMON = "trusted_node_daemon"
    SANDBOX = "sandbox"


class OverlayPublicationMode(StrEnum):
    BROKER_LOGICAL_SERVICE = "broker_logical_service"
    DIRECT_ENDPOINT = "direct_endpoint"


def _require_uuid(value: str, *, field: str) -> None:
    try:
        UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field} must be a UUID string") from exc


def _require_positive(value: int, *, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer")


def _require_non_negative(value: int, *, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")


def _require_sha256(value: str, *, field: str) -> None:
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def _require_aware(value: datetime, *, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class OverlayOperationIntent:
    """Logical request from the Workspace Network Broker, never Browser JSON."""

    operation_id: str
    tenant_id: str
    workspace_id: str
    peer_grant_id: str
    service_id: str
    network_lease_id: str
    source_node_id: str
    target_node_id: str
    source_subject_kind: OverlaySubjectKind
    target_subject_kind: OverlaySubjectKind
    publication_mode: OverlayPublicationMode

    def __post_init__(self) -> None:
        for identifier_field, identifier_value in (
            ("operation_id", self.operation_id),
            ("tenant_id", self.tenant_id),
            ("workspace_id", self.workspace_id),
            ("peer_grant_id", self.peer_grant_id),
            ("service_id", self.service_id),
            ("network_lease_id", self.network_lease_id),
            ("source_node_id", self.source_node_id),
            ("target_node_id", self.target_node_id),
        ):
            _require_uuid(identifier_value, field=identifier_field)
        if self.source_node_id == self.target_node_id:
            raise ValueError("overlay peers must be distinct nodes")
        if (
            self.source_subject_kind is not OverlaySubjectKind.TRUSTED_NODE_DAEMON
            or self.target_subject_kind is not OverlaySubjectKind.TRUSTED_NODE_DAEMON
        ):
            raise ValueError("Sandbox cannot become an Overlay peer")
        if self.publication_mode is not OverlayPublicationMode.BROKER_LOGICAL_SERVICE:
            raise ValueError("Overlay services must be published by the logical Broker")


@dataclass(frozen=True, slots=True)
class VerifiedOverlayBinding:
    """Fresh server-owned proof of Peer/Service/Lease/generation/fencing state."""

    tenant_id: str
    workspace_id: str
    peer_grant_id: str
    service_id: str
    network_lease_id: str
    source_node_id: str
    target_node_id: str
    workspace_generation: int
    service_generation: int
    peer_fencing_token: int
    network_fencing_token: int
    source_node_fencing_token: int
    target_node_fencing_token: int
    service_logical_name: str
    service_protocol: str
    service_transport_protocol: str
    service_port: int
    live_credential_generation: int
    verified_at: datetime
    expires_at: datetime
    verification_digest: str

    def __post_init__(self) -> None:
        for identifier_field, identifier_value in (
            ("tenant_id", self.tenant_id),
            ("workspace_id", self.workspace_id),
            ("peer_grant_id", self.peer_grant_id),
            ("service_id", self.service_id),
            ("network_lease_id", self.network_lease_id),
            ("source_node_id", self.source_node_id),
            ("target_node_id", self.target_node_id),
        ):
            _require_uuid(identifier_value, field=identifier_field)
        for fencing_field, fencing_value in (
            ("workspace_generation", self.workspace_generation),
            ("service_generation", self.service_generation),
            ("peer_fencing_token", self.peer_fencing_token),
            ("network_fencing_token", self.network_fencing_token),
            ("source_node_fencing_token", self.source_node_fencing_token),
            ("target_node_fencing_token", self.target_node_fencing_token),
        ):
            _require_positive(fencing_value, field=fencing_field)
        if _LOGICAL_NAME_RE.fullmatch(self.service_logical_name) is None:
            raise ValueError("service_logical_name must be a normalized logical identifier")
        if self.service_protocol not in _SERVICE_PROTOCOLS:
            raise ValueError("service_protocol is not a logical Broker protocol")
        if self.service_transport_protocol not in _TRANSPORT_PROTOCOLS:
            raise ValueError("service_transport_protocol must be tcp or udp")
        if not 1 <= self.service_port <= 65_535:
            raise ValueError("service_port must be within [1, 65535]")
        _require_non_negative(
            self.live_credential_generation,
            field="live_credential_generation",
        )
        _require_aware(self.verified_at, field="verified_at")
        _require_aware(self.expires_at, field="expires_at")
        if self.expires_at <= self.verified_at:
            raise ValueError("verified Overlay binding is already expired")
        _require_sha256(self.verification_digest, field="verification_digest")

    def verify_intent(self, intent: OverlayOperationIntent, *, now: datetime) -> None:
        _require_aware(now, field="clock")
        expected = (
            intent.tenant_id,
            intent.workspace_id,
            intent.peer_grant_id,
            intent.service_id,
            intent.network_lease_id,
            intent.source_node_id,
            intent.target_node_id,
        )
        actual = (
            self.tenant_id,
            self.workspace_id,
            self.peer_grant_id,
            self.service_id,
            self.network_lease_id,
            self.source_node_id,
            self.target_node_id,
        )
        if actual != expected or self.verified_at > now or self.expires_at <= now:
            raise OverlayRejected("overlay_binding_rejected")


@dataclass(frozen=True, slots=True)
class VerifiedNodeDaemon:
    """Short-lived proof for one trusted, fenced member Node Daemon."""

    daemon_id: str
    node_id: str
    node_fencing_token: int
    identity_thumbprint_digest: str
    attestation_digest: str
    verified_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        _require_uuid(self.daemon_id, field="daemon_id")
        _require_uuid(self.node_id, field="node_id")
        _require_positive(self.node_fencing_token, field="node_fencing_token")
        _require_sha256(
            self.identity_thumbprint_digest,
            field="identity_thumbprint_digest",
        )
        _require_sha256(self.attestation_digest, field="attestation_digest")
        _require_aware(self.verified_at, field="verified_at")
        _require_aware(self.expires_at, field="expires_at")
        if self.expires_at <= self.verified_at:
            raise ValueError("Node Daemon attestation is already expired")

    def verify_binding(
        self,
        binding: VerifiedOverlayBinding,
        *,
        expected_node_id: str,
        expected_fencing_token: int,
        now: datetime,
    ) -> None:
        _require_aware(now, field="clock")
        if (
            self.node_id != expected_node_id
            or self.node_fencing_token != expected_fencing_token
            or self.verified_at > now
            or self.expires_at <= now
            or self.expires_at > binding.expires_at
        ):
            raise OverlayRejected("overlay_node_daemon_attestation_rejected")


@dataclass(frozen=True, slots=True)
class ShortLivedCredentialReference:
    """Opaque secret-store reference; the raw Headscale/Tailscale key is absent."""

    reference: str
    provider: str
    operation_id: str
    action: OverlayAction
    network_lease_id: str
    binding_digest: str
    rotation_generation: int
    issued_at: datetime
    expires_at: datetime
    reference_digest: str

    def __post_init__(self) -> None:
        parsed = urlsplit(self.reference)
        if (
            parsed.scheme != "omnibase-secret"
            or parsed.netloc != "overlay"
            or not parsed.path.startswith("/leases/")
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
            or len(self.reference) > 256
        ):
            raise ValueError("credential reference must be an opaque Overlay secret reference")
        if _PROVIDER_RE.fullmatch(self.provider) is None:
            raise ValueError("credential provider must be a stable provider identifier")
        _require_uuid(self.operation_id, field="operation_id")
        if self.action not in {OverlayAction.ACTIVATE, OverlayAction.ROTATE}:
            raise ValueError("credential reference action is not a mutation")
        _require_uuid(self.network_lease_id, field="network_lease_id")
        _require_sha256(self.binding_digest, field="binding_digest")
        _require_positive(self.rotation_generation, field="rotation_generation")
        _require_aware(self.issued_at, field="issued_at")
        _require_aware(self.expires_at, field="expires_at")
        if self.expires_at <= self.issued_at:
            raise ValueError("credential reference is already expired")
        _require_sha256(self.reference_digest, field="reference_digest")

    def verify_binding(
        self,
        binding: VerifiedOverlayBinding,
        *,
        operation_id: str,
        action: OverlayAction,
        now: datetime,
    ) -> None:
        _require_aware(now, field="clock")
        if (
            self.operation_id != operation_id
            or self.action is not action
            or self.network_lease_id != binding.network_lease_id
            or self.binding_digest != binding.verification_digest
            or self.issued_at > now
            or self.expires_at <= now
            or self.expires_at > binding.expires_at
        ):
            raise OverlayRejected("overlay_credential_reference_rejected")


@dataclass(frozen=True, slots=True)
class OverlayDaemonCommand:
    action: OverlayAction
    operation_id: str
    provider: str
    binding: VerifiedOverlayBinding
    source_daemon: VerifiedNodeDaemon
    target_daemon: VerifiedNodeDaemon
    credential: ShortLivedCredentialReference | None
    requested_at: datetime

    def __post_init__(self) -> None:
        _require_uuid(self.operation_id, field="operation_id")
        if _PROVIDER_RE.fullmatch(self.provider) is None:
            raise ValueError("unsupported Overlay provider identifier")
        _require_aware(self.requested_at, field="requested_at")
        requires_credential = self.action in {OverlayAction.ACTIVATE, OverlayAction.ROTATE}
        if requires_credential != (self.credential is not None):
            raise ValueError("credential presence does not match Overlay action")


@dataclass(frozen=True, slots=True)
class OverlayDaemonReceipt:
    action: OverlayAction
    operation_id: str
    provider: str
    state: OverlayState
    network_lease_id: str
    binding_digest: str
    workspace_generation: int
    service_generation: int
    peer_fencing_token: int
    network_fencing_token: int
    source_node_fencing_token: int
    target_node_fencing_token: int
    source_daemon_attestation_digest: str
    target_daemon_attestation_digest: str
    credential_generation: int | None
    observed_at: datetime
    receipt_digest: str

    def __post_init__(self) -> None:
        _require_uuid(self.operation_id, field="operation_id")
        if _PROVIDER_RE.fullmatch(self.provider) is None:
            raise ValueError("unsupported Overlay provider identifier")
        _require_uuid(self.network_lease_id, field="network_lease_id")
        _require_sha256(self.binding_digest, field="binding_digest")
        for field, value in (
            ("workspace_generation", self.workspace_generation),
            ("service_generation", self.service_generation),
            ("peer_fencing_token", self.peer_fencing_token),
            ("network_fencing_token", self.network_fencing_token),
            ("source_node_fencing_token", self.source_node_fencing_token),
            ("target_node_fencing_token", self.target_node_fencing_token),
        ):
            _require_positive(value, field=field)
        if self.credential_generation is not None:
            _require_positive(self.credential_generation, field="credential_generation")
        _require_sha256(
            self.source_daemon_attestation_digest,
            field="source_daemon_attestation_digest",
        )
        _require_sha256(
            self.target_daemon_attestation_digest,
            field="target_daemon_attestation_digest",
        )
        _require_aware(self.observed_at, field="observed_at")
        _require_sha256(self.receipt_digest, field="receipt_digest")

    def verify_command(self, command: OverlayDaemonCommand) -> None:
        binding = command.binding
        expected_credential_generation = (
            command.credential.rotation_generation if command.credential is not None else None
        )
        if (
            self.action is not command.action
            or self.operation_id != command.operation_id
            or self.provider != command.provider
            or self.network_lease_id != binding.network_lease_id
            or self.binding_digest != binding.verification_digest
            or self.workspace_generation != binding.workspace_generation
            or self.service_generation != binding.service_generation
            or self.peer_fencing_token != binding.peer_fencing_token
            or self.network_fencing_token != binding.network_fencing_token
            or self.source_node_fencing_token != binding.source_node_fencing_token
            or self.target_node_fencing_token != binding.target_node_fencing_token
            or self.source_daemon_attestation_digest != command.source_daemon.attestation_digest
            or self.target_daemon_attestation_digest != command.target_daemon.attestation_digest
            or self.credential_generation != expected_credential_generation
            or self.observed_at < binding.verified_at
            or self.observed_at >= binding.expires_at
        ):
            raise OverlayRejected("overlay_daemon_receipt_binding_rejected")

    def verify_live_replay(
        self,
        *,
        action: OverlayAction,
        operation_id: str,
        provider: str,
        binding: VerifiedOverlayBinding,
        now: datetime,
    ) -> None:
        """Verify a committed receipt against freshly checked live fencing.

        Daemon attestations are deliberately not compared byte-for-byte because
        an exact replay may be verified under newer short-lived attestations.
        Their Node IDs and fencing tokens were revalidated before this method.
        """

        _require_aware(now, field="clock")
        if (
            self.action is not action
            or self.operation_id != operation_id
            or self.provider != provider
            or self.network_lease_id != binding.network_lease_id
            or self.workspace_generation != binding.workspace_generation
            or self.service_generation != binding.service_generation
            or self.peer_fencing_token != binding.peer_fencing_token
            or self.network_fencing_token != binding.network_fencing_token
            or self.source_node_fencing_token != binding.source_node_fencing_token
            or self.target_node_fencing_token != binding.target_node_fencing_token
            or self.observed_at >= binding.expires_at
            or self.observed_at > now
        ):
            raise OverlayRejected("overlay_operation_replay_binding_rejected")

    @property
    def usable(self) -> bool:
        return self.state is OverlayState.ACTIVE


def overlay_operation_binding_digest(
    *,
    intent: OverlayOperationIntent,
    action: OverlayAction,
    binding: VerifiedOverlayBinding,
) -> str:
    """Bind durable idempotency to stable live control-plane facts.

    The current credential generation is intentionally excluded: a successful
    activate/rotate operation advances it, and an exact replay must still find
    the committed receipt.  Generation/fencing drift of the Workspace, service,
    lease, peer or Nodes remains a conflict.
    """

    encoded = json.dumps(
        {
            "action": action.value,
            "intent": {
                "network_lease_id": intent.network_lease_id,
                "operation_id": intent.operation_id,
                "peer_grant_id": intent.peer_grant_id,
                "publication_mode": intent.publication_mode.value,
                "service_id": intent.service_id,
                "source_node_id": intent.source_node_id,
                "source_subject_kind": intent.source_subject_kind.value,
                "target_node_id": intent.target_node_id,
                "target_subject_kind": intent.target_subject_kind.value,
                "tenant_id": intent.tenant_id,
                "workspace_id": intent.workspace_id,
            },
            "live_binding": {
                "network_fencing_token": binding.network_fencing_token,
                "peer_fencing_token": binding.peer_fencing_token,
                "service_generation": binding.service_generation,
                "service_logical_name": binding.service_logical_name,
                "service_port": binding.service_port,
                "service_protocol": binding.service_protocol,
                "service_transport_protocol": binding.service_transport_protocol,
                "source_node_fencing_token": binding.source_node_fencing_token,
                "target_node_fencing_token": binding.target_node_fencing_token,
                "workspace_generation": binding.workspace_generation,
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class OverlayOperationReservation:
    """Durable mutation reservation returned by an injected ledger."""

    operation_id: str
    action: OverlayAction
    operation_binding_digest: str
    replayed: bool = False
    receipt: OverlayDaemonReceipt | None = None

    def __post_init__(self) -> None:
        _require_uuid(self.operation_id, field="operation_id")
        if self.action is OverlayAction.STATUS:
            raise ValueError("read-only status does not use the mutation ledger")
        _require_sha256(
            self.operation_binding_digest,
            field="operation_binding_digest",
        )
        if not isinstance(self.replayed, bool):
            raise TypeError("replayed must be bool")
        if self.replayed != (self.receipt is not None):
            raise ValueError("replayed reservation must carry its committed receipt")


class OverlayOperationLedger(Protocol):
    """Durable, atomic idempotency ledger supplied by the composition root."""

    def replay(
        self,
        *,
        operation_id: str,
        action: OverlayAction,
        operation_binding_digest: str,
    ) -> OverlayDaemonReceipt | None: ...

    def reserve(
        self,
        *,
        operation_id: str,
        action: OverlayAction,
        operation_binding_digest: str,
    ) -> OverlayOperationReservation: ...

    def commit(
        self,
        *,
        reservation: OverlayOperationReservation,
        receipt: OverlayDaemonReceipt,
    ) -> None: ...


class RejectingOverlayOperationLedger:
    def replay(
        self,
        *,
        operation_id: str,
        action: OverlayAction,
        operation_binding_digest: str,
    ) -> OverlayDaemonReceipt | None:
        del operation_id, action, operation_binding_digest
        raise OverlayUnavailable("overlay_operation_ledger_unavailable")

    def reserve(
        self,
        *,
        operation_id: str,
        action: OverlayAction,
        operation_binding_digest: str,
    ) -> OverlayOperationReservation:
        del operation_id, action, operation_binding_digest
        raise OverlayUnavailable("overlay_operation_ledger_unavailable")

    def commit(
        self,
        *,
        reservation: OverlayOperationReservation,
        receipt: OverlayDaemonReceipt,
    ) -> None:
        del reservation, receipt
        raise OverlayUnavailable("overlay_operation_ledger_unavailable")


@dataclass(frozen=True, slots=True)
class OverlayLogicalServicePublication:
    """Server-owned publication consumed by the Workspace Network Broker.

    This DTO intentionally has no address, route, provider handle, credential
    reference, daemon identity or Sandbox runtime identity.
    """

    tenant_id: str
    workspace_id: str
    service_id: str
    publisher_node_id: str
    network_lease_id: str
    logical_name: str
    application_protocol: str
    transport_protocol: str
    logical_port: int
    workspace_generation: int
    service_version: int
    publisher_node_fencing_token: int
    network_fencing_token: int
    binding_digest: str
    active_receipt_digest: str
    published_at: datetime
    expires_at: datetime
    publication_digest: str

    def __post_init__(self) -> None:
        for field, value in (
            ("tenant_id", self.tenant_id),
            ("workspace_id", self.workspace_id),
            ("service_id", self.service_id),
            ("publisher_node_id", self.publisher_node_id),
            ("network_lease_id", self.network_lease_id),
        ):
            _require_uuid(value, field=field)
        if _LOGICAL_NAME_RE.fullmatch(self.logical_name) is None:
            raise ValueError("logical_name must be a normalized logical identifier")
        if self.application_protocol not in _SERVICE_PROTOCOLS:
            raise ValueError("application_protocol is not a logical Broker protocol")
        if self.transport_protocol not in _TRANSPORT_PROTOCOLS:
            raise ValueError("transport_protocol must be tcp or udp")
        if not 1 <= self.logical_port <= 65_535:
            raise ValueError("logical_port must be within [1, 65535]")
        for generation_field, generation_value in (
            ("workspace_generation", self.workspace_generation),
            ("service_version", self.service_version),
            ("publisher_node_fencing_token", self.publisher_node_fencing_token),
            ("network_fencing_token", self.network_fencing_token),
        ):
            _require_positive(generation_value, field=generation_field)
        _require_sha256(self.binding_digest, field="binding_digest")
        _require_sha256(self.active_receipt_digest, field="active_receipt_digest")
        _require_aware(self.published_at, field="published_at")
        _require_aware(self.expires_at, field="expires_at")
        if self.expires_at <= self.published_at:
            raise ValueError("logical service publication is already expired")
        _require_sha256(self.publication_digest, field="publication_digest")

    def expected_digest(self) -> str:
        encoded = json.dumps(
            {
                "active_receipt_digest": self.active_receipt_digest,
                "application_protocol": self.application_protocol,
                "binding_digest": self.binding_digest,
                "expires_at": self.expires_at.isoformat(),
                "logical_name": self.logical_name,
                "logical_port": self.logical_port,
                "network_fencing_token": self.network_fencing_token,
                "network_lease_id": self.network_lease_id,
                "published_at": self.published_at.isoformat(),
                "publisher_node_fencing_token": self.publisher_node_fencing_token,
                "publisher_node_id": self.publisher_node_id,
                "service_id": self.service_id,
                "service_version": self.service_version,
                "tenant_id": self.tenant_id,
                "transport_protocol": self.transport_protocol,
                "workspace_generation": self.workspace_generation,
                "workspace_id": self.workspace_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def verify(self, *, now: datetime) -> None:
        _require_aware(now, field="clock")
        if (
            self.published_at > now
            or self.expires_at <= now
            or self.publication_digest != self.expected_digest()
        ):
            raise OverlayRejected("overlay_logical_service_publication_rejected")


class OverlayBindingVerifier(Protocol):
    def verify(
        self,
        *,
        intent: OverlayOperationIntent,
        action: OverlayAction,
    ) -> VerifiedOverlayBinding: ...


class NodeDaemonAttestor(Protocol):
    def attest(
        self,
        *,
        binding: VerifiedOverlayBinding,
        node_id: str,
    ) -> VerifiedNodeDaemon: ...


class CredentialReferenceIssuer(Protocol):
    def issue(
        self,
        *,
        binding: VerifiedOverlayBinding,
        operation_id: str,
        action: OverlayAction,
    ) -> ShortLivedCredentialReference: ...


class RejectingOverlayBindingVerifier:
    def verify(
        self,
        *,
        intent: OverlayOperationIntent,
        action: OverlayAction,
    ) -> VerifiedOverlayBinding:
        del intent, action
        raise OverlayUnavailable("overlay_binding_verifier_unavailable")


class RejectingNodeDaemonAttestor:
    def attest(
        self,
        *,
        binding: VerifiedOverlayBinding,
        node_id: str,
    ) -> VerifiedNodeDaemon:
        del binding, node_id
        raise OverlayUnavailable("overlay_node_daemon_attestor_unavailable")


class RejectingCredentialReferenceIssuer:
    def issue(
        self,
        *,
        binding: VerifiedOverlayBinding,
        operation_id: str,
        action: OverlayAction,
    ) -> ShortLivedCredentialReference:
        del binding, operation_id, action
        raise OverlayUnavailable("overlay_credential_issuer_unavailable")


__all__ = [
    "CredentialReferenceIssuer",
    "NodeDaemonAttestor",
    "OverlayAction",
    "OverlayAdapterError",
    "OverlayBindingVerifier",
    "OverlayDaemonCommand",
    "OverlayDaemonReceipt",
    "OverlayLogicalServicePublication",
    "OverlayOperationIntent",
    "OverlayOperationLedger",
    "OverlayOperationReservation",
    "OverlayOutcomeUnknown",
    "OverlayPublicationMode",
    "OverlayRejected",
    "OverlayState",
    "OverlaySubjectKind",
    "OverlayUnavailable",
    "RejectingCredentialReferenceIssuer",
    "RejectingNodeDaemonAttestor",
    "RejectingOverlayBindingVerifier",
    "RejectingOverlayOperationLedger",
    "ShortLivedCredentialReference",
    "VerifiedNodeDaemon",
    "VerifiedOverlayBinding",
    "overlay_operation_binding_digest",
]
