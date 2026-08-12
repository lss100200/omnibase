"""Guarded PostgreSQL proof for the P5.5B tenant Memory foundation.

The shared integration sentinel must authorize an isolated
``omnibase_test_p55b_*`` database before this module can run.  The tests create
only a run-owned tenant schema, exercise the real Alembic 0013 DDL, and leave
volume removal to the guarded Makefile target.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Column, MetaData, Table, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import DBAPIError

from omnibase.agent_memory.service import (
    CandidateConfirmation,
    CandidateDraft,
    OwnerMemoryOperation,
    candidate_confirmation_sha256,
    confirm_candidate,
    create_candidate,
    delete_memory,
    export_memory,
)
from omnibase.control_plane.service import create_approval, create_operation, decide_approval
from tests.integration.test_p5_1b_agent_registry_foundation import (
    ACTOR_ID,
    _binding_dto,
    _binding_mapping,
    _install,
    _seed_definition_version,
    _session,
)
from tests.integration.test_p5_2b_task_ledger_foundation import _insert_minimal_task

pytestmark = pytest.mark.integration

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_BACKUP_SCRIPT = _BACKEND_ROOT.parent / "scripts" / "production" / "manage_p5_personal_backup.py"
_BACKUP_SPEC = importlib.util.spec_from_file_location("p55b_backup_inventory", _BACKUP_SCRIPT)
assert _BACKUP_SPEC is not None
assert _BACKUP_SPEC.loader is not None
backup_inventory = importlib.util.module_from_spec(_BACKUP_SPEC)
_BACKUP_SPEC.loader.exec_module(backup_inventory)
_MEMORY_TABLES = {
    "context_capsule_items",
    "context_capsules",
    "memories",
    "memory_candidates",
    "memory_effects",
    "memory_embeddings_v1",
    "memory_embeddings_v2",
    "memory_review_evidence",
    "memory_tombstones",
    "memory_versions",
}
_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_DIGEST_C = "c" * 64
_DIGEST_D = "d" * 64


@dataclass(frozen=True, slots=True)
class _MemorySeed:
    tenant_id: str
    schema_name: str
    owner_user_id: str
    other_user_id: str
    workspace_id: str
    agent_definition_id: str
    agent_version_id: str
    task_id: str
    source_resource_id: str


@dataclass(frozen=True, slots=True)
class _PublishedMemory:
    candidate_id: str
    memory_id: str
    review_evidence_id: str | None
    content_sha256: str
    acceptance_operation_id: str
    acceptance_approval_id: str


def _run_alembic(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=_BACKEND_ROOT,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )


def _alembic(*arguments: str) -> None:
    result = _run_alembic(*arguments)
    assert result.returncode == 0, result.stdout + result.stderr


def _create_run_owned_tenant(connection, run_owned_resources) -> tuple[str, str]:
    tenant_id = str(uuid.uuid4())
    suffix = uuid.uuid4().hex[:8]
    schema_name = f"tenant_{suffix}"
    connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    connection.execute(
        text(
            "INSERT INTO omnibase_meta.tenants "
            "(id, name, slug, schema_name, is_default, is_active) "
            "VALUES (:id, :name, :slug, :schema, FALSE, TRUE)"
        ),
        {
            "id": tenant_id,
            "name": "P5.5B disposable tenant",
            "slug": f"p55b-{suffix}",
            "schema": schema_name,
        },
    )
    run_owned_resources.add(tenant_id, schema_name)
    return tenant_id, schema_name


def _head(connection, schema_name: str) -> str:
    return str(
        connection.execute(
            text(f'SELECT version_num FROM "{schema_name}".alembic_version')  # noqa: S608
        ).scalar_one()
    )


def _set_tenant_search_path(connection, schema_name: str) -> None:  # type: ignore[no-untyped-def]
    connection.execute(text(f'SET LOCAL search_path TO "{schema_name}", omnibase_meta, public'))


def _assert_nested_rejection(
    connection,
    message: str,
    action: Callable[[], None],
) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(DBAPIError, match=message), connection.begin_nested():
        action()


def _seed_memory_target(db_engine, run_owned_resources, label: str) -> _MemorySeed:  # type: ignore[no-untyped-def]
    tenant_id, workspace_id, version = _seed_definition_version(
        db_engine,
        run_owned_resources,
        label,
    )
    binding = _binding_dto(
        _binding_mapping(
            tenant_id,
            workspace_id,
            version.agent_definition_id,
            version,
        )
    )
    with _session(db_engine, tenant_id) as session:
        installed = _install(
            session,
            tenant_id=tenant_id,
            binding=binding,
            key=uuid.uuid4().hex,
        )
        session.commit()

    other_user_id = str(uuid.uuid4())
    with db_engine.begin() as connection:
        schema_name = str(
            connection.execute(
                text("SELECT schema_name FROM omnibase_meta.tenants WHERE id = :tenant"),
                {"tenant": tenant_id},
            ).scalar_one()
        )
        _set_tenant_search_path(connection, schema_name)
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, email, password_hash, is_tenant_admin, is_active) "
                "VALUES (:id, :email, :password_hash, FALSE, TRUE)"
            ),
            {
                "id": other_user_id,
                "email": f"p55b-{label}-{uuid.uuid4().hex[:8]}@example.invalid",
                "password_hash": uuid.uuid4().hex,
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_memberships "
                "(tenant_id, workspace_id, user_id, role, state, created_by_user_id) "
                "VALUES (:tenant, :workspace, :owner, 'owner', 'active', :owner)"
            ),
            {
                "tenant": tenant_id,
                "workspace": workspace_id,
                "owner": ACTOR_ID,
            },
        )
        task_id = _insert_minimal_task(
            connection,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            version=version,
            binding=installed,
        )
    return _MemorySeed(
        tenant_id=tenant_id,
        schema_name=schema_name,
        owner_user_id=ACTOR_ID,
        other_user_id=other_user_id,
        workspace_id=workspace_id,
        agent_definition_id=version.agent_definition_id,
        agent_version_id=version.agent_version_id,
        task_id=task_id,
        source_resource_id=task_id,
    )


def _advance_task_to_succeeded(connection, seed: _MemorySeed) -> None:  # type: ignore[no-untyped-def]
    for state in ("scheduled", "running", "succeeded"):
        connection.execute(
            text(
                "UPDATE omnibase_meta.agent_tasks SET state = :state "
                "WHERE id = :task AND tenant_id = :tenant"
            ),
            {"state": state, "task": seed.task_id, "tenant": seed.tenant_id},
        )


def _insert_source_capsule(
    connection,
    seed: _MemorySeed,
    *,
    invocation_id: str,
    memory_policy_id: str,
) -> str:  # type: ignore[no-untyped-def]
    source_capsule_id = str(uuid.uuid4())
    connection.execute(
        text(
            "INSERT INTO context_capsules "
            "(id, tenant_id, owner_user_id, workspace_id, agent_version_id, task_id, "
            " invocation_id, memory_policy_id, compiler_policy_sha256, issued_at, expires_at, "
            " max_tokens, total_tokens, delegable, trusted_instructions, "
            " sensitivity_summary, content_sha256) VALUES "
            "(:id, :tenant, :owner, :workspace, :agent_version, :task, :invocation, :policy, "
            " :digest, clock_timestamp(), clock_timestamp() + interval '5 minutes', "
            " 128, 1, FALSE, FALSE, '{}'::jsonb, :digest)"
        ),
        {
            "id": source_capsule_id,
            "tenant": seed.tenant_id,
            "owner": seed.owner_user_id,
            "workspace": seed.workspace_id,
            "agent_version": seed.agent_version_id,
            "task": seed.task_id,
            "invocation": invocation_id,
            "policy": memory_policy_id,
            "digest": _DIGEST_A,
        },
    )
    return source_capsule_id


def _insert_candidate(
    connection,
    seed: _MemorySeed,
    *,
    scope: str = "workspace_private",
    sensitivity: str = "standard",
    candidate_id: str | None = None,
    content_sha256: str = _DIGEST_A,
) -> str:  # type: ignore[no-untyped-def]
    candidate_id = candidate_id or str(uuid.uuid4())
    invocation_id = str(uuid.uuid4())
    memory_policy_id = str(uuid.uuid4())
    source_capsule_id = str(uuid.uuid4())
    connection.execute(
        text(
            "INSERT INTO context_capsules "
            "(id, tenant_id, owner_user_id, workspace_id, agent_version_id, task_id, "
            " invocation_id, memory_policy_id, compiler_policy_sha256, issued_at, expires_at, "
            " max_tokens, total_tokens, delegable, trusted_instructions, "
            " sensitivity_summary, content_sha256) VALUES "
            "(:id, :tenant, :owner, :workspace, :agent_version, :task, :invocation, :policy, "
            " :digest, clock_timestamp(), clock_timestamp() + interval '5 minutes', "
            " 128, 1, FALSE, FALSE, '{}'::jsonb, :digest)"
        ),
        {
            "id": source_capsule_id,
            "tenant": seed.tenant_id,
            "owner": seed.owner_user_id,
            "workspace": seed.workspace_id,
            "agent_version": seed.agent_version_id,
            "task": seed.task_id,
            "invocation": invocation_id,
            "policy": memory_policy_id,
            "digest": _DIGEST_A,
        },
    )
    connection.execute(
        text(
            "INSERT INTO memory_candidates "
            "(id, tenant_id, owner_user_id, workspace_id, agent_version_id, task_id, "
            " invocation_id, source_capsule_id, memory_policy_id, requested_scope, sensitivity, lifecycle_state, "
            " content_ciphertext, content_nonce, content_key_version, content_sha256, "
            " source_resource_id, source_resource_version, evidence_reference_ids, "
            " confidence_millis, retention_days, requires_user_confirmation, contains_secret, "
            " inferred_sensitive_categories, candidate_created_by) VALUES "
            "(:id, :tenant, :owner, :workspace, :agent_version, :task, :invocation, :capsule, :policy, "
            " :scope, :sensitivity, 'candidate', :ciphertext, :nonce, 1, :content_sha, "
            " :resource, 1, CAST(:evidence AS jsonb), 900, 30, :requires_confirmation, "
            " FALSE, '[]'::jsonb, 'agent')"
        ),
        {
            "id": candidate_id,
            "tenant": seed.tenant_id,
            "owner": seed.owner_user_id,
            "workspace": seed.workspace_id,
            "agent_version": seed.agent_version_id,
            "task": seed.task_id,
            "invocation": invocation_id,
            "capsule": source_capsule_id,
            "policy": memory_policy_id,
            "scope": scope,
            "sensitivity": sensitivity,
            "ciphertext": b"candidate-ciphertext",
            "nonce": b"candidate-nonce",
            "content_sha": content_sha256,
            "resource": seed.source_resource_id,
            "evidence": f'["{uuid.uuid4()}"]',
            "requires_confirmation": scope == "controlled_shared"
            or sensitivity in {"sensitive", "restricted"},
        },
    )
    return candidate_id


def _insert_acceptance_authority(
    connection,
    seed: _MemorySeed,
    *,
    confirmation_sha256: str,
    operation_request_sha256: str | None = None,
    approval_request_sha256: str | None = None,
    operation_actor_type: str = "agent",
    operation_actor_id: str | None = None,
    approval_requester_type: str = "agent",
    approval_requester_id: str | None = None,
    approval_decider_user_id: str | None = None,
) -> tuple[str, str, datetime]:  # type: ignore[no-untyped-def]
    operation_id = str(uuid.uuid4())
    approval_id = str(uuid.uuid4())
    operation_request_sha256 = operation_request_sha256 or confirmation_sha256
    approval_request_sha256 = approval_request_sha256 or confirmation_sha256
    operation_actor_id = operation_actor_id or seed.agent_definition_id
    approval_requester_id = approval_requester_id or seed.agent_definition_id
    approval_decider_user_id = approval_decider_user_id or seed.owner_user_id
    now = datetime.now(UTC)
    connection.execute(
        text(
            "INSERT INTO omnibase_meta.operations "
            "(id, tenant_id, workspace_id, actor_type, actor_id, approval_id, request_hash, "
            " resource_id, resource_version, kind, state, risk_level, progress, completed_at) VALUES "
            "(:id, :tenant, :workspace, :actor_type, :actor, :approval, :request_hash, :resource, 1, "
            " 'memory.candidate.accept', 'succeeded', 'R2', 100, :completed_at)"
        ),
        {
            "id": operation_id,
            "tenant": seed.tenant_id,
            "workspace": seed.workspace_id,
            "actor_type": operation_actor_type,
            "actor": operation_actor_id,
            "approval": approval_id,
            "request_hash": operation_request_sha256,
            "resource": seed.source_resource_id,
            "completed_at": now,
        },
    )
    connection.execute(
        text(
            "INSERT INTO omnibase_meta.approval_requests "
            "(id, tenant_id, requester_type, requester_id, workspace_id, operation_id, "
            " grant_id, resource_id, resource_version, action, risk_level, required_approver_role, state, request_hash, "
            " version, decided_by_actor_type, decided_by_actor_id, expires_at, decided_at, "
            " consumed_at) VALUES "
            "(:id, :tenant, :requester_type, :requester, :workspace, :operation, :grant, :resource, 1, "
            " 'memory.candidate.accept', 'R2', 'tenant_admin', 'consumed', :request_hash, "
            " 3, 'user', :decider, :expires_at, :decided_at, :consumed_at)"
        ),
        {
            "id": approval_id,
            "tenant": seed.tenant_id,
            "requester_type": approval_requester_type,
            "requester": approval_requester_id,
            "workspace": seed.workspace_id,
            "operation": operation_id,
            "grant": str(uuid.uuid4()),
            "resource": seed.source_resource_id,
            "request_hash": approval_request_sha256,
            "decider": approval_decider_user_id,
            "expires_at": now + timedelta(hours=1),
            "decided_at": now - timedelta(seconds=2),
            "consumed_at": now - timedelta(seconds=1),
        },
    )
    return operation_id, approval_id, now


def _memory_scope_binding(seed: _MemorySeed, scope: str) -> tuple[str | None, str | None]:
    if scope == "user_private":
        return None, None
    if scope == "agent_private":
        return seed.workspace_id, seed.agent_version_id
    return seed.workspace_id, None


def _insert_memory_and_version(
    connection,
    seed: _MemorySeed,
    *,
    candidate_id: str,
    memory_id: str,
    scope: str,
    content_sha256: str,
    review_evidence_id: str | None = None,
) -> None:  # type: ignore[no-untyped-def]
    workspace_id, agent_version_id = _memory_scope_binding(seed, scope)
    connection.execute(
        text(
            "INSERT INTO memories "
            "(id, tenant_id, owner_user_id, workspace_id, agent_version_id, scope, sensitivity, "
            " lifecycle_state, current_version, created_from_candidate_id, review_evidence_id) "
            "VALUES (:id, :tenant, :owner, :workspace, :agent_version, :scope, 'standard', "
            " 'active', 1, :candidate, :review)"
        ),
        {
            "id": memory_id,
            "tenant": seed.tenant_id,
            "owner": seed.owner_user_id,
            "workspace": workspace_id,
            "agent_version": agent_version_id,
            "scope": scope,
            "candidate": candidate_id,
            "review": review_evidence_id,
        },
    )
    connection.execute(
        text(
            "INSERT INTO memory_versions "
            "(tenant_id, memory_id, version, content_ciphertext, content_nonce, "
            " content_key_version, content_sha256, source_resource_id, source_resource_version, "
            " evidence_reference_ids, token_count, created_at) VALUES "
            "(:tenant, :memory, 1, :ciphertext, :nonce, 1, :content_sha, :resource, 1, "
            " CAST(:evidence AS jsonb), 7, :created_at)"
        ),
        {
            "tenant": seed.tenant_id,
            "memory": memory_id,
            "ciphertext": b"version-ciphertext",
            "nonce": b"version-nonce",
            "content_sha": content_sha256,
            "resource": seed.source_resource_id,
            "evidence": f'["{uuid.uuid4()}"]',
            "created_at": datetime.now(UTC) - timedelta(minutes=2),
        },
    )


def _insert_review(
    connection,
    seed: _MemorySeed,
    *,
    review_evidence_id: str,
    memory_id: str,
    content_sha256: str,
    reviewer_user_id: str | None = None,
    evidence_sha256: str = _DIGEST_D,
    reviewed_at: datetime | None = None,
) -> datetime:  # type: ignore[no-untyped-def]
    reviewed_at = reviewed_at or datetime.now(UTC) - timedelta(minutes=1)
    connection.execute(
        text(
            "INSERT INTO memory_review_evidence "
            "(id, tenant_id, reviewer_user_id, workspace_id, memory_id, memory_version, "
            " content_sha256, decision, evidence_sha256, reviewed_at, created_at) VALUES "
            "(:id, :tenant, :reviewer, :workspace, :memory, 1, :content_sha, 'approved', "
            " :evidence_sha, :reviewed_at, :created_at)"
        ),
        {
            "id": review_evidence_id,
            "tenant": seed.tenant_id,
            "reviewer": reviewer_user_id or seed.owner_user_id,
            "workspace": seed.workspace_id,
            "memory": memory_id,
            "content_sha": content_sha256,
            "evidence_sha": evidence_sha256,
            "reviewed_at": reviewed_at,
            "created_at": reviewed_at + timedelta(seconds=1),
        },
    )
    return reviewed_at


def _accept_candidate(
    connection,
    seed: _MemorySeed,
    *,
    candidate_id: str,
    memory_id: str,
    operation_id: str,
    approval_id: str,
    confirmed_at: datetime,
    confirmation_sha256: str,
    confirmed_by_user_id: str | None = None,
) -> None:  # type: ignore[no-untyped-def]
    connection.execute(
        text(
            "UPDATE memory_candidates SET lifecycle_state = 'accepted', "
            "active_memory_id = :memory, acceptance_operation_id = :operation, "
            "acceptance_approval_id = :approval, confirmed_by_user_id = :confirmed_by, "
            "confirmed_at = :confirmed_at, confirmation_sha256 = :confirmation_sha "
            "WHERE id = :candidate AND tenant_id = :tenant"
        ),
        {
            "memory": memory_id,
            "operation": operation_id,
            "approval": approval_id,
            "confirmed_by": confirmed_by_user_id or seed.owner_user_id,
            "confirmed_at": confirmed_at,
            "confirmation_sha": confirmation_sha256,
            "candidate": candidate_id,
            "tenant": seed.tenant_id,
        },
    )


def _publish_memory(
    connection,
    seed: _MemorySeed,
    *,
    candidate_id: str,
    scope: str = "workspace_private",
    content_sha256: str = _DIGEST_A,
    review_reviewer_user_id: str | None = None,
    review_content_sha256: str | None = None,
) -> _PublishedMemory:  # type: ignore[no-untyped-def]
    confirmation_sha256 = _DIGEST_C
    operation_id, approval_id, confirmed_at = _insert_acceptance_authority(
        connection,
        seed,
        confirmation_sha256=confirmation_sha256,
    )
    memory_id = str(uuid.uuid4())
    review_evidence_id = str(uuid.uuid4()) if scope == "controlled_shared" else None
    _insert_memory_and_version(
        connection,
        seed,
        candidate_id=candidate_id,
        memory_id=memory_id,
        scope=scope,
        content_sha256=content_sha256,
        review_evidence_id=review_evidence_id,
    )
    if review_evidence_id is not None:
        _insert_review(
            connection,
            seed,
            review_evidence_id=review_evidence_id,
            memory_id=memory_id,
            content_sha256=review_content_sha256 or content_sha256,
            reviewer_user_id=review_reviewer_user_id,
        )
    _accept_candidate(
        connection,
        seed,
        candidate_id=candidate_id,
        memory_id=memory_id,
        operation_id=operation_id,
        approval_id=approval_id,
        confirmed_at=confirmed_at,
        confirmation_sha256=confirmation_sha256,
    )
    connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    return _PublishedMemory(
        candidate_id=candidate_id,
        memory_id=memory_id,
        review_evidence_id=review_evidence_id,
        content_sha256=content_sha256,
        acceptance_operation_id=operation_id,
        acceptance_approval_id=approval_id,
    )


def _insert_delete_effect(
    connection,
    seed: _MemorySeed,
    *,
    memory_id: str,
) -> tuple[str, str, str]:  # type: ignore[no-untyped-def]
    operation_id = str(uuid.uuid4())
    effect_id = str(uuid.uuid4())
    request_sha256 = _DIGEST_B
    result_sha256 = _DIGEST_D
    connection.execute(
        text(
            "INSERT INTO omnibase_meta.operations "
            "(id, tenant_id, workspace_id, actor_type, actor_id, request_hash, kind, state, "
            " risk_level, progress, completed_at) VALUES "
            "(:id, :tenant, :workspace, 'user', :owner, :request_hash, 'memory.delete', "
            " 'succeeded', 'R1', 100, clock_timestamp())"
        ),
        {
            "id": operation_id,
            "tenant": seed.tenant_id,
            "workspace": seed.workspace_id,
            "owner": seed.owner_user_id,
            "request_hash": request_sha256,
        },
    )
    connection.execute(
        text(
            "INSERT INTO memory_effects "
            "(id, tenant_id, owner_user_id, workspace_id, operation_id, memory_id, "
            " effect_kind, request_sha256, state) VALUES "
            "(:id, :tenant, :owner, :workspace, :operation, :memory, 'delete', "
            " :request_sha, 'pending')"
        ),
        {
            "id": effect_id,
            "tenant": seed.tenant_id,
            "owner": seed.owner_user_id,
            "workspace": seed.workspace_id,
            "operation": operation_id,
            "memory": memory_id,
            "request_sha": request_sha256,
        },
    )
    connection.execute(
        text(
            "UPDATE memory_effects SET state = 'committed', result_sha256 = :result_sha "
            "WHERE id = :id AND tenant_id = :tenant"
        ),
        {"result_sha": result_sha256, "id": effect_id, "tenant": seed.tenant_id},
    )
    return effect_id, request_sha256, result_sha256


def _begin_pending_delete(
    connection,
    seed: _MemorySeed,
    published: _PublishedMemory,
) -> str:  # type: ignore[no-untyped-def]
    effect_id, request_sha256, result_sha256 = _insert_delete_effect(
        connection,
        seed,
        memory_id=published.memory_id,
    )
    connection.execute(
        text(
            "UPDATE memories SET lifecycle_state = 'deletion_pending', "
            "deletion_effect_id = :effect WHERE id = :memory AND tenant_id = :tenant"
        ),
        {"effect": effect_id, "memory": published.memory_id, "tenant": seed.tenant_id},
    )
    tombstone_id = str(uuid.uuid4())
    connection.execute(
        text(
            "INSERT INTO memory_tombstones "
            "(id, tenant_id, memory_id, last_memory_version, deleted_by_user_id, "
            " owner_user_id, workspace_id, deletion_effect_id, request_sha256, result_sha256, "
            " reason_code, deletion_sha256, state) VALUES "
            "(:id, :tenant, :memory, 1, :owner, :owner, :workspace, :effect, "
            " :request_sha, :result_sha, 'owner_requested_delete', :result_sha, 'pending')"
        ),
        {
            "id": tombstone_id,
            "tenant": seed.tenant_id,
            "memory": published.memory_id,
            "owner": seed.owner_user_id,
            "workspace": seed.workspace_id,
            "effect": effect_id,
            "request_sha": request_sha256,
            "result_sha": result_sha256,
        },
    )
    return tombstone_id


def _run_formal_memory_service_full_lifecycle_reaches_real_postgresql(
    db_engine, run_owned_resources
) -> None:  # type: ignore[no-untyped-def]
    _alembic("upgrade", "head")
    seed = _seed_memory_target(db_engine, run_owned_resources, "formal-service")
    with db_engine.begin() as connection:
        _set_tenant_search_path(connection, seed.schema_name)
        _advance_task_to_succeeded(connection, seed)

    invocation_id = str(uuid.uuid4())
    memory_policy_id = str(uuid.uuid4())
    grant_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    with _session(db_engine, seed.tenant_id) as session:
        source_capsule_id = _insert_source_capsule(
            session,
            seed,
            invocation_id=invocation_id,
            memory_policy_id=memory_policy_id,
        )
        create_record = create_operation(
            session,
            tenant_id=seed.tenant_id,
            kind="memory.candidate.create",
            risk_level="R1",
            actor_type="agent",
            actor_id=seed.agent_definition_id,
            workspace_id=seed.workspace_id,
            resource_id=seed.source_resource_id,
            resource_version=1,
            request_hash=_DIGEST_B,
        )
        candidate = create_candidate(
            session,
            CandidateDraft(
                tenant_id=seed.tenant_id,
                owner_user_id=seed.owner_user_id,
                workspace_id=seed.workspace_id,
                agent_version_id=seed.agent_version_id,
                task_id=seed.task_id,
                invocation_id=invocation_id,
                source_capsule_id=source_capsule_id,
                memory_policy_id=memory_policy_id,
                requested_scope="workspace_private",
                sensitivity="personal",
                content_ciphertext=b"formal-service-ciphertext",
                content_nonce=b"formal-service-nonce",
                content_key_version=1,
                content_sha256=_DIGEST_A,
                source_resource_id=seed.source_resource_id,
                source_resource_version=1,
                evidence_reference_ids=(str(uuid.uuid4()),),
                confidence_millis=900,
                retention_days=30,
                requires_user_confirmation=False,
                operation_id=create_record.id,
                operation_expected_version=1,
                request_sha256=_DIGEST_B,
                request_id=f"p55b-create-{uuid.uuid4()}",
            ),
        )
        confirmation_digest = candidate_confirmation_sha256(candidate)
        session.execute(
            text(
                "INSERT INTO omnibase_meta.capability_grants "
                "(id, tenant_id, workspace_id, runtime_instance_id, actor_user_id, "
                " actions, resource_ids, constraints, not_before, expires_at, max_calls, "
                " max_bytes, max_cost_units, delegation_depth, delegation_depth_limit, "
                " created_by_actor_type, created_by_actor_id) VALUES "
                "(:id, :tenant, :workspace, :runtime, :owner, "
                " ARRAY['data.rows.read']::varchar[], ARRAY[:resource]::uuid[], "
                " CAST(:constraints AS jsonb), :not_before, :expires_at, 10, 10000, 10, "
                " 0, 0, 'system', :issuer)"
            ),
            {
                "id": grant_id,
                "tenant": seed.tenant_id,
                "workspace": seed.workspace_id,
                "runtime": str(uuid.uuid4()),
                "owner": seed.owner_user_id,
                "resource": seed.source_resource_id,
                "constraints": '{"timeout_ms":1500}',
                "not_before": now - timedelta(minutes=1),
                "expires_at": now + timedelta(minutes=10),
                "issuer": str(uuid.uuid4()),
            },
        )
        acceptance_record = create_operation(
            session,
            tenant_id=seed.tenant_id,
            kind="memory.candidate.accept",
            risk_level="R2",
            actor_type="agent",
            actor_id=seed.agent_definition_id,
            workspace_id=seed.workspace_id,
            resource_id=seed.source_resource_id,
            resource_version=1,
            request_hash=confirmation_digest,
        )
        approval = create_approval(
            session,
            tenant_id=seed.tenant_id,
            requester_type="agent",
            requester_id=seed.agent_definition_id,
            action="memory.candidate.accept",
            risk_level="R2",
            request_hash=confirmation_digest,
            expires_at=now + timedelta(minutes=10),
            grant_id=grant_id,
            workspace_id=seed.workspace_id,
            resource_id=seed.source_resource_id,
            resource_version=1,
            operation_id=acceptance_record.id,
        )
        decided = decide_approval(
            session,
            tenant_id=seed.tenant_id,
            approval_id=approval.id,
            expected_version=1,
            decided_by_actor_type="user",
            decided_by_actor_id=seed.owner_user_id,
            decided_by_actor_role="tenant_admin",
            decision="approved",
            decision_reason="personal Owner accepted exact Candidate",
            request_hash=confirmation_digest,
            resource_version=1,
        )
        memory, version = confirm_candidate(
            session,
            CandidateConfirmation(
                tenant_id=seed.tenant_id,
                candidate_id=candidate.id,
                owner_user_id=seed.owner_user_id,
                operation_id=acceptance_record.id,
                operation_expected_version=1,
                approval_id=approval.id,
                approval_expected_version=decided.version,
                grant_id=grant_id,
                token_count=7,
                request_id=f"p55b-accept-{uuid.uuid4()}",
            ),
        )
        assert version.memory_id == memory.id

        export_record = create_operation(
            session,
            tenant_id=seed.tenant_id,
            kind="memory.export",
            risk_level="R1",
            actor_type="user",
            actor_id=seed.owner_user_id,
            workspace_id=seed.workspace_id,
            resource_id=seed.source_resource_id,
            resource_version=1,
            request_hash=_DIGEST_C,
        )
        exported = export_memory(
            session,
            OwnerMemoryOperation(
                tenant_id=seed.tenant_id,
                memory_id=memory.id,
                owner_user_id=seed.owner_user_id,
                operation_id=export_record.id,
                operation_expected_version=1,
                request_sha256=_DIGEST_C,
                request_id=f"p55b-export-{uuid.uuid4()}",
            ),
        )
        assert exported.payload.endswith(b"\n")
        assert b"formal-service-ciphertext" not in exported.payload
        assert all(
            locator not in exported.payload for locator in (b"schema", b"table", b"database")
        )

        delete_record = create_operation(
            session,
            tenant_id=seed.tenant_id,
            kind="memory.delete",
            risk_level="R1",
            actor_type="user",
            actor_id=seed.owner_user_id,
            workspace_id=seed.workspace_id,
            resource_id=seed.source_resource_id,
            resource_version=1,
            request_hash=_DIGEST_D,
        )
        tombstone = delete_memory(
            session,
            OwnerMemoryOperation(
                tenant_id=seed.tenant_id,
                memory_id=memory.id,
                owner_user_id=seed.owner_user_id,
                operation_id=delete_record.id,
                operation_expected_version=1,
                request_sha256=_DIGEST_D,
                request_id=f"p55b-delete-{uuid.uuid4()}",
            ),
            reason_code="owner_requested",
        )
        memory_id = memory.id
        candidate_id = candidate.id
        assert tombstone.state == "completed"
        session.commit()

    with db_engine.connect() as connection, connection.begin():
        captured = backup_inventory._capture_postgres_inventory_value(
            connection,
            capture_mode="source_backup",
            expected_database=str(db_engine.url.database),
            expected_head="0015",
            postgres_dump_sha256=_DIGEST_D,
        )
    assert captured["global_alembic_head"] == "0015"
    assert captured["postgres_dump_sha256"] == _DIGEST_D
    tenant_registry = {entry["tenant_id"]: entry for entry in captured["tenant_registry"]}
    assert tenant_registry[seed.tenant_id] == {
        "is_active": True,
        "schema_name": seed.schema_name,
        "tenant_id": seed.tenant_id,
    }
    memory_inventories = {
        entry["tenant_id"]: entry for entry in captured["tenant_memory_inventories"]
    }
    assert memory_inventories[seed.tenant_id]["memory_table_names"] == sorted(_MEMORY_TABLES)

    with _session(db_engine, seed.tenant_id) as session:
        terminal_memory = session.execute(
            text(
                "SELECT lifecycle_state, current_version FROM memories "
                "WHERE id = :memory AND tenant_id = :tenant"
            ),
            {"memory": memory_id, "tenant": seed.tenant_id},
        ).one()
        candidate_payload = session.execute(
            text(
                "SELECT lifecycle_state, content_ciphertext, content_nonce "
                "FROM memory_candidates WHERE id = :candidate AND tenant_id = :tenant"
            ),
            {"candidate": candidate_id, "tenant": seed.tenant_id},
        ).one()
        effect_kinds = set(
            session.execute(
                text(
                    "SELECT effect_kind FROM memory_effects "
                    "WHERE tenant_id = :tenant AND candidate_id = :candidate"
                ),
                {"tenant": seed.tenant_id, "candidate": candidate_id},
            ).scalars()
        )
        audit_actions = set(
            session.execute(
                text(
                    "SELECT action FROM audit_events WHERE tenant_id = :tenant "
                    "AND action LIKE 'memory.%'"
                ),
                {"tenant": seed.tenant_id},
            ).scalars()
        )
        version_count = session.execute(
            text(
                "SELECT count(*) FROM memory_versions "
                "WHERE tenant_id = :tenant AND memory_id = :memory"
            ),
            {"tenant": seed.tenant_id, "memory": memory_id},
        ).scalar_one()
        assert terminal_memory == ("deleted", None)
        assert candidate_payload == ("accepted", None, None)
        assert version_count == 0
        assert effect_kinds == {"candidate_create", "publish", "export", "delete"}
        assert audit_actions == {
            "memory.candidate.accept",
            "memory.candidate.create",
            "memory.delete",
            "memory.export",
        }


def test_0013_real_tenant_ddl_vector_lanes_and_empty_round_trip(
    db_engine, run_owned_resources
) -> None:  # type: ignore[no-untyped-def]
    _alembic("upgrade", "head")
    with db_engine.begin() as connection:
        tenant_id, schema_name = _create_run_owned_tenant(connection, run_owned_resources)
        other_tenant_id, other_schema_name = _create_run_owned_tenant(
            connection, run_owned_resources
        )

    _alembic("upgrade", "head")

    with db_engine.begin() as connection:
        assert _head(connection, "omnibase_meta") == "0015"
        assert _head(connection, schema_name) == "0015"
        tables = set(
            connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :schema AND table_name = ANY(:tables)"
                ),
                {"schema": schema_name, "tables": sorted(_MEMORY_TABLES)},
            ).scalars()
        )
        assert tables == _MEMORY_TABLES

        vector_types = dict(
            connection.execute(
                text(
                    "SELECT c.relname, format_type(a.atttypid, a.atttypmod) "
                    "FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "JOIN pg_attribute a ON a.attrelid = c.oid "
                    "WHERE n.nspname = :schema AND a.attname = 'embedding' "
                    "AND c.relname IN ('memory_embeddings_v1', 'memory_embeddings_v2')"
                ),
                {"schema": schema_name},
            )
            .tuples()
            .all()
        )
        assert vector_types == {
            "memory_embeddings_v1": "vector(1024)",
            "memory_embeddings_v2": "vector(1536)",
        }

        hnsw_indexes = set(
            connection.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE schemaname = :schema "
                    "AND indexname IN "
                    "('memory_embeddings_v1_embedding_hnsw_idx', "
                    " 'memory_embeddings_v2_embedding_hnsw_idx')"
                ),
                {"schema": schema_name},
            ).scalars()
        )
        assert hnsw_indexes == {
            "memory_embeddings_v1_embedding_hnsw_idx",
            "memory_embeddings_v2_embedding_hnsw_idx",
        }

        tenant_guard_count = connection.execute(
            text(
                "SELECT count(*) FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :schema AND NOT t.tgisinternal "
                "AND t.tgname LIKE '%_tenant_schema_guard'"
            ),
            {"schema": schema_name},
        ).scalar_one()
        assert tenant_guard_count == len(_MEMORY_TABLES)

        connection.execute(
            text(
                f'CREATE TABLE "{schema_name}".p55b_tenant_binding_probe '
                "(tenant_id uuid NOT NULL)"
            )
        )
        connection.execute(
            text(
                f"CREATE TRIGGER p55b_tenant_binding_probe_guard BEFORE INSERT OR UPDATE "
                f'ON "{schema_name}".p55b_tenant_binding_probe FOR EACH ROW '
                f'EXECUTE FUNCTION "{schema_name}".memory_assert_tenant_schema_binding()'
            )
        )
        connection.execute(text(f'SET LOCAL search_path TO "{other_schema_name}", public'))
        probe = Table(
            "p55b_tenant_binding_probe",
            MetaData(),
            Column("tenant_id", UUID(as_uuid=False), nullable=False),
            schema=schema_name,
        )
        with (
            pytest.raises(
                DBAPIError, match="memory tenant_id does not match current tenant schema"
            ),
            connection.begin_nested(),
        ):
            connection.execute(probe.insert().values(tenant_id=other_tenant_id))
        connection.execute(text(f'DROP TABLE "{schema_name}".p55b_tenant_binding_probe'))

        connection.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))
        with (
            pytest.raises(
                DBAPIError, match="memory tenant_id does not match current tenant schema"
            ),
            connection.begin_nested(),
        ):
            connection.execute(
                text(
                    "INSERT INTO context_capsules "
                    "(tenant_id, owner_user_id, workspace_id, agent_version_id, task_id, "
                    " invocation_id, memory_policy_id, compiler_policy_sha256, issued_at, "
                    " expires_at, max_tokens, total_tokens, sensitivity_summary, "
                    " content_sha256) VALUES "
                    "(:tenant, gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), "
                    " gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), :digest, "
                    " clock_timestamp(), clock_timestamp() + interval '1 minute', 1, 1, "
                    " '{}'::jsonb, :digest)"
                ),
                {"tenant": str(uuid.uuid4()), "digest": "0" * 64},
            )

    _alembic("downgrade", "0012")
    with db_engine.begin() as connection:
        assert _head(connection, "omnibase_meta") == "0012"
        assert _head(connection, schema_name) == "0012"
        remaining = connection.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_name = ANY(:tables)"
            ),
            {"schema": schema_name, "tables": sorted(_MEMORY_TABLES)},
        ).scalar_one()
        assert remaining == 0

    _alembic("upgrade", "head")
    with db_engine.begin() as connection:
        assert _head(connection, "omnibase_meta") == "0015"
        assert _head(connection, schema_name) == "0015"
        assert connection.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_name = ANY(:tables)"
            ),
            {"schema": schema_name, "tables": sorted(_MEMORY_TABLES)},
        ).scalar_one() == len(_MEMORY_TABLES)
        assert (
            connection.execute(
                text("SELECT schema_name FROM omnibase_meta.tenants WHERE id = :tenant"),
                {"tenant": tenant_id},
            ).scalar_one()
            == schema_name
        )


def test_formal_memory_service_full_lifecycle_reaches_real_postgresql(
    db_engine, run_owned_resources
) -> None:  # type: ignore[no-untyped-def]
    """Run after the empty downgrade proof so audited data cannot mask it."""
    _run_formal_memory_service_full_lifecycle_reaches_real_postgresql(
        db_engine, run_owned_resources
    )


def test_direct_accepted_candidate_insert_is_rejected(db_engine, run_owned_resources) -> None:  # type: ignore[no-untyped-def]
    seed = _seed_memory_target(db_engine, run_owned_resources, "direct-accepted")
    with db_engine.begin() as connection:
        _set_tenant_search_path(connection, seed.schema_name)
        invocation_id = str(uuid.uuid4())
        memory_policy_id = str(uuid.uuid4())
        source_capsule_id = str(uuid.uuid4())
        connection.execute(
            text(
                "INSERT INTO context_capsules "
                "(id, tenant_id, owner_user_id, workspace_id, agent_version_id, task_id, "
                " invocation_id, memory_policy_id, compiler_policy_sha256, issued_at, expires_at, "
                " max_tokens, total_tokens, delegable, trusted_instructions, "
                " sensitivity_summary, content_sha256) VALUES "
                "(:id, :tenant, :owner, :workspace, :agent_version, :task, :invocation, :policy, "
                " :digest, clock_timestamp(), clock_timestamp() + interval '5 minutes', "
                " 128, 1, FALSE, FALSE, '{}'::jsonb, :digest)"
            ),
            {
                "id": source_capsule_id,
                "tenant": seed.tenant_id,
                "owner": seed.owner_user_id,
                "workspace": seed.workspace_id,
                "agent_version": seed.agent_version_id,
                "task": seed.task_id,
                "invocation": invocation_id,
                "policy": memory_policy_id,
                "digest": _DIGEST_A,
            },
        )
        with (
            pytest.raises(DBAPIError, match="memory candidate insert must begin unaccepted"),
            connection.begin_nested(),
        ):
            connection.execute(
                text(
                    "INSERT INTO memory_candidates "
                    "(id, tenant_id, owner_user_id, workspace_id, agent_version_id, task_id, "
                    " invocation_id, source_capsule_id, memory_policy_id, requested_scope, sensitivity, "
                    " lifecycle_state, content_ciphertext, content_nonce, content_key_version, "
                    " content_sha256, source_resource_id, source_resource_version, "
                    " evidence_reference_ids, confidence_millis, retention_days, "
                    " requires_user_confirmation, contains_secret, inferred_sensitive_categories, "
                    " active_memory_id, acceptance_operation_id, acceptance_approval_id, "
                    " confirmed_by_user_id, confirmed_at, confirmation_sha256, "
                    " candidate_created_by) VALUES "
                    "(:id, :tenant, :owner, :workspace, :agent_version, :task, :invocation, :capsule, "
                    " :policy, 'workspace_private', 'standard', 'accepted', :ciphertext, :nonce, "
                    " 1, :digest, :resource, 1, CAST(:evidence AS jsonb), 900, 30, FALSE, "
                    " FALSE, '[]'::jsonb, :memory, :operation, :approval, :owner, "
                    " clock_timestamp(), :digest, 'agent')"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "tenant": seed.tenant_id,
                    "owner": seed.owner_user_id,
                    "workspace": seed.workspace_id,
                    "agent_version": seed.agent_version_id,
                    "task": seed.task_id,
                    "invocation": invocation_id,
                    "capsule": source_capsule_id,
                    "policy": memory_policy_id,
                    "ciphertext": b"direct-ciphertext",
                    "nonce": b"direct-nonce",
                    "digest": _DIGEST_A,
                    "resource": seed.source_resource_id,
                    "evidence": f'["{uuid.uuid4()}"]',
                    "memory": str(uuid.uuid4()),
                    "operation": str(uuid.uuid4()),
                    "approval": str(uuid.uuid4()),
                },
            )
        assert connection.execute(text("SELECT count(*) FROM memory_candidates")).scalar_one() == 0


def test_candidate_cannot_self_activate_without_durable_memory(
    db_engine, run_owned_resources
) -> None:  # type: ignore[no-untyped-def]
    seed = _seed_memory_target(db_engine, run_owned_resources, "self-activate")
    with db_engine.begin() as connection:
        _set_tenant_search_path(connection, seed.schema_name)
        candidate_id = _insert_candidate(connection, seed)

    with db_engine.begin() as connection:
        _set_tenant_search_path(connection, seed.schema_name)

        def attempt_self_activation() -> None:
            operation_id, approval_id, confirmed_at = _insert_acceptance_authority(
                connection,
                seed,
                confirmation_sha256=_DIGEST_C,
            )
            _accept_candidate(
                connection,
                seed,
                candidate_id=candidate_id,
                memory_id=str(uuid.uuid4()),
                operation_id=operation_id,
                approval_id=approval_id,
                confirmed_at=confirmed_at,
                confirmation_sha256=_DIGEST_C,
            )
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

        _assert_nested_rejection(
            connection,
            "memory_candidates_active_memory_tenant_fk",
            attempt_self_activation,
        )
        assert (
            connection.execute(
                text("SELECT lifecycle_state FROM memory_candidates WHERE id = :candidate"),
                {"candidate": candidate_id},
            ).scalar_one()
            == "candidate"
        )


def test_deferred_candidate_memory_cross_wire_is_rejected_atomically(
    db_engine, run_owned_resources
) -> None:  # type: ignore[no-untyped-def]
    seed = _seed_memory_target(db_engine, run_owned_resources, "cross-wire")
    with db_engine.begin() as connection:
        _set_tenant_search_path(connection, seed.schema_name)
        candidate_a = _insert_candidate(connection, seed, content_sha256=_DIGEST_A)
        candidate_b = _insert_candidate(connection, seed, content_sha256=_DIGEST_B)

    with db_engine.begin() as connection:
        _set_tenant_search_path(connection, seed.schema_name)

        def attempt_cross_wire() -> None:
            authority_a = _insert_acceptance_authority(
                connection,
                seed,
                confirmation_sha256=_DIGEST_C,
            )
            authority_b = _insert_acceptance_authority(
                connection,
                seed,
                confirmation_sha256=_DIGEST_D,
            )
            memory_a = str(uuid.uuid4())
            memory_b = str(uuid.uuid4())
            _insert_memory_and_version(
                connection,
                seed,
                candidate_id=candidate_a,
                memory_id=memory_a,
                scope="workspace_private",
                content_sha256=_DIGEST_A,
            )
            _insert_memory_and_version(
                connection,
                seed,
                candidate_id=candidate_b,
                memory_id=memory_b,
                scope="workspace_private",
                content_sha256=_DIGEST_B,
            )
            _accept_candidate(
                connection,
                seed,
                candidate_id=candidate_a,
                memory_id=memory_b,
                operation_id=authority_a[0],
                approval_id=authority_a[1],
                confirmed_at=authority_a[2],
                confirmation_sha256=_DIGEST_C,
            )
            _accept_candidate(
                connection,
                seed,
                candidate_id=candidate_b,
                memory_id=memory_a,
                operation_id=authority_b[0],
                approval_id=authority_b[1],
                confirmed_at=authority_b[2],
                confirmation_sha256=_DIGEST_D,
            )
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

        _assert_nested_rejection(
            connection,
            "memory candidate publication identity binding drifted",
            attempt_cross_wire,
        )

        assert connection.execute(text("SELECT count(*) FROM memories")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM memory_versions")).scalar_one() == 0
        assert set(
            connection.execute(
                text(
                    "SELECT lifecycle_state FROM memory_candidates "
                    "WHERE id IN (:candidate_a, :candidate_b)"
                ),
                {"candidate_a": candidate_a, "candidate_b": candidate_b},
            ).scalars()
        ) == {"candidate"}


@pytest.mark.parametrize(
    ("drift", "message"),
    [
        ("operation_request", "memory candidate acceptance operation binding drifted"),
        ("approval_request", "memory candidate acceptance approval binding drifted"),
        ("operation_owner", "memory candidate acceptance operation binding drifted"),
        ("approval_owner", "memory candidate acceptance approval binding drifted"),
        ("confirmed_owner", "memory candidate confirmation must be performed by Owner"),
    ],
)
def test_candidate_acceptance_authority_drift_is_rejected(
    db_engine,
    run_owned_resources,
    drift: str,
    message: str,
) -> None:  # type: ignore[no-untyped-def]
    seed = _seed_memory_target(db_engine, run_owned_resources, f"authority-{drift}")
    with db_engine.begin() as connection:
        _set_tenant_search_path(connection, seed.schema_name)
        candidate_id = _insert_candidate(connection, seed)

    with db_engine.begin() as connection:
        _set_tenant_search_path(connection, seed.schema_name)

        def attempt_drifted_acceptance() -> None:
            operation_id, approval_id, confirmed_at = _insert_acceptance_authority(
                connection,
                seed,
                confirmation_sha256=_DIGEST_C,
                operation_request_sha256=(_DIGEST_B if drift == "operation_request" else _DIGEST_C),
                approval_request_sha256=(_DIGEST_B if drift == "approval_request" else _DIGEST_C),
                operation_actor_id=(
                    seed.other_user_id if drift == "operation_owner" else seed.agent_definition_id
                ),
                approval_decider_user_id=(
                    seed.other_user_id if drift == "approval_owner" else seed.owner_user_id
                ),
            )
            memory_id = str(uuid.uuid4())
            _insert_memory_and_version(
                connection,
                seed,
                candidate_id=candidate_id,
                memory_id=memory_id,
                scope="workspace_private",
                content_sha256=_DIGEST_A,
            )
            _accept_candidate(
                connection,
                seed,
                candidate_id=candidate_id,
                memory_id=memory_id,
                operation_id=operation_id,
                approval_id=approval_id,
                confirmed_at=confirmed_at,
                confirmation_sha256=_DIGEST_C,
                confirmed_by_user_id=(
                    seed.other_user_id if drift == "confirmed_owner" else seed.owner_user_id
                ),
            )
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

        _assert_nested_rejection(connection, message, attempt_drifted_acceptance)

        assert (
            connection.execute(
                text("SELECT lifecycle_state FROM memory_candidates WHERE id = :candidate"),
                {"candidate": candidate_id},
            ).scalar_one()
            == "candidate"
        )
        assert connection.execute(text("SELECT count(*) FROM memories")).scalar_one() == 0


def test_owner_self_request_and_self_approval_cannot_replace_agent_requester(
    db_engine, run_owned_resources
) -> None:  # type: ignore[no-untyped-def]
    seed = _seed_memory_target(db_engine, run_owned_resources, "owner-self-approval")
    with db_engine.begin() as connection:
        _set_tenant_search_path(connection, seed.schema_name)
        candidate_id = _insert_candidate(connection, seed)

    with db_engine.begin() as connection:
        _set_tenant_search_path(connection, seed.schema_name)

        def attempt_owner_self_approval() -> None:
            operation_id, approval_id, confirmed_at = _insert_acceptance_authority(
                connection,
                seed,
                confirmation_sha256=_DIGEST_C,
                operation_actor_type="user",
                operation_actor_id=seed.owner_user_id,
                approval_requester_type="user",
                approval_requester_id=seed.owner_user_id,
                approval_decider_user_id=seed.owner_user_id,
            )
            memory_id = str(uuid.uuid4())
            _insert_memory_and_version(
                connection,
                seed,
                candidate_id=candidate_id,
                memory_id=memory_id,
                scope="workspace_private",
                content_sha256=_DIGEST_A,
            )
            _accept_candidate(
                connection,
                seed,
                candidate_id=candidate_id,
                memory_id=memory_id,
                operation_id=operation_id,
                approval_id=approval_id,
                confirmed_at=confirmed_at,
                confirmation_sha256=_DIGEST_C,
            )
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

        _assert_nested_rejection(
            connection,
            "memory candidate acceptance operation binding drifted",
            attempt_owner_self_approval,
        )
        assert (
            connection.execute(
                text("SELECT lifecycle_state FROM memory_candidates WHERE id = :candidate"),
                {"candidate": candidate_id},
            ).scalar_one()
            == "candidate"
        )
        assert connection.execute(text("SELECT count(*) FROM memories")).scalar_one() == 0


def test_owner_can_publish_controlled_shared_memory_with_exact_review(
    db_engine, run_owned_resources
) -> None:  # type: ignore[no-untyped-def]
    seed = _seed_memory_target(db_engine, run_owned_resources, "owner-publication")
    with db_engine.begin() as connection:
        _set_tenant_search_path(connection, seed.schema_name)
        candidate_id = _insert_candidate(connection, seed, scope="controlled_shared")
        published = _publish_memory(
            connection,
            seed,
            candidate_id=candidate_id,
            scope="controlled_shared",
        )

    with db_engine.connect() as connection:
        _set_tenant_search_path(connection, seed.schema_name)
        assert connection.execute(
            text(
                "SELECT candidate.lifecycle_state, candidate.active_memory_id::text, "
                "memory.lifecycle_state, memory.current_version, review.reviewer_user_id::text, "
                "review.decision, version.content_sha256 "
                "FROM memory_candidates candidate "
                "JOIN memories memory ON memory.id = candidate.active_memory_id "
                "JOIN memory_versions version ON version.memory_id = memory.id "
                " AND version.version = memory.current_version "
                "JOIN memory_review_evidence review ON review.id = memory.review_evidence_id "
                "WHERE candidate.id = :candidate"
            ),
            {"candidate": candidate_id},
        ).one() == (
            "accepted",
            published.memory_id,
            "active",
            1,
            seed.owner_user_id,
            "approved",
            _DIGEST_A,
        )


@pytest.mark.parametrize("drift", ["non_owner", "content"])
def test_controlled_shared_publication_review_drift_is_rejected(
    db_engine,
    run_owned_resources,
    drift: str,
) -> None:  # type: ignore[no-untyped-def]
    seed = _seed_memory_target(db_engine, run_owned_resources, f"shared-{drift}")
    with db_engine.begin() as connection:
        _set_tenant_search_path(connection, seed.schema_name)
        candidate_id = _insert_candidate(connection, seed, scope="controlled_shared")

    with db_engine.begin() as connection:
        _set_tenant_search_path(connection, seed.schema_name)
        with (
            pytest.raises(DBAPIError, match="memory review evidence binding drifted"),
            connection.begin_nested(),
        ):
            _publish_memory(
                connection,
                seed,
                candidate_id=candidate_id,
                scope="controlled_shared",
                review_reviewer_user_id=(
                    seed.other_user_id if drift == "non_owner" else seed.owner_user_id
                ),
                review_content_sha256=_DIGEST_B if drift == "content" else _DIGEST_A,
            )
        assert connection.execute(text("SELECT count(*) FROM memories")).scalar_one() == 0
        assert (
            connection.execute(
                text("SELECT lifecycle_state FROM memory_candidates WHERE id = :candidate"),
                {"candidate": candidate_id},
            ).scalar_one()
            == "candidate"
        )


def test_controlled_shared_review_after_capsule_issuance_is_rejected(
    db_engine, run_owned_resources
) -> None:  # type: ignore[no-untyped-def]
    seed = _seed_memory_target(db_engine, run_owned_resources, "review-after-capsule")
    with db_engine.begin() as connection:
        _set_tenant_search_path(connection, seed.schema_name)
        candidate_id = _insert_candidate(connection, seed, scope="controlled_shared")
        published = _publish_memory(
            connection,
            seed,
            candidate_id=candidate_id,
            scope="controlled_shared",
        )

    with db_engine.begin() as connection:
        _set_tenant_search_path(connection, seed.schema_name)
        baseline_capsules = connection.execute(
            text("SELECT count(*) FROM context_capsules")
        ).scalar_one()
        reviewed_at = connection.execute(
            text("SELECT reviewed_at FROM memory_review_evidence WHERE id = :review"),
            {"review": published.review_evidence_id},
        ).scalar_one()

        def attempt_late_review_item() -> None:
            capsule_id = str(uuid.uuid4())
            issued_at = reviewed_at - timedelta(seconds=1)
            connection.execute(
                text(
                    "INSERT INTO context_capsules "
                    "(id, tenant_id, owner_user_id, workspace_id, agent_version_id, task_id, "
                    " invocation_id, memory_policy_id, compiler_policy_sha256, issued_at, "
                    " expires_at, max_tokens, total_tokens, sensitivity_summary, content_sha256) "
                    "VALUES (:id, :tenant, :owner, :workspace, :agent_version, :task, "
                    " :invocation, :policy, :digest, :issued_at, :expires_at, 100, 7, "
                    " CAST(:summary AS jsonb), :digest)"
                ),
                {
                    "id": capsule_id,
                    "tenant": seed.tenant_id,
                    "owner": seed.owner_user_id,
                    "workspace": seed.workspace_id,
                    "agent_version": seed.agent_version_id,
                    "task": seed.task_id,
                    "invocation": str(uuid.uuid4()),
                    "policy": str(uuid.uuid4()),
                    "digest": _DIGEST_C,
                    "summary": '{"standard":1}',
                    "issued_at": issued_at,
                    "expires_at": issued_at + timedelta(hours=1),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO context_capsule_items "
                    "(tenant_id, capsule_id, position, memory_id, memory_version, scope, "
                    " owner_user_id, workspace_id, agent_version_id, review_evidence_id, "
                    " review_evidence_sha256, source_resource_id, source_resource_version, "
                    " evidence_reference_ids, content_sha256, selection_reason, sensitivity, "
                    " token_count) VALUES "
                    "(:tenant, :capsule, 1, :memory, 1, 'controlled_shared', :owner, "
                    " :workspace, NULL, :review, :review_digest, :resource, 1, "
                    " CAST(:evidence AS jsonb), :content_sha, 'explicit_user', 'standard', 7)"
                ),
                {
                    "tenant": seed.tenant_id,
                    "capsule": capsule_id,
                    "memory": published.memory_id,
                    "owner": seed.owner_user_id,
                    "workspace": seed.workspace_id,
                    "review": published.review_evidence_id,
                    "review_digest": _DIGEST_D,
                    "resource": seed.source_resource_id,
                    "evidence": f'["{published.review_evidence_id}"]',
                    "content_sha": published.content_sha256,
                },
            )

        _assert_nested_rejection(
            connection,
            "controlled-shared review evidence binding drifted",
            attempt_late_review_item,
        )
        assert (
            connection.execute(text("SELECT count(*) FROM context_capsules")).scalar_one()
            == baseline_capsules
        )


@pytest.mark.parametrize("scope", ["workspace_private", "controlled_shared"])
def test_complete_crypto_erasure_lifecycle_requires_exact_pending_tombstone(
    db_engine, run_owned_resources, scope: str
) -> None:  # type: ignore[no-untyped-def]
    seed = _seed_memory_target(db_engine, run_owned_resources, f"crypto-erasure-{scope}")
    with db_engine.begin() as connection:
        _set_tenant_search_path(connection, seed.schema_name)
        candidate_id = _insert_candidate(connection, seed, scope=scope)
        published = _publish_memory(
            connection,
            seed,
            candidate_id=candidate_id,
            scope=scope,
        )
        for table, dimension, model_digest in (
            ("memory_embeddings_v1", 1024, _DIGEST_A),
            ("memory_embeddings_v2", 1536, _DIGEST_B),
        ):
            connection.execute(
                text(
                    f"INSERT INTO {table} "  # noqa: S608 -- closed test table tuple
                    "(tenant_id, memory_id, memory_version, scope, workspace_id, "
                    " agent_version_id, content_sha256, embedding_model_id, "
                    " embedding_model_sha256, embedding) VALUES "
                    "(:tenant, :memory, 1, :scope, :workspace, NULL, "
                    " :content_sha, :model_id, :model_digest, CAST(:embedding AS vector))"
                ),
                {
                    "tenant": seed.tenant_id,
                    "memory": published.memory_id,
                    "scope": scope,
                    "workspace": seed.workspace_id,
                    "content_sha": published.content_sha256,
                    "model_id": f"p55b-{table}",
                    "model_digest": model_digest,
                    "embedding": "[" + ",".join("0" for _ in range(dimension)) + "]",
                },
            )

        tombstone_id = _begin_pending_delete(connection, seed, published)
        connection.execute(
            text(
                "UPDATE memory_candidates SET content_ciphertext = NULL, content_nonce = NULL "
                "WHERE id = :candidate AND tenant_id = :tenant"
            ),
            {"candidate": candidate_id, "tenant": seed.tenant_id},
        )
        connection.execute(
            text(
                "DELETE FROM memory_embeddings_v1 WHERE memory_id = :memory AND tenant_id = :tenant"
            ),
            {"memory": published.memory_id, "tenant": seed.tenant_id},
        )
        connection.execute(
            text(
                "DELETE FROM memory_embeddings_v2 WHERE memory_id = :memory AND tenant_id = :tenant"
            ),
            {"memory": published.memory_id, "tenant": seed.tenant_id},
        )
        connection.execute(
            text("DELETE FROM memory_versions WHERE memory_id = :memory AND tenant_id = :tenant"),
            {"memory": published.memory_id, "tenant": seed.tenant_id},
        )
        connection.execute(
            text(
                "UPDATE memory_tombstones SET state = 'completed', completed_at = clock_timestamp() "
                "WHERE id = :tombstone AND tenant_id = :tenant"
            ),
            {"tombstone": tombstone_id, "tenant": seed.tenant_id},
        )
        connection.execute(
            text(
                "UPDATE memories SET lifecycle_state = 'deleted', current_version = NULL, "
                "deleted_at = clock_timestamp() WHERE id = :memory AND tenant_id = :tenant"
            ),
            {"memory": published.memory_id, "tenant": seed.tenant_id},
        )
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

        assert connection.execute(
            text(
                "SELECT lifecycle_state, current_version, deleted_at IS NOT NULL "
                "FROM memories WHERE id = :memory"
            ),
            {"memory": published.memory_id},
        ).one() == ("deleted", None, True)
        assert connection.execute(
            text("SELECT state, completed_at IS NOT NULL FROM memory_tombstones WHERE id = :id"),
            {"id": tombstone_id},
        ).one() == ("completed", True)
        assert connection.execute(
            text(
                "SELECT content_ciphertext IS NULL, content_nonce IS NULL "
                "FROM memory_candidates WHERE id = :candidate"
            ),
            {"candidate": candidate_id},
        ).one() == (True, True)
        for table in ("memory_versions", "memory_embeddings_v1", "memory_embeddings_v2"):
            assert (
                connection.execute(
                    text(
                        f"SELECT count(*) FROM {table} "  # noqa: S608 -- closed test table tuple
                        "WHERE memory_id = :memory"
                    ),
                    {"memory": published.memory_id},
                ).scalar_one()
                == 0
            )


def test_fake_tombstone_and_incomplete_erasure_are_rejected(db_engine, run_owned_resources) -> None:  # type: ignore[no-untyped-def]
    seed = _seed_memory_target(db_engine, run_owned_resources, "incomplete-erasure")
    with db_engine.begin() as connection:
        _set_tenant_search_path(connection, seed.schema_name)
        candidate_id = _insert_candidate(connection, seed)
        published = _publish_memory(connection, seed, candidate_id=candidate_id)

    with db_engine.begin() as connection:
        _set_tenant_search_path(connection, seed.schema_name)

        def attempt_forged_tombstone() -> None:
            effect_id, request_sha256, result_sha256 = _insert_delete_effect(
                connection,
                seed,
                memory_id=published.memory_id,
            )
            connection.execute(
                text(
                    "INSERT INTO memory_tombstones "
                    "(tenant_id, memory_id, last_memory_version, deleted_by_user_id, "
                    " owner_user_id, workspace_id, deletion_effect_id, request_sha256, "
                    " result_sha256, reason_code, deletion_sha256, state) VALUES "
                    "(:tenant, :memory, 1, :owner, :owner, :workspace, :effect, "
                    " :request_sha, :result_sha, 'forged_delete', :result_sha, 'pending')"
                ),
                {
                    "tenant": seed.tenant_id,
                    "memory": published.memory_id,
                    "owner": seed.owner_user_id,
                    "workspace": seed.workspace_id,
                    "effect": effect_id,
                    "request_sha": request_sha256,
                    "result_sha": result_sha256,
                },
            )

        _assert_nested_rejection(
            connection,
            "memory tombstone deletion binding drifted",
            attempt_forged_tombstone,
        )

    with db_engine.begin() as connection:
        _set_tenant_search_path(connection, seed.schema_name)
        tombstone_id = _begin_pending_delete(connection, seed, published)

    with db_engine.begin() as connection:
        _set_tenant_search_path(connection, seed.schema_name)
        with (
            pytest.raises(DBAPIError, match="cannot complete before crypto-erasure"),
            connection.begin_nested(),
        ):
            connection.execute(
                text(
                    "UPDATE memory_tombstones SET state = 'completed', "
                    "completed_at = clock_timestamp() WHERE id = :id"
                ),
                {"id": tombstone_id},
            )
        assert (
            connection.execute(
                text("SELECT state FROM memory_tombstones WHERE id = :id"),
                {"id": tombstone_id},
            ).scalar_one()
            == "pending"
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM memory_versions WHERE memory_id = :memory"),
                {"memory": published.memory_id},
            ).scalar_one()
            == 1
        )


def test_mid_publication_exception_rolls_back_candidate_activation_and_payload(
    db_engine, run_owned_resources
) -> None:  # type: ignore[no-untyped-def]
    seed = _seed_memory_target(db_engine, run_owned_resources, "publication-rollback")
    with db_engine.begin() as connection:
        _set_tenant_search_path(connection, seed.schema_name)
        candidate_id = _insert_candidate(connection, seed)

    connection = db_engine.connect()
    transaction = connection.begin()
    try:
        _set_tenant_search_path(connection, seed.schema_name)
        _publish_memory(connection, seed, candidate_id=candidate_id)
        raise RuntimeError("synthetic failure after valid publication")
    except RuntimeError:
        transaction.rollback()
    finally:
        connection.close()

    with db_engine.connect() as connection:
        _set_tenant_search_path(connection, seed.schema_name)
        assert connection.execute(
            text(
                "SELECT lifecycle_state, active_memory_id, content_ciphertext IS NOT NULL "
                "FROM memory_candidates WHERE id = :candidate"
            ),
            {"candidate": candidate_id},
        ).one() == ("candidate", None, True)
        assert connection.execute(text("SELECT count(*) FROM memories")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM memory_versions")).scalar_one() == 0


def test_populated_0013_downgrade_fails_closed_without_head_or_data_drift(
    db_engine, run_owned_resources
) -> None:  # type: ignore[no-untyped-def]
    seed = _seed_memory_target(db_engine, run_owned_resources, "populated-downgrade")
    with db_engine.begin() as connection:
        _set_tenant_search_path(connection, seed.schema_name)
        candidate_id = _insert_candidate(connection, seed)

    result = _run_alembic("downgrade", "0012")
    assert result.returncode != 0
    assert "0013 populated downgrade is forbidden" in result.stdout + result.stderr

    with db_engine.connect() as connection:
        assert _head(connection, "omnibase_meta") == "0015"
        assert _head(connection, seed.schema_name) == "0015"
        _set_tenant_search_path(connection, seed.schema_name)
        assert connection.execute(
            text(
                "SELECT lifecycle_state, content_ciphertext IS NOT NULL "
                "FROM memory_candidates WHERE id = :candidate"
            ),
            {"candidate": candidate_id},
        ).one() == ("candidate", True)
