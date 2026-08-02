from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from omnibase.sandbox.broker import (
    BrokerConnectionPlan,
    BrokerConnectionReceipt,
    ControlledWorkspaceNetworkBroker,
    InMemoryNetworkBudgetLedger,
    RejectingWorkspaceNetworkBroker,
    VerifiedNetworkNamespace,
)
from omnibase.sandbox.contracts import SandboxConflict, SandboxRejected, SandboxUnavailable
from omnibase.sandbox.network import (
    LogicalNetworkService,
    NetworkDestination,
    NetworkProtocol,
    NetworkRouteKind,
    SandboxNetworkAuthorizationRequest,
    SandboxNetworkBudget,
    VerifiedSandboxNetworkAuthorization,
)

NOW = datetime(2026, 8, 2, 9, 0, tzinfo=UTC)
TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
WORKSPACE_ID = UUID("00000000-0000-0000-0000-000000000002")
RUN_ID = UUID("00000000-0000-0000-0000-000000000003")
RUNTIME_ID = UUID("00000000-0000-0000-0000-000000000004")
NODE_ID = UUID("00000000-0000-0000-0000-000000000005")
LEASE_ID = UUID("00000000-0000-0000-0000-000000000006")
SERVICE_ID = UUID("00000000-0000-0000-0000-000000000007")
OPERATION_ID = UUID("00000000-0000-0000-0000-000000000008")
NAMESPACE_ID = UUID("00000000-0000-0000-0000-000000000009")
RUNNER_ID = UUID("00000000-0000-0000-0000-00000000000a")
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64


def _request(**changes: object) -> SandboxNetworkAuthorizationRequest:
    values: dict[str, object] = {
        "operation_id": OPERATION_ID,
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "run_id": RUN_ID,
        "runtime_instance_id": RUNTIME_ID,
        "node_id": NODE_ID,
        "network_lease_id": LEASE_ID,
        "logical_service_id": SERVICE_ID,
        "workload_identity_thumbprint": DIGEST_A,
        "workspace_generation": 3,
        "run_fencing_token": 5,
        "node_fencing_token": 7,
        "network_fencing_token": 11,
        "service_version": 13,
        "protocol": NetworkProtocol.TCP,
        "port": 8443,
        "requested_connections": 1,
        "requested_bytes_in": 1_024,
        "requested_bytes_out": 2_048,
        "deadline": NOW + timedelta(seconds=30),
        "direct_overlay": False,
    }
    values.update(changes)
    return SandboxNetworkAuthorizationRequest(**values)  # type: ignore[arg-type]


def _service(**changes: object) -> LogicalNetworkService:
    values: dict[str, object] = {
        "service_id": SERVICE_ID,
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "publisher_node_id": UUID("00000000-0000-0000-0000-00000000000b"),
        "logical_name": "workspace.git",
        "protocol": NetworkProtocol.TCP,
        "logical_port": 8443,
        "workspace_generation": 3,
        "publisher_node_fencing_token": 17,
        "network_fencing_token": 11,
        "service_version": 13,
        "expires_at": NOW + timedelta(seconds=60),
        "active": True,
        "revoked_at": None,
    }
    values.update(changes)
    return LogicalNetworkService(**values)  # type: ignore[arg-type]


def _authorization(
    *,
    request: SandboxNetworkAuthorizationRequest | None = None,
    service: LogicalNetworkService | None = None,
    budget: SandboxNetworkBudget | None = None,
    **changes: object,
) -> VerifiedSandboxNetworkAuthorization:
    values: dict[str, object] = {
        "request": request or _request(),
        "service": service or _service(),
        "expected_runner_id": RUNNER_ID,
        "expected_namespace_id": NAMESPACE_ID,
        "expected_network_namespace_identity": "2:4026533001",
        "expected_namespace_process_id": 4242,
        "expected_namespace_process_start_time_ticks": 12345,
        "budget": budget
        or SandboxNetworkBudget(
            max_connections=4,
            max_bytes_in=8_192,
            max_bytes_out=8_192,
            max_ttl_seconds=60,
        ),
        "allowed_service_ids": (SERVICE_ID,),
        "policy_digest": DIGEST_B,
        "verified_at": NOW - timedelta(seconds=1),
        "expires_at": NOW + timedelta(seconds=60),
        "verification_digest": DIGEST_C,
        "revoked": False,
    }
    values.update(changes)
    return VerifiedSandboxNetworkAuthorization(**values)  # type: ignore[arg-type]


