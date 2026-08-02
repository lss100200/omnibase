"""Durability, concurrency and crash semantics for the P34.5B budget ledger."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from omnibase.sandbox.broker import (
    BrokerConnectionPlan,
    BrokerConnectionReceipt,
    ControlledWorkspaceNetworkBroker,
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
from omnibase.sandbox.network_ledger import SqliteNetworkBudgetLedger

NOW = datetime(2026, 8, 2, 16, 0, tzinfo=UTC)
TENANT_ID = UUID("30000000-0000-4000-8000-000000000001")
WORKSPACE_ID = UUID("30000000-0000-4000-8000-000000000002")
RUN_ID = UUID("30000000-0000-4000-8000-000000000003")
RUNTIME_ID = UUID("30000000-0000-4000-8000-000000000004")
NODE_ID = UUID("30000000-0000-4000-8000-000000000005")
LEASE_ID = UUID("30000000-0000-4000-8000-000000000006")
SERVICE_ID = UUID("30000000-0000-4000-8000-000000000007")
OPERATION_ID = UUID("30000000-0000-4000-8000-000000000008")
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64


def _request(
    operation_id: UUID = OPERATION_ID, **changes: object
) -> SandboxNetworkAuthorizationRequest:
    values: dict[str, object] = {
        "operation_id": operation_id,
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


def _authorization(
    operation_id: UUID = OPERATION_ID,
    *,
    budget: SandboxNetworkBudget | None = None,
    request_changes: dict[str, object] | None = None,
) -> VerifiedSandboxNetworkAuthorization:
    request = _request(operation_id, **(request_changes or {}))
    service = LogicalNetworkService(
        service_id=SERVICE_ID,
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        publisher_node_id=UUID("30000000-0000-4000-8000-00000000000b"),
        logical_name="workspace.gateway",
        protocol=NetworkProtocol.TCP,
        logical_port=8443,
        workspace_generation=3,
        publisher_node_fencing_token=17,
        network_fencing_token=11,
        service_version=13,
        expires_at=NOW + timedelta(seconds=60),
    )
    return VerifiedSandboxNetworkAuthorization(
        request=request,
        service=service,
        expected_runner_id=UUID("30000000-0000-4000-8000-00000000000d"),
        expected_namespace_id=UUID("30000000-0000-4000-8000-00000000000c"),
        expected_network_namespace_identity="2:4026533001",
        expected_namespace_process_id=4242,
        expected_namespace_process_start_time_ticks=12345,
        budget=budget or SandboxNetworkBudget(4, 8_192, 8_192, 60),
        allowed_service_ids=(SERVICE_ID,),
        policy_digest=DIGEST_B,
        verified_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=60),
        verification_digest=DIGEST_C,
    )


def _receipt(request: SandboxNetworkAuthorizationRequest) -> BrokerConnectionReceipt:
    return BrokerConnectionReceipt(
        operation_id=request.operation_id,
        request_binding_digest=request.binding_digest(),
        plan_digest=DIGEST_D,
        namespace_evidence_digest=DIGEST_C,
        destination_resolution_digest=DIGEST_A,
        connections=1,
        bytes_in=512,
        bytes_out=1_024,
        accepted_at=NOW,
    )


def _state_directory(tmp_path: Path) -> Path:
    state = tmp_path / "broker-state"
    state.mkdir(mode=0o700)
    state.chmod(0o700)
    return state


@pytest.mark.skipif(os.name != "posix", reason="hardened ledger is POSIX-only")
def test_durable_ledger_replays_exact_receipt_across_restart_and_rejects_drift(
    tmp_path: Path,
) -> None:
    database = _state_directory(tmp_path) / "network-budget.sqlite3"
    authorization = _authorization()
    first = SqliteNetworkBudgetLedger(database_path=database)
    reservation = first.reserve(authorization=authorization)
    receipt = _receipt(authorization.request)
    first.commit(reservation=reservation, receipt=receipt)

    restarted = SqliteNetworkBudgetLedger(database_path=database)
    replay = restarted.reserve(authorization=authorization)
    assert replay.replayed is True
    assert replay.receipt == receipt

    drifted = _authorization(request_changes={"requested_bytes_out": 2_049})
    with pytest.raises(SandboxConflict, match="binding_conflict"):
        restarted.reserve(authorization=drifted)


@pytest.mark.skipif(os.name != "posix", reason="hardened ledger is POSIX-only")
def test_pending_crash_and_explicit_unknown_survive_process_restart(tmp_path: Path) -> None:
    database = _state_directory(tmp_path) / "network-budget.sqlite3"
    operation_text = str(OPERATION_ID)
    code = f"""
