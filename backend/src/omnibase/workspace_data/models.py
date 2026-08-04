"""Global persistence models for P34.6 Workspace-private data.

The records in this module contain logical identities, immutable digests and
durable lifecycle state.  Object-store keys, database identifiers, credentials
and content remain server-owned adapter details and must not be serialized.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnibase.db.models import GLOBAL_SCHEMA, Base

_UUID = UUID(as_uuid=False)


class WorkspaceArtifact(Base):
    """Immutable, content-addressed Workspace artifact metadata."""

    __tablename__ = "workspace_artifacts"
    __table_args__ = (
        CheckConstraint(
            "source_generation >= 1",
            name="workspace_artifacts_generation_check",
        ),
        CheckConstraint(
            "content_digest ~ '^[0-9a-f]{64}$'",
            name="workspace_artifacts_digest_check",
        ),
        CheckConstraint("size_bytes >= 0", name="workspace_artifacts_size_check"),
        CheckConstraint(
            "state IN ('staging', 'available', 'tombstoned', 'purge_pending', "
            "'purged', 'failed', 'unknown')",
            name="workspace_artifacts_state_check",
        ),
        CheckConstraint("version >= 1", name="workspace_artifacts_version_check"),
        ForeignKeyConstraint(
            ["id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.resource_registry.id",
                f"{GLOBAL_SCHEMA}.resource_registry.tenant_id",
            ],
            name="workspace_artifacts_resource_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.id",
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
            ],
            name="workspace_artifacts_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_run_id", "tenant_id", "workspace_id"],
            [
                f"{GLOBAL_SCHEMA}.workspace_runs.id",
                f"{GLOBAL_SCHEMA}.workspace_runs.tenant_id",
                f"{GLOBAL_SCHEMA}.workspace_runs.workspace_id",
            ],
            name="workspace_artifacts_run_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["operation_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.operations.id",
                f"{GLOBAL_SCHEMA}.operations.tenant_id",
            ],
            name="workspace_artifacts_operation_tenant_fk",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="workspace_artifacts_id_tenant_uq"),
        UniqueConstraint("tenant_id", "operation_id", name="workspace_artifacts_operation_uq"),
        Index(
            "workspace_artifacts_workspace_state_idx",
            "tenant_id",
            "workspace_id",
            "state",
            "created_at",
        ),
        Index(
            "workspace_artifacts_workspace_digest_idx",
            "tenant_id",
            "workspace_id",
            "content_digest",
        ),
    )

    id: Mapped[str] = mapped_column(_UUID, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    source_run_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    source_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'staging'"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_actor_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class WorkspaceDerivedIndex(Base):
    """One immutable derived-index generation, isolated from canonical RAG."""

    __tablename__ = "workspace_derived_indexes"
    __table_args__ = (
        CheckConstraint("source_version >= 1", name="workspace_derived_source_version_check"),
        CheckConstraint(
            "index_profile_digest ~ '^[0-9a-f]{64}$'",
            name="workspace_derived_profile_digest_check",
        ),
        CheckConstraint(
            "manifest_digest IS NULL OR manifest_digest ~ '^[0-9a-f]{64}$'",
            name="workspace_derived_manifest_digest_check",
        ),
        CheckConstraint("chunk_count >= 0", name="workspace_derived_chunk_count_check"),
        CheckConstraint(
            "state IN ('pending', 'building', 'ready', 'failed', 'revoked', 'unknown')",
            name="workspace_derived_state_check",
        ),
        CheckConstraint(
            "state <> 'ready' OR manifest_digest IS NOT NULL",
            name="workspace_derived_ready_manifest_check",
        ),
        CheckConstraint("version >= 1", name="workspace_derived_version_check"),
        ForeignKeyConstraint(
            ["id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.resource_registry.id",
                f"{GLOBAL_SCHEMA}.resource_registry.tenant_id",
            ],
            name="workspace_derived_resource_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.id",
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
            ],
            name="workspace_derived_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_resource_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.resource_registry.id",
                f"{GLOBAL_SCHEMA}.resource_registry.tenant_id",
            ],
            name="workspace_derived_source_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["operation_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.operations.id",
                f"{GLOBAL_SCHEMA}.operations.tenant_id",
            ],
            name="workspace_derived_operation_tenant_fk",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="workspace_derived_id_tenant_uq"),
        UniqueConstraint("tenant_id", "operation_id", name="workspace_derived_operation_uq"),
        UniqueConstraint("tenant_id", "generation", name="workspace_derived_generation_uq"),
        Index(
            "workspace_derived_workspace_state_idx",
            "tenant_id",
            "workspace_id",
            "state",
            "created_at",
        ),
        Index(
            "workspace_derived_source_idx",
            "tenant_id",
            "source_resource_id",
            "source_version",
        ),
    )

    id: Mapped[str] = mapped_column(_UUID, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    source_resource_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    generation: Mapped[str] = mapped_column(
        _UUID, nullable=False, server_default=text("gen_random_uuid()")
    )
    index_profile_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_by_actor_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class WorkspacePublication(Base):
    """Approval-bound copy-on-publish ledger; the source is never reclassified."""

    __tablename__ = "workspace_publications"
    __table_args__ = (
        CheckConstraint("source_version >= 1", name="workspace_publications_source_version_check"),
        CheckConstraint(
            "source_manifest_digest ~ '^[0-9a-f]{64}$'",
            name="workspace_publications_source_digest_check",
        ),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="workspace_publications_request_hash_check",
        ),
        CheckConstraint(
            "target_scope IN ('workspace_shared', 'tenant_shared')",
            name="workspace_publications_target_scope_check",
        ),
        CheckConstraint(
            "(target_scope = 'workspace_shared' AND target_workspace_id IS NOT NULL) OR "
            "(target_scope = 'tenant_shared' AND target_workspace_id IS NULL)",
            name="workspace_publications_target_identity_check",
        ),
        CheckConstraint(
            "state IN ('pending_approval', 'approved', 'copying', 'published', "
            "'rejected', 'expired', 'failed', 'unknown')",
            name="workspace_publications_state_check",
        ),
        CheckConstraint(
            "state = 'pending_approval' OR approval_id IS NOT NULL",
            name="workspace_publications_approval_check",
        ),
        CheckConstraint(
            "state <> 'published' OR target_resource_id IS NOT NULL",
            name="workspace_publications_published_target_check",
        ),
        CheckConstraint("version >= 1", name="workspace_publications_version_check"),
        ForeignKeyConstraint(
            ["source_workspace_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.id",
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
            ],
            name="workspace_publications_source_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["target_workspace_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.id",
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
            ],
            name="workspace_publications_target_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_resource_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.resource_registry.id",
                f"{GLOBAL_SCHEMA}.resource_registry.tenant_id",
            ],
            name="workspace_publications_source_resource_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["target_resource_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.resource_registry.id",
                f"{GLOBAL_SCHEMA}.resource_registry.tenant_id",
            ],
            name="workspace_publications_target_resource_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["operation_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.operations.id",
                f"{GLOBAL_SCHEMA}.operations.tenant_id",
            ],
            name="workspace_publications_operation_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["approval_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.approval_requests.id",
                f"{GLOBAL_SCHEMA}.approval_requests.tenant_id",
            ],
            name="workspace_publications_approval_tenant_fk",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="workspace_publications_id_tenant_uq"),
        UniqueConstraint("tenant_id", "operation_id", name="workspace_publications_operation_uq"),
        Index(
            "workspace_publications_source_target_uq",
            "tenant_id",
            "source_resource_id",
            "source_version",
            "source_manifest_digest",
            "target_scope",
            "target_workspace_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index(
            "workspace_publications_workspace_state_idx",
            "tenant_id",
            "source_workspace_id",
            "state",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        _UUID, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    source_workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    target_workspace_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    source_resource_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    target_scope: Mapped[str] = mapped_column(String(24), nullable=False)
    target_resource_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    operation_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    approval_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'pending_approval'")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_by_actor_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class WorkspaceSnapshotItem(Base):
    """Immutable server-verified item in a Workspace snapshot inventory."""

    __tablename__ = "workspace_snapshot_items"
    __table_args__ = (
        CheckConstraint("ordinal >= 1", name="workspace_snapshot_items_ordinal_check"),
        CheckConstraint("source_version >= 1", name="workspace_snapshot_items_version_check"),
        CheckConstraint(
            "item_kind IN ('private_table', 'artifact', 'derived_index')",
            name="workspace_snapshot_items_kind_check",
        ),
        CheckConstraint(
            "source_policy_class IN ('workspace_private', 'workspace_derived')",
            name="workspace_snapshot_items_policy_check",
        ),
        CheckConstraint(
            "content_digest ~ '^[0-9a-f]{64}$'",
            name="workspace_snapshot_items_digest_check",
        ),
        CheckConstraint("size_bytes >= 0", name="workspace_snapshot_items_size_check"),
        ForeignKeyConstraint(
            ["snapshot_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspace_snapshots.id",
                f"{GLOBAL_SCHEMA}.workspace_snapshots.tenant_id",
            ],
            name="workspace_snapshot_items_snapshot_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.id",
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
            ],
            name="workspace_snapshot_items_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_resource_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.resource_registry.id",
                f"{GLOBAL_SCHEMA}.resource_registry.tenant_id",
            ],
            name="workspace_snapshot_items_source_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["payload_artifact_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspace_artifacts.id",
                f"{GLOBAL_SCHEMA}.workspace_artifacts.tenant_id",
            ],
            name="workspace_snapshot_items_payload_tenant_fk",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "snapshot_id",
            "source_resource_id",
            name="workspace_snapshot_items_source_uq",
        ),
        Index(
            "workspace_snapshot_items_workspace_idx",
            "tenant_id",
            "workspace_id",
            "snapshot_id",
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(_UUID, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    source_resource_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_policy_class: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    item_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_artifact_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class WorkspaceDataEffect(Base):
    """Durable external-effect reservation with explicit unknown outcome."""

    __tablename__ = "workspace_data_effects"
    __table_args__ = (
        CheckConstraint("sequence >= 1", name="workspace_data_effects_sequence_check"),
        CheckConstraint(
            "effect_kind IN ('artifact_put', 'artifact_delete', 'derived_build', "
            "'publication_copy', 'snapshot_capture', 'snapshot_restore')",
            name="workspace_data_effects_kind_check",
        ),
        CheckConstraint(
            "binding_digest ~ '^[0-9a-f]{64}$'",
            name="workspace_data_effects_binding_digest_check",
        ),
        CheckConstraint(
            "receipt_digest IS NULL OR receipt_digest ~ '^[0-9a-f]{64}$'",
            name="workspace_data_effects_receipt_digest_check",
        ),
        CheckConstraint(
            "state IN ('pending', 'committed', 'failed', 'unknown')",
            name="workspace_data_effects_state_check",
        ),
        CheckConstraint(
            "state <> 'committed' OR receipt_digest IS NOT NULL",
            name="workspace_data_effects_committed_receipt_check",
        ),
        CheckConstraint("version >= 1", name="workspace_data_effects_version_check"),
        ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.id",
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
            ],
            name="workspace_data_effects_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["resource_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.resource_registry.id",
                f"{GLOBAL_SCHEMA}.resource_registry.tenant_id",
            ],
            name="workspace_data_effects_resource_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["operation_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.operations.id",
                f"{GLOBAL_SCHEMA}.operations.tenant_id",
            ],
            name="workspace_data_effects_operation_tenant_fk",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "operation_id",
            "sequence",
            name="workspace_data_effects_operation_sequence_uq",
        ),
        UniqueConstraint(
            "tenant_id",
            "operation_id",
            "effect_kind",
            "binding_digest",
            name="workspace_data_effects_binding_uq",
        ),
        Index(
            "workspace_data_effects_workspace_state_idx",
            "tenant_id",
            "workspace_id",
            "state",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        _UUID, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    resource_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    operation_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    effect_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    binding_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))
    receipt_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


__all__ = [
    "WorkspaceArtifact",
    "WorkspaceDataEffect",
    "WorkspaceDerivedIndex",
    "WorkspacePublication",
    "WorkspaceSnapshotItem",
]