def _namespace(**changes: object) -> VerifiedNetworkNamespace:
    values: dict[str, object] = {
        "namespace_id": NAMESPACE_ID,
        "network_namespace_identity": "2:4026533001",
        "namespace_process_id": 4242,
        "namespace_process_start_time_ticks": 12345,
        "runner_id": RUNNER_ID,
        "node_id": NODE_ID,
        "runtime_instance_id": RUNTIME_ID,
        "workload_identity_thumbprint": DIGEST_A,
        "workspace_generation": 3,
        "run_fencing_token": 5,
        "node_fencing_token": 7,
        "network_fencing_token": 11,
        "policy_digest": DIGEST_B,
        "direct_overlay": False,
        "verified_at": NOW - timedelta(seconds=1),
        "expires_at": NOW + timedelta(seconds=60),
        "evidence_digest": DIGEST_D,
    }
    values.update(changes)
    return VerifiedNetworkNamespace(**values)  # type: ignore[arg-type]


def _destination(
    address: str = "8.8.8.8",
    **changes: object,
) -> NetworkDestination:
    values: dict[str, object] = {
        "service_id": SERVICE_ID,
        "protocol": NetworkProtocol.TCP,
        "port": 8443,
        "address": address,
        "route_kind": NetworkRouteKind.WORKSPACE_SERVICE,
        "resolution_digest": DIGEST_A,
        "resolved_at": NOW - timedelta(seconds=1),
        "expires_at": NOW + timedelta(seconds=60),
    }
    values.update(changes)
    return NetworkDestination.from_text(**values)  # type: ignore[arg-type]


class FixedAuthorizer:
    def __init__(self, authorization: VerifiedSandboxNetworkAuthorization) -> None:
        self.authorization = authorization
        self.calls = 0

    def authorize(
        self,
        request: SandboxNetworkAuthorizationRequest,
    ) -> VerifiedSandboxNetworkAuthorization:
        del request
        self.calls += 1
        return self.authorization


class DynamicAuthorizer:
    def __init__(self, *, budget: SandboxNetworkBudget | None = None) -> None:
        self.budget = budget

    def authorize(
        self,
        request: SandboxNetworkAuthorizationRequest,
    ) -> VerifiedSandboxNetworkAuthorization:
        service = _service(
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            service_id=request.logical_service_id,
            protocol=request.protocol,
            logical_port=request.port,
            workspace_generation=request.workspace_generation,
            network_fencing_token=request.network_fencing_token,
            service_version=request.service_version,
        )
        return _authorization(request=request, service=service, budget=self.budget)


class FixedAttestor:
    def __init__(self, namespace: VerifiedNetworkNamespace) -> None:
        self.namespace = namespace
        self.calls = 0

    def attest(
        self,
        *,
        authorization: VerifiedSandboxNetworkAuthorization,
    ) -> VerifiedNetworkNamespace:
        del authorization
        self.calls += 1
        return self.namespace


class DynamicAttestor:
    def attest(
        self,
        *,
        authorization: VerifiedSandboxNetworkAuthorization,
    ) -> VerifiedNetworkNamespace:
        request = authorization.request
        return _namespace(
            node_id=request.node_id,
            runtime_instance_id=request.runtime_instance_id,
            workload_identity_thumbprint=request.workload_identity_thumbprint,
            workspace_generation=request.workspace_generation,
            run_fencing_token=request.run_fencing_token,
            node_fencing_token=request.node_fencing_token,
            network_fencing_token=request.network_fencing_token,
            policy_digest=authorization.policy_digest,
        )


