from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

import omnibase.sandbox.coordinator as coordinator_module
import omnibase.sandbox.host as host_module
import omnibase.sandbox.transport as transport_module
from omnibase.sandbox import (
    InMemorySandboxOperationStore,
    RunnerExecutionPlan,
    RunnerIsolationProfile,
    RunnerOutcomeUnknown,
    RunnerPlatform,
    RunnerReceipt,
    SandboxAction,
    SandboxCommandSpec,
    SandboxConflict,
    SandboxDispatchResult,
    SandboxExecutionCoordinator,
    SandboxIsolationPolicy,
    SandboxNetworkPolicy,
    SandboxOperationIntent,
    SandboxOperationRequest,
    SandboxOperationState,
    SandboxRejected,
    SandboxRelativePath,
    SandboxResourceLimits,
    SandboxRuntimeHandle,
    SandboxRuntimeSpec,
    SandboxUnavailable,
    SqlAlchemySandboxLeaseVerifier,
    VerifiedRunnerHost,
    VerifiedSandboxAuthorization,
)
from omnibase.sandbox import authorization as authorization_module
from omnibase.workspaces.service import LeaseRejected

_A = "a" * 64
_B = "b" * 64
_C = "c" * 64
_D = "d" * 64
_E = "e" * 64
_F = "f" * 64


def _request() -> SandboxOperationRequest:
    return SandboxOperationRequest(
        operation_id=uuid4(),
        action=SandboxAction.EXEC,
        tenant_id=uuid4(),
        workspace_id=uuid4(),
        run_id=uuid4(),
        runtime_instance_id=uuid4(),
        capability_grant_id=uuid4(),
        node_id=uuid4(),
        lease_id=uuid4(),
        workspace_generation=3,
        run_fencing_token=5,
        node_fencing_token=7,
        workload_identity_thumbprint=_D,
    )


def _runtime_spec() -> SandboxRuntimeSpec:
    return SandboxRuntimeSpec(
        template_digest=_A,
        policy_digest=_B,
        limits=SandboxResourceLimits(
            cpu_millis=500,
            memory_bytes=128 * 1024 * 1024,
            pids=32,
            writable_bytes=32 * 1024 * 1024,
            inodes=4096,
            wall_time_seconds=30,
            output_bytes=64 * 1024,
        ),
        network=SandboxNetworkPolicy(),
        isolation=SandboxIsolationPolicy(run_as_uid=10000, run_as_gid=10000),
    )


def _profile() -> RunnerIsolationProfile:
    return RunnerIsolationProfile(
        platform=RunnerPlatform.LINUX,
        cgroup_v2=True,
        user_namespace=True,
        pid_namespace=True,
        mount_namespace=True,
        network_namespace=True,
        seccomp_profile_digest=_B,
        lsm_profile_digest=_C,
        bounded_kill_seconds=10,
    )


def _command() -> SandboxCommandSpec:
    return SandboxCommandSpec(
        argv=("python", "-I", "probe.py"),
        cwd=SandboxRelativePath("workspace"),
        timeout_seconds=10,
        max_output_bytes=4096,
    )


def _intent(request: SandboxOperationRequest) -> SandboxOperationIntent:
    return SandboxOperationIntent(
        operation_id=request.operation_id,
        tenant_id=request.tenant_id,
        workspace_id=request.workspace_id,
        run_id=request.run_id,
        runtime_instance_id=request.runtime_instance_id,
        capability_grant_id=request.capability_grant_id,
        workspace_generation=request.workspace_generation,
        run_fencing_token=request.run_fencing_token,
        node_fencing_token=request.node_fencing_token,
        action=request.action.value,
        request_digest=_A,
        spec_digest=_B,
    )


