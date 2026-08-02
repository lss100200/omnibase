"""Controlled Workspace Network Broker seam for P34.5B.

The production defaults in this module reject every request.  The controlled
implementation only orders live authorization, trusted network-namespace
attestation, repeated logical-service resolution, budget reservation and an
independent transport seam.  No class here opens a socket or joins an Overlay.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from omnibase.sandbox.contracts import (
    SandboxConflict,
    SandboxRejected,
    SandboxUnavailable,
    utc_now,
)
from omnibase.sandbox.network import (
    NetworkDestination,
    RejectingSandboxNetworkAuthorizer,
    RejectingWorkspaceServiceResolver,
    SandboxNetworkAuthorizationRequest,
    SandboxNetworkAuthorizer,
    VerifiedSandboxNetworkAuthorization,
    WorkspaceServiceResolver,
    stable_digest,
    validate_destination_address,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NETWORK_NAMESPACE_IDENTITY_RE = re.compile(r"^[1-9][0-9]*:[1-9][0-9]*$")


def _require_aware(value: datetime, *, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_positive_int(value: int, *, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _require_non_negative_int(value: int, *, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _require_sha256(value: str, *, name: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be sha256")


@dataclass(frozen=True, slots=True)
class VerifiedNetworkNamespace:
    """Trusted proof that the Sandbox is in the expected isolated namespace."""

    namespace_id: UUID
    network_namespace_identity: str
    namespace_process_id: int
    namespace_process_start_time_ticks: int
    runner_id: UUID
    node_id: UUID
    runtime_instance_id: UUID
    workload_identity_thumbprint: str
    workspace_generation: int
    run_fencing_token: int
    node_fencing_token: int
    network_fencing_token: int
    policy_digest: str
    direct_overlay: bool
    verified_at: datetime
    expires_at: datetime
    evidence_digest: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, UUID)
            for value in (
                self.namespace_id,
                self.runner_id,
                self.node_id,
                self.runtime_instance_id,
            )
        ):
            raise TypeError("network namespace identifiers must be UUID values")
        if (
            not isinstance(self.network_namespace_identity, str)
            or _NETWORK_NAMESPACE_IDENTITY_RE.fullmatch(self.network_namespace_identity) is None
        ):
            raise ValueError("network_namespace_identity is invalid")
        _require_positive_int(self.namespace_process_id, name="namespace_process_id")
        _require_positive_int(
            self.namespace_process_start_time_ticks,
            name="namespace_process_start_time_ticks",
        )
        _require_sha256(
            self.workload_identity_thumbprint,
            name="workload_identity_thumbprint",
        )
        for name, value in (
            ("workspace_generation", self.workspace_generation),
            ("run_fencing_token", self.run_fencing_token),
            ("node_fencing_token", self.node_fencing_token),
            ("network_fencing_token", self.network_fencing_token),
        ):
            _require_positive_int(value, name=name)
        _require_sha256(self.policy_digest, name="policy_digest")
        if not isinstance(self.direct_overlay, bool):
            raise TypeError("direct_overlay must be bool")
        _require_aware(self.verified_at, name="verified_at")
        _require_aware(self.expires_at, name="expires_at")
        if self.expires_at <= self.verified_at:
            raise ValueError("network namespace proof is already expired")
        _require_sha256(self.evidence_digest, name="evidence_digest")


class NetworkNamespaceAttestor(Protocol):
    def attest(
        self,
        *,
        authorization: VerifiedSandboxNetworkAuthorization,
    ) -> VerifiedNetworkNamespace: ...


class RejectingNetworkNamespaceAttestor:
    def attest(
        self,
        *,
        authorization: VerifiedSandboxNetworkAuthorization,
    ) -> VerifiedNetworkNamespace:
        del authorization
        raise SandboxUnavailable("sandbox_network_namespace_attestor_unavailable")


@dataclass(frozen=True, slots=True)
class BrokerConnectionPlan:
    operation_id: UUID
    request_binding_digest: str
    authorization_digest: str
    namespace_evidence_digest: str
    destination_resolution_digest: str
    plan_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, UUID):
            raise TypeError("operation_id must be UUID")
        for name, value in (
            ("request_binding_digest", self.request_binding_digest),
            ("authorization_digest", self.authorization_digest),
            ("namespace_evidence_digest", self.namespace_evidence_digest),
            ("destination_resolution_digest", self.destination_resolution_digest),
            ("plan_digest", self.plan_digest),
        ):
            _require_sha256(value, name=name)


@dataclass(frozen=True, slots=True)
class BrokerConnectionReceipt:
    operation_id: UUID
    request_binding_digest: str
    plan_digest: str
    namespace_evidence_digest: str
    destination_resolution_digest: str
    connections: int
    bytes_in: int
    bytes_out: int
    accepted_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, UUID):
            raise TypeError("operation_id must be UUID")
        for name, value in (
            ("request_binding_digest", self.request_binding_digest),
            ("plan_digest", self.plan_digest),
            ("namespace_evidence_digest", self.namespace_evidence_digest),
            ("destination_resolution_digest", self.destination_resolution_digest),
        ):
            _require_sha256(value, name=name)
        _require_positive_int(self.connections, name="connections")
        _require_non_negative_int(self.bytes_in, name="bytes_in")
        _require_non_negative_int(self.bytes_out, name="bytes_out")
        _require_aware(self.accepted_at, name="accepted_at")


class BrokerTransport(Protocol):
    """Independent trusted transport; implementations live outside Core API."""

    def connect(
        self,
        *,
        plan: BrokerConnectionPlan,
        namespace: VerifiedNetworkNamespace,
        destination: NetworkDestination,
    ) -> BrokerConnectionReceipt: ...


class UnavailableBrokerTransport:
    def connect(
        self,
        *,
        plan: BrokerConnectionPlan,
        namespace: VerifiedNetworkNamespace,
        destination: NetworkDestination,
    ) -> BrokerConnectionReceipt:
        del plan, namespace, destination
        raise SandboxUnavailable("workspace_network_broker_transport_unavailable")


@dataclass(frozen=True, slots=True)
class NetworkBudgetReservation:
    operation_id: UUID
    binding_digest: str
    replayed: bool
    receipt: BrokerConnectionReceipt | None

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, UUID):
            raise TypeError("operation_id must be UUID")
        _require_sha256(self.binding_digest, name="binding_digest")
        if not isinstance(self.replayed, bool):
            raise TypeError("replayed must be bool")
        if self.replayed != (self.receipt is not None):
            raise ValueError("replayed reservation must contain its committed receipt")


class NetworkBudgetOperationState(StrEnum):
    """Durable one-way state for one budgeted network side effect."""

    PENDING = "pending"
    COMMITTED = "committed"
    UNKNOWN = "unknown"


class NetworkBudgetLedger(Protocol):
    def reserve(
        self,
        *,
        authorization: VerifiedSandboxNetworkAuthorization,
    ) -> NetworkBudgetReservation: ...

    def commit(
        self,
        *,
        reservation: NetworkBudgetReservation,
        receipt: BrokerConnectionReceipt,
    ) -> None: ...

    def mark_unknown(self, *, reservation: NetworkBudgetReservation) -> None: ...


class UnavailableNetworkBudgetLedger:
    def reserve(
        self,
        *,
        authorization: VerifiedSandboxNetworkAuthorization,
    ) -> NetworkBudgetReservation:
        del authorization
        raise SandboxUnavailable("sandbox_network_budget_ledger_unavailable")

    def commit(
        self,
        *,
        reservation: NetworkBudgetReservation,
        receipt: BrokerConnectionReceipt,
    ) -> None:
        del reservation, receipt
        raise SandboxUnavailable("sandbox_network_budget_ledger_unavailable")

    def mark_unknown(self, *, reservation: NetworkBudgetReservation) -> None:
        del reservation
        raise SandboxUnavailable("sandbox_network_budget_ledger_unavailable")


@dataclass(slots=True)
class _LedgerEntry:
    binding_digest: str
    network_lease_id: UUID
    connections: int
    bytes_in: int
    bytes_out: int
    state: NetworkBudgetOperationState = NetworkBudgetOperationState.PENDING
    receipt: BrokerConnectionReceipt | None = None


class InMemoryNetworkBudgetLedger:
    """Thread-safe deterministic test ledger; not a production data store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[UUID, _LedgerEntry] = {}

    def reserve(
        self,
        *,
        authorization: VerifiedSandboxNetworkAuthorization,
    ) -> NetworkBudgetReservation:
        request = authorization.request
        binding = network_budget_binding_digest(authorization)
        with self._lock:
            existing = self._entries.get(request.operation_id)
            if existing is not None:
                if existing.binding_digest != binding:
                    raise SandboxConflict("sandbox_network_operation_binding_conflict")
                if existing.state is not NetworkBudgetOperationState.COMMITTED:
                    raise SandboxConflict("sandbox_network_operation_outcome_unknown")
                if existing.receipt is None:
                    raise SandboxConflict("sandbox_network_operation_outcome_unknown")
                return NetworkBudgetReservation(
                    operation_id=request.operation_id,
                    binding_digest=binding,
                    replayed=True,
                    receipt=existing.receipt,
                )
            same_lease = [
                entry
                for entry in self._entries.values()
                if entry.network_lease_id == request.network_lease_id
            ]
            used_connections = sum(entry.connections for entry in same_lease)
            used_bytes_in = sum(entry.bytes_in for entry in same_lease)
            used_bytes_out = sum(entry.bytes_out for entry in same_lease)
            budget = authorization.budget
            if used_connections + request.requested_connections > budget.max_connections:
                raise SandboxRejected("sandbox_network_connection_budget_exceeded")
            if used_bytes_in + request.requested_bytes_in > budget.max_bytes_in:
                raise SandboxRejected("sandbox_network_bytes_in_budget_exceeded")
            if used_bytes_out + request.requested_bytes_out > budget.max_bytes_out:
                raise SandboxRejected("sandbox_network_bytes_out_budget_exceeded")
            self._entries[request.operation_id] = _LedgerEntry(
                binding_digest=binding,
                network_lease_id=request.network_lease_id,
                connections=request.requested_connections,
                bytes_in=request.requested_bytes_in,
                bytes_out=request.requested_bytes_out,
            )
            return NetworkBudgetReservation(
                operation_id=request.operation_id,
                binding_digest=binding,
                replayed=False,
                receipt=None,
            )

    def commit(
        self,
        *,
        reservation: NetworkBudgetReservation,
        receipt: BrokerConnectionReceipt,
    ) -> None:
        if reservation.replayed:
            raise SandboxConflict("sandbox_network_operation_already_committed")
        with self._lock:
            entry = self._entries.get(reservation.operation_id)
            if entry is None or entry.binding_digest != reservation.binding_digest:
                raise SandboxConflict("sandbox_network_operation_binding_conflict")
            if entry.receipt is not None:
                if entry.receipt != receipt:
                    raise SandboxConflict("sandbox_network_receipt_conflict")
                return
            entry.receipt = receipt
            entry.state = NetworkBudgetOperationState.COMMITTED

    def mark_unknown(self, *, reservation: NetworkBudgetReservation) -> None:
        if reservation.replayed:
            return
        with self._lock:
            entry = self._entries.get(reservation.operation_id)
            if entry is None or entry.binding_digest != reservation.binding_digest:
                raise SandboxConflict("sandbox_network_operation_binding_conflict")
            if entry.state is NetworkBudgetOperationState.COMMITTED:
                return
            entry.state = NetworkBudgetOperationState.UNKNOWN