class SequenceResolver:
    def __init__(self, *destinations: NetworkDestination) -> None:
        self.destinations = list(destinations)
        self.calls = 0

    def resolve(
        self,
        *,
        authorization: VerifiedSandboxNetworkAuthorization,
    ) -> NetworkDestination:
        del authorization
        index = min(self.calls, len(self.destinations) - 1)
        self.calls += 1
        return self.destinations[index]


class DynamicResolver:
    def resolve(
        self,
        *,
        authorization: VerifiedSandboxNetworkAuthorization,
    ) -> NetworkDestination:
        request = authorization.request
        return _destination(
            service_id=request.logical_service_id,
            protocol=request.protocol,
            port=request.port,
        )


class RecordingTransport:
    def __init__(self) -> None:
        self.calls = 0
        self.mutate: dict[str, object] = {}

    def connect(
        self,
        *,
        plan: BrokerConnectionPlan,
        namespace: VerifiedNetworkNamespace,
        destination: NetworkDestination,
    ) -> BrokerConnectionReceipt:
        del destination
        self.calls += 1
        values: dict[str, object] = {
            "operation_id": plan.operation_id,
            "request_binding_digest": plan.request_binding_digest,
            "plan_digest": plan.plan_digest,
            "namespace_evidence_digest": namespace.evidence_digest,
            "destination_resolution_digest": plan.destination_resolution_digest,
            "connections": 1,
            "bytes_in": 512,
            "bytes_out": 1_024,
            "accepted_at": NOW,
        }
        values.update(self.mutate)
        return BrokerConnectionReceipt(**values)  # type: ignore[arg-type]


def _broker(
    *,
    authorization: VerifiedSandboxNetworkAuthorization | None = None,
    namespace: VerifiedNetworkNamespace | None = None,
    resolver: object | None = None,
    ledger: InMemoryNetworkBudgetLedger | None = None,
    transport: RecordingTransport | None = None,
) -> tuple[ControlledWorkspaceNetworkBroker, RecordingTransport]:
    selected_transport = transport or RecordingTransport()
    return (
        ControlledWorkspaceNetworkBroker(
            authorizer=FixedAuthorizer(authorization or _authorization()),
            namespace_attestor=FixedAttestor(namespace or _namespace()),
            resolver=resolver or SequenceResolver(_destination()),  # type: ignore[arg-type]
            budget_ledger=ledger or InMemoryNetworkBudgetLedger(),
            transport=selected_transport,
            clock=lambda: NOW,
        ),
        selected_transport,
    )


def test_production_defaults_reject_without_transport() -> None:
    request = _request()
    with pytest.raises(SandboxUnavailable, match="workspace_network_broker_unavailable"):
        RejectingWorkspaceNetworkBroker().connect(request)
    with pytest.raises(SandboxUnavailable, match="sandbox_network_authorizer_unavailable"):
        ControlledWorkspaceNetworkBroker().connect(request)


def test_valid_logical_service_connects_and_exact_replay_does_not_charge_twice() -> None:
    resolver = SequenceResolver(_destination())
    broker, transport = _broker(resolver=resolver)

    first = broker.connect(_request())
    replay = broker.connect(_request())

    assert replay == first
    assert transport.calls == 1
    assert resolver.calls == 4


@pytest.mark.parametrize(
    "changed_authorization",
    [
        _authorization(verification_digest=DIGEST_D),
        _authorization(
            service=_service(publisher_node_id=UUID("10000000-0000-0000-0000-00000000000b"))
        ),
        _authorization(service=_service(publisher_node_fencing_token=18)),
    ],
)
def test_committed_replay_rejects_authorization_or_publisher_drift(
    changed_authorization: VerifiedSandboxNetworkAuthorization,
) -> None:
    ledger = InMemoryNetworkBudgetLedger()
    transport = RecordingTransport()
    first, _ = _broker(ledger=ledger, transport=transport)
    first.connect(_request())

    replay, _ = _broker(
        authorization=changed_authorization,
        ledger=ledger,
        transport=transport,
    )
    with pytest.raises(SandboxConflict, match="binding_conflict"):
        replay.connect(_request())
    assert transport.calls == 1


