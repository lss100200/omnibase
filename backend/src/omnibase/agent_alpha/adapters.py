"""DB-backed adapters for the engineering-only Agent Alpha composition.

These adapters are the only database touchpoints of the Alpha service.  Every
operation runs in a caller-owned transaction (one session per call, commit or
rollback, then close) and never exposes physical schema/table/column names,
connection strings, credentials or provider details.  The default Browser
composition never constructs them: ``agent_alpha/engineering.py`` decides
whether the engineering-only seam may assemble the DB-backed service.

Registry reads reuse the P5.1B sealed models and the P5.1C lock order; the
durable Task/Run/Step/Attempt/Lease/Budget/Effect lifecycle reuses the P5.2B
``TaskLedgerPersistenceService`` (migration ``0011`` tables).  No second
registry or ledger state machine is created.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import desc, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from omnibase.agent_alpha.contracts import (
    AlphaAgentProfile,
    AlphaContextChunk,
    AlphaInvocationIdentity,
)
from omnibase.agent_registry.models import (
    AgentDefinitionModel,
    AgentVersionModel,
    WorkspaceAgentBindingModel,
)
from omnibase.control_plane.models import IdempotencyRecord
from omnibase.control_plane.service import create_operation
from omnibase.core.logging import get_logger
from omnibase.db.models import Tenant
from omnibase.db.tenant import User
from omnibase.model_gateway import ModelUsage
from omnibase.onboarding import ensure_local_model_runtime_anchor
from omnibase.task_ledger.models import (
    AgentAttemptModel,
    AgentRunModel,
    AgentTaskEffectModel,
    AgentTaskModel,
)
from omnibase.task_ledger.service import (
    TaskLedgerConflict,
    TaskLedgerError,
    TaskLedgerPersistenceService,
    TaskLedgerStateError,
)
from omnibase.workspace_data.models import WorkspaceDerivedIndex
from omnibase.workspace_data.tenant_models import WorkspaceDerivedChunkV2
from omnibase.workspaces.models import Workspace, WorkspaceMembership, WorkspaceNode
from omnibase.workspaces.service import (
    WorkspaceError,
    bind_run_runtime_identity,
    claim_run_lease,
    submit_run_state,
)
from omnibase.workspaces.service import (
    create_run as create_workspace_run,
)

log = get_logger(__name__)

_ALPHA_MEMBER_ROLES = frozenset({"member", "operator", "maintainer", "owner"})
_ALPHA_ACTIVE_ATTEMPT_STATES = frozenset({"leased", "dispatching", "running"})
_ALPHA_MAX_RAG_CHUNKS = 8
_ALPHA_MAX_RAG_CHUNK_CHARACTERS = 1200
_ALPHA_MAX_RAG_CONTEXT_CHARACTERS = 8_000
_ALPHA_INVOCATION_DEADLINE = timedelta(seconds=110)
_ALPHA_LEASE_TTL_SECONDS = 90
_ALPHA_WORKSPACE_RUN_LEASE_SECONDS = 120
_ALPHA_MODEL_OPERATION = "agent.model.invoke"
_ALPHA_BUDGET_LIMITS = {
    "input_tokens": 64_000,
    "output_tokens": 8_192,
    "reasoning_tokens": 8_192,
    "total_tokens": 72_000,
    "cost_micros": 5_000_000,
    "model_calls": 1,
    "tool_calls": 1,
    "wall_clock_ms": 75_000,
    "artifact_bytes": 1_000_000,
    "sandbox_jobs": 1,
    "max_attempts": 1,
    "max_parallel_steps": 1,
}


class AlphaAdapterError(RuntimeError):
    """Stable adapter failure without SQL, locators or secrets."""


class AlphaAdapterUnavailable(AlphaAdapterError):
    """A required dependency is missing in the current environment."""


def canonical_digest(payload: object) -> str:
    value = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _tenant_session(factory: sessionmaker[Session], tenant_id: str) -> Session:
    """Open a session whose transaction resolves unqualified names against the tenant schema."""
    session = factory()
    try:
        schema_name = session.execute(
            select(Tenant.schema_name).where(Tenant.id == tenant_id)
        ).scalar_one_or_none()
    except Exception:
        session.rollback()
        session.close()
        raise
    if schema_name is None:
        session.close()
        raise AlphaAdapterUnavailable("agent_alpha_tenant_missing")
    session.execute(text(f'SET LOCAL search_path TO "{schema_name}", omnibase_meta, public'))
    return session


def _local_runtime_node(
    session: Session, *, tenant_id: str, workspace: Workspace, actor_user_id: str
) -> WorkspaceNode | None:
    """Resolve or renew only this deployment's server-created model node."""
    try:
        return ensure_local_model_runtime_anchor(
            session,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            workspace=workspace,
        )
    except WorkspaceError:
        return None


