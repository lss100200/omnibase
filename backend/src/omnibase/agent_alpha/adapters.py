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
import re
import uuid
from collections.abc import Callable
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
from omnibase.control_plane.models import IdempotencyRecord, ResourceRecord
from omnibase.control_plane.service import create_operation
from omnibase.core.logging import get_logger
from omnibase.db.models import Tenant
from omnibase.db.tenant import Embedding, User
from omnibase.model_gateway import ModelUsage
from omnibase.onboarding import ensure_local_model_runtime_anchor
from omnibase.task_ledger.models import (
    AgentAttemptModel,
    AgentReconciliationCaseModel,
    AgentRunModel,
    AgentTaskEffectModel,
    AgentTaskLeaseModel,
    AgentTaskModel,
)
from omnibase.task_ledger.service import (
    TaskAdmissionContext,
    TaskLedgerConflict,
    TaskLedgerError,
    TaskLedgerPersistenceService,
    TaskLedgerStateError,
)
from omnibase.workspace_data.models import WorkspaceDerivedIndex
from omnibase.workspace_data.tenant_models import WorkspaceDerivedChunkV2
from omnibase.workspaces.models import (
    ResourceScopeBinding,
    Workspace,
    WorkspaceMembership,
    WorkspaceNode,
)
from omnibase.workspaces.service import (
    LeaseRejected,
    WorkspaceError,
    bind_run_runtime_identity,
    claim_run_lease,
    close_historical_run_holder,
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
_ALPHA_MAX_RAG_QUERY_TERMS = 32
_RAG_QUERY_TERM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{1,63}")
_ALPHA_INVOCATION_DEADLINE = timedelta(seconds=110)
_ALPHA_LEASE_TTL_SECONDS = 90
_ALPHA_WORKSPACE_RUN_LEASE_SECONDS = 120
_ALPHA_MODEL_OPERATION = "agent.model.invoke"
_ALPHA_RESTART_RECOVERY_REASON = "agent_alpha_restart_lease_expired"
_ALPHA_RETRYABLE_TASK_STATES = frozenset({"blocked_unknown", "failed", "cancelled"})
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


def _bounded_websearch_query(query: str) -> str:
    unique: list[str] = []
    seen: set[str] = set()
    for match in _RAG_QUERY_TERM.finditer(query):
        term = match.group(0).casefold()
        if term not in seen:
            seen.add(term)
            unique.append(term)
    if not unique:
        return '"omnibase_no_match_sentinel"'
    prioritized = sorted(
        enumerate(unique),
        key=lambda item: (
            (
                0
                if any(character in "_-" for character in item[1])
                else 1
                if any(character.isdigit() for character in item[1])
                else 2
            ),
            item[0],
        ),
    )
    selected = [term for _, term in prioritized[:_ALPHA_MAX_RAG_QUERY_TERMS]]
    return " OR ".join(f'"{term}"' for term in selected)


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
    node_fencing_token = run.node_fencing_token
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
    try:
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
    except LeaseRejected:
        # The Workspace Run Lease has lapsed / been revoked / gone stale
        # while the stream was disconnected.  submit_run_state is NOT
        # relaxed.  A committed outcome can never fall back to the
        # historical holder path (an expired authorization can never be
        # closed as succeeded); only terminal FAILURE states may close the
        # exact historical holder, release the interactive slot and leave
        # reconciliation in the same transaction.
        if outcome == "committed":
            raise
        close_historical_run_holder(
            session,
            tenant_id=tenant_id,
            workspace_id=run.workspace_id,
            workspace_run_id=workspace_run_id,
            run_lease_id=lease_id,
            node_id=node_id,
            generation=run.workspace_generation,
            run_fencing_token=fencing_token,
            node_fencing_token=node_fencing_token or 0,
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
        raw_instructions = manifest.get("instructions")
        instructions_text = raw_instructions if isinstance(raw_instructions, str) else ""
        if instructions_text.strip():
            actual_instructions_digest = hashlib.sha256(
                instructions_text.encode("utf-8")
            ).hexdigest()
            if actual_instructions_digest != version.instructions_digest:
                raise AlphaAdapterUnavailable("agent_alpha_instructions_digest_mismatch")
        else:
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
            workspace_generation=binding.workspace_generation,
        )


class RagKnowledgeRetriever:
    """Read-only, capped, logical-identity RAG retrieval for the Alpha.

    Retrieval reads ready P34.6 derived-index generations plus canonical v1
    document chunks whose Resource Registry binding proves that the document is
    active and private to the requested Workspace.  Unbound tenant-wide RAG is
    deliberately excluded.  V1 is authoritative and written by every normal
    ingestion; the optional v2 shadow lane cannot be required for visibility.
    When retrieval itself fails the adapter logs a content-free diagnostic and
    returns no chunks: the Alpha service then explicitly states that no context
    was retrieved.  No physical locator ever leaves this adapter.
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
            query_expression = func.websearch_to_tsquery(
                "pg_catalog.simple", _bounded_websearch_query(query)
            )
            canonical_rank = func.ts_rank(Embedding.tsv, query_expression)
            canonical_rows = session.execute(
                select(
                    Embedding.id,
                    Embedding.document_id,
                    Embedding.content,
                    Embedding.metadata_,
                    canonical_rank.label("rank"),
                    Embedding.chunk_index,
                )
                .join(
                    ResourceScopeBinding,
                    ResourceScopeBinding.resource_id == Embedding.document_id,
                )
                .join(ResourceRecord, ResourceRecord.id == ResourceScopeBinding.resource_id)
                .where(
                    ResourceScopeBinding.tenant_id == tenant_id,
                    ResourceScopeBinding.workspace_id == workspace_id,
                    ResourceScopeBinding.scope_class == "workspace_private",
                    ResourceRecord.tenant_id == tenant_id,
                    ResourceRecord.kind == "document",
                    ResourceRecord.policy_class == "workspace_private",
                    ResourceRecord.state == "active",
                    Embedding.tsv.op("@@")(query_expression),
                )
                .order_by(desc("rank"), Embedding.chunk_index)
                .limit(top_k)
            ).all()
            derived_rank = func.ts_rank(WorkspaceDerivedChunkV2.tsv, query_expression)
            derived_rows = session.execute(
                select(
                    WorkspaceDerivedChunkV2.id,
                    WorkspaceDerivedIndex.source_resource_id,
                    WorkspaceDerivedChunkV2.content,
                    WorkspaceDerivedChunkV2.metadata_,
                    derived_rank.label("rank"),
                    WorkspaceDerivedChunkV2.chunk_index,
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
            canonical_document_ids = {str(row[1]) for row in canonical_rows}
            nonduplicated_derived_rows = tuple(
                row for row in derived_rows if str(row[1]) not in canonical_document_ids
            )
            rows = sorted(
                (*canonical_rows, *nonduplicated_derived_rows),
                key=lambda row: (-float(row[4] or 0.0), int(row[5])),
            )[:top_k]
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
        for chunk_id, source_resource_id, raw_content, metadata, score, _ in rows:
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

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        invocation_guard: Callable[
            [
                Session,
                str,
                str,
                str,
                AlphaAgentProfile,
                TaskAdmissionContext,
                str | None,
            ],
            None,
        ]
        | None = None,
    ) -> None:
        self._factory = session_factory
        self._invocation_guard = invocation_guard

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

    @staticmethod
    def _validate_retry_target(
        session: Session,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_user_id: str,
        profile: AlphaAgentProfile,
        retry_of: str | None,
    ) -> None:
        """Bind an explicit personal retry to one exact terminal invocation.

        A retry is a new invocation, never another Attempt on the old Task.
        The old Task and its reconciliation evidence remain immutable while
        the new idempotency key receives completely new ledger/runtime rows.
        """
        if retry_of is None:
            return
        target = session.execute(
            select(AgentTaskModel)
            .where(
                AgentTaskModel.id == retry_of,
                AgentTaskModel.tenant_id == tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if target is None:
            raise AlphaAdapterError("agent_alpha_retry_target_missing")
        if target.actor_user_id != actor_user_id or target.workspace_id != workspace_id:
            raise AlphaAdapterError("agent_alpha_retry_target_scope_mismatch")
        if (
            target.agent_definition_id != profile.agent_definition_id
            or target.agent_version_id != profile.agent_version_id
            or target.agent_version_digest != profile.agent_version_digest
            or target.workspace_agent_binding_id != profile.workspace_agent_binding_id
            or target.resource_scope_digest != profile.resource_scope_digest
            or target.budget_policy_digest != profile.budget_policy_digest
        ):
            raise AlphaAdapterError("agent_alpha_retry_target_agent_mismatch")
        if target.state not in _ALPHA_RETRYABLE_TASK_STATES:
            raise AlphaAdapterError("agent_alpha_retry_target_not_retryable")
        attempt = session.execute(
            select(AgentAttemptModel)
            .where(
                AgentAttemptModel.task_id == target.id,
                AgentAttemptModel.tenant_id == tenant_id,
            )
            .order_by(AgentAttemptModel.attempt_number.desc())
            .with_for_update()
        ).scalar_one_or_none()
        expected_attempt_state = {
            "blocked_unknown": "unknown",
            "failed": "failed",
            "cancelled": "cancelled",
        }[target.state]
        if attempt is None or attempt.state != expected_attempt_state:
            raise AlphaAdapterError("agent_alpha_retry_target_inconsistent")
        if target.state == "blocked_unknown":
            open_case = session.execute(
                select(AgentReconciliationCaseModel).where(
                    AgentReconciliationCaseModel.tenant_id == tenant_id,
                    AgentReconciliationCaseModel.task_id == target.id,
                    AgentReconciliationCaseModel.attempt_id == attempt.id,
                    AgentReconciliationCaseModel.state == "open",
                )
            ).scalar_one_or_none()
            if open_case is None:
                raise AlphaAdapterError("agent_alpha_retry_reconciliation_missing")

    @staticmethod
    def _recover_expired_attempt(
        session: Session,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_user_id: str,
        attempt_id: str,
    ) -> AlphaInvocationIdentity | None:
        """Atomically close one expired active invocation without replaying it."""
        attempt = session.execute(
            select(AgentAttemptModel)
            .where(
                AgentAttemptModel.id == attempt_id,
                AgentAttemptModel.tenant_id == tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if attempt is None or attempt.state not in _ALPHA_ACTIVE_ATTEMPT_STATES:
            return None
        if attempt.task_lease_id is None or attempt.task_fencing_token is None:
            raise AlphaAdapterUnavailable("agent_alpha_recovery_lease_binding_missing")
        lease = session.execute(
            select(AgentTaskLeaseModel)
            .where(
                AgentTaskLeaseModel.id == attempt.task_lease_id,
                AgentTaskLeaseModel.tenant_id == tenant_id,
                AgentTaskLeaseModel.attempt_id == attempt.id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if lease is None or lease.state != "active":
            raise AlphaAdapterUnavailable("agent_alpha_recovery_lease_stale")
        now = session.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            raise AlphaAdapterUnavailable("agent_alpha_recovery_clock_unavailable")
        if now < lease.expires_at:
            return None
        task = session.execute(
            select(AgentTaskModel)
            .where(
                AgentTaskModel.id == attempt.task_id,
                AgentTaskModel.tenant_id == tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if (
            task is None
            or task.workspace_id != workspace_id
            or task.actor_user_id != actor_user_id
            or task.state not in {"scheduled", "running"}
        ):
            raise AlphaAdapterUnavailable("agent_alpha_recovery_task_scope_mismatch")
        effect = session.execute(
            select(AgentTaskEffectModel)
            .where(
                AgentTaskEffectModel.task_id == task.id,
                AgentTaskEffectModel.attempt_id == attempt.id,
                AgentTaskEffectModel.tenant_id == tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if effect is None or effect.state not in {"reserved", "dispatching"}:
            raise AlphaAdapterUnavailable("agent_alpha_recovery_effect_missing")
        run = session.execute(
            select(AgentRunModel)
            .where(
                AgentRunModel.id == attempt.agent_run_id,
                AgentRunModel.tenant_id == tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        svc = TaskLedgerPersistenceService(session)
        settled = svc.finish_attempt(
            tenant_id=tenant_id,
            attempt_id=attempt.id,
            task_lease_id=lease.id,
            task_fencing_token=lease.task_fencing_token,
            outcome="unknown",
        )
        if settled != "unknown":
            raise AlphaAdapterUnavailable("agent_alpha_recovery_outcome_invalid")
        svc.finish_effect(
            tenant_id=tenant_id,
            effect_id=effect.id,
            outcome="unknown",
        )
        svc.open_reconciliation(
            tenant_id=tenant_id,
            attempt_id=attempt.id,
            reason_code=_ALPHA_RESTART_RECOVERY_REASON,
            effect_id=effect.id,
        )
        task.state = "blocked_unknown"
        try:
            _terminalize_workspace_run(
                session,
                tenant_id=tenant_id,
                run=run,
                outcome="unknown",
                result_digest=None,
                error_code=_ALPHA_RESTART_RECOVERY_REASON,
            )
        except (LeaseRejected, WorkspaceError) as exc:
            raise AlphaAdapterUnavailable("agent_alpha_restart_recovery_rejected") from exc
        session.flush()
        return AlphaInvocationIdentity(
            invocation_id=task.id,
            task_id=task.id,
            attempt_id=attempt.id,
            effect_id=effect.id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            replayed_state="blocked_unknown",
        )

    @classmethod
    def _recover_expired_workspace_attempts(
        cls,
        session: Session,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_user_id: str,
        exclude_task_id: str,
    ) -> None:
        attempt_ids = session.execute(
            select(AgentAttemptModel.id)
            .join(
                AgentTaskModel,
                (AgentTaskModel.id == AgentAttemptModel.task_id)
                & (AgentTaskModel.tenant_id == AgentAttemptModel.tenant_id),
            )
            .join(
                AgentTaskLeaseModel,
                (AgentTaskLeaseModel.id == AgentAttemptModel.task_lease_id)
                & (AgentTaskLeaseModel.tenant_id == AgentAttemptModel.tenant_id),
            )
            .where(
                AgentAttemptModel.tenant_id == tenant_id,
                AgentAttemptModel.state.in_(_ALPHA_ACTIVE_ATTEMPT_STATES),
                AgentTaskModel.workspace_id == workspace_id,
                AgentTaskModel.actor_user_id == actor_user_id,
                AgentTaskModel.id != exclude_task_id,
                AgentTaskLeaseModel.state == "active",
                AgentTaskLeaseModel.expires_at <= func.clock_timestamp(),
            )
            .order_by(AgentAttemptModel.created_at)
        ).scalars()
        for attempt_id in tuple(attempt_ids):
            cls._recover_expired_attempt(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                attempt_id=attempt_id,
            )

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
            admission_context: TaskAdmissionContext | None = None

            def admission_guard(
                *,
                session: Session,
                context: TaskAdmissionContext,
            ) -> None:
                nonlocal admission_context
                if self._invocation_guard is not None:
                    self._invocation_guard(
                        session,
                        tenant_id,
                        workspace_id,
                        actor_user_id,
                        profile,
                        context,
                        None,
                    )
                admission_context = context

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
                admission_guard=(admission_guard if self._invocation_guard is not None else None),
            )
            if replay_task_id is None:
                self._recover_expired_workspace_attempts(
                    session,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    exclude_task_id=task.id,
                )
                self._validate_retry_target(
                    session,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    profile=profile,
                    retry_of=retry_of,
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
                    recovered = self._recover_expired_attempt(
                        session,
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        actor_user_id=actor_user_id,
                        attempt_id=existing_attempt.id,
                    )
                    if recovered is None:
                        session.rollback()
                        raise AlphaAdapterError("agent_alpha_replay_in_flight")
                    session.commit()
                    return recovered
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
            if self._invocation_guard is not None and admission_context is not None:
                self._invocation_guard(
                    session,
                    tenant_id,
                    workspace_id,
                    actor_user_id,
                    profile,
                    admission_context,
                    workspace_run.id,
                )
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
            # Settle the attempt FIRST: the lease window is the live
            # authorization, and an expired lease must never commit success
            # (settle_terminal_outcome derails committed -> unknown).  Every
            # follow-on row (budget, effect, reconciliation, task, run) is
            # driven by the SAME settled outcome so the terminal transition
            # is atomic and no success is ever recorded from an expired lease.
            settled = svc.finish_attempt(
                tenant_id=identity.tenant_id,
                attempt_id=identity.attempt_id,
                task_lease_id=attempt.task_lease_id or "",
                task_fencing_token=attempt.task_fencing_token or 0,
                outcome=outcome,
            )
            derailed = outcome == "committed" and settled == "unknown"
            effective_error_code = "agent_alpha_task_lease_expired" if derailed else error_code
            if settled == "committed":
                # settled == "committed" only ever derives from
                # outcome == "committed" (an expired lease derails committed
                # to unknown, never the reverse), and that path already
                # required result_digest + usage above.
                assert usage is not None
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
                if settled == "committed"
                else "unknown"
                if settled == "unknown"
                else "failed"
            )
            svc.finish_effect(
                tenant_id=identity.tenant_id,
                effect_id=identity.effect_id,
                outcome=effect_outcome,
                result_digest=result_digest if settled == "committed" else None,
            )
            if settled == "unknown" and (reconcile or derailed):
                svc.open_reconciliation(
                    tenant_id=identity.tenant_id,
                    attempt_id=identity.attempt_id,
                    reason_code=effective_error_code,
                    effect_id=identity.effect_id,
                )
            task_state = {
                "committed": "succeeded",
                "failed": "failed",
                "unknown": "blocked_unknown",
                "cancelled": "cancelled",
            }[settled]
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
                outcome=settled,
                result_digest=result_digest if settled == "committed" else None,
                error_code=effective_error_code,
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