def network_budget_binding_digest(
    authorization: VerifiedSandboxNetworkAuthorization,
) -> str:
    """Canonical authorization/budget binding shared by all ledger backends."""

    request = authorization.request
    service = authorization.service
    budget = authorization.budget
    return stable_digest(
        {
            "allowed_service_ids": sorted(
                str(service_id) for service_id in authorization.allowed_service_ids
            ),
            "authorization_verification_digest": authorization.verification_digest,
            "budget": {
                "max_bytes_in": budget.max_bytes_in,
                "max_bytes_out": budget.max_bytes_out,
                "max_connections": budget.max_connections,
                "max_ttl_seconds": budget.max_ttl_seconds,
            },
            "expected_namespace": {
                "namespace_id": str(authorization.expected_namespace_id),
                "network_namespace_identity": (authorization.expected_network_namespace_identity),
                "process_id": authorization.expected_namespace_process_id,
                "process_start_time_ticks": (
                    authorization.expected_namespace_process_start_time_ticks
                ),
                "runner_id": str(authorization.expected_runner_id),
            },
            "policy_digest": authorization.policy_digest,
            "request": request.binding_digest(),
            "service": {
                "network_fencing_token": service.network_fencing_token,
                "publisher_node_fencing_token": service.publisher_node_fencing_token,
                "publisher_node_id": str(service.publisher_node_id),
                "service_id": str(service.service_id),
                "service_version": service.service_version,
                "workspace_generation": service.workspace_generation,
            },
        }
    )


