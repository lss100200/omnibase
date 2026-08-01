"""P34.5A fail-closed Sandbox foundation tests.

These tests exercise contracts and a metadata-only in-memory harness.  They do
not run commands, create containers, open sockets or access data services.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest

import omnibase.sandbox.provider as provider_module
from omnibase.sandbox import (
    FakeInMemorySandboxProvider,
    InMemorySandboxAuthorizer,
    RejectingSandboxAuthorizer,
    SandboxAction,
    SandboxCommandSpec,
    SandboxConflict,
    SandboxExecutionDisabled,
    SandboxIsolationPolicy,
    SandboxNetworkPolicy,
    SandboxOperationRequest,
    SandboxRejected,
    SandboxRelativePath,
    SandboxResourceLimits,
    SandboxRuntimeSpec,
    SandboxRuntimeState,
    SandboxUnavailable,
    UnavailableSandboxProvider,
)

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_IDENTITY_A = "c" * 64
_IDENTITY_B = "d" * 64


def _limits() -> SandboxResourceLimits:
    return SandboxResourceLimits(
        cpu_millis=1_000,
        memory_bytes=512 * 1024 * 1024,
        pids=64,
        writable_bytes=1024 * 1024 * 1024,
        inodes=100_000,
        wall_time_seconds=600,
        output_bytes=1024 * 1024,
    )


def _spec() -> SandboxRuntimeSpec:
    return SandboxRuntimeSpec(
        template_digest=_DIGEST_A,
        policy_digest=_DIGEST_B,
        limits=_limits(),
        network=SandboxNetworkPolicy(),
        isolation=SandboxIsolationPolicy(run_as_uid=10_001, run_as_gid=10_001),
    )


def _request(
    action: SandboxAction,
    *,
    tenant_id: UUID | None = None,
    workspace_id: UUID | None = None,
    run_id: UUID | None = None,
    runtime_instance_id: UUID | None = None,
    node_id: UUID | None = None,
    lease_id: UUID | None = None,
    generation: int = 1,
    run_fencing: int = 11,
    node_fencing: int = 17,
    identity: str = _IDENTITY_A,
) -> SandboxOperationRequest:
    return SandboxOperationRequest(
        operation_id=uuid4(),
        action=action,
        tenant_id=tenant_id or uuid4(),
        workspace_id=workspace_id or uuid4(),
        run_id=run_id or uuid4(),
        runtime_instance_id=runtime_instance_id or uuid4(),
        node_id=node_id or uuid4(),
        lease_id=lease_id or uuid4(),
        workspace_generation=generation,
        run_fencing_token=run_fencing,
        node_fencing_token=node_fencing,
        workload_identity_thumbprint=identity,
    )


def _for_action(request: SandboxOperationRequest, action: SandboxAction) -> SandboxOperationRequest:
    return replace(request, operation_id=uuid4(), action=action)


def _authorized_harness(
    *,
    now: datetime,
    actions: frozenset[SandboxAction] = frozenset(SandboxAction),
) -> tuple[
    list[datetime],
    SandboxOperationRequest,
    InMemorySandboxAuthorizer,
    FakeInMemorySandboxProvider,
]:
    clock = [now]
    request = _request(SandboxAction.PREPARE)
    authorizer = InMemorySandboxAuthorizer(clock=lambda: clock[0])
    authorizer.install(
        request=request,
        allowed_actions=actions,
        expires_at=now + timedelta(minutes=5),
    )
    provider = FakeInMemorySandboxProvider(authorizer=authorizer)
    return clock, request, authorizer, provider


def test_production_defaults_reject_everything() -> None:
    request = _request(SandboxAction.PREPARE)
    with pytest.raises(SandboxUnavailable, match="sandbox_authorizer_unavailable"):
        RejectingSandboxAuthorizer().authorize(request)
    with pytest.raises(SandboxUnavailable, match="sandbox_provider_unavailable"):
        UnavailableSandboxProvider().prepare(request=request, spec=_spec())


@pytest.mark.parametrize(
    "path",
    [
        "../README.md",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "src\\main.py",
        "workspace/.env",
        ".ssh/id_ed25519",
        "run/docker.sock",
        "a/./b",
        "a//b",
        "",
    ],
)
def test_sandbox_paths_reject_escape_and_reserved_locations(path: str) -> None:
    with pytest.raises(ValueError):
        SandboxRelativePath(path)


def test_strict_command_resource_network_and_isolation_contracts() -> None:
    command = SandboxCommandSpec(
        argv=("python", "-m", "compileall", "src"),
        cwd=SandboxRelativePath("workspace"),
        timeout_seconds=60,
        max_output_bytes=4096,
    )
    assert command.argv[0] == "python"

    with pytest.raises(TypeError, match="immutable tuple"):
        SandboxCommandSpec(
            argv=cast(tuple[str, ...], ["python"]),
            cwd=SandboxRelativePath("workspace"),
            timeout_seconds=60,
            max_output_bytes=4096,
        )
    with pytest.raises(ValueError, match="NUL"):
        SandboxCommandSpec(
            argv=("python\x00",),
            cwd=SandboxRelativePath("workspace"),
            timeout_seconds=60,
            max_output_bytes=4096,
        )
    with pytest.raises(TypeError, match="cpu_millis"):
        replace(_limits(), cpu_millis=True)
    with pytest.raises(ValueError, match="cannot allow network services"):
        SandboxNetworkPolicy(allowed_service_ids=(uuid4(),))
    with pytest.raises(ValueError, match="cannot join"):
        SandboxNetworkPolicy(direct_overlay=True)
    with pytest.raises(ValueError, match="cannot be disabled"):
        SandboxIsolationPolicy(
            run_as_uid=10_001,
            run_as_gid=10_001,
            no_new_privileges=False,
        )
    with pytest.raises(ValueError, match="host capabilities"):
        SandboxIsolationPolicy(
            run_as_uid=10_001,
            run_as_gid=10_001,
            allow_runtime_socket=True,
        )


def test_authorizer_revalidates_full_binding_action_expiry_and_revocation() -> None:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    clock = [now]
    request = _request(SandboxAction.START)
    authorizer = InMemorySandboxAuthorizer(clock=lambda: clock[0])
    authorizer.install(
        request=request,
        allowed_actions=frozenset({SandboxAction.START}),
        expires_at=now + timedelta(seconds=30),
    )
    verified = authorizer.authorize(request)
    assert verified.request == request
    assert len(verified.verification_digest) == 64

    with pytest.raises(SandboxRejected, match="sandbox_authorization_rejected"):
        authorizer.authorize(replace(request, operation_id=uuid4(), node_fencing_token=18))
    with pytest.raises(SandboxRejected, match="sandbox_authorization_rejected"):
        authorizer.authorize(_for_action(request, SandboxAction.EXEC))

    clock[0] = now + timedelta(seconds=31)
    with pytest.raises(SandboxRejected, match="sandbox_authorization_expired"):
        authorizer.authorize(replace(request, operation_id=uuid4()))

    clock[0] = now
    authorizer.revoke(request.lease_id)
    with pytest.raises(SandboxRejected, match="sandbox_authorization_rejected"):
        authorizer.authorize(replace(request, operation_id=uuid4()))


def test_metadata_harness_exercises_lifecycle_but_never_executes(monkeypatch) -> None:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    _, base, _, provider = _authorized_harness(now=now)

    def forbidden(*args, **kwargs):
        del args, kwargs
        raise AssertionError("external side effect attempted")

    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr("socket.socket", forbidden)

    prepared = provider.prepare(request=base, spec=_spec())
    created = provider.create(
        request=_for_action(base, SandboxAction.CREATE),
        spec=_spec(),
        prepared_digest=prepared,
    )
    assert created.state is SandboxRuntimeState.CREATED
    assert str(created.handle.value) not in repr(created.handle)

    running = provider.start(
        request=_for_action(base, SandboxAction.START),
        handle=created.handle,
    )
    assert running.state is SandboxRuntimeState.RUNNING
    stats = provider.stats(
        request=_for_action(base, SandboxAction.STATS),
        handle=created.handle,
    )
    assert stats.state is SandboxRuntimeState.RUNNING
    assert stats.memory_bytes_used == 0
    assert (
        provider.logs(
            request=_for_action(base, SandboxAction.LOGS),
            handle=created.handle,
            cursor=None,
            byte_limit=4096,
        ).chunks
        == ()
    )

    with pytest.raises(SandboxExecutionDisabled, match="sandbox_execution_not_unlocked"):
        provider.exec(
            request=_for_action(base, SandboxAction.EXEC),
            handle=created.handle,
            command=SandboxCommandSpec(
                argv=("python", "main.py"),
                cwd=SandboxRelativePath("workspace"),
                timeout_seconds=30,
                max_output_bytes=4096,
            ),
        )

    stopped = provider.stop(
        request=_for_action(base, SandboxAction.STOP),
        handle=created.handle,
    )
    assert stopped.state is SandboxRuntimeState.STOPPED
    snapshot = provider.snapshot(
        request=_for_action(base, SandboxAction.SNAPSHOT),
        handle=created.handle,
    )
    assert snapshot.metadata_only is True
    destroyed = provider.destroy(
        request=_for_action(base, SandboxAction.DESTROY),
        handle=created.handle,
    )
    assert destroyed.state is SandboxRuntimeState.DESTROYED
    with pytest.raises(SandboxConflict, match="sandbox_runtime_destroyed"):
        provider.start(
            request=_for_action(base, SandboxAction.START),
            handle=created.handle,
        )
    with pytest.raises(SandboxConflict, match="sandbox_run_already_has_runtime"):
        provider.create(
            request=_for_action(base, SandboxAction.CREATE),
            spec=_spec(),
            prepared_digest=prepared,
        )


def test_provider_rejects_action_mismatch_stale_fencing_and_cross_runtime_binding() -> None:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    _, base, authorizer, provider = _authorized_harness(now=now)
    prepared = provider.prepare(request=base, spec=_spec())
    created = provider.create(
        request=_for_action(base, SandboxAction.CREATE),
        spec=_spec(),
        prepared_digest=prepared,
    )
    with pytest.raises(SandboxRejected, match="sandbox_action_mismatch"):
        provider.start(request=_for_action(base, SandboxAction.LOGS), handle=created.handle)
    with pytest.raises(SandboxRejected, match="sandbox_authorization_rejected"):
        provider.start(
            request=replace(
                _for_action(base, SandboxAction.START),
                node_fencing_token=base.node_fencing_token + 1,
            ),
            handle=created.handle,
        )
    other_run = replace(
        _for_action(base, SandboxAction.START),
        run_id=uuid4(),
        operation_id=uuid4(),
    )
    with pytest.raises(SandboxRejected, match="sandbox_authorization_rejected"):
        provider.start(request=other_run, handle=created.handle)

    refenced = replace(
        _for_action(base, SandboxAction.START),
        run_fencing_token=base.run_fencing_token + 1,
    )
    authorizer.install(
        request=refenced,
        allowed_actions=frozenset({SandboxAction.START}),
        expires_at=now + timedelta(minutes=5),
    )
    with pytest.raises(SandboxRejected, match="sandbox_runtime_not_found"):
        provider.start(request=refenced, handle=created.handle)


def test_revocation_blocks_lifecycle_after_runtime_creation() -> None:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    _, base, authorizer, provider = _authorized_harness(now=now)
    prepared = provider.prepare(request=base, spec=_spec())
    created = provider.create(
        request=_for_action(base, SandboxAction.CREATE),
        spec=_spec(),
        prepared_digest=prepared,
    )
    authorizer.revoke(base.lease_id)
    with pytest.raises(SandboxRejected, match="sandbox_authorization_rejected"):
        provider.start(
            request=_for_action(base, SandboxAction.START),
            handle=created.handle,
        )


def test_restore_requires_new_generation_run_and_workload_identity() -> None:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    _, base, authorizer, provider = _authorized_harness(now=now)
    prepared = provider.prepare(request=base, spec=_spec())
    created = provider.create(
        request=_for_action(base, SandboxAction.CREATE),
        spec=_spec(),
        prepared_digest=prepared,
    )
    provider.stop(request=_for_action(base, SandboxAction.STOP), handle=created.handle)
    snapshot = provider.snapshot(
        request=_for_action(base, SandboxAction.SNAPSHOT),
        handle=created.handle,
    )

    restored_request = _request(
        SandboxAction.RESTORE,
        tenant_id=base.tenant_id,
        workspace_id=base.workspace_id,
        node_id=base.node_id,
        generation=base.workspace_generation + 1,
        run_fencing=base.run_fencing_token + 1,
        node_fencing=base.node_fencing_token,
        identity=_IDENTITY_B,
    )
    authorizer.install(
        request=restored_request,
        allowed_actions=frozenset({SandboxAction.RESTORE}),
        expires_at=now + timedelta(minutes=5),
    )
    restored = provider.restore_new_generation(
        request=restored_request,
        snapshot=snapshot,
        spec=_spec(),
    )
    assert restored.state is SandboxRuntimeState.CREATED
    assert restored.workspace_generation == base.workspace_generation + 1
    assert restored.run_id != base.run_id
    assert restored.workload_identity_thumbprint != base.workload_identity_thumbprint

    replay = replace(restored_request, operation_id=uuid4())
    with pytest.raises(SandboxConflict, match="sandbox_run_already_has_runtime"):
        provider.restore_new_generation(
            request=replay,
            snapshot=snapshot,
            spec=_spec(),
        )

    fabricated_snapshot = replace(snapshot, snapshot_id=uuid4())
    fabricated_request = _request(
        SandboxAction.RESTORE,
        tenant_id=base.tenant_id,
        workspace_id=base.workspace_id,
        node_id=base.node_id,
        generation=base.workspace_generation + 2,
        run_fencing=base.run_fencing_token + 2,
        node_fencing=base.node_fencing_token,
        identity="e" * 64,
    )
    authorizer.install(
        request=fabricated_request,
        allowed_actions=frozenset({SandboxAction.RESTORE}),
        expires_at=now + timedelta(minutes=5),
    )
    with pytest.raises(SandboxRejected, match="sandbox_snapshot_not_found"):
        provider.restore_new_generation(
            request=fabricated_request,
            snapshot=fabricated_snapshot,
            spec=_spec(),
        )

    reused_identity = replace(
        restored_request,
        operation_id=uuid4(),
        workload_identity_thumbprint=base.workload_identity_thumbprint,
    )
    authorizer.install(
        request=reused_identity,
        allowed_actions=frozenset({SandboxAction.RESTORE}),
        expires_at=now + timedelta(minutes=5),
    )
    with pytest.raises(SandboxRejected, match="sandbox_restore_identity_reuse_rejected"):
        provider.restore_new_generation(
            request=reused_identity,
            snapshot=snapshot,
            spec=_spec(),
        )


def test_provider_source_has_no_runtime_or_network_control_imports() -> None:
    tree = ast.parse(inspect.getsource(provider_module))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.partition(".")[0])
    assert imported_roots.isdisjoint({"docker", "httpx", "os", "pathlib", "socket", "subprocess"})
