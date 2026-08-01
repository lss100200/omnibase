from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

import omnibase.sandbox.transport_auth as auth_module
import omnibase.sandbox.transport_service as service_module
from omnibase.sandbox.contracts import (
    SandboxAction,
    SandboxCommandSpec,
    SandboxIsolationPolicy,
    SandboxNetworkPolicy,
    SandboxOperationRequest,
    SandboxRejected,
    SandboxRelativePath,
    SandboxResourceLimits,
    SandboxRuntimeHandle,
    SandboxRuntimeSpec,
    SandboxUnavailable,
    VerifiedSandboxAuthorization,
)
from omnibase.sandbox.host import VerifiedRunnerHost
from omnibase.sandbox.operations import SandboxOperationIntent
from omnibase.sandbox.runner import (
    RunnerExecutionPlan,
    RunnerIsolationProfile,
    RunnerPlatform,
    RunnerReceipt,
)
from omnibase.sandbox.runtime_driver import execution_binding_digest
from omnibase.sandbox.transport import RunnerOutcomeUnknown
from omnibase.sandbox.transport_auth import (
    HmacRunnerTransportAuthenticator,
    MtlsRunnerTransportAuthenticator,
    RunnerTransportEnvelope,
    SqliteRunnerReplayStore,
    TrustedRunnerMtlsPeer,
)
from omnibase.sandbox.transport_service import (
    AuthenticatedLocalRunnerTransport,
    AuthenticatedRunnerService,
)

_A = "a" * 64
_B = "b" * 64
_C = "c" * 64
_D = "d" * 64
_E = "e" * 64
_F = "f" * 64
_SECRET = b"local-dev-runner-auth-key-32bytes!!"


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
        bounded_kill_seconds=5,
    )