class _Authorizer:
    def __init__(self, request: SandboxOperationRequest, now: datetime) -> None:
        self.request = request
        self.now = now

    def authorize(self, request: SandboxOperationRequest) -> VerifiedSandboxAuthorization:
        if request != self.request:
            raise AssertionError("unexpected request")
        return VerifiedSandboxAuthorization(
            request=request,
            verified_at=self.now,
            expires_at=self.now + timedelta(seconds=30),
            verification_digest=_E,
        )


class _HostAttestor:
    def __init__(
        self,
        *,
        request: SandboxOperationRequest,
        profile: RunnerIsolationProfile,
        now: datetime,
    ) -> None:
        self.value = VerifiedRunnerHost(
            runner_id=uuid4(),
            node_id=request.node_id,
            node_fencing_token=request.node_fencing_token,
            runner_identity_thumbprint=_F,
            isolation_profile_digest=profile.digest(),
            verified_at=now,
            expires_at=now + timedelta(seconds=20),
            evidence_digest=_C,
        )

    def attest(
        self,
        *,
        request: SandboxOperationRequest,
        isolation_profile: RunnerIsolationProfile,
    ) -> VerifiedRunnerHost:
        del request, isolation_profile
        return self.value


class _RecordingTransport:
    def __init__(
        self,
        *,
        outcome_unknown: bool = False,
        mismatched_receipt: bool = False,
    ) -> None:
        self.calls: list[tuple[RunnerExecutionPlan, VerifiedRunnerHost]] = []
        self.outcome_unknown = outcome_unknown
        self.mismatched_receipt = mismatched_receipt

    def execute(
        self,
        *,
        plan: RunnerExecutionPlan,
        host: VerifiedRunnerHost,
    ) -> RunnerReceipt:
        self.calls.append((plan, host))
        if self.outcome_unknown:
            raise RunnerOutcomeUnknown("runner_transport_timeout")
        return RunnerReceipt(
            operation_id=uuid4() if self.mismatched_receipt else plan.intent.operation_id,
            evidence_digest=_F,
            reason_code="runner_probe_succeeded",
        )

    def terminate(self, *, plan, host):  # pragma: no cover - not in A2 execute slice
        raise AssertionError((plan, host))


def _coordinator(
    *,
    request: SandboxOperationRequest,
    profile: RunnerIsolationProfile,
    store: InMemorySandboxOperationStore,
    transport: _RecordingTransport,
    now: datetime,
) -> SandboxExecutionCoordinator:
    return SandboxExecutionCoordinator(
        operation_store=store,
        authorizer=_Authorizer(request, now),
        host_attestor=_HostAttestor(request=request, profile=profile, now=now),
        transport=transport,
        clock=lambda: now,
    )


def _execute(
    coordinator: SandboxExecutionCoordinator,
    *,
    request: SandboxOperationRequest,
    profile: RunnerIsolationProfile,
) -> SandboxDispatchResult:
    return coordinator.execute(
        intent=_intent(request),
        request=request,
        runtime_handle=SandboxRuntimeHandle(uuid4()),
        runtime_spec=_runtime_spec(),
        command=_command(),
        isolation_profile=profile,
    )


class _SessionContext:
    def __init__(self) -> None:
        self.entered = 0
        self.exited = 0
        self.transactions = 0

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        self.exited += 1

    def begin(self):
        owner = self

        class _Transaction:
            def __enter__(self):
                owner.transactions += 1
                return self

            def __exit__(self, exc_type, exc, traceback) -> None:
                del exc_type, exc, traceback

        return _Transaction()