def test_committed_replay_rebuilds_and_requires_current_plan_digest() -> None:
    ledger = InMemoryNetworkBudgetLedger()
    transport = RecordingTransport()
    first, _ = _broker(ledger=ledger, transport=transport)
    first.connect(_request())

    replay, _ = _broker(
        namespace=_namespace(evidence_digest="e" * 64),
        ledger=ledger,
        transport=transport,
    )
    with pytest.raises(SandboxRejected, match="transport_receipt_rejected"):
        replay.connect(_request())
    assert transport.calls == 1


@pytest.mark.parametrize(
    "second_destination",
    [
        _destination(address="8.8.4.4"),
        _destination(resolution_digest=DIGEST_B),
    ],
)
def test_safe_destination_drift_is_rejected_before_budget_or_transport(
    second_destination: NetworkDestination,
) -> None:
    resolver = SequenceResolver(_destination(), second_destination)
    broker, transport = _broker(resolver=resolver)

    with pytest.raises(SandboxRejected, match="sandbox_network_resolution_drift"):
        broker.connect(_request())

    assert resolver.calls == 2
    assert transport.calls == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"tenant_id": UUID("10000000-0000-0000-0000-000000000001")},
        {"workspace_id": UUID("10000000-0000-0000-0000-000000000002")},
        {"run_id": UUID("10000000-0000-0000-0000-000000000003")},
        {"runtime_instance_id": UUID("10000000-0000-0000-0000-000000000004")},
        {"workload_identity_thumbprint": DIGEST_D},
        {"workspace_generation": 4},
        {"run_fencing_token": 6},
        {"node_fencing_token": 8},
        {"network_fencing_token": 12},
    ],
)
def test_live_authorization_binding_drift_is_rejected(changes: dict[str, object]) -> None:
    broker, transport = _broker()

    with pytest.raises(SandboxRejected, match="sandbox_network_authorization_binding_rejected"):
        broker.connect(_request(**changes))

    assert transport.calls == 0


def test_direct_overlay_request_is_rejected() -> None:
    request = _request(direct_overlay=True)
    authorization = _authorization(request=request)
    broker, transport = _broker(authorization=authorization)

    with pytest.raises(SandboxRejected, match="sandbox_direct_overlay_rejected"):
        broker.connect(request)

    assert transport.calls == 0


def test_logical_service_not_in_authorized_allowlist_is_rejected() -> None:
    authorization = _authorization(
        allowed_service_ids=(UUID("10000000-0000-0000-0000-000000000007"),)
    )
    broker, transport = _broker(authorization=authorization)

    with pytest.raises(SandboxRejected, match="sandbox_network_service_not_allowed"):
        broker.connect(_request())

    assert transport.calls == 0


@pytest.mark.parametrize(
    ("service_changes", "code"),
    [
        ({"tenant_id": UUID("10000000-0000-0000-0000-000000000001")}, "binding"),
        ({"workspace_id": UUID("10000000-0000-0000-0000-000000000002")}, "binding"),
        ({"service_id": UUID("10000000-0000-0000-0000-000000000007")}, "binding"),
        ({"workspace_generation": 4}, "binding"),
        ({"network_fencing_token": 12}, "binding"),
        ({"service_version": 14}, "binding"),
        ({"protocol": NetworkProtocol.UDP}, "binding"),
        ({"logical_port": 9443}, "binding"),
        ({"active": False}, "revoked"),
        ({"revoked_at": NOW}, "revoked"),
        ({"expires_at": NOW}, "expired"),
    ],
)
def test_stale_or_revoked_service_is_rejected(
    service_changes: dict[str, object],
    code: str,
) -> None:
    authorization = _authorization(service=_service(**service_changes))
    broker, transport = _broker(authorization=authorization)

    with pytest.raises(SandboxRejected, match=f"sandbox_network_service_{code}"):
        broker.connect(_request())

    assert transport.calls == 0