def _terminalize_workspace_run(
    session: Session,
    *,
    tenant_id: str,
    run: AgentRunModel | None,
    outcome: Literal["committed", "failed", "unknown", "cancelled"],
    result_digest: str | None,
    error_code: str,
) -> None:
    if run is None:
        raise AlphaAdapterUnavailable("agent_alpha_run_missing")
    workspace_run_id = run.workspace_run_id
    lease_id = run.run_lease_id
    node_id = run.node_id
    fencing_token = run.run_fencing_token
    if lease_id is None or node_id is None or fencing_token is None:
        raise AlphaAdapterUnavailable("agent_alpha_run_binding_missing")
    terminal_state = {
        "committed": "succeeded",
        "failed": "failed",
        "unknown": "failed",
        "cancelled": "cancelled",
    }[outcome]
    run.state = terminal_state
    run.run_lease_id = None
    run.run_fencing_token = None
    run.node_id = None
    run.node_fencing_token = None
    run.runtime_instance_id = None
    run.workload_identity_digest = None
    submit_run_state(
        session,
        tenant_id=tenant_id,
        run_id=workspace_run_id,
        lease_id=lease_id,
        node_id=node_id,
        generation=run.workspace_generation,
        fencing_token=fencing_token,
        observed_state=terminal_state,
        result_digest=result_digest,
        error_code=error_code or None,
    )