def test_sqlalchemy_lease_verifier_uses_fresh_scoped_session_and_preserves_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    now = datetime(2026, 8, 1, 16, tzinfo=UTC)
    sessions: list[_SessionContext] = []

    def session_factory():
        session = _SessionContext()
        sessions.append(session)
        return session

    verify = MagicMock(
        return_value=SimpleNamespace(
            tenant_id=str(request.tenant_id),
            workspace_id=str(request.workspace_id),
            run_id=str(request.run_id),
            runtime_instance_id=str(request.runtime_instance_id),
            node_id=str(request.node_id),
            lease_id=str(request.lease_id),
            workspace_generation=request.workspace_generation,
            run_fencing_token=request.run_fencing_token,
            node_fencing_token=request.node_fencing_token,
            workload_identity_digest=request.workload_identity_thumbprint,
            verified_at=now,
            expires_at=now + timedelta(seconds=30),
            verification_digest=_A,
        )
    )
    monkeypatch.setattr(authorization_module, "verify_run_lease_for_sandbox", verify)
    verifier = SqlAlchemySandboxLeaseVerifier(session_factory=session_factory)

    first = verifier.verify(request)
    second = verifier.verify(request)

    assert first == second
    assert first.runtime_instance_id == request.runtime_instance_id
    assert first.node_fencing_token == request.node_fencing_token
    assert len(sessions) == 2
    assert all((item.entered, item.transactions, item.exited) == (1, 1, 1) for item in sessions)
    assert verify.call_count == 2
    assert verify.call_args.kwargs["runtime_instance_id"] == str(request.runtime_instance_id)


def test_sqlalchemy_lease_verifier_maps_rejection_and_still_closes_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionContext()
    monkeypatch.setattr(
        authorization_module,
        "verify_run_lease_for_sandbox",
        MagicMock(side_effect=LeaseRejected("stale")),
    )

    with pytest.raises(SandboxRejected, match="sandbox_live_lease_rejected"):
        SqlAlchemySandboxLeaseVerifier(session_factory=lambda: session).verify(_request())

    assert (session.entered, session.transactions, session.exited) == (1, 1, 1)


def test_a2_defaults_reject_before_runner_dispatch() -> None:
    request = _request()
    with pytest.raises(SandboxUnavailable, match="sandbox_operation_store_unavailable"):
        _execute(
            SandboxExecutionCoordinator(),
            request=request,
            profile=_profile(),
        )


@pytest.mark.parametrize(
    "drift",
    [
        {"tenant_id": uuid4()},
        {"workspace_id": uuid4()},
        {"run_id": uuid4()},
        {"runtime_instance_id": uuid4()},
        {"capability_grant_id": uuid4()},
        {"workspace_generation": 4},
        {"run_fencing_token": 6},
        {"node_fencing_token": 8},
    ],
)
def test_dispatch_rejects_durable_intent_binding_drift_before_store_or_transport(
    drift: dict[str, object],
) -> None:
    now = datetime(2026, 8, 1, 16, tzinfo=UTC)
    request = _request()
    profile = _profile()
    store = InMemorySandboxOperationStore(clock=lambda: now)
    transport = _RecordingTransport()
    coordinator = _coordinator(
        request=request,
        profile=profile,
        store=store,
        transport=transport,
        now=now,
    )
    with pytest.raises(ValueError, match="dispatch operation binding mismatch"):
        coordinator.execute(
            intent=replace(_intent(request), **drift),
            request=request,
            runtime_handle=SandboxRuntimeHandle(uuid4()),
            runtime_spec=_runtime_spec(),
            command=_command(),
            isolation_profile=profile,
        )
    with pytest.raises(SandboxConflict, match="sandbox_operation_not_found"):
        store.get(request.operation_id)
    assert transport.calls == []


def test_successful_dispatch_is_durable_and_exact_replay_does_not_redispatch() -> None:
    now = datetime(2026, 8, 1, 16, tzinfo=UTC)
    request = _request()
    profile = _profile()
    store = InMemorySandboxOperationStore(clock=lambda: now)
    transport = _RecordingTransport()
    coordinator = _coordinator(
        request=request,
        profile=profile,
        store=store,
        transport=transport,
        now=now,
    )

    result = _execute(coordinator, request=request, profile=profile)
    assert result.record.state is SandboxOperationState.SUCCEEDED
    assert result.receipt is not None
    assert result.replayed is False
    assert len(transport.calls) == 1

    replay = _execute(coordinator, request=request, profile=profile)
    assert replay.record == result.record
    assert replay.receipt is None
    assert replay.replayed is True
    assert len(transport.calls) == 1