def _plan(now: datetime) -> tuple[RunnerExecutionPlan, VerifiedRunnerHost]:
    request = SandboxOperationRequest(
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
    plan = RunnerExecutionPlan(
        intent=SandboxOperationIntent(
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
        ),
        request=request,
        authorization=VerifiedSandboxAuthorization(
            request=request,
            verified_at=now,
            expires_at=now + timedelta(seconds=30),
            verification_digest=_E,
        ),
        runtime_handle=SandboxRuntimeHandle(uuid4()),
        runtime_spec=SandboxRuntimeSpec(
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
            isolation=SandboxIsolationPolicy(run_as_uid=10_000, run_as_gid=10_000),
        ),
        command=SandboxCommandSpec(
            argv=("python", "-I", "probe.py"),
            cwd=SandboxRelativePath("workspace"),
            timeout_seconds=10,
            max_output_bytes=4096,
        ),
        isolation_profile=_profile(),
    )
    host = VerifiedRunnerHost(
        runner_id=uuid4(),
        node_id=request.node_id,
        node_fencing_token=request.node_fencing_token,
        runner_identity_thumbprint=_F,
        isolation_profile_digest=plan.isolation_profile.digest(),
        verified_at=now,
        expires_at=now + timedelta(seconds=20),
        evidence_digest=_C,
    )
    return plan, host


class _BoundRunner:
    def __init__(self, *, mismatch: bool = False) -> None:
        self.mismatch = mismatch
        self.calls = 0

    def execute(self, *, plan: RunnerExecutionPlan, host: VerifiedRunnerHost) -> RunnerReceipt:
        self.calls += 1
        return RunnerReceipt(
            operation_id=plan.request.operation_id,
            evidence_digest=_E,
            reason_code="runner_execution_succeeded",
            binding_digest=_F if self.mismatch else execution_binding_digest(plan, host),
            runner_id=host.runner_id,
            runtime_instance_id=plan.request.runtime_instance_id,
            exit_code=0,
        )

    def terminate(self, *, plan, host):  # pragma: no cover - separate control slice
        raise AssertionError((plan, host))


def test_hmac_transport_authentication_and_replay_protection() -> None:
    now = datetime(2026, 8, 2, tzinfo=UTC)
    plan, host = _plan(now)
    binding = execution_binding_digest(plan, host)
    auth = HmacRunnerTransportAuthenticator(
        key_id="local.a4",
        secret=_SECRET,
        clock=lambda: now,
    )
    envelope = auth.sign(
        runner_id=host.runner_id,
        node_id=host.node_id,
        operation_id=plan.request.operation_id,
        action=plan.request.action.value,
        payload_digest=binding,
        host_evidence_digest=host.evidence_digest,
        sequence=1,
    )
    expected = {
        "expected_runner_id": host.runner_id,
        "expected_node_id": host.node_id,
        "expected_operation_id": plan.request.operation_id,
        "expected_action": plan.request.action.value,
        "expected_payload_digest": binding,
        "expected_host_evidence_digest": host.evidence_digest,
        "expected_runner_identity_thumbprint": host.runner_identity_thumbprint,
    }
    auth.verify(envelope, **expected)
    with pytest.raises(SandboxRejected, match="sandbox_runner_transport_replay_rejected"):
        auth.verify(envelope, **expected)


def _mtls_envelope(
    *,
    peer: TrustedRunnerMtlsPeer,
    plan: RunnerExecutionPlan,
    host: VerifiedRunnerHost,
    now: datetime,
    sequence: int,
) -> RunnerTransportEnvelope:
    return RunnerTransportEnvelope(
        audience="omnibase-sandbox-runner-v1",
        runner_id=host.runner_id,
        node_id=host.node_id,
        operation_id=plan.request.operation_id,
        action=plan.request.action.value,
        payload_digest=execution_binding_digest(plan, host),
        host_evidence_digest=host.evidence_digest,
        key_id=peer.key_id,
        nonce=uuid4(),
        sequence=sequence,
        sent_at=now,
        expires_at=now + timedelta(seconds=10),
        signature="",
    )


def test_mtls_transport_uses_durable_replay_and_sequence_state(tmp_path: Path) -> None:
    now = datetime(2026, 8, 2, tzinfo=UTC)
    plan, host = _plan(now)
    peer = TrustedRunnerMtlsPeer(
        runner_id=host.runner_id,
        node_id=host.node_id,
        certificate_thumbprint=host.runner_identity_thumbprint,
        verified_at=now,
        expires_at=now + timedelta(minutes=1),
    )
    ledger_root = tmp_path / "runner-private"
    ledger_root.mkdir(mode=0o700)
    ledger_root.chmod(0o700)
    ledger_path = ledger_root / "replay.sqlite3"
    envelope = _mtls_envelope(
        peer=peer,
        plan=plan,
        host=host,
        now=now,
        sequence=1,
    )
    expected = {
        "expected_runner_id": host.runner_id,
        "expected_node_id": host.node_id,
        "expected_operation_id": plan.request.operation_id,
        "expected_action": plan.request.action.value,
        "expected_payload_digest": execution_binding_digest(plan, host),
        "expected_host_evidence_digest": host.evidence_digest,
        "expected_runner_identity_thumbprint": host.runner_identity_thumbprint,
    }
    first = MtlsRunnerTransportAuthenticator(
        peer=peer,
        replay_store=SqliteRunnerReplayStore(database_path=ledger_path),
        clock=lambda: now,
    )
    first.verify(envelope, **expected)

    restarted = MtlsRunnerTransportAuthenticator(
        peer=peer,
        replay_store=SqliteRunnerReplayStore(database_path=ledger_path),
        clock=lambda: now,
    )
    with pytest.raises(SandboxRejected, match="transport_replay_rejected"):
        restarted.verify(envelope, **expected)
    with pytest.raises(SandboxRejected, match="transport_sequence_rejected"):
        restarted.verify(
            replace(envelope, nonce=uuid4()),
            **expected,
        )

    next_envelope = replace(envelope, nonce=uuid4(), sequence=2)
    restarted.verify(next_envelope, **expected)
    assert ledger_path.stat().st_mode & 0o077 == 0


def test_mtls_transport_rejects_peer_binding_signature_and_untrusted_store(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 2, tzinfo=UTC)
    plan, host = _plan(now)
    peer = TrustedRunnerMtlsPeer(
        runner_id=host.runner_id,
        node_id=host.node_id,
        certificate_thumbprint=host.runner_identity_thumbprint,
        verified_at=now,
        expires_at=now + timedelta(minutes=1),
    )
    private_root = tmp_path / "private"
    private_root.mkdir(mode=0o700)
    private_root.chmod(0o700)
    authenticator = MtlsRunnerTransportAuthenticator(
        peer=peer,
        replay_store=SqliteRunnerReplayStore(database_path=private_root / "replay.sqlite3"),
        clock=lambda: now,
    )
    envelope = _mtls_envelope(
        peer=peer,
        plan=plan,
        host=host,
        now=now,
        sequence=1,
    )
    expected = {
        "expected_runner_id": host.runner_id,
        "expected_node_id": host.node_id,
        "expected_operation_id": plan.request.operation_id,
        "expected_action": plan.request.action.value,
        "expected_payload_digest": execution_binding_digest(plan, host),
        "expected_host_evidence_digest": host.evidence_digest,
        "expected_runner_identity_thumbprint": host.runner_identity_thumbprint,
    }
    with pytest.raises(SandboxRejected, match="transport_authentication_rejected"):
        authenticator.verify(replace(envelope, signature=_B), **expected)
    with pytest.raises(SandboxRejected, match="transport_binding_rejected"):
        authenticator.verify(replace(envelope, node_id=uuid4()), **expected)
    with pytest.raises(SandboxRejected, match="transport_binding_rejected"):
        authenticator.verify(
            envelope,
            **{**expected, "expected_runner_identity_thumbprint": _A},
        )

    unsafe_root = tmp_path / "unsafe"
    unsafe_root.mkdir(mode=0o777)
    unsafe_root.chmod(0o777)
    with pytest.raises(ValueError, match="parent is not private"):
        SqliteRunnerReplayStore(database_path=unsafe_root / "replay.sqlite3")

    symlink_root = tmp_path / "symlink-root"
    symlink_root.mkdir(mode=0o700)
    symlink_root.chmod(0o700)
    real_database = symlink_root / "real.sqlite3"
    real_database.touch(mode=0o600)
    linked_database = symlink_root / "linked.sqlite3"
    linked_database.symlink_to(real_database)
    with pytest.raises(ValueError, match="database is not trusted"):
        SqliteRunnerReplayStore(database_path=linked_database)


def test_transport_rejects_signature_binding_expiry_and_sequence_drift() -> None:
    now = datetime(2026, 8, 2, tzinfo=UTC)
    clock = [now]
    plan, host = _plan(now)
    binding = execution_binding_digest(plan, host)
    auth = HmacRunnerTransportAuthenticator(
        key_id="local.a4",
        secret=_SECRET,
        clock=lambda: clock[0],
    )
    envelope = auth.sign(
        runner_id=host.runner_id,
        node_id=host.node_id,
        operation_id=plan.request.operation_id,
        action=plan.request.action.value,
        payload_digest=binding,
        host_evidence_digest=host.evidence_digest,
        sequence=2,
        validity_seconds=5,
    )
    arguments = {
        "expected_runner_id": host.runner_id,
        "expected_node_id": host.node_id,
        "expected_operation_id": plan.request.operation_id,
        "expected_action": plan.request.action.value,
        "expected_payload_digest": binding,
        "expected_host_evidence_digest": host.evidence_digest,
        "expected_runner_identity_thumbprint": host.runner_identity_thumbprint,
    }
    with pytest.raises(SandboxRejected, match="authentication_rejected"):
        auth.verify(replace(envelope, signature=_A), **arguments)
    with pytest.raises(SandboxRejected, match="binding_rejected"):
        auth.verify(envelope, **{**arguments, "expected_payload_digest": _F})
    clock[0] = now + timedelta(seconds=6)
    with pytest.raises(SandboxRejected, match="transport_expired"):
        auth.verify(envelope, **arguments)


def test_authenticated_local_transport_round_trip_and_receipt_binding() -> None:
    now = datetime(2026, 8, 2, tzinfo=UTC)
    plan, host = _plan(now)
    auth = HmacRunnerTransportAuthenticator(
        key_id="local.a4",
        secret=_SECRET,
        clock=lambda: now,
    )
    runner = _BoundRunner()
    transport = AuthenticatedLocalRunnerTransport(
        signer=auth,
        service=AuthenticatedRunnerService(authenticator=auth, runner=runner),
    )
    receipt = transport.execute(plan=plan, host=host)
    assert receipt.operation_id == plan.request.operation_id
    assert receipt.binding_digest == execution_binding_digest(plan, host)
    assert runner.calls == 1


def test_authenticated_service_rejects_mismatched_runner_receipt_as_unknown() -> None:
    now = datetime(2026, 8, 2, tzinfo=UTC)
    plan, host = _plan(now)
    auth = HmacRunnerTransportAuthenticator(
        key_id="local.a4",
        secret=_SECRET,
        clock=lambda: now,
    )
    transport = AuthenticatedLocalRunnerTransport(
        signer=auth,
        service=AuthenticatedRunnerService(authenticator=auth, runner=_BoundRunner(mismatch=True)),
    )
    with pytest.raises(RunnerOutcomeUnknown, match="sandbox_runner_receipt_binding_rejected"):
        transport.execute(plan=plan, host=host)


def test_authenticated_runner_service_defaults_reject_before_runner() -> None:
    now = datetime(2026, 8, 2, tzinfo=UTC)
    plan, host = _plan(now)
    auth = HmacRunnerTransportAuthenticator(
        key_id="local.a4",
        secret=_SECRET,
        clock=lambda: now,
    )
    envelope = auth.sign(
        runner_id=host.runner_id,
        node_id=host.node_id,
        operation_id=plan.request.operation_id,
        action=plan.request.action.value,
        payload_digest=execution_binding_digest(plan, host),
        host_evidence_digest=host.evidence_digest,
        sequence=1,
    )
    with pytest.raises(
        SandboxUnavailable,
        match="sandbox_runner_transport_authenticator_unavailable",
    ):
        AuthenticatedRunnerService().execute(envelope=envelope, plan=plan, host=host)


def test_transport_sources_have_no_network_data_service_or_environment_access() -> None:
    forbidden = {
        "docker",
        "httpx",
        "minio",
        "psycopg",
        "redis",
        "requests",
        "socket",
        "subprocess",
    }
    for module in (auth_module, service_module):
        tree = ast.parse(inspect.getsource(module))
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.partition(".")[0])
        assert imported_roots.isdisjoint(forbidden)
        source = inspect.getsource(module)
        assert "os.environ" not in source
        assert "getenv(" not in source
