"""Fail-closed logical network contracts for P34.5B.

The objects in this module deliberately contain no socket, DNS client,
Overlay credential or provider handle.  A Sandbox addresses a logical service
and every use is rebound to current server-owned Workspace, Run, Node and
Network fencing state.  Physical resolution remains an internal Broker concern.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from omnibase.sandbox.contracts import SandboxRejected, SandboxUnavailable

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LOGICAL_NAME_RE = re.compile(r"^[a-z][a-z0-9.-]{0,126}[a-z0-9]$|^[a-z]$")
_MAX_BYTES = 16 * 1024 * 1024 * 1024
_NETWORK_NAMESPACE_IDENTITY_RE = re.compile(r"^[1-9][0-9]*:[1-9][0-9]*$")


def _require_aware(value: datetime, *, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_positive_int(value: int, *, name: str, maximum: int = 2**63 - 1) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be a positive integer")


def _require_non_negative_int(
    value: int,
    *,
    name: str,
    maximum: int = _MAX_BYTES,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError(f"{name} must be a non-negative integer")


def _require_sha256(value: str, *, name: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be sha256")


def stable_digest(value: object) -> str:
    """Return the canonical digest used for network binding evidence."""

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class NetworkProtocol(StrEnum):
    TCP = "tcp"
    UDP = "udp"


class NetworkRouteKind(StrEnum):
    """Server-owned route classification, never supplied by a Sandbox."""

    WORKSPACE_SERVICE = "workspace_service"
    MEMBER_OVERLAY = "member_overlay"
    PUBLIC_INTERNET = "public_internet"


@dataclass(frozen=True, slots=True)
class SandboxNetworkBudget:
    """Aggregate limits attached to one live logical Network lease."""

    max_connections: int
    max_bytes_in: int
    max_bytes_out: int
    max_ttl_seconds: int

    def __post_init__(self) -> None:
        _require_positive_int(
            self.max_connections,
            name="max_connections",
            maximum=4_096,
        )
        _require_non_negative_int(self.max_bytes_in, name="max_bytes_in")
        _require_non_negative_int(self.max_bytes_out, name="max_bytes_out")
        _require_positive_int(
            self.max_ttl_seconds,
            name="max_ttl_seconds",
            maximum=3_600,
        )


@dataclass(frozen=True, slots=True)
class LogicalNetworkService:
    """A published logical service with no physical locator or credential."""

    service_id: UUID
    tenant_id: UUID
    workspace_id: UUID
    publisher_node_id: UUID
    logical_name: str
    protocol: NetworkProtocol
    logical_port: int
    workspace_generation: int
    publisher_node_fencing_token: int
    network_fencing_token: int
    service_version: int
    expires_at: datetime
    active: bool = True
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, UUID)
            for value in (
                self.service_id,
                self.tenant_id,
                self.workspace_id,
                self.publisher_node_id,
            )
        ):
            raise TypeError("logical service identifiers must be UUID values")
        if (
            not isinstance(self.logical_name, str)
            or _LOGICAL_NAME_RE.fullmatch(self.logical_name) is None
        ):
            raise ValueError("logical_name must be a normalized logical identifier")
        if not isinstance(self.protocol, NetworkProtocol):
            raise TypeError("protocol must be NetworkProtocol")
        _require_positive_int(self.logical_port, name="logical_port", maximum=65_535)
        for name, value in (
            ("workspace_generation", self.workspace_generation),
            ("publisher_node_fencing_token", self.publisher_node_fencing_token),
            ("network_fencing_token", self.network_fencing_token),
            ("service_version", self.service_version),
        ):
            _require_positive_int(value, name=name)
        _require_aware(self.expires_at, name="expires_at")
        if not isinstance(self.active, bool):
            raise TypeError("active must be bool")
        if self.revoked_at is not None:
            _require_aware(self.revoked_at, name="revoked_at")


@dataclass(frozen=True, slots=True)
class SandboxNetworkAuthorizationRequest:
    """Untrusted claims; possession never authorizes a connection."""

    operation_id: UUID
    tenant_id: UUID
    workspace_id: UUID
    run_id: UUID
    runtime_instance_id: UUID
    node_id: UUID
    network_lease_id: UUID
    logical_service_id: UUID
    workload_identity_thumbprint: str
    workspace_generation: int
    run_fencing_token: int
    node_fencing_token: int
    network_fencing_token: int
    service_version: int
    protocol: NetworkProtocol
    port: int
    requested_connections: int
    requested_bytes_in: int
    requested_bytes_out: int
    deadline: datetime
    direct_overlay: bool = False

    def __post_init__(self) -> None:
        identifiers = (
            self.operation_id,
            self.tenant_id,
            self.workspace_id,
            self.run_id,
            self.runtime_instance_id,
            self.node_id,
            self.network_lease_id,
            self.logical_service_id,
        )
        if any(not isinstance(value, UUID) for value in identifiers):
            raise TypeError("network request identifiers must be UUID values")
        _require_sha256(
            self.workload_identity_thumbprint,
            name="workload_identity_thumbprint",
        )
        for name, value in (
            ("workspace_generation", self.workspace_generation),
            ("run_fencing_token", self.run_fencing_token),
            ("node_fencing_token", self.node_fencing_token),
            ("network_fencing_token", self.network_fencing_token),
            ("service_version", self.service_version),
        ):
            _require_positive_int(value, name=name)
        if not isinstance(self.protocol, NetworkProtocol):
            raise TypeError("protocol must be NetworkProtocol")
        _require_positive_int(self.port, name="port", maximum=65_535)
        _require_positive_int(
            self.requested_connections,
            name="requested_connections",
            maximum=1_024,
        )
        _require_non_negative_int(self.requested_bytes_in, name="requested_bytes_in")
        _require_non_negative_int(self.requested_bytes_out, name="requested_bytes_out")
        _require_aware(self.deadline, name="deadline")
        if not isinstance(self.direct_overlay, bool):
            raise TypeError("direct_overlay must be bool")

    def binding_digest(self) -> str:
        return stable_digest(
            {
                "deadline": self.deadline.isoformat(),
                "direct_overlay": self.direct_overlay,
                "logical_service_id": str(self.logical_service_id),
                "network_fencing_token": self.network_fencing_token,
                "network_lease_id": str(self.network_lease_id),
                "node_fencing_token": self.node_fencing_token,
                "node_id": str(self.node_id),
                "operation_id": str(self.operation_id),
                "port": self.port,
                "protocol": self.protocol.value,
                "requested_bytes_in": self.requested_bytes_in,
                "requested_bytes_out": self.requested_bytes_out,
                "requested_connections": self.requested_connections,
                "run_fencing_token": self.run_fencing_token,
                "run_id": str(self.run_id),
                "runtime_instance_id": str(self.runtime_instance_id),
                "service_version": self.service_version,
                "tenant_id": str(self.tenant_id),
                "workload_identity_thumbprint": self.workload_identity_thumbprint,
                "workspace_generation": self.workspace_generation,
                "workspace_id": str(self.workspace_id),
            }
        )


@dataclass(frozen=True, slots=True)
class VerifiedSandboxNetworkAuthorization:
    """Current server-owned authorization and service facts."""

    request: SandboxNetworkAuthorizationRequest
    service: LogicalNetworkService
    expected_runner_id: UUID
    expected_namespace_id: UUID
    expected_network_namespace_identity: str
    expected_namespace_process_id: int
    expected_namespace_process_start_time_ticks: int
    budget: SandboxNetworkBudget
    allowed_service_ids: tuple[UUID, ...]
    policy_digest: str
    verified_at: datetime
    expires_at: datetime
    verification_digest: str
    revoked: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.request, SandboxNetworkAuthorizationRequest):
            raise TypeError("request must be SandboxNetworkAuthorizationRequest")
        if not isinstance(self.service, LogicalNetworkService):
            raise TypeError("service must be LogicalNetworkService")
        if not isinstance(self.expected_runner_id, UUID) or not isinstance(
            self.expected_namespace_id, UUID
        ):
            raise TypeError("expected network runtime identifiers must be UUID values")
        if (
            not isinstance(self.expected_network_namespace_identity, str)
            or _NETWORK_NAMESPACE_IDENTITY_RE.fullmatch(self.expected_network_namespace_identity)
            is None
        ):
            raise ValueError("expected_network_namespace_identity is invalid")
        _require_positive_int(
            self.expected_namespace_process_id,
            name="expected_namespace_process_id",
            maximum=2**31 - 1,
        )
        _require_positive_int(
            self.expected_namespace_process_start_time_ticks,
            name="expected_namespace_process_start_time_ticks",
        )
        if not isinstance(self.budget, SandboxNetworkBudget):
            raise TypeError("budget must be SandboxNetworkBudget")
        if not isinstance(self.allowed_service_ids, tuple) or not all(
            isinstance(service_id, UUID) for service_id in self.allowed_service_ids
        ):
            raise TypeError("allowed_service_ids must be an immutable UUID tuple")
        if len(set(self.allowed_service_ids)) != len(self.allowed_service_ids):
            raise ValueError("allowed_service_ids cannot contain duplicates")
        _require_sha256(self.policy_digest, name="policy_digest")
        _require_aware(self.verified_at, name="verified_at")
        _require_aware(self.expires_at, name="expires_at")
        if self.expires_at <= self.verified_at:
            raise ValueError("network authorization is already expired")
        _require_sha256(self.verification_digest, name="verification_digest")
        if not isinstance(self.revoked, bool):
            raise TypeError("revoked must be bool")


class SandboxNetworkAuthorizer(Protocol):
    """Live verifier for lease, service, revocation, fencing and identity."""

    def authorize(
        self,
        request: SandboxNetworkAuthorizationRequest,
    ) -> VerifiedSandboxNetworkAuthorization: ...


class RejectingSandboxNetworkAuthorizer:
    def authorize(
        self,
        request: SandboxNetworkAuthorizationRequest,
    ) -> VerifiedSandboxNetworkAuthorization:
        del request
        raise SandboxUnavailable("sandbox_network_authorizer_unavailable")


@dataclass(frozen=True, slots=True)
class NetworkDestination:
    """Internal resolver result; never a public request or SDK contract."""

    service_id: UUID
    protocol: NetworkProtocol
    port: int
    address: ipaddress.IPv4Address | ipaddress.IPv6Address
    route_kind: NetworkRouteKind
    resolution_digest: str
    resolved_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.service_id, UUID):
            raise TypeError("service_id must be UUID")
        if not isinstance(self.protocol, NetworkProtocol):
            raise TypeError("protocol must be NetworkProtocol")
        _require_positive_int(self.port, name="port", maximum=65_535)
        if not isinstance(self.address, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
            raise TypeError("address must be an IP address object")
        if not isinstance(self.route_kind, NetworkRouteKind):
            raise TypeError("route_kind must be NetworkRouteKind")
        _require_sha256(self.resolution_digest, name="resolution_digest")
        _require_aware(self.resolved_at, name="resolved_at")
        _require_aware(self.expires_at, name="expires_at")
        if self.expires_at <= self.resolved_at:
            raise ValueError("network destination is already expired")

    @classmethod
    def from_text(
        cls,
        *,
        service_id: UUID,
        protocol: NetworkProtocol,
        port: int,
        address: str,
        route_kind: NetworkRouteKind,
        resolution_digest: str,
        resolved_at: datetime,
        expires_at: datetime,
    ) -> NetworkDestination:
        return cls(
            service_id=service_id,
            protocol=protocol,
            port=port,
            address=ipaddress.ip_address(address),
            route_kind=route_kind,
            resolution_digest=resolution_digest,
            resolved_at=resolved_at,
            expires_at=expires_at,
        )


class WorkspaceServiceResolver(Protocol):
    """Trusted internal service resolver; called again immediately before use."""

    def resolve(
        self,
        *,
        authorization: VerifiedSandboxNetworkAuthorization,
    ) -> NetworkDestination: ...


class RejectingWorkspaceServiceResolver:
    def resolve(
        self,
        *,
        authorization: VerifiedSandboxNetworkAuthorization,
    ) -> NetworkDestination:
        del authorization
        raise SandboxUnavailable("workspace_service_resolver_unavailable")


def validate_destination_address(destination: NetworkDestination) -> None:
    """Reject destinations that could bypass the logical service boundary."""

    address = destination.address
    blocked = (
        address.is_unspecified
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_private
        or address.is_reserved
    )
    if isinstance(address, ipaddress.IPv4Address) and int(address) == 0xFFFFFFFF:
        blocked = True
    if blocked:
        raise SandboxRejected("sandbox_network_destination_rejected")
    if destination.route_kind is NetworkRouteKind.MEMBER_OVERLAY:
        raise SandboxRejected("sandbox_direct_overlay_rejected")
    if destination.route_kind is NetworkRouteKind.PUBLIC_INTERNET:
        raise SandboxRejected("sandbox_direct_public_internet_rejected")
    if destination.route_kind is not NetworkRouteKind.WORKSPACE_SERVICE:
        raise SandboxRejected("sandbox_network_route_rejected")


__all__ = [
    "LogicalNetworkService",
    "NetworkDestination",
    "NetworkProtocol",
    "NetworkRouteKind",
    "RejectingSandboxNetworkAuthorizer",
    "RejectingWorkspaceServiceResolver",
    "SandboxNetworkAuthorizationRequest",
    "SandboxNetworkAuthorizer",
    "SandboxNetworkBudget",
    "VerifiedSandboxNetworkAuthorization",
    "WorkspaceServiceResolver",
    "stable_digest",
    "validate_destination_address",
]
