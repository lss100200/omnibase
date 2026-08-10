"""Internal P5.2B Task ledger transaction service.

The service owns no transaction and performs no model or tool invocation.
Provider calls may begin only after the caller commits a claimed Attempt and a
reserved Effect.  Production composition remains unavailable by default.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, cast
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from omnibase.agent_registry.models import (
    AgentDefinitionModel,
    AgentVersionModel,
    WorkspaceAgentBindingModel,
)
from omnibase.control_plane.models import OperationRecord
from omnibase.control_plane.service import (
    IdempotencyConflict,
    append_audit_event,
    complete_idempotency,
    create_operation,
    register_resource,
    reserve_idempotency,
)
from omnibase.db.models import Tenant
from omnibase.db.tenant import User
from omnibase.task_ledger.models import (
    AgentAttemptModel,
    AgentCheckpointModel,
    AgentReconciliationCaseModel,
    AgentRunModel,
    AgentStepDependencyModel,
    AgentStepModel,
    AgentTaskBudgetLedgerModel,
    AgentTaskEffectModel,
    AgentTaskFencingCursorModel,
    AgentTaskLeaseModel,
    AgentTaskModel,
)
from omnibase.workspaces.models import (
    RunLease,
    Workspace,
    WorkspaceMembership,
    WorkspaceNode,
    WorkspaceRun,
)

_IDEMPOTENCY_TTL = timedelta(hours=24)
_LEASE_TTL_CEILING = timedelta(seconds=300)
_ACTIVE_ATTEMPT_STATES = ("leased", "dispatching", "running")
_TERMINAL_ATTEMPT_STATES = ("committed", "failed", "unknown", "cancelled")
_TERMINAL_OUTCOME = Literal["committed", "failed", "unknown", "cancelled"]


def settle_terminal_outcome(
    *, now: datetime, expires_at: datetime, outcome: _TERMINAL_OUTCOME
) -> _TERMINAL_OUTCOME:
    """An expired Task Lease must never commit success.

    The lease window is the live authorization for the attempt.  When the
    terminalization arrives at or after ``expires_at`` (client-disconnected
    stream that only finishes at the provider tail, or a long stream whose
    TTL is deliberately shorter than the invocation deadline), the lease
    can no longer authorize ``committed``: the outcome derails to
    ``unknown`` (never ``failed``/``cancelled``, which would fabricate a
    deterministic terminal state we cannot prove, and never a successful
    commit).  ``unknown`` is terminal and only ever opens reconciliation;
    it is never replayed.  ``failed``/``unknown``/``cancelled`` outcomes
    are unaffected because they are not authorizations.
    """
    if outcome == "committed" and now >= expires_at:
        return "unknown"
    return outcome


_BUDGET_DIMENSIONS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
        "cost_micros",
        "model_calls",
        "tool_calls",
        "wall_clock_ms",
        "artifact_bytes",
        "sandbox_jobs",
        "max_attempts",
        "max_parallel_steps",
    }
)


class _RowCountResult(Protocol):
    rowcount: int


def _rowcount(result: object) -> int:
    return cast("_RowCountResult", result).rowcount


class TaskLedgerError(ValueError):
    """Stable logical error; never contains SQL or physical locators."""


class TaskLedgerNotFound(TaskLedgerError):
    pass


class TaskLedgerConflict(TaskLedgerError):
    pass


class TaskLedgerStateError(TaskLedgerError):
    pass


def canonical_digest(payload: object) -> str:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _lock_tenant(session: Session, tenant_id: str) -> Tenant:
    tenant = session.execute(
        select(Tenant).where(Tenant.id == tenant_id).with_for_update()
    ).scalar_one_or_none()
    if tenant is None:
        raise TaskLedgerNotFound("task_tenant_not_found")
    if not tenant.is_active:
        raise TaskLedgerStateError("task_tenant_inactive")
    return tenant


def _lock_actor(session: Session, actor_user_id: str) -> User:
    actor = session.execute(
        select(User).where(User.id == actor_user_id, User.is_active.is_(True)).with_for_update()
    ).scalar_one_or_none()
    if actor is None:
        raise TaskLedgerStateError("task_actor_inactive_or_missing")
    return actor


def _lock_workspace_access(
    session: Session, *, tenant_id: str, workspace_id: str, actor_user_id: str
) -> tuple[Workspace, WorkspaceMembership]:
    workspace = session.execute(
        select(Workspace)
        .where(Workspace.id == workspace_id, Workspace.tenant_id == tenant_id)
        .with_for_update()
    ).scalar_one_or_none()
    if workspace is None:
        raise TaskLedgerNotFound("task_workspace_not_found")
    if workspace.observed_state == "archived":
        raise TaskLedgerStateError("task_workspace_archived")
    membership = session.execute(
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.tenant_id == tenant_id,
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == actor_user_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if membership is None or membership.state != "active":
        raise TaskLedgerStateError("task_workspace_membership_inactive")
    if membership.role not in {"member", "operator", "maintainer", "owner"}:
        raise TaskLedgerStateError("task_workspace_role_insufficient")
    return workspace, membership


def _task_for_update(session: Session, *, tenant_id: str, task_id: str) -> AgentTaskModel:
    task = session.execute(
        select(AgentTaskModel)
        .where(AgentTaskModel.id == task_id, AgentTaskModel.tenant_id == tenant_id)
        .with_for_update()
    ).scalar_one_or_none()
    if task is None:
        raise TaskLedgerNotFound("task_not_found")
    return task


def _attempt_for_update(session: Session, *, tenant_id: str, attempt_id: str) -> AgentAttemptModel:
    attempt = session.execute(
        select(AgentAttemptModel)
        .where(AgentAttemptModel.id == attempt_id, AgentAttemptModel.tenant_id == tenant_id)
        .with_for_update()
    ).scalar_one_or_none()
    if attempt is None:
        raise TaskLedgerNotFound("task_attempt_not_found")
    return attempt


def _lock_alpha_binding(
    session: Session,
    *,
    tenant_id: str,
    workspace: Workspace,
    workspace_agent_binding_id: str,
) -> tuple[WorkspaceAgentBindingModel, AgentDefinitionModel, AgentVersionModel]:
    binding = session.execute(
        select(WorkspaceAgentBindingModel)
        .where(
            WorkspaceAgentBindingModel.id == workspace_agent_binding_id,
            WorkspaceAgentBindingModel.tenant_id == tenant_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if binding is None:
        raise TaskLedgerNotFound("task_agent_binding_not_found")
    definition = session.execute(
        select(AgentDefinitionModel)
        .where(
            AgentDefinitionModel.id == binding.agent_definition_id,
            AgentDefinitionModel.tenant_id == tenant_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    version = session.execute(
        select(AgentVersionModel)
        .where(
            AgentVersionModel.id == binding.agent_version_id,
            AgentVersionModel.tenant_id == tenant_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if definition is None or version is None:
        raise TaskLedgerNotFound("task_agent_identity_not_found")
    if (
        definition.definition_state != "active"
        or version.version_state != "sealed"
        or binding.binding_state != "installed"
    ):
        raise TaskLedgerStateError("task_agent_binding_not_live")
    if binding.workspace_id != workspace.id or binding.workspace_generation != workspace.generation:
        raise TaskLedgerConflict("task_workspace_generation_stale")
    if version.definition_id != definition.id or binding.agent_definition_id != definition.id:
        raise TaskLedgerConflict("task_agent_definition_mismatch")
    if binding.agent_version_digest != version.manifest_digest:
        raise TaskLedgerConflict("task_agent_version_digest_mismatch")
    if version.allowed_tool_ids:
        raise TaskLedgerStateError("task_alpha_tools_forbidden")
    if version.risk_level != "low":
        raise TaskLedgerStateError("task_alpha_low_risk_only")
    return binding, definition, version


def _validate_budget_limits(budget_limits: dict[str, int]) -> None:
    if set(budget_limits) != _BUDGET_DIMENSIONS:
        raise TaskLedgerStateError("task_budget_dimensions_invalid")
    if any(value < 1 for value in budget_limits.values()):
        raise TaskLedgerStateError("task_budget_dimensions_invalid")


class TaskLedgerPersistenceService:
    """Tenant-safe P5.2B persistence in one caller-owned SQLAlchemy Session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_task(
        self,
        *,
        tenant_id: str,
        actor_user_id: str,
        request_id: str,
        idempotency_key: str,
        task_id: str,
        workspace_id: str,
        workspace_agent_binding_id: str,
        plan_id: str,
        plan_version: int,
        plan_digest: str,
        deadline: datetime,
        resource_scope_digest: str,
        budget_policy_digest: str,
        budget_limits: dict[str, int],
        request_hash_override: str | None = None,
    ) -> AgentTaskModel:
        """Create one low-risk, tool-free task with Resource/Operation/Audit atomically."""

        _lock_tenant(self._session, tenant_id)
        _lock_actor(self._session, actor_user_id)
        workspace, _ = _lock_workspace_access(
            self._session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
        )
        binding, definition, version = _lock_alpha_binding(
            self._session,
            tenant_id=tenant_id,
            workspace=workspace,
            workspace_agent_binding_id=workspace_agent_binding_id,
        )
        _validate_budget_limits(budget_limits)

        payload = {
            "task_id": task_id,
            "tenant_id": tenant_id,
            "actor_user_id": actor_user_id,
            "workspace_id": workspace_id,
            "workspace_generation": workspace.generation,
            "binding_id": binding.id,
            "agent_version_id": version.id,
            "agent_version_digest": version.manifest_digest,
            "plan_id": plan_id,
            "plan_version": plan_version,
            "plan_digest": plan_digest,
            "deadline": deadline.isoformat(),
            "resource_scope_digest": resource_scope_digest,
            "budget_policy_digest": budget_policy_digest,
            "budget_limits": budget_limits,
        }
        if request_hash_override is not None:
            if not re.fullmatch(r"[0-9a-f]{64}", request_hash_override):
                raise TaskLedgerStateError("task_request_hash_override_invalid")
            payload["caller_intent_hash"] = request_hash_override
        request_hash = canonical_digest(payload)
        try:
            record, inserted = reserve_idempotency(
                self._session,
                tenant_id=tenant_id,
                actor_scope=f"user:{actor_user_id}",
                operation_name="agent.task.create",
                key=idempotency_key,
                request_hash=request_hash,
                expires_at=datetime.now(UTC) + _IDEMPOTENCY_TTL,
            )
        except IdempotencyConflict as exc:
            raise TaskLedgerConflict("task_replay_input_mismatch") from exc
        if not inserted:
            replay_id = (record.response_ref or {}).get("task_id")
            if not isinstance(replay_id, str):
                raise TaskLedgerConflict("task_replay_incomplete")
            replay = self._session.execute(
                select(AgentTaskModel).where(
                    AgentTaskModel.id == replay_id,
                    AgentTaskModel.tenant_id == tenant_id,
                )
            ).scalar_one_or_none()
            if replay is None:
                raise TaskLedgerConflict("task_replay_target_missing")
            return replay

        # The deadline is only validated on the fresh-insert path: an exact
        # replay reproduces the original payload (including the original
        # server-assigned deadline, which is immutable under the task guard),
        # and a replay arriving after that deadline must still return the
        # original durable task instead of failing.
        if deadline.tzinfo is None or deadline <= datetime.now(UTC):
            raise TaskLedgerStateError("task_deadline_invalid")

        resource = register_resource(
            self._session,
            tenant_id=tenant_id,
            kind="agent_task",
            owner_type="workspace",
            owner_id=workspace_id,
            parent_id=workspace_id,
            resource_id=task_id,
            display_name=f"Agent task {task_id[:8]}",
            policy_class="workspace_private",
            metadata={
                "agent_definition_id": definition.id,
                "agent_version_id": version.id,
                "workspace_agent_binding_id": binding.id,
            },
            created_by_actor_id=actor_user_id,
        )
        operation = create_operation(
            self._session,
            tenant_id=tenant_id,
            kind="agent.task.create",
            risk_level="R1",
            actor_type="user",
            actor_id=actor_user_id,
            workspace_id=workspace_id,
            resource_id=resource.id,
            resource_version=resource.version,
            request_hash=request_hash,
            deadline_at=deadline,
        )
        task = AgentTaskModel(
            id=task_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            workspace_generation=workspace.generation,
            actor_user_id=actor_user_id,
            agent_definition_id=definition.id,
            agent_version_id=version.id,
            agent_version_digest=version.manifest_digest,
            workspace_agent_binding_id=binding.id,
            task_generation=1,
            plan_id=plan_id,
            plan_version=plan_version,
            plan_digest=plan_digest,
            deadline=deadline,
            state="created",
            resource_scope_digest=resource_scope_digest,
            budget_policy_digest=budget_policy_digest,
            request_hash=request_hash,
            approval_id=None,
            creation_operation_id=operation.id,
        )
        self._session.add(task)
        # Flush the task row before any row that references it: the fencing
        # cursor and budget ledger rows point at agent_tasks by composite FK
        # and the mappers declare no ORM relationship, so a combined flush may
        # order their INSERTs before the task INSERT.  Task-first is always
        # correct regardless of the unit-of-work table ordering.
        self._session.flush()
        self._session.add(
            AgentTaskFencingCursorModel(
                task_id=task_id,
                tenant_id=tenant_id,
                next_fencing_token=1,
                last_claimed_at=None,
            )
        )
        self._session.add_all(
            AgentTaskBudgetLedgerModel(
                task_id=task_id,
                tenant_id=tenant_id,
                dimension=dimension,
                limit_value=limit_value,
                reserved=0,
                committed=0,
                released=0,
                remaining=limit_value,
                policy_digest=budget_policy_digest,
            )
            for dimension, limit_value in sorted(budget_limits.items())
        )
        self._session.flush()
        complete_idempotency(
            self._session,
            tenant_id=tenant_id,
            record_id=record.id,
            response_ref={"task_id": task.id},
            operation_id=operation.id,
        )
        append_audit_event(
            self._session,
            tenant_id=tenant_id,
            request_id=request_id,
            actor_type="user",
            actor_id=actor_user_id,
            workspace_id=workspace_id,
            resource_id=task.id,
            operation_id=operation.id,
            action="agent.task.created",
            decision="allowed",
            risk_level="R1",
            input_hash=request_hash,
            details={"operation_kind": "agent.task.create", "resource_kind": "agent_task"},
        )
        return task

    def create_run(
        self,
        *,
        tenant_id: str,
        task_id: str,
        workspace_run_id: str,
        run_lease_id: str | None = None,
        runtime_instance_id: str | None = None,
        workload_identity_digest: str | None = None,
    ) -> AgentRunModel:
        task = _task_for_update(self._session, tenant_id=tenant_id, task_id=task_id)
        workspace_run = self._session.execute(
            select(WorkspaceRun)
            .where(
                WorkspaceRun.id == workspace_run_id,
                WorkspaceRun.tenant_id == tenant_id,
                WorkspaceRun.workspace_id == task.workspace_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if workspace_run is None:
            raise TaskLedgerNotFound("task_workspace_run_not_found")
        values: dict[str, object] = {
            "id": str(uuid4()),
            "tenant_id": tenant_id,
            "task_id": task_id,
            "workspace_id": task.workspace_id,
            "workspace_generation": task.workspace_generation,
            "workspace_run_id": workspace_run_id,
            "state": "created",
        }
        if run_lease_id is not None:
            lease = self._lock_live_run_lease(
                tenant_id=tenant_id,
                workspace_id=task.workspace_id,
                workspace_run_id=workspace_run_id,
                run_lease_id=run_lease_id,
            )
            if runtime_instance_id is None or workload_identity_digest is None:
                raise TaskLedgerStateError("task_run_runtime_identity_required")
            values.update(
                state="leased",
                run_lease_id=lease.id,
                run_fencing_token=lease.fencing_token,
                node_id=lease.node_id,
                node_fencing_token=lease.node_fencing_token,
                runtime_instance_id=runtime_instance_id,
                workload_identity_digest=workload_identity_digest,
            )
        run = AgentRunModel(**values)
        self._session.add(run)
        self._session.flush()
        return run

    def create_step(
        self,
        *,
        tenant_id: str,
        task_id: str,
        agent_run_id: str,
        plan_id: str,
        plan_version: int,
        plan_digest: str,
        depends_on_step_ids: tuple[str, ...] = (),
    ) -> AgentStepModel:
        task = _task_for_update(self._session, tenant_id=tenant_id, task_id=task_id)
        run = self._session.execute(
            select(AgentRunModel)
            .where(
                AgentRunModel.id == agent_run_id,
                AgentRunModel.task_id == task_id,
                AgentRunModel.tenant_id == tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if run is None:
            raise TaskLedgerNotFound("task_agent_run_not_found")
        if (plan_id, plan_version, plan_digest) != (
            task.plan_id,
            task.plan_version,
            task.plan_digest,
        ):
            raise TaskLedgerConflict("task_plan_binding_mismatch")
        next_number = (
            int(
                self._session.scalar(
                    select(func.coalesce(func.max(AgentStepModel.step_number), 0)).where(
                        AgentStepModel.task_id == task_id,
                        AgentStepModel.agent_run_id == agent_run_id,
                        AgentStepModel.tenant_id == tenant_id,
                    )
                )
                or 0
            )
            + 1
        )
        step = AgentStepModel(
            id=str(uuid4()),
            tenant_id=tenant_id,
            task_id=task_id,
            agent_run_id=agent_run_id,
            step_number=next_number,
            plan_id=plan_id,
            plan_version=plan_version,
            plan_digest=plan_digest,
            state="pending" if depends_on_step_ids else "ready",
        )
        self._session.add(step)
        self._session.flush()
        for dependency_id in depends_on_step_ids:
            self._session.add(
                AgentStepDependencyModel(
                    step_id=step.id,
                    depends_on_step_id=dependency_id,
                    tenant_id=tenant_id,
                    task_id=task_id,
                    agent_run_id=agent_run_id,
                )
            )
        self._session.flush()
        return step

    def create_attempt(
        self,
        *,
        tenant_id: str,
        task_id: str,
        step_id: str,
        agent_run_id: str,
        deadline: datetime,
    ) -> AgentAttemptModel:
        _task_for_update(self._session, tenant_id=tenant_id, task_id=task_id)
        step = self._session.execute(
            select(AgentStepModel)
            .where(
                AgentStepModel.id == step_id,
                AgentStepModel.task_id == task_id,
                AgentStepModel.agent_run_id == agent_run_id,
                AgentStepModel.tenant_id == tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if step is None:
            raise TaskLedgerNotFound("task_step_not_found")
        if step.state not in {"ready", "running"}:
            raise TaskLedgerStateError("task_step_not_attemptable")
        number = (
            int(
                self._session.scalar(
                    select(func.coalesce(func.max(AgentAttemptModel.attempt_number), 0)).where(
                        AgentAttemptModel.task_id == task_id,
                        AgentAttemptModel.step_id == step_id,
                        AgentAttemptModel.tenant_id == tenant_id,
                    )
                )
                or 0
            )
            + 1
        )
        attempt = AgentAttemptModel(
            id=str(uuid4()),
            tenant_id=tenant_id,
            task_id=task_id,
            step_id=step_id,
            agent_run_id=agent_run_id,
            attempt_number=number,
            state="ready",
            task_lease_id=None,
            task_fencing_token=None,
            expected_previous_state="ready" if number == 1 else "failed",
            deadline=deadline,
        )
        self._session.add(attempt)
        self._session.flush()
        return attempt

    def claim_attempt(
        self,
        *,
        tenant_id: str,
        attempt_id: str,
        run_lease_id: str,
        ttl_seconds: int,
    ) -> AgentTaskLeaseModel:
        if ttl_seconds < 1 or ttl_seconds > int(_LEASE_TTL_CEILING.total_seconds()):
            raise TaskLedgerStateError("task_lease_ttl_invalid")
        attempt = _attempt_for_update(self._session, tenant_id=tenant_id, attempt_id=attempt_id)
        if attempt.state != "ready" or attempt.task_lease_id is not None:
            raise TaskLedgerConflict("task_attempt_not_claimable")
        task = _task_for_update(self._session, tenant_id=tenant_id, task_id=attempt.task_id)
        run = self._session.execute(
            select(AgentRunModel)
            .where(
                AgentRunModel.id == attempt.agent_run_id,
                AgentRunModel.task_id == task.id,
                AgentRunModel.tenant_id == tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if run is None or run.run_lease_id != run_lease_id:
            raise TaskLedgerStateError("task_agent_run_not_live")
        live_run_lease = self._lock_live_run_lease(
            tenant_id=tenant_id,
            workspace_id=task.workspace_id,
            workspace_run_id=run.workspace_run_id,
            run_lease_id=run_lease_id,
        )
        if (
            run.run_fencing_token != live_run_lease.fencing_token
            or run.node_id != live_run_lease.node_id
            or run.node_fencing_token != live_run_lease.node_fencing_token
            or run.workspace_generation != task.workspace_generation
        ):
            raise TaskLedgerStateError("task_run_fencing_stale")
        cursor = self._session.execute(
            select(AgentTaskFencingCursorModel)
            .where(
                AgentTaskFencingCursorModel.task_id == task.id,
                AgentTaskFencingCursorModel.tenant_id == tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if cursor is None:
            raise TaskLedgerStateError("task_fencing_cursor_missing")
        claimed_at = self._session.scalar(select(func.clock_timestamp()))
        if not isinstance(claimed_at, datetime):
            raise TaskLedgerStateError("task_database_clock_unavailable")
        if cursor.last_claimed_at is not None and claimed_at <= cursor.last_claimed_at:
            raise TaskLedgerConflict("task_claim_chronology_ambiguous")
        token = cursor.next_fencing_token
        cursor.next_fencing_token = token + 1
        cursor.last_claimed_at = claimed_at
        self._session.flush()
        lease = AgentTaskLeaseModel(
            id=str(uuid4()),
            tenant_id=tenant_id,
            task_id=task.id,
            attempt_id=attempt.id,
            agent_run_id=run.id,
            run_lease_id=live_run_lease.id,
            run_fencing_token=live_run_lease.fencing_token,
            node_id=live_run_lease.node_id,
            node_fencing_token=live_run_lease.node_fencing_token,
            workspace_generation=task.workspace_generation,
            task_fencing_token=token,
            state="active",
            expires_at=claimed_at + timedelta(seconds=ttl_seconds),
            heartbeat_at=claimed_at,
            created_at=claimed_at,
        )
        self._session.add(lease)
        attempt.state = "leased"
        attempt.task_lease_id = lease.id
        attempt.task_fencing_token = token
        self._session.flush()
        return lease

    def heartbeat_attempt(
        self,
        *,
        tenant_id: str,
        attempt_id: str,
        task_lease_id: str,
        task_fencing_token: int,
        extend_seconds: int,
    ) -> AgentTaskLeaseModel:
        if extend_seconds < 1:
            raise TaskLedgerStateError("task_heartbeat_extension_invalid")
        attempt = _attempt_for_update(self._session, tenant_id=tenant_id, attempt_id=attempt_id)
        lease = self._session.execute(
            select(AgentTaskLeaseModel)
            .where(
                AgentTaskLeaseModel.id == task_lease_id,
                AgentTaskLeaseModel.tenant_id == tenant_id,
                AgentTaskLeaseModel.attempt_id == attempt_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if (
            lease is None
            or lease.state != "active"
            or attempt.task_lease_id != task_lease_id
            or attempt.task_fencing_token != task_fencing_token
            or lease.task_fencing_token != task_fencing_token
        ):
            raise TaskLedgerConflict("task_lease_stale")
        now = self._session.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime) or now >= lease.expires_at:
            raise TaskLedgerStateError("task_lease_expired")
        lease.heartbeat_at = now
        lease.expires_at = min(
            lease.created_at + _LEASE_TTL_CEILING,
            now + timedelta(seconds=extend_seconds),
        )
        self._session.flush()
        return lease

    def finish_attempt(
        self,
        *,
        tenant_id: str,
        attempt_id: str,
        task_lease_id: str,
        task_fencing_token: int,
        outcome: _TERMINAL_OUTCOME,
    ) -> _TERMINAL_OUTCOME:
        if outcome not in _TERMINAL_ATTEMPT_STATES:
            raise TaskLedgerStateError("task_attempt_outcome_invalid")
        attempt = _attempt_for_update(self._session, tenant_id=tenant_id, attempt_id=attempt_id)
        lease = self._session.execute(
            select(AgentTaskLeaseModel)
            .where(
                AgentTaskLeaseModel.id == task_lease_id,
                AgentTaskLeaseModel.tenant_id == tenant_id,
                AgentTaskLeaseModel.attempt_id == attempt_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if (
            lease is None
            or lease.state != "active"
            or attempt.state not in _ACTIVE_ATTEMPT_STATES
            or attempt.task_lease_id != task_lease_id
            or attempt.task_fencing_token != task_fencing_token
            or lease.task_fencing_token != task_fencing_token
        ):
            # A stale or replaced lease id / fencing token is never
            # terminalized by this caller: the attempt belongs to another
            # (possibly newer) lease, so this finish is refused outright.
            raise TaskLedgerConflict("task_attempt_finish_stale")
        now = self._session.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            raise TaskLedgerStateError("task_database_clock_unavailable")
        # Database clock under lock is the only clock.  An expired lease
        # cannot authorize committed; settle first, then atomically close
        # the lease, attempt and all follow-on rows with the SAME outcome.
        settled = settle_terminal_outcome(now=now, expires_at=lease.expires_at, outcome=outcome)
        lease.state = "completed" if settled == "committed" else "revoked"
        # The heartbeat stays inside the lease window
        # (agent_task_leases_heartbeat_window_check); when the window has
        # lapsed it is fixed at the boundary — never extended, never revived.
        lease.heartbeat_at = min(now, lease.expires_at)
        # Terminalize the lease row before clearing the attempt: the
        # agent_attempt_lease_consistency_guard trigger requires that a
        # cleared attempt has no active lease, and the two UPDATEs are emitted
        # in table order, not code order.
        self._session.flush()
        attempt.state = settled
        attempt.task_lease_id = None
        attempt.task_fencing_token = None
        self._session.flush()
        return settled

    def reserve_budget(
        self, *, tenant_id: str, task_id: str, dimension: str, amount: int
    ) -> AgentTaskBudgetLedgerModel:
        if dimension not in _BUDGET_DIMENSIONS or amount < 1:
            raise TaskLedgerStateError("task_budget_reservation_invalid")
        row = self._budget_for_update(tenant_id, task_id, dimension)
        if row.remaining < amount:
            raise TaskLedgerStateError("task_budget_exceeded")
        row.reserved += amount
        row.remaining -= amount
        self._session.flush()
        return row

    def commit_budget(
        self, *, tenant_id: str, task_id: str, dimension: str, amount: int
    ) -> AgentTaskBudgetLedgerModel:
        if amount < 1:
            raise TaskLedgerStateError("task_budget_commit_invalid")
        row = self._budget_for_update(tenant_id, task_id, dimension)
        if row.committed + amount > row.reserved:
            raise TaskLedgerStateError("task_budget_not_reserved")
        row.committed += amount
        self._session.flush()
        return row

    def release_committed_budget(
        self, *, tenant_id: str, task_id: str, dimension: str, amount: int
    ) -> AgentTaskBudgetLedgerModel:
        if amount < 1:
            raise TaskLedgerStateError("task_budget_release_invalid")
        row = self._budget_for_update(tenant_id, task_id, dimension)
        if row.released + amount > row.committed:
            raise TaskLedgerStateError("task_budget_release_exceeds_commit")
        row.released += amount
        self._session.flush()
        return row

    def reserve_effect(
        self,
        *,
        tenant_id: str,
        attempt_id: str,
        operation_id: str,
        request_hash: str,
    ) -> AgentTaskEffectModel:
        attempt = _attempt_for_update(self._session, tenant_id=tenant_id, attempt_id=attempt_id)
        if attempt.state not in {"leased", "dispatching", "running"}:
            raise TaskLedgerStateError("task_effect_attempt_not_active")
        operation = self._session.execute(
            select(OperationRecord)
            .where(
                OperationRecord.id == operation_id,
                OperationRecord.tenant_id == tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if operation is None:
            raise TaskLedgerNotFound("task_effect_operation_not_found")
        existing = self._session.execute(
            select(AgentTaskEffectModel)
            .where(
                AgentTaskEffectModel.task_id == attempt.task_id,
                AgentTaskEffectModel.request_hash == request_hash,
                AgentTaskEffectModel.tenant_id == tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if existing is not None:
            if existing.state == "unknown":
                raise TaskLedgerStateError("task_effect_unknown_requires_reconciliation")
            return existing
        effect = AgentTaskEffectModel(
            id=str(uuid4()),
            tenant_id=tenant_id,
            task_id=attempt.task_id,
            attempt_id=attempt.id,
            agent_run_id=attempt.agent_run_id,
            operation_id=operation_id,
            request_hash=request_hash,
            result_digest=None,
            state="reserved",
        )
        self._session.add(effect)
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise TaskLedgerConflict("task_effect_concurrent_replay") from exc
        return effect

    def finish_effect(
        self,
        *,
        tenant_id: str,
        effect_id: str,
        outcome: Literal["committed", "failed", "unknown"],
        result_digest: str | None = None,
    ) -> AgentTaskEffectModel:
        effect = self._session.execute(
            select(AgentTaskEffectModel)
            .where(
                AgentTaskEffectModel.id == effect_id,
                AgentTaskEffectModel.tenant_id == tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if effect is None:
            raise TaskLedgerNotFound("task_effect_not_found")
        if effect.state not in {"reserved", "dispatching"}:
            raise TaskLedgerStateError("task_effect_terminal")
        if outcome == "committed" and result_digest is None:
            raise TaskLedgerStateError("task_effect_result_digest_required")
        if outcome != "committed" and result_digest is not None:
            raise TaskLedgerStateError("task_effect_result_digest_forbidden")
        effect.state = outcome
        effect.result_digest = result_digest
        self._session.flush()
        return effect

    def create_checkpoint(
        self,
        *,
        tenant_id: str,
        attempt_id: str,
        committed_attempt_results: list[object],
        budget_snapshot: dict[str, object],
    ) -> AgentCheckpointModel:
        attempt = _attempt_for_update(self._session, tenant_id=tenant_id, attempt_id=attempt_id)
        if attempt.state != "committed":
            raise TaskLedgerStateError("task_checkpoint_attempt_not_committed")
        task = _task_for_update(self._session, tenant_id=tenant_id, task_id=attempt.task_id)
        checkpoint = AgentCheckpointModel(
            id=str(uuid4()),
            tenant_id=tenant_id,
            task_id=task.id,
            attempt_id=attempt.id,
            agent_run_id=attempt.agent_run_id,
            committed_plan_version=task.plan_version,
            committed_plan_digest=task.plan_digest,
            committed_attempt_results=committed_attempt_results,
            budget_snapshot=budget_snapshot,
            budget_policy_digest=task.budget_policy_digest,
        )
        self._session.add(checkpoint)
        self._session.flush()
        return checkpoint

    def open_reconciliation(
        self,
        *,
        tenant_id: str,
        attempt_id: str,
        reason_code: str,
        effect_id: str | None = None,
    ) -> AgentReconciliationCaseModel:
        attempt = _attempt_for_update(self._session, tenant_id=tenant_id, attempt_id=attempt_id)
        case = AgentReconciliationCaseModel(
            id=str(uuid4()),
            tenant_id=tenant_id,
            task_id=attempt.task_id,
            attempt_id=attempt.id,
            agent_run_id=attempt.agent_run_id,
            effect_id=effect_id,
            state="open",
            reason_code=reason_code,
            resolution_note=None,
            resolved_at=None,
        )
        self._session.add(case)
        self._session.flush()
        return case

    def resolve_reconciliation(
        self, *, tenant_id: str, case_id: str, resolution_note: str
    ) -> AgentReconciliationCaseModel:
        case = self._session.execute(
            select(AgentReconciliationCaseModel)
            .where(
                AgentReconciliationCaseModel.id == case_id,
                AgentReconciliationCaseModel.tenant_id == tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if case is None:
            raise TaskLedgerNotFound("task_reconciliation_not_found")
        if case.state != "open":
            raise TaskLedgerStateError("task_reconciliation_terminal")
        case.state = "resolved"
        case.resolution_note = resolution_note
        case.resolved_at = datetime.now(UTC)
        self._session.flush()
        return case

    def _budget_for_update(
        self, tenant_id: str, task_id: str, dimension: str
    ) -> AgentTaskBudgetLedgerModel:
        row = self._session.execute(
            select(AgentTaskBudgetLedgerModel)
            .where(
                AgentTaskBudgetLedgerModel.task_id == task_id,
                AgentTaskBudgetLedgerModel.tenant_id == tenant_id,
                AgentTaskBudgetLedgerModel.dimension == dimension,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise TaskLedgerNotFound("task_budget_dimension_not_found")
        return row

    def _lock_live_run_lease(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        workspace_run_id: str,
        run_lease_id: str,
    ) -> RunLease:
        lease = self._session.execute(
            select(RunLease)
            .where(
                RunLease.id == run_lease_id,
                RunLease.tenant_id == tenant_id,
                RunLease.workspace_id == workspace_id,
                RunLease.run_id == workspace_run_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if lease is None or lease.state != "active" or lease.expires_at <= datetime.now(UTC):
            raise TaskLedgerStateError("task_run_lease_inactive")
        node = self._session.execute(
            select(WorkspaceNode)
            .where(
                WorkspaceNode.id == lease.node_id,
                WorkspaceNode.tenant_id == tenant_id,
                WorkspaceNode.workspace_id == workspace_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if (
            node is None
            or node.state != "active"
            or node.attestation_state != "verified"
            or node.fencing_token != lease.node_fencing_token
        ):
            raise TaskLedgerStateError("task_node_attestation_stale")
        return lease


__all__ = [
    "TaskLedgerConflict",
    "TaskLedgerError",
    "TaskLedgerNotFound",
    "TaskLedgerPersistenceService",
    "TaskLedgerStateError",
    "canonical_digest",
]
