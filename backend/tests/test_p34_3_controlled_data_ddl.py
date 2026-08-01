from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from omnibase.control_plane.models import OperationRecord
from omnibase.controlled_data.ddl import (
    ApplyExpectedVersions,
    DDLApprovalError,
    DDLAuthorizationError,
    DDLContractError,
    DDLVersionConflict,
    RiskPolicy,
    authorize_apply,
    build_ddl,
    build_ddl_preview,
    compile_postgresql,
    load_apply_records_for_update,
    validate_plan,
)
from omnibase.controlled_data.ddl_contracts import (
    AddNullableColumnPlanDefinition,
    ApprovalGrant,
    CreateBtreeIndexPlanDefinition,
    CreateTablePlanDefinition,
    DDLPlan,
    LiveAuthorization,
    RenameColumnDisplayPlanDefinition,
    RenameTableDisplayPlanDefinition,
    TrustedAuthorizationSnapshot,
    TrustedColumnLocator,
    TrustedTableLocator,
    canonical_plan_hash,
)
from omnibase.controlled_data.identifiers import (
    column_identifier,
    index_identifier,
    table_identifier,
)
from omnibase.controlled_data.models import (
    AuthorizationContext,
    DataIndexBinding,
    OperationCompensation,
    OperationDispatchOutbox,
    SchemaChangePlan,
)
from omnibase.controlled_data.operation_service import (
    ApplyConflict,
    AutomaticRetryForbidden,
    CompensationFailure,
    OperationStateError,
    claim_schema_apply,
    fail_compensation,
    mark_apply_failed,
    mark_apply_started,
    mark_apply_succeeded,
    queue_schema_apply,
    register_create_table,
    start_compensation,
)
from omnibase.controlled_data.tenant_models import ControlledDataOperationPayload
from omnibase.db.models import Tenant
from omnibase.db.tenant import User

ZERO_HASH = "0" * 64


def _column(column_id: UUID | None = None, *, display_name: str = "Title") -> dict[str, object]:
    return {
        "id": column_id or uuid4(),
        "display_name": display_name,
        "data_type": {"type": "string", "args": {"max_length": 200}},
        "nullable": True,
    }


def _scope() -> tuple[UUID, UUID, UUID, UUID, UUID, UUID]:
    return uuid4(), uuid4(), uuid4(), uuid4(), uuid4(), uuid4()


def _plan(kind: str, definition: object, scope: tuple[UUID, ...]) -> DDLPlan:
    tenant, workspace, resource, table_binding, authorization, operation = scope
    draft = DDLPlan(
        tenant_id=tenant,
        workspace_id=workspace,
        resource_id=resource,
        table_binding_id=table_binding,
        authorization_context_id=authorization,
        operation_id=operation,
        kind=kind,
        base_version=3,
        request_hash=ZERO_HASH,
        definition=definition,
    )
    return draft.model_copy(update={"request_hash": canonical_plan_hash(draft)})


def _locator(
    scope: tuple[UUID, ...],
    *,
    state: str = "active",
    columns: tuple[TrustedColumnLocator, ...] = (),
    version: int = 3,
    schema: str = "tenant_1234abcd",
) -> TrustedTableLocator:
    tenant, workspace, resource, table_binding, _, _ = scope
    return TrustedTableLocator(
        tenant_id=tenant,
        workspace_id=workspace,
        resource_id=resource,
        table_binding_id=table_binding,
        schema_name=schema,
        physical_table_name=table_identifier(resource),
        resource_version=version,
        state=state,
        policy_class="workspace_private",
        columns=columns,
    )