def test_unknown_runner_outcome_becomes_ambiguous_and_never_auto_replays() -> None:
    now = datetime(2026, 8, 1, 16, tzinfo=UTC)
    request = _request()
    profile = _profile()
    store = InMemorySandboxOperationStore(clock=lambda: now)
    transport = _RecordingTransport(outcome_unknown=True)
    coordinator = _coordinator(
        request=request,
        profile=profile,
        store=store,
        transport=transport,
        now=now,
    )

    with pytest.raises(RunnerOutcomeUnknown, match="runner_transport_timeout"):
        _execute(coordinator, request=request, profile=profile)
    assert store.get(request.operation_id).state is SandboxOperationState.AMBIGUOUS
    assert len(transport.calls) == 1

    with pytest.raises(SandboxConflict, match="sandbox_operation_reconciliation_required"):
        _execute(coordinator, request=request, profile=profile)
    assert len(transport.calls) == 1


def test_mismatched_runner_receipt_is_ambiguous_not_success() -> None:
    now = datetime(2026, 8, 1, 16, tzinfo=UTC)
    request = _request()
    profile = _profile()
    store = InMemorySandboxOperationStore(clock=lambda: now)
    coordinator = _coordinator(
        request=request,
        profile=profile,
        store=store,
        transport=_RecordingTransport(mismatched_receipt=True),
        now=now,
    )

    with pytest.raises(RunnerOutcomeUnknown, match="sandbox_runner_receipt_binding_rejected"):
        _execute(coordinator, request=request, profile=profile)
    assert store.get(request.operation_id).state is SandboxOperationState.AMBIGUOUS


def test_stale_runner_host_fencing_fails_before_transport() -> None:
    now = datetime(2026, 8, 1, 16, tzinfo=UTC)
    request = _request()
    profile = _profile()
    store = InMemorySandboxOperationStore(clock=lambda: now)
    transport = _RecordingTransport()
    attestor = _HostAttestor(request=request, profile=profile, now=now)
    attestor.value = replace(
        attestor.value,
        node_fencing_token=request.node_fencing_token + 1,
    )
    coordinator = SandboxExecutionCoordinator(
        operation_store=store,
        authorizer=_Authorizer(request, now),
        host_attestor=attestor,
        transport=transport,
        clock=lambda: now,
    )

    with pytest.raises(SandboxRejected, match="sandbox_runner_host_attestation_rejected"):
        _execute(coordinator, request=request, profile=profile)
    assert store.get(request.operation_id).state is SandboxOperationState.FAILED
    assert transport.calls == []


def test_crash_after_dispatch_marker_is_forced_to_ambiguous() -> None:
    now = datetime(2026, 8, 1, 16, tzinfo=UTC)
    request = _request()
    profile = _profile()
    store = InMemorySandboxOperationStore(clock=lambda: now)
    intent = _intent(request)
    store.begin(intent)
    store.authorize(intent.operation_id, evidence_digest=_A)
    store.claim_dispatch(intent.operation_id)
    transport = _RecordingTransport()
    coordinator = _coordinator(
        request=request,
        profile=profile,
        store=store,
        transport=transport,
        now=now,
    )

    with pytest.raises(SandboxConflict, match="sandbox_operation_reconciliation_required"):
        _execute(coordinator, request=request, profile=profile)
    assert store.get(request.operation_id).state is SandboxOperationState.AMBIGUOUS
    assert transport.calls == []


def test_a2_core_sources_have_no_runtime_or_network_side_effect_imports() -> None:
    forbidden = {
        "docker",
        "httpx",
        "os",
        "pathlib",
        "requests",
        "socket",
        "subprocess",
    }
    for module in (coordinator_module, host_module, transport_module):
        tree = ast.parse(inspect.getsource(module))
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.partition(".")[0])
        assert imported_roots.isdisjoint(forbidden)
