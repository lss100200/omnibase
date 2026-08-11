"""Governed P5.5B Memory persistence transactions.

Every function receives a caller-owned tenant session and never commits. The
caller must roll back the entire transaction on any exception so Operation,
Approval, Audit, Memory payload and tombstone state cannot partially diverge.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from omnibase.agent_memory.models import (
    ContextCapsuleModel,
    MemoryCandidateModel,
    MemoryEffectModel,
    MemoryEmbeddingV1Model,
    MemoryEmbeddingV2Model,
    MemoryModel,
    MemoryReviewEvidenceModel,
    MemoryTombstoneModel,
    MemoryVersionModel,
)
from omnibase.agent_registry.models import AgentVersionModel
from omnibase.control_plane.models import OperationRecord
from omnibase.control_plane.service import (
    append_audit_event,
    authorize_operation,
    get_operation,
    transition_operation,
)
from omnibase.db.models import Tenant
from omnibase.db.tenant import User
from omnibase.task_ledger.models import AgentTaskModel
from omnibase.workspaces.models import Workspace, WorkspaceMembership

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REASON = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_SCOPES = frozenset({"user_private", "workspace_private", "agent_private", "controlled_shared"})
_SENSITIVITY = frozenset({"standard", "personal", "sensitive", "restricted"})


class MemoryPersistenceError(RuntimeError):
    """Base class for fail-closed P5.5B persistence failures."""


class MemoryNotFound(MemoryPersistenceError):
    pass


class MemoryConflict(MemoryPersistenceError):
    pass


@dataclass(frozen=True, slots=True)
class CandidateDraft:
    tenant_id: str
    owner_user_id: str
    workspace_id: str
    agent_version_id: str
    task_id: str
    invocation_id: str
    source_capsule_id: str
    memory_policy_id: str
    requested_scope: str
    sensitivity: str
    content_ciphertext: bytes
    content_nonce: bytes
    content_key_version: int
    content_sha256: str
    source_resource_id: str
    source_resource_version: int
    evidence_reference_ids: tuple[str, ...]
    confidence_millis: int
    retention_days: int
    requires_user_confirmation: bool
    operation_id: str
    operation_expected_version: int
    request_sha256: str
    request_id: str


@dataclass(frozen=True, slots=True)
class CandidateConfirmation:
    tenant_id: str
    candidate_id: str
    owner_user_id: str
    operation_id: str
    operation_expected_version: int
    approval_id: str
    approval_expected_version: int
    grant_id: str
    token_count: int
    request_id: str


@dataclass(frozen=True, slots=True)
class OwnerMemoryOperation:
    tenant_id: str
    memory_id: str
    owner_user_id: str
    operation_id: str
    operation_expected_version: int
    request_sha256: str
    request_id: str


@dataclass(frozen=True, slots=True)
class MemoryExport:
    payload: bytes
    sha256: str
    effect_id: str


def _uuid() -> str:
    return str(uuid4())


def _validate_uuid(value: str, field: str) -> None:
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc
    if str(parsed) != value:
        raise ValueError(f"{field} must use canonical UUID spelling")


def _validate_sha256(value: str, field: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def _canonical_bytes(value: dict[str, object]) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _digest(value: dict[str, object]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _database_now(session: Session) -> datetime:
    value = session.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise MemoryConflict("database clock is unavailable")
    return value


def _require_live_owner(
    session: Session, *, tenant_id: str, owner_user_id: str, workspace_id: str
) -> None:
    tenant = session.scalar(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.is_active.is_(True)).with_for_update()
    )
    if tenant is None:
        raise MemoryConflict("tenant is not active")
    owner = session.scalar(
        select(User)
        .where(
            User.id == owner_user_id,
            User.is_active.is_(True),
            User.is_tenant_admin.is_(True),
        )
        .with_for_update()
    )
    if owner is None:
        raise MemoryConflict("Memory Owner must be an active tenant administrator")
    workspace = session.scalar(
        select(Workspace)
        .where(
            Workspace.id == workspace_id,
            Workspace.tenant_id == tenant_id,
            Workspace.owner_user_id == owner_user_id,
        )
        .with_for_update()
    )
    membership = session.scalar(
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.tenant_id == tenant_id,
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == owner_user_id,
            WorkspaceMembership.role == "owner",
            WorkspaceMembership.state == "active",
        )
        .with_for_update()
    )
    if workspace is None or membership is None:
        raise MemoryConflict("Memory Owner lost the live Workspace owner binding")


def _require_task_context(
    session: Session,
    *,
    tenant_id: str,
    owner_user_id: str,
    workspace_id: str,
    agent_version_id: str,
    task_id: str,
) -> AgentTaskModel:
    task = session.scalar(
        select(AgentTaskModel)
        .where(
            AgentTaskModel.id == task_id,
            AgentTaskModel.tenant_id == tenant_id,
            AgentTaskModel.actor_user_id == owner_user_id,
            AgentTaskModel.workspace_id == workspace_id,
            AgentTaskModel.agent_version_id == agent_version_id,
            AgentTaskModel.state == "succeeded",
        )
        .with_for_update()
    )
    version = session.scalar(
        select(AgentVersionModel).where(
            AgentVersionModel.id == agent_version_id,
            AgentVersionModel.tenant_id == tenant_id,
            AgentVersionModel.version_state == "sealed",
        )
    )
    if task is None or version is None:
        raise MemoryConflict("Candidate requires a succeeded Task and sealed AgentVersion")
    return task


def _require_source_capsule(session: Session, draft: CandidateDraft) -> ContextCapsuleModel:
    capsule = session.scalar(
        select(ContextCapsuleModel).where(
            ContextCapsuleModel.id == draft.source_capsule_id,
            ContextCapsuleModel.tenant_id == draft.tenant_id,
            ContextCapsuleModel.owner_user_id == draft.owner_user_id,
            ContextCapsuleModel.workspace_id == draft.workspace_id,
            ContextCapsuleModel.agent_version_id == draft.agent_version_id,
            ContextCapsuleModel.task_id == draft.task_id,
            ContextCapsuleModel.invocation_id == draft.invocation_id,
            ContextCapsuleModel.memory_policy_id == draft.memory_policy_id,
        )
    )
    if capsule is None:
        raise MemoryConflict("Candidate source Capsule binding is not exact")
    return capsule


def _require_operation(
    session: Session,
    *,
    tenant_id: str,
    operation_id: str,
    expected_version: int,
    state: str,
    kind: str,
    actor_type: str,
    actor_id: str,
    workspace_id: str,
    request_sha256: str,
    resource_id: str | None,
    resource_version: int | None,
) -> OperationRecord:
    operation = get_operation(session, tenant_id=tenant_id, operation_id=operation_id)
    if any(
        (
            operation.version != expected_version,
            operation.state != state,
            operation.kind != kind,
            operation.actor_type != actor_type,
            operation.actor_id != actor_id,
            operation.workspace_id != workspace_id,
            operation.run_id is not None,
            operation.request_hash != request_sha256,
            operation.resource_id != resource_id,
            operation.resource_version != resource_version,
        )
    ):
        raise MemoryConflict("Operation binding changed")
    return operation


def _complete_operation(
    session: Session,
    *,
    tenant_id: str,
    operation_id: str,
    expected_version: int,
    result_ref: dict[str, object],
) -> OperationRecord:
    running = transition_operation(
        session,
        tenant_id=tenant_id,
        operation_id=operation_id,
        expected_version=expected_version,
        target_state="running",
        progress=50,
    )
    return transition_operation(
        session,
        tenant_id=tenant_id,
        operation_id=operation_id,
        expected_version=running.version,
        target_state="succeeded",
        progress=100,
        result_ref=result_ref,
    )


def _validate_draft(draft: CandidateDraft) -> None:
    for field in (
        "tenant_id",
        "owner_user_id",
        "workspace_id",
        "agent_version_id",
        "task_id",
        "invocation_id",
        "source_capsule_id",
        "memory_policy_id",
        "source_resource_id",
        "operation_id",
    ):
        _validate_uuid(str(getattr(draft, field)), field)
    _validate_sha256(draft.content_sha256, "content_sha256")
    _validate_sha256(draft.request_sha256, "request_sha256")
    if draft.requested_scope not in _SCOPES:
        raise ValueError("requested_scope is not supported")
    if draft.sensitivity not in _SENSITIVITY:
        raise ValueError("sensitivity is not supported")
    if not draft.content_ciphertext or not draft.content_nonce:
        raise ValueError("encrypted Candidate payload and nonce are required")
    if draft.content_key_version < 1 or draft.source_resource_version < 1:
        raise ValueError("content key and source resource versions must be positive")
    if not 0 <= draft.confidence_millis <= 1000:
        raise ValueError("confidence_millis must be between 0 and 1000")
    if not 1 <= draft.retention_days <= 3650:
        raise ValueError("retention_days must be between 1 and 3650")
    evidence = list(draft.evidence_reference_ids)
    if not evidence or len(evidence) != len(set(evidence)):
        raise ValueError("evidence references must be non-empty and unique")
    for reference in evidence:
        _validate_uuid(reference, "evidence_reference_id")
    if (
        draft.requested_scope == "controlled_shared"
        or draft.sensitivity in {"sensitive", "restricted"}
    ) and not draft.requires_user_confirmation:
        raise ValueError("sensitive/shared Candidates require Owner confirmation")


def create_candidate(session: Session, draft: CandidateDraft) -> MemoryCandidateModel:
    """Persist an Agent-created Candidate and its committed creation effect."""
    _validate_draft(draft)
    _require_live_owner(
        session,
        tenant_id=draft.tenant_id,
        owner_user_id=draft.owner_user_id,
        workspace_id=draft.workspace_id,
    )
    task = _require_task_context(
        session,
        tenant_id=draft.tenant_id,
        owner_user_id=draft.owner_user_id,
        workspace_id=draft.workspace_id,
        agent_version_id=draft.agent_version_id,
        task_id=draft.task_id,
    )
    _require_source_capsule(session, draft)
    _require_operation(
        session,
        tenant_id=draft.tenant_id,
        operation_id=draft.operation_id,
        expected_version=draft.operation_expected_version,
        state="queued",
        kind="memory.candidate.create",
        actor_type="agent",
        actor_id=task.agent_definition_id,
        workspace_id=draft.workspace_id,
        request_sha256=draft.request_sha256,
        resource_id=draft.source_resource_id,
        resource_version=draft.source_resource_version,
    )
    candidate = MemoryCandidateModel(
        tenant_id=draft.tenant_id,
        owner_user_id=draft.owner_user_id,
        workspace_id=draft.workspace_id,
        agent_version_id=draft.agent_version_id,
        task_id=draft.task_id,
        invocation_id=draft.invocation_id,
        source_capsule_id=draft.source_capsule_id,
        memory_policy_id=draft.memory_policy_id,
        requested_scope=draft.requested_scope,
        sensitivity=draft.sensitivity,
        lifecycle_state="candidate",
        content_ciphertext=draft.content_ciphertext,
        content_nonce=draft.content_nonce,
        content_key_version=draft.content_key_version,
        content_sha256=draft.content_sha256,
        source_resource_id=draft.source_resource_id,
        source_resource_version=draft.source_resource_version,
        evidence_reference_ids=list(draft.evidence_reference_ids),
        confidence_millis=draft.confidence_millis,
        retention_days=draft.retention_days,
        requires_user_confirmation=draft.requires_user_confirmation,
        contains_secret=False,
        inferred_sensitive_categories=[],
        candidate_created_by="agent",
    )
    session.add(candidate)
    session.flush()
    result_sha256 = _digest(
        {
            "candidate_id": candidate.id,
            "content_sha256": candidate.content_sha256,
            "state": candidate.lifecycle_state,
        }
    )
    effect = MemoryEffectModel(
        tenant_id=draft.tenant_id,
        owner_user_id=draft.owner_user_id,
        workspace_id=draft.workspace_id,
        operation_id=draft.operation_id,
        candidate_id=candidate.id,
        memory_id=None,
        effect_kind="candidate_create",
        request_sha256=draft.request_sha256,
        state="committed",
        result_sha256=result_sha256,
    )
    session.add(effect)
    _complete_operation(
        session,
        tenant_id=draft.tenant_id,
        operation_id=draft.operation_id,
        expected_version=draft.operation_expected_version,
        result_ref={"candidate_id": candidate.id, "result_sha256": result_sha256},
    )
    append_audit_event(
        session,
        tenant_id=draft.tenant_id,
        request_id=draft.request_id,
        actor_type="agent",
        actor_id=task.agent_definition_id,
        workspace_id=draft.workspace_id,
        resource_id=draft.source_resource_id,
        operation_id=draft.operation_id,
        action="memory.candidate.create",
        decision="allowed",
        risk_level="R1",
        input_hash=draft.request_sha256,
        row_count=1,
        details={"candidate_id": candidate.id, "result_sha256": result_sha256},
    )
    session.flush()
    return candidate


def candidate_confirmation_sha256(candidate: MemoryCandidateModel) -> str:
    return _digest(
        {
            "agent_version_id": candidate.agent_version_id,
            "candidate_id": candidate.id,
            "content_sha256": candidate.content_sha256,
            "memory_policy_id": candidate.memory_policy_id,
            "owner_user_id": candidate.owner_user_id,
            "requested_scope": candidate.requested_scope,
            "source_capsule_id": candidate.source_capsule_id,
            "source_resource_id": candidate.source_resource_id,
            "source_resource_version": candidate.source_resource_version,
            "task_id": candidate.task_id,
            "tenant_id": candidate.tenant_id,
            "workspace_id": candidate.workspace_id,
        }
    )


def _scope_storage(candidate: MemoryCandidateModel) -> tuple[str | None, str | None]:
    if candidate.requested_scope == "user_private":
        return None, None
    if candidate.requested_scope == "agent_private":
        return candidate.workspace_id, candidate.agent_version_id
    return candidate.workspace_id, None


def confirm_candidate(
    session: Session, confirmation: CandidateConfirmation
) -> tuple[MemoryModel, MemoryVersionModel]:
    """Consume exact Agent approval and atomically publish version one."""
    if confirmation.token_count < 1:
        raise ValueError("token_count must be positive")
    candidate = session.scalar(
        select(MemoryCandidateModel)
        .where(
            MemoryCandidateModel.id == confirmation.candidate_id,
            MemoryCandidateModel.tenant_id == confirmation.tenant_id,
        )
        .with_for_update()
    )
    if candidate is None:
        raise MemoryNotFound("Memory Candidate not found")
    if candidate.owner_user_id != confirmation.owner_user_id:
        raise MemoryNotFound("Memory Candidate not found")
    if candidate.lifecycle_state not in {"candidate", "awaiting_confirmation"}:
        raise MemoryConflict("Candidate is not publishable")
    if candidate.content_ciphertext is None or candidate.content_nonce is None:
        raise MemoryConflict("Candidate payload is unavailable")
    _require_live_owner(
        session,
        tenant_id=confirmation.tenant_id,
        owner_user_id=confirmation.owner_user_id,
        workspace_id=candidate.workspace_id,
    )
    task = _require_task_context(
        session,
        tenant_id=confirmation.tenant_id,
        owner_user_id=confirmation.owner_user_id,
        workspace_id=candidate.workspace_id,
        agent_version_id=candidate.agent_version_id,
        task_id=candidate.task_id,
    )
    request_sha256 = candidate_confirmation_sha256(candidate)
    _require_operation(
        session,
        tenant_id=confirmation.tenant_id,
        operation_id=confirmation.operation_id,
        expected_version=confirmation.operation_expected_version,
        state="pending_approval",
        kind="memory.candidate.accept",
        actor_type="agent",
        actor_id=task.agent_definition_id,
        workspace_id=candidate.workspace_id,
        request_sha256=request_sha256,
        resource_id=candidate.source_resource_id,
        resource_version=candidate.source_resource_version,
    )
    authorized = authorize_operation(
        session,
        tenant_id=confirmation.tenant_id,
        operation_id=confirmation.operation_id,
        expected_version=confirmation.operation_expected_version,
        approval_id=confirmation.approval_id,
        approval_expected_version=confirmation.approval_expected_version,
        consumer_actor_type="agent",
        consumer_actor_id=task.agent_definition_id,
        action="memory.candidate.accept",
        workspace_id=candidate.workspace_id,
        run_id=None,
        request_hash=request_sha256,
        resource_version=candidate.source_resource_version,
        grant_id=confirmation.grant_id,
    )
    memory_id = _uuid()
    result_sha256 = _digest(
        {
            "candidate_id": candidate.id,
            "content_sha256": candidate.content_sha256,
            "memory_id": memory_id,
            "version": 1,
        }
    )
    _complete_operation(
        session,
        tenant_id=confirmation.tenant_id,
        operation_id=confirmation.operation_id,
        expected_version=authorized.version,
        result_ref={"memory_id": memory_id, "result_sha256": result_sha256},
    )
    confirmed_at = _database_now(session)
    workspace_id, agent_version_id = _scope_storage(candidate)
    review_id = _uuid() if candidate.requested_scope == "controlled_shared" else None
    memory = MemoryModel(
        id=memory_id,
        tenant_id=confirmation.tenant_id,
        owner_user_id=confirmation.owner_user_id,
        workspace_id=workspace_id,
        agent_version_id=agent_version_id,
        scope=candidate.requested_scope,
        sensitivity=candidate.sensitivity,
        lifecycle_state="active",
        current_version=1,
        created_from_candidate_id=candidate.id,
        review_evidence_id=review_id,
        deletion_effect_id=None,
        created_at=confirmed_at,
        updated_at=confirmed_at,
    )
    version = MemoryVersionModel(
        id=_uuid(),
        tenant_id=confirmation.tenant_id,
        memory_id=memory_id,
        version=1,
        content_ciphertext=candidate.content_ciphertext,
        content_nonce=candidate.content_nonce,
        content_key_version=candidate.content_key_version,
        content_sha256=candidate.content_sha256,
        source_resource_id=candidate.source_resource_id,
        source_resource_version=candidate.source_resource_version,
        evidence_reference_ids=list(candidate.evidence_reference_ids),
        token_count=confirmation.token_count,
        created_at=confirmed_at,
    )
    session.add_all([memory, version])
    session.flush([memory, version])
    if review_id is not None:
        review_payload: dict[str, object] = {
            "content_sha256": candidate.content_sha256,
            "decision": "approved",
            "memory_id": memory_id,
            "memory_version": 1,
            "reviewed_at": confirmed_at.isoformat(),
            "reviewer_user_id": confirmation.owner_user_id,
            "tenant_id": confirmation.tenant_id,
            "workspace_id": candidate.workspace_id,
        }
        session.add(
            MemoryReviewEvidenceModel(
                id=review_id,
                tenant_id=confirmation.tenant_id,
                reviewer_user_id=confirmation.owner_user_id,
                workspace_id=candidate.workspace_id,
                memory_id=memory_id,
                memory_version=1,
                content_sha256=candidate.content_sha256,
                decision="approved",
                evidence_sha256=_digest(review_payload),
                reviewed_at=confirmed_at,
                created_at=confirmed_at,
            )
        )
    candidate.lifecycle_state = "accepted"
    candidate.active_memory_id = memory_id
    candidate.acceptance_operation_id = confirmation.operation_id
    candidate.acceptance_approval_id = confirmation.approval_id
    candidate.confirmed_by_user_id = confirmation.owner_user_id
    candidate.confirmed_at = confirmed_at
    candidate.confirmation_sha256 = request_sha256
    session.add(
        MemoryEffectModel(
            tenant_id=confirmation.tenant_id,
            owner_user_id=confirmation.owner_user_id,
            workspace_id=candidate.workspace_id,
            operation_id=confirmation.operation_id,
            memory_id=memory_id,
            candidate_id=candidate.id,
            effect_kind="publish",
            request_sha256=request_sha256,
            state="committed",
            result_sha256=result_sha256,
        )
    )
    append_audit_event(
        session,
        tenant_id=confirmation.tenant_id,
        request_id=confirmation.request_id,
        actor_type="user",
        actor_id=confirmation.owner_user_id,
        workspace_id=candidate.workspace_id,
        grant_id=confirmation.grant_id,
        resource_id=candidate.source_resource_id,
        approval_id=confirmation.approval_id,
        operation_id=confirmation.operation_id,
        action="memory.candidate.accept",
        decision="allowed",
        risk_level="R2",
        input_hash=request_sha256,
        after_version=1,
        row_count=1,
        details={"memory_id": memory_id, "result_sha256": result_sha256},
    )
    session.flush()
    publication_constraints = (
        "memory_candidates_publication_binding, " "memories_candidate_publication_binding"
    )
    session.execute(text(f"SET CONSTRAINTS {publication_constraints} IMMEDIATE"))
    session.execute(text(f"SET CONSTRAINTS {publication_constraints} DEFERRED"))
    return memory, version


def _require_owner_memory(
    session: Session, operation: OwnerMemoryOperation
) -> tuple[MemoryModel, MemoryVersionModel]:
    memory = session.scalar(
        select(MemoryModel)
        .where(
            MemoryModel.id == operation.memory_id,
            MemoryModel.tenant_id == operation.tenant_id,
            MemoryModel.owner_user_id == operation.owner_user_id,
        )
        .with_for_update()
    )
    if memory is None or memory.current_version is None:
        raise MemoryNotFound("Memory not found")
    _require_live_owner(
        session,
        tenant_id=operation.tenant_id,
        owner_user_id=operation.owner_user_id,
        workspace_id=memory.workspace_id or _candidate_workspace(session, memory),
    )
    version = session.scalar(
        select(MemoryVersionModel).where(
            MemoryVersionModel.tenant_id == operation.tenant_id,
            MemoryVersionModel.memory_id == operation.memory_id,
            MemoryVersionModel.version == memory.current_version,
        )
    )
    if version is None:
        raise MemoryConflict("current Memory version is missing")
    return memory, version


def _candidate_workspace(session: Session, memory: MemoryModel) -> str:
    workspace_id = session.scalar(
        select(MemoryCandidateModel.workspace_id).where(
            MemoryCandidateModel.id == memory.created_from_candidate_id,
            MemoryCandidateModel.tenant_id == memory.tenant_id,
        )
    )
    if not isinstance(workspace_id, str):
        raise MemoryConflict("Memory source Candidate is missing")
    return workspace_id


def _logical_export(memory: MemoryModel, version: MemoryVersionModel) -> bytes:
    return _canonical_bytes(
        {
            "content_sha256": version.content_sha256,
            "created_at": memory.created_at.isoformat(),
            "evidence_reference_ids": sorted(version.evidence_reference_ids),
            "lifecycle_state": memory.lifecycle_state,
            "memory_id": memory.id,
            "owner_user_id": memory.owner_user_id,
            "provenance": {
                "candidate_id": memory.created_from_candidate_id,
                "source_resource_id": version.source_resource_id,
                "source_resource_version": version.source_resource_version,
            },
            "retention": "governed_by_candidate",
            "scope": memory.scope,
            "sensitivity": memory.sensitivity,
            "tenant_id": memory.tenant_id,
            "token_count": version.token_count,
            "version": version.version,
            "workspace_id": memory.workspace_id,
        }
    )


def export_memory(session: Session, operation: OwnerMemoryOperation) -> MemoryExport:
    """Create canonical logical export bytes without physical locators or keys."""
    _validate_sha256(operation.request_sha256, "request_sha256")
    memory, version = _require_owner_memory(session, operation)
    if memory.lifecycle_state not in {"active", "blocked"}:
        raise MemoryConflict("Memory is not exportable")
    workspace_id = memory.workspace_id or _candidate_workspace(session, memory)
    _require_operation(
        session,
        tenant_id=operation.tenant_id,
        operation_id=operation.operation_id,
        expected_version=operation.operation_expected_version,
        state="queued",
        kind="memory.export",
        actor_type="user",
        actor_id=operation.owner_user_id,
        workspace_id=workspace_id,
        request_sha256=operation.request_sha256,
        resource_id=version.source_resource_id,
        resource_version=version.source_resource_version,
    )
    payload = _logical_export(memory, version)
    result_sha256 = hashlib.sha256(payload).hexdigest()
    effect = MemoryEffectModel(
        tenant_id=operation.tenant_id,
        owner_user_id=operation.owner_user_id,
        workspace_id=memory.workspace_id,
        operation_id=operation.operation_id,
        memory_id=memory.id,
        candidate_id=memory.created_from_candidate_id,
        effect_kind="export",
        request_sha256=operation.request_sha256,
        state="committed",
        result_sha256=result_sha256,
    )
    session.add(effect)
    _complete_operation(
        session,
        tenant_id=operation.tenant_id,
        operation_id=operation.operation_id,
        expected_version=operation.operation_expected_version,
        result_ref={"memory_id": memory.id, "result_sha256": result_sha256},
    )
    append_audit_event(
        session,
        tenant_id=operation.tenant_id,
        request_id=operation.request_id,
        actor_type="user",
        actor_id=operation.owner_user_id,
        workspace_id=workspace_id,
        resource_id=version.source_resource_id,
        operation_id=operation.operation_id,
        action="memory.export",
        decision="allowed",
        risk_level="R1",
        input_hash=operation.request_sha256,
        bytes_out=len(payload),
        details={"memory_id": memory.id, "result_sha256": result_sha256},
    )
    session.flush()
    return MemoryExport(payload=payload, sha256=result_sha256, effect_id=effect.id)


def delete_memory(
    session: Session, operation: OwnerMemoryOperation, *, reason_code: str
) -> MemoryTombstoneModel:
    """Crypto-erase one Memory and complete its code-only tombstone atomically."""
    _validate_sha256(operation.request_sha256, "request_sha256")
    if _REASON.fullmatch(reason_code) is None:
        raise ValueError("reason_code must be a bounded code-like identifier")
    memory, version = _require_owner_memory(session, operation)
    if memory.lifecycle_state not in {"active", "blocked"}:
        raise MemoryConflict("Memory cannot enter deletion")
    workspace_id = memory.workspace_id or _candidate_workspace(session, memory)
    _require_operation(
        session,
        tenant_id=operation.tenant_id,
        operation_id=operation.operation_id,
        expected_version=operation.operation_expected_version,
        state="queued",
        kind="memory.delete",
        actor_type="user",
        actor_id=operation.owner_user_id,
        workspace_id=workspace_id,
        request_sha256=operation.request_sha256,
        resource_id=version.source_resource_id,
        resource_version=version.source_resource_version,
    )
    result_sha256 = _digest(
        {
            "last_memory_version": version.version,
            "memory_id": memory.id,
            "reason_code": reason_code,
            "state": "deleted",
        }
    )
    effect = MemoryEffectModel(
        id=_uuid(),
        tenant_id=operation.tenant_id,
        owner_user_id=operation.owner_user_id,
        workspace_id=memory.workspace_id,
        operation_id=operation.operation_id,
        memory_id=memory.id,
        candidate_id=memory.created_from_candidate_id,
        effect_kind="delete",
        request_sha256=operation.request_sha256,
        state="committed",
        result_sha256=result_sha256,
    )
    session.add(effect)
    session.flush()
    memory.lifecycle_state = "deletion_pending"
    memory.deletion_effect_id = effect.id
    session.flush()
    tombstone = MemoryTombstoneModel(
        tenant_id=operation.tenant_id,
        memory_id=memory.id,
        last_memory_version=version.version,
        deleted_by_user_id=operation.owner_user_id,
        owner_user_id=operation.owner_user_id,
        workspace_id=memory.workspace_id,
        deletion_effect_id=effect.id,
        request_sha256=operation.request_sha256,
        result_sha256=result_sha256,
        reason_code=reason_code,
        deletion_sha256=result_sha256,
        state="pending",
        completed_at=None,
    )
    session.add(tombstone)
    session.flush()
    session.execute(text("SET CONSTRAINTS memories_current_version_tenant_fk DEFERRED"))
    candidate = session.scalar(
        select(MemoryCandidateModel)
        .where(
            MemoryCandidateModel.id == memory.created_from_candidate_id,
            MemoryCandidateModel.tenant_id == operation.tenant_id,
        )
        .with_for_update()
    )
    if candidate is None:
        raise MemoryConflict("Memory source Candidate is missing")
    candidate.content_ciphertext = None
    candidate.content_nonce = None
    session.flush()
    session.execute(
        delete(MemoryEmbeddingV1Model).where(
            MemoryEmbeddingV1Model.tenant_id == operation.tenant_id,
            MemoryEmbeddingV1Model.memory_id == memory.id,
        )
    )
    session.execute(
        delete(MemoryEmbeddingV2Model).where(
            MemoryEmbeddingV2Model.tenant_id == operation.tenant_id,
            MemoryEmbeddingV2Model.memory_id == memory.id,
        )
    )
    session.execute(
        delete(MemoryVersionModel).where(
            MemoryVersionModel.tenant_id == operation.tenant_id,
            MemoryVersionModel.memory_id == memory.id,
        )
    )
    completed_at = _database_now(session)
    tombstone.state = "completed"
    tombstone.completed_at = completed_at
    session.flush()
    memory.lifecycle_state = "deleted"
    memory.current_version = None
    memory.deleted_at = _database_now(session)
    _complete_operation(
        session,
        tenant_id=operation.tenant_id,
        operation_id=operation.operation_id,
        expected_version=operation.operation_expected_version,
        result_ref={"memory_id": memory.id, "result_sha256": result_sha256},
    )
    append_audit_event(
        session,
        tenant_id=operation.tenant_id,
        request_id=operation.request_id,
        actor_type="user",
        actor_id=operation.owner_user_id,
        workspace_id=workspace_id,
        resource_id=version.source_resource_id,
        operation_id=operation.operation_id,
        action="memory.delete",
        decision="allowed",
        risk_level="R1",
        input_hash=operation.request_sha256,
        before_version=version.version,
        row_count=1,
        details={"memory_id": memory.id, "result_sha256": result_sha256},
    )
    session.flush()
    return tombstone


__all__ = [
    "CandidateConfirmation",
    "CandidateDraft",
    "MemoryConflict",
    "MemoryExport",
    "MemoryNotFound",
    "MemoryPersistenceError",
    "OwnerMemoryOperation",
    "candidate_confirmation_sha256",
    "confirm_candidate",
    "create_candidate",
    "delete_memory",
    "export_memory",
]
