"""Real-PostgreSQL concurrency checks for controlled schema operations.

The module is inert unless the shared destructive-test sentinel has approved
an explicit ``omnibase_test_*`` database and restricted non-owner role. Tests
reuse ``db_engine`` and never construct an independent database connection.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from omnibase.control_plane.models import OperationRecord
from omnibase.controlled_data.ddl import ApplyExpectedVersions, load_apply_records_for_update
from omnibase.controlled_data.ddl_contracts import (
    CreateTablePlanDefinition,
    DDLPlan,
    canonical_plan_hash,
)
from omnibase.controlled_data.identifiers import column_identifier, table_identifier
from omnibase.controlled_data.models import (
    OperationCompensation,
    OperationDispatchOutbox,
    SchemaChangePlan,
)
from omnibase.controlled_data.operation_service import (
    CompensationFailure,
    OperationStateError,
    claim_next_schema_apply,
    fail_compensation,
    mark_apply_started,
    start_compensation,
)
from omnibase.controlled_data.tenant_models import ControlledDataOperationPayload

if os.environ.get("OMNIBASE_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "P34.3 concurrency integration requires OMNIBASE_INTEGRATION_TESTS=1",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class _SeededOperation:
    tenant_id: str
    tenant_schema: str
    actor_id: str
    resource_id: str
    table_binding_id: str
    column_id: str
    authorization_id: str
    operation_id: str
    plan_id: str
    payload_id: str
    outbox_id: str
    compensation_id: str | None
    request_hash: str


def _upgrade_head() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_BACKEND_ROOT,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _set_tenant_search_path(session: Session, schema_name: str) -> None:
    assert schema_name.startswith("tenant_")
    session.execute(text(f'SET LOCAL search_path TO "{schema_name}", omnibase_meta, public'))


def _seed_schema_operation(
    db_engine: object,
    run_owned_resources: object,
    *,
    compensating: bool = False,
) -> _SeededOperation:
    _upgrade_head()
    tenant_id = str(uuid.uuid4())
    suffix = uuid.uuid4().hex[:8]
    tenant_schema = f"tenant_{suffix}"
    resource_id = str(uuid.uuid4())
    table_binding_id = str(uuid.uuid4())
    column_id = str(uuid.uuid4())
    actor_id = str(uuid.uuid4())
    authorization_id = str(uuid.uuid4())
    operation_id = str(uuid.uuid4())
    plan_id = str(uuid.uuid4())
    payload_id = str(uuid.uuid4())
    outbox_id = str(uuid.uuid4())
    compensation_id = str(uuid.uuid4()) if compensating else None
    definition = CreateTablePlanDefinition.model_validate(
        {
            "display_name": "Concurrency table",
            "columns": [
                {
                    "id": uuid.UUID(column_id),
                    "display_name": "Value",
                    "data_type": {"type": "string", "args": {"max_length": 200}},
                    "nullable": True,
                }
            ],
        }
    )
    draft = DDLPlan(
        tenant_id=uuid.UUID(tenant_id),
        workspace_id=None,
        resource_id=uuid.UUID(resource_id),
        table_binding_id=uuid.UUID(table_binding_id),
        authorization_context_id=uuid.UUID(authorization_id),
        operation_id=uuid.UUID(operation_id),
        kind="create_table",
        base_version=1,
        request_hash="0" * 64,
        definition=definition,
    )
    normalized_plan = draft.model_copy(update={"request_hash": canonical_plan_hash(draft)})
    request_hash = normalized_plan.request_hash
    normalized_spec = json.dumps(normalized_plan.model_dump(mode="json"))
    dedupe_key = uuid.uuid4().hex + uuid.uuid4().hex
    now = datetime.now(UTC)

    with db_engine.begin() as connection:  # type: ignore[attr-defined]
        connection.execute(text(f'CREATE SCHEMA "{tenant_schema}"'))
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.tenants "
                "(id, name, slug, schema_name, is_default, is_active) "
                "VALUES (:id, :name, :slug, :schema, FALSE, TRUE)"
            ),
            {
                "id": tenant_id,
                "name": "P34.3 schema concurrency tenant",
                "slug": f"p343-schema-concurrency-{suffix}",
                "schema": tenant_schema,
            },
        )
    run_owned_resources.add(tenant_id, tenant_schema)
    _upgrade_head()

    operation_state = "compensating" if compensating else "queued"
    plan_state = "compensating" if compensating else "validated"
    payload_state = "claimed" if compensating else "pending"
    outbox_state = "dead_letter" if compensating else "pending"
    attempt_count = 1 if compensating else 0
    with db_engine.begin() as connection:  # type: ignore[attr-defined]
        connection.execute(
            text(
                f'INSERT INTO "{tenant_schema}".users '  # noqa: S608
                "(id, email, password_hash, is_tenant_admin, is_active) "
                "VALUES (:id, :email, :password_hash, TRUE, TRUE)"
            ),
            {
                "id": actor_id,
                "email": f"schema-owner-{suffix}@example.invalid",
                "password_hash": uuid.uuid4().hex,
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.resource_registry "
                "(id, tenant_id, kind, owner_type, owner_id, display_name, state, "
                "version, policy_class, created_by_actor_id) VALUES "
                "(:id, :tenant, 'controlled_table', 'user', :actor, "
                "'Concurrency table', 'provisioning', 1, 'tenant_managed', :actor)"
            ),
            {
                "id": resource_id,
                "tenant": tenant_id,
                "actor": actor_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.data_table_bindings "
                "(id, tenant_id, resource_id, display_name, policy_class, "
                "physical_table_name, state, resource_version, version, "
                "created_by_actor_id) VALUES "
                "(:id, :tenant, :resource, 'Concurrency table', 'tenant_managed', "
                ":physical, 'pending', 1, 1, :actor)"
            ),
            {
                "id": table_binding_id,
                "tenant": tenant_id,
                "resource": resource_id,
                "physical": table_identifier(resource_id),
                "actor": actor_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.data_column_bindings "
                "(id, tenant_id, table_binding_id, display_name, physical_column_name, "
                "data_type, type_args, nullable, ordinal, state, version) VALUES "
                "(:id, :tenant, :binding, 'Value', :physical, 'string', "
                "'{\"max_length\": 200}'::jsonb, TRUE, 1, 'pending', 1)"
            ),
            {
                "id": column_id,
                "tenant": tenant_id,
                "binding": table_binding_id,
                "physical": column_identifier(column_id),
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.authorization_contexts "
                "(id, tenant_id, source, actor_user_id, role_snapshot, actions, "
                "resource_ids, source_version, snapshot_hash, live_recheck_required, "
                "created_at, expires_at) VALUES "
                "(:id, :tenant, 'user_rbac', :actor, ARRAY['tenant_admin']::varchar[], "
                "ARRAY['data.schema.apply']::varchar[], ARRAY[:resource]::uuid[], "
                "1, :snapshot, TRUE, :created, :expires)"
            ),
            {
                "id": authorization_id,
                "tenant": tenant_id,
                "actor": actor_id,
                "resource": resource_id,
                "snapshot": "a" * 64,
                "created": now - timedelta(minutes=1),
                "expires": now + timedelta(minutes=10),
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.operations "
                "(id, tenant_id, actor_type, actor_id, resource_id, resource_version, "
                "request_hash, kind, state, risk_level, progress, attempt_count, version) "
                "VALUES (:id, :tenant, 'user', :actor, :resource, 1, :hash, "
                "'data.schema.apply', :state, 'R1', 0, :attempts, 1)"
            ),
            {
                "id": operation_id,
                "tenant": tenant_id,
                "actor": actor_id,
                "resource": resource_id,
                "hash": request_hash,
                "state": operation_state,
                "attempts": attempt_count,
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.schema_change_plans "
                "(id, tenant_id, table_binding_id, authorization_context_id, operation_id, kind, "
                "normalized_spec, request_hash, base_version, risk_level, "
                "requires_approval, state, version, expires_at) VALUES "
                "(:id, :tenant, :binding, :authorization, :operation, 'create_table', "
                "CAST(:spec AS jsonb), :hash, 1, 'R1', FALSE, :state, 1, :expires)"
            ),
            {
                "id": plan_id,
                "tenant": tenant_id,
                "binding": table_binding_id,
                "authorization": authorization_id,
                "operation": operation_id,
                "spec": normalized_spec,
                "hash": request_hash,
                "state": plan_state,
                "expires": now + timedelta(minutes=10),
            },
        )
        connection.execute(
            text(
                f'INSERT INTO "{tenant_schema}".controlled_data_operation_payloads '  # noqa: S608
                "(id, operation_id, plan_id, payload_kind, normalized_payload, "
                "request_hash, state, expires_at) VALUES "
                "(:id, :operation, :plan, 'schema_change', CAST(:payload AS jsonb), "
                ":hash, :state, :expires)"
            ),
            {
                "id": payload_id,
                "operation": operation_id,
                "plan": plan_id,
                "payload": normalized_spec,
                "hash": request_hash,
                "state": payload_state,
                "expires": now + timedelta(minutes=10),
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.operation_dispatch_outbox "
                "(id, tenant_id, operation_id, plan_id, payload_id, event_type, "
                "dedupe_key, state, attempt_count, max_attempts, available_at, "
                "last_error_code) VALUES "
                "(:id, :tenant, :operation, :plan, :payload, 'schema_change', "
                ":dedupe, :state, :attempts, 1, :available, :error)"
            ),
            {
                "id": outbox_id,
                "tenant": tenant_id,
                "operation": operation_id,
                "plan": plan_id,
                "payload": payload_id,
                "dedupe": dedupe_key,
                "state": outbox_state,
                "attempts": attempt_count,
                "available": now - timedelta(seconds=1),
                "error": "DDL_FAILED" if compensating else None,
            },
        )
        if compensation_id is not None:
            connection.execute(
                text(
                    "INSERT INTO omnibase_meta.operation_compensations "
                    "(id, tenant_id, operation_id, plan_id, payload_id, "
                    "target_logical_id, plan_digest, resource_version, before_snapshot, "
                    "sequence, kind, state, attempt_count) VALUES "
                    "(:id, :tenant, :operation, :plan, :payload, :target, :digest, 1, "
                    "'{}'::jsonb, 1, 'drop_created_table', 'pending', 0)"
                ),
                {
                    "id": compensation_id,
                    "tenant": tenant_id,
                    "operation": operation_id,
                    "plan": plan_id,
                    "payload": payload_id,
                    "target": resource_id,
                    "digest": request_hash,
                },
            )

    return _SeededOperation(
        tenant_id,
        tenant_schema,
        actor_id,
        resource_id,
        table_binding_id,
        column_id,
        authorization_id,
        operation_id,
        plan_id,
        payload_id,
        outbox_id,
        compensation_id,
        request_hash,
    )


def test_two_sessions_claim_schema_outbox_once_and_wrong_owner_cannot_start(
    db_engine,
    run_owned_resources,
) -> None:
    seeded = _seed_schema_operation(db_engine, run_owned_resources)
    barrier = threading.Barrier(2)
    lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)

    def claim(worker_id: str) -> tuple[str, str] | None:
        with Session(db_engine) as session, session.begin():
            barrier.wait(timeout=10)
            row = claim_next_schema_apply(
                session,
                worker_id=worker_id,
                lease_expires_at=lease_expires_at,
            )
            return (row.id, worker_id) if row is not None else None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            future.result(timeout=15)
            for future in (
                executor.submit(claim, "schema-worker-a"),
                executor.submit(claim, "schema-worker-b"),
            )
        )
    claimed = [item for item in results if item is not None]
    assert len(claimed) == 1
    assert claimed[0][0] == seeded.outbox_id
    winner = claimed[0][1]
    loser = "schema-worker-b" if winner == "schema-worker-a" else "schema-worker-a"

    def start_as_wrong_owner() -> None:
        with Session(db_engine) as session, session.begin():
            _set_tenant_search_path(session, seeded.tenant_schema)
            operation = session.get(OperationRecord, seeded.operation_id)
            plan = session.get(SchemaChangePlan, seeded.plan_id)
            payload = session.get(ControlledDataOperationPayload, seeded.payload_id)
            outbox = session.execute(
                select(OperationDispatchOutbox)
                .where(OperationDispatchOutbox.id == seeded.outbox_id)
                .with_for_update()
            ).scalar_one()
            assert operation is not None
            assert plan is not None
            assert payload is not None
            mark_apply_started(
                operation,
                plan,
                payload,
                outbox,
                worker_id=loser,
            )

    with pytest.raises(OperationStateError, match="lease owner"):
        start_as_wrong_owner()

    with db_engine.connect() as connection:
        outbox = connection.execute(
            text(
                "SELECT state, attempt_count, lease_owner FROM "
                "omnibase_meta.operation_dispatch_outbox WHERE id = :id"
            ),
            {"id": seeded.outbox_id},
        ).one()
        operation_state = connection.execute(
            text("SELECT state FROM omnibase_meta.operations WHERE id = :id"),
            {"id": seeded.operation_id},
        ).scalar_one()
        plan_state = connection.execute(
            text("SELECT state FROM omnibase_meta.schema_change_plans WHERE id = :id"),
            {"id": seeded.plan_id},
        ).scalar_one()
        payload_state = connection.execute(
            text(
                f'SELECT state FROM "{seeded.tenant_schema}".'  # noqa: S608
                "controlled_data_operation_payloads WHERE id = :id"
            ),
            {"id": seeded.payload_id},
        ).scalar_one()
    assert outbox == ("leased", 1, winner)
    assert operation_state == "queued"
    assert plan_state == "validated"
    assert payload_state == "pending"


def test_operation_version_compare_and_swap_loses_cleanly_after_row_lock_race(
    db_engine,
    run_owned_resources,
) -> None:
    seeded = _seed_schema_operation(db_engine, run_owned_resources)
    locked = threading.Event()
    update_attempted = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with Session(db_engine) as session, session.begin():
            operation = session.execute(
                select(OperationRecord)
                .where(OperationRecord.id == seeded.operation_id)
                .with_for_update()
            ).scalar_one()
            operation.version = 2
            locked.set()
            assert release.wait(timeout=10)

    def stale_compare_and_swap() -> int:
        assert locked.wait(timeout=10)
        with Session(db_engine) as session, session.begin():
            update_attempted.set()
            result = session.execute(
                update(OperationRecord)
                .where(
                    OperationRecord.id == seeded.operation_id,
                    OperationRecord.version == 1,
                )
                .values(version=3)
            )
            return int(result.rowcount or 0)

    with ThreadPoolExecutor(max_workers=2) as executor:
        holder_future = executor.submit(holder)
        contender_future = executor.submit(stale_compare_and_swap)
        assert update_attempted.wait(timeout=10)
        release.set()
        holder_future.result(timeout=15)
        stale_rowcount = contender_future.result(timeout=15)

    with db_engine.connect() as connection:
        persisted_version = connection.execute(
            text("SELECT version FROM omnibase_meta.operations WHERE id = :id"),
            {"id": seeded.operation_id},
        ).scalar_one()
    assert stale_rowcount == 0
    assert persisted_version == 2


def test_apply_aggregate_uses_registry_schema_not_decoy_search_path(
    db_engine,
    run_owned_resources,
) -> None:
    seeded = _seed_schema_operation(db_engine, run_owned_resources)
    decoy_tenant_id = str(uuid.uuid4())
    decoy_suffix = uuid.uuid4().hex[:8]
    decoy_schema = f"tenant_{decoy_suffix}"
    with db_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{decoy_schema}"'))
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.tenants "
                "(id, name, slug, schema_name, is_default, is_active) "
                "VALUES (:id, 'Decoy tenant', :slug, :schema, FALSE, TRUE)"
            ),
            {
                "id": decoy_tenant_id,
                "slug": f"p343-schema-decoy-{decoy_suffix}",
                "schema": decoy_schema,
            },
        )
    run_owned_resources.add(decoy_tenant_id, decoy_schema)
    _upgrade_head()
    with db_engine.begin() as connection:
        connection.execute(
            text(
                f'INSERT INTO "{decoy_schema}".users '  # noqa: S608
                "(id, email, password_hash, is_tenant_admin, is_active) "
                "VALUES (:id, 'decoy@example.invalid', :password_hash, FALSE, TRUE)"
            ),
            {"id": seeded.actor_id, "password_hash": uuid.uuid4().hex},
        )
        connection.execute(
            text(
                f'INSERT INTO "{decoy_schema}".controlled_data_operation_payloads '  # noqa: S608
                "(id, operation_id, plan_id, payload_kind, normalized_payload, "
                "request_hash, state, expires_at) VALUES "
                "(:id, :operation, :plan, 'schema_change', '{}'::jsonb, :hash, "
                "'pending', :expires)"
            ),
            {
                "id": seeded.payload_id,
                "operation": seeded.operation_id,
                "plan": seeded.plan_id,
                "hash": "f" * 64,
                "expires": datetime.now(UTC) + timedelta(minutes=10),
            },
        )

    with Session(db_engine) as session, session.begin():
        _set_tenant_search_path(session, decoy_schema)
        aggregate = load_apply_records_for_update(
            session,
            tenant_id=uuid.UUID(seeded.tenant_id),
            plan_id=uuid.UUID(seeded.plan_id),
            expected=ApplyExpectedVersions(
                resource=1,
                table_binding=1,
                authorization_source=1,
                operation=1,
                plan=1,
                columns=((uuid.UUID(seeded.column_id), 1),),
            ),
        )
        assert aggregate.actor_user is not None
        assert aggregate.actor_user.email.startswith("schema-owner-")
        assert aggregate.payload.request_hash == seeded.request_hash
        assert aggregate.validated.locator.schema_name == seeded.tenant_schema


def test_compensation_failure_manual_intervention_state_survives_commit(
    db_engine,
    run_owned_resources,
) -> None:
    seeded = _seed_schema_operation(
        db_engine,
        run_owned_resources,
        compensating=True,
    )
    assert seeded.compensation_id is not None

    with Session(db_engine) as session, session.begin():
        _set_tenant_search_path(session, seeded.tenant_schema)
        operation = session.execute(
            select(OperationRecord)
            .where(OperationRecord.id == seeded.operation_id)
            .with_for_update()
        ).scalar_one()
        plan = session.execute(
            select(SchemaChangePlan).where(SchemaChangePlan.id == seeded.plan_id).with_for_update()
        ).scalar_one()
        payload = session.execute(
            select(ControlledDataOperationPayload)
            .where(ControlledDataOperationPayload.id == seeded.payload_id)
            .with_for_update()
        ).scalar_one()
        step = session.execute(
            select(OperationCompensation)
            .where(OperationCompensation.id == seeded.compensation_id)
            .with_for_update()
        ).scalar_one()
        start_compensation(step)
        failure = fail_compensation(
            operation,
            plan,
            payload,
            step,
            error_code="COMPENSATION_FAILED",
        )
        assert isinstance(failure, CompensationFailure)

    with db_engine.connect() as connection:
        operation = connection.execute(
            text("SELECT state, error_code FROM omnibase_meta.operations " "WHERE id = :id"),
            {"id": seeded.operation_id},
        ).one()
        plan_state = connection.execute(
            text("SELECT state FROM omnibase_meta.schema_change_plans WHERE id = :id"),
            {"id": seeded.plan_id},
        ).scalar_one()
        step = connection.execute(
            text(
                "SELECT state, attempt_count, error_code FROM "
                "omnibase_meta.operation_compensations WHERE id = :id"
            ),
            {"id": seeded.compensation_id},
        ).one()
        payload_state = connection.execute(
            text(
                f'SELECT state FROM "{seeded.tenant_schema}".'  # noqa: S608
                "controlled_data_operation_payloads WHERE id = :id"
            ),
            {"id": seeded.payload_id},
        ).scalar_one()
    assert operation == ("failed", "COMPENSATION_FAILED")
    assert plan_state == "manual_intervention_required"
    assert step == ("manual_intervention_required", 1, "COMPENSATION_FAILED")
    assert payload_state == "discarded"