def _snapshot(plan: DDLPlan, actor_user_id: UUID) -> TrustedAuthorizationSnapshot:
    return TrustedAuthorizationSnapshot(
        id=plan.authorization_context_id,
        tenant_id=plan.tenant_id,
        workspace_id=plan.workspace_id,
        actor_user_id=actor_user_id,
        actions=frozenset({"data.schema.apply"}),
        resource_ids=frozenset({plan.resource_id}),
        source_version=2,
        snapshot_hash="c" * 64,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


def _live(plan: DDLPlan, actor_user_id: UUID) -> LiveAuthorization:
    return LiveAuthorization(
        tenant_id=plan.tenant_id,
        workspace_id=plan.workspace_id,
        actor_user_id=actor_user_id,
        actions=frozenset({"data.schema.apply"}),
        resource_ids=frozenset({plan.resource_id}),
        source_version=2,
        checked_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    "definition_type,payload",
    [
        (CreateTablePlanDefinition, {"display_name": "T", "columns": [_column()], "sql": "DROP"}),
        (AddNullableColumnPlanDefinition, {"column": _column(), "default": "now()"}),
        (RenameTableDisplayPlanDefinition, {"display_name": "T", "physical_name": "users"}),
        (
            RenameColumnDisplayPlanDefinition,
            {"column_id": uuid4(), "display_name": "C", "expression": "lower(x)"},
        ),
        (
            CreateBtreeIndexPlanDefinition,
            {
                "index_id": uuid4(),
                "display_name": "I",
                "column_ids": [uuid4()],
                "unique": True,
            },
        ),
    ],
)
def test_ddl_contracts_reject_sql_and_advanced_features(
    definition_type: type[object], payload: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        definition_type.model_validate(payload)  # type: ignore[attr-defined]


@pytest.mark.parametrize("method", ["gin", "gist", "hash"])
def test_index_method_is_fixed_to_btree(method: str) -> None:
    with pytest.raises(ValidationError):
        CreateBtreeIndexPlanDefinition(
            index_id=uuid4(),
            display_name="I",
            column_ids=[uuid4()],
            method=method,  # type: ignore[arg-type]
        )


def test_display_name_injection_never_becomes_identifier() -> None:
    scope = _scope()
    injected = 'Pretty"; DROP TABLE accounts; --'
    definition = CreateTablePlanDefinition(display_name=injected, columns=[_column()])
    validated = validate_plan(
        _plan("create_table", definition, scope),
        _locator(
            scope,
            state="pending",
            columns=(
                TrustedColumnLocator(
                    definition.columns[0].id,
                    column_identifier(definition.columns[0].id),
                    state="pending",
                    display_name=definition.columns[0].display_name,
                ),
            ),
        ),
    )
    sql = compile_postgresql(build_ddl_preview(validated).statements[0])
    assert injected not in sql
    assert table_identifier(scope[2]) in sql
    assert "DROP TABLE" not in sql


def test_trusted_locator_rejects_identifier_and_schema_injection() -> None:
    scope = _scope()
    definition = RenameTableDisplayPlanDefinition(before_display_name="Table", display_name="Safe")
    plan = _plan("rename_table_display", definition, scope)
    locator = _locator(scope)
    with pytest.raises(DDLContractError):
        validate_plan(
            plan,
            replace(locator, physical_table_name='x"; DROP TABLE x; --'),
        )
    with pytest.raises(DDLContractError):
        validate_plan(plan, _locator(scope, schema='tenant_x";DROP'))


def test_plan_scope_and_version_are_bound() -> None:
    scope = _scope()
    plan = _plan(
        "rename_table_display",
        RenameTableDisplayPlanDefinition(before_display_name="Table", display_name="N"),
        scope,
    )
    other_scope = (uuid4(), *scope[1:])
    with pytest.raises(DDLAuthorizationError):
        validate_plan(plan, _locator(other_scope))
    with pytest.raises(DDLVersionConflict):
        validate_plan(plan, _locator(scope, version=4))
    forged = plan.model_copy(update={"workspace_id": uuid4()})
    with pytest.raises(DDLContractError, match="hash"):
        validate_plan(forged, _locator(scope))


def test_index_columns_must_be_active_members() -> None:
    scope = _scope()
    active, archived, missing = uuid4(), uuid4(), uuid4()
    locator = _locator(
        scope,
        columns=(
            TrustedColumnLocator(active, column_identifier(active)),
            TrustedColumnLocator(archived, column_identifier(archived), state="archived"),
        ),
    )
    for bad in (archived, missing):
        plan = _plan(
            "create_btree_index",
            CreateBtreeIndexPlanDefinition(
                index_id=uuid4(), display_name="I", column_ids=[active, bad]
            ),
            scope,
        )
        with pytest.raises(DDLContractError, match="active table members"):
            validate_plan(plan, locator)
    with pytest.raises(ValidationError):
        CreateBtreeIndexPlanDefinition(
            index_id=uuid4(), display_name="I", column_ids=[active, active]
        )


def test_builders_use_deterministic_identifiers_and_rename_is_metadata_only() -> None:
    scope = _scope()
    active = uuid4()
    locator = _locator(scope, columns=(TrustedColumnLocator(active, column_identifier(active)),))
    new_column = uuid4()
    add = _plan(
        "add_nullable_column",
        AddNullableColumnPlanDefinition(column=_column(new_column)),
        scope,
    )
    add_sql = compile_postgresql(build_ddl_preview(validate_plan(add, locator)).statements[0])
    assert column_identifier(new_column) in add_sql
    assert "ADD COLUMN" in add_sql
    index_id = uuid4()
    index_plan = _plan(
        "create_btree_index",
        CreateBtreeIndexPlanDefinition(
            index_id=index_id, display_name="Search", column_ids=[active]
        ),
        scope,
    )
    index_sql = compile_postgresql(
        build_ddl_preview(validate_plan(index_plan, locator)).statements[0]
    )
    assert index_identifier(index_id) in index_sql
    rename = _plan(
        "rename_column_display",
        RenameColumnDisplayPlanDefinition(
            column_id=active,
            before_display_name="Column",
            display_name="Human Name",
        ),
        scope,
    )
    result = build_ddl_preview(validate_plan(rename, locator))
    assert result.statements == ()
    assert result.metadata_changes[0].display_name == "Human Name"
    assert result.compensations[0].target_logical_id == active
    assert result.compensations[0].before_display_name == "Column"


def test_r2_apply_requires_exact_live_approval() -> None:
    scope = _scope()
    member = uuid4()
    locator = _locator(scope, columns=(TrustedColumnLocator(member, column_identifier(member)),))
    plan = _plan(
        "create_btree_index",
        CreateBtreeIndexPlanDefinition(index_id=uuid4(), display_name="I", column_ids=[member]),
        scope,
    )
    validated = validate_plan(plan, locator)
    actor_user_id = uuid4()
    snapshot = _snapshot(plan, actor_user_id)
    live = _live(plan, actor_user_id)
    assert validated.risk_level == "R2"
    assert validated.requires_approval
    with pytest.raises(DDLApprovalError):
        authorize_apply(
            validated,
            live_locator=locator,
            authorization_snapshot=snapshot,
            live_authorization=live,
            expected_actor_user_id=actor_user_id,
            expected_plan_approval_id=None,
            expected_operation_approval_id=None,
            plan_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            operation_deadline_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    approval_id = uuid4()
    approval = ApprovalGrant(
        id=approval_id,
        tenant_id=plan.tenant_id,
        workspace_id=plan.workspace_id,
        requester_id=actor_user_id,
        resource_id=plan.resource_id,
        operation_id=plan.operation_id,
        grant_id=uuid4(),
        action="data.schema.apply",
        request_hash=plan.request_hash,
        resource_version=plan.base_version,
        risk_level="R2",
        required_approver_role="tenant_admin",
        state="consumed",
        version=3,
        decided_by_actor_type="user",
        decided_by_actor_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        consumed_at=datetime.now(UTC),
    )
    authorized = authorize_apply(
        validated,
        live_locator=locator,
        authorization_snapshot=snapshot,
        live_authorization=live,
        expected_actor_user_id=actor_user_id,
        expected_plan_approval_id=approval_id,
        expected_operation_approval_id=approval_id,
        plan_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        operation_deadline_at=datetime.now(UTC) + timedelta(minutes=5),
        approval=approval,
    )
    assert build_ddl(authorized).statements
    for field, bad in (
        ("request_hash", "f" * 64),
        ("resource_version", 4),
        ("resource_id", uuid4()),
        ("operation_id", uuid4()),
        ("risk_level", "R1"),
        ("state", "approved"),
    ):
        with pytest.raises(DDLApprovalError):
            authorize_apply(
                validated,
                live_locator=locator,
                authorization_snapshot=snapshot,
                live_authorization=live,
                expected_actor_user_id=actor_user_id,
                expected_plan_approval_id=approval_id,
                expected_operation_approval_id=approval_id,
                plan_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                operation_deadline_at=datetime.now(UTC) + timedelta(minutes=5),
                approval=replace(approval, **{field: bad}),  # type: ignore[arg-type]
            )


def test_snapshot_alone_and_cross_tenant_live_auth_are_rejected() -> None:
    scope = _scope()
    plan = _plan(
        "rename_table_display",
        RenameTableDisplayPlanDefinition(before_display_name="Table", display_name="N"),
        scope,
    )
    locator = _locator(scope)
    validated = validate_plan(plan, locator)
    actor_user_id = uuid4()
    snapshot = _snapshot(plan, actor_user_id)
    with pytest.raises(DDLAuthorizationError):
        authorize_apply(
            validated,
            live_locator=locator,
            authorization_snapshot=snapshot,
            live_authorization=None,
            expected_actor_user_id=actor_user_id,
            expected_plan_approval_id=None,
            expected_operation_approval_id=None,
            plan_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            operation_deadline_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    bad = replace(_live(plan, actor_user_id), tenant_id=uuid4())
    with pytest.raises(DDLAuthorizationError):
        authorize_apply(
            validated,
            live_locator=locator,
            authorization_snapshot=snapshot,
            live_authorization=bad,
            expected_actor_user_id=actor_user_id,
            expected_plan_approval_id=None,
            expected_operation_approval_id=None,
            plan_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            operation_deadline_at=datetime.now(UTC) + timedelta(minutes=5),
        )


def test_stricter_risk_policy_and_authorization_snapshot_are_frozen() -> None:
    scope = _scope()
    plan = _plan(
        "rename_table_display",
        RenameTableDisplayPlanDefinition(before_display_name="Table", display_name="N"),
        scope,
    )
    locator = _locator(scope)
    validated = validate_plan(plan, locator, risk_policy=RiskPolicy("R0"))
    actor_user_id = uuid4()
    snapshot = _snapshot(plan, actor_user_id)
    approval_id = uuid4()
    approval = ApprovalGrant(
        id=approval_id,
        tenant_id=plan.tenant_id,
        workspace_id=plan.workspace_id,
        requester_id=actor_user_id,
        resource_id=plan.resource_id,
        operation_id=plan.operation_id,
        grant_id=uuid4(),
        action="data.schema.apply",
        request_hash=plan.request_hash,
        resource_version=plan.base_version,
        risk_level="R0",
        required_approver_role="tenant_admin",
        state="consumed",
        version=3,
        decided_by_actor_type="user",
        decided_by_actor_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        consumed_at=datetime.now(UTC),
    )
    authorize_apply(
        validated,
        live_locator=locator,
        authorization_snapshot=snapshot,
        live_authorization=_live(plan, actor_user_id),
        expected_actor_user_id=actor_user_id,
        expected_plan_approval_id=approval_id,
        expected_operation_approval_id=approval_id,
        plan_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        operation_deadline_at=datetime.now(UTC) + timedelta(minutes=5),
        approval=approval,
    )
    with pytest.raises(DDLAuthorizationError):
        authorize_apply(
            validated,
            live_locator=locator,
            authorization_snapshot=replace(
                snapshot, expires_at=datetime.now(UTC) - timedelta(seconds=1)
            ),
            live_authorization=_live(plan, actor_user_id),
            expected_actor_user_id=actor_user_id,
            expected_plan_approval_id=approval_id,
            expected_operation_approval_id=approval_id,
            plan_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            operation_deadline_at=datetime.now(UTC) + timedelta(minutes=5),
            approval=approval,
        )


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, instance: object) -> None:
        self.added.append(instance)

    def flush(self) -> None:
        for item in self.added:
            if getattr(item, "id", None) is None:
                item.id = str(uuid4())


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one(self) -> object:
        return self.value

    def one(self) -> object:
        return self.value

    def scalars(self) -> tuple[object, ...]:
        assert isinstance(self.value, tuple)
        return self.value


class _SequenceSession:
    def __init__(self, values: list[object]) -> None:
        self.values = values
        self.lock_order: list[str] = []
        self.lock_sql: list[str] = []

    def execute(self, statement: object) -> _Result:
        if getattr(statement, "_for_update_arg", None) is not None:
            entity = statement.column_descriptions[0]["entity"]  # type: ignore[attr-defined]
            entity_class = getattr(entity, "class_", entity)
            entity_name = entity_class.__name__
            if entity_name.startswith("aliased("):
                entity_name = entity_name.removeprefix("aliased(").removesuffix(")")
            self.lock_order.append(entity_name)
            self.lock_sql.append(str(statement.compile(dialect=postgresql.dialect())))
        return _Result(self.values.pop(0))


def test_create_table_registration_generates_only_server_owned_physical_ids() -> None:
    session = _FakeSession()
    tenant_id, workspace_id, _, _, authorization_id, _ = _scope()
    definition = CreateTablePlanDefinition(
        display_name="Customer Notes",
        columns=[_column(), _column(display_name="Created")],
    )
    result = register_create_table(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_user_id=uuid4(),
        authorization_context_id=authorization_id,
        policy_class="workspace_private",
        definition=definition,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    resource_id = UUID(result.resource.id)
    assert result.resource.state == "provisioning"
    assert result.table_binding.state == "pending"
    assert result.table_binding.physical_table_name == table_identifier(resource_id)
    assert tuple(item.ordinal for item in result.column_bindings) == (1, 2)
    assert all(
        item.physical_column_name == column_identifier(item.id) for item in result.column_bindings
    )
    assert result.operation.resource_id == result.resource.id
    assert result.plan.operation_id == result.operation.id
    persisted = DDLPlan.model_validate(result.plan.normalized_spec, strict=False)
    assert persisted.request_hash == result.operation.request_hash == result.plan.request_hash
    assert "schema_name" not in CreateTablePlanDefinition.model_fields
    assert "physical_table_name" not in CreateTablePlanDefinition.model_fields


def _locked_aggregate_rows(
    *, cross_wire: bool = False
) -> tuple[list[object], ApplyExpectedVersions]:
    registration_session = _FakeSession()
    tenant_id, workspace_id, _, _, authorization_id, _ = _scope()
    actor_id = uuid4()
    definition = CreateTablePlanDefinition(
        display_name="Locked Table",
        columns=[_column(), _column(display_name="Value")],
    )
    registration = register_create_table(
        registration_session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        authorization_context_id=authorization_id,
        policy_class="workspace_private",
        definition=definition,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    tenant = Tenant(
        id=str(tenant_id),
        name="Tenant",
        schema_name="tenant_1234abcd",
        slug=f"tenant-{tenant_id.hex[:8]}",
        is_default=False,
        is_active=True,
    )
    user = User(
        id=str(actor_id),
        email="admin@example.test",
        password_hash=uuid4().hex,
        is_tenant_admin=True,
        is_active=True,
    )
    authorization = AuthorizationContext(
        id=str(authorization_id),
        tenant_id=str(tenant_id),
        workspace_id=str(workspace_id),
        source="user_rbac",
        actor_user_id=str(actor_id),
        grant_id=None,
        role_snapshot=["tenant_admin"],
        actions=["data.schema.apply"],
        resource_ids=[registration.resource.id],
        source_version=2,
        snapshot_hash="c" * 64,
        live_recheck_required=True,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    payload = ControlledDataOperationPayload(
        id=str(uuid4()),
        operation_id=registration.operation.id,
        plan_id=registration.plan.id,
        payload_kind="schema_change",
        normalized_payload=registration.plan.normalized_spec,
        request_hash=("d" * 64 if cross_wire else registration.plan.request_hash),
        state="pending",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    outbox = OperationDispatchOutbox(
        id=str(uuid4()),
        tenant_id=str(tenant_id),
        operation_id=registration.operation.id,
        plan_id=registration.plan.id,
        payload_id=payload.id,
        event_type="schema_change",
        dedupe_key="e" * 64,
        state="pending",
        attempt_count=0,
        max_attempts=1,
    )
    columns = tuple(registration.column_bindings)
    indexes: tuple[DataIndexBinding, ...] = ()
    expected = ApplyExpectedVersions(
        resource=registration.resource.version,
        table_binding=registration.table_binding.version,
        authorization_source=authorization.source_version,
        operation=registration.operation.version,
        plan=registration.plan.version,
        columns=tuple((UUID(item.id), item.version) for item in columns),
    )
    values: list[object] = [
        SimpleNamespace(
            id=registration.plan.id,
            tenant_id=registration.plan.tenant_id,
            authorization_context_id=registration.plan.authorization_context_id,
            operation_id=registration.plan.operation_id,
            normalized_spec=registration.plan.normalized_spec,
        ),
        authorization.actor_user_id,
        SimpleNamespace(
            actor_type=registration.operation.actor_type,
            actor_id=registration.operation.actor_id,
        ),
        tenant,
        user,
        registration.resource,
        registration.table_binding,
        columns,
        indexes,
        authorization,
        registration.operation,
        registration.plan,
        payload,
        outbox,
    ]
    return values, expected


def test_apply_aggregate_locks_in_global_order_and_rebuilds_from_rows() -> None:
    values, expected = _locked_aggregate_rows()
    plan = values[0]
    session = _SequenceSession(values)
    aggregate = load_apply_records_for_update(
        session,
        tenant_id=UUID(plan.tenant_id),  # type: ignore[attr-defined]
        plan_id=UUID(plan.id),  # type: ignore[attr-defined]
        expected=expected,
    )
    assert session.lock_order == [
        "Tenant",
        "User",
        "ResourceRecord",
        "DataTableBinding",
        "DataColumnBinding",
        "DataIndexBinding",
        "AuthorizationContext",
        "OperationRecord",
        "SchemaChangePlan",
        "ControlledDataOperationPayload",
        "OperationDispatchOutbox",
    ]
    assert aggregate.validated.plan.resource_id == UUID(aggregate.resource.id)
    assert aggregate.validated.locator.schema_name == aggregate.tenant.schema_name
    assert "tenant_1234abcd.users" in session.lock_sql[1]
    assert "tenant_1234abcd.controlled_data_operation_payloads" in session.lock_sql[-2]
    live = aggregate.user_rbac_live_authorization(checked_at=datetime.now(UTC))
    assert live.active
    assert live.resource_ids == frozenset({UUID(aggregate.resource.id)})


def test_apply_aggregate_rejects_cross_wired_payload_after_locking() -> None:
    values, expected = _locked_aggregate_rows(cross_wire=True)
    plan = values[0]
    session = _SequenceSession(values)
    with pytest.raises(DDLAuthorizationError, match="cross-wired"):
        load_apply_records_for_update(
            session,
            tenant_id=UUID(plan.tenant_id),  # type: ignore[attr-defined]
            plan_id=UUID(plan.id),  # type: ignore[attr-defined]
            expected=expected,
        )


def test_apply_aggregate_rejects_changed_column_version_set() -> None:
    values, expected = _locked_aggregate_rows()
    plan = values[0]
    first_id, first_version = expected.columns[0]
    stale = replace(
        expected,
        columns=((first_id, first_version + 1), *expected.columns[1:]),
    )
    session = _SequenceSession(values)
    with pytest.raises(DDLVersionConflict, match="column binding set or version"):
        load_apply_records_for_update(
            session,
            tenant_id=UUID(plan.tenant_id),  # type: ignore[attr-defined]
            plan_id=UUID(plan.id),  # type: ignore[attr-defined]
            expected=stale,
        )


def _records() -> tuple[OperationRecord, SchemaChangePlan]:
    tenant, workspace, resource, table_binding, authorization, operation_id = _scope()
    operation = OperationRecord(
        id=str(operation_id),
        tenant_id=str(tenant),
        workspace_id=str(workspace),
        actor_type="user",
        actor_id=str(uuid4()),
        resource_id=str(resource),
        resource_version=3,
        request_hash="a" * 64,
        kind="data.schema.apply",
        state="queued",
        risk_level="R1",
        progress=0,
        attempt_count=0,
        version=1,
        operation_metadata={},
    )
    plan = SchemaChangePlan(
        id=str(uuid4()),
        tenant_id=str(tenant),
        workspace_id=str(workspace),
        table_binding_id=str(table_binding),
        authorization_context_id=str(authorization),
        operation_id=str(operation_id),
        kind="add_nullable_column",
        normalized_spec={"kind": "add_nullable_column"},
        request_hash="a" * 64,
        base_version=3,
        risk_level="R1",
        requires_approval=False,
        state="validated",
        version=1,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    return operation, plan


def test_queue_is_idempotent_and_conflicting_replay_fails() -> None:
    session = _FakeSession()
    operation, plan = _records()
    payload, outbox, replay = queue_schema_apply(
        session,
        tenant_id=operation.tenant_id,
        operation=operation,
        plan=plan,
        normalized_payload={"kind": "add_nullable_column"},
        request_hash="a" * 64,
        dedupe_key="b" * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    assert not replay
    assert outbox.max_attempts == 1
    same = queue_schema_apply(
        session,
        tenant_id=operation.tenant_id,
        operation=operation,
        plan=plan,
        normalized_payload={"kind": "add_nullable_column"},
        request_hash="a" * 64,
        dedupe_key="b" * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        existing_outbox=outbox,
        existing_payload=payload,
    )
    assert same == (payload, outbox, True)
    with pytest.raises(ApplyConflict):
        queue_schema_apply(
            session,
            tenant_id=operation.tenant_id,
            operation=operation,
            plan=plan,
            normalized_payload={"kind": "create_table"},
            request_hash="a" * 64,
            dedupe_key="b" * 64,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            existing_outbox=outbox,
            existing_payload=payload,
        )


def test_queue_rejects_payload_drift_and_nested_sensitive_keys() -> None:
    session = _FakeSession()
    operation, plan = _records()
    with pytest.raises(ApplyConflict):
        queue_schema_apply(
            session,
            tenant_id=operation.tenant_id,
            operation=operation,
            plan=plan,
            normalized_payload={"kind": "create_table"},
            request_hash="a" * 64,
            dedupe_key="b" * 64,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    plan.normalized_spec = {"kind": "add_nullable_column", "definition": {"raw_sql": "DROP"}}
    with pytest.raises(ValueError, match="forbidden sensitive key"):
        queue_schema_apply(
            session,
            tenant_id=operation.tenant_id,
            operation=operation,
            plan=plan,
            normalized_payload=plan.normalized_spec,
            request_hash="a" * 64,
            dedupe_key="b" * 64,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )


def test_claim_requires_row_lock_and_state_records_cannot_be_cross_wired() -> None:
    session = _FakeSession()
    operation, plan = _records()
    payload, outbox, _ = queue_schema_apply(
        session,
        tenant_id=operation.tenant_id,
        operation=operation,
        plan=plan,
        normalized_payload=plan.normalized_spec,
        request_hash="a" * 64,
        dedupe_key="b" * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    lease_expiry = datetime.now(UTC) + timedelta(minutes=1)
    with pytest.raises(OperationStateError, match="row-locked"):
        claim_schema_apply(
            outbox,
            worker_id="worker-1",
            lease_expires_at=lease_expiry,
            lock_held=False,
        )
    assert claim_schema_apply(
        outbox,
        worker_id="worker-1",
        lease_expires_at=lease_expiry,
        lock_held=True,
    )
    outbox.operation_id = str(uuid4())
    with pytest.raises(ApplyConflict, match="do not bind"):
        mark_apply_started(operation, plan, payload, outbox, worker_id="worker-1")


def test_apply_start_requires_current_lease_owner_even_on_replay() -> None:
    session = _FakeSession()
    operation, plan = _records()
    payload, outbox, _ = queue_schema_apply(
        session,
        tenant_id=operation.tenant_id,
        operation=operation,
        plan=plan,
        normalized_payload=plan.normalized_spec,
        request_hash="a" * 64,
        dedupe_key="b" * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    assert claim_schema_apply(
        outbox,
        worker_id="worker-1",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        lock_held=True,
    )

    with pytest.raises(OperationStateError, match="lease owner"):
        mark_apply_started(operation, plan, payload, outbox, worker_id="worker-2")

    assert mark_apply_started(operation, plan, payload, outbox, worker_id="worker-1")
    with pytest.raises(OperationStateError, match="lease owner"):
        mark_apply_started(operation, plan, payload, outbox, worker_id="worker-2")
    assert not mark_apply_started(operation, plan, payload, outbox, worker_id="worker-1")


def test_result_ref_rejects_sensitive_nested_metadata() -> None:
    operation, plan = _records()
    payload = ControlledDataOperationPayload(
        id=str(uuid4()),
        operation_id=operation.id,
        plan_id=plan.id,
        payload_kind="schema_change",
        normalized_payload=plan.normalized_spec,
        request_hash="a" * 64,
        state="claimed",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    outbox = OperationDispatchOutbox(
        tenant_id=operation.tenant_id,
        operation_id=operation.id,
        plan_id=plan.id,
        payload_id=payload.id,
        event_type="schema_change",
        dedupe_key="b" * 64,
        state="leased",
        attempt_count=1,
        max_attempts=1,
        lease_owner="worker-1",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    operation.state, plan.state = "running", "applying"
    with pytest.raises(ValueError, match="forbidden sensitive key"):
        mark_apply_succeeded(
            operation,
            plan,
            payload,
            outbox,
            worker_id="worker-1",
            result_ref={"diagnostic": {"schemaName": "tenant_secret"}},
        )


def test_apply_failure_cannot_be_retried_or_reported_as_success() -> None:
    session = _FakeSession()
    operation, plan = _records()
    payload, outbox, _ = queue_schema_apply(
        session,
        tenant_id=operation.tenant_id,
        operation=operation,
        plan=plan,
        normalized_payload={"kind": "add_nullable_column"},
        request_hash="a" * 64,
        dedupe_key="b" * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    assert claim_schema_apply(
        outbox,
        worker_id="worker-1",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        lock_held=True,
    )
    assert mark_apply_started(operation, plan, payload, outbox, worker_id="worker-1")
    assert operation.version == 2
    mark_apply_failed(
        operation,
        plan,
        payload,
        outbox,
        worker_id="worker-1",
        error_code="DDL_FAILED",
        compensation_required=False,
    )
    assert operation.version == 3
    with pytest.raises(AutomaticRetryForbidden):
        claim_schema_apply(
            outbox,
            worker_id="worker-2",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
            lock_held=True,
        )
    with pytest.raises(OperationStateError):
        mark_apply_succeeded(
            operation,
            plan,
            payload,
            outbox,
            worker_id="worker-1",
            result_ref={},
        )


def test_repeated_success_is_noop_and_compensation_failure_is_manual() -> None:
    operation, plan = _records()
    payload = ControlledDataOperationPayload(
        id=str(uuid4()),
        operation_id=operation.id,
        plan_id=plan.id,
        payload_kind="schema_change",
        normalized_payload={},
        request_hash="a" * 64,
        state="claimed",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    outbox = OperationDispatchOutbox(
        tenant_id=operation.tenant_id,
        operation_id=operation.id,
        plan_id=plan.id,
        payload_id=payload.id,
        event_type="schema_change",
        dedupe_key="b" * 64,
        state="leased",
        attempt_count=1,
        max_attempts=1,
    )
    operation.state, plan.state = "running", "applying"
    outbox.lease_owner = "worker-1"
    assert mark_apply_succeeded(
        operation,
        plan,
        payload,
        outbox,
        worker_id="worker-1",
        result_ref={"ok": True},
    )
    assert operation.version == 2
    assert not mark_apply_succeeded(
        operation,
        plan,
        payload,
        outbox,
        worker_id="worker-1",
        result_ref={"ok": True},
    )
    assert operation.version == 2

    operation.state, plan.state, payload.state = "compensating", "compensating", "claimed"
    step = OperationCompensation(
        tenant_id=operation.tenant_id,
        operation_id=operation.id,
        plan_id=plan.id,
        payload_id=payload.id,
        target_logical_id=str(uuid4()),
        plan_digest="a" * 64,
        resource_version=3,
        before_snapshot={},
        sequence=1,
        kind="drop_added_column",
        state="pending",
        attempt_count=0,
    )
    start_compensation(step)
    failure = fail_compensation(
        operation,
        plan,
        payload,
        step,
        error_code="COMPENSATION_FAILED",
    )
    assert isinstance(failure, CompensationFailure)
    assert plan.state == "manual_intervention_required"
    assert operation.state == "failed"
    assert operation.version == 3
    assert payload.state == "discarded"
    assert step.state == "manual_intervention_required"
