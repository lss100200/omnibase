"""Closed, non-executing DDL planning for controlled tenant data.

Only trusted logical-to-physical bindings may reach these builders.  The
module returns SQLAlchemy DDL elements and metadata intents; it never opens a
connection and never executes a statement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, TypeVar, cast
from uuid import UUID

from sqlalchemy import Column, Index, MetaData, Table, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, aliased
from sqlalchemy.schema import CreateIndex, CreateTable, DDLElement
from sqlalchemy.sql.type_api import TypeEngine

from omnibase.control_plane.models import ApprovalRequest, OperationRecord, ResourceRecord
from omnibase.controlled_data.ddl_contracts import (
    AddNullableColumnPlanDefinition,
    ApprovalGrant,
    AuthorizedDDLPlan,
    CreateBtreeIndexPlanDefinition,
    CreateTablePlanDefinition,
    DDLPlan,
    LiveAuthorization,
    RenameColumnDisplayPlanDefinition,
    RenameTableDisplayPlanDefinition,
    RiskLevel,
    TrustedAuthorizationSnapshot,
    TrustedColumnLocator,
    TrustedTableLocator,
    ValidatedDDLPlan,
    canonical_plan_hash,
)
from omnibase.controlled_data.identifiers import (
    column_identifier,
    index_identifier,
    table_identifier,
)
from omnibase.controlled_data.models import (
    AuthorizationContext,
    DataColumnBinding,
    DataIndexBinding,
    DataTableBinding,
    OperationDispatchOutbox,
    SchemaChangePlan,
)
from omnibase.controlled_data.tenant_models import ControlledDataOperationPayload
from omnibase.controlled_data.types import sqlalchemy_type, validate_type_spec
from omnibase.db.models import Tenant
from omnibase.db.tenant import User
from omnibase.tenants.schema_manager import SchemaError, validate_schema_name

_POLICY_CLASSES = frozenset({"workspace_private", "tenant_managed", "controlled_shared"})
_RISK_ORDER = {"R0": 0, "R1": 1, "R2": 2, "R3": 3, "R4": 4}
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_LIVE_AUTH_AGE = timedelta(seconds=30)
_RISK_BY_KIND: dict[str, RiskLevel] = {
    "rename_table_display": "R0",
    "rename_column_display": "R0",
    "create_table": "R1",
    "add_nullable_column": "R1",
    "create_btree_index": "R2",
}
_TenantModel = TypeVar("_TenantModel", User, ControlledDataOperationPayload)


class DDLContractError(ValueError):
    """A plan or trusted binding escaped the closed DDL contract."""


class DDLVersionConflict(DDLContractError):
    """The resource changed between plan, validation, and apply."""


class DDLAuthorizationError(DDLContractError):
    """Live authorization is absent, stale, or out of scope."""


class DDLApprovalError(DDLContractError):
    """A required approval is absent or does not bind the exact request."""


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    """Frozen policy may tighten R0/R1 but can never waive R2 approval."""

    require_approval_from: RiskLevel = "R2"

    def __post_init__(self) -> None:
        if self.require_approval_from not in _RISK_ORDER:
            raise DDLContractError("approval threshold is invalid")

    def requires_approval(self, risk_level: RiskLevel) -> bool:
        threshold = min(_RISK_ORDER[self.require_approval_from], _RISK_ORDER["R2"])
        return _RISK_ORDER[risk_level] >= threshold


@dataclass(frozen=True, slots=True)
class MetadataChange:
    target: Literal["table", "column"]
    logical_id: UUID
    display_name: str


@dataclass(frozen=True, slots=True)
class CompensationSpec:
    kind: Literal[
        "drop_created_table",
        "drop_added_column",
        "drop_created_index",
        "restore_display_name",
    ]
    resource_id: UUID
    target_logical_id: UUID
    plan_digest: str
    resource_version: int
    before_display_name: str | None = None


@dataclass(frozen=True, slots=True)
class DDLBuildResult:
    statements: tuple[DDLElement, ...]
    metadata_changes: tuple[MetadataChange, ...] = ()
    compensations: tuple[CompensationSpec, ...] = ()


class AddControlledColumn(DDLElement):
    """Server-owned ALTER TABLE ADD COLUMN element for PostgreSQL."""

    inherit_cache = False

    def __init__(self, table: Table, column: Column[object]) -> None:
        self.table = table
        self.column = column


@compiles(AddControlledColumn, "postgresql")
def _compile_add_column(element: AddControlledColumn, compiler: object, **kw: object) -> str:
    ddl_compiler = compiler
    preparer = ddl_compiler.preparer  # type: ignore[attr-defined]
    table_name = preparer.format_table(element.table)
    column_name = preparer.quote(element.column.name)
    type_sql = ddl_compiler.dialect.type_compiler.process(element.column.type)  # type: ignore[attr-defined]
    return f"ALTER TABLE {table_name} ADD COLUMN {column_name} {type_sql}"


def classify_risk(kind: str) -> RiskLevel:
    try:
        return _RISK_BY_KIND[kind]
    except KeyError as exc:
        raise DDLContractError("schema change kind is not in the closed allowlist") from exc


def _validate_locator(locator: TrustedTableLocator) -> None:
    try:
        validate_schema_name(locator.schema_name)
    except SchemaError as exc:
        raise DDLContractError("tenant schema binding is invalid") from exc
    if locator.physical_table_name != table_identifier(locator.resource_id):
        raise DDLContractError("table physical name is not deterministic")
    if locator.policy_class not in _POLICY_CLASSES:
        raise DDLContractError("table policy class is not mutable")
    if locator.state not in {"pending", "active"}:
        raise DDLContractError("table binding is not mutable")
    if locator.resource_version < 1:
        raise DDLContractError("resource version must be positive")
    if not 1 <= len(locator.display_name.strip()) <= 120:
        raise DDLContractError("table display name snapshot is invalid")
    seen: set[UUID] = set()
    for item in locator.columns:
        if item.id in seen:
            raise DDLContractError("trusted locator has duplicate columns")
        seen.add(item.id)
        if item.physical_name != column_identifier(item.id):
            raise DDLContractError("column physical name is not deterministic")
        if not 1 <= len(item.display_name.strip()) <= 120:
            raise DDLContractError("column display name snapshot is invalid")


def _require_active(locator: TrustedTableLocator, message: str) -> None:
    if locator.state != "active":
        raise DDLContractError(message)


def _validate_definition_membership(plan: DDLPlan, locator: TrustedTableLocator) -> None:
    members = {item.id for item in locator.columns if item.state == "active"}
    all_columns = {item.id for item in locator.columns}
    definition = plan.definition
    if isinstance(definition, CreateTablePlanDefinition):
        expected = {item.id for item in definition.columns}
        if (
            locator.state != "pending"
            or all_columns != expected
            or any(item.state != "pending" for item in locator.columns)
        ):
            raise DDLContractError("create_table requires exact pending server-registered columns")
        return
    if isinstance(definition, AddNullableColumnPlanDefinition):
        _require_active(locator, "column can only be added to an active table")
        if definition.column.id in all_columns:
            raise DDLContractError("column logical ID already belongs to this table")
        return
    if isinstance(definition, RenameColumnDisplayPlanDefinition):
        member = next(
            (item for item in locator.columns if item.id == definition.column_id),
            None,
        )
        if member is None or member.id not in members:
            raise DDLContractError("renamed column is not an active table member")
        if definition.before_display_name != member.display_name:
            raise DDLVersionConflict("column display name changed after planning")
        return
    if isinstance(definition, CreateBtreeIndexPlanDefinition):
        _require_active(locator, "index can only be added to an active table")
        if not set(definition.column_ids).issubset(members):
            raise DDLContractError("index columns must be active table members")
        return
    if isinstance(definition, RenameTableDisplayPlanDefinition):
        _require_active(locator, "display name can only be changed on an active table")
        if definition.before_display_name != locator.display_name:
            raise DDLVersionConflict("table display name changed after planning")
        return
    raise DDLContractError("unsupported plan definition")


def validate_plan(
    plan: DDLPlan,
    locator: TrustedTableLocator,
    *,
    risk_policy: RiskPolicy | None = None,
) -> ValidatedDDLPlan:
    """Validate immutable scope, membership, version, and canonical digest."""
    _validate_locator(locator)
    if canonical_plan_hash(plan) != plan.request_hash:
        raise DDLContractError("plan request hash does not match its canonical content")
    if (
        plan.tenant_id != locator.tenant_id
        or plan.workspace_id != locator.workspace_id
        or plan.resource_id != locator.resource_id
        or plan.table_binding_id != locator.table_binding_id
    ):
        raise DDLAuthorizationError("plan scope does not match the trusted locator")
    if plan.base_version != locator.resource_version:
        raise DDLVersionConflict("resource version changed after planning")

    _validate_definition_membership(plan, locator)

    risk = classify_risk(plan.kind)
    policy = risk_policy or RiskPolicy()
    return ValidatedDDLPlan(
        plan=plan,
        locator=locator,
        risk_level=risk,
        requires_approval=policy.requires_approval(risk),
        approval_policy_threshold=policy.require_approval_from,
        plan_digest=plan.request_hash,
    )


def _aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DDLAuthorizationError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def authorize_apply(
    validated: ValidatedDDLPlan,
    *,
    live_locator: TrustedTableLocator,
    authorization_snapshot: TrustedAuthorizationSnapshot,
    live_authorization: LiveAuthorization | None,
    expected_actor_user_id: UUID,
    expected_plan_approval_id: UUID | None,
    expected_operation_approval_id: UUID | None,
    plan_expires_at: datetime,
    operation_deadline_at: datetime | None,
    approval: ApprovalGrant | None = None,
    now: datetime | None = None,
) -> AuthorizedDDLPlan:
    """Fail closed unless live state still binds the exact validated request."""
    current_time = _aware(now or datetime.now(UTC), "now")
    if _aware(plan_expires_at, "plan expiry") <= current_time:
        raise DDLVersionConflict("schema change plan expired before apply")
    if (
        operation_deadline_at is not None
        and _aware(operation_deadline_at, "operation deadline") <= current_time
    ):
        raise DDLVersionConflict("operation deadline expired before apply")
    current = validate_plan(
        validated.plan,
        live_locator,
        risk_policy=RiskPolicy(validated.approval_policy_threshold),
    )
    if (
        current.plan_digest != validated.plan_digest
        or current.risk_level != validated.risk_level
        or current.requires_approval != validated.requires_approval
    ):
        raise DDLVersionConflict("validated plan no longer matches live state")
    plan = validated.plan
    snapshot_expires_at = _aware(authorization_snapshot.expires_at, "authorization expiry")
    snapshot_valid = (
        authorization_snapshot.id == plan.authorization_context_id
        and authorization_snapshot.tenant_id == plan.tenant_id
        and authorization_snapshot.workspace_id == plan.workspace_id
        and authorization_snapshot.actor_user_id == expected_actor_user_id
        and "data.schema.apply" in authorization_snapshot.actions
        and plan.resource_id in authorization_snapshot.resource_ids
        and authorization_snapshot.source_version >= 1
        and _DIGEST.fullmatch(authorization_snapshot.snapshot_hash) is not None
        and authorization_snapshot.live_recheck_required
        and snapshot_expires_at > current_time
    )
    if not snapshot_valid:
        raise DDLAuthorizationError("authorization snapshot is stale or out of scope")
    if live_authorization is None or not live_authorization.active:
        raise DDLAuthorizationError("live authorization is required")
    checked_at = _aware(live_authorization.checked_at, "live authorization check time")
    if (
        live_authorization.tenant_id != plan.tenant_id
        or live_authorization.workspace_id != plan.workspace_id
        or live_authorization.actor_user_id != authorization_snapshot.actor_user_id
        or "data.schema.apply" not in live_authorization.actions
        or plan.resource_id not in live_authorization.resource_ids
        or live_authorization.source_version < authorization_snapshot.source_version
        or checked_at > current_time
        or current_time - checked_at > _MAX_LIVE_AUTH_AGE
    ):
        raise DDLAuthorizationError("live authorization does not cover this operation")
    if not validated.requires_approval:
        if (
            expected_plan_approval_id is not None
            or expected_operation_approval_id is not None
            or approval is not None
        ):
            raise DDLApprovalError("unapproved plan unexpectedly carries an approval")
        return AuthorizedDDLPlan(
            validated=validated,
            authorization_context_id=authorization_snapshot.id,
            authorization_source_version=live_authorization.source_version,
            authorization_snapshot_hash=authorization_snapshot.snapshot_hash,
            approval_id=None,
            approval_version=None,
            authorized_at=current_time,
        )
    if (
        approval is None
        or expected_plan_approval_id is None
        or expected_operation_approval_id is None
    ):
        raise DDLApprovalError("this risk level requires approval")
    approval_expires_at = _aware(approval.expires_at, "approval expiry")
    consumed_at = _aware(approval.consumed_at, "approval consumption time")
    required_role = "platform_admin" if validated.risk_level == "R4" else "tenant_admin"
    expected = (
        approval.id == expected_plan_approval_id == expected_operation_approval_id
        and approval.tenant_id == plan.tenant_id
        and approval.workspace_id == plan.workspace_id
        and approval.requester_id == expected_actor_user_id
        and approval.resource_id == plan.resource_id
        and approval.operation_id == plan.operation_id
        and approval.action == "data.schema.apply"
        and approval.request_hash == plan.request_hash
        and approval.resource_version == live_locator.resource_version
        and approval.risk_level == validated.risk_level
        and approval.required_approver_role == required_role
        and approval.state == "consumed"
        and approval.version >= 3
        and approval.decided_by_actor_type in {"user", "system"}
        and approval.decided_by_actor_id != approval.requester_id
        and consumed_at <= current_time
        and consumed_at < approval_expires_at
        and approval_expires_at > current_time
    )
    if not expected:
        raise DDLApprovalError("approval does not bind the exact live request")
    return AuthorizedDDLPlan(
        validated=validated,
        authorization_context_id=authorization_snapshot.id,
        authorization_source_version=live_authorization.source_version,
        authorization_snapshot_hash=authorization_snapshot.snapshot_hash,
        approval_id=approval.id,
        approval_version=approval.version,
        authorized_at=current_time,
    )


def build_ddl(authorized: AuthorizedDDLPlan) -> DDLBuildResult:
    """Build executable DDL only from a sealed authorization result."""
    return _build_ddl(authorized.validated)


def build_ddl_preview(validated: ValidatedDDLPlan) -> DDLBuildResult:
    """Build non-executable review output before authorization."""
    return _build_ddl(validated)


def _build_ddl(validated: ValidatedDDLPlan) -> DDLBuildResult:
    plan = validated.plan
    locator = validated.locator
    definition = plan.definition
    metadata = MetaData()

    if isinstance(definition, CreateTablePlanDefinition):
        columns = [
            Column(
                column_identifier(item.id),
                sqlalchemy_type(validate_type_spec(item.data_type.type, item.data_type.args)),
                nullable=item.nullable,
            )
            for item in definition.columns
        ]
        target = Table(
            locator.physical_table_name,
            metadata,
            *columns,
            schema=locator.schema_name,
        )
        return DDLBuildResult(
            statements=(CreateTable(target),),
            metadata_changes=(
                MetadataChange("table", locator.resource_id, definition.display_name),
            ),
            compensations=(
                CompensationSpec(
                    "drop_created_table",
                    plan.resource_id,
                    locator.resource_id,
                    validated.plan_digest,
                    plan.base_version,
                ),
            ),
        )

    if isinstance(definition, AddNullableColumnPlanDefinition):
        target = Table(locator.physical_table_name, metadata, schema=locator.schema_name)
        item = definition.column
        new_column = Column(
            column_identifier(item.id),
            cast(
                "TypeEngine[object]",
                sqlalchemy_type(validate_type_spec(item.data_type.type, item.data_type.args)),
            ),
            nullable=True,
        )
        return DDLBuildResult(
            statements=(AddControlledColumn(target, new_column),),
            metadata_changes=(MetadataChange("column", item.id, item.display_name),),
            compensations=(
                CompensationSpec(
                    "drop_added_column",
                    plan.resource_id,
                    item.id,
                    validated.plan_digest,
                    plan.base_version,
                ),
            ),
        )
    if isinstance(definition, CreateBtreeIndexPlanDefinition):
        bound = {item.id: item for item in locator.columns}
        index_columns: list[Column[object]] = [
            Column(bound[item].physical_name) for item in definition.column_ids
        ]
        Table(
            locator.physical_table_name,
            metadata,
            *index_columns,
            schema=locator.schema_name,
        )
        index = Index(index_identifier(definition.index_id), *index_columns)
        return DDLBuildResult(
            statements=(CreateIndex(index),),
            compensations=(
                CompensationSpec(
                    "drop_created_index",
                    plan.resource_id,
                    definition.index_id,
                    validated.plan_digest,
                    plan.base_version,
                ),
            ),
        )
    if isinstance(definition, RenameTableDisplayPlanDefinition):
        return DDLBuildResult(
            statements=(),
            metadata_changes=(
                MetadataChange("table", locator.resource_id, definition.display_name),
            ),
            compensations=(
                CompensationSpec(
                    "restore_display_name",
                    plan.resource_id,
                    locator.resource_id,
                    validated.plan_digest,
                    plan.base_version,
                    before_display_name=definition.before_display_name,
                ),
            ),
        )
    if isinstance(definition, RenameColumnDisplayPlanDefinition):
        return DDLBuildResult(
            statements=(),
            metadata_changes=(
                MetadataChange("column", definition.column_id, definition.display_name),
            ),
            compensations=(
                CompensationSpec(
                    "restore_display_name",
                    plan.resource_id,
                    definition.column_id,
                    validated.plan_digest,
                    plan.base_version,
                    before_display_name=definition.before_display_name,
                ),
            ),
        )
    raise DDLContractError("unsupported plan definition")


def compile_postgresql(statement: DDLElement) -> str:
    """Test/audit helper; compilation still performs no database I/O."""
    return str(statement.compile(dialect=postgresql.dialect()))


@dataclass(frozen=True, slots=True)
class ApplyExpectedVersions:
    """Optimistic versions captured when the immutable plan is sealed."""

    resource: int
    table_binding: int
    authorization_source: int
    operation: int
    plan: int
    columns: tuple[tuple[UUID, int], ...]
    indexes: tuple[tuple[UUID, int], ...] = ()
    approval: int | None = None


@dataclass(frozen=True, slots=True)
class LockedApplyAggregate:
    """All rows needed by apply, locked in the global canonical order."""

    tenant: Tenant
    actor_user: User | None
    resource: ResourceRecord
    table_binding: DataTableBinding
    columns: tuple[DataColumnBinding, ...]
    indexes: tuple[DataIndexBinding, ...]
    authorization: AuthorizationContext
    operation: OperationRecord
    plan: SchemaChangePlan
    approval: ApprovalRequest | None
    payload: ControlledDataOperationPayload
    outbox: OperationDispatchOutbox
    validated: ValidatedDDLPlan

    def authorization_snapshot(self) -> TrustedAuthorizationSnapshot:
        return TrustedAuthorizationSnapshot(
            id=UUID(self.authorization.id),
            tenant_id=UUID(self.authorization.tenant_id),
            workspace_id=(
                UUID(self.authorization.workspace_id) if self.authorization.workspace_id else None
            ),
            actor_user_id=UUID(self.authorization.actor_user_id),
            actions=frozenset(self.authorization.actions),
            resource_ids=frozenset(UUID(item) for item in self.authorization.resource_ids),
            source_version=self.authorization.source_version,
            snapshot_hash=self.authorization.snapshot_hash,
            expires_at=self.authorization.expires_at,
            live_recheck_required=self.authorization.live_recheck_required,
        )

    def user_rbac_live_authorization(self, *, checked_at: datetime) -> LiveAuthorization:
        if self.authorization.source != "user_rbac" or self.actor_user is None:
            raise DDLAuthorizationError("user RBAC live authorization is unavailable")
        active = bool(self.tenant.is_active and self.actor_user.is_active)
        actions = (
            frozenset({"data.schema.apply"})
            if active and self.actor_user.is_tenant_admin
            else frozenset()
        )
        return LiveAuthorization(
            tenant_id=UUID(self.tenant.id),
            workspace_id=(
                UUID(self.authorization.workspace_id) if self.authorization.workspace_id else None
            ),
            actor_user_id=UUID(self.actor_user.id),
            actions=actions,
            resource_ids=frozenset({UUID(self.resource.id)}) if actions else frozenset(),
            source_version=self.authorization.source_version,
            checked_at=checked_at,
            active=active and bool(actions),
        )

    def approval_grant(self) -> ApprovalGrant | None:
        if self.approval is None:
            return None
        row = self.approval
        if (
            row.requester_id is None
            or row.resource_id is None
            or row.operation_id is None
            or row.grant_id is None
            or row.resource_version is None
            or row.decided_by_actor_type is None
            or row.decided_by_actor_id is None
            or row.consumed_at is None
        ):
            raise DDLApprovalError("locked approval is incomplete")
        return ApprovalGrant(
            id=UUID(row.id),
            tenant_id=UUID(row.tenant_id),
            workspace_id=UUID(row.workspace_id) if row.workspace_id else None,
            requester_id=UUID(row.requester_id),
            resource_id=UUID(row.resource_id),
            operation_id=UUID(row.operation_id),
            grant_id=UUID(row.grant_id),
            action=row.action,
            request_hash=row.request_hash,
            resource_version=row.resource_version,
            risk_level=row.risk_level,  # type: ignore[arg-type]
            required_approver_role=row.required_approver_role,  # type: ignore[arg-type]
            state=row.state,
            version=row.version,
            decided_by_actor_type=row.decided_by_actor_type,  # type: ignore[arg-type]
            decided_by_actor_id=UUID(row.decided_by_actor_id),
            expires_at=row.expires_at,
            consumed_at=row.consumed_at,
        )


def _version_map(rows: tuple[object, ...]) -> tuple[tuple[UUID, int], ...]:
    return tuple(sorted((UUID(row.id), row.version) for row in rows))  # type: ignore[attr-defined]


@dataclass(frozen=True, slots=True)
class _ApplyHints:
    authorization_context_id: str
    operation_id: str
    normalized_spec: dict[str, object]
    actor_user_id: str
    operation_actor_type: str
    operation_actor_id: str | None


def _load_apply_hints(session: Session, tenant_id: UUID, plan_id: UUID) -> _ApplyHints:
    plan_hint = session.execute(
        select(
            SchemaChangePlan.authorization_context_id,
            SchemaChangePlan.operation_id,
            SchemaChangePlan.normalized_spec,
        ).where(
            SchemaChangePlan.id == str(plan_id),
            SchemaChangePlan.tenant_id == str(tenant_id),
        )
    ).one()
    actor_user_id = session.execute(
        select(AuthorizationContext.actor_user_id).where(
            AuthorizationContext.id == plan_hint.authorization_context_id,
            AuthorizationContext.tenant_id == str(tenant_id),
        )
    ).scalar_one()
    operation_hint = session.execute(
        select(OperationRecord.actor_type, OperationRecord.actor_id).where(
            OperationRecord.id == plan_hint.operation_id,
            OperationRecord.tenant_id == str(tenant_id),
        )
    ).one()
    return _ApplyHints(
        plan_hint.authorization_context_id,
        plan_hint.operation_id,
        plan_hint.normalized_spec,
        actor_user_id,
        operation_hint.actor_type,
        operation_hint.actor_id,
    )


def _tenant_entity(model: type[_TenantModel], schema_name: str) -> type[_TenantModel]:
    try:
        validate_schema_name(schema_name)
    except SchemaError as exc:
        raise DDLAuthorizationError("locked tenant registry schema is invalid") from exc
    qualified = model.__table__.to_metadata(MetaData(), schema=schema_name)  # type: ignore[attr-defined]
    return aliased(model, qualified, adapt_on_names=True)


def _rebuild_locked_plan(
    *,
    tenant: Tenant,
    actor_user: User | None,
    resource: ResourceRecord,
    table: DataTableBinding,
    columns: tuple[DataColumnBinding, ...],
    indexes: tuple[DataIndexBinding, ...],
    authorization: AuthorizationContext,
    operation: OperationRecord,
    plan_row: SchemaChangePlan,
    approval: ApprovalRequest | None,
    payload: ControlledDataOperationPayload,
    outbox: OperationDispatchOutbox,
) -> ValidatedDDLPlan:
    try:
        plan = DDLPlan.model_validate(plan_row.normalized_spec, strict=False)
    except ValueError as exc:
        raise DDLContractError("persisted plan does not contain a closed DDL contract") from exc
    tenant_id = str(plan.tenant_id)
    workspace_id = str(plan.workspace_id) if plan.workspace_id else None
    expected = (
        tenant.id == tenant_id
        and resource.tenant_id == tenant_id
        and table.tenant_id == tenant_id
        and authorization.tenant_id == tenant_id
        and operation.tenant_id == tenant_id
        and plan_row.tenant_id == tenant_id
        and outbox.tenant_id == tenant_id
        and resource.id == str(plan.resource_id) == table.resource_id == operation.resource_id
        and table.id == str(plan.table_binding_id) == plan_row.table_binding_id
        and authorization.id
        == str(plan.authorization_context_id)
        == plan_row.authorization_context_id
        and operation.id == str(plan.operation_id) == plan_row.operation_id
        and (
            operation.actor_type != "user"
            or (
                actor_user is not None
                and operation.actor_id == actor_user.id == authorization.actor_user_id
            )
        )
        and plan_row.id == payload.plan_id == outbox.plan_id
        and operation.id == payload.operation_id == outbox.operation_id
        and payload.id == outbox.payload_id
        and plan.workspace_id == (UUID(table.workspace_id) if table.workspace_id else None)
        and workspace_id
        == authorization.workspace_id
        == operation.workspace_id
        == plan_row.workspace_id
        and resource.kind == "controlled_table"
        and resource.policy_class == table.policy_class
        and resource.version == table.resource_version == plan.base_version
        and plan_row.base_version == plan.base_version
        and operation.resource_version == plan.base_version
        and operation.request_hash
        == plan_row.request_hash
        == payload.request_hash
        == plan.request_hash
        and operation.risk_level == plan_row.risk_level == classify_risk(plan.kind)
        and operation.kind == "data.schema.apply"
        and operation.state == "queued"
        and plan_row.kind == plan.kind
        and plan_row.state in {"validated", "approved"}
        and payload.payload_kind == outbox.event_type == "schema_change"
        and payload.state == "pending"
        and outbox.state == "pending"
        and outbox.max_attempts == 1
        and outbox.attempt_count == 0
        and payload.normalized_payload == plan_row.normalized_spec
        and plan_row.approval_id == operation.approval_id
        and ((approval is None and plan_row.approval_id is None) or approval is not None)
    )
    if not expected:
        raise DDLAuthorizationError("locked apply rows are cross-wired or stale")
    if any(row.tenant_id != tenant_id or row.table_binding_id != table.id for row in columns):
        raise DDLAuthorizationError("locked columns are cross-wired")
    if any(row.tenant_id != tenant_id or row.table_binding_id != table.id for row in indexes):
        raise DDLAuthorizationError("locked indexes are cross-wired")
    if any(not set(row.column_ids).issubset({column.id for column in columns}) for row in indexes):
        raise DDLContractError("locked index references a non-member column")
    locator = TrustedTableLocator(
        tenant_id=plan.tenant_id,
        workspace_id=plan.workspace_id,
        resource_id=plan.resource_id,
        table_binding_id=plan.table_binding_id,
        schema_name=tenant.schema_name,
        physical_table_name=table.physical_table_name,
        resource_version=resource.version,
        state=table.state,
        policy_class=table.policy_class,
        display_name=table.display_name,
        columns=tuple(
            TrustedColumnLocator(
                id=UUID(row.id),
                physical_name=row.physical_column_name,
                state=row.state,
                display_name=row.display_name,
            )
            for row in columns
        ),
    )
    validated = validate_plan(plan, locator)
    if (
        plan_row.requires_approval != validated.requires_approval
        or (validated.requires_approval and plan_row.state != "approved")
        or (not validated.requires_approval and plan_row.state != "validated")
    ):
        raise DDLApprovalError("persisted approval policy or plan state changed")
    return validated


def load_apply_records_for_update(
    session: Session,
    *,
    tenant_id: UUID,
    plan_id: UUID,
    expected: ApplyExpectedVersions,
) -> LockedApplyAggregate:
    """Lock and rebuild the complete apply aggregate in canonical order.

    Unlocked rows are used only as identifiers for lock acquisition. Every
    security decision is made again from the locked rows and expected CAS
    versions before the aggregate is returned.
    """
    hints = _load_apply_hints(session, tenant_id, plan_id)
    try:
        contract_hint = DDLPlan.model_validate(hints.normalized_spec, strict=False)
    except ValueError as exc:
        raise DDLContractError("persisted plan hint is not a closed DDL contract") from exc
    tenant = session.execute(
        select(Tenant)
        .where(Tenant.id == str(tenant_id), Tenant.is_active.is_(True))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    actor_user = None
    user_entity = _tenant_entity(User, tenant.schema_name)
    if hints.operation_actor_type == "user":
        if hints.operation_actor_id != hints.actor_user_id:
            raise DDLAuthorizationError("operation actor and authorization actor differ")
        actor_user = session.execute(
            select(user_entity)
            .where(user_entity.id == hints.operation_actor_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()
    resource = session.execute(
        select(ResourceRecord)
        .where(
            ResourceRecord.id == str(contract_hint.resource_id),
            ResourceRecord.tenant_id == str(tenant_id),
            ResourceRecord.version == expected.resource,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    table = session.execute(
        select(DataTableBinding)
        .where(
            DataTableBinding.id == str(contract_hint.table_binding_id),
            DataTableBinding.tenant_id == str(tenant_id),
            DataTableBinding.version == expected.table_binding,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    columns = tuple(
        session.execute(
            select(DataColumnBinding)
            .where(
                DataColumnBinding.tenant_id == str(tenant_id),
                DataColumnBinding.table_binding_id == table.id,
            )
            .order_by(DataColumnBinding.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalars()
    )
    if _version_map(columns) != tuple(sorted(expected.columns)):
        raise DDLVersionConflict("column binding set or version changed")
    indexes = tuple(
        session.execute(
            select(DataIndexBinding)
            .where(
                DataIndexBinding.tenant_id == str(tenant_id),
                DataIndexBinding.table_binding_id == table.id,
            )
            .order_by(DataIndexBinding.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalars()
    )
    if _version_map(indexes) != tuple(sorted(expected.indexes)):
        raise DDLVersionConflict("index binding set or version changed")
    authorization = session.execute(
        select(AuthorizationContext)
        .where(
            AuthorizationContext.id == hints.authorization_context_id,
            AuthorizationContext.tenant_id == str(tenant_id),
            AuthorizationContext.source_version == expected.authorization_source,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    operation = session.execute(
        select(OperationRecord)
        .where(
            OperationRecord.id == hints.operation_id,
            OperationRecord.tenant_id == str(tenant_id),
            OperationRecord.version == expected.operation,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    plan_row = session.execute(
        select(SchemaChangePlan)
        .where(
            SchemaChangePlan.id == str(plan_id),
            SchemaChangePlan.tenant_id == str(tenant_id),
            SchemaChangePlan.version == expected.plan,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    approval = None
    if plan_row.requires_approval:
        if plan_row.approval_id is None or expected.approval is None:
            raise DDLApprovalError("required approval version is missing")
        approval = session.execute(
            select(ApprovalRequest)
            .where(
                ApprovalRequest.id == plan_row.approval_id,
                ApprovalRequest.tenant_id == str(tenant_id),
                ApprovalRequest.version == expected.approval,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()
    elif plan_row.approval_id is not None or expected.approval is not None:
        raise DDLApprovalError("low-risk plan unexpectedly binds an approval")
    payload_entity = _tenant_entity(ControlledDataOperationPayload, tenant.schema_name)
    payload = session.execute(
        select(payload_entity)
        .where(
            payload_entity.plan_id == str(plan_id),
            payload_entity.operation_id == plan_row.operation_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    outbox = session.execute(
        select(OperationDispatchOutbox)
        .where(
            OperationDispatchOutbox.tenant_id == str(tenant_id),
            OperationDispatchOutbox.plan_id == str(plan_id),
            OperationDispatchOutbox.operation_id == plan_row.operation_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    validated = _rebuild_locked_plan(
        tenant=tenant,
        actor_user=actor_user,
        resource=resource,
        table=table,
        columns=columns,
        indexes=indexes,
        authorization=authorization,
        operation=operation,
        plan_row=plan_row,
        approval=approval,
        payload=payload,
        outbox=outbox,
    )
    return LockedApplyAggregate(
        tenant,
        actor_user,
        resource,
        table,
        columns,
        indexes,
        authorization,
        operation,
        plan_row,
        approval,
        payload,
        outbox,
        validated,
    )


__all__ = [
    "AddControlledColumn",
    "ApplyExpectedVersions",
    "CompensationSpec",
    "DDLApprovalError",
    "DDLAuthorizationError",
    "DDLBuildResult",
    "DDLContractError",
    "DDLVersionConflict",
    "LockedApplyAggregate",
    "MetadataChange",
    "RiskPolicy",
    "authorize_apply",
    "build_ddl",
    "build_ddl_preview",
    "classify_risk",
    "compile_postgresql",
    "load_apply_records_for_update",
    "validate_plan",
]