@pytest.mark.parametrize(
    ("authorization_changes", "code"),
    [
        ({"revoked": True}, "revoked"),
        ({"expires_at": NOW}, "expired"),
        ({"verified_at": NOW + timedelta(seconds=1)}, "expired"),
    ],
)
def test_revoked_or_expired_authorization_is_rejected(
    authorization_changes: dict[str, object],
    code: str,
) -> None:
    authorization = _authorization(**authorization_changes)
    broker, transport = _broker(authorization=authorization)

    with pytest.raises(SandboxRejected, match=f"sandbox_network_authorization_{code}"):
        broker.connect(_request())

    assert transport.calls == 0


@pytest.mark.parametrize(
    "namespace_changes",
    [
        {"runner_id": UUID("10000000-0000-0000-0000-00000000000a")},
        {"namespace_id": UUID("10000000-0000-0000-0000-000000000009")},
        {"network_namespace_identity": "3:4026533002"},
        {"namespace_process_id": 4343},
        {"namespace_process_start_time_ticks": 12346},
        {"runtime_instance_id": UUID("10000000-0000-0000-0000-000000000004")},
        {"node_id": UUID("10000000-0000-0000-0000-000000000005")},
        {"workload_identity_thumbprint": DIGEST_D},
        {"workspace_generation": 4},
        {"run_fencing_token": 6},
        {"node_fencing_token": 8},
        {"network_fencing_token": 12},
        {"policy_digest": DIGEST_C},
        {"direct_overlay": True},
    ],
)
def test_namespace_binding_drift_is_rejected(namespace_changes: dict[str, object]) -> None:
    broker, transport = _broker(namespace=_namespace(**namespace_changes))

    with pytest.raises(SandboxRejected, match="sandbox_network_namespace_binding_rejected"):
        broker.connect(_request())

    assert transport.calls == 0


@pytest.mark.parametrize(
    "namespace_changes",
    [
        {"expires_at": NOW},
        {"verified_at": NOW + timedelta(seconds=1)},
    ],
)
def test_expired_namespace_proof_is_rejected(namespace_changes: dict[str, object]) -> None:
    broker, transport = _broker(namespace=_namespace(**namespace_changes))

    with pytest.raises(SandboxRejected, match="sandbox_network_namespace_expired"):
        broker.connect(_request())

    assert transport.calls == 0


@pytest.mark.parametrize(
    "address",
    [
        "0.0.0.0",
        "::",
        "127.0.0.1",
        "::1",
        "169.254.169.254",
        "169.254.1.1",
        "fe80::1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "fd00:ec2::254",
        "fc00::1",
        "224.0.0.1",
        "ff02::1",
        "255.255.255.255",
        "192.0.2.1",
    ],
)
def test_unsafe_address_classes_are_rejected(address: str) -> None:
    broker, transport = _broker(resolver=SequenceResolver(_destination(address)))

    with pytest.raises(SandboxRejected, match="sandbox_network_destination_rejected"):
        broker.connect(_request())

    assert transport.calls == 0


@pytest.mark.parametrize(
    ("route_kind", "code"),
    [
        (NetworkRouteKind.MEMBER_OVERLAY, "sandbox_direct_overlay_rejected"),
        (NetworkRouteKind.PUBLIC_INTERNET, "sandbox_direct_public_internet_rejected"),
    ],
)
def test_direct_member_overlay_or_public_internet_route_is_rejected(
    route_kind: NetworkRouteKind,
    code: str,
) -> None:
    broker, transport = _broker(resolver=SequenceResolver(_destination(route_kind=route_kind)))

    with pytest.raises(SandboxRejected, match=code):
        broker.connect(_request())

    assert transport.calls == 0


def test_dns_rebinding_to_metadata_is_rejected_before_transport() -> None:
    resolver = SequenceResolver(_destination("8.8.8.8"), _destination("169.254.169.254"))
    broker, transport = _broker(resolver=resolver)

    with pytest.raises(SandboxRejected, match="sandbox_network_destination_rejected"):
        broker.connect(_request())

    assert resolver.calls == 2
    assert transport.calls == 0


