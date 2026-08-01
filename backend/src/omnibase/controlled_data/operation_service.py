"""Transaction-local lifecycle helpers for controlled schema operations.

The caller owns commit/rollback and database routing.  These helpers only add,
flush, and mutate already locked records; they never retry non-idempotent DDL.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from omnibase.control_plane.models import OperationRecord, ResourceRecord
from omnibase.controlled_data.ddl import CompensationSpec, build_ddl
from omnibase.controlled_data.ddl_contracts import (
    AuthorizedDDLPlan,
    CreateTablePlanDefinition,
    DDLPlan,
    canonical_plan_hash,
)
from omnibase.controlled_data.identifiers import column_identifier, table_identifier
from omnibase.controlled_data.models import (
    DataColumnBinding,
    DataTableBinding,
    OperationCompensation,
    OperationDispatchOutbox,
    SchemaChangePlan,
)
from omnibase.controlled_data.tenant_models import ControlledDataOperationPayload

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_MAX_PAYLOAD_BYTES = 262_144
_MAX_RESULT_REF_BYTES = 16_384
_FORBIDDEN_METADATA_KEYS = frozenset(
    {
        "connection_string",
        "credential",
        "credentials",
        "database_url",
        "dsn",
        "locator",
        "password",
        "physical_column",
        "physical_column_name",
        "physical_table",
        "physical_table_name",
        "raw_sql",
        "schema_name",
        "secret",
        "sql",
        "tenant_schema",
        "token",
    }
)


class OperationStateError(RuntimeError):
    """A lifecycle transition would violate the fail-closed state machine."""


class ApplyConflict(OperationStateError):
    """An idempotency key was reused for different work."""


class AutomaticRetryForbidden(OperationStateError):
    """A non-idempotent schema step was about to be retried automatically."""


class CompensationFailure(OperationStateError):
    """A compensation failed and now requires human intervention."""


class AddFlushSession(Protocol):
    def add(self, instance: object) -> None: ...

    def flush(self) -> None: ...


@dataclass(frozen=True, slots=True)
class CreateTableRegistration:
    resource: ResourceRecord
    table_binding: DataTableBinding
    column_bindings: tuple[DataColumnBinding, ...]
    operation: OperationRecord
    plan: SchemaChangePlan


def _now() -> datetime:
    return datetime.now(UTC)


def _check_digest(value: str, field: str) -> None:
    if not _DIGEST.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def _public_error_detail(code: str) -> str:
    if not _ERROR_CODE.fullmatch(code):
        raise ValueError("error_code must be a short uppercase code")
    return f"Controlled operation failed ({code})."


def register_create_table(
    session: AddFlushSession,
    *,
    tenant_id: UUID,
    workspace_id: UUID | None,
    actor_user_id: UUID,
    authorization_context_id: UUID,
    policy_class: str,
    definition: CreateTablePlanDefinition,
    expires_at: datetime,
    resource_id: UUID | None = None,
) -> CreateTableRegistration:
    """Create the complete pending create-table aggregate in one transaction.

    The request-shaped ``definition`` contains display metadata and logical
    column IDs only. Tenant/schema/physical identifiers are trusted service
    context or generated here; a caller may reserve ``resource_id`` internally
    before creating the immutable authorization context, but it is never a
    public request field.
    """
    if policy_class not in {"workspace_private", "tenant_managed", "controlled_shared"}:
        raise ValueError("policy_class is not mutable controlled data")
    if policy_class == "workspace_private" and workspace_id is None:
        raise ValueError("workspace_private tables require workspace_id")
    if expires_at.tzinfo is None or expires_at.utcoffset() is None or expires_at <= _now():
        raise ValueError("schema plan expiry must be a future aware timestamp")

    logical_resource_id = resource_id or uuid4()
    table_binding_id = uuid4()
    operation_id = uuid4()
    plan_id = uuid4()
    resource = ResourceRecord(
        id=str(logical_resource_id),
        tenant_id=str(tenant_id),
        kind="controlled_table",
        owner_type="workspace" if workspace_id else "user",
        owner_id=str(workspace_id or actor_user_id),
        display_name=definition.display_name,
        state="provisioning",
        version=1,
        policy_class=policy_class,
        physical_locator=None,
        resource_metadata={},
        created_by_actor_id=str(actor_user_id),
    )
    session.add(resource)
    session.flush()

    table_binding = DataTableBinding(
        id=str(table_binding_id),
        tenant_id=str(tenant_id),
        resource_id=str(logical_resource_id),
        workspace_id=str(workspace_id) if workspace_id else None,
        display_name=definition.display_name,
        policy_class=policy_class,
        physical_table_name=table_identifier(logical_resource_id),
        state="pending",
        resource_version=1,
        version=1,
        created_by_actor_id=str(actor_user_id),
    )
    session.add(table_binding)
    columns = tuple(
        DataColumnBinding(
            id=str(item.id),
            tenant_id=str(tenant_id),
            table_binding_id=str(table_binding_id),
            display_name=item.display_name,
            physical_column_name=column_identifier(item.id),
            data_type=item.data_type.type,
            type_args=dict(item.data_type.args),
            nullable=item.nullable,
            ordinal=ordinal,
            state="pending",
            version=1,
        )
        for ordinal, item in enumerate(definition.columns, start=1)
    )
    for column in columns:
        session.add(column)
    session.flush()

    draft = DDLPlan(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        resource_id=logical_resource_id,
        table_binding_id=table_binding_id,
        authorization_context_id=authorization_context_id,
        operation_id=operation_id,
        kind="create_table",
        base_version=1,
        request_hash="0" * 64,
        definition=definition,
    )
    normalized = draft.model_copy(update={"request_hash": canonical_plan_hash(draft)})
    operation = OperationRecord(
        id=str(operation_id),
        tenant_id=str(tenant_id),
        workspace_id=str(workspace_id) if workspace_id else None,
        actor_type="user",
        actor_id=str(actor_user_id),
        resource_id=str(logical_resource_id),
        resource_version=1,
        request_hash=normalized.request_hash,
        kind="data.schema.apply",
        state="queued",
        risk_level="R1",
        progress=0,
        attempt_count=0,
        version=1,
        deadline_at=expires_at,
        operation_metadata={"schema_change_kind": "create_table"},
    )
    session.add(operation)
    session.flush()
    plan = SchemaChangePlan(
        id=str(plan_id),
        tenant_id=str(tenant_id),
        workspace_id=str(workspace_id) if workspace_id else None,
        table_binding_id=str(table_binding_id),
        authorization_context_id=str(authorization_context_id),
        operation_id=str(operation_id),
        approval_id=None,
        kind="create_table",
        normalized_spec=normalized.model_dump(mode="json"),
        request_hash=normalized.request_hash,
        base_version=1,
        risk_level="R1",
        requires_approval=False,
        state="validated",
        version=1,
        expires_at=expires_at,
    )
    session.add(plan)
    session.flush()
    return CreateTableRegistration(resource, table_binding, columns, operation, plan)


def _normalized_key(value: object) -> str:
    raw = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value))
    return "_".join(part for part in re.split(r"[^a-z0-9]+", raw.casefold()) if part)


def _validate_json_tree(value: Any, *, field: str, max_bytes: int) -> dict[str, object]:
    node_count = 0

    def walk(item: Any, depth: int) -> None:
        nonlocal node_count
        node_count += 1
        if depth > 8 or node_count > 1024:
            raise ValueError(f"{field} exceeds its structural limit")
        if isinstance(item, dict):
            for key, child in item.items():
                if _normalized_key(key) in _FORBIDDEN_METADATA_KEYS:
                    raise ValueError(f"{field} contains a forbidden sensitive key")
                walk(child, depth + 1)
            return
        if isinstance(item, list):
            for child in item:
                walk(child, depth + 1)
            return
        if item is None or isinstance(item, (str, int, float, bool)):
            return
        raise ValueError(f"{field} must contain JSON-compatible values")

    walk(value, 1)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError(f"{field} exceeds its byte limit")
    return value


def _validate_record_bindings(
    operation: OperationRecord,
    plan: SchemaChangePlan,
    payload: ControlledDataOperationPayload,
    outbox: OperationDispatchOutbox,
) -> None:
    if not all(
        (
            operation.tenant_id == plan.tenant_id == outbox.tenant_id,
            operation.id == plan.operation_id == payload.operation_id == outbox.operation_id,
            plan.id == payload.plan_id == outbox.plan_id,
            payload.id == outbox.payload_id,
            operation.request_hash == plan.request_hash == payload.request_hash,
            operation.kind == "data.schema.apply",
            outbox.event_type == "schema_change",
            payload.payload_kind == "schema_change",
        )
    ):
        raise ApplyConflict("operation records do not bind the same schema request")


def queue_schema_apply(
    session: AddFlushSession,
    *,
    tenant_id: str,
    operation: OperationRecord,
    plan: SchemaChangePlan,
    normalized_payload: dict[str, object],
    request_hash: str,
    dedupe_key: str,
    expires_at: datetime,
    existing_outbox: OperationDispatchOutbox | None = None,
    existing_payload: ControlledDataOperationPayload | None = None,
) -> tuple[ControlledDataOperationPayload, OperationDispatchOutbox, bool]:
    """Write tenant payload and global outbox together, or replay exactly."""
    _check_digest(request_hash, "request_hash")
    _check_digest(dedupe_key, "dedupe_key")
    _validate_json_tree(
        normalized_payload,
        field="normalized_payload",
        max_bytes=_MAX_PAYLOAD_BYTES,
    )
    if (
        operation.tenant_id != tenant_id
        or plan.tenant_id != tenant_id
        or operation.id != plan.operation_id
        or operation.request_hash != request_hash
        or plan.request_hash != request_hash
        or operation.kind != "data.schema.apply"
        or operation.state != "queued"
        or plan.state not in {"validated", "approved"}
        or operation.risk_level != plan.risk_level
        or normalized_payload != plan.normalized_spec
    ):
        raise ApplyConflict("operation, plan, tenant, and request hash must match")
    now = _now()
    if expires_at.tzinfo is None or expires_at.utcoffset() is None or expires_at <= now:
        raise ValueError("expires_at must be a future timezone-aware timestamp")
    if existing_outbox is not None or existing_payload is not None:
        if existing_outbox is None or existing_payload is None:
            raise ApplyConflict("partial idempotency replay state")
        exact = (
            existing_outbox.tenant_id == tenant_id
            and existing_outbox.operation_id == operation.id
            and existing_outbox.plan_id == plan.id
            and existing_outbox.event_type == "schema_change"
            and existing_outbox.dedupe_key == dedupe_key
            and existing_outbox.payload_id == existing_payload.id
            and existing_payload.operation_id == operation.id
            and existing_payload.plan_id == plan.id
            and existing_payload.payload_kind == "schema_change"
            and existing_payload.request_hash == request_hash
            and existing_payload.normalized_payload == normalized_payload
        )
        if not exact:
            raise ApplyConflict("idempotency key was reused with different work")
        return existing_payload, existing_outbox, True

    payload = ControlledDataOperationPayload(
        operation_id=operation.id,
        plan_id=plan.id,
        payload_kind="schema_change",
        normalized_payload=normalized_payload,
        request_hash=request_hash,
        state="pending",
        expires_at=expires_at,
    )
    session.add(payload)
    session.flush()
    outbox = OperationDispatchOutbox(
        tenant_id=tenant_id,
        operation_id=operation.id,
        plan_id=plan.id,
        payload_id=payload.id,
        event_type="schema_change",
        dedupe_key=dedupe_key,
        state="pending",
        attempt_count=0,
        max_attempts=1,
    )
    session.add(outbox)
    session.flush()
    return payload, outbox, False


def claim_schema_apply(
    outbox: OperationDispatchOutbox,
    *,
    worker_id: str,
    lease_expires_at: datetime,
    lock_held: bool,
) -> bool:
    """Claim an outbox row already loaded with SELECT FOR UPDATE."""
    if lock_held is not True:
        raise OperationStateError("schema outbox must be row-locked before claim")
    if outbox.event_type != "schema_change" or outbox.max_attempts != 1:
        raise AutomaticRetryForbidden("schema DDL must have exactly one attempt")
    if outbox.state == "dispatched":
        return False
    if outbox.state in {"failed", "dead_letter"} or outbox.attempt_count >= 1:
        raise AutomaticRetryForbidden("non-idempotent schema DDL cannot auto-retry")
    if outbox.state != "pending":
        raise OperationStateError("outbox is not claimable")
    if not worker_id or len(worker_id) > 128:
        raise ValueError("worker_id must contain 1 to 128 characters")
    if (
        lease_expires_at.tzinfo is None
        or lease_expires_at.utcoffset() is None
        or lease_expires_at <= _now()
    ):
        raise ValueError("lease_expires_at must be a future timezone-aware timestamp")
    outbox.state = "leased"
    outbox.attempt_count = 1
    outbox.lease_owner = worker_id
    outbox.lease_expires_at = lease_expires_at
    return True


def claim_next_schema_apply(
    session: Session,
    *,
    worker_id: str,
    lease_expires_at: datetime,
    now: datetime | None = None,
) -> OperationDispatchOutbox | None:
    """Lock and claim at most one pending schema outbox row in this transaction."""
    current_time = now or _now()
    outbox = session.execute(
        select(OperationDispatchOutbox)
        .where(
            OperationDispatchOutbox.event_type == "schema_change",
            OperationDispatchOutbox.state == "pending",
            OperationDispatchOutbox.attempt_count == 0,
            OperationDispatchOutbox.max_attempts == 1,
            OperationDispatchOutbox.available_at <= current_time,
        )
        .order_by(OperationDispatchOutbox.available_at, OperationDispatchOutbox.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    ).scalar_one_or_none()
    if outbox is None:
        return None
    claim_schema_apply(
        outbox,
        worker_id=worker_id,
        lease_expires_at=lease_expires_at,
        lock_held=True,
    )
    session.flush()
    return outbox


def mark_apply_started(
    operation: OperationRecord,
    plan: SchemaChangePlan,
    payload: ControlledDataOperationPayload,
    outbox: OperationDispatchOutbox,
    *,
    worker_id: str,
) -> bool:
    _validate_record_bindings(operation, plan, payload, outbox)
    if outbox.lease_owner != worker_id:
        raise OperationStateError("schema apply lease owner changed")
    if (
        outbox.state != "leased"
        or outbox.lease_expires_at is None
        or outbox.lease_expires_at <= _now()
    ):
        raise OperationStateError("schema apply cannot start from the current states")
    if operation.state == "running" and plan.state == "applying" and payload.state == "claimed":
        return False
    if (
        operation.state != "queued"
        or plan.state not in {"validated", "approved"}
        or payload.state != "pending"
    ):
        raise OperationStateError("schema apply cannot start from the current states")
    operation.state = "running"
    operation.started_at = operation.started_at or _now()
    operation.attempt_count += 1
    operation.progress = 1
    operation.version += 1
    plan.state = "applying"
    plan.version += 1
    payload.state = "claimed"
    return True


def mark_apply_succeeded(
    operation: OperationRecord,
    plan: SchemaChangePlan,
    payload: ControlledDataOperationPayload,
    outbox: OperationDispatchOutbox,
    *,
    worker_id: str,
    result_ref: dict[str, object],
) -> bool:
    """Mark success only from the exact active state; replay is a no-op."""
    _validate_record_bindings(operation, plan, payload, outbox)
    if (
        operation.state == "succeeded"
        and plan.state == "applied"
        and payload.state == "applied"
        and outbox.state == "dispatched"
    ):
        return False
    if outbox.lease_owner != worker_id:
        raise OperationStateError("schema apply lease owner changed")
    if (
        operation.state != "running"
        or plan.state != "applying"
        or payload.state != "claimed"
        or outbox.state != "leased"
    ):
        raise OperationStateError("failed or incomplete work cannot be reported as success")
    safe_result_ref = _validate_json_tree(
        result_ref,
        field="result_ref",
        max_bytes=_MAX_RESULT_REF_BYTES,
    )
    completed_at = _now()
    operation.state = "succeeded"
    operation.progress = 100
    operation.completed_at = completed_at
    operation.result_ref = safe_result_ref
    operation.error_code = None
    operation.error_detail = None
    operation.version += 1
    plan.state = "applied"
    plan.version += 1
    payload.state = "applied"
    outbox.state = "dispatched"
    outbox.dispatched_at = completed_at
    outbox.lease_owner = None
    outbox.lease_expires_at = None
    return True


def mark_apply_failed(
    operation: OperationRecord,
    plan: SchemaChangePlan,
    payload: ControlledDataOperationPayload,
    outbox: OperationDispatchOutbox,
    *,
    worker_id: str,
    error_code: str,
    compensation_required: bool,
) -> None:
    _validate_record_bindings(operation, plan, payload, outbox)
    if outbox.lease_owner != worker_id:
        raise OperationStateError("schema apply lease owner changed")
    error_detail = _public_error_detail(error_code)
    if operation.state != "running" or plan.state != "applying":
        raise OperationStateError("only an active apply may fail")
    operation.state = "compensating" if compensation_required else "failed"
    operation.completed_at = None if compensation_required else _now()
    operation.error_code = error_code
    operation.error_detail = error_detail
    operation.version += 1
    plan.state = "compensating" if compensation_required else "failed"
    plan.version += 1
    payload.state = "claimed" if compensation_required else "discarded"
    outbox.state = "dead_letter"
    outbox.last_error_code = error_code
    outbox.lease_owner = None
    outbox.lease_expires_at = None


def create_compensation(
    session: AddFlushSession,
    *,
    tenant_id: str,
    operation: OperationRecord,
    plan: SchemaChangePlan,
    payload: ControlledDataOperationPayload,
    authorized: AuthorizedDDLPlan,
    compensation: CompensationSpec,
    sequence: int,
) -> OperationCompensation:
    if operation.state != "compensating" or plan.state != "compensating":
        raise OperationStateError("compensation may only be created after an apply failure")
    if (
        operation.tenant_id != tenant_id
        or plan.tenant_id != tenant_id
        or operation.id != plan.operation_id
        or operation.id != payload.operation_id
        or plan.id != payload.plan_id
    ):
        raise ApplyConflict("compensation records do not bind the same operation")
    authorized_plan = authorized.validated.plan
    expected_compensations = build_ddl(authorized).compensations
    if compensation not in expected_compensations:
        raise ApplyConflict("compensation is not part of the authorized DDL plan")
    if (
        str(authorized_plan.tenant_id) != tenant_id
        or str(authorized_plan.operation_id) != operation.id
        or authorized_plan.request_hash != plan.request_hash
        or compensation.plan_digest != plan.request_hash
        or str(compensation.resource_id) != operation.resource_id
        or compensation.resource_version != operation.resource_version
        or compensation.resource_version != plan.base_version
    ):
        raise ApplyConflict("compensation binding changed after authorization")
    if sequence < 1:
        raise ValueError("compensation sequence must be positive")
    before_snapshot: dict[str, object] = {}
    if compensation.kind == "restore_display_name":
        if compensation.before_display_name is None:
            raise ApplyConflict("display-name compensation lost its before snapshot")
        before_snapshot = {"display_name": compensation.before_display_name}
    elif compensation.before_display_name is not None:
        raise ApplyConflict("non-restore compensation cannot carry a display snapshot")
    step = OperationCompensation(
        tenant_id=tenant_id,
        operation_id=operation.id,
        plan_id=plan.id,
        payload_id=payload.id,
        target_logical_id=str(compensation.target_logical_id),
        plan_digest=compensation.plan_digest,
        resource_version=compensation.resource_version,
        before_snapshot=before_snapshot,
        sequence=sequence,
        kind=compensation.kind,
        state="pending",
        attempt_count=0,
    )
    session.add(step)
    session.flush()
    return step


def start_compensation(step: OperationCompensation) -> None:
    if step.state != "pending" or step.attempt_count != 0:
        raise AutomaticRetryForbidden("compensation step cannot be retried automatically")
    step.state = "running"
    step.attempt_count = 1


def finish_compensation(
    operation: OperationRecord,
    plan: SchemaChangePlan,
    payload: ControlledDataOperationPayload,
    step: OperationCompensation,
    *,
    remaining_steps: int,
) -> None:
    if (
        step.tenant_id != operation.tenant_id
        or step.operation_id != operation.id
        or step.plan_id != plan.id
        or step.payload_id != payload.id
    ):
        raise ApplyConflict("compensation step is bound to different work")
    if step.state != "running" or operation.state != "compensating" or plan.state != "compensating":
        raise OperationStateError("compensation is not running")
    step.state = "succeeded"
    step.completed_at = _now()
    if remaining_steps < 0:
        raise ValueError("remaining_steps cannot be negative")
    if remaining_steps:
        return
    operation.state = "compensated"
    operation.progress = 100
    operation.completed_at = _now()
    operation.version += 1
    plan.state = "compensated"
    plan.version += 1
    payload.state = "compensated"


def fail_compensation(
    operation: OperationRecord,
    plan: SchemaChangePlan,
    payload: ControlledDataOperationPayload,
    step: OperationCompensation,
    *,
    error_code: str,
) -> CompensationFailure:
    error_detail = _public_error_detail(error_code)
    if (
        step.tenant_id != operation.tenant_id
        or step.operation_id != operation.id
        or step.plan_id != plan.id
        or step.payload_id != payload.id
    ):
        raise ApplyConflict("compensation step is bound to different work")
    if step.state != "running" or operation.state != "compensating" or plan.state != "compensating":
        raise OperationStateError("compensation is not running")
    step.state = "manual_intervention_required"
    step.error_code = error_code
    step.error_detail = error_detail
    step.completed_at = _now()
    operation.state = "failed"
    operation.completed_at = _now()
    operation.error_code = error_code
    operation.error_detail = error_detail
    operation.version += 1
    plan.state = "manual_intervention_required"
    plan.version += 1
    payload.state = "discarded"
    return CompensationFailure("compensation failed; manual intervention is required")


def flush_state(session: Session) -> None:
    """Explicit transaction boundary hook; deliberately does not commit."""
    session.flush()


__all__ = [
    "ApplyConflict",
    "AutomaticRetryForbidden",
    "CompensationFailure",
    "CreateTableRegistration",
    "OperationStateError",
    "claim_next_schema_apply",
    "claim_schema_apply",
    "create_compensation",
    "fail_compensation",
    "finish_compensation",
    "flush_state",
    "mark_apply_failed",
    "mark_apply_started",
    "mark_apply_succeeded",
    "queue_schema_apply",
    "register_create_table",
    "start_compensation",
]
