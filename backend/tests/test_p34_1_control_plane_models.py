"""Security contracts for the Phase 3-4 control-plane persistence models."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import CheckConstraint, UniqueConstraint

from omnibase.control_plane.models import (
    ApprovalRequest,
    AuditEvent,
    IdempotencyRecord,
    OperationRecord,
    ResourceLineage,
    ResourceRecord,
)
from omnibase.control_plane.schemas import (
    ApprovalRead,
    AuditEventRead,
    OperationRead,
    ResourceRead,
)
from omnibase.db.models import GLOBAL_SCHEMA


def test_all_six_control_plane_models_are_global_and_tenant_scoped() -> None:
    expected = {
        ResourceRecord: "resource_registry",
        ResourceLineage: "resource_lineage",
        AuditEvent: "audit_events",
        OperationRecord: "operations",
        ApprovalRequest: "approval_requests",
        IdempotencyRecord: "idempotency_records",
    }

    for model, table_name in expected.items():
        assert model.__table__.name == table_name
        assert model.__table__.schema == GLOBAL_SCHEMA
        assert "tenant_id" in model.__table__.columns
        assert model.__table__.columns.tenant_id.nullable is False


def test_resource_physical_locator_is_internal_and_repr_safe() -> None:
    secret = "tenant_deadbeef/private/minio/key"
    resource = ResourceRecord(
        id="10000000-0000-0000-0000-000000000001",
        tenant_id="20000000-0000-0000-0000-000000000001",
        kind="document",
        owner_type="user",
        display_name="Public title",
        state="active",
        version=1,
        policy_class="canonical_readonly",
        physical_locator={"schema": "tenant_deadbeef", "key": secret},
        resource_metadata={},
    )

    representation = repr(resource)
    assert "physical_locator" not in representation
    assert "tenant_deadbeef" not in representation
    assert secret not in representation


def test_resource_kind_is_extensible_but_restricted_to_safe_identifiers() -> None:
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in ResourceRecord.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    expression = checks["resource_registry_kind_check"]
    assert "^[a-z][a-z0-9_]{1,63}$" in expression
    assert "workspace" not in expression


def test_resource_owner_type_and_identity_must_be_consistent() -> None:
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in ResourceRecord.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    expression = checks["resource_registry_owner_identity_check"]
    assert "owner_type = 'system' AND owner_id IS NULL" in expression
    assert "owner_type IN ('user', 'workspace', 'agent')" in expression
    assert "owner_id IS NOT NULL" in expression


def test_audit_model_has_no_physical_locator_or_secret_columns() -> None:
    columns = set(AuditEvent.__table__.columns.keys())
    forbidden = {
        "physical_locator",
        "schema_name",
        "minio_key",
        "authorization",
        "token",
        "password",
        "sql",
        "prompt",
        "file_bytes",
    }
    assert columns.isdisjoint(forbidden)


def test_public_resource_schema_never_contains_locator_or_metadata() -> None:
    now = datetime.now(UTC)
    resource = SimpleNamespace(
        id="10000000-0000-0000-0000-000000000001",
        kind="agent_memory",
        owner_type="user",
        owner_id=None,
        parent_id=None,
        display_name="Document",
        state="active",
        version=1,
        policy_class="canonical_readonly",
        physical_locator={"schema": "tenant_secret"},
        resource_metadata={
            "safe": True,
            "physical_locator": {"schema": "tenant_secret"},
            "nested": {"physical_locator": "minio/secret/key"},
        },
        created_at=now,
        updated_at=now,
    )

    payload = ResourceRead.model_validate(resource).model_dump(mode="json")
    assert "physical_locator" not in ResourceRead.model_json_schema()["properties"]
    assert "metadata" not in ResourceRead.model_json_schema()["properties"]
    assert "physical_locator" not in str(payload)
    assert "metadata" not in payload


def test_public_audit_schema_omits_internal_details_entirely() -> None:
    now = datetime.now(UTC)
    event = SimpleNamespace(
        id="30000000-0000-0000-0000-000000000001",
        request_id="req-1",
        actor_type="user",
        actor_id=None,
        workspace_id=None,
        run_id=None,
        grant_id=None,
        resource_id=None,
        approval_id=None,
        operation_id=None,
        action="resource.read",
        decision="allowed",
        risk_level="R0",
        input_hash=None,
        before_version=None,
        after_version=None,
        status_code=200,
        row_count=None,
        bytes_in=0,
        bytes_out=0,
        duration_ms=1,
        details={
            "safe": "value",
            "physical_locator": {"schema": "tenant_secret"},
            "items": [{"physical_locator": "minio/secret"}, {"ok": True}],
        },
        created_at=now,
    )

    payload = AuditEventRead.model_validate(event).model_dump(mode="json")
    assert "physical_locator" not in str(payload)
    assert "details" not in AuditEventRead.model_json_schema()["properties"]
    assert "details" not in payload


def test_public_operation_and_approval_schemas_omit_internal_execution_data() -> None:
    operation_properties = OperationRead.model_json_schema()["properties"]
    approval_properties = ApprovalRead.model_json_schema()["properties"]

    assert {"metadata", "result_ref", "error_detail"}.isdisjoint(operation_properties)
    assert {"metadata", "decision_reason"}.isdisjoint(approval_properties)


def test_idempotency_scope_has_database_unique_constraint() -> None:
    constraints = {
        tuple(column.name for column in constraint.columns)
        for constraint in IdempotencyRecord.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("tenant_id", "actor_scope", "operation_name", "key") in constraints


def test_approval_binds_exact_authorization_context() -> None:
    columns = set(ApprovalRequest.__table__.columns.keys())
    assert {
        "grant_id",
        "action",
        "workspace_id",
        "run_id",
        "operation_id",
        "request_hash",
        "resource_id",
        "resource_version",
        "required_approver_role",
        "expires_at",
        "version",
    } <= columns


def test_non_system_approval_requester_requires_an_identity() -> None:
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in ApprovalRequest.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    expression = checks["approval_requests_requester_identity_check"]
    assert "requester_type" in expression
    assert "system" in expression
    assert "requester_id IS NOT NULL" in expression


def test_approval_decider_identity_and_admin_role_are_database_enforced() -> None:
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in ApprovalRequest.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    pair = checks["approval_requests_decider_identity_pair_check"]
    decided_state = checks["approval_requests_decided_state_identity_check"]
    role = checks["approval_requests_required_role_check"]
    assert "decided_by_actor_type IS NULL" in pair
    assert "decided_by_actor_id IS NULL" in pair
    assert "decided_by_actor_type IS NOT NULL" in pair
    assert "decided_by_actor_id IS NOT NULL" in pair
    assert "approved" in decided_state
    assert "rejected" in decided_state
    assert "consumed" in decided_state
    assert "tenant_admin" in role
    assert "platform_admin" in role
    assert "'user'" not in role

    risk_role = checks["approval_requests_risk_role_check"]
    committed_grant = checks["approval_requests_committed_grant_check"]
    high_risk_operation = checks["approval_requests_high_risk_operation_check"]
    assert "risk_level = 'R4'" in risk_role
    assert "required_approver_role = 'platform_admin'" in risk_role
    assert "R2" in risk_role
    assert "R3" in risk_role
    assert "required_approver_role = 'tenant_admin'" in risk_role
    assert "grant_id IS NOT NULL" in committed_grant
    assert "operation_id IS NOT NULL" in high_risk_operation

    public_role = ApprovalRead.model_json_schema()["properties"]["required_approver_role"]
    assert set(public_role["enum"]) == {"tenant_admin", "platform_admin"}


def test_operation_state_machine_persists_authorization_and_compensation_states() -> None:
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in OperationRecord.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    expression = checks["operations_state_check"]
    assert "pending_approval" in expression
    assert "compensating" in expression
    assert "compensated" in expression
    assert "deadline_at" in OperationRecord.__table__.columns


def test_operation_and_approval_records_are_versioned() -> None:
    for model in (OperationRecord, ApprovalRequest, IdempotencyRecord):
        version = model.__table__.columns.version
        assert version.nullable is False
