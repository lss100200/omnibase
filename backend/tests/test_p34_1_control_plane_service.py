"""Tenant isolation and state-machine contracts for the P34.1 services."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from omnibase.control_plane import service

InvalidStateTransition = getattr(
    service,
    "InvalidStateTransition",
    service.InvalidTransition,
)
create_approval_request = getattr(
    service,
    "create_approval_request",
    service.create_approval,
)


@pytest.mark.parametrize(
    ("function", "id_keyword", "exception"),
    [
        (service.get_resource, "resource_id", service.ResourceNotFound),
        (service.get_operation, "operation_id", service.OperationNotFound),
        (service.get_approval, "approval_id", service.ApprovalNotFound),
    ],
)
def test_cross_tenant_or_unknown_ids_share_safe_not_found_semantics(
    function: object,
    id_keyword: str,
    exception: type[Exception],
) -> None:
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = None

    with pytest.raises(exception, match="not found") as raised:
        function(  # type: ignore[operator]
            session,
            tenant_id="tenant-a",
            **{id_keyword: "opaque-id-from-tenant-b"},
        )

    assert "tenant-b" not in str(raised.value)
    statement = str(session.execute.call_args.args[0])
    assert "tenant_id" in statement
    assert id_keyword.removesuffix("_id") in statement


@pytest.mark.parametrize(
    ("function", "extra"),
    [
        (service.list_resources, {"kind": None, "state": None}),
        (
            service.list_resource_lineage,
            {
                "source_resource_id": None,
                "derived_resource_id": None,
                "relation": None,
            },
        ),
        (service.list_operations, {"state": None, "resource_id": None}),
        (service.list_approvals, {"state": None, "resource_id": None}),
        (service.list_audit_events, {"action": None, "resource_id": None}),
    ],
)
def test_all_control_plane_lists_scope_count_and_items_to_tenant(
    function: object,
    extra: dict[str, object],
) -> None:
    session = MagicMock()
    session.scalar.return_value = 0
    session.scalars.return_value = []

    items, total = function(  # type: ignore[operator]
        session,
        tenant_id="tenant-a",
        limit=20,
        offset=0,
        **extra,
    )

    assert items == []
    assert total == 0
    assert "tenant_id" in str(session.scalar.call_args.args[0])
    assert "tenant_id" in str(session.scalars.call_args.args[0])


def test_lineage_list_scopes_count_and_items_to_tenant() -> None:
    session = MagicMock()
    session.scalar.return_value = 0
    session.scalars.return_value = []

    items, total = service.list_resource_lineage(
        session,
        tenant_id="tenant-a",
        limit=20,
        offset=0,
    )

    assert items == []
    assert total == 0
    assert "tenant_id" in str(session.scalar.call_args.args[0])
    assert "tenant_id" in str(session.scalars.call_args.args[0])


def test_lineage_rejects_cross_tenant_derived_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def resource_lookup(
        _session: object,
        *,
        tenant_id: str,
        resource_id: str,
    ) -> SimpleNamespace:
        assert tenant_id == "tenant-a"
        if resource_id == "source-a":
            return SimpleNamespace(id=resource_id, version=2)
        raise service.ResourceNotFound("Resource not found")

    monkeypatch.setattr(service, "get_resource", resource_lookup)
    session = MagicMock()

    with pytest.raises(service.ResourceNotFound, match="Resource not found"):
        service.append_resource_lineage(
            session,
            tenant_id="tenant-a",
            source_resource_id="source-a",
            derived_resource_id="derived-from-tenant-b",
            relation="derived_from",
            source_version=2,
        )
    session.add.assert_not_called()


def test_lineage_binds_exact_source_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service,
        "get_resource",
        lambda *args, **kwargs: SimpleNamespace(id=kwargs["resource_id"], version=3),
    )

    with pytest.raises(service.OptimisticLockConflict, match="Source resource version"):
        service.append_resource_lineage(
            MagicMock(),
            tenant_id="tenant-a",
            source_resource_id="source-a",
            derived_resource_id="derived-a",
            relation="derived_from",
            source_version=2,
        )


def test_register_resource_rejects_path_like_kind_before_database_access() -> None:
    session = MagicMock()
    with pytest.raises(ValueError, match="Resource kind"):
        service.register_resource(
            session,
            tenant_id="tenant-a",
            kind="../../host",
            owner_type="agent",
            display_name="Unsafe",
            policy_class="workspace_private",
        )
    session.add.assert_not_called()


@pytest.mark.parametrize("forbidden_key", ["physical_locator", "safe", "nested"])
def test_append_audit_event_rejects_unclassified_or_sensitive_details(
    forbidden_key: str,
) -> None:
    session = MagicMock()
    with pytest.raises(ValueError, match="audit detail"):
        service.append_audit_event(
            session,
            tenant_id="tenant-a",
            request_id="req-1",
            actor_type="agent",
            action="resource.read",
            decision="allowed",
            risk_level="R0",
            details={forbidden_key: "tenant_secret"},
        )
    session.add.assert_not_called()


def test_append_audit_event_accepts_only_classified_code_like_details() -> None:
    session = MagicMock()
    event = service.append_audit_event(
        session,
        tenant_id="tenant-a",
        request_id="req-1",
        actor_type="system",
        action="resource.read",
        decision="denied",
        risk_level="R0",
        details={"reason_code": "policy.denied", "retryable": False},
    )

    assert event.details == {"reason_code": "policy.denied", "retryable": False}
    session.add.assert_called_once_with(event)


def test_metadata_redaction_recursively_removes_sensitive_keys() -> None:
    session = MagicMock()
    service.register_resource(
        session,
        tenant_id="tenant-a",
        kind="agent_memory",
        owner_type="system",
        display_name="Memory",
        policy_class="workspace_private",
        metadata={
            "safe": "kept",
            "physical_locator": "hidden",
            "schema_name": "tenant_secret",
            "minio_key": "private/object",
            "authorization": "Bearer secret",
            "token": "secret-token",
            "password": "secret-password",
            "sql": "SELECT secret",
            "prompt": "private prompt",
            "file_bytes": "private bytes",
            "nested": [{"credential": "secret", "ok": True}],
        },
    )

    resource = session.add.call_args.args[0]
    serialized = str(resource.resource_metadata).lower()
    assert resource.resource_metadata == {"safe": "kept", "nested": [{"ok": True}]}
    for secret in ("tenant_secret", "bearer secret", "secret-token", "private/object"):
        assert secret not in serialized


def test_user_resource_owner_must_be_an_active_user_in_current_tenant() -> None:
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = None

    with pytest.raises(service.DomainConflict, match="active tenant user"):
        service.register_resource(
            session,
            tenant_id="tenant-a",
            kind="document",
            owner_type="user",
            owner_id="inactive-user",
            display_name="Document",
            policy_class="canonical_readonly",
        )

    assert "is_active" in str(session.execute.call_args.args[0])
    session.add.assert_not_called()


def test_user_operation_actor_must_be_an_active_user_in_current_tenant() -> None:
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = None

    with pytest.raises(service.DomainConflict, match="active tenant user"):
        service.create_operation(
            session,
            tenant_id="tenant-a",
            kind="resource.read",
            risk_level="R0",
            actor_type="user",
            actor_id="inactive-user",
            request_hash="a" * 64,
        )

    assert "is_active" in str(session.execute.call_args.args[0])
    session.add.assert_not_called()


def test_user_approval_requester_must_be_an_active_user_in_current_tenant() -> None:
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = None

    with pytest.raises(service.DomainConflict, match="active tenant user"):
        create_approval_request(
            session,
            tenant_id="tenant-a",
            requester_type="user",
            requester_id="inactive-user",
            action="data.schema.apply",
            risk_level="R2",
            request_hash="a" * 64,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            grant_id="grant-1",
            operation_id="operation-1",
        )

    assert "is_active" in str(session.execute.call_args.args[0])
    session.add.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_id", "contains space"),
        ("action", "../../shell"),
        ("input_hash", "A" * 64),
    ],
)
def test_audit_identifiers_and_hashes_are_strictly_bounded(
    field: str,
    value: str,
) -> None:
    session = MagicMock()
    parameters: dict[str, object] = {
        "tenant_id": "tenant-a",
        "request_id": "req-1",
        "actor_type": "system",
        "action": "resource.read",
        "decision": "denied",
        "risk_level": "R0",
    }
    parameters[field] = value

    with pytest.raises(ValueError):
        service.append_audit_event(session, **parameters)  # type: ignore[arg-type]
    session.add.assert_not_called()


def test_denied_audit_can_record_unknown_logical_references_without_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for attribute in ("get_resource", "get_operation", "get_approval"):
        monkeypatch.setattr(
            service,
            attribute,
            MagicMock(side_effect=AssertionError("denied audit must not dereference")),
        )
    session = MagicMock()

    event = service.append_audit_event(
        session,
        tenant_id="tenant-a",
        request_id="req-denied-1",
        actor_type="system",
        action="resource.read",
        decision="denied",
        risk_level="R0",
        resource_id="unknown-resource",
        operation_id="unknown-operation",
        approval_id="unknown-approval",
        details={"reason_code": "not_found"},
    )

    assert event.decision == "denied"
    session.add.assert_called_once_with(event)


def test_operation_illegal_transition_fails_before_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation = SimpleNamespace(id="op-1", tenant_id="tenant-a", state="succeeded", version=4)
    monkeypatch.setattr(service, "get_operation", lambda *args, **kwargs: operation)
    session = MagicMock()

    with pytest.raises(InvalidStateTransition):
        service.transition_operation(
            session,
            tenant_id="tenant-a",
            operation_id="op-1",
            expected_version=4,
            target_state="running",
        )

    session.execute.assert_not_called()


def test_operation_kind_rejects_path_like_identifier() -> None:
    session = MagicMock()
    with pytest.raises(ValueError, match="(?i)operation kind"):
        service.create_operation(
            session,
            tenant_id="tenant-a",
            kind="../../shell",
            risk_level="R2",
            actor_type="agent",
        )
    session.add.assert_not_called()


def _operation(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "id": "operation-1",
        "tenant_id": "tenant-a",
        "workspace_id": None,
        "run_id": None,
        "actor_type": "user",
        "actor_id": "requester-1",
        "resource_id": None,
        "resource_version": None,
        "approval_id": None,
        "request_hash": "a" * 64,
        "kind": "data.schema.apply",
        "state": "pending_approval",
        "risk_level": "R2",
        "version": 1,
        "deadline_at": datetime.now(UTC) + timedelta(minutes=5),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    ("risk_level", "expected_state"),
    [("R0", "queued"), ("R1", "queued"), ("R2", "pending_approval"), ("R4", "pending_approval")],
)
def test_create_operation_routes_high_risk_through_pending_approval(
    risk_level: str,
    expected_state: str,
) -> None:
    session = MagicMock()
    operation = service.create_operation(
        session,
        tenant_id="tenant-a",
        kind="data.schema.apply",
        risk_level=risk_level,
        actor_type="system",
        request_hash="a" * 64,
        deadline_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    assert operation.state == expected_state
    assert operation.request_hash == "a" * 64
    session.add.assert_called_once_with(operation)


@pytest.mark.parametrize("target_state", ["queued", "running"])
def test_pending_approval_cannot_use_general_transition_path(
    monkeypatch: pytest.MonkeyPatch,
    target_state: str,
) -> None:
    operation = _operation()
    monkeypatch.setattr(service, "get_operation", lambda *args, **kwargs: operation)
    session = MagicMock()

    with pytest.raises(InvalidStateTransition, match="Cannot transition"):
        service.transition_operation(
            session,
            tenant_id="tenant-a",
            operation_id=operation.id,
            expected_version=1,
            target_state=target_state,
        )

    session.execute.assert_not_called()


@pytest.mark.parametrize("target_state", ["failed", "cancelled"])
def test_pending_approval_can_fail_closed_without_authorization(
    monkeypatch: pytest.MonkeyPatch,
    target_state: str,
) -> None:
    operation = _operation()
    monkeypatch.setattr(service, "get_operation", lambda *args, **kwargs: operation)
    session = MagicMock()
    session.execute.return_value.rowcount = 1

    service.transition_operation(
        session,
        tenant_id="tenant-a",
        operation_id=operation.id,
        expected_version=1,
        target_state=target_state,
        error_code="approval.denied" if target_state == "failed" else None,
    )

    statement = session.execute.call_args.args[0]
    assert target_state in statement.compile().params.values()


def test_create_operation_rejects_approval_prebinding_after_tenant_scoped_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval = _pending_approval()
    lookup = MagicMock(return_value=approval)
    monkeypatch.setattr(service, "get_approval", lookup)
    session = MagicMock()

    with pytest.raises(service.ApprovalConflict, match="Create the operation before"):
        service.create_operation(
            session,
            tenant_id="tenant-a",
            kind="data.schema.apply",
            risk_level="R2",
            actor_type="system",
            request_hash="a" * 64,
            approval_id=approval.id,
        )

    assert lookup.call_args.kwargs == {
        "tenant_id": "tenant-a",
        "approval_id": approval.id,
    }
    session.add.assert_not_called()


def test_create_operation_rejects_expired_deadline_before_insert() -> None:
    session = MagicMock()
    with pytest.raises(ValueError, match="deadline"):
        service.create_operation(
            session,
            tenant_id="tenant-a",
            kind="resource.read",
            risk_level="R0",
            actor_type="system",
            request_hash="a" * 64,
            deadline_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    session.add.assert_not_called()


def test_expired_operation_cannot_start(monkeypatch: pytest.MonkeyPatch) -> None:
    operation = _operation(
        state="queued",
        risk_level="R0",
        deadline_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    monkeypatch.setattr(service, "get_operation", lambda *args, **kwargs: operation)
    session = MagicMock()

    with pytest.raises(InvalidStateTransition, match="deadline"):
        service.transition_operation(
            session,
            tenant_id="tenant-a",
            operation_id=operation.id,
            expected_version=1,
            target_state="running",
        )
    session.execute.assert_not_called()


@pytest.mark.parametrize(
    ("initial_state", "target_state"),
    [("running", "compensating"), ("failed", "compensating"), ("compensating", "compensated")],
)
def test_operation_compensation_transitions_are_explicitly_supported(
    monkeypatch: pytest.MonkeyPatch,
    initial_state: str,
    target_state: str,
) -> None:
    operation = _operation(state=initial_state, risk_level="R0")
    monkeypatch.setattr(service, "get_operation", lambda *args, **kwargs: operation)
    session = MagicMock()
    session.execute.return_value.rowcount = 1

    service.transition_operation(
        session,
        tenant_id="tenant-a",
        operation_id=operation.id,
        expected_version=1,
        target_state=target_state,
    )

    statement = session.execute.call_args.args[0]
    assert target_state in statement.compile().params.values()


@pytest.mark.parametrize(
    ("field", "resource_kind"),
    [("workspace_id", "document"), ("run_id", "workspace"), ("actor_id", "document")],
)
def test_operation_resource_references_require_exact_same_tenant_kind(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    resource_kind: str,
) -> None:
    seen_tenants: list[str] = []

    def wrong_kind(*args: object, **kwargs: object) -> SimpleNamespace:
        seen_tenants.append(str(kwargs["tenant_id"]))
        return SimpleNamespace(id=kwargs["resource_id"], kind=resource_kind, parent_id=None)

    monkeypatch.setattr(service, "get_resource", wrong_kind)
    parameters: dict[str, object] = {
        "tenant_id": "tenant-a",
        "kind": "resource.read",
        "risk_level": "R0",
        "actor_type": "system",
        "request_hash": "a" * 64,
    }
    if field == "actor_id":
        parameters.update(actor_type="agent", actor_id="agent-1")
    else:
        parameters[field] = f"{field}-1"

    with pytest.raises(service.DomainConflict, match="resource kind"):
        service.create_operation(MagicMock(), **parameters)  # type: ignore[arg-type]

    assert seen_tenants == ["tenant-a"]


def _pending_approval(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "id": "approval-1",
        "tenant_id": "tenant-a",
        "requester_type": "user",
        "requester_id": "requester-1",
        "workspace_id": None,
        "run_id": None,
        "operation_id": "operation-1",
        "grant_id": "grant-1",
        "action": "data.schema.apply",
        "risk_level": "R2",
        "required_approver_role": "tenant_admin",
        "state": "pending",
        "version": 1,
        "expires_at": datetime.now(UTC) + timedelta(minutes=5),
        "request_hash": "a" * 64,
        "resource_id": "resource-1",
        "resource_version": 3,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_non_system_approval_requester_requires_id_before_database_access() -> None:
    session = MagicMock()
    with pytest.raises(ValueError, match="requester_id"):
        create_approval_request(
            session,
            tenant_id="tenant-a",
            requester_type="user",
            requester_id=None,
            action="data.schema.apply",
            risk_level="R2",
            request_hash="a" * 64,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            grant_id="grant-1",
        )
    session.add.assert_not_called()


@pytest.mark.parametrize(
    ("risk_level", "required_role"),
    [("R2", "tenant_admin"), ("R3", "tenant_admin"), ("R4", "platform_admin")],
)
def test_approval_risk_maps_to_required_admin_role(
    monkeypatch: pytest.MonkeyPatch,
    risk_level: str,
    required_role: str,
) -> None:
    operation = _operation(risk_level=risk_level, actor_type="system", actor_id=None)
    monkeypatch.setattr(service, "get_operation", lambda *args, **kwargs: operation)
    session = MagicMock()
    approval = create_approval_request(
        session,
        tenant_id="tenant-a",
        requester_type="system",
        requester_id=None,
        action="data.schema.apply",
        risk_level=risk_level,
        request_hash="a" * 64,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        grant_id="grant-1",
        operation_id=operation.id,
    )

    assert approval.required_approver_role == required_role


def test_create_approval_rejects_caller_supplied_role_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation = _operation(risk_level="R4", actor_type="system", actor_id=None)
    monkeypatch.setattr(service, "get_operation", lambda *args, **kwargs: operation)
    session = MagicMock()
    with pytest.raises(ValueError, match="required_approver_role"):
        create_approval_request(
            session,
            tenant_id="tenant-a",
            requester_type="system",
            requester_id=None,
            action="data.schema.apply",
            risk_level="R4",
            request_hash="a" * 64,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            grant_id="grant-1",
            operation_id=operation.id,
            required_approver_role="tenant_admin",
        )
    session.add.assert_not_called()


@pytest.mark.parametrize("actor_type", ["agent", "workspace", "run"])
def test_non_human_runtime_actor_cannot_decide_approval(
    monkeypatch: pytest.MonkeyPatch,
    actor_type: str,
) -> None:
    approval = _pending_approval(
        operation_id=None,
        resource_id=None,
        resource_version=None,
        risk_level="R1",
    )
    monkeypatch.setattr(service, "get_approval", lambda *args, **kwargs: approval)
    session = MagicMock()

    with pytest.raises(ValueError, match="decided_by_actor_type"):
        service.decide_approval(
            session,
            tenant_id="tenant-a",
            approval_id=approval.id,
            expected_version=1,
            decided_by_actor_type=actor_type,
            decided_by_actor_id="approver-1",
            decided_by_actor_role="tenant_admin",
            decision="approved",
            decision_reason=None,
            request_hash="a" * 64,
            resource_version=None,
        )
    session.execute.assert_not_called()


@pytest.mark.parametrize("actor_type", ["user", "system"])
def test_every_approver_including_system_requires_stable_id(
    monkeypatch: pytest.MonkeyPatch,
    actor_type: str,
) -> None:
    approval = _pending_approval(
        operation_id=None,
        resource_id=None,
        resource_version=None,
        risk_level="R1",
    )
    monkeypatch.setattr(service, "get_approval", lambda *args, **kwargs: approval)

    with pytest.raises(ValueError, match="decided_by_actor_id"):
        service.decide_approval(
            MagicMock(),
            tenant_id="tenant-a",
            approval_id=approval.id,
            expected_version=1,
            decided_by_actor_type=actor_type,
            decided_by_actor_id=None,
            decided_by_actor_role="tenant_admin",
            decision="approved",
            decision_reason=None,
            request_hash="a" * 64,
            resource_version=None,
        )


def test_approver_role_is_admin_only_and_must_meet_required_rank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval = _pending_approval(
        operation_id=None,
        resource_id=None,
        resource_version=None,
        risk_level="R1",
        required_approver_role="tenant_admin",
    )
    monkeypatch.setattr(service, "get_approval", lambda *args, **kwargs: approval)

    with pytest.raises(ValueError, match="decided_by_actor_role"):
        service.decide_approval(
            MagicMock(),
            tenant_id="tenant-a",
            approval_id=approval.id,
            expected_version=1,
            decided_by_actor_type="user",
            decided_by_actor_id="approver-1",
            decided_by_actor_role="user",
            decision="approved",
            decision_reason=None,
            request_hash="a" * 64,
            resource_version=None,
        )


def test_active_non_admin_user_cannot_self_assert_tenant_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval = _pending_approval(
        operation_id=None,
        resource_id=None,
        resource_version=None,
        risk_level="R1",
    )
    monkeypatch.setattr(service, "get_approval", lambda *args, **kwargs: approval)
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = None

    with pytest.raises(service.DomainConflict, match="active tenant admin"):
        service.decide_approval(
            session,
            tenant_id="tenant-a",
            approval_id=approval.id,
            expected_version=1,
            decided_by_actor_type="user",
            decided_by_actor_id="ordinary-user",
            decided_by_actor_role="tenant_admin",
            decision="approved",
            decision_reason=None,
            request_hash="a" * 64,
            resource_version=None,
        )

    query = str(session.execute.call_args.args[0])
    assert "is_active" in query
    assert "is_tenant_admin" in query


def test_r4_user_cannot_assert_platform_admin_but_trusted_system_can(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation = _operation(risk_level="R4")
    approval = _pending_approval(
        resource_id=None,
        resource_version=None,
        risk_level="R4",
        required_approver_role="platform_admin",
    )
    monkeypatch.setattr(service, "get_approval", lambda *args, **kwargs: approval)
    monkeypatch.setattr(service, "get_operation", lambda *args, **kwargs: operation)

    with pytest.raises(service.ApprovalConflict, match="cannot assert platform_admin"):
        service.decide_approval(
            MagicMock(),
            tenant_id="tenant-a",
            approval_id=approval.id,
            expected_version=1,
            decided_by_actor_type="user",
            decided_by_actor_id="tenant-admin",
            decided_by_actor_role="platform_admin",
            decision="approved",
            decision_reason=None,
            request_hash="a" * 64,
            resource_version=None,
        )

    session = MagicMock()
    session.execute.return_value.rowcount = 1
    service.decide_approval(
        session,
        tenant_id="tenant-a",
        approval_id=approval.id,
        expected_version=1,
        decided_by_actor_type="system",
        decided_by_actor_id="trusted-platform-approver",
        decided_by_actor_role="platform_admin",
        decision="approved",
        decision_reason=None,
        request_hash="a" * 64,
        resource_version=None,
    )
    assert getattr(session.execute.call_args.args[0], "is_update", False)

    with pytest.raises(service.ApprovalConflict, match="insufficient"):
        service.decide_approval(
            MagicMock(),
            tenant_id="tenant-a",
            approval_id=approval.id,
            expected_version=1,
            decided_by_actor_type="user",
            decided_by_actor_id="approver-1",
            decided_by_actor_role="tenant_admin",
            decision="approved",
            decision_reason=None,
            request_hash="a" * 64,
            resource_version=None,
        )


def test_decide_approval_rechecks_bound_operation_state_and_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval = _pending_approval(resource_id=None, resource_version=None)
    monkeypatch.setattr(service, "get_approval", lambda *args, **kwargs: approval)

    monkeypatch.setattr(
        service,
        "get_operation",
        lambda *args, **kwargs: _operation(state="queued"),
    )
    with pytest.raises(service.ApprovalConflict, match="no longer pending"):
        service.decide_approval(
            MagicMock(),
            tenant_id="tenant-a",
            approval_id=approval.id,
            expected_version=1,
            decided_by_actor_type="system",
            decided_by_actor_id="trusted-approver",
            decided_by_actor_role="tenant_admin",
            decision="approved",
            decision_reason=None,
            request_hash="a" * 64,
            resource_version=None,
        )

    monkeypatch.setattr(
        service,
        "get_operation",
        lambda *args, **kwargs: _operation(risk_level="R4"),
    )
    with pytest.raises(service.ApprovalConflict, match="operation binding changed"):
        service.decide_approval(
            MagicMock(),
            tenant_id="tenant-a",
            approval_id=approval.id,
            expected_version=1,
            decided_by_actor_type="system",
            decided_by_actor_id="trusted-approver",
            decided_by_actor_role="tenant_admin",
            decision="approved",
            decision_reason=None,
            request_hash="a" * 64,
            resource_version=None,
        )


def test_authorize_operation_is_the_only_public_approval_consumption_path() -> None:
    assert hasattr(service, "authorize_operation")
    assert "authorize_operation" in service.__all__
    assert not hasattr(service, "consume_approval")
    assert "consume_approval" not in service.__all__


def test_authorize_operation_consumes_exact_binding_and_queues_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation = _operation()
    approval = _pending_approval(
        state="approved",
        consumed_at=None,
        resource_id=None,
        resource_version=None,
    )
    monkeypatch.setattr(service, "get_operation", lambda *args, **kwargs: operation)
    monkeypatch.setattr(service, "get_approval", lambda *args, **kwargs: approval)
    session = MagicMock()
    session.execute.return_value.rowcount = 1

    service.authorize_operation(
        session,
        tenant_id="tenant-a",
        operation_id=operation.id,
        expected_version=1,
        approval_id=approval.id,
        approval_expected_version=1,
        consumer_actor_type="user",
        consumer_actor_id="requester-1",
        action="data.schema.apply",
        workspace_id=None,
        run_id=None,
        request_hash="a" * 64,
        resource_version=None,
        grant_id="grant-1",
    )

    update_statements = [
        call.args[0]
        for call in session.execute.call_args_list
        if getattr(call.args[0], "is_update", False)
    ]
    assert len(update_statements) == 2
    statement = update_statements[-1]
    assert "queued" in statement.compile().params.values()


@pytest.mark.parametrize(
    ("approval_field", "mismatched_value"),
    [
        ("grant_id", "grant-other"),
        ("action", "data.rows.read"),
        ("workspace_id", "workspace-other"),
        ("run_id", "run-other"),
        ("operation_id", "operation-other"),
        ("request_hash", "b" * 64),
        ("resource_version", 9),
    ],
)
def test_authorize_operation_rejects_any_exact_binding_drift(
    monkeypatch: pytest.MonkeyPatch,
    approval_field: str,
    mismatched_value: object,
) -> None:
    operation = _operation()
    approval_values: dict[str, object] = {
        "state": "approved",
        "consumed_at": None,
        "resource_id": None,
        "resource_version": None,
    }
    approval_values[approval_field] = mismatched_value
    approval = _pending_approval(**approval_values)
    monkeypatch.setattr(service, "get_operation", lambda *args, **kwargs: operation)
    monkeypatch.setattr(service, "get_approval", lambda *args, **kwargs: approval)

    with pytest.raises(service.ApprovalConflict, match="Approval"):
        service.authorize_operation(
            MagicMock(),
            tenant_id="tenant-a",
            operation_id=operation.id,
            expected_version=1,
            approval_id=approval.id,
            approval_expected_version=1,
            consumer_actor_type="user",
            consumer_actor_id="requester-1",
            action="data.schema.apply",
            workspace_id=None,
            run_id=None,
            request_hash="a" * 64,
            resource_version=None,
            grant_id="grant-1",
        )


def test_r4_operation_cannot_bind_or_authorize_with_r2_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation = _operation(risk_level="R4")
    approval = _pending_approval(
        state="approved",
        consumed_at=None,
        resource_id=None,
        resource_version=None,
        risk_level="R2",
    )
    monkeypatch.setattr(service, "get_operation", lambda *args, **kwargs: operation)

    with pytest.raises(service.ApprovalConflict, match="operation bindings"):
        create_approval_request(
            MagicMock(),
            tenant_id="tenant-a",
            requester_type="user",
            requester_id="requester-1",
            action="data.schema.apply",
            risk_level="R2",
            request_hash="a" * 64,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            grant_id="grant-1",
            operation_id=operation.id,
        )

    monkeypatch.setattr(service, "get_approval", lambda *args, **kwargs: approval)
    with pytest.raises(service.ApprovalConflict, match="match operation"):
        service.authorize_operation(
            MagicMock(),
            tenant_id="tenant-a",
            operation_id=operation.id,
            expected_version=1,
            approval_id=approval.id,
            approval_expected_version=1,
            consumer_actor_type="user",
            consumer_actor_id="requester-1",
            action="data.schema.apply",
            workspace_id=None,
            run_id=None,
            request_hash="a" * 64,
            resource_version=None,
            grant_id="grant-1",
        )


def test_approval_requester_cannot_decide_own_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval = _pending_approval()
    monkeypatch.setattr(service, "get_approval", lambda *args, **kwargs: approval)

    with pytest.raises(service.ApprovalConflict, match="own approval"):
        service.decide_approval(
            MagicMock(),
            tenant_id="tenant-a",
            approval_id=approval.id,
            expected_version=1,
            decided_by_actor_id="requester-1",
            decision="approved",
            decision_reason=None,
            request_hash="a" * 64,
            resource_version=3,
        )


def test_expired_approval_cannot_be_decided(monkeypatch: pytest.MonkeyPatch) -> None:
    approval = _pending_approval(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    monkeypatch.setattr(service, "get_approval", lambda *args, **kwargs: approval)

    with pytest.raises(service.ApprovalConflict, match="expired"):
        service.decide_approval(
            MagicMock(),
            tenant_id="tenant-a",
            approval_id=approval.id,
            expected_version=1,
            decided_by_actor_id="admin-2",
            decision="approved",
            decision_reason=None,
            request_hash="a" * 64,
            resource_version=3,
        )


def test_approval_is_invalidated_when_resource_version_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval = _pending_approval()
    monkeypatch.setattr(service, "get_approval", lambda *args, **kwargs: approval)
    monkeypatch.setattr(
        service,
        "get_resource",
        lambda *args, **kwargs: SimpleNamespace(version=4),
    )

    with pytest.raises(service.ApprovalConflict, match="Resource version changed"):
        service.decide_approval(
            MagicMock(),
            tenant_id="tenant-a",
            approval_id=approval.id,
            expected_version=1,
            decided_by_actor_id="admin-2",
            decision="approved",
            decision_reason=None,
            request_hash="a" * 64,
            resource_version=3,
        )


def test_create_approval_request_rejects_stale_resource_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service,
        "get_resource",
        lambda *args, **kwargs: SimpleNamespace(version=5),
    )
    with pytest.raises(service.ApprovalConflict, match="current resource version"):
        create_approval_request(
            MagicMock(),
            tenant_id="tenant-a",
            requester_type="user",
            requester_id="user-1",
            action="data.schema.apply",
            risk_level="R3",
            request_hash="a" * 64,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            resource_id="resource-1",
            resource_version=4,
        )


@pytest.mark.parametrize("bad_hash", ["", "a" * 63, "g" * 64, "A" * 64])
def test_approval_request_hash_must_be_lowercase_sha256_hex(bad_hash: str) -> None:
    with pytest.raises(ValueError, match="request_hash"):
        create_approval_request(
            MagicMock(),
            tenant_id="tenant-a",
            requester_type="user",
            requester_id="user-1",
            action="data.schema.apply",
            risk_level="R3",
            request_hash=bad_hash,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value

    def scalar_one(self) -> object:
        return self.value


def _idempotency_session(record: SimpleNamespace, inserted_id: str | None) -> MagicMock:
    session = MagicMock()
    session.execute.side_effect = [_ScalarResult(inserted_id), _ScalarResult(record)]
    return session


def test_idempotency_exact_replay_returns_original_record() -> None:
    record = SimpleNamespace(id="idem-1", request_hash="a" * 64, state="pending")
    session = _idempotency_session(record, None)

    returned, created = service.reserve_idempotency(
        session,
        tenant_id="tenant-a",
        actor_scope="user:1",
        operation_name="resource.register",
        key="key-1",
        request_hash="a" * 64,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    assert returned is record
    assert created is False


def test_idempotency_same_key_different_hash_conflicts() -> None:
    record = SimpleNamespace(id="idem-1", request_hash="a" * 64, state="pending")
    session = _idempotency_session(record, None)

    with pytest.raises(service.IdempotencyConflict, match="different input"):
        service.reserve_idempotency(
            session,
            tenant_id="tenant-a",
            actor_scope="user:1",
            operation_name="resource.register",
            key="key-1",
            request_hash="b" * 64,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )


@pytest.mark.parametrize("bad_hash", ["", "a" * 63, "g" * 64, "A" * 64])
def test_idempotency_request_hash_must_be_lowercase_sha256_hex(bad_hash: str) -> None:
    with pytest.raises(ValueError, match="request_hash"):
        service.reserve_idempotency(
            MagicMock(),
            tenant_id="tenant-a",
            actor_scope="user:1",
            operation_name="resource.register",
            key="key-1",
            request_hash=bad_hash,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )


def test_idempotency_operation_reference_is_tenant_scoped_before_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lookup = MagicMock(side_effect=service.OperationNotFound("Operation not found"))
    monkeypatch.setattr(service, "get_operation", lookup)
    session = MagicMock()

    with pytest.raises(service.OperationNotFound, match="not found"):
        service.reserve_idempotency(
            session,
            tenant_id="tenant-a",
            actor_scope="user:1",
            operation_name="resource.register",
            key="key-1",
            request_hash="a" * 64,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            operation_id="operation-from-tenant-b",
        )

    assert lookup.call_args.kwargs == {
        "tenant_id": "tenant-a",
        "operation_id": "operation-from-tenant-b",
    }
    session.execute.assert_not_called()


def test_idempotency_reservation_uses_atomic_on_conflict_unique_scope() -> None:
    record = SimpleNamespace(id="idem-1", request_hash="a" * 64, state="pending")
    session = _idempotency_session(record, "idem-1")

    _, created = service.reserve_idempotency(
        session,
        tenant_id="tenant-a",
        actor_scope="user:1",
        operation_name="resource.register",
        key="key-1",
        request_hash="a" * 64,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    insert_statement = str(session.execute.call_args_list[0].args[0])
    assert created is True
    assert "ON CONFLICT" in insert_statement
    for column in ("tenant_id", "actor_scope", "operation_name", "key"):
        assert column in insert_statement


@pytest.mark.parametrize(
    ("function", "extra"),
    [
        (service.list_resources, {"kind": None, "state": None}),
        (service.list_operations, {"state": None, "resource_id": None}),
        (service.list_approvals, {"state": None, "resource_id": None}),
        (service.list_audit_events, {"action": None, "resource_id": None}),
    ],
)
@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (201, 0), (20, -1)])
def test_list_pagination_is_bounded_before_query(
    function: object,
    extra: dict[str, object],
    limit: int,
    offset: int,
) -> None:
    session = MagicMock()
    with pytest.raises(ValueError, match="limit|offset"):
        function(  # type: ignore[operator]
            session,
            tenant_id="tenant-a",
            limit=limit,
            offset=offset,
            **extra,
        )
    session.scalar.assert_not_called()
    session.scalars.assert_not_called()
