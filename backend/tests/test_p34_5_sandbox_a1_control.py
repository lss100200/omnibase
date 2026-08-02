"""P34.5A1 authorization, emergency control and operation contract tests.

The tests are pure in-memory checks.  They do not start a process, create a
container, open a socket, read a workspace or contact a data service.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

import omnibase.sandbox.authorization as authorization_module
import omnibase.sandbox.control as control_module
import omnibase.sandbox.operations as operations_module
import omnibase.sandbox.runner as runner_module
from omnibase.sandbox import (
    ComposedSandboxAuthorizer,
    InMemorySandboxAuthorizer,
    InMemorySandboxControlAuthorizer,
    InMemorySandboxOperationStore,
    RejectingSandboxControlAuthorizer,
    RunnerIsolationProfile,
    RunnerPlatform,
    RunnerTerminationPlan,
    SandboxAction,
    SandboxConflict,
    SandboxControlAction,
    SandboxControlRequest,
    SandboxOperationIntent,
    SandboxOperationRequest,
    SandboxOperationState,
    SandboxRejected,
    SandboxRuntimeHandle,
    SandboxUnavailable,
    UnavailableSandboxRunner,
    VerifiedSandboxCapability,
    VerifiedSandboxLease,
)

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_DIGEST_C = "c" * 64
_WORKLOAD = "d" * 64
_CONTROLLER = "e" * 64


def _request(action: SandboxAction = SandboxAction.START) -> SandboxOperationRequest:
    return SandboxOperationRequest(
        operation_id=uuid4(),
        action=action,
        tenant_id=uuid4(),
        workspace_id=uuid4(),
        run_id=uuid4(),
        runtime_instance_id=uuid4(),
        capability_grant_id=uuid4(),
        node_id=uuid4(),
        lease_id=uuid4(),
        workspace_generation=7,
        run_fencing_token=11,
        node_fencing_token=13,
        workload_identity_thumbprint=_WORKLOAD,
    )


def _control_request(
    request: SandboxOperationRequest,
    *,
    now: datetime,
) -> SandboxControlRequest:
    return SandboxControlRequest(
        operation_id=uuid4(),
        action=SandboxControlAction.EMERGENCY_DESTROY,
        controller_id=uuid4(),
        controller_identity_thumbprint=_CONTROLLER,
        tenant_id=request.tenant_id,
        workspace_id=request.workspace_id,
        run_id=request.run_id,
        runtime_instance_id=request.runtime_instance_id,
        node_id=request.node_id,
        runtime_handle=SandboxRuntimeHandle(uuid4()),
        workspace_generation=request.workspace_generation,
        run_fencing_token=request.run_fencing_token,
        node_fencing_token=request.node_fencing_token,
        reason_code="workload_grant_revoked",
        deadline_at=now + timedelta(seconds=20),
    )


class _LeaseVerifier:
    def __init__(self, value: VerifiedSandboxLease) -> None:
        self.value = value

    def verify(self, request: SandboxOperationRequest) -> VerifiedSandboxLease:
        del request
        return self.value


class _CapabilityVerifier:
    def __init__(self, value: VerifiedSandboxCapability) -> None:
        self.value = value

    def verify(self, request: SandboxOperationRequest) -> VerifiedSandboxCapability:
        del request
        return self.value


def test_composed_authorizer_requires_matching_live_lease_and_capability() -> None:
    now = datetime(2026, 8, 1, 15, tzinfo=UTC)
    request = _request()
    lease = VerifiedSandboxLease(
        tenant_id=request.tenant_id,
        workspace_id=request.workspace_id,
        run_id=request.run_id,
        runtime_instance_id=request.runtime_instance_id,
        node_id=request.node_id,
        lease_id=request.lease_id,
        workspace_generation=request.workspace_generation,
        run_fencing_token=request.run_fencing_token,
        node_fencing_token=request.node_fencing_token,
        workload_identity_thumbprint=request.workload_identity_thumbprint,
        verified_at=now,
        expires_at=now + timedelta(seconds=30),
        verification_digest=_DIGEST_A,
    )
    capability = VerifiedSandboxCapability(
        grant_id=request.capability_grant_id,
        tenant_id=request.tenant_id,
        workspace_id=request.workspace_id,
        run_id=request.run_id,
        runtime_instance_id=request.runtime_instance_id,
        workload_identity_thumbprint=request.workload_identity_thumbprint,
        action=request.action,
        verified_at=now,
        expires_at=now + timedelta(seconds=20),
        verification_digest=_DIGEST_B,
    )
    authorizer = ComposedSandboxAuthorizer(
        lease_verifier=_LeaseVerifier(lease),
        capability_verifier=_CapabilityVerifier(capability),
        clock=lambda: now,
    )
    verified = authorizer.authorize(request)
    assert verified.expires_at == capability.expires_at
    assert verified.request == request

    with pytest.raises(SandboxRejected, match="sandbox_live_authorization_binding_rejected"):
        authorizer.authorize(replace(request, operation_id=uuid4(), node_fencing_token=14))

    with pytest.raises(SandboxUnavailable, match="sandbox_lease_verifier_unavailable"):
        ComposedSandboxAuthorizer(clock=lambda: now).authorize(request)


def test_emergency_control_is_independent_but_never_anonymous() -> None:
    now = datetime(2026, 8, 1, 15, tzinfo=UTC)
    request = _request()
    workload_authorizer = InMemorySandboxAuthorizer(clock=lambda: now)
    workload_authorizer.install(
        request=request,
        allowed_actions=frozenset({request.action}),
        expires_at=now + timedelta(minutes=1),
    )
    workload_authorizer.revoke(request.lease_id)
    with pytest.raises(SandboxRejected, match="sandbox_authorization_rejected"):
        workload_authorizer.authorize(request)

    control_request = _control_request(request, now=now)
    with pytest.raises(SandboxUnavailable, match="sandbox_control_authorizer_unavailable"):
        RejectingSandboxControlAuthorizer().authorize(control_request)

    control_authorizer = InMemorySandboxControlAuthorizer(clock=lambda: now)
    control_authorizer.install(
        request=control_request,
        expires_at=now + timedelta(seconds=10),
    )
    verified = control_authorizer.authorize(control_request)
    assert verified.request.runtime_handle == control_request.runtime_handle

    with pytest.raises(SandboxRejected, match="sandbox_control_authorization_rejected"):
        control_authorizer.authorize(
            replace(
                control_request,
                operation_id=uuid4(),
                workspace_generation=control_request.workspace_generation + 1,
            )
        )
    with pytest.raises(SandboxRejected, match="sandbox_control_authorization_rejected"):
        control_authorizer.authorize(
            replace(
                control_request,
                operation_id=uuid4(),
                node_fencing_token=control_request.node_fencing_token + 1,
            )
        )


def test_durable_operation_exact_replay_drift_and_ambiguous_reconciliation() -> None:
    now = datetime(2026, 8, 1, 15, tzinfo=UTC)
    store = InMemorySandboxOperationStore(clock=lambda: now)
    intent = SandboxOperationIntent(
        operation_id=uuid4(),
        tenant_id=uuid4(),
        workspace_id=uuid4(),
        run_id=uuid4(),
        runtime_instance_id=uuid4(),
        capability_grant_id=uuid4(),
        workspace_generation=7,
        run_fencing_token=11,
        node_fencing_token=13,
        action=SandboxAction.START.value,
        request_digest=_DIGEST_A,
        spec_digest=_DIGEST_B,
    )
    first = store.begin(intent)
    replay = store.begin(intent)
    assert replay == first
    assert replay.state is SandboxOperationState.ACCEPTED

    with pytest.raises(SandboxConflict, match="sandbox_operation_payload_drift"):
        store.begin(replace(intent, request_digest=_DIGEST_C))

    store.authorize(intent.operation_id, evidence_digest=_DIGEST_C)
    store.claim_dispatch(intent.operation_id)
    ambiguous = store.mark_ambiguous(intent.operation_id)
    assert ambiguous.state is SandboxOperationState.AMBIGUOUS
    with pytest.raises(SandboxConflict, match="sandbox_operation_transition_rejected"):
        store.claim_dispatch(intent.operation_id)
    store.require_reconciliation(intent.operation_id)
    terminal = store.reconcile(
        intent.operation_id,
        succeeded=False,
        evidence_digest=_DIGEST_A,
    )
    assert terminal.state is SandboxOperationState.RECONCILED_FAILED
    assert [item.sequence for item in terminal.transitions] == [1, 2, 3, 4, 5, 6]
    with pytest.raises(SandboxConflict, match="sandbox_operation_terminal"):
        store.reconcile(intent.operation_id, succeeded=True, evidence_digest=_DIGEST_B)


def test_runner_default_is_unavailable_after_valid_control_authorization() -> None:
    now = datetime(2026, 8, 1, 15, tzinfo=UTC)
    request = _request()
    control_request = _control_request(request, now=now)
    authorizer = InMemorySandboxControlAuthorizer(clock=lambda: now)
    authorizer.install(
        request=control_request,
        expires_at=now + timedelta(seconds=10),
    )
    verified = authorizer.authorize(control_request)
    intent = SandboxOperationIntent(
        operation_id=control_request.operation_id,
        tenant_id=control_request.tenant_id,
        workspace_id=control_request.workspace_id,
        run_id=control_request.run_id,
        runtime_instance_id=control_request.runtime_instance_id,
        capability_grant_id=None,
        workspace_generation=control_request.workspace_generation,
        run_fencing_token=control_request.run_fencing_token,
        node_fencing_token=control_request.node_fencing_token,
        action=control_request.action.value,
        request_digest=_DIGEST_A,
    )
    profile = RunnerIsolationProfile(
        platform=RunnerPlatform.LINUX,
        cgroup_v2=True,
        user_namespace=True,
        pid_namespace=True,
        mount_namespace=True,
        network_namespace=True,
        seccomp_profile_digest=_DIGEST_B,
        lsm_profile_digest=_DIGEST_C,
        bounded_kill_seconds=10,
    )
    plan = RunnerTerminationPlan(
        intent=intent,
        authorization=verified,
        isolation_profile=profile,
    )
    with pytest.raises(SandboxUnavailable, match="sandbox_runner_unavailable"):
        UnavailableSandboxRunner().terminate(plan)
    with pytest.raises(ValueError, match="cannot be disabled"):
        replace(profile, user_namespace=False)


def test_a1_sources_have_no_runtime_control_or_host_side_effect_imports() -> None:
    forbidden = {
        "docker",
        "httpx",
        "os",
        "pathlib",
        "requests",
        "socket",
        "subprocess",
    }
    for module in (
        authorization_module,
        control_module,
        operations_module,
        runner_module,
    ):
        tree = ast.parse(inspect.getsource(module))
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.partition(".")[0])
        assert imported_roots.isdisjoint(forbidden)