class RegistryProfileResolver:
    """Resolve the live installed tool-free AgentVersion for a workspace member.

    Every rejection is a stable ``AlphaAdapterError`` code; nothing is guessed
    from a bare UUID, a JWT claim or a pre-transaction snapshot.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._factory = session_factory

    def list_available(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_user_id: str,
    ) -> tuple[AlphaAgentProfile, ...]:
        session = _tenant_session(self._factory, tenant_id)
        try:
            actor = session.execute(
                select(User).where(User.id == actor_user_id, User.is_active.is_(True))
            ).scalar_one_or_none()
            workspace = session.execute(
                select(Workspace).where(
                    Workspace.id == workspace_id,
                    Workspace.tenant_id == tenant_id,
                )
            ).scalar_one_or_none()
            membership = session.execute(
                select(WorkspaceMembership).where(
                    WorkspaceMembership.tenant_id == tenant_id,
                    WorkspaceMembership.workspace_id == workspace_id,
                    WorkspaceMembership.user_id == actor_user_id,
                    WorkspaceMembership.state == "active",
                )
            ).scalar_one_or_none()
            if actor is None or workspace is None or membership is None:
                raise AlphaAdapterUnavailable("agent_alpha_workspace_not_found")
            if membership.role not in _ALPHA_MEMBER_ROLES:
                raise AlphaAdapterUnavailable("agent_alpha_role_insufficient")
            bindings = session.execute(
                select(WorkspaceAgentBindingModel)
                .where(
                    WorkspaceAgentBindingModel.tenant_id == tenant_id,
                    WorkspaceAgentBindingModel.workspace_id == workspace_id,
                    WorkspaceAgentBindingModel.binding_state == "installed",
                    WorkspaceAgentBindingModel.workspace_generation == workspace.generation,
                )
                .order_by(WorkspaceAgentBindingModel.created_at, WorkspaceAgentBindingModel.id)
            ).scalars()
            profiles: list[AlphaAgentProfile] = []
            for binding in bindings:
                try:
                    profiles.append(
                        self._load_identity(session, tenant_id=tenant_id, binding=binding)
                    )
                except AlphaAdapterError:
                    continue
            return tuple(profiles)
        finally:
            session.close()

    def resolve(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_user_id: str,
        agent_version_id: str,
    ) -> AlphaAgentProfile:
        session = _tenant_session(self._factory, tenant_id)
        try:
            actor = session.execute(
                select(User).where(User.id == actor_user_id, User.is_active.is_(True))
            ).scalar_one_or_none()
            if actor is None:
                raise AlphaAdapterUnavailable("agent_alpha_actor_inactive")
            workspace = session.execute(
                select(Workspace)
                .where(Workspace.id == workspace_id, Workspace.tenant_id == tenant_id)
                .with_for_update()
            ).scalar_one_or_none()
            if workspace is None:
                raise AlphaAdapterUnavailable("agent_alpha_workspace_not_found")
            if workspace.observed_state == "archived":
                raise AlphaAdapterUnavailable("agent_alpha_workspace_archived")
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
                raise AlphaAdapterUnavailable("agent_alpha_membership_inactive")
            if membership.role not in _ALPHA_MEMBER_ROLES:
                raise AlphaAdapterUnavailable("agent_alpha_role_insufficient")
            binding = session.execute(
                select(WorkspaceAgentBindingModel)
                .where(
                    WorkspaceAgentBindingModel.tenant_id == tenant_id,
                    WorkspaceAgentBindingModel.workspace_id == workspace_id,
                    WorkspaceAgentBindingModel.agent_version_id == agent_version_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if binding is None:
                raise AlphaAdapterUnavailable("agent_alpha_binding_not_found")
            if binding.binding_state != "installed":
                raise AlphaAdapterUnavailable("agent_alpha_binding_not_live")
            if binding.workspace_generation != workspace.generation:
                raise AlphaAdapterUnavailable("agent_alpha_workspace_generation_stale")
            return self._load_identity(
                session,
                tenant_id=tenant_id,
                binding=binding,
            )
        finally:
            session.close()

    @staticmethod
    def _load_identity(
        session: Session,
        *,
        tenant_id: str,
        binding: WorkspaceAgentBindingModel,
    ) -> AlphaAgentProfile:
        definition = session.execute(
            select(AgentDefinitionModel).where(
                AgentDefinitionModel.id == binding.agent_definition_id,
                AgentDefinitionModel.tenant_id == tenant_id,
            )
        ).scalar_one_or_none()
        version = session.execute(
            select(AgentVersionModel).where(
                AgentVersionModel.id == binding.agent_version_id,
                AgentVersionModel.tenant_id == tenant_id,
            )
        ).scalar_one_or_none()
        if definition is None or version is None:
            raise AlphaAdapterUnavailable("agent_alpha_identity_not_found")
        if definition.definition_state != "active":
            raise AlphaAdapterUnavailable("agent_alpha_definition_not_active")
        if version.version_state != "sealed":
            raise AlphaAdapterUnavailable("agent_alpha_version_not_sealed")
        if version.definition_id != definition.id or binding.agent_definition_id != definition.id:
            raise AlphaAdapterUnavailable("agent_alpha_definition_mismatch")
        if binding.agent_version_digest != version.manifest_digest:
            raise AlphaAdapterUnavailable("agent_alpha_version_digest_mismatch")
        if version.allowed_tool_ids:
            # A tool-bearing version is a valid rejection of this invocation,
            # not an unavailability: same stable code and status (409) as the
            # service-level check.
            raise AlphaAdapterError("agent_alpha_tools_forbidden")
        if version.risk_level != "low":
            raise AlphaAdapterUnavailable("agent_alpha_low_risk_only")
        manifest = version.manifest_payload if isinstance(version.manifest_payload, dict) else {}
        instructions_text = str(manifest.get("instructions", "")).strip()
        if not instructions_text:
            # The sealed manifest carries no instructions: fall back to the
            # server-owned tool-free research posture instead of sending an
            # empty system message.
            instructions_text = (
                "You are a tool-free research assistant. Answer using only the "
                "provided workspace knowledge context and cite it by index. "
                "If the context does not answer the question, say so explicitly."
            )
        return AlphaAgentProfile(
            agent_definition_id=definition.id,
            agent_version_id=version.id,
            agent_version_digest=version.manifest_digest,
            display_name=definition.display_name,
            instructions=instructions_text,
            instructions_digest=version.instructions_digest,
            max_context_tokens=version.max_context_tokens,
            allowed_tool_ids=tuple(str(item) for item in version.allowed_tool_ids),
            workspace_agent_binding_id=binding.id,
            resource_scope_digest=canonical_digest(list(binding.resource_scopes)),
            budget_policy_digest=canonical_digest(binding.default_budget_policy),
        )


class RagKnowledgeRetriever:
    """Read-only, capped, logical-identity RAG retrieval for the Alpha.

    Retrieval reads only ready P34.6 derived-index generations belonging to the
    requested tenant and Workspace.  Canonical tenant-wide RAG is deliberately
    excluded because it cannot prove Workspace authorization.  When retrieval
    itself fails the adapter logs a content-free diagnostic and returns no
    chunks: the Alpha service then explicitly states that no context was
    retrieved.  No physical locator ever leaves this adapter.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._factory = session_factory

    def retrieve(
        self,
        *,
        tenant_id: str,
        tenant_schema: str,
        workspace_id: str,
        query: str,
        top_k: int,
    ) -> tuple[AlphaContextChunk, ...]:
        del tenant_schema
        if top_k < 1:
            raise AlphaAdapterError("agent_alpha_top_k_invalid")
        top_k = min(top_k, _ALPHA_MAX_RAG_CHUNKS)
        session = _tenant_session(self._factory, tenant_id)
        try:
            query_expression = func.plainto_tsquery("pg_catalog.simple", query)
            rank = func.ts_rank(WorkspaceDerivedChunkV2.tsv, query_expression)
            rows = session.execute(
                select(
                    WorkspaceDerivedChunkV2.id,
                    WorkspaceDerivedIndex.source_resource_id,
                    WorkspaceDerivedChunkV2.content,
                    WorkspaceDerivedChunkV2.metadata_,
                    rank.label("rank"),
                )
                .join(
                    WorkspaceDerivedIndex,
                    (WorkspaceDerivedIndex.id == WorkspaceDerivedChunkV2.derived_index_id)
                    & (WorkspaceDerivedIndex.generation == WorkspaceDerivedChunkV2.generation),
                )
                .where(
                    WorkspaceDerivedIndex.tenant_id == tenant_id,
                    WorkspaceDerivedIndex.workspace_id == workspace_id,
                    WorkspaceDerivedIndex.state == "ready",
                    WorkspaceDerivedChunkV2.workspace_id == workspace_id,
                    WorkspaceDerivedChunkV2.tsv.op("@@")(query_expression),
                )
                .order_by(desc("rank"), WorkspaceDerivedChunkV2.chunk_index)
                .limit(top_k)
            ).all()
        except Exception as exc:
            log.warning(
                "agent_alpha.workspace_rag_unavailable",
                error_type=type(exc).__name__,
            )
            return ()
        finally:
            session.close()
        chunks: list[AlphaContextChunk] = []
        total = 0
        for chunk_id, source_resource_id, raw_content, metadata, score in rows:
            content = str(raw_content)[:_ALPHA_MAX_RAG_CHUNK_CHARACTERS]
            total += len(content)
            if total > _ALPHA_MAX_RAG_CONTEXT_CHARACTERS:
                break
            safe_metadata = metadata if isinstance(metadata, dict) else {}
            page_number = safe_metadata.get("page", 1)
            if not isinstance(page_number, int) or page_number < 1:
                page_number = 1
            chunks.append(
                AlphaContextChunk(
                    chunk_id=str(chunk_id),
                    document_id=str(source_resource_id),
                    content=content,
                    score=float(score or 0.0),
                    page_number=page_number,
                )
            )
        return tuple(chunks)