@pytest.mark.parametrize(
    ("request_changes", "budget", "code"),
    [
        (
            {"requested_connections": 2},
            SandboxNetworkBudget(1, 8_192, 8_192, 60),
            "sandbox_network_connection_budget_exceeded",
        ),
        (
            {"requested_bytes_in": 2_048},
            SandboxNetworkBudget(4, 1_024, 8_192, 60),
            "sandbox_network_bytes_in_budget_exceeded",
        ),
        (
            {"requested_bytes_out": 2_048},
            SandboxNetworkBudget(4, 8_192, 1_024, 60),
            "sandbox_network_bytes_out_budget_exceeded",
        ),
        (
            {"deadline": NOW + timedelta(seconds=30)},
            SandboxNetworkBudget(4, 8_192, 8_192, 10),
            "sandbox_network_ttl_budget_exceeded",
        ),
    ],
)
def test_per_operation_budget_is_enforced(
    request_changes: dict[str, object],
    budget: SandboxNetworkBudget,
    code: str,
) -> None:
    request = _request(**request_changes)
    authorization = _authorization(request=request, budget=budget)
    broker, transport = _broker(authorization=authorization)

    with pytest.raises(SandboxRejected, match=code):
        broker.connect(request)

    assert transport.calls == 0


def test_aggregate_connection_budget_is_enforced_across_operations() -> None:
    budget = SandboxNetworkBudget(1, 8_192, 8_192, 60)
    transport = RecordingTransport()
    broker = ControlledWorkspaceNetworkBroker(
        authorizer=DynamicAuthorizer(budget=budget),
        namespace_attestor=DynamicAttestor(),
        resolver=DynamicResolver(),
        budget_ledger=InMemoryNetworkBudgetLedger(),
        transport=transport,
        clock=lambda: NOW,
    )
    broker.connect(_request())

    with pytest.raises(SandboxRejected, match="sandbox_network_connection_budget_exceeded"):
        broker.connect(_request(operation_id=UUID("10000000-0000-0000-0000-000000000008")))

    assert transport.calls == 1


def test_same_operation_id_with_different_binding_is_conflict() -> None:
    transport = RecordingTransport()
    broker = ControlledWorkspaceNetworkBroker(
        authorizer=DynamicAuthorizer(),
        namespace_attestor=DynamicAttestor(),
        resolver=DynamicResolver(),
        budget_ledger=InMemoryNetworkBudgetLedger(),
        transport=transport,
        clock=lambda: NOW,
    )
    broker.connect(_request())

    with pytest.raises(SandboxConflict, match="sandbox_network_operation_binding_conflict"):
        broker.connect(_request(runtime_instance_id=UUID("10000000-0000-0000-0000-000000000004")))

    assert transport.calls == 1


@pytest.mark.parametrize(
    "receipt_changes",
    [
        {"operation_id": UUID("10000000-0000-0000-0000-000000000008")},
        {"request_binding_digest": DIGEST_D},
        {"plan_digest": DIGEST_D},
        {"namespace_evidence_digest": DIGEST_C},
        {"destination_resolution_digest": DIGEST_D},
        {"connections": 2},
        {"bytes_in": 2_048},
        {"bytes_out": 4_096},
        {"accepted_at": NOW + timedelta(microseconds=1)},
        {"accepted_at": NOW - timedelta(seconds=2)},
    ],
)
def test_transport_receipt_binding_drift_is_rejected(
    receipt_changes: dict[str, object],
) -> None:
    transport = RecordingTransport()
    transport.mutate = receipt_changes
    broker, _ = _broker(transport=transport)

    with pytest.raises(SandboxRejected, match="sandbox_network_transport_receipt_rejected"):
        broker.connect(_request())

    assert transport.calls == 1


def test_destination_binding_or_expiry_is_rejected_without_fallback() -> None:
    destinations = [
        _destination(service_id=UUID("10000000-0000-0000-0000-000000000007")),
        _destination(protocol=NetworkProtocol.UDP),
        _destination(port=9443),
        _destination(expires_at=NOW),
    ]
    for destination in destinations:
        broker, transport = _broker(resolver=SequenceResolver(destination))
        with pytest.raises(SandboxRejected):
            broker.connect(_request())
        assert transport.calls == 0
