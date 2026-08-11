"""ORM mappings for migration 0013 tenant Memory persistence.

Database constraints and triggers in migration 0013 remain authoritative. The
ORM intentionally declares no relationships that could hide tenant predicates
or cascade payload deletion outside the governed tombstone transaction.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Integer, LargeBinary, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnibase.db.tenant import TenantBase

_UUID = UUID(as_uuid=False)
_PK_DEFAULT = text("gen_random_uuid()")
_CLOCK_DEFAULT = func.clock_timestamp()


class MemoryCandidateModel(TenantBase):
    __tablename__ = "memory_candidates"

    id: Mapped[str] = mapped_column(_UUID, primary_key=True, server_default=_PK_DEFAULT)
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    owner_user_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    agent_version_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    task_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    invocation_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    source_capsule_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    memory_policy_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    requested_scope: Mapped[str] = mapped_column(String(24), nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(16), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(24), nullable=False)
    content_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    content_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    content_key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_resource_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    source_resource_version: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_reference_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    confidence_millis: Mapped[int] = mapped_column(Integer, nullable=False)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    requires_user_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False)
    contains_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    inferred_sensitive_categories: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    active_memory_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    acceptance_operation_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    acceptance_approval_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    confirmed_by_user_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmation_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    candidate_created_by: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_CLOCK_DEFAULT
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_CLOCK_DEFAULT
    )


class MemoryModel(TenantBase):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(_UUID, primary_key=True, server_default=_PK_DEFAULT)
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    owner_user_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    agent_version_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    scope: Mapped[str] = mapped_column(String(24), nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(16), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(16), nullable=False)
    current_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_from_candidate_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    review_evidence_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    deletion_effect_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_CLOCK_DEFAULT
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_CLOCK_DEFAULT
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MemoryVersionModel(TenantBase):
    __tablename__ = "memory_versions"

    id: Mapped[str] = mapped_column(_UUID, primary_key=True, server_default=_PK_DEFAULT)
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    memory_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_resource_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    source_resource_version: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_reference_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_CLOCK_DEFAULT
    )


class MemoryReviewEvidenceModel(TenantBase):
    __tablename__ = "memory_review_evidence"

    id: Mapped[str] = mapped_column(_UUID, primary_key=True, server_default=_PK_DEFAULT)
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    reviewer_user_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    memory_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    memory_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_CLOCK_DEFAULT
    )


class ContextCapsuleModel(TenantBase):
    __tablename__ = "context_capsules"

    id: Mapped[str] = mapped_column(_UUID, primary_key=True, server_default=_PK_DEFAULT)
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    owner_user_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    agent_version_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    task_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    invocation_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    memory_policy_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    compiler_policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    delegable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    trusted_instructions: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sensitivity_summary: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_CLOCK_DEFAULT
    )


class ContextCapsuleItemModel(TenantBase):
    __tablename__ = "context_capsule_items"

    id: Mapped[str] = mapped_column(_UUID, primary_key=True, server_default=_PK_DEFAULT)
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    capsule_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    memory_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    memory_version: Mapped[int] = mapped_column(Integer, nullable=False)
    scope: Mapped[str] = mapped_column(String(24), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    agent_version_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    review_evidence_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    review_evidence_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_resource_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    source_resource_version: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_reference_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    selection_reason: Mapped[str] = mapped_column(String(24), nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(16), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_CLOCK_DEFAULT
    )


class MemoryEffectModel(TenantBase):
    __tablename__ = "memory_effects"

    id: Mapped[str] = mapped_column(_UUID, primary_key=True, server_default=_PK_DEFAULT)
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    owner_user_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    operation_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    memory_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    candidate_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    effect_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    result_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_CLOCK_DEFAULT
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_CLOCK_DEFAULT
    )


class MemoryTombstoneModel(TenantBase):
    __tablename__ = "memory_tombstones"

    id: Mapped[str] = mapped_column(_UUID, primary_key=True, server_default=_PK_DEFAULT)
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    memory_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    last_memory_version: Mapped[int] = mapped_column(Integer, nullable=False)
    deleted_by_user_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    owner_user_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    deletion_effect_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    result_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    deletion_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_CLOCK_DEFAULT
    )


class MemoryEmbeddingV1Model(TenantBase):
    __tablename__ = "memory_embeddings_v1"

    id: Mapped[str] = mapped_column(_UUID, primary_key=True, server_default=_PK_DEFAULT)
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    memory_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    memory_version: Mapped[int] = mapped_column(Integer, nullable=False)
    scope: Mapped[str] = mapped_column(String(24), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    agent_version_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model_id: Mapped[str] = mapped_column(String(96), nullable=False)
    embedding_model_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding = mapped_column(Vector(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_CLOCK_DEFAULT
    )


class MemoryEmbeddingV2Model(TenantBase):
    __tablename__ = "memory_embeddings_v2"

    id: Mapped[str] = mapped_column(_UUID, primary_key=True, server_default=_PK_DEFAULT)
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    memory_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    memory_version: Mapped[int] = mapped_column(Integer, nullable=False)
    scope: Mapped[str] = mapped_column(String(24), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    agent_version_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model_id: Mapped[str] = mapped_column(String(96), nullable=False)
    embedding_model_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding = mapped_column(Vector(1536), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_CLOCK_DEFAULT
    )


__all__ = [
    "ContextCapsuleItemModel",
    "ContextCapsuleModel",
    "MemoryCandidateModel",
    "MemoryEffectModel",
    "MemoryEmbeddingV1Model",
    "MemoryEmbeddingV2Model",
    "MemoryModel",
    "MemoryReviewEvidenceModel",
    "MemoryTombstoneModel",
    "MemoryVersionModel",
]