class WorkspaceNetworkPolicyEngine:
    """Pure binding, expiry, route and budget policy checks."""

    def verify(
        self,
        *,
        authorization: VerifiedSandboxNetworkAuthorization,
        namespace: VerifiedNetworkNamespace,
        destination: NetworkDestination,
        now: datetime,
    ) -> None:
        _require_aware(now, name="clock")
        self._verify_authorization(authorization=authorization, now=now)
        self._verify_service(authorization=authorization, now=now)
        self._verify_namespace(
            authorization=authorization,
            namespace=namespace,
            now=now,
        )
        self._verify_destination(
            authorization=authorization,
            destination=destination,
            now=now,
        )
        self._verify_deadline_and_budget(
            authorization=authorization,
            namespace=namespace,
            destination=destination,
            now=now,
        )

    @staticmethod
    def _verify_authorization(
        *,
        authorization: VerifiedSandboxNetworkAuthorization,
        now: datetime,
    ) -> None:
        request = authorization.request
        if request.direct_overlay:
            raise SandboxRejected("sandbox_direct_overlay_rejected")
        if authorization.revoked:
            raise SandboxRejected("sandbox_network_authorization_revoked")
        if authorization.verified_at > now or authorization.expires_at <= now:
            raise SandboxRejected("sandbox_network_authorization_expired")

    @staticmethod
    def _verify_service(
        *,
        authorization: VerifiedSandboxNetworkAuthorization,
        now: datetime,
    ) -> None:
        request = authorization.request
        service = authorization.service
        if request.logical_service_id not in authorization.allowed_service_ids:
            raise SandboxRejected("sandbox_network_service_not_allowed")
        service_binding = (
            service.tenant_id,
            service.workspace_id,
            service.service_id,
            service.workspace_generation,
            service.network_fencing_token,
            service.service_version,
            service.protocol,
            service.logical_port,
        )
        request_service_binding = (
            request.tenant_id,
            request.workspace_id,
            request.logical_service_id,
            request.workspace_generation,
            request.network_fencing_token,
            request.service_version,
            request.protocol,
            request.port,
        )
        if service_binding != request_service_binding:
            raise SandboxRejected("sandbox_network_service_binding_rejected")
        if not service.active or service.revoked_at is not None:
            raise SandboxRejected("sandbox_network_service_revoked")
        if service.expires_at <= now:
            raise SandboxRejected("sandbox_network_service_expired")

    @staticmethod
    def _verify_namespace(
        *,
        authorization: VerifiedSandboxNetworkAuthorization,
        namespace: VerifiedNetworkNamespace,
        now: datetime,
    ) -> None:
        request = authorization.request
        namespace_binding = (
            namespace.runner_id,
            namespace.namespace_id,
            namespace.network_namespace_identity,
            namespace.namespace_process_id,
            namespace.namespace_process_start_time_ticks,
            namespace.node_id,
            namespace.runtime_instance_id,
            namespace.workload_identity_thumbprint,
            namespace.workspace_generation,
            namespace.run_fencing_token,
            namespace.node_fencing_token,
            namespace.network_fencing_token,
            namespace.policy_digest,
        )
        request_namespace_binding = (
            authorization.expected_runner_id,
            authorization.expected_namespace_id,
            authorization.expected_network_namespace_identity,
            authorization.expected_namespace_process_id,
            authorization.expected_namespace_process_start_time_ticks,
            request.node_id,
            request.runtime_instance_id,
            request.workload_identity_thumbprint,
            request.workspace_generation,
            request.run_fencing_token,
            request.node_fencing_token,
            request.network_fencing_token,
            authorization.policy_digest,
        )
        if namespace_binding != request_namespace_binding or namespace.direct_overlay:
            raise SandboxRejected("sandbox_network_namespace_binding_rejected")
        if namespace.verified_at > now or namespace.expires_at <= now:
            raise SandboxRejected("sandbox_network_namespace_expired")

    @staticmethod
    def _verify_destination(
        *,
        authorization: VerifiedSandboxNetworkAuthorization,
        destination: NetworkDestination,
        now: datetime,
    ) -> None:
        request = authorization.request
        destination_binding = (
            destination.service_id,
            destination.protocol,
            destination.port,
        )
        if destination_binding != (
            request.logical_service_id,
            request.protocol,
            request.port,
        ):
            raise SandboxRejected("sandbox_network_destination_binding_rejected")
        if destination.resolved_at > now or destination.expires_at <= now:
            raise SandboxRejected("sandbox_network_destination_expired")
        validate_destination_address(destination)

    @staticmethod
    def _verify_deadline_and_budget(
        *,
        authorization: VerifiedSandboxNetworkAuthorization,
        namespace: VerifiedNetworkNamespace,
        destination: NetworkDestination,
        now: datetime,
    ) -> None:
        request = authorization.request
        service = authorization.service
        if request.deadline <= now:
            raise SandboxRejected("sandbox_network_deadline_expired")
        if request.deadline > min(
            authorization.expires_at,
            service.expires_at,
            namespace.expires_at,
            destination.expires_at,
        ):
            raise SandboxRejected("sandbox_network_deadline_exceeds_authorization")
        if (request.deadline - now).total_seconds() > authorization.budget.max_ttl_seconds:
            raise SandboxRejected("sandbox_network_ttl_budget_exceeded")
        if request.requested_connections > authorization.budget.max_connections:
            raise SandboxRejected("sandbox_network_connection_budget_exceeded")
        if request.requested_bytes_in > authorization.budget.max_bytes_in:
            raise SandboxRejected("sandbox_network_bytes_in_budget_exceeded")
        if request.requested_bytes_out > authorization.budget.max_bytes_out:
            raise SandboxRejected("sandbox_network_bytes_out_budget_exceeded")


