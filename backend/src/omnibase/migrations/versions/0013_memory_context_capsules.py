"""P5.5B tenant Memory and ContextCapsule persistence foundation.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-11

Global scope is an explicit no-op. Tenant scope creates only durable Memory
records, review evidence, ContextCapsules, deletion/effect evidence and two
independent embedding lanes. It does not expose a Browser API, install a
compiler/worker, inject Memory into a Runtime, or enable a Phase 5 feature gate.
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | None = None
depends_on: str | None = None

_GLOBAL_SCHEMA = "omnibase_meta"
_UUID = postgresql.UUID(as_uuid=False)
_JSONB = postgresql.JSONB(astext_type=sa.Text())
_SHA256 = "~ '^[0-9a-f]{64}$'"
_TENANT_SCHEMA_PATTERN = re.compile(r"^tenant_[a-z0-9]{8,12}$")
_SCOPES = "('user_private', 'workspace_private', 'agent_private', 'controlled_shared')"
_SENSITIVITY = "('standard', 'personal', 'sensitive', 'restricted')"
_MEMORY_VECTOR_LANE_VERSIONS = (1, 2)
_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_MEMORY_TABLES = (
    "memory_candidates",
    "memories",
    "memory_versions",
    "memory_review_evidence",
    "context_capsules",
    "context_capsule_items",
    "memory_effects",
    "memory_tombstones",
    "memory_embeddings_v1",
    "memory_embeddings_v2",
)


def _migration_schema_scope() -> str:
    config = op.get_context().config
    if config is None:
        raise RuntimeError("migration configuration is unavailable")
    scope = config.attributes.get("migration_schema_scope")
    if scope not in {"global", "tenant"}:
        raise RuntimeError(f"unsupported migration_schema_scope: {scope!r}")
    return str(scope)


def _id() -> sa.Column:
    return sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _tenant_id() -> sa.Column:
    return sa.Column(
        "tenant_id",
        _UUID,
        sa.ForeignKey(f"{_GLOBAL_SCHEMA}.tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("clock_timestamp()"),
    )


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("clock_timestamp()"),
    )


def _tenant_unique(name: str) -> sa.UniqueConstraint:
    return sa.UniqueConstraint("id", "tenant_id", name=name)


def _workspace_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["workspace_id", "tenant_id"],
        [f"{_GLOBAL_SCHEMA}.workspaces.id", f"{_GLOBAL_SCHEMA}.workspaces.tenant_id"],
        name=name,
        ondelete="RESTRICT",
    )


def _agent_version_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["agent_version_id", "tenant_id"],
        [
            f"{_GLOBAL_SCHEMA}.agent_versions.id",
            f"{_GLOBAL_SCHEMA}.agent_versions.tenant_id",
        ],
        name=name,
        ondelete="RESTRICT",
    )


def _task_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["task_id", "tenant_id"],
        [f"{_GLOBAL_SCHEMA}.agent_tasks.id", f"{_GLOBAL_SCHEMA}.agent_tasks.tenant_id"],
        name=name,
        ondelete="RESTRICT",
    )


def _scope_shape(column_prefix: str = "") -> str:
    scope = f"{column_prefix}scope"
    workspace = f"{column_prefix}workspace_id"
    agent = f"{column_prefix}agent_version_id"
    return (
        f"(({scope} = 'user_private' AND {workspace} IS NULL AND {agent} IS NULL) OR "
        f"({scope} IN ('workspace_private', 'controlled_shared') AND "
        f"{workspace} IS NOT NULL AND {agent} IS NULL) OR "
        f"({scope} = 'agent_private' AND {workspace} IS NOT NULL AND {agent} IS NOT NULL))"
    )


def _create_candidates() -> None:
    op.create_table(
        "memory_candidates",
        _id(),
        _tenant_id(),
        sa.Column(
            "owner_user_id", _UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("agent_version_id", _UUID, nullable=False),
        sa.Column("task_id", _UUID, nullable=False),
        sa.Column("invocation_id", _UUID, nullable=False),
        sa.Column("source_capsule_id", _UUID, nullable=False),
        sa.Column("memory_policy_id", _UUID, nullable=False),
        sa.Column("requested_scope", sa.String(24), nullable=False),
        sa.Column("sensitivity", sa.String(16), nullable=False),
        sa.Column("lifecycle_state", sa.String(24), nullable=False),
        sa.Column("content_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("content_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("content_key_version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("source_resource_id", _UUID, nullable=False),
        sa.Column("source_resource_version", sa.Integer(), nullable=False),
        sa.Column("evidence_reference_ids", _JSONB, nullable=False),
        sa.Column("confidence_millis", sa.Integer(), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("requires_user_confirmation", sa.Boolean(), nullable=False),
        sa.Column("contains_secret", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "inferred_sensitive_categories",
            _JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("active_memory_id", _UUID, nullable=True),
        sa.Column("acceptance_operation_id", _UUID, nullable=True),
        sa.Column("acceptance_approval_id", _UUID, nullable=True),
        sa.Column(
            "confirmed_by_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmation_sha256", sa.String(64), nullable=True),
        sa.Column("candidate_created_by", sa.String(16), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(f"requested_scope IN {_SCOPES}", name="memory_candidates_scope_check"),
        sa.CheckConstraint(
            f"sensitivity IN {_SENSITIVITY}", name="memory_candidates_sensitivity_check"
        ),
        sa.CheckConstraint(
            "lifecycle_state IN ('candidate', 'awaiting_confirmation', 'accepted', 'rejected', 'superseded')",
            name="memory_candidates_state_check",
        ),
        sa.CheckConstraint(f"content_sha256 {_SHA256}", name="memory_candidates_digest_check"),
        sa.CheckConstraint(
            "(content_ciphertext IS NULL) = (content_nonce IS NULL)",
            name="memory_candidates_payload_parity_check",
        ),
        sa.CheckConstraint(
            "confirmation_sha256 IS NULL OR confirmation_sha256 ~ '^[0-9a-f]{64}$'",
            name="memory_candidates_confirmation_digest_check",
        ),
        sa.CheckConstraint("content_key_version >= 1", name="memory_candidates_key_version_check"),
        sa.CheckConstraint(
            "source_resource_version >= 1", name="memory_candidates_resource_version_check"
        ),
        sa.CheckConstraint(
            "confidence_millis BETWEEN 0 AND 1000", name="memory_candidates_confidence_check"
        ),
        sa.CheckConstraint(
            "retention_days BETWEEN 1 AND 3650", name="memory_candidates_retention_check"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_reference_ids) = 'array' AND jsonb_array_length(evidence_reference_ids) >= 1",
            name="memory_candidates_evidence_check",
        ),
        sa.CheckConstraint(
            "contains_secret = false AND inferred_sensitive_categories = '[]'::jsonb",
            name="memory_candidates_sensitive_data_check",
        ),
        sa.CheckConstraint(
            "candidate_created_by = 'agent'", name="memory_candidates_creator_check"
        ),
        sa.CheckConstraint(
            "(lifecycle_state = 'accepted' AND active_memory_id IS NOT NULL "
            "AND acceptance_operation_id IS NOT NULL AND acceptance_approval_id IS NOT NULL "
            "AND confirmed_by_user_id IS NOT NULL AND confirmed_at IS NOT NULL "
            "AND confirmation_sha256 IS NOT NULL) OR "
            "(lifecycle_state <> 'accepted' AND active_memory_id IS NULL "
            "AND acceptance_operation_id IS NULL AND acceptance_approval_id IS NULL "
            "AND confirmed_by_user_id IS NULL AND confirmed_at IS NULL "
            "AND confirmation_sha256 IS NULL)",
            name="memory_candidates_active_binding_check",
        ),
        sa.CheckConstraint(
            "NOT (requested_scope = 'controlled_shared' OR sensitivity IN ('sensitive', 'restricted')) OR requires_user_confirmation",
            name="memory_candidates_confirmation_check",
        ),
        _tenant_unique("memory_candidates_id_tenant_uq"),
        _workspace_fk("memory_candidates_workspace_tenant_fk"),
        _agent_version_fk("memory_candidates_version_tenant_fk"),
        _task_fk("memory_candidates_task_tenant_fk"),
        sa.ForeignKeyConstraint(
            ["source_resource_id", "tenant_id"],
            [
                f"{_GLOBAL_SCHEMA}.resource_registry.id",
                f"{_GLOBAL_SCHEMA}.resource_registry.tenant_id",
            ],
            name="memory_candidates_resource_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["acceptance_operation_id", "tenant_id"],
            [f"{_GLOBAL_SCHEMA}.operations.id", f"{_GLOBAL_SCHEMA}.operations.tenant_id"],
            name="memory_candidates_accept_operation_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["acceptance_approval_id", "tenant_id"],
            [
                f"{_GLOBAL_SCHEMA}.approval_requests.id",
                f"{_GLOBAL_SCHEMA}.approval_requests.tenant_id",
            ],
            name="memory_candidates_accept_approval_tenant_fk",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "memory_candidates_scope_state_idx",
        "memory_candidates",
        ["tenant_id", "owner_user_id", "workspace_id", "requested_scope", "lifecycle_state"],
    )


def _create_memories_and_versions() -> None:
    op.create_table(
        "memories",
        _id(),
        _tenant_id(),
        sa.Column(
            "owner_user_id", _UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("workspace_id", _UUID, nullable=True),
        sa.Column("agent_version_id", _UUID, nullable=True),
        sa.Column("scope", sa.String(24), nullable=False),
        sa.Column("sensitivity", sa.String(16), nullable=False),
        sa.Column("lifecycle_state", sa.String(16), nullable=False, server_default="active"),
        sa.Column("current_version", sa.Integer(), nullable=True),
        sa.Column("created_from_candidate_id", _UUID, nullable=False),
        sa.Column("review_evidence_id", _UUID, nullable=True),
        sa.Column("deletion_effect_id", _UUID, nullable=True),
        _created_at(),
        _updated_at(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"scope IN {_SCOPES}", name="memories_scope_check"),
        sa.CheckConstraint(_scope_shape(), name="memories_scope_shape_check"),
        sa.CheckConstraint(f"sensitivity IN {_SENSITIVITY}", name="memories_sensitivity_check"),
        sa.CheckConstraint(
            "lifecycle_state IN ('active', 'blocked', 'deletion_pending', 'deleted')",
            name="memories_state_check",
        ),
        sa.CheckConstraint(
            "current_version IS NULL OR current_version >= 1",
            name="memories_current_version_check",
        ),
        sa.CheckConstraint(
            "(scope = 'controlled_shared' AND review_evidence_id IS NOT NULL) OR (scope <> 'controlled_shared' AND review_evidence_id IS NULL)",
            name="memories_review_evidence_check",
        ),
        sa.CheckConstraint(
            "(lifecycle_state IN ('active', 'blocked') AND current_version IS NOT NULL "
            "AND deletion_effect_id IS NULL AND deleted_at IS NULL) OR "
            "(lifecycle_state = 'deletion_pending' AND current_version IS NOT NULL "
            "AND deletion_effect_id IS NOT NULL AND deleted_at IS NULL) OR "
            "(lifecycle_state = 'deleted' AND current_version IS NULL "
            "AND deletion_effect_id IS NOT NULL AND deleted_at IS NOT NULL)",
            name="memories_deleted_at_check",
        ),
        _tenant_unique("memories_id_tenant_uq"),
        _workspace_fk("memories_workspace_tenant_fk"),
        _agent_version_fk("memories_agent_version_tenant_fk"),
        sa.ForeignKeyConstraint(
            ["created_from_candidate_id", "tenant_id"],
            ["memory_candidates.id", "memory_candidates.tenant_id"],
            name="memories_candidate_tenant_fk",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "memories_selection_scope_idx",
        "memories",
        [
            "tenant_id",
            "owner_user_id",
            "workspace_id",
            "agent_version_id",
            "scope",
            "lifecycle_state",
        ],
    )

    op.create_table(
        "memory_versions",
        _id(),
        _tenant_id(),
        sa.Column("memory_id", _UUID, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("content_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("content_key_version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("source_resource_id", _UUID, nullable=False),
        sa.Column("source_resource_version", sa.Integer(), nullable=False),
        sa.Column("evidence_reference_ids", _JSONB, nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        _created_at(),
        sa.CheckConstraint("version >= 1", name="memory_versions_version_check"),
        sa.CheckConstraint("content_key_version >= 1", name="memory_versions_key_version_check"),
        sa.CheckConstraint(f"content_sha256 {_SHA256}", name="memory_versions_digest_check"),
        sa.CheckConstraint(
            "source_resource_version >= 1", name="memory_versions_resource_version_check"
        ),
        sa.CheckConstraint("token_count >= 1", name="memory_versions_token_count_check"),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_reference_ids) = 'array' AND jsonb_array_length(evidence_reference_ids) >= 1",
            name="memory_versions_evidence_check",
        ),
        _tenant_unique("memory_versions_id_tenant_uq"),
        sa.UniqueConstraint(
            "memory_id", "version", "tenant_id", name="memory_versions_memory_version_uq"
        ),
        sa.ForeignKeyConstraint(
            ["memory_id", "tenant_id"],
            ["memories.id", "memories.tenant_id"],
            name="memory_versions_memory_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_resource_id", "tenant_id"],
            [
                f"{_GLOBAL_SCHEMA}.resource_registry.id",
                f"{_GLOBAL_SCHEMA}.resource_registry.tenant_id",
            ],
            name="memory_versions_resource_tenant_fk",
            ondelete="RESTRICT",
        ),
    )
    op.create_foreign_key(
        "memory_candidates_active_memory_tenant_fk",
        "memory_candidates",
        "memories",
        ["active_memory_id", "tenant_id"],
        ["id", "tenant_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "memories_current_version_tenant_fk",
        "memories",
        "memory_versions",
        ["id", "current_version", "tenant_id"],
        ["memory_id", "version", "tenant_id"],
        ondelete="NO ACTION",
        deferrable=True,
        initially="DEFERRED",
    )


def _create_review_and_capsules() -> None:
    op.create_table(
        "memory_review_evidence",
        _id(),
        _tenant_id(),
        sa.Column(
            "reviewer_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("memory_id", _UUID, nullable=False),
        sa.Column("memory_version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.CheckConstraint("memory_version >= 1", name="memory_review_version_check"),
        sa.CheckConstraint(f"content_sha256 {_SHA256}", name="memory_review_content_digest_check"),
        sa.CheckConstraint(
            f"evidence_sha256 {_SHA256}", name="memory_review_evidence_digest_check"
        ),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected', 'revoked')", name="memory_review_decision_check"
        ),
        sa.CheckConstraint("reviewed_at <= created_at", name="memory_review_time_check"),
        _tenant_unique("memory_review_evidence_id_tenant_uq"),
        _workspace_fk("memory_review_workspace_tenant_fk"),
        sa.ForeignKeyConstraint(
            ["memory_id", "tenant_id"],
            ["memories.id", "memories.tenant_id"],
            name="memory_review_memory_tenant_fk",
            ondelete="RESTRICT",
        ),
    )
    op.create_foreign_key(
        "memories_review_evidence_tenant_fk",
        "memories",
        "memory_review_evidence",
        ["review_evidence_id", "tenant_id"],
        ["id", "tenant_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_table(
        "context_capsules",
        _id(),
        _tenant_id(),
        sa.Column(
            "owner_user_id", _UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("agent_version_id", _UUID, nullable=False),
        sa.Column("task_id", _UUID, nullable=False),
        sa.Column("invocation_id", _UUID, nullable=False),
        sa.Column("memory_policy_id", _UUID, nullable=False),
        sa.Column("compiler_policy_sha256", sa.String(64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("delegable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trusted_instructions", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sensitivity_summary", _JSONB, nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            f"compiler_policy_sha256 {_SHA256}", name="context_capsules_policy_digest_check"
        ),
        sa.CheckConstraint(
            f"content_sha256 {_SHA256}", name="context_capsules_content_digest_check"
        ),
        sa.CheckConstraint(
            "expires_at > issued_at AND expires_at - issued_at <= interval '1 day'",
            name="context_capsules_ttl_check",
        ),
        sa.CheckConstraint(
            "max_tokens >= 1 AND total_tokens BETWEEN 1 AND max_tokens",
            name="context_capsules_tokens_check",
        ),
        sa.CheckConstraint(
            "delegable = false AND trusted_instructions = false",
            name="context_capsules_untrusted_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(sensitivity_summary) = 'object'", name="context_capsules_summary_check"
        ),
        _tenant_unique("context_capsules_id_tenant_uq"),
        _workspace_fk("context_capsules_workspace_tenant_fk"),
        _agent_version_fk("context_capsules_agent_version_tenant_fk"),
        _task_fk("context_capsules_task_tenant_fk"),
        sa.UniqueConstraint(
            "tenant_id",
            "task_id",
            "invocation_id",
            "content_sha256",
            name="context_capsules_invocation_digest_uq",
        ),
    )

    op.create_table(
        "context_capsule_items",
        _id(),
        _tenant_id(),
        sa.Column("capsule_id", _UUID, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("memory_id", _UUID, nullable=False),
        sa.Column("memory_version", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(24), nullable=False),
        sa.Column(
            "owner_user_id", _UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("workspace_id", _UUID, nullable=True),
        sa.Column("agent_version_id", _UUID, nullable=True),
        sa.Column("review_evidence_id", _UUID, nullable=True),
        sa.Column("review_evidence_sha256", sa.String(64), nullable=True),
        sa.Column("source_resource_id", _UUID, nullable=False),
        sa.Column("source_resource_version", sa.Integer(), nullable=False),
        sa.Column("evidence_reference_ids", _JSONB, nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("selection_reason", sa.String(24), nullable=False),
        sa.Column("sensitivity", sa.String(16), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        _created_at(),
        sa.CheckConstraint("position >= 1", name="context_capsule_items_position_check"),
        sa.CheckConstraint(
            "memory_version >= 1 AND source_resource_version >= 1",
            name="context_capsule_items_version_check",
        ),
        sa.CheckConstraint(f"scope IN {_SCOPES}", name="context_capsule_items_scope_check"),
        sa.CheckConstraint(_scope_shape(), name="context_capsule_items_scope_shape_check"),
        sa.CheckConstraint(
            f"sensitivity IN {_SENSITIVITY}", name="context_capsule_items_sensitivity_check"
        ),
        sa.CheckConstraint(
            "selection_reason IN ('explicit_user', 'current_task', 'pinned', 'semantic_match', 'workspace_policy')",
            name="context_capsule_items_reason_check",
        ),
        sa.CheckConstraint(f"content_sha256 {_SHA256}", name="context_capsule_items_digest_check"),
        sa.CheckConstraint(
            "review_evidence_sha256 IS NULL OR review_evidence_sha256 ~ '^[0-9a-f]{64}$'",
            name="context_capsule_items_review_digest_check",
        ),
        sa.CheckConstraint(
            "(scope = 'controlled_shared' AND review_evidence_id IS NOT NULL AND review_evidence_sha256 IS NOT NULL) OR (scope <> 'controlled_shared' AND review_evidence_id IS NULL AND review_evidence_sha256 IS NULL)",
            name="context_capsule_items_review_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_reference_ids) = 'array' AND jsonb_array_length(evidence_reference_ids) >= 1",
            name="context_capsule_items_evidence_check",
        ),
        sa.CheckConstraint("token_count >= 1", name="context_capsule_items_token_check"),
        _tenant_unique("context_capsule_items_id_tenant_uq"),
        sa.UniqueConstraint(
            "capsule_id", "position", "tenant_id", name="context_capsule_items_position_uq"
        ),
        sa.UniqueConstraint(
            "capsule_id",
            "memory_id",
            "memory_version",
            "tenant_id",
            name="context_capsule_items_memory_uq",
        ),
        sa.ForeignKeyConstraint(
            ["capsule_id", "tenant_id"],
            ["context_capsules.id", "context_capsules.tenant_id"],
            name="context_capsule_items_capsule_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["memory_id", "memory_version", "tenant_id"],
            ["memory_versions.memory_id", "memory_versions.version", "memory_versions.tenant_id"],
            name="context_capsule_items_memory_version_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["review_evidence_id", "tenant_id"],
            ["memory_review_evidence.id", "memory_review_evidence.tenant_id"],
            name="context_capsule_items_review_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_resource_id", "tenant_id"],
            [
                f"{_GLOBAL_SCHEMA}.resource_registry.id",
                f"{_GLOBAL_SCHEMA}.resource_registry.tenant_id",
            ],
            name="context_capsule_items_resource_tenant_fk",
            ondelete="RESTRICT",
        ),
    )
    op.create_foreign_key(
        "memory_candidates_source_capsule_tenant_fk",
        "memory_candidates",
        "context_capsules",
        ["source_capsule_id", "tenant_id"],
        ["id", "tenant_id"],
        ondelete="RESTRICT",
    )


def _create_effects_and_tombstones() -> None:
    op.create_table(
        "memory_effects",
        _id(),
        _tenant_id(),
        sa.Column(
            "owner_user_id", _UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("workspace_id", _UUID, nullable=True),
        sa.Column("operation_id", _UUID, nullable=False),
        sa.Column("memory_id", _UUID, nullable=True),
        sa.Column("candidate_id", _UUID, nullable=True),
        sa.Column("effect_kind", sa.String(24), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("result_sha256", sa.String(64), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "effect_kind IN ('candidate_create', 'publish', 'delete', 'export', 'capsule_compile')",
            name="memory_effects_kind_check",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'committed', 'failed', 'unknown')",
            name="memory_effects_state_check",
        ),
        sa.CheckConstraint(f"request_sha256 {_SHA256}", name="memory_effects_request_digest_check"),
        sa.CheckConstraint(
            "result_sha256 IS NULL OR result_sha256 ~ '^[0-9a-f]{64}$'",
            name="memory_effects_result_digest_check",
        ),
        sa.CheckConstraint(
            "(state = 'committed' AND result_sha256 IS NOT NULL) OR (state <> 'committed' AND result_sha256 IS NULL)",
            name="memory_effects_result_state_check",
        ),
        sa.CheckConstraint(
            "memory_id IS NOT NULL OR candidate_id IS NOT NULL", name="memory_effects_subject_check"
        ),
        _tenant_unique("memory_effects_id_tenant_uq"),
        _workspace_fk("memory_effects_workspace_tenant_fk"),
        sa.ForeignKeyConstraint(
            ["operation_id", "tenant_id"],
            [f"{_GLOBAL_SCHEMA}.operations.id", f"{_GLOBAL_SCHEMA}.operations.tenant_id"],
            name="memory_effects_operation_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["memory_id", "tenant_id"],
            ["memories.id", "memories.tenant_id"],
            name="memory_effects_memory_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id", "tenant_id"],
            ["memory_candidates.id", "memory_candidates.tenant_id"],
            name="memory_effects_candidate_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "operation_id", "request_sha256", "tenant_id", name="memory_effects_request_uq"
        ),
    )
    op.create_foreign_key(
        "memories_deletion_effect_tenant_fk",
        "memories",
        "memory_effects",
        ["deletion_effect_id", "tenant_id"],
        ["id", "tenant_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "memory_tombstones",
        _id(),
        _tenant_id(),
        sa.Column("memory_id", _UUID, nullable=False),
        sa.Column("last_memory_version", sa.Integer(), nullable=False),
        sa.Column(
            "deleted_by_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "owner_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("workspace_id", _UUID, nullable=True),
        sa.Column("deletion_effect_id", _UUID, nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("result_sha256", sa.String(64), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("deletion_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.CheckConstraint("last_memory_version >= 1", name="memory_tombstones_version_check"),
        sa.CheckConstraint(
            "reason_code ~ '^[a-z][a-z0-9_]{2,63}$'", name="memory_tombstones_reason_check"
        ),
        sa.CheckConstraint(f"deletion_sha256 {_SHA256}", name="memory_tombstones_digest_check"),
        sa.CheckConstraint(f"request_sha256 {_SHA256}", name="memory_tombstones_request_check"),
        sa.CheckConstraint(f"result_sha256 {_SHA256}", name="memory_tombstones_result_check"),
        sa.CheckConstraint(
            "state IN ('pending', 'completed')", name="memory_tombstones_state_check"
        ),
        sa.CheckConstraint(
            "(state = 'pending' AND completed_at IS NULL) OR "
            "(state = 'completed' AND completed_at IS NOT NULL)",
            name="memory_tombstones_completion_check",
        ),
        _tenant_unique("memory_tombstones_id_tenant_uq"),
        sa.UniqueConstraint("memory_id", "tenant_id", name="memory_tombstones_memory_uq"),
        _workspace_fk("memory_tombstones_workspace_tenant_fk"),
        sa.ForeignKeyConstraint(
            ["memory_id", "tenant_id"],
            ["memories.id", "memories.tenant_id"],
            name="memory_tombstones_memory_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deletion_effect_id", "tenant_id"],
            ["memory_effects.id", "memory_effects.tenant_id"],
            name="memory_tombstones_effect_tenant_fk",
            ondelete="RESTRICT",
        ),
    )


def _create_embedding_lane(table: str, dimension: int) -> None:
    op.create_table(
        table,
        _id(),
        _tenant_id(),
        sa.Column("memory_id", _UUID, nullable=False),
        sa.Column("memory_version", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(24), nullable=False),
        sa.Column("workspace_id", _UUID, nullable=True),
        sa.Column("agent_version_id", _UUID, nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("embedding_model_id", sa.String(96), nullable=False),
        sa.Column("embedding_model_sha256", sa.String(64), nullable=False),
        sa.Column("embedding", Vector(dimension), nullable=False),
        _created_at(),
        sa.CheckConstraint(f"scope IN {_SCOPES}", name=f"{table}_scope_check"),
        sa.CheckConstraint(_scope_shape(), name=f"{table}_scope_shape_check"),
        sa.CheckConstraint(f"content_sha256 {_SHA256}", name=f"{table}_content_digest_check"),
        sa.CheckConstraint(f"embedding_model_sha256 {_SHA256}", name=f"{table}_model_digest_check"),
        _tenant_unique(f"{table}_id_tenant_uq"),
        sa.UniqueConstraint(
            "memory_id",
            "memory_version",
            "tenant_id",
            "embedding_model_sha256",
            name=f"{table}_memory_model_uq",
        ),
        sa.ForeignKeyConstraint(
            ["memory_id", "memory_version", "tenant_id"],
            ["memory_versions.memory_id", "memory_versions.version", "memory_versions.tenant_id"],
            name=f"{table}_memory_version_fk",
            ondelete="RESTRICT",
        ),
        _workspace_fk(f"{table}_workspace_tenant_fk"),
        _agent_version_fk(f"{table}_agent_version_tenant_fk"),
    )
    op.create_index(
        f"{table}_scope_idx", table, ["tenant_id", "workspace_id", "agent_version_id", "scope"]
    )
    op.create_index(
        f"{table}_embedding_hnsw_idx",
        table,
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_with={"m": 16, "ef_construction": 64},
    )


def _install_tenant_triggers() -> None:
    tables = ", ".join(f"'{name}'" for name in _MEMORY_TABLES)
    trigger_sql = """
            CREATE OR REPLACE FUNCTION memory_assert_tenant_schema_binding()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM omnibase_meta.tenants t
                     WHERE t.id = NEW.tenant_id AND t.schema_name = TG_TABLE_SCHEMA
                ) THEN
                    RAISE EXCEPTION 'memory tenant_id does not match current tenant schema'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END;
            $$;

            DO $$
            DECLARE table_name text;
            BEGIN
                FOREACH table_name IN ARRAY ARRAY[__MEMORY_TABLES__]
                LOOP
                    EXECUTE format(
                        'CREATE TRIGGER %I BEFORE INSERT OR UPDATE ON %I '
                        'FOR EACH ROW EXECUTE FUNCTION memory_assert_tenant_schema_binding()',
                        table_name || '_tenant_schema_guard', table_name
                    );
                END LOOP;
            END;
            $$;

            CREATE OR REPLACE FUNCTION memory_exact_pending_delete(
                p_memory_id uuid,
                p_tenant_id uuid
            ) RETURNS boolean LANGUAGE sql STABLE AS $$
                SELECT EXISTS (
                    SELECT 1
                      FROM memories m
                      JOIN memory_tombstones t
                        ON t.memory_id = m.id AND t.tenant_id = m.tenant_id
                      JOIN memory_effects e
                        ON e.id = t.deletion_effect_id AND e.tenant_id = t.tenant_id
                     WHERE m.id = p_memory_id AND m.tenant_id = p_tenant_id
                       AND m.lifecycle_state = 'deletion_pending'
                       AND m.deletion_effect_id = t.deletion_effect_id
                       AND t.state = 'pending'
                       AND t.owner_user_id = m.owner_user_id
                       AND t.workspace_id IS NOT DISTINCT FROM m.workspace_id
                       AND e.memory_id = m.id
                       AND e.owner_user_id = m.owner_user_id
                       AND e.workspace_id IS NOT DISTINCT FROM m.workspace_id
                       AND e.effect_kind = 'delete'
                       AND e.state = 'committed'
                       AND e.request_sha256 = t.request_sha256
                       AND e.result_sha256 = t.result_sha256
                       AND t.deletion_sha256 = e.result_sha256
                );
            $$;

            CREATE OR REPLACE FUNCTION memory_candidate_guard()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE source_capsule context_capsules%ROWTYPE;
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM omnibase_meta.agent_tasks t
                     WHERE t.id = NEW.task_id AND t.tenant_id = NEW.tenant_id
                       AND t.workspace_id = NEW.workspace_id
                       AND t.agent_version_id = NEW.agent_version_id
                       AND t.actor_user_id = NEW.owner_user_id
                ) THEN
                    RAISE EXCEPTION 'memory candidate task identity binding drifted'
                        USING ERRCODE = '55000';
                END IF;
                SELECT * INTO source_capsule
                  FROM context_capsules capsule
                 WHERE capsule.id = NEW.source_capsule_id
                   AND capsule.tenant_id = NEW.tenant_id;
                IF NOT FOUND
                   OR source_capsule.owner_user_id IS DISTINCT FROM NEW.owner_user_id
                   OR source_capsule.workspace_id IS DISTINCT FROM NEW.workspace_id
                   OR source_capsule.agent_version_id IS DISTINCT FROM NEW.agent_version_id
                   OR source_capsule.task_id IS DISTINCT FROM NEW.task_id
                   OR source_capsule.invocation_id IS DISTINCT FROM NEW.invocation_id
                   OR source_capsule.memory_policy_id IS DISTINCT FROM NEW.memory_policy_id THEN
                    RAISE EXCEPTION 'memory candidate source capsule binding drifted'
                        USING ERRCODE = '55000';
                END IF;
                IF TG_OP = 'INSERT' THEN
                    IF NEW.lifecycle_state <> 'candidate'
                       OR NEW.active_memory_id IS NOT NULL
                       OR NEW.acceptance_operation_id IS NOT NULL
                       OR NEW.acceptance_approval_id IS NOT NULL
                       OR NEW.confirmed_by_user_id IS NOT NULL
                       OR NEW.confirmed_at IS NOT NULL
                       OR NEW.confirmation_sha256 IS NOT NULL
                       OR NEW.content_ciphertext IS NULL
                       OR NEW.content_nonce IS NULL THEN
                        RAISE EXCEPTION 'memory candidate insert must begin unaccepted'
                            USING ERRCODE = '55000';
                    END IF;
                END IF;
                IF TG_OP = 'UPDATE' THEN
                    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                       OR NEW.owner_user_id IS DISTINCT FROM OLD.owner_user_id
                       OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
                       OR NEW.agent_version_id IS DISTINCT FROM OLD.agent_version_id
                       OR NEW.task_id IS DISTINCT FROM OLD.task_id
                       OR NEW.invocation_id IS DISTINCT FROM OLD.invocation_id
                       OR NEW.source_capsule_id IS DISTINCT FROM OLD.source_capsule_id
                       OR NEW.memory_policy_id IS DISTINCT FROM OLD.memory_policy_id
                       OR NEW.requested_scope IS DISTINCT FROM OLD.requested_scope
                       OR NEW.sensitivity IS DISTINCT FROM OLD.sensitivity
                       OR NEW.content_key_version IS DISTINCT FROM OLD.content_key_version
                       OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256
                       OR NEW.source_resource_id IS DISTINCT FROM OLD.source_resource_id
                       OR NEW.source_resource_version IS DISTINCT FROM OLD.source_resource_version
                       OR NEW.evidence_reference_ids IS DISTINCT FROM OLD.evidence_reference_ids
                       OR NEW.confidence_millis IS DISTINCT FROM OLD.confidence_millis
                       OR NEW.retention_days IS DISTINCT FROM OLD.retention_days
                       OR NEW.requires_user_confirmation IS DISTINCT FROM OLD.requires_user_confirmation
                       OR NEW.contains_secret IS DISTINCT FROM OLD.contains_secret
                       OR NEW.inferred_sensitive_categories IS DISTINCT FROM OLD.inferred_sensitive_categories
                       OR NEW.candidate_created_by IS DISTINCT FROM OLD.candidate_created_by
                       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                        RAISE EXCEPTION 'memory candidate immutable binding changed'
                            USING ERRCODE = '55000';
                    END IF;
                    IF NEW.content_ciphertext IS DISTINCT FROM OLD.content_ciphertext
                       OR NEW.content_nonce IS DISTINCT FROM OLD.content_nonce THEN
                        IF OLD.lifecycle_state <> 'accepted'
                           OR OLD.content_ciphertext IS NULL OR OLD.content_nonce IS NULL
                           OR NEW.content_ciphertext IS NOT NULL OR NEW.content_nonce IS NOT NULL
                           OR OLD.active_memory_id IS NULL
                           OR NOT memory_exact_pending_delete(OLD.active_memory_id, OLD.tenant_id) THEN
                            RAISE EXCEPTION 'memory candidate payload is immutable outside exact crypto-erasure'
                                USING ERRCODE = '55000';
                        END IF;
                    END IF;
                    IF NEW.lifecycle_state <> OLD.lifecycle_state AND NOT (
                        (OLD.lifecycle_state = 'candidate' AND NEW.lifecycle_state IN ('awaiting_confirmation', 'accepted', 'rejected', 'superseded')) OR
                        (OLD.lifecycle_state = 'awaiting_confirmation' AND NEW.lifecycle_state IN ('accepted', 'rejected', 'superseded'))
                    ) THEN
                        RAISE EXCEPTION 'invalid memory candidate transition' USING ERRCODE = '55000';
                    END IF;
                    IF NEW.acceptance_operation_id IS DISTINCT FROM OLD.acceptance_operation_id
                       OR NEW.acceptance_approval_id IS DISTINCT FROM OLD.acceptance_approval_id
                       OR NEW.confirmed_by_user_id IS DISTINCT FROM OLD.confirmed_by_user_id
                       OR NEW.confirmed_at IS DISTINCT FROM OLD.confirmed_at
                       OR NEW.confirmation_sha256 IS DISTINCT FROM OLD.confirmation_sha256
                       OR NEW.active_memory_id IS DISTINCT FROM OLD.active_memory_id THEN
                        IF OLD.lifecycle_state NOT IN ('candidate', 'awaiting_confirmation')
                           OR NEW.lifecycle_state <> 'accepted' THEN
                            RAISE EXCEPTION 'memory candidate acceptance binding is immutable'
                                USING ERRCODE = '55000';
                        END IF;
                    END IF;
                    NEW.updated_at := clock_timestamp();
                END IF;
                IF NEW.lifecycle_state = 'accepted' THEN
                    IF TG_OP <> 'UPDATE'
                       OR OLD.lifecycle_state NOT IN ('candidate', 'awaiting_confirmation')
                          AND NEW.content_ciphertext IS NOT NULL THEN
                        RAISE EXCEPTION 'memory candidate acceptance requires an explicit update'
                            USING ERRCODE = '55000';
                    END IF;
                    IF NEW.confirmed_by_user_id IS DISTINCT FROM NEW.owner_user_id THEN
                        RAISE EXCEPTION 'memory candidate confirmation must be performed by Owner'
                            USING ERRCODE = '55000';
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM users owner
                         WHERE owner.id = NEW.owner_user_id AND owner.is_active IS TRUE
                    ) THEN
                        RAISE EXCEPTION 'memory candidate confirmation requires active Owner'
                            USING ERRCODE = '55000';
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1
                          FROM omnibase_meta.operations operation
                          JOIN omnibase_meta.agent_tasks task
                            ON task.id = NEW.task_id
                           AND task.tenant_id = NEW.tenant_id
                         WHERE operation.id = NEW.acceptance_operation_id
                           AND operation.tenant_id = NEW.tenant_id
                           AND operation.workspace_id = NEW.workspace_id
                           AND operation.actor_type = 'agent'
                           AND operation.actor_id = task.agent_definition_id
                           AND operation.resource_id = NEW.source_resource_id
                           AND operation.resource_version = NEW.source_resource_version
                           AND operation.approval_id = NEW.acceptance_approval_id
                           AND operation.kind = 'memory.candidate.accept'
                           AND operation.state = 'succeeded'
                           AND operation.request_hash = NEW.confirmation_sha256
                    ) THEN
                        RAISE EXCEPTION 'memory candidate acceptance operation binding drifted'
                            USING ERRCODE = '55000';
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1
                          FROM omnibase_meta.approval_requests approval
                          JOIN omnibase_meta.agent_tasks task
                            ON task.id = NEW.task_id
                           AND task.tenant_id = NEW.tenant_id
                         WHERE approval.id = NEW.acceptance_approval_id
                           AND approval.tenant_id = NEW.tenant_id
                           AND approval.workspace_id = NEW.workspace_id
                           AND approval.operation_id = NEW.acceptance_operation_id
                           AND approval.requester_type = 'agent'
                           AND approval.requester_id = task.agent_definition_id
                           AND approval.resource_id = NEW.source_resource_id
                           AND approval.resource_version = NEW.source_resource_version
                           AND approval.action = 'memory.candidate.accept'
                           AND approval.state = 'consumed'
                           AND approval.decided_by_actor_type = 'user'
                           AND approval.decided_by_actor_id = NEW.owner_user_id
                           AND approval.consumed_at IS NOT NULL
                           AND approval.request_hash = NEW.confirmation_sha256
                           AND approval.decided_at <= NEW.confirmed_at
                           AND approval.consumed_at <= NEW.confirmed_at
                    ) THEN
                        RAISE EXCEPTION 'memory candidate acceptance approval binding drifted'
                            USING ERRCODE = '55000';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$;
            CREATE TRIGGER memory_candidates_state_guard
            BEFORE INSERT OR UPDATE ON memory_candidates
            FOR EACH ROW EXECUTE FUNCTION memory_candidate_guard();

            CREATE OR REPLACE FUNCTION memories_state_guard()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'memory identity cannot be deleted; use a tombstone'
                        USING ERRCODE = '55000';
                END IF;
                IF TG_OP = 'INSERT' THEN
                    IF NEW.lifecycle_state <> 'active' OR NEW.current_version <> 1
                       OR NEW.deletion_effect_id IS NOT NULL OR NEW.deleted_at IS NOT NULL THEN
                        RAISE EXCEPTION 'memory insert must begin active at version one'
                            USING ERRCODE = '55000';
                    END IF;
                    RETURN NEW;
                END IF;
                IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                   OR NEW.owner_user_id IS DISTINCT FROM OLD.owner_user_id
                   OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
                   OR NEW.agent_version_id IS DISTINCT FROM OLD.agent_version_id
                   OR NEW.scope IS DISTINCT FROM OLD.scope
                   OR NEW.created_from_candidate_id IS DISTINCT FROM OLD.created_from_candidate_id
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION 'memory immutable binding changed' USING ERRCODE = '55000';
                END IF;
                IF NEW.lifecycle_state = 'deleted' THEN
                    IF NEW.current_version IS NOT NULL THEN
                        RAISE EXCEPTION 'deleted memory must clear current version'
                            USING ERRCODE = '55000';
                    END IF;
                ELSIF NEW.current_version IS NULL OR OLD.current_version IS NULL
                   OR NEW.current_version < OLD.current_version
                   OR NEW.current_version > OLD.current_version + 1 THEN
                    RAISE EXCEPTION 'memory version must advance exactly by one'
                        USING ERRCODE = '55000';
                END IF;
                IF NEW.review_evidence_id IS DISTINCT FROM OLD.review_evidence_id
                   AND NOT (NEW.scope = 'controlled_shared'
                            AND NEW.current_version = OLD.current_version + 1) THEN
                    RAISE EXCEPTION 'memory review evidence may change only with controlled version advance'
                        USING ERRCODE = '55000';
                END IF;
                IF NEW.deletion_effect_id IS DISTINCT FROM OLD.deletion_effect_id
                   AND NOT (OLD.lifecycle_state IN ('active', 'blocked')
                            AND NEW.lifecycle_state = 'deletion_pending'
                            AND OLD.deletion_effect_id IS NULL
                            AND NEW.deletion_effect_id IS NOT NULL) THEN
                    RAISE EXCEPTION 'memory deletion effect binding is immutable'
                        USING ERRCODE = '55000';
                END IF;
                IF NEW.lifecycle_state <> OLD.lifecycle_state AND NOT (
                    (OLD.lifecycle_state = 'active' AND NEW.lifecycle_state IN ('blocked', 'deletion_pending')) OR
                    (OLD.lifecycle_state = 'blocked' AND NEW.lifecycle_state = 'deletion_pending') OR
                    (OLD.lifecycle_state = 'deletion_pending' AND NEW.lifecycle_state = 'deleted')
                ) THEN
                    RAISE EXCEPTION 'invalid memory lifecycle transition' USING ERRCODE = '55000';
                END IF;
                IF NEW.lifecycle_state = 'deletion_pending'
                   AND OLD.lifecycle_state IN ('active', 'blocked')
                   AND NOT EXISTS (
                       SELECT 1 FROM memory_effects effect
                        WHERE effect.id = NEW.deletion_effect_id
                          AND effect.tenant_id = NEW.tenant_id
                          AND effect.memory_id = NEW.id
                          AND effect.owner_user_id = NEW.owner_user_id
                          AND effect.workspace_id IS NOT DISTINCT FROM NEW.workspace_id
                          AND effect.effect_kind = 'delete'
                          AND effect.state = 'committed'
                          AND effect.result_sha256 IS NOT NULL
                   ) THEN
                    RAISE EXCEPTION 'memory deletion pending requires exact committed delete effect'
                        USING ERRCODE = '55000';
                END IF;
                IF NEW.lifecycle_state = 'deleted' AND NOT EXISTS (
                    SELECT 1 FROM memory_tombstones tombstone
                     WHERE tombstone.memory_id = NEW.id
                       AND tombstone.tenant_id = NEW.tenant_id
                       AND tombstone.deletion_effect_id = NEW.deletion_effect_id
                       AND tombstone.owner_user_id = NEW.owner_user_id
                       AND tombstone.workspace_id IS NOT DISTINCT FROM NEW.workspace_id
                       AND tombstone.state = 'completed'
                       AND tombstone.completed_at IS NOT NULL
                       AND tombstone.completed_at <= NEW.deleted_at
                ) THEN
                    RAISE EXCEPTION 'memory deletion requires a completed exact tombstone'
                        USING ERRCODE = '55000';
                END IF;
                NEW.updated_at := clock_timestamp();
                RETURN NEW;
            END;
            $$;
            CREATE TRIGGER memories_state_guard
            BEFORE INSERT OR UPDATE OR DELETE ON memories
            FOR EACH ROW EXECUTE FUNCTION memories_state_guard();

            CREATE OR REPLACE FUNCTION memory_candidate_publication_binding_guard()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE
                candidate memory_candidates%ROWTYPE;
                memory memories%ROWTYPE;
                initial_version memory_versions%ROWTYPE;
                current_version memory_versions%ROWTYPE;
                review memory_review_evidence%ROWTYPE;
            BEGIN
                IF TG_TABLE_NAME = 'memory_candidates' THEN
                    candidate := NEW;
                    IF candidate.lifecycle_state <> 'accepted' THEN
                        RETURN NULL;
                    END IF;
                    SELECT * INTO memory FROM memories
                     WHERE id = candidate.active_memory_id AND tenant_id = candidate.tenant_id;
                ELSE
                    memory := NEW;
                    SELECT * INTO candidate FROM memory_candidates
                     WHERE id = memory.created_from_candidate_id AND tenant_id = memory.tenant_id;
                END IF;
                IF memory.lifecycle_state IN ('deletion_pending', 'deleted') THEN
                    RETURN NULL;
                END IF;
                IF NOT FOUND OR candidate.lifecycle_state <> 'accepted'
                   OR candidate.tenant_id IS DISTINCT FROM memory.tenant_id
                   OR candidate.owner_user_id IS DISTINCT FROM memory.owner_user_id
                   OR candidate.requested_scope IS DISTINCT FROM memory.scope
                   OR candidate.active_memory_id IS DISTINCT FROM memory.id
                   OR memory.created_from_candidate_id IS DISTINCT FROM candidate.id
                   OR (memory.scope = 'user_private' AND memory.workspace_id IS NOT NULL)
                   OR (memory.scope <> 'user_private'
                       AND candidate.workspace_id IS DISTINCT FROM memory.workspace_id)
                   OR (memory.scope = 'agent_private'
                       AND candidate.agent_version_id IS DISTINCT FROM memory.agent_version_id)
                   OR (memory.scope <> 'agent_private' AND memory.agent_version_id IS NOT NULL) THEN
                    RAISE EXCEPTION 'memory candidate publication identity binding drifted'
                        USING ERRCODE = '55000';
                END IF;
                SELECT * INTO initial_version FROM memory_versions
                 WHERE memory_id = memory.id AND version = 1 AND tenant_id = memory.tenant_id;
                IF NOT FOUND
                   OR candidate.content_sha256 IS DISTINCT FROM initial_version.content_sha256
                   OR candidate.source_resource_id IS DISTINCT FROM initial_version.source_resource_id
                   OR candidate.source_resource_version IS DISTINCT FROM initial_version.source_resource_version
                   OR (TG_TABLE_NAME = 'memories' AND TG_OP = 'INSERT'
                       AND memory.current_version <> 1) THEN
                    RAISE EXCEPTION 'memory candidate publication version binding drifted'
                        USING ERRCODE = '55000';
                END IF;
                IF memory.scope = 'controlled_shared' THEN
                    SELECT * INTO current_version FROM memory_versions
                     WHERE memory_id = memory.id AND version = memory.current_version
                       AND tenant_id = memory.tenant_id;
                    IF NOT FOUND THEN
                        RAISE EXCEPTION 'controlled-shared current memory version is missing'
                            USING ERRCODE = '55000';
                    END IF;
                    SELECT * INTO review FROM memory_review_evidence
                     WHERE id = memory.review_evidence_id AND tenant_id = memory.tenant_id;
                    IF NOT FOUND OR review.decision <> 'approved'
                       OR review.reviewer_user_id IS DISTINCT FROM memory.owner_user_id
                       OR review.workspace_id IS DISTINCT FROM memory.workspace_id
                       OR review.memory_id IS DISTINCT FROM memory.id
                       OR review.memory_version IS DISTINCT FROM memory.current_version
                       OR review.content_sha256 IS DISTINCT FROM current_version.content_sha256
                       OR review.reviewed_at < current_version.created_at
                       OR NOT EXISTS (
                           SELECT 1 FROM users owner
                            WHERE owner.id = memory.owner_user_id AND owner.is_active IS TRUE
                       ) THEN
                        RAISE EXCEPTION 'controlled-shared memory review binding drifted'
                            USING ERRCODE = '55000';
                    END IF;
                END IF;
                RETURN NULL;
            END;
            $$;
            CREATE CONSTRAINT TRIGGER memory_candidates_publication_binding
            AFTER INSERT OR UPDATE ON memory_candidates
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION memory_candidate_publication_binding_guard();
            CREATE CONSTRAINT TRIGGER memories_candidate_publication_binding
            AFTER INSERT OR UPDATE ON memories
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION memory_candidate_publication_binding_guard();

            CREATE OR REPLACE FUNCTION memory_append_only_guard()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'memory evidence record is append-only' USING ERRCODE = '55000';
            END;
            $$;
            CREATE TRIGGER memory_review_evidence_append_only BEFORE UPDATE OR DELETE ON memory_review_evidence FOR EACH ROW EXECUTE FUNCTION memory_append_only_guard();
            CREATE TRIGGER context_capsules_append_only BEFORE UPDATE OR DELETE ON context_capsules FOR EACH ROW EXECUTE FUNCTION memory_append_only_guard();
            CREATE TRIGGER context_capsule_items_append_only BEFORE UPDATE OR DELETE ON context_capsule_items FOR EACH ROW EXECUTE FUNCTION memory_append_only_guard();

            CREATE OR REPLACE FUNCTION memory_review_evidence_insert_guard()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE memory memories%ROWTYPE; version memory_versions%ROWTYPE;
            BEGIN
                SELECT * INTO memory FROM memories stored_memory
                 WHERE stored_memory.id = NEW.memory_id
                   AND stored_memory.tenant_id = NEW.tenant_id;
                SELECT * INTO version FROM memory_versions stored_version
                 WHERE stored_version.memory_id = NEW.memory_id
                   AND stored_version.version = NEW.memory_version
                   AND stored_version.tenant_id = NEW.tenant_id;
                IF memory.id IS NULL OR version.id IS NULL
                   OR memory.owner_user_id IS DISTINCT FROM NEW.reviewer_user_id
                   OR memory.workspace_id IS DISTINCT FROM NEW.workspace_id
                   OR version.content_sha256 IS DISTINCT FROM NEW.content_sha256
                   OR NEW.reviewed_at < version.created_at
                   OR NOT EXISTS (
                       SELECT 1 FROM users owner
                        WHERE owner.id = NEW.reviewer_user_id
                          AND owner.is_active IS TRUE
                   ) THEN
                    RAISE EXCEPTION 'memory review evidence binding drifted'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END;
            $$;
            CREATE TRIGGER memory_review_evidence_insert_binding
            BEFORE INSERT ON memory_review_evidence
            FOR EACH ROW EXECUTE FUNCTION memory_review_evidence_insert_guard();

            CREATE OR REPLACE FUNCTION memory_tombstone_guard()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE memory memories%ROWTYPE; effect memory_effects%ROWTYPE;
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'memory tombstone identity is append-only'
                        USING ERRCODE = '55000';
                END IF;
                IF TG_OP = 'INSERT' THEN
                    IF NEW.state <> 'pending' OR NEW.completed_at IS NOT NULL THEN
                        RAISE EXCEPTION 'memory tombstone must begin pending'
                            USING ERRCODE = '55000';
                    END IF;
                    SELECT * INTO memory FROM memories
                     WHERE id = NEW.memory_id AND tenant_id = NEW.tenant_id;
                    IF NOT FOUND THEN
                        RAISE EXCEPTION 'memory tombstone target is missing'
                            USING ERRCODE = '55000';
                    END IF;
                    SELECT * INTO effect FROM memory_effects
                     WHERE id = NEW.deletion_effect_id AND tenant_id = NEW.tenant_id;
                    IF NOT FOUND OR memory.lifecycle_state <> 'deletion_pending'
                       OR memory.owner_user_id IS DISTINCT FROM NEW.owner_user_id
                       OR memory.workspace_id IS DISTINCT FROM NEW.workspace_id
                       OR memory.current_version IS DISTINCT FROM NEW.last_memory_version
                       OR memory.deletion_effect_id IS DISTINCT FROM NEW.deletion_effect_id
                       OR NEW.deleted_by_user_id IS DISTINCT FROM memory.owner_user_id
                       OR effect.memory_id IS DISTINCT FROM memory.id
                       OR effect.owner_user_id IS DISTINCT FROM memory.owner_user_id
                       OR effect.workspace_id IS DISTINCT FROM memory.workspace_id
                       OR effect.effect_kind <> 'delete'
                       OR effect.state <> 'committed'
                       OR effect.request_sha256 IS DISTINCT FROM NEW.request_sha256
                       OR effect.result_sha256 IS DISTINCT FROM NEW.result_sha256
                       OR NEW.deletion_sha256 IS DISTINCT FROM effect.result_sha256 THEN
                        RAISE EXCEPTION 'memory tombstone deletion binding drifted'
                            USING ERRCODE = '55000';
                    END IF;
                    RETURN NEW;
                END IF;
                IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                   OR NEW.memory_id IS DISTINCT FROM OLD.memory_id
                   OR NEW.last_memory_version IS DISTINCT FROM OLD.last_memory_version
                   OR NEW.deleted_by_user_id IS DISTINCT FROM OLD.deleted_by_user_id
                   OR NEW.owner_user_id IS DISTINCT FROM OLD.owner_user_id
                   OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
                   OR NEW.deletion_effect_id IS DISTINCT FROM OLD.deletion_effect_id
                   OR NEW.request_sha256 IS DISTINCT FROM OLD.request_sha256
                   OR NEW.result_sha256 IS DISTINCT FROM OLD.result_sha256
                   OR NEW.reason_code IS DISTINCT FROM OLD.reason_code
                   OR NEW.deletion_sha256 IS DISTINCT FROM OLD.deletion_sha256
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION 'memory tombstone identity is immutable'
                        USING ERRCODE = '55000';
                END IF;
                IF OLD.state <> 'pending' OR NEW.state <> 'completed'
                   OR OLD.completed_at IS NOT NULL OR NEW.completed_at IS NULL THEN
                    RAISE EXCEPTION 'invalid memory tombstone transition'
                        USING ERRCODE = '55000';
                END IF;
                IF NOT memory_exact_pending_delete(OLD.memory_id, OLD.tenant_id) THEN
                    RAISE EXCEPTION 'memory tombstone completion lost exact delete binding'
                        USING ERRCODE = '55000';
                END IF;
                IF EXISTS (
                    SELECT 1 FROM memory_versions stored_version
                     WHERE stored_version.memory_id = NEW.memory_id
                       AND stored_version.tenant_id = NEW.tenant_id
                ) OR EXISTS (
                    SELECT 1
                      FROM memories stored_memory
                      JOIN memory_candidates candidate
                        ON candidate.id = stored_memory.created_from_candidate_id
                       AND candidate.tenant_id = stored_memory.tenant_id
                     WHERE stored_memory.id = NEW.memory_id
                       AND stored_memory.tenant_id = NEW.tenant_id
                       AND candidate.lifecycle_state = 'accepted'
                       AND (candidate.content_ciphertext IS NOT NULL
                            OR candidate.content_nonce IS NOT NULL)
                ) OR EXISTS (
                    SELECT 1 FROM memory_embeddings_v1 embedding
                     WHERE embedding.memory_id = NEW.memory_id
                       AND embedding.tenant_id = NEW.tenant_id
                ) OR EXISTS (
                    SELECT 1 FROM memory_embeddings_v2 embedding
                     WHERE embedding.memory_id = NEW.memory_id
                       AND embedding.tenant_id = NEW.tenant_id
                ) THEN
                    RAISE EXCEPTION 'memory tombstone cannot complete before crypto-erasure'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END;
            $$;
            CREATE TRIGGER memory_tombstones_state_guard
            BEFORE INSERT OR UPDATE OR DELETE ON memory_tombstones
            FOR EACH ROW EXECUTE FUNCTION memory_tombstone_guard();

            CREATE OR REPLACE FUNCTION memory_version_payload_guard()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'UPDATE' THEN
                    RAISE EXCEPTION 'memory version is immutable'
                        USING ERRCODE = '55000';
                END IF;
                IF NOT memory_exact_pending_delete(OLD.memory_id, OLD.tenant_id) THEN
                    RAISE EXCEPTION 'memory version deletion requires exact pending tombstone'
                        USING ERRCODE = '55000';
                END IF;
                RETURN OLD;
            END;
            $$;
            CREATE TRIGGER memory_versions_payload_guard
            BEFORE UPDATE OR DELETE ON memory_versions
            FOR EACH ROW EXECUTE FUNCTION memory_version_payload_guard();

            CREATE OR REPLACE FUNCTION memory_embedding_payload_guard()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'UPDATE' THEN
                    RAISE EXCEPTION 'memory embedding is immutable'
                        USING ERRCODE = '55000';
                END IF;
                IF NOT memory_exact_pending_delete(OLD.memory_id, OLD.tenant_id) THEN
                    RAISE EXCEPTION 'memory embedding deletion requires exact pending tombstone'
                        USING ERRCODE = '55000';
                END IF;
                RETURN OLD;
            END;
            $$;
            CREATE TRIGGER memory_embeddings_v1_payload_guard
            BEFORE UPDATE OR DELETE ON memory_embeddings_v1
            FOR EACH ROW EXECUTE FUNCTION memory_embedding_payload_guard();
            CREATE TRIGGER memory_embeddings_v2_payload_guard
            BEFORE UPDATE OR DELETE ON memory_embeddings_v2
            FOR EACH ROW EXECUTE FUNCTION memory_embedding_payload_guard();

            CREATE OR REPLACE FUNCTION memory_effect_guard()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'memory effect cannot be deleted' USING ERRCODE = '55000';
                END IF;
                IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                   OR NEW.owner_user_id IS DISTINCT FROM OLD.owner_user_id
                   OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
                   OR NEW.operation_id IS DISTINCT FROM OLD.operation_id
                   OR NEW.memory_id IS DISTINCT FROM OLD.memory_id
                   OR NEW.candidate_id IS DISTINCT FROM OLD.candidate_id
                   OR NEW.effect_kind IS DISTINCT FROM OLD.effect_kind
                   OR NEW.request_sha256 IS DISTINCT FROM OLD.request_sha256
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION 'memory effect immutable binding changed' USING ERRCODE = '55000';
                END IF;
                IF OLD.state <> 'pending' OR NEW.state NOT IN ('committed', 'failed', 'unknown') THEN
                    RAISE EXCEPTION 'invalid memory effect transition' USING ERRCODE = '55000';
                END IF;
                NEW.updated_at := clock_timestamp();
                RETURN NEW;
            END;
            $$;
            CREATE TRIGGER memory_effects_state_guard BEFORE UPDATE OR DELETE ON memory_effects FOR EACH ROW EXECUTE FUNCTION memory_effect_guard();

            CREATE OR REPLACE FUNCTION context_capsule_item_binding_guard()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE capsule context_capsules%ROWTYPE; memory memories%ROWTYPE; version memory_versions%ROWTYPE; review memory_review_evidence%ROWTYPE;
            BEGIN
                SELECT * INTO capsule FROM context_capsules stored_capsule
                 WHERE stored_capsule.id = NEW.capsule_id
                   AND stored_capsule.tenant_id = NEW.tenant_id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'context capsule item capsule is missing'
                        USING ERRCODE = '55000';
                END IF;
                SELECT * INTO memory FROM memories stored_memory
                 WHERE stored_memory.id = NEW.memory_id
                   AND stored_memory.tenant_id = NEW.tenant_id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'context capsule item memory is missing'
                        USING ERRCODE = '55000';
                END IF;
                SELECT * INTO version FROM memory_versions stored_version
                 WHERE stored_version.memory_id = NEW.memory_id
                   AND stored_version.version = NEW.memory_version
                   AND stored_version.tenant_id = NEW.tenant_id;
                IF NOT FOUND OR capsule.owner_user_id IS DISTINCT FROM NEW.owner_user_id
                   OR capsule.workspace_id IS DISTINCT FROM COALESCE(NEW.workspace_id, capsule.workspace_id)
                   OR (NEW.scope = 'agent_private' AND capsule.agent_version_id IS DISTINCT FROM NEW.agent_version_id)
                   OR memory.scope IS DISTINCT FROM NEW.scope
                   OR memory.owner_user_id IS DISTINCT FROM NEW.owner_user_id
                   OR memory.workspace_id IS DISTINCT FROM NEW.workspace_id
                   OR memory.agent_version_id IS DISTINCT FROM NEW.agent_version_id
                   OR version.content_sha256 IS DISTINCT FROM NEW.content_sha256
                   OR version.source_resource_id IS DISTINCT FROM NEW.source_resource_id
                   OR version.source_resource_version IS DISTINCT FROM NEW.source_resource_version THEN
                    RAISE EXCEPTION 'context capsule item binding drifted' USING ERRCODE = '55000';
                END IF;
                IF NEW.scope = 'controlled_shared' THEN
                    SELECT * INTO review FROM memory_review_evidence WHERE id = NEW.review_evidence_id AND tenant_id = NEW.tenant_id;
                    IF NOT FOUND OR review.decision <> 'approved'
                       OR review.reviewer_user_id IS DISTINCT FROM memory.owner_user_id
                       OR review.workspace_id IS DISTINCT FROM NEW.workspace_id
                       OR review.memory_id IS DISTINCT FROM NEW.memory_id
                       OR review.memory_version IS DISTINCT FROM NEW.memory_version
                       OR review.content_sha256 IS DISTINCT FROM NEW.content_sha256
                       OR review.evidence_sha256 IS DISTINCT FROM NEW.review_evidence_sha256
                       OR memory.review_evidence_id IS DISTINCT FROM review.id
                       OR review.reviewed_at < version.created_at
                       OR review.reviewed_at > capsule.issued_at
                       OR NOT EXISTS (
                           SELECT 1 FROM users owner
                            WHERE owner.id = memory.owner_user_id
                              AND owner.id = review.reviewer_user_id
                              AND owner.is_active IS TRUE
                       ) THEN
                        RAISE EXCEPTION 'controlled-shared review evidence binding drifted'
                            USING ERRCODE = '55000';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$;
            CREATE TRIGGER context_capsule_items_binding_guard
            BEFORE INSERT ON context_capsule_items
            FOR EACH ROW EXECUTE FUNCTION context_capsule_item_binding_guard();
            """.replace("__MEMORY_TABLES__", tables)
    op.execute(sa.text(trigger_sql))


def _drop_tenant_triggers() -> None:
    for function in (
        "context_capsule_item_binding_guard",
        "memory_review_evidence_insert_guard",
        "memory_effect_guard",
        "memory_embedding_payload_guard",
        "memory_version_payload_guard",
        "memory_tombstone_guard",
        "memory_append_only_guard",
        "memory_candidate_publication_binding_guard",
        "memories_state_guard",
        "memory_candidate_guard",
        "memory_assert_tenant_schema_binding",
    ):
        op.execute(sa.text(f"DROP FUNCTION IF EXISTS {function}() CASCADE"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS memory_exact_pending_delete(uuid, uuid) CASCADE"))


def _assert_global_downgrade_safe() -> None:
    bind = op.get_bind()
    for raw_schema in bind.execute(
        sa.text("SELECT schema_name FROM omnibase_meta.tenants ORDER BY schema_name")
    ).scalars():
        schema = str(raw_schema)
        if _TENANT_SCHEMA_PATTERN.fullmatch(schema) is None:
            raise RuntimeError("0013 downgrade refused: invalid tenant schema name")
        present = set(
            bind.execute(
                sa.text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = :schema AND table_name = ANY(CAST(:tables AS text[]))"
                ),
                {"schema": schema, "tables": list(_MEMORY_TABLES)},
            ).scalars()
        )
        if present and present != set(_MEMORY_TABLES):
            raise RuntimeError("0013 downgrade refused: tenant memory table set is incomplete")
        if not present:
            continue
        union = " UNION ALL ".join(
            f'(SELECT 1 FROM "{schema}"."{table}" LIMIT 1)'  # noqa: S608 -- validated registry schema and closed table set
            for table in _MEMORY_TABLES
        )
        if bind.execute(sa.text(f"SELECT EXISTS ({union})")).scalar_one():
            raise RuntimeError(
                "0013 populated downgrade is forbidden; use a forward fix or restore into a new omnibase_restore_* database"
            )


def _drop_empty_tenant_global_dependencies() -> None:
    """Remove only empty 0013 tables' foreign keys into the global schema.

    Global revisions downgrade before tenant revisions. The safety check above
    proves that every retained 0013 table is empty; removing only cross-schema
    foreign keys lets historical global hard locks run in their original order.
    The surrounding Alembic transaction restores these constraints if any
    lower revision refuses the downgrade.
    """
    bind = op.get_bind()
    for raw_schema in bind.execute(
        sa.text("SELECT schema_name FROM omnibase_meta.tenants ORDER BY schema_name")
    ).scalars():
        schema = str(raw_schema)
        if _TENANT_SCHEMA_PATTERN.fullmatch(schema) is None:
            raise RuntimeError("0013 downgrade refused: invalid tenant schema name")
        present = set(
            bind.execute(
                sa.text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :schema "
                    "AND table_name = ANY(CAST(:tables AS text[]))"
                ),
                {"schema": schema, "tables": list(_MEMORY_TABLES)},
            ).scalars()
        )
        if not present:
            continue
        if present != set(_MEMORY_TABLES):
            raise RuntimeError("0013 downgrade refused: tenant memory table set is incomplete")

        dependencies = bind.execute(
            sa.text(
                "SELECT source_table.relname, constraint_row.conname "
                "FROM pg_constraint constraint_row "
                "JOIN pg_class source_table ON source_table.oid = constraint_row.conrelid "
                "JOIN pg_namespace source_schema ON source_schema.oid = source_table.relnamespace "
                "JOIN pg_class target_table ON target_table.oid = constraint_row.confrelid "
                "JOIN pg_namespace target_schema ON target_schema.oid = target_table.relnamespace "
                "WHERE constraint_row.contype = 'f' "
                "AND source_schema.nspname = :schema "
                "AND source_table.relname = ANY(CAST(:tables AS text[])) "
                "AND target_schema.nspname = :global_schema "
                "ORDER BY source_table.relname, constraint_row.conname"
            ),
            {
                "schema": schema,
                "tables": list(_MEMORY_TABLES),
                "global_schema": _GLOBAL_SCHEMA,
            },
        ).tuples()
        for raw_table, raw_constraint in dependencies:
            table = str(raw_table)
            constraint = str(raw_constraint)
            if table not in _MEMORY_TABLES or _IDENTIFIER_PATTERN.fullmatch(constraint) is None:
                raise RuntimeError("0013 downgrade refused: invalid global dependency identifier")
            op.execute(sa.text(f'ALTER TABLE "{schema}"."{table}" DROP CONSTRAINT "{constraint}"'))


def upgrade() -> None:
    if _migration_schema_scope() == "global":
        return
    _create_candidates()
    _create_memories_and_versions()
    _create_review_and_capsules()
    _create_effects_and_tombstones()
    _create_embedding_lane("memory_embeddings_v1", 1024)
    _create_embedding_lane("memory_embeddings_v2", 1536)
    _install_tenant_triggers()


def downgrade() -> None:
    if _migration_schema_scope() == "global":
        _assert_global_downgrade_safe()
        _drop_empty_tenant_global_dependencies()
        return
    bind = op.get_bind()
    populated = " OR ".join(
        f"EXISTS (SELECT 1 FROM {table} LIMIT 1)"  # noqa: S608 -- closed table set
        for table in _MEMORY_TABLES
    )
    if bind.execute(sa.text(f"SELECT {populated}")).scalar_one():
        raise RuntimeError(
            "0013 populated tenant downgrade is forbidden; use a forward fix or restore into a new omnibase_restore_* database"
        )
    _drop_tenant_triggers()
    op.drop_constraint("memories_deletion_effect_tenant_fk", "memories", type_="foreignkey")
    op.drop_constraint("memories_current_version_tenant_fk", "memories", type_="foreignkey")
    op.drop_constraint(
        "memory_candidates_active_memory_tenant_fk",
        "memory_candidates",
        type_="foreignkey",
    )
    op.drop_constraint(
        "memory_candidates_source_capsule_tenant_fk",
        "memory_candidates",
        type_="foreignkey",
    )
    op.drop_constraint("memories_review_evidence_tenant_fk", "memories", type_="foreignkey")
    for table in reversed(_MEMORY_TABLES):
        op.drop_table(table)
