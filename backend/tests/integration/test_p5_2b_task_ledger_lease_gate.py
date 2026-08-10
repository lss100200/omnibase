"""Guarded PostgreSQL behavior tests for the double-lease settlement gate.

P5.4D master-review Round 2 findings P1-1/P1-2.  These tests run against an
explicit ``omnibase_test_p52b_*`` disposable sentinel database (Makefile
``test-p5-2b-task-ledger``), reuse the P5.1B foundation scaffolding (tenant +
workspace + membership + sealed tool-free AgentVersion + installed binding)
and drive the real ``LedgerInvocationAdapter`` transaction A/B path.

Covered scenarios:

* A — Task Lease expired + Run Lease live: ``committed`` derails to
  ``unknown`` and the whole terminal transition commits atomically.
* B — Task Lease expired + Run Lease expired: no success, the restricted
  historical run-holder close safely terminalizes and releases the slot.
* C — Task Lease expired + Run Lease revoked: the RunLease is never revived
  and the exact holder is closed as failure.
* D — Node fencing advanced: a stale holder fails closed and touches nothing.
* E — Workspace generation drift: a stale invocation fails closed.
* F — wrong RunLease id / wrong TaskLease id / wrong binding: all refused.
* G — a follow-on write failure rolls the whole terminal transition back.
* H — immediately after a successful close, the next invocation succeeds
  (no active interactive run occupies the slot).

The assertions in each test read the FULL persisted row matrix: TaskLease,
Attempt, Effect, Task, AgentRun, WorkspaceRun, RunLease, Reconciliation
(exact bindings and reason), Budget (reserved/committed/released/remaining)
and the Workspace slot itself.
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


def _row(db_engine, sql: str, **params) -> dict[str, object]:  # type: ignore[no-untyped-def]
    with db_engine.connect() as connection:
        return dict(connection.execute(text(sql), params).mappings().one())


def _scalar(db_engine, sql: str, **params) -> object:  # type: ignore[no-untyped-def]
    with db_engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


def _task_lease_row(db_engine, identity) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return _row(
        db_engine,
        "SELECT id, state, expires_at, heartbeat_at, task_fencing_token, "
        "run_fencing_token, node_id, node_fencing_token, workspace_generation "
        "FROM omnibase_meta.agent_task_leases WHERE attempt_id = :attempt",
        attempt=identity.attempt_id,
    )


def _attempt_row(db_engine, identity) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return _row(
        db_engine,
        "SELECT id, state, task_lease_id, task_fencing_token, agent_run_id, "
        "updated_at FROM omnibase_meta.agent_attempts WHERE id = :attempt",
        attempt=identity.attempt_id,
    )


def _effect_row(db_engine, identity) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return _row(
        db_engine,
        "SELECT id, task_id, attempt_id, agent_run_id, state, result_digest, "
        "request_hash FROM omnibase_meta.agent_task_effects WHERE id = :effect",
        effect=identity.effect_id,
    )


def _task_row(db_engine, identity) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return _row(
        db_engine,
        "SELECT id, state, deadline, updated_at FROM omnibase_meta.agent_tasks " "WHERE id = :task",
        task=identity.task_id,
    )


def _agent_run_row(db_engine, identity) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return _row(
        db_engine,
        "SELECT id, state, workspace_run_id, run_lease_id, run_fencing_token, "
        "node_id, node_fencing_token, runtime_instance_id, workload_identity_digest "
        "FROM omnibase_meta.agent_runs WHERE id = :run",
        run=_scalar(
            db_engine,
            "SELECT agent_run_id FROM omnibase_meta.agent_attempts WHERE id = :attempt",
            attempt=identity.attempt_id,
        ),
    )


def _workspace_run_row(db_engine, identity) -> dict[str, object]:  # type: ignore[no-untyped-def]
    agent_run = _agent_run_row(db_engine, identity)
    return _row(
        db_engine,
        "SELECT id, workspace_id, kind, generation, desired_state, observed_state, "
        "runtime_instance_id, workload_identity_digest, last_result_digest, "
        "last_error_code, version FROM omnibase_meta.workspace_runs "
        "WHERE id = :run",
        run=agent_run["workspace_run_id"],
    )


def _run_lease_row(db_engine, identity) -> dict[str, object]:  # type: ignore[no-untyped-def]
    agent_run = _agent_run_row(db_engine, identity)
    return _row(
        db_engine,
        "SELECT id, run_id, state, expires_at, heartbeat_at, fencing_token, "
        "node_id, node_fencing_token, generation FROM omnibase_meta.run_leases "
        "WHERE run_id = :run",
        run=agent_run["workspace_run_id"],
    )


def _reconciliation_rows(db_engine, identity) -> list[dict[str, object]]:  # type: ignore[no-untyped-def]
    with db_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT attempt_id, task_id, effect_id, agent_run_id, state, reason_code "
                    "FROM omnibase_meta.agent_reconciliation_cases WHERE attempt_id = :attempt"
                ),
                {"attempt": identity.attempt_id},
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]


def _budget_rows(db_engine, identity) -> list[dict[str, object]]:  # type: ignore[no-untyped-def]
    with db_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT dimension, limit_value, reserved, committed, released, remaining "
                    "FROM omnibase_meta.agent_task_budget_ledgers WHERE task_id = :task "
                    "ORDER BY dimension"
                ),
                {"task": identity.task_id},
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]


def _expire_task_lease(db_engine, identity) -> None:  # type: ignore[no-untyped-def]
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE omnibase_meta.agent_task_leases SET expires_at = clock_timestamp() "
                "WHERE attempt_id = :attempt"
            ),
            {"attempt": identity.attempt_id},
        )


def _expire_run_lease(db_engine, identity) -> None:  # type: ignore[no-untyped-def]
    agent_run = _agent_run_row(db_engine, identity)
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE omnibase_meta.run_leases SET expires_at = clock_timestamp() "
                "WHERE run_id = :run"
            ),
            {"run": agent_run["workspace_run_id"]},
        )


def _revoke_run_lease(db_engine, identity) -> None:  # type: ignore[no-untyped-def]
    agent_run = _agent_run_row(db_engine, identity)
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE omnibase_meta.run_leases SET state = 'revoked' " "WHERE run_id = :run"),
            {"run": agent_run["workspace_run_id"]},
        )


def _usage():  # type: ignore[no-untyped-def]
    from omnibase.model_gateway import ModelUsage

    return ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15, reasoning_tokens=0)


def _assert_failed_unknown_matrix(db_engine, identity, *, reason: str) -> None:  # type: ignore[no-untyped-def]
    """P1-2 full row matrix for a derailed/unknown terminal state."""
    task_lease = _task_lease_row(db_engine, identity)
    attempt = _attempt_row(db_engine, identity)
    effect = _effect_row(db_engine, identity)
    task = _task_row(db_engine, identity)
    agent_run = _agent_run_row(db_engine, identity)
    workspace_run = _workspace_run_row(db_engine, identity)
    run_lease = _run_lease_row(db_engine, identity)

    # TaskLease: revoked, heartbeat fixed at the boundary, tokens intact.
    assert task_lease["state"] == "revoked"
    assert task_lease["heartbeat_at"] == task_lease["expires_at"]
    assert int(task_lease["task_fencing_token"]) >= 1
    # Attempt: terminal unknown, current lease binding cleared.
    assert attempt["state"] == "unknown"
    assert attempt["task_lease_id"] is None
    assert attempt["task_fencing_token"] is None
    # Effect: unknown, no fabricated committed result digest.
    assert effect["state"] == "unknown"
    assert effect["result_digest"] is None
    assert str(effect["attempt_id"]) == identity.attempt_id
    assert str(effect["agent_run_id"]) == str(agent_run["id"])
    # Task: blocked_unknown, never completed/succeeded.
    assert task["state"] == "blocked_unknown"
    # AgentRun: terminal failure with ALL runtime/fencing bindings cleared.
    assert agent_run["state"] == "failed"
    assert agent_run["run_lease_id"] is None
    assert agent_run["run_fencing_token"] is None
    assert agent_run["node_id"] is None
    assert agent_run["node_fencing_token"] is None
    assert agent_run["runtime_instance_id"] is None
    assert agent_run["workload_identity_digest"] is None
    # WorkspaceRun: terminal failed, bindings cleared, no success anywhere.
    assert workspace_run["observed_state"] == "failed"
    assert workspace_run["desired_state"] == "stopped"
    assert workspace_run["runtime_instance_id"] is None
    assert workspace_run["workload_identity_digest"] is None
    assert workspace_run["last_result_digest"] is None
    assert workspace_run["last_error_code"] == reason
    # RunLease: terminal revoked/expired, never active, never renewed.
    assert run_lease["state"] != "active"
    assert run_lease["state"] in {"expired", "revoked", "completed"}
    assert int(run_lease["fencing_token"]) >= 1
    # Reconciliation: exactly one case, precise reason and full bindings
    # (attempt/task/effect/agent-run; the agent run carries the
    # workspace_run binding, so the case is transitively bound to the run).
    cases = _reconciliation_rows(db_engine, identity)
    assert len(cases) == 1
    case = cases[0]
    assert case["reason_code"] == reason
    assert case["state"] == "open"
    assert str(case["attempt_id"]) == identity.attempt_id
    assert str(case["task_id"]) == identity.task_id
    assert str(case["effect_id"]) == identity.effect_id
    assert str(case["agent_run_id"]) == str(agent_run["id"])
    # Budget: nothing committed/released for an unknown outcome; the input
    # and output reservations stay reserved (charging policy for unknown).
    budget = {row["dimension"]: row for row in _budget_rows(db_engine, identity)}
    for dimension in ("input_tokens", "output_tokens", "model_calls"):
        row = budget[dimension]
        assert int(row["reserved"]) >= 0
        assert int(row["committed"]) == 0
        assert int(row["released"]) == 0
        assert int(row["remaining"]) == int(row["limit_value"]) - int(row["reserved"])


# ---------------------------------------------------------------------------
# Scenario A — Task Lease expired + Run Lease live: committed derails unknown.
# ---------------------------------------------------------------------------


def test_a_task_lease_expired_run_lease_live_committed_derails_unknown(
    db_engine, run_owned_resources
) -> None:  # type: ignore[no-untyped-def]
    tenant_id, workspace_id, version, binding = _setup(
        db_engine, run_owned_resources, "a-task-live-run"
    )
    adapter, identity = _begin_invocation(
        db_engine, tenant_id, workspace_id, version, binding, "a-task-live-run"
    )
    time.sleep(1.1)
    _expire_task_lease(db_engine, identity)
    adapter.complete(
        identity=identity,
        result_digest=canonical_digest({"answer": "probe"}),
        usage=_usage(),
    )
    _assert_failed_unknown_matrix(db_engine, identity, reason="agent_alpha_task_lease_expired")


# ---------------------------------------------------------------------------
# Scenario B — Task Lease expired + Run Lease expired: historical holder close.
# ---------------------------------------------------------------------------


def test_b_both_leases_expired_closes_historical_holder_and_releases_slot(
    db_engine, run_owned_resources
) -> None:  # type: ignore[no-untyped-def]
    tenant_id, workspace_id, version, binding = _setup(
        db_engine, run_owned_resources, "b-double-expired"
    )
    adapter, identity = _begin_invocation(
        db_engine, tenant_id, workspace_id, version, binding, "b-double-expired"
    )
    time.sleep(1.1)
    _expire_task_lease(db_engine, identity)
    _expire_run_lease(db_engine, identity)
    adapter.complete(
        identity=identity,
        result_digest=canonical_digest({"answer": "probe"}),
        usage=_usage(),
    )
    _assert_failed_unknown_matrix(db_engine, identity, reason="agent_alpha_task_lease_expired")
    # The WorkspaceRun observed_state is terminal, so the unique partial
    # index workspace_runs_one_active_uq no longer holds the slot.
    workspace_run = _workspace_run_row(db_engine, identity)
    assert workspace_run["observed_state"] == "failed"
    assert (
        _scalar(
            db_engine,
            "SELECT count(*) FROM omnibase_meta.workspace_runs "
            "WHERE workspace_id = :ws AND observed_state IN "
            "('leased','starting','running','pausing','stopping')",
            ws=workspace_id,
        )
        == 0
    )


# ---------------------------------------------------------------------------
# Scenario C — Task Lease expired + Run Lease revoked: never revived.
# ---------------------------------------------------------------------------


def test_c_run_lease_revoked_is_not_revived_and_holder_closes_failed(
    db_engine, run_owned_resources
) -> None:  # type: ignore[no-untyped-def]
    tenant_id, workspace_id, version, binding = _setup(
        db_engine, run_owned_resources, "c-revoked-run"
    )
    adapter, identity = _begin_invocation(
        db_engine, tenant_id, workspace_id, version, binding, "c-revoked-run"
    )
    time.sleep(1.1)
    _expire_task_lease(db_engine, identity)
    _revoke_run_lease(db_engine, identity)
    adapter.fail(identity=identity, outcome="unknown", error_code="agent_alpha_sse_disconnected")
    _assert_failed_unknown_matrix(db_engine, identity, reason="agent_alpha_sse_disconnected")
    run_lease = _run_lease_row(db_engine, identity)
    assert run_lease["state"] == "revoked"  # revoked stays revoked, never revived


# ---------------------------------------------------------------------------
# Scenario D — Node fencing advanced: stale holder fails closed.
# ---------------------------------------------------------------------------


def test_d_advanced_node_fencing_stale_holder_fails_closed(db_engine, run_owned_resources) -> None:  # type: ignore[no-untyped-def]
    from omnibase.workspaces.service import LeaseRejected, close_historical_run_holder

    tenant_id, workspace_id, version, binding = _setup(
        db_engine, run_owned_resources, "d-node-fencing"
    )
    adapter, identity = _begin_invocation(
        db_engine, tenant_id, workspace_id, version, binding, "d-node-fencing"
    )
    del adapter
    agent_run = _agent_run_row(db_engine, identity)
    run_lease = _run_lease_row(db_engine, identity)
    with _session(db_engine, tenant_id) as session:
        with pytest.raises(LeaseRejected):
            close_historical_run_holder(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                workspace_run_id=str(agent_run["workspace_run_id"]),
                run_lease_id=str(run_lease["id"]),
                node_id=str(run_lease["node_id"]),
                generation=int(run_lease["generation"]),
                run_fencing_token=int(run_lease["fencing_token"]),
                node_fencing_token=int(run_lease["node_fencing_token"]) + 1,  # advanced
                observed_state="failed",
                error_code="agent_alpha_task_lease_expired",
            )
        session.rollback()
    # Nothing was modified: run/lease untouched, slot still occupied.
    assert _workspace_run_row(db_engine, identity)["observed_state"] == "running"
    assert _run_lease_row(db_engine, identity)["state"] == "active"


# ---------------------------------------------------------------------------
# Scenario E — Workspace generation drift: stale invocation fails closed.
# ---------------------------------------------------------------------------


def test_e_workspace_generation_drift_stale_invocation_fails_closed(
    db_engine, run_owned_resources
) -> None:  # type: ignore[no-untyped-def]
    from omnibase.workspaces.service import LeaseRejected, close_historical_run_holder

    tenant_id, workspace_id, version, binding = _setup(
        db_engine, run_owned_resources, "e-generation-drift"
    )
    adapter, identity = _begin_invocation(
        db_engine, tenant_id, workspace_id, version, binding, "e-generation-drift"
    )
    del adapter
    agent_run = _agent_run_row(db_engine, identity)
    run_lease = _run_lease_row(db_engine, identity)
    with _session(db_engine, tenant_id) as session:
        with pytest.raises(LeaseRejected):
            close_historical_run_holder(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                workspace_run_id=str(agent_run["workspace_run_id"]),
                run_lease_id=str(run_lease["id"]),
                node_id=str(run_lease["node_id"]),
                generation=int(run_lease["generation"]) + 1,  # drift
                run_fencing_token=int(run_lease["fencing_token"]),
                node_fencing_token=int(run_lease["node_fencing_token"]),
                observed_state="failed",
                error_code="agent_alpha_task_lease_expired",
            )
        session.rollback()
    assert _workspace_run_row(db_engine, identity)["observed_state"] == "running"


# ---------------------------------------------------------------------------
# Scenario F — wrong RunLease / wrong TaskLease / wrong binding: all refused.
# ---------------------------------------------------------------------------


def test_f_wrong_identities_are_refused(db_engine, run_owned_resources) -> None:  # type: ignore[no-untyped-def]
    from omnibase.task_ledger.service import (
        TaskLedgerConflict,
        TaskLedgerPersistenceService,
    )
    from omnibase.workspaces.service import LeaseRejected, close_historical_run_holder

    tenant_id, workspace_id, version, binding = _setup(
        db_engine, run_owned_resources, "f-wrong-ids"
    )
    adapter, identity = _begin_invocation(
        db_engine, tenant_id, workspace_id, version, binding, "f-wrong-ids"
    )
    del adapter
    agent_run = _agent_run_row(db_engine, identity)
    run_lease = _run_lease_row(db_engine, identity)
    task_lease = _task_lease_row(db_engine, identity)

    # F1 — wrong RunLease id: refused by the historical holder path.
    with _session(db_engine, tenant_id) as session:
        with pytest.raises(LeaseRejected):
            close_historical_run_holder(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                workspace_run_id=str(agent_run["workspace_run_id"]),
                run_lease_id=str(uuid.uuid4()),
                node_id=str(run_lease["node_id"]),
                generation=int(run_lease["generation"]),
                run_fencing_token=int(run_lease["fencing_token"]),
                node_fencing_token=int(run_lease["node_fencing_token"]),
                observed_state="failed",
                error_code="agent_alpha_task_lease_expired",
            )
        session.rollback()
    # F2 — wrong TaskLease id: refused by finish_attempt (stale).
    with _session(db_engine, tenant_id) as session:
        svc = TaskLedgerPersistenceService(session)
        with pytest.raises(TaskLedgerConflict, match="task_attempt_finish_stale"):
            svc.finish_attempt(
                tenant_id=tenant_id,
                attempt_id=identity.attempt_id,
                task_lease_id=str(uuid.uuid4()),
                task_fencing_token=int(task_lease["task_fencing_token"]),
                outcome="unknown",
            )
        session.rollback()
    # F3 — wrong AgentRun/WorkspaceRun binding: a workspace run id that
    # does not exist can never be closed (the run and its lease lookup are
    # bound to that id, so the holder never matches).
    with _session(db_engine, tenant_id) as session:
        with pytest.raises(LeaseRejected):
            close_historical_run_holder(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                workspace_run_id=str(uuid.uuid4()),
                run_lease_id=str(run_lease["id"]),
                node_id=str(run_lease["node_id"]),
                generation=int(run_lease["generation"]),
                run_fencing_token=int(run_lease["fencing_token"]),
                node_fencing_token=int(run_lease["node_fencing_token"]),
                observed_state="failed",
                error_code="agent_alpha_task_lease_expired",
            )
        session.rollback()
    # Nothing changed anywhere.
    assert _workspace_run_row(db_engine, identity)["observed_state"] == "running"
    assert _run_lease_row(db_engine, identity)["state"] == "active"
    assert _task_lease_row(db_engine, identity)["state"] == "active"
    assert _task_row(db_engine, identity)["state"] == "running"


# ---------------------------------------------------------------------------
# Scenario G — a follow-on failure rolls the whole terminal transition back.
# ---------------------------------------------------------------------------


def test_g_follow_on_failure_rolls_back_everything(db_engine, run_owned_resources) -> None:  # type: ignore[no-untyped-def]
    from omnibase.task_ledger.service import TaskLedgerPersistenceService
    from omnibase.workspaces.service import close_historical_run_holder

    tenant_id, workspace_id, version, binding = _setup(db_engine, run_owned_resources, "g-rollback")
    adapter, identity = _begin_invocation(
        db_engine, tenant_id, workspace_id, version, binding, "g-rollback"
    )
    del adapter
    time.sleep(1.1)
    _expire_task_lease(db_engine, identity)
    _expire_run_lease(db_engine, identity)
    agent_run = _agent_run_row(db_engine, identity)
    run_lease = _run_lease_row(db_engine, identity)
    with _session(db_engine, tenant_id) as session:
        svc = TaskLedgerPersistenceService(session)
        svc.finish_attempt(
            tenant_id=tenant_id,
            attempt_id=identity.attempt_id,
            task_lease_id=str(_task_lease_row(db_engine, identity)["id"]),
            task_fencing_token=int(_task_lease_row(db_engine, identity)["task_fencing_token"]),
            outcome="unknown",
        )
        close_historical_run_holder(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            workspace_run_id=str(agent_run["workspace_run_id"]),
            run_lease_id=str(run_lease["id"]),
            node_id=str(run_lease["node_id"]),
            generation=int(run_lease["generation"]),
            run_fencing_token=int(run_lease["fencing_token"]),
            node_fencing_token=int(run_lease["node_fencing_token"]),
            observed_state="failed",
            error_code="agent_alpha_task_lease_expired",
        )
        # Deliberate follow-on failure inside the same transaction: a
        # duplicate primary key on agent_tasks must abort the whole unit.
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO omnibase_meta.agent_tasks "
                    "(id, tenant_id, workspace_id, workspace_generation, actor_user_id, "
                    "agent_definition_id, agent_version_id, agent_version_digest, "
                    "workspace_agent_binding_id, plan_id, plan_version, plan_digest, "
                    "deadline, state, resource_scope_digest, budget_policy_digest, "
                    "request_hash) "
                    "VALUES (:id, :tenant, :ws, 1, :actor, :defn, :ver, :digest, :binding, "
                    ":plan, 1, :digest, :deadline, 'created', :digest, :digest, :digest)"
                ),
                {
                    "id": identity.task_id,  # duplicate pk -> IntegrityError
                    "tenant": tenant_id,
                    "ws": workspace_id,
                    "actor": ACTOR_ID,
                    "defn": version.agent_definition_id,
                    "ver": version.agent_version_id,
                    "digest": "b" * 64,
                    "binding": binding.id,
                    "plan": str(uuid.uuid4()),
                    "deadline": "2026-12-31T00:00:00Z",
                },
            )
        session.rollback()
    # The whole terminal transition rolled back: task/attempt/lease/run all
    # still occupy their pre-transition states.
    assert _task_row(db_engine, identity)["state"] == "running"
    assert _attempt_row(db_engine, identity)["state"] == "dispatching"
    assert _task_lease_row(db_engine, identity)["state"] == "active"
    assert _run_lease_row(db_engine, identity)["state"] == "active"
    assert _workspace_run_row(db_engine, identity)["observed_state"] == "running"


# ---------------------------------------------------------------------------
# Scenario H — after a successful close the next invocation starts at once.
# ---------------------------------------------------------------------------


def test_h_slot_released_next_invocation_starts_immediately(db_engine, run_owned_resources) -> None:  # type: ignore[no-untyped-def]
    tenant_id, workspace_id, version, binding = _setup(
        db_engine, run_owned_resources, "h-next-invocation"
    )
    adapter, identity = _begin_invocation(
        db_engine, tenant_id, workspace_id, version, binding, "h-next-invocation"
    )
    time.sleep(1.1)
    _expire_task_lease(db_engine, identity)
    _expire_run_lease(db_engine, identity)
    adapter.complete(
        identity=identity,
        result_digest=canonical_digest({"answer": "probe"}),
        usage=_usage(),
    )
    _assert_failed_unknown_matrix(db_engine, identity, reason="agent_alpha_task_lease_expired")
    # The interactive slot is free: a second begin() succeeds immediately.
    adapter2, identity2 = _begin_invocation(
        db_engine, tenant_id, workspace_id, version, binding, "h-next-invocation"
    )
    assert identity2.invocation_id != identity.invocation_id
    adapter2.complete(
        identity=identity2,
        result_digest=canonical_digest({"answer": "second"}),
        usage=_usage(),
    )
    assert _task_row(db_engine, identity2)["state"] == "succeeded"
