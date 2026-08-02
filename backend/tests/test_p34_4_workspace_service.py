"""P34.4 Workspace policy, lifecycle, template, and lease contracts."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from omnibase.workspaces import service


def _result(*, one: object | None = None, optional: object | None = None) -> MagicMock:
    result = MagicMock()
    result.scalar_one.return_value = one
    result.scalar_one_or_none.return_value = optional
    return result


@pytest.mark.parametrize(
    "unsafe_spec",
    [
        {"password": "not-even-a-real-password"},
        {"nested": {"provider_handle": "opaque"}},
        {"runtime": {"docker_socket": "disabled"}},
        {"files": {"host_path": "workspace"}},
        {"command": "load C:\\Users\\operator\\project"},
        {"command": "/var/run/worker"},
        {"command": "read \\\\server\\share"},
        {"endpoint": "postgresql://user:pass@database/name"},
        {"header": "Bearer placeholder"},
        {"config": ".env.production"},
    ],
)
def test_template_spec_rejects_sensitive_keys_credentials_and_host_paths(
    unsafe_spec: dict[str, object],
) -> None:
    with pytest.raises(service.TemplateRejected):
        service.validate_template_spec(unsafe_spec)


def test_template_digest_is_canonical_and_order_independent() -> None:
    first = {"image": "python-3.12", "resources": {"cpu": 1, "memory_mb": 512}}
    second = {"resources": {"memory_mb": 512, "cpu": 1}, "image": "python-3.12"}

    assert service.validate_template_spec(first) == service.validate_template_spec(second)
    assert len(service.validate_template_spec(first)) == 64


def test_template_version_reuse_is_idempotent_only_for_identical_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = {"profile": "metadata-only"}
    existing = SimpleNamespace(
        digest=service.validate_template_spec(spec),
        template_spec=spec,
        display_name="Original",
        supersedes_template_id=None,
    )
    audit = MagicMock()
    monkeypatch.setattr(service, "append_audit_event", audit)
    replay_session = MagicMock()
    replay_session.execute.side_effect = [
        _result(optional=SimpleNamespace(id="admin-a")),
        _result(optional=None),
        _result(one=existing),
    ]

    result = service.register_template(
        replay_session,
        tenant_id="tenant-a",
        actor_user_id="admin-a",
        template_key="safe.template",
        version=1,
        display_name="Original",
        template_spec=spec,
        request_id="request-a",
    )

    assert result is existing
    audit.assert_called_once()
    assert audit.call_args.kwargs["status_code"] == 200

    conflict_session = MagicMock()
    conflict_session.execute.side_effect = [
        _result(optional=SimpleNamespace(id="admin-a")),
        _result(optional=None),
        _result(one=existing),
    ]

    with pytest.raises(service.WorkspaceConflict, match="different content"):
        service.register_template(
            conflict_session,
            tenant_id="tenant-a",
            actor_user_id="admin-a",
            template_key="safe.template",
            version=1,
            display_name="Changed",
            template_spec=spec,
            request_id="request-b",
        )

    audit.assert_called_once()


def test_template_registration_revalidates_live_tenant_admin_before_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    session.execute.return_value = _result(optional=None)
    audit = MagicMock()
    monkeypatch.setattr(service, "append_audit_event", audit)

    with pytest.raises(service.WorkspacePolicyDenied, match="governance access"):
        service.register_template(
            session,
            tenant_id="tenant-a",
            actor_user_id="stale-admin",
            template_key="safe.template",
            version=1,
            display_name="Safe",
            template_spec={"profile": "metadata-only"},
            request_id="request-a",
        )

    session.execute.assert_called_once()
    audit.assert_not_called()


def test_same_tenant_other_workspace_has_no_implicit_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str, str]] = []

    def no_membership(
        _session: object,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        lock: bool = False,
    ) -> None:
        del lock
        seen.append((tenant_id, workspace_id, user_id))

    monkeypatch.setattr(service, "_get_membership", no_membership)

    with pytest.raises(service.WorkspacePolicyDenied, match="workspace not found"):
        service.authorize_workspace_action(
            MagicMock(),
            tenant_id="tenant-a",
            workspace_id="workspace-b",
            user_id="member-of-workspace-a",
            action="workspace.read",
        )

    assert seen == [("tenant-a", "workspace-b", "member-of-workspace-a")]


@pytest.mark.parametrize(
    ("role", "action", "allowed"),
    [
        ("viewer", "workspace.read", True),
        ("viewer", "workspace.run", False),
        ("member", "workspace.run", True),
        ("member", "workspace.lifecycle", False),
        ("operator", "workspace.lifecycle", True),
        ("operator", "workspace.snapshot", True),
        ("operator", "workspace.members.manage", False),
        ("maintainer", "workspace.members.manage", True),
        ("maintainer", "workspace.restore", True),
        ("maintainer", "workspace.nodes.manage", True),
        ("owner", "workspace.nodes.manage", True),
    ],
)
def test_workspace_role_matrix_is_monotonic_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    role: str,
    action: str,
    allowed: bool,
) -> None:
    membership = SimpleNamespace(role=role, state="active")
    monkeypatch.setattr(service, "_get_membership", lambda *args, **kwargs: membership)

    if allowed:
        assert (
            service.authorize_workspace_action(
                MagicMock(),
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                user_id="user-a",
                action=action,
            )
            is membership
        )
    else:
        with pytest.raises(service.WorkspacePolicyDenied):
            service.authorize_workspace_action(
                MagicMock(),
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                user_id="user-a",
                action=action,
            )


def test_unknown_workspace_action_is_denied_before_membership_lookup() -> None:
    session = MagicMock()

    with pytest.raises(service.WorkspacePolicyDenied, match="unknown workspace action"):
        service.authorize_workspace_action(
            session,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            action="workspace.superuser",
        )

    session.execute.assert_not_called()


def test_last_active_owner_cannot_be_suspended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = SimpleNamespace(role="owner", state="active", version=4)
    monkeypatch.setattr(
        service,
        "_lock_workspace_aggregate",
        lambda *args, **kwargs: SimpleNamespace(id="workspace-a"),
    )
    monkeypatch.setattr(
        service,
        "authorize_workspace_action",
        lambda *args, **kwargs: SimpleNamespace(role="owner", state="active"),
    )
    monkeypatch.setattr(service, "_get_membership", lambda *args, **kwargs: owner)
    monkeypatch.setattr(service, "_active_owner_count", lambda *args, **kwargs: 1)
    session = MagicMock()

    with pytest.raises(service.WorkspaceConflict, match="last workspace owner"):
        service.set_membership_state(
            session,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            actor_user_id="owner-a",
            target_user_id="owner-a",
            state="suspended",
            request_id="request-a",
        )

    assert owner.state == "active"
    assert owner.version == 4
    session.flush.assert_not_called()


def test_membership_mutation_locks_aggregate_then_actor_target_and_owner_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    actor = SimpleNamespace(role="owner", state="active")
    target = SimpleNamespace(role="owner", state="active", version=2)

    def lock_workspace(*args: object, **kwargs: object) -> SimpleNamespace:
        calls.append("workspace")
        return SimpleNamespace(id="workspace-a")

    def authorize(*args: object, **kwargs: object) -> SimpleNamespace:
        assert kwargs["lock"] is True
        calls.append("actor")
        return actor

    def get_target(*args: object, **kwargs: object) -> SimpleNamespace:
        assert kwargs["lock"] is True
        calls.append("target")
        return target

    def count_owners(*args: object, **kwargs: object) -> int:
        calls.append("owners")
        return 2

    monkeypatch.setattr(service, "_lock_workspace_aggregate", lock_workspace)
    monkeypatch.setattr(service, "authorize_workspace_action", authorize)
    monkeypatch.setattr(service, "_get_membership", get_target)
    monkeypatch.setattr(service, "_active_owner_count", count_owners)
    monkeypatch.setattr(service, "append_audit_event", MagicMock())
    session = MagicMock()

    result = service.set_membership_state(
        session,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_user_id="owner-a",
        target_user_id="owner-b",
        state="suspended",
        request_id="request-a",
    )

    assert result is target
    assert calls == ["workspace", "actor", "target", "owners"]
    assert target.state == "suspended"


def test_membership_aggregate_lock_failure_prevents_stale_actor_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorize = MagicMock()
    monkeypatch.setattr(service, "authorize_workspace_action", authorize)
    monkeypatch.setattr(
        service,
        "_lock_workspace_aggregate",
        MagicMock(side_effect=service.WorkspaceNotFound("workspace not found")),
    )

    with pytest.raises(service.WorkspaceNotFound):
        service.set_membership_state(
            MagicMock(),
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            actor_user_id="owner-a",
            target_user_id="owner-b",
            state="suspended",
            request_id="request-a",
        )

    authorize.assert_not_called()


def test_maintainer_cannot_demote_an_existing_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = SimpleNamespace(role="owner", state="active", version=3)
    monkeypatch.setattr(
        service,
        "_lock_workspace_aggregate",
        lambda *args, **kwargs: SimpleNamespace(id="workspace-a"),
    )
    monkeypatch.setattr(
        service,
        "authorize_workspace_action",
        lambda *args, **kwargs: SimpleNamespace(role="maintainer", state="active"),
    )
    monkeypatch.setattr(service, "_get_membership", lambda *args, **kwargs: owner)
    count_owners = MagicMock(return_value=2)
    monkeypatch.setattr(service, "_active_owner_count", count_owners)
    session = MagicMock()
    session.execute.return_value = _result(optional=SimpleNamespace(id="owner-b"))

    with pytest.raises(service.WorkspacePolicyDenied, match="only an owner"):
        service.upsert_membership(
            session,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            actor_user_id="maintainer-a",
            target_user_id="owner-b",
            role="maintainer",
            expected_version=3,
            request_id="request-a",
        )

    assert owner.role == "owner"
    assert owner.version == 3
    count_owners.assert_not_called()
    session.flush.assert_not_called()


def test_repeating_desired_state_is_idempotent_even_with_old_expected_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = SimpleNamespace(
        desired_state="paused",
        observed_state="paused",
        version=9,
        generation=3,
    )
    monkeypatch.setattr(service, "get_workspace", lambda *args, **kwargs: workspace)
    session = MagicMock()

    result = service.request_workspace_state(
        session,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_user_id="operator-a",
        desired_state="paused",
        expected_version=1,
        request_id="request-a",
    )

    assert result is not None
    assert workspace.version == 9
    session.execute.assert_not_called()
    session.flush.assert_not_called()


def test_stale_version_cannot_change_workspace_desired_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = SimpleNamespace(
        desired_state="stopped",
        observed_state="stopped",
        version=8,
        generation=2,
    )
    monkeypatch.setattr(service, "get_workspace", lambda *args, **kwargs: workspace)
    session = MagicMock()

    with pytest.raises(service.WorkspaceConflict, match="version changed"):
        service.request_workspace_state(
            session,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            actor_user_id="operator-a",
            desired_state="running",
            expected_version=7,
            request_id="request-a",
        )

    assert workspace.desired_state == "stopped"
    assert workspace.version == 8
    session.execute.assert_not_called()
    session.flush.assert_not_called()


def test_restore_creates_new_workspace_identity_and_generation_without_runtime_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = SimpleNamespace(
        id="source-workspace",
        template_id="template-a",
        parent_workspace_id="parent-a",
        generation=6,
        quota={"max_active_runs": 1},
    )
    snapshot = SimpleNamespace(
        id="snapshot-a",
        manifest_digest="a" * 64,
        state="ready",
    )
    resource = SimpleNamespace(id="restored-workspace", version=1)
    monkeypatch.setattr(service, "get_workspace", lambda *args, **kwargs: source)
    monkeypatch.setattr(service, "register_resource", lambda *args, **kwargs: resource)
    monkeypatch.setattr(service, "append_resource_lineage", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        service,
        "get_resource",
        lambda *args, **kwargs: SimpleNamespace(id=kwargs["resource_id"], version=1),
    )
    session = MagicMock()
    session.execute.return_value = _result(optional=snapshot)

    restored = service.restore_snapshot_new_workspace(
        session,
        tenant_id="tenant-a",
        source_workspace_id="source-workspace",
        snapshot_id="snapshot-a",
        actor_user_id="owner-a",
        display_name="Restored",
    )

    assert restored.id == "restored-workspace"
    assert restored.id != source.id
    assert restored.generation == source.generation + 1
    assert restored.version == 1
    assert restored.desired_state == restored.observed_state == "stopped"
    assert restored.restored_from_snapshot_id == snapshot.id
    assert "runtime_instance_id" not in restored.__dict__
    assert "workload_identity_digest" not in restored.__dict__
    added = session.add_all.call_args.args[0]
    assert all(item.__class__.__name__ not in {"WorkspaceRun", "RunLease"} for item in added)


def _lease_session(
    *,
    now: datetime,
    run: SimpleNamespace,
    lease: SimpleNamespace,
    workspace: SimpleNamespace,
) -> MagicMock:
    session = MagicMock()
    session.execute.side_effect = [
        _result(optional=SimpleNamespace(workspace_id=workspace.id)),
        _result(optional=workspace),
        _result(optional=run),
        _result(optional=lease),
        _result(one=now),
    ]
    return session


@pytest.mark.parametrize(
    "mutation",
    [
        lambda run, lease, workspace, node, now: setattr(lease, "generation", run.generation - 1),
        lambda run, lease, workspace, node, now: setattr(run, "generation", lease.generation + 1),
        lambda run, lease, workspace, node, now: setattr(
            workspace, "generation", lease.generation + 1
        ),
        lambda run, lease, workspace, node, now: setattr(lease, "fencing_token", 6),
        lambda run, lease, workspace, node, now: setattr(run, "next_fencing_token", 9),
        lambda run, lease, workspace, node, now: setattr(
            lease, "node_fencing_token", node.fencing_token - 1
        ),
        lambda run, lease, workspace, node, now: setattr(node, "fencing_token", 12),
        lambda run, lease, workspace, node, now: setattr(lease, "expires_at", now),
        lambda run, lease, workspace, node, now: setattr(lease, "state", "revoked"),
    ],
)
def test_run_lease_rejects_stale_generation_fencing_expiry_and_revocation(
    monkeypatch: pytest.MonkeyPatch,
    mutation: Callable[
        [SimpleNamespace, SimpleNamespace, SimpleNamespace, SimpleNamespace, datetime],
        None,
    ],
) -> None:
    now = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)
    workspace = SimpleNamespace(id="workspace-a", generation=3)
    node = SimpleNamespace(fencing_token=11)
    run = SimpleNamespace(generation=3, next_fencing_token=8)
    lease = SimpleNamespace(
        state="active",
        node_id="node-a",
        node_fencing_token=11,
        generation=3,
        fencing_token=7,
        expires_at=now + timedelta(seconds=30),
    )
    mutation(run, lease, workspace, node, now)
    session = _lease_session(
        now=now,
        run=run,
        lease=lease,
        workspace=workspace,
    )
    monkeypatch.setattr(service, "get_active_attested_node", lambda *args, **kwargs: node)

    with pytest.raises(service.LeaseRejected, match="expired, stale, revoked"):
        service._validated_run_lease(
            session,
            tenant_id="tenant-a",
            run_id="run-a",
            lease_id="lease-a",
            node_id="node-a",
            generation=3,
            fencing_token=7,
        )


def test_bind_run_runtime_identity_is_single_assignment_and_exactly_replayable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = SimpleNamespace(
        observed_state="leased",
        runtime_instance_id=None,
        workload_identity_digest=None,
        version=4,
    )
    session = MagicMock()
    monkeypatch.setattr(
        service,
        "_validated_run_lease",
        lambda *args, **kwargs: (run, SimpleNamespace(), datetime(2026, 8, 1, tzinfo=UTC)),
    )
    runtime_instance_id = "98bd1424-7592-4f09-af37-61105246d7ce"
    workload_identity_digest = "a" * 64

    first = service.bind_run_runtime_identity(
        session,
        tenant_id="tenant-a",
        run_id="run-a",
        lease_id="lease-a",
        node_id="node-a",
        generation=3,
        fencing_token=7,
        runtime_instance_id=runtime_instance_id.upper(),
        workload_identity_digest=workload_identity_digest,
    )
    replay = service.bind_run_runtime_identity(
        session,
        tenant_id="tenant-a",
        run_id="run-a",
        lease_id="lease-a",
        node_id="node-a",
        generation=3,
        fencing_token=7,
        runtime_instance_id=runtime_instance_id,
        workload_identity_digest=workload_identity_digest,
    )

    assert first is replay is run
    assert run.runtime_instance_id == runtime_instance_id
    assert run.workload_identity_digest == workload_identity_digest
    assert run.version == 5
    session.flush.assert_called_once()


@pytest.mark.parametrize(
    ("runtime_instance_id", "workload_identity_digest"),
    [
        ("e6a78c2d-4a35-4385-b737-191445a66c8c", "a" * 64),
        ("98bd1424-7592-4f09-af37-61105246d7ce", "b" * 64),
    ],
)
def test_bind_run_runtime_identity_rejects_identity_drift(
    monkeypatch: pytest.MonkeyPatch,
    runtime_instance_id: str,
    workload_identity_digest: str,
) -> None:
    run = SimpleNamespace(
        observed_state="leased",
        runtime_instance_id="98bd1424-7592-4f09-af37-61105246d7ce",
        workload_identity_digest="a" * 64,
        version=5,
    )
    session = MagicMock()
    monkeypatch.setattr(
        service,
        "_validated_run_lease",
        lambda *args, **kwargs: (run, SimpleNamespace(), datetime(2026, 8, 1, tzinfo=UTC)),
    )

    with pytest.raises(service.LeaseRejected, match="already bound"):
        service.bind_run_runtime_identity(
            session,
            tenant_id="tenant-a",
            run_id="run-a",
            lease_id="lease-a",
            node_id="node-a",
            generation=3,
            fencing_token=7,
            runtime_instance_id=runtime_instance_id,
            workload_identity_digest=workload_identity_digest,
        )

    assert run.version == 5
    session.flush.assert_not_called()


def test_verify_run_lease_for_sandbox_returns_complete_runtime_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)
    runtime_instance_id = "98bd1424-7592-4f09-af37-61105246d7ce"
    run = SimpleNamespace(
        id="888f19b4-2959-45ed-b5b6-bc06e77bb1f3",
        tenant_id="0e237468-a17a-44c9-a882-cb513e8f5a9b",
        workspace_id="4dbcf4f1-a5e4-45ff-95c5-afc00e5f7f17",
        generation=3,
        runtime_instance_id=runtime_instance_id,
        workload_identity_digest="a" * 64,
    )
    lease = SimpleNamespace(
        id="37c6f3cf-a90f-45dd-8810-a4e18d0c2158",
        node_id="0fb5d57b-bb49-4cd6-8011-a32d29c34bbe",
        node_fencing_token=11,
        fencing_token=7,
        expires_at=now + timedelta(seconds=30),
    )
    monkeypatch.setattr(
        service,
        "_validated_run_lease",
        lambda *args, **kwargs: (run, lease, now),
    )

    facts = service.verify_run_lease_for_sandbox(
        MagicMock(),
        tenant_id=run.tenant_id,
        run_id=run.id,
        runtime_instance_id=runtime_instance_id,
        lease_id=lease.id,
        node_id=lease.node_id,
        generation=3,
        fencing_token=7,
        workload_identity_digest="a" * 64,
    )

    assert facts.runtime_instance_id == runtime_instance_id
    assert facts.workspace_id == run.workspace_id
    assert facts.node_fencing_token == 11
    assert facts.verified_at == now
    assert facts.expires_at == lease.expires_at
    assert len(facts.verification_digest) == 64


@pytest.mark.parametrize(
    ("runtime_instance_id", "workload_identity_digest"),
    [
        ("e6a78c2d-4a35-4385-b737-191445a66c8c", "a" * 64),
        ("98bd1424-7592-4f09-af37-61105246d7ce", "b" * 64),
    ],
)
def test_verify_run_lease_for_sandbox_rejects_stale_runtime_binding(
    monkeypatch: pytest.MonkeyPatch,
    runtime_instance_id: str,
    workload_identity_digest: str,
) -> None:
    run = SimpleNamespace(
        runtime_instance_id="98bd1424-7592-4f09-af37-61105246d7ce",
        workload_identity_digest="a" * 64,
    )
    monkeypatch.setattr(
        service,
        "_validated_run_lease",
        lambda *args, **kwargs: (
            run,
            SimpleNamespace(),
            datetime(2026, 8, 1, tzinfo=UTC),
        ),
    )

    with pytest.raises(service.LeaseRejected, match="stale or unbound"):
        service.verify_run_lease_for_sandbox(
            MagicMock(),
            tenant_id="tenant-a",
            run_id="run-a",
            runtime_instance_id=runtime_instance_id,
            lease_id="lease-a",
            node_id="node-a",
            generation=3,
            fencing_token=7,
            workload_identity_digest=workload_identity_digest,
        )


def test_claim_run_lease_binds_current_node_fencing_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)
    workspace = SimpleNamespace(id="workspace-a", generation=3)
    node = SimpleNamespace(fencing_token=13)
    run = SimpleNamespace(
        workspace_id="workspace-a",
        generation=3,
        desired_state="running",
        observed_state="queued",
        next_fencing_token=7,
        version=4,
    )
    session = MagicMock()
    session.execute.side_effect = [
        _result(optional=SimpleNamespace(workspace_id="workspace-a")),
        _result(optional=workspace),
        _result(optional=run),
        _result(one=now),
        _result(optional=None),
    ]
    monkeypatch.setattr(service, "get_active_attested_node", lambda *args, **kwargs: node)

    lease = service.claim_run_lease(
        session,
        tenant_id="tenant-a",
        run_id="run-a",
        node_id="node-a",
    )

    assert lease.node_fencing_token == 13
    assert lease.fencing_token == 7
    assert run.next_fencing_token == 8
    assert run.observed_state == "leased"
    assert run.version == 5
    session.add.assert_called_once_with(lease)
    session.flush.assert_called_once()


@pytest.mark.parametrize(
    ("terminal_state", "expected_lease_state", "expected_desired_state"),
    [
        ("stopped", "completed", "stopped"),
        ("succeeded", "completed", "stopped"),
        ("failed", "revoked", "stopped"),
        ("cancelled", "revoked", "cancelled"),
    ],
)
def test_terminal_run_state_revokes_runtime_identity_and_closes_lease(
    monkeypatch: pytest.MonkeyPatch,
    terminal_state: str,
    expected_lease_state: str,
    expected_desired_state: str,
) -> None:
    run = SimpleNamespace(
        observed_state="running",
        desired_state="running",
        runtime_instance_id="runtime-a",
        workload_identity_digest="a" * 64,
        last_result_digest=None,
        last_error_code=None,
        version=4,
    )
    lease = SimpleNamespace(state="active")
    monkeypatch.setattr(
        service,
        "_validated_run_lease",
        lambda *args, **kwargs: (run, lease, datetime(2026, 8, 1, tzinfo=UTC)),
    )
    session = MagicMock()

    result = service.submit_run_state(
        session,
        tenant_id="tenant-a",
        run_id="run-a",
        lease_id="lease-a",
        node_id="node-a",
        generation=3,
        fencing_token=7,
        observed_state=terminal_state,
        result_digest="b" * 64,
        error_code="synthetic" if terminal_state == "failed" else None,
    )

    assert result is run
    assert run.observed_state == terminal_state
    assert run.desired_state == expected_desired_state
    assert run.runtime_instance_id is None
    assert run.workload_identity_digest is None
    assert run.last_result_digest == "b" * 64
    assert lease.state == expected_lease_state
    assert run.version == 5
    session.flush.assert_called_once()


def test_terminal_run_cannot_be_revived_by_a_stale_holder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = SimpleNamespace(
        observed_state="succeeded",
        desired_state="stopped",
        runtime_instance_id=None,
        workload_identity_digest=None,
        last_result_digest="b" * 64,
        last_error_code=None,
        version=5,
    )
    lease = SimpleNamespace(state="completed")
    monkeypatch.setattr(
        service,
        "_validated_run_lease",
        lambda *args, **kwargs: (run, lease, datetime(2026, 8, 1, tzinfo=UTC)),
    )
    session = MagicMock()

    with pytest.raises(service.WorkspaceConflict, match="invalid run observed-state transition"):
        service.submit_run_state(
            session,
            tenant_id="tenant-a",
            run_id="run-a",
            lease_id="lease-a",
            node_id="node-a",
            generation=3,
            fencing_token=7,
            observed_state="running",
        )

    assert run.observed_state == "succeeded"
    assert run.version == 5
    session.flush.assert_not_called()