class WorkspaceNetworkBroker(Protocol):
    def connect(
        self,
        request: SandboxNetworkAuthorizationRequest,
    ) -> BrokerConnectionReceipt: ...


class RejectingWorkspaceNetworkBroker:
    def connect(
        self,
        request: SandboxNetworkAuthorizationRequest,
    ) -> BrokerConnectionReceipt:
        del request
        raise SandboxUnavailable("workspace_network_broker_unavailable")


class ControlledWorkspaceNetworkBroker:
    """Fail-closed orchestration with no direct network implementation."""

    def __init__(
        self,
        *,
        authorizer: SandboxNetworkAuthorizer | None = None,
        namespace_attestor: NetworkNamespaceAttestor | None = None,
        resolver: WorkspaceServiceResolver | None = None,
        budget_ledger: NetworkBudgetLedger | None = None,
        transport: BrokerTransport | None = None,
        policy_engine: WorkspaceNetworkPolicyEngine | None = None,
        clock=utc_now,
    ) -> None:
        self._authorizer = authorizer or RejectingSandboxNetworkAuthorizer()
        self._namespace_attestor = namespace_attestor or RejectingNetworkNamespaceAttestor()
        self._resolver = resolver or RejectingWorkspaceServiceResolver()
        self._budget_ledger = budget_ledger or UnavailableNetworkBudgetLedger()
        self._transport = transport or UnavailableBrokerTransport()
        self._policy_engine = policy_engine or WorkspaceNetworkPolicyEngine()
        self._clock = clock

    def connect(
        self,
        request: SandboxNetworkAuthorizationRequest,
    ) -> BrokerConnectionReceipt:
        if not isinstance(request, SandboxNetworkAuthorizationRequest):
            raise TypeError("request must be SandboxNetworkAuthorizationRequest")
        authorization = self._authorizer.authorize(request)
        if authorization.request != request:
            raise SandboxRejected("sandbox_network_authorization_binding_rejected")
        first_namespace = self._namespace_attestor.attest(authorization=authorization)
        now = self._clock()
        first_destination = self._resolver.resolve(authorization=authorization)
        self._policy_engine.verify(
            authorization=authorization,
            namespace=first_namespace,
            destination=first_destination,
            now=now,
        )
        destination = self._resolver.resolve(authorization=authorization)
        now = self._clock()
        self._policy_engine.verify(
            authorization=authorization,
            namespace=first_namespace,
            destination=destination,
            now=now,
        )
        self._verify_resolution_stability(
            first_destination=first_destination,
            destination=destination,
        )
        # Re-read the live PID/netns proof immediately before the durable
        # reservation and potential transport side effect.  A changed process,
        # namespace inode or evidence file is never carried forward.
        namespace = self._namespace_attestor.attest(authorization=authorization)
        now = self._clock()
        self._policy_engine.verify(
            authorization=authorization,
            namespace=namespace,
            destination=destination,
            now=now,
        )
        self._verify_namespace_stability(
            first_namespace=first_namespace,
            namespace=namespace,
        )
        plan = self._build_plan(
            authorization=authorization,
            namespace=namespace,
            destination=destination,
        )
        reservation = self._budget_ledger.reserve(authorization=authorization)
        if reservation.replayed:
            assert reservation.receipt is not None
            self._verify_receipt(
                receipt=reservation.receipt,
                authorization=authorization,
                request=request,
                destination=destination,
                namespace=namespace,
                plan=plan,
                now=now,
                replayed=True,
            )
            return reservation.receipt
        try:
            dispatch_started_at = self._clock()
            receipt = self._transport.connect(
                plan=plan,
                namespace=namespace,
                destination=destination,
            )
            self._verify_receipt(
                receipt=receipt,
                authorization=authorization,
                request=request,
                destination=destination,
                namespace=namespace,
                plan=plan,
                now=self._clock(),
                dispatch_started_at=dispatch_started_at,
                replayed=False,
            )
            self._budget_ledger.commit(reservation=reservation, receipt=receipt)
        except Exception:
            # Once transport may have observed the request, automatic replay is
            # unsafe.  A process crash before this best-effort transition still
            # leaves the durable PENDING reservation, which is also treated as
            # outcome-unknown on recovery.
            self._budget_ledger.mark_unknown(reservation=reservation)
            raise
        return receipt

    @staticmethod
    def _verify_resolution_stability(
        *,
        first_destination: NetworkDestination,
        destination: NetworkDestination,
    ) -> None:
        """Reject any security-relevant drift between authorize/use resolution."""

        first_binding = (
            first_destination.service_id,
            first_destination.protocol,
            first_destination.port,
            first_destination.address,
            first_destination.route_kind,
            first_destination.resolution_digest,
        )
        destination_binding = (
            destination.service_id,
            destination.protocol,
            destination.port,
            destination.address,
            destination.route_kind,
            destination.resolution_digest,
        )
        if destination_binding != first_binding:
            raise SandboxRejected("sandbox_network_resolution_drift")

    @staticmethod
    def _verify_namespace_stability(
        *,
        first_namespace: VerifiedNetworkNamespace,
        namespace: VerifiedNetworkNamespace,
    ) -> None:
        if namespace != first_namespace:
            raise SandboxRejected("sandbox_network_namespace_drift")

    @staticmethod
    def _build_plan(
        *,
        authorization: VerifiedSandboxNetworkAuthorization,
        namespace: VerifiedNetworkNamespace,
        destination: NetworkDestination,
    ) -> BrokerConnectionPlan:
        request_digest = authorization.request.binding_digest()
        values = {
            "authorization": authorization.verification_digest,
            "destination": destination.resolution_digest,
            "namespace": namespace.evidence_digest,
            "operation_id": str(authorization.request.operation_id),
            "request": request_digest,
        }
        return BrokerConnectionPlan(
            operation_id=authorization.request.operation_id,
            request_binding_digest=request_digest,
            authorization_digest=authorization.verification_digest,
            namespace_evidence_digest=namespace.evidence_digest,
            destination_resolution_digest=destination.resolution_digest,
            plan_digest=stable_digest(values),
        )

    @staticmethod
    def _verify_receipt(
        *,
        receipt: BrokerConnectionReceipt,
        authorization: VerifiedSandboxNetworkAuthorization,
        request: SandboxNetworkAuthorizationRequest,
        destination: NetworkDestination,
        namespace: VerifiedNetworkNamespace,
        plan: BrokerConnectionPlan,
        now: datetime,
        replayed: bool,
        dispatch_started_at: datetime | None = None,
    ) -> None:
        if not isinstance(receipt, BrokerConnectionReceipt):
            raise SandboxRejected("sandbox_network_transport_receipt_rejected")
        if receipt.operation_id != request.operation_id:
            raise SandboxRejected("sandbox_network_transport_receipt_rejected")
        if receipt.request_binding_digest != request.binding_digest():
            raise SandboxRejected("sandbox_network_transport_receipt_rejected")
        if receipt.namespace_evidence_digest != namespace.evidence_digest:
            raise SandboxRejected("sandbox_network_transport_receipt_rejected")
        if receipt.destination_resolution_digest != destination.resolution_digest:
            raise SandboxRejected("sandbox_network_transport_receipt_rejected")
        if receipt.plan_digest != plan.plan_digest:
            raise SandboxRejected("sandbox_network_transport_receipt_rejected")
        if receipt.connections > request.requested_connections:
            raise SandboxRejected("sandbox_network_transport_receipt_rejected")
        if receipt.bytes_in > request.requested_bytes_in:
            raise SandboxRejected("sandbox_network_transport_receipt_rejected")
        if receipt.bytes_out > request.requested_bytes_out:
            raise SandboxRejected("sandbox_network_transport_receipt_rejected")
        ControlledWorkspaceNetworkBroker._verify_receipt_time(
            receipt=receipt,
            authorization=authorization,
            request=request,
            destination=destination,
            namespace=namespace,
            now=now,
            replayed=replayed,
            dispatch_started_at=dispatch_started_at,
        )

    @staticmethod
    def _verify_receipt_time(
        *,
        receipt: BrokerConnectionReceipt,
        authorization: VerifiedSandboxNetworkAuthorization,
        request: SandboxNetworkAuthorizationRequest,
        destination: NetworkDestination,
        namespace: VerifiedNetworkNamespace,
        now: datetime,
        replayed: bool,
        dispatch_started_at: datetime | None,
    ) -> None:
        latest_accepted_at = min(
            now,
            request.deadline,
            authorization.expires_at,
            authorization.service.expires_at,
            namespace.expires_at,
            destination.expires_at,
        )
        if receipt.accepted_at > latest_accepted_at:
            raise SandboxRejected("sandbox_network_transport_receipt_rejected")
        if not replayed and (
            dispatch_started_at is None
            or receipt.accepted_at
            < max(
                dispatch_started_at,
                authorization.verified_at,
                namespace.verified_at,
                destination.resolved_at,
            )
        ):
            raise SandboxRejected("sandbox_network_transport_receipt_rejected")


__all__ = [
    "BrokerConnectionPlan",
    "BrokerConnectionReceipt",
    "BrokerTransport",
    "ControlledWorkspaceNetworkBroker",
    "InMemoryNetworkBudgetLedger",
    "NetworkBudgetLedger",
    "NetworkBudgetOperationState",
    "NetworkBudgetReservation",
    "NetworkNamespaceAttestor",
    "RejectingNetworkNamespaceAttestor",
    "RejectingWorkspaceNetworkBroker",
    "UnavailableBrokerTransport",
    "UnavailableNetworkBudgetLedger",
    "VerifiedNetworkNamespace",
    "WorkspaceNetworkBroker",
    "WorkspaceNetworkPolicyEngine",
    "network_budget_binding_digest",
]
