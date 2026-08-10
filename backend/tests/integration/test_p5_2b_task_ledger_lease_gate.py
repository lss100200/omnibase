"""Guarded PostgreSQL behavior tests for the Task Lease settlement gate.

P5.4D master-review finding P1-2: an expired Task Lease must never commit
success.  These tests run against an explicit ``omnibase_test_p52b_*``
disposable sentinel database (Makefile ``test-p5-2b-task-ledger``), reuse the
P5.1B foundation scaffolding (tenant + workspace + membership + sealed
AgentVersion + installed binding) and drive the real
``LedgerInvocationAdapter`` transaction A/B path.  They assert the settled
terminal rows: an expired lease derails ``committed`` to ``unknown`` with an
open reconciliation case and no committed budget/effect, a fresh lease still
commits, and stale lease ids / fencing drift are refused.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from omnibase.agent_alpha.adapters import LedgerInvocationAdapter, canonical_digest
from omnibase.agent_alpha.contracts import AlphaAgentProfile
from omnibase.agent_registry.service import RegistryPersistenceService
from tests.integration.test_p5_1b_agent_registry_foundation import (
    ACTOR_ID,
    _binding_dto,
    _binding_mapping,
    _canonical_hash,
    _definition_mapping,
    _install,
    _register,
    _seed_actor_user,
    _session,
    _template,
    _tenant,
    _tenant_schema,
    _upgrade_head,
    _version_dto,
    _version_mapping,
    _workspace,
)

if os.environ.get("OMNIBASE_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "P5.2B lease-gate integration tests require OMNIBASE_INTEGRATION_TESTS=1",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def p52b_lease_schema(db_engine) -> None:  # type: ignore[no-untyped-def]
    _upgrade_head()


def _profile(binding, version) -> AlphaAgentProfile:  # type: ignore[no-untyped-def]
    return AlphaAgentProfile(
        agent_definition_id=version.agent_definition_id,
        agent_version_id=version.agent_version_id,
        agent_version_digest=version.canonical_digest(),
        display_name="lease-gate probe",
        instructions="Probe agent for the lease settlement gate.",
        instructions_digest=canonical_digest(
            {"instructions": "Probe agent for the lease settlement gate."}
        ),
        max_context_tokens=16384,
        allowed_tool_ids=(),
        workspace_agent_binding_id=binding.id,
        resource_scope_digest=canonical_digest({"scope": "workspace_readonly"}),
        budget_policy_digest=canonical_digest({"policy": "probe"}),
    )


def _setup(db_engine, run_owned_resources, label: str):  # type: ignore[no-untyped-def]
    # Tenant + workspace (no sealed version yet), then a TOOL-FREE sealed
    # version: Agent Alpha rejects any version with allowed_tool_ids.
    with db_engine.begin() as connection:
        tenant_id = _tenant(connection, run_owned_resources, label)
        schema_name = _tenant_schema(connection, tenant_id)
        template_id = _template(connection, tenant_id)
        workspace_id = _workspace(connection, tenant_id, template_id, label)
    _upgrade_head()
    with db_engine.begin() as connection:
        _seed_actor_user(connection, schema_name, label)
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_memberships "
                "(tenant_id, workspace_id, user_id, role, state, created_by_user_id) "
                "VALUES (:tenant, :workspace, :user, 'owner', 'active', :user)"
            ),
            {"tenant": tenant_id, "workspace": workspace_id, "user": ACTOR_ID},
        )
    definition_mapping = _definition_mapping(tenant_id, risk_level="low")
    definition_id = str(definition_mapping["agent_definition_id"])
    version_mapping = _version_mapping(tenant_id, definition_id, risk_level="low")
    version_mapping["allowed_tool_ids"] = []
    version_mapping["manifest_digest"] = _canonical_hash(
        {k: v for k, v in version_mapping.items() if k != "manifest_digest"}
    )
    version = _version_dto(version_mapping)
    with _session(db_engine, tenant_id) as session:
        _register(session, tenant_id=tenant_id, mapping=definition_mapping, key=uuid.uuid4().hex)
        RegistryPersistenceService(session).seal_version(
            tenant_id=tenant_id,
            actor_user_id=ACTOR_ID,
            request_id=str(uuid.uuid4()),
            version=version,
            idempotency_key=uuid.uuid4().hex,
        )
        binding = _install(
            session,
            tenant_id=tenant_id,
            binding=_binding_dto(_binding_mapping(tenant_id, workspace_id, definition_id, version)),
            key=uuid.uuid4().hex,
        )
        session.commit()
    return tenant_id, workspace_id, version, binding


def _begin_invocation(db_engine, tenant_id, workspace_id, version, binding, label):  # type: ignore[no-untyped-def]
    factory = sessionmaker(db_engine)
    adapter = LedgerInvocationAdapter(factory)
    identity = adapter.begin(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_user_id=ACTOR_ID,
        profile=_profile(binding, version),
        idempotency_key=f"{label}-{uuid.uuid4().hex}",
        request_hash=canonical_digest(
            {
                "workspace_id": workspace_id,
                "agent_version_id": version.agent_version_id,
                "message": "probe",
                "top_k": 1,
            }
        ),
        retry_of=None,
    )
    return adapter, identity


def _lease_row(db_engine, identity) -> dict[str, object]:  # type: ignore[no-untyped-def]
    with db_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT id, state, expires_at, heartbeat_at, task_fencing_token "
                    "FROM omnibase_meta.agent_task_leases "
                    "WHERE attempt_id = :attempt"
                ),
                {"attempt": identity.attempt_id},
            )
            .mappings()
            .one()
        )
        return dict(row)


def _expire_lease_at_now(db_engine, lease_id: str) -> None:  # type: ignore[no-untyped-def]
    """Set expires_at to the current clock; the next finish is at-or-after it."""
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE omnibase_meta.agent_task_leases "
                "SET expires_at = clock_timestamp() "
                "WHERE id = :lease"
            ),
            {"lease": lease_id},
        )


def _task_state(db_engine, identity) -> str:  # type: ignore[no-untyped-def]
    with db_engine.connect() as connection:
        return str(
            connection.execute(
                text("SELECT state FROM omnibase_meta.agent_tasks WHERE id = :task"),
                {"task": identity.task_id},
            ).scalar_one()
        )


def _reconciliation_count(db_engine, identity, reason: str) -> int:  # type: ignore[no-untyped-def]
    with db_engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT count(*) FROM omnibase_meta.agent_reconciliation_cases "
                    "WHERE attempt_id = :attempt AND reason_code = :reason"
                ),
                {"attempt": identity.attempt_id, "reason": reason},
            ).scalar_one()
        )


def _committed_budget_rows(db_engine, identity) -> int:  # type: ignore[no-untyped-def]
    with db_engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT count(*) FROM omnibase_meta.agent_task_budget_ledgers "
                    "WHERE task_id = :task AND committed > 0"
                ),
                {"task": identity.task_id},
            ).scalar_one()
        )


def test_expired_lease_never_commits_success(db_engine, run_owned_resources) -> None:  # type: ignore[no-untyped-def]
    tenant_id, workspace_id, version, binding = _setup(
        db_engine, run_owned_resources, "expired-commit"
    )
    adapter, identity = _begin_invocation(
        db_engine, tenant_id, workspace_id, version, binding, "expired-commit"
    )
    lease = _lease_row(db_engine, identity)
    time.sleep(1.1)  # let the claim timestamp age so the boundary is stable
    _expire_lease_at_now(db_engine, lease["id"])  # type: ignore[arg-type]
    # Transaction B tries to commit a successful outcome on an expired lease.
    adapter.complete(
        identity=identity,
        result_digest=canonical_digest({"answer": "probe"}),
        usage=_usage(),
    )
    # The success must be derailed to unknown/reconciliation, never succeeded.
    assert _task_state(db_engine, identity) == "blocked_unknown"
    with db_engine.connect() as connection:
        attempt_state = str(
            connection.execute(
                text("SELECT state FROM omnibase_meta.agent_attempts WHERE id = :attempt"),
                {"attempt": identity.attempt_id},
            ).scalar_one()
        )
        assert attempt_state == "unknown"
        settled_lease = dict(
            connection.execute(
                text(
                    "SELECT state, heartbeat_at, expires_at "
                    "FROM omnibase_meta.agent_task_leases WHERE id = :lease"
                ),
                {"lease": lease["id"]},
            )
            .mappings()
            .one()
        )
        # Lease closed revoked; heartbeat fixed at the boundary, never extended.
        assert settled_lease["state"] == "revoked"
        assert settled_lease["heartbeat_at"] == settled_lease["expires_at"]
        run_id = str(
            connection.execute(
                text("SELECT agent_run_id FROM omnibase_meta.agent_attempts WHERE id = :attempt"),
                {"attempt": identity.attempt_id},
            ).scalar_one()
        )
        run_state = str(
            connection.execute(
                text("SELECT state FROM omnibase_meta.agent_runs WHERE id = :run"),
                {"run": run_id},
            ).scalar_one()
        )
        # The AgentRun is terminal (unknown -> failed) and its runtime/fencing
        # bindings were cleared, so no workspace slot stays occupied.
        assert run_state == "failed"
    assert _reconciliation_count(db_engine, identity, "agent_alpha_task_lease_expired") == 1
    assert _committed_budget_rows(db_engine, identity) == 0


def test_fresh_lease_still_commits_success(db_engine, run_owned_resources) -> None:  # type: ignore[no-untyped-def]
    tenant_id, workspace_id, version, binding = _setup(
        db_engine, run_owned_resources, "fresh-commit"
    )
    adapter, identity = _begin_invocation(
        db_engine, tenant_id, workspace_id, version, binding, "fresh-commit"
    )
    adapter.complete(
        identity=identity,
        result_digest=canonical_digest({"answer": "probe"}),
        usage=_usage(),
    )
    assert _task_state(db_engine, identity) == "succeeded"
    assert _committed_budget_rows(db_engine, identity) == 3
    with db_engine.connect() as connection:
        lease_state = str(
            connection.execute(
                text(
                    "SELECT state FROM omnibase_meta.agent_task_leases "
                    "WHERE attempt_id = :attempt"
                ),
                {"attempt": identity.attempt_id},
            ).scalar_one()
        )
        assert lease_state == "completed"


def test_expired_lease_unknown_and_cancelled_pass_through(db_engine, run_owned_resources) -> None:  # type: ignore[no-untyped-def]
    tenant_id, workspace_id, version, binding = _setup(
        db_engine, run_owned_resources, "expired-unknown"
    )
    adapter, identity = _begin_invocation(
        db_engine, tenant_id, workspace_id, version, binding, "expired-unknown"
    )
    lease = _lease_row(db_engine, identity)
    time.sleep(1.1)
    _expire_lease_at_now(db_engine, lease["id"])  # type: ignore[arg-type]
    # unknown stays unknown and still opens the reconciliation it declared.
    adapter.fail(identity=identity, outcome="unknown", error_code="agent_alpha_sse_disconnected")
    assert _task_state(db_engine, identity) == "blocked_unknown"
    assert _reconciliation_count(db_engine, identity, "agent_alpha_sse_disconnected") == 1
    # cancelled is a deterministic user decision and passes through unchanged.
    adapter2, identity2 = _begin_invocation(
        db_engine, tenant_id, workspace_id, version, binding, "expired-cancelled"
    )
    lease2 = _lease_row(db_engine, identity2)
    time.sleep(1.1)
    _expire_lease_at_now(db_engine, lease2["id"])  # type: ignore[arg-type]
    adapter2.fail(identity=identity2, outcome="cancelled", error_code="agent_alpha_cancelled")
    assert _task_state(db_engine, identity2) == "cancelled"


def test_stale_lease_and_fencing_drift_are_rejected(db_engine, run_owned_resources) -> None:  # type: ignore[no-untyped-def]
    tenant_id, workspace_id, version, binding = _setup(
        db_engine, run_owned_resources, "stale-finish"
    )
    adapter, identity = _begin_invocation(
        db_engine, tenant_id, workspace_id, version, binding, "stale-finish"
    )
    del adapter
    lease = _lease_row(db_engine, identity)
    from omnibase.task_ledger.service import (
        TaskLedgerConflict,
        TaskLedgerPersistenceService,
    )

    # A) An unknown / replaced lease id is refused outright: the attempt is
    # not terminalized by a caller that no longer owns its lease.
    with _session(db_engine, tenant_id) as session:
        svc = TaskLedgerPersistenceService(session)
        with pytest.raises(TaskLedgerConflict, match="task_attempt_finish_stale"):
            svc.finish_attempt(
                tenant_id=tenant_id,
                attempt_id=identity.attempt_id,
                task_lease_id=str(uuid.uuid4()),
                task_fencing_token=int(lease["task_fencing_token"]),
                outcome="committed",
            )
        session.rollback()
    # B) Fencing drift on the live lease is refused too: a finish that
    # presents a token different from the one the attempt recorded (and the
    # lease carries) is a stale authorization.  The lease identity itself is
    # immutable (agent_task_lease_guard), so the drift is presented as a
    # parameter, never by mutating the row.
    with _session(db_engine, tenant_id) as session:
        svc = TaskLedgerPersistenceService(session)
        with pytest.raises(TaskLedgerConflict, match="task_attempt_finish_stale"):
            svc.finish_attempt(
                tenant_id=tenant_id,
                attempt_id=identity.attempt_id,
                task_lease_id=str(lease["id"]),
                task_fencing_token=99999,
                outcome="committed",
            )
        session.rollback()
    # Nothing was terminalized: the task is still active and the lease is
    # still the live active lease, so no success was ever recorded.
    assert _task_state(db_engine, identity) == "running"
    with db_engine.connect() as connection:
        lease_state = str(
            connection.execute(
                text("SELECT state FROM omnibase_meta.agent_task_leases WHERE id = :lease"),
                {"lease": lease["id"]},
            ).scalar_one()
        )
        assert lease_state == "active"


def _usage():  # type: ignore[no-untyped-def]
    from omnibase.model_gateway import ModelUsage

    return ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15, reasoning_tokens=0)