class LedgerInvocationAdapter:
    """Map one Alpha invocation onto the durable P5.2B ledger.

    Transaction A (``begin``) revalidates tenant/user/workspace/membership and
    the live binding via ``TaskLedgerPersistenceService.create_task``, creates
    the Run/Step/Attempt chain, claims an active Task Lease, reserves the model
    budget, creates the pending model Effect and appends Audit -- all in one
    caller-owned transaction that commits before any provider call.  A fresh
    invocation creates one short-lived interactive WorkspaceRun and fenced
    RunLease on the server-created local Model Gateway node.  This anchor is
    not a Sandbox/Runner isolation claim and cannot execute tools.

    Transaction B (``complete``/``fail``) reopens a fresh transaction, reloads
    the live rows, revalidates fencing/deadline/cancellation, terminalizes the
    Effect/Attempt/Run/Task and appends Audit.  An ambiguous provider outcome
    terminalizes the Effect as ``unknown`` and opens a reconciliation case; it
    never auto-replays.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._factory = session_factory

    @staticmethod
    def _replay_payload_anchor(
        session: Session,
        *,
        tenant_id: str,
        actor_user_id: str,
        idempotency_key: str,
    ) -> tuple[str | None, datetime]:
        """Return the durable task identity and deadline for exact replay.

        The task_create canonical payload hashes the pre-assigned task id and
        the server-assigned deadline, so a replay must reproduce the first
        call's payload byte for byte.  The committed idempotency record
        (response_ref -> task id) is the durable anchor: when present, reuse
        the stored task's identity and immutable deadline; otherwise the
        caller pre-assigns a fresh id and ``now + ttl`` deadline.
        """
        record = session.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.tenant_id == tenant_id,
                IdempotencyRecord.actor_scope == f"user:{actor_user_id}",
                IdempotencyRecord.operation_name == "agent.task.create",
                IdempotencyRecord.key == f"alpha-invoke:{idempotency_key}",
            )
        ).scalar_one_or_none()
        replay_id = (record.response_ref or {}).get("task_id") if record is not None else None
        if not isinstance(replay_id, str):
            return None, datetime.now(UTC) + _ALPHA_INVOCATION_DEADLINE
        task = session.execute(
            select(AgentTaskModel).where(
                AgentTaskModel.id == replay_id,
                AgentTaskModel.tenant_id == tenant_id,
            )
        ).scalar_one_or_none()
        if task is None or task.deadline is None:
            return None, datetime.now(UTC) + _ALPHA_INVOCATION_DEADLINE
        return task.id, task.deadline

    def begin(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_user_id: str,
        profile: AlphaAgentProfile,
        idempotency_key: str,
        request_hash: str,
        retry_of: str | None,
    ) -> AlphaInvocationIdentity:
        del retry_of
        session = _tenant_session(self._factory, tenant_id)
        try:
            # Exact replay identity: the task_create canonical payload includes
            # the pre-assigned task id and the server-assigned deadline, so a
            # replay must reproduce the first call's payload byte for byte.
            # The idempotency record (with its response_ref task id) is the
            # durable anchor: when it exists, reuse the stored task's identity
            # and deadline; otherwise pre-assign a fresh id and deadline.
            replay_task_id, deadline = self._replay_payload_anchor(
                session,
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
            )
            svc = TaskLedgerPersistenceService(session)
            task = svc.create_task(
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                request_id=str(uuid.uuid4()),
                idempotency_key=f"alpha-invoke:{idempotency_key}",
                task_id=replay_task_id or str(uuid.uuid4()),
                workspace_id=workspace_id,
                workspace_agent_binding_id=profile.workspace_agent_binding_id,
                plan_id=profile.agent_definition_id,
                plan_version=1,
                plan_digest=profile.agent_version_digest,
                deadline=deadline,
                resource_scope_digest=profile.resource_scope_digest,
                budget_policy_digest=profile.budget_policy_digest,
                budget_limits=dict(_ALPHA_BUDGET_LIMITS),
                request_hash_override=request_hash,
            )
            existing_attempt = session.execute(
                select(AgentAttemptModel)
                .where(
                    AgentAttemptModel.task_id == task.id,
                    AgentAttemptModel.tenant_id == tenant_id,
                )
                .order_by(AgentAttemptModel.attempt_number.desc())
            ).scalar_one_or_none()
            if existing_attempt is not None:
                # Exact replay of a previous invoke with the same idempotency
                # key: the durable task already exists.  An in-flight attempt
                # must never be dispatched a second time; a terminal attempt is
                # re-exposed without creating any new rows or calling any
                # provider.
                if existing_attempt.state in _ALPHA_ACTIVE_ATTEMPT_STATES:
                    session.rollback()
                    raise AlphaAdapterError("agent_alpha_replay_in_flight")
                existing_effect = session.execute(
                    select(AgentTaskEffectModel).where(
                        AgentTaskEffectModel.task_id == task.id,
                        AgentTaskEffectModel.request_hash == request_hash,
                        AgentTaskEffectModel.tenant_id == tenant_id,
                    )
                ).scalar_one_or_none()
                session.rollback()
                return AlphaInvocationIdentity(
                    invocation_id=task.id,
                    task_id=task.id,
                    attempt_id=existing_attempt.id,
                    effect_id=existing_effect.id if existing_effect is not None else "",
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    replayed_state=task.state,
                )
            workspace = session.execute(
                select(Workspace).where(
                    Workspace.id == workspace_id,
                    Workspace.tenant_id == tenant_id,
                )
            ).scalar_one_or_none()
            node = (
                _local_runtime_node(
                    session,
                    tenant_id=tenant_id,
                    workspace=workspace,
                    actor_user_id=actor_user_id,
                )
                if workspace is not None
                else None
            )
            if workspace is None or node is None:
                raise AlphaAdapterUnavailable("agent_alpha_run_unavailable")
            workspace_run = create_workspace_run(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                kind="interactive",
                expected_workspace_generation=workspace.generation,
                request_digest=request_hash,
                idempotency_key=f"alpha-runtime:{idempotency_key}",
            )
            run_lease = claim_run_lease(
                session,
                tenant_id=tenant_id,
                run_id=workspace_run.id,
                node_id=node.id,
                lease_seconds=_ALPHA_WORKSPACE_RUN_LEASE_SECONDS,
            )
            runtime_instance_id = str(uuid.uuid4())
            workload_identity_digest = canonical_digest(
                {
                    "kind": "tool_free_agent_alpha_model_request",
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "workspace_run_id": workspace_run.id,
                    "run_lease_id": run_lease.id,
                    "run_fencing_token": run_lease.fencing_token,
                    "node_id": node.id,
                    "node_fencing_token": run_lease.node_fencing_token,
                    "runtime_instance_id": runtime_instance_id,
                    "tools_enabled": False,
                }
            )
            bind_run_runtime_identity(
                session,
                tenant_id=tenant_id,
                run_id=workspace_run.id,
                lease_id=run_lease.id,
                node_id=node.id,
                generation=workspace_run.generation,
                fencing_token=run_lease.fencing_token,
                runtime_instance_id=runtime_instance_id,
                workload_identity_digest=workload_identity_digest,
            )
            submit_run_state(
                session,
                tenant_id=tenant_id,
                run_id=workspace_run.id,
                lease_id=run_lease.id,
                node_id=node.id,
                generation=workspace_run.generation,
                fencing_token=run_lease.fencing_token,
                observed_state="starting",
            )
            submit_run_state(
                session,
                tenant_id=tenant_id,
                run_id=workspace_run.id,
                lease_id=run_lease.id,
                node_id=node.id,
                generation=workspace_run.generation,
                fencing_token=run_lease.fencing_token,
                observed_state="running",
            )
            run = svc.create_run(
                tenant_id=tenant_id,
                task_id=task.id,
                workspace_run_id=run_lease.run_id,
                run_lease_id=run_lease.id,
                runtime_instance_id=runtime_instance_id,
                workload_identity_digest=workload_identity_digest,
            )
            step = svc.create_step(
                tenant_id=tenant_id,
                task_id=task.id,
                agent_run_id=run.id,
                plan_id=profile.agent_definition_id,
                plan_version=1,
                plan_digest=profile.agent_version_digest,
            )
            attempt = svc.create_attempt(
                tenant_id=tenant_id,
                task_id=task.id,
                step_id=step.id,
                agent_run_id=run.id,
                deadline=deadline,
            )
            svc.claim_attempt(
                tenant_id=tenant_id,
                attempt_id=attempt.id,
                run_lease_id=run_lease.id,
                ttl_seconds=_ALPHA_LEASE_TTL_SECONDS,
            )
            svc.reserve_budget(
                tenant_id=tenant_id, task_id=task.id, dimension="input_tokens", amount=4096
            )
            svc.reserve_budget(
                tenant_id=tenant_id, task_id=task.id, dimension="output_tokens", amount=2048
            )
            svc.reserve_budget(
                tenant_id=tenant_id, task_id=task.id, dimension="model_calls", amount=1
            )
            operation = create_operation(
                session,
                tenant_id=tenant_id,
                kind=_ALPHA_MODEL_OPERATION,
                risk_level="R1",
                actor_type="user",
                actor_id=actor_user_id,
                workspace_id=workspace_id,
                resource_id=task.id,
                resource_version=task.task_generation,
                request_hash=request_hash,
                deadline_at=deadline,
            )
            effect = svc.reserve_effect(
                tenant_id=tenant_id,
                attempt_id=attempt.id,
                operation_id=operation.id,
                request_hash=request_hash,
            )
            # The effect state machine requires reserved -> dispatching before
            # the provider boundary is crossed; the attempt guard permits
            # leased -> dispatching (but not leased -> committed), the run
            # guard permits leased -> running (but not leased -> succeeded),
            # and the task guard validates OLD -> NEW per UPDATE, so the task
            # must walk created -> scheduled -> running across separate
            # flushes. Terminal transitions happen in transaction B.
            effect.state = "dispatching"
            attempt.state = "dispatching"
            run.state = "running"
            session.flush()
            task.state = "scheduled"
            session.flush()
            task.state = "running"
            session.flush()
            session.commit()
            return AlphaInvocationIdentity(
                invocation_id=task.id,
                task_id=task.id,
                attempt_id=attempt.id,
                effect_id=effect.id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
            )
        except (TaskLedgerConflict, TaskLedgerStateError) as exc:
            session.rollback()
            raise AlphaAdapterError(str(exc)) from exc
        except TaskLedgerError as exc:
            session.rollback()
            raise AlphaAdapterUnavailable(str(exc)) from exc
        except AlphaAdapterError:
            session.rollback()
            raise
        finally:
            session.close()

    def _terminalize(
        self,
        *,
        identity: AlphaInvocationIdentity,
        outcome: Literal["committed", "failed", "unknown", "cancelled"],
        result_digest: str | None,
        usage: ModelUsage | None,
        error_code: str,
        reconcile: bool,
    ) -> None:
        """Transaction B: revalidate and terminalize Effect/Attempt/Run/Task."""
        session = _tenant_session(self._factory, identity.tenant_id)
        try:
            svc = TaskLedgerPersistenceService(session)
            attempt = session.execute(
                select(AgentAttemptModel)
                .where(
                    AgentAttemptModel.id == identity.attempt_id,
                    AgentAttemptModel.tenant_id == identity.tenant_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if attempt is None:
                raise AlphaAdapterUnavailable("agent_alpha_attempt_missing")
            task = session.execute(
                select(AgentTaskModel)
                .where(
                    AgentTaskModel.id == identity.task_id,
                    AgentTaskModel.tenant_id == identity.tenant_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if task is None:
                raise AlphaAdapterUnavailable("agent_alpha_task_missing")
            if task.workspace_id != identity.workspace_id:
                raise AlphaAdapterUnavailable("agent_alpha_task_scope_mismatch")
            if outcome == "committed" and (result_digest is None or usage is None):
                raise AlphaAdapterUnavailable("agent_alpha_result_incomplete")
            if outcome != "committed" and result_digest is not None:
                raise AlphaAdapterUnavailable("agent_alpha_result_forbidden")
            if usage is not None and outcome == "committed":
                reserved_input = 4096
                reserved_output = 2048
                used_input = min(int(usage.input_tokens or 0), reserved_input)
                used_output = min(int(usage.output_tokens or 0), reserved_output)
                svc.commit_budget(
                    tenant_id=identity.tenant_id,
                    task_id=task.id,
                    dimension="input_tokens",
                    amount=reserved_input,
                )
                svc.release_committed_budget(
                    tenant_id=identity.tenant_id,
                    task_id=task.id,
                    dimension="input_tokens",
                    amount=reserved_input - used_input,
                )
                svc.commit_budget(
                    tenant_id=identity.tenant_id,
                    task_id=task.id,
                    dimension="output_tokens",
                    amount=reserved_output,
                )
                svc.release_committed_budget(
                    tenant_id=identity.tenant_id,
                    task_id=task.id,
                    dimension="output_tokens",
                    amount=reserved_output - used_output,
                )
                svc.commit_budget(
                    tenant_id=identity.tenant_id,
                    task_id=task.id,
                    dimension="model_calls",
                    amount=1,
                )
            effect_outcome: Literal["committed", "failed", "unknown"] = (
                "committed"
                if outcome == "committed"
                else "unknown"
                if outcome == "unknown"
                else "failed"
            )
            svc.finish_effect(
                tenant_id=identity.tenant_id,
                effect_id=identity.effect_id,
                outcome=effect_outcome,
                result_digest=result_digest,
            )
            if outcome in {"committed", "failed", "unknown", "cancelled"}:
                svc.finish_attempt(
                    tenant_id=identity.tenant_id,
                    attempt_id=identity.attempt_id,
                    task_lease_id=attempt.task_lease_id or "",
                    task_fencing_token=attempt.task_fencing_token or 0,
                    outcome=outcome,
                )
            if outcome == "unknown" and reconcile:
                svc.open_reconciliation(
                    tenant_id=identity.tenant_id,
                    attempt_id=identity.attempt_id,
                    reason_code=error_code,
                    effect_id=identity.effect_id,
                )
            task_state = {
                "committed": "succeeded",
                "failed": "failed",
                "unknown": "blocked_unknown",
                "cancelled": "cancelled",
            }[outcome]
            task.state = task_state
            run = session.execute(
                select(AgentRunModel).where(
                    AgentRunModel.id == attempt.agent_run_id,
                    AgentRunModel.tenant_id == identity.tenant_id,
                )
            ).scalar_one_or_none()
            _terminalize_workspace_run(
                session,
                tenant_id=identity.tenant_id,
                run=run,
                outcome=outcome,
                result_digest=result_digest,
                error_code=error_code,
            )
            session.commit()
        except TaskLedgerError as exc:
            session.rollback()
            raise AlphaAdapterUnavailable(str(exc)) from exc
        except AlphaAdapterError:
            session.rollback()
            raise
        finally:
            session.close()

    def complete(
        self,
        *,
        identity: AlphaInvocationIdentity,
        result_digest: str,
        usage: ModelUsage,
    ) -> None:
        self._terminalize(
            identity=identity,
            outcome="committed",
            result_digest=result_digest,
            usage=usage,
            error_code="",
            reconcile=False,
        )

    def fail(
        self,
        *,
        identity: AlphaInvocationIdentity,
        outcome: Literal["failed", "unknown", "cancelled"],
        error_code: str,
    ) -> None:
        self._terminalize(
            identity=identity,
            outcome=outcome,
            result_digest=None,
            usage=None,
            error_code=error_code,
            reconcile=outcome == "unknown",
        )


__all__ = [
    "AlphaAdapterError",
    "AlphaAdapterUnavailable",
    "LedgerInvocationAdapter",
    "RagKnowledgeRetriever",
    "RegistryProfileResolver",
    "canonical_digest",
]