from datetime import UTC,datetime,timedelta
from uuid import UUID
from omnibase.sandbox.network import *
from omnibase.sandbox.network_ledger import SqliteNetworkBudgetLedger
now=datetime(2026,8,2,16,0,tzinfo=UTC)
request=SandboxNetworkAuthorizationRequest(operation_id=UUID('{operation_text}'),tenant_id=UUID('{TENANT_ID}'),workspace_id=UUID('{WORKSPACE_ID}'),run_id=UUID('{RUN_ID}'),runtime_instance_id=UUID('{RUNTIME_ID}'),node_id=UUID('{NODE_ID}'),network_lease_id=UUID('{LEASE_ID}'),logical_service_id=UUID('{SERVICE_ID}'),workload_identity_thumbprint='{'a' * 64}',workspace_generation=3,run_fencing_token=5,node_fencing_token=7,network_fencing_token=11,service_version=13,protocol=NetworkProtocol.TCP,port=8443,requested_connections=1,requested_bytes_in=1024,requested_bytes_out=2048,deadline=now+timedelta(seconds=30))
service=LogicalNetworkService(service_id=UUID('{SERVICE_ID}'),tenant_id=UUID('{TENANT_ID}'),workspace_id=UUID('{WORKSPACE_ID}'),publisher_node_id=UUID('30000000-0000-4000-8000-00000000000b'),logical_name='workspace.gateway',protocol=NetworkProtocol.TCP,logical_port=8443,workspace_generation=3,publisher_node_fencing_token=17,network_fencing_token=11,service_version=13,expires_at=now+timedelta(seconds=60))
authorization=VerifiedSandboxNetworkAuthorization(request=request,service=service,expected_runner_id=UUID('30000000-0000-4000-8000-00000000000d'),expected_namespace_id=UUID('30000000-0000-4000-8000-00000000000c'),expected_network_namespace_identity='2:4026533001',expected_namespace_process_id=4242,expected_namespace_process_start_time_ticks=12345,budget=SandboxNetworkBudget(4,8192,8192,60),allowed_service_ids=(UUID('{SERVICE_ID}'),),policy_digest='{'b' * 64}',verified_at=now-timedelta(seconds=1),expires_at=now+timedelta(seconds=60),verification_digest='{'c' * 64}')
SqliteNetworkBudgetLedger(database_path=__import__('pathlib').Path(r'{database}')).reserve(authorization=authorization)
"""
    subprocess.run([sys.executable, "-c", code], check=True)

    recovered = SqliteNetworkBudgetLedger(database_path=database)
    with pytest.raises(SandboxConflict, match="outcome_unknown"):
        recovered.reserve(authorization=_authorization())

    second_id = UUID("30000000-0000-4000-8000-000000000009")
    second_auth = _authorization(second_id)
    reservation = recovered.reserve(authorization=second_auth)
    recovered.mark_unknown(reservation=reservation)
    restarted = SqliteNetworkBudgetLedger(database_path=database)
    with pytest.raises(SandboxConflict, match="outcome_unknown"):
        restarted.reserve(authorization=second_auth)


@pytest.mark.skipif(os.name != "posix", reason="hardened ledger is POSIX-only")
def test_begin_immediate_serializes_competing_budget_reservations(tmp_path: Path) -> None:
    database = _state_directory(tmp_path) / "network-budget.sqlite3"
    SqliteNetworkBudgetLedger(database_path=database)
    budget = SandboxNetworkBudget(1, 8_192, 8_192, 60)
    authorizations = (
        _authorization(UUID("30000000-0000-4000-8000-000000000010"), budget=budget),
        _authorization(UUID("30000000-0000-4000-8000-000000000011"), budget=budget),
    )

    def reserve(authorization: VerifiedSandboxNetworkAuthorization) -> str:
        ledger = SqliteNetworkBudgetLedger(database_path=database)
        try:
            ledger.reserve(authorization=authorization)
        except SandboxRejected:
            return "rejected"
        return "reserved"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(reserve, authorizations))
    assert sorted(outcomes) == ["rejected", "reserved"]


@pytest.mark.skipif(os.name != "posix", reason="hardened ledger is POSIX-only")
def test_sqlite_rows_are_append_only_and_path_swaps_fail_closed(tmp_path: Path) -> None:
    state = _state_directory(tmp_path)
    database = state / "network-budget.sqlite3"
    ledger = SqliteNetworkBudgetLedger(database_path=database)
    ledger.reserve(authorization=_authorization())

    connection = sqlite3.connect(database)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE network_budget_operations SET bytes_out = 1 WHERE operation_id = ?",
                (str(OPERATION_ID),),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            connection.execute(
                "DELETE FROM network_budget_operations WHERE operation_id = ?",
                (str(OPERATION_ID),),
            )
    finally:
        connection.close()

    trusted_copy = state / "trusted-copy.sqlite3"
    database.rename(trusted_copy)
    database.symlink_to(trusted_copy)
    with pytest.raises(SandboxUnavailable, match="file_rejected"):
        ledger.reserve(authorization=_authorization())


class _DynamicAuthorizer:
    def authorize(
        self,
        request: SandboxNetworkAuthorizationRequest,
    ) -> VerifiedSandboxNetworkAuthorization:
        return _authorization(request.operation_id)


class _Attestor:
    def attest(
        self,
        *,
        authorization: VerifiedSandboxNetworkAuthorization,
    ) -> VerifiedNetworkNamespace:
        request = authorization.request
        return VerifiedNetworkNamespace(
            namespace_id=UUID("30000000-0000-4000-8000-00000000000c"),
            network_namespace_identity="2:4026533001",
            namespace_process_id=4242,
            namespace_process_start_time_ticks=12345,
            runner_id=UUID("30000000-0000-4000-8000-00000000000d"),
            node_id=request.node_id,
            runtime_instance_id=request.runtime_instance_id,
            workload_identity_thumbprint=request.workload_identity_thumbprint,
            workspace_generation=request.workspace_generation,
            run_fencing_token=request.run_fencing_token,
            node_fencing_token=request.node_fencing_token,
            network_fencing_token=request.network_fencing_token,
            policy_digest=authorization.policy_digest,
            direct_overlay=False,
            verified_at=NOW - timedelta(seconds=1),
            expires_at=NOW + timedelta(seconds=60),
            evidence_digest=DIGEST_C,
        )


class _Resolver:
    def resolve(
        self,
        *,
        authorization: VerifiedSandboxNetworkAuthorization,
    ) -> NetworkDestination:
        return NetworkDestination.from_text(
            service_id=authorization.request.logical_service_id,
            protocol=NetworkProtocol.TCP,
            port=8443,
            address="8.8.8.8",
            route_kind=NetworkRouteKind.WORKSPACE_SERVICE,
            resolution_digest=DIGEST_A,
            resolved_at=NOW - timedelta(seconds=1),
            expires_at=NOW + timedelta(seconds=60),
        )


class _DropAfterSideEffectTransport:
    def __init__(self) -> None:
        self.calls = 0

    def connect(
        self,
        *,
        plan: BrokerConnectionPlan,
        namespace: VerifiedNetworkNamespace,
        destination: NetworkDestination,
    ) -> BrokerConnectionReceipt:
        del plan, namespace, destination
        self.calls += 1
        raise OSError("connection dropped after daemon accepted request")


class _ReceiptTransport:
    def __init__(self) -> None:
        self.calls = 0

    def connect(
        self,
        *,
        plan: BrokerConnectionPlan,
        namespace: VerifiedNetworkNamespace,
        destination: NetworkDestination,
    ) -> BrokerConnectionReceipt:
        self.calls += 1
        return BrokerConnectionReceipt(
            operation_id=plan.operation_id,
            request_binding_digest=plan.request_binding_digest,
            plan_digest=plan.plan_digest,
            namespace_evidence_digest=namespace.evidence_digest,
            destination_resolution_digest=destination.resolution_digest,
            connections=1,
            bytes_in=512,
            bytes_out=1_024,
            accepted_at=NOW,
        )


class _CommitFailureLedger:
    def __init__(self, inner: SqliteNetworkBudgetLedger, *, after_commit: bool) -> None:
        self._inner = inner
        self._after_commit = after_commit

    def reserve(self, *, authorization: VerifiedSandboxNetworkAuthorization):
        return self._inner.reserve(authorization=authorization)

    def commit(self, *, reservation, receipt) -> None:
        if self._after_commit:
            self._inner.commit(reservation=reservation, receipt=receipt)
        raise SandboxUnavailable("injected_network_budget_commit_failure")

    def mark_unknown(self, *, reservation) -> None:
        self._inner.mark_unknown(reservation=reservation)


@pytest.mark.skipif(os.name != "posix", reason="hardened ledger is POSIX-only")
def test_transport_ambiguity_is_durable_and_never_auto_replayed(tmp_path: Path) -> None:
    database = _state_directory(tmp_path) / "network-budget.sqlite3"
    transport = _DropAfterSideEffectTransport()
    broker = ControlledWorkspaceNetworkBroker(
        authorizer=_DynamicAuthorizer(),
        namespace_attestor=_Attestor(),
        resolver=_Resolver(),
        budget_ledger=SqliteNetworkBudgetLedger(database_path=database),
        transport=transport,
        clock=lambda: NOW,
    )
    with pytest.raises(OSError, match="dropped"):
        broker.connect(_request())

    restarted = ControlledWorkspaceNetworkBroker(
        authorizer=_DynamicAuthorizer(),
        namespace_attestor=_Attestor(),
        resolver=_Resolver(),
        budget_ledger=SqliteNetworkBudgetLedger(database_path=database),
        transport=transport,
        clock=lambda: NOW,
    )
    with pytest.raises(SandboxConflict, match="outcome_unknown"):
        restarted.connect(_request())
    assert transport.calls == 1


@pytest.mark.skipif(os.name != "posix", reason="hardened ledger is POSIX-only")
@pytest.mark.parametrize("after_commit", [False, True])
def test_commit_ambiguity_never_duplicates_transport(
    tmp_path: Path,
    after_commit: bool,
) -> None:
    database = _state_directory(tmp_path) / "network-budget.sqlite3"
    transport = _ReceiptTransport()
    broker = ControlledWorkspaceNetworkBroker(
        authorizer=_DynamicAuthorizer(),
        namespace_attestor=_Attestor(),
        resolver=_Resolver(),
        budget_ledger=_CommitFailureLedger(
            SqliteNetworkBudgetLedger(database_path=database),
            after_commit=after_commit,
        ),
        transport=transport,
        clock=lambda: NOW,
    )
    with pytest.raises(SandboxUnavailable, match="commit_failure"):
        broker.connect(_request())

    restarted = ControlledWorkspaceNetworkBroker(
        authorizer=_DynamicAuthorizer(),
        namespace_attestor=_Attestor(),
        resolver=_Resolver(),
        budget_ledger=SqliteNetworkBudgetLedger(database_path=database),
        transport=transport,
        clock=lambda: NOW,
    )
    if after_commit:
        assert restarted.connect(_request()).operation_id == OPERATION_ID
    else:
        with pytest.raises(SandboxConflict, match="outcome_unknown"):
            restarted.connect(_request())
    assert transport.calls == 1
