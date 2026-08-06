"""Persistence models for the P34.4 Workspace control plane.

Only logical identities, policy state, leases, and synthetic collaboration
metadata live here.  Runtime provider handles, host paths, addresses, secrets,
and real tenant data are deliberately outside the public control-plane model.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnibase.db.models import GLOBAL_SCHEMA, Base

_UUID = UUID(as_uuid=False)
_EMPTY_JSON = text("'{}'::jsonb")
_EMPTY_ARRAY = text("ARRAY[]::varchar[]")


class WorkspaceTemplate(Base):
    """Immutable, versioned template metadata with a canonical digest."""

    __tablename__ = "workspace_templates"
    __table_args__ = (
        CheckConstraint("version >= 1", name="workspace_templates_version_check"),
        CheckConstraint(
            "digest ~ '^[0-9a-f]{64}$'",
            name="workspace_templates_digest_check",
        ),
        CheckConstraint(
            "state IN ('active', 'deprecated', 'revoked')",
            name="workspace_templates_state_check",
        ),
        CheckConstraint(
            "jsonb_typeof(template_spec) = 'object'",
            name="workspace_templates_spec_object_check",
        ),
        UniqueConstraint(
            "tenant_id",
            "template_key",
            "version",
            name="workspace_templates_key_version_uq",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name="workspace_templates_id_tenant_uq",
        ),
        Index(
            "workspace_templates_tenant_state_idx",
            "tenant_id",
            "state",
            "template_key",
        ),
    )

    id: Mapped[str] = mapped_column(
        _UUID,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(
        _UUID,
        ForeignKey(f"{GLOBAL_SCHEMA}.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    template_key: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    template_spec: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=_EMPTY_JSON,
    )
    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'active'"),
    )
    supersedes_template_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    created_by_user_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class Workspace(Base):
    """Long-lived AI Space identity and desired/observed lifecycle state."""

    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint(
            "desired_state IN ('stopped', 'running', 'paused', 'archived')",
            name="workspaces_desired_state_check",
        ),
        CheckConstraint(
            "observed_state IN ('provisioning', 'stopped', 'starting', 'running', "
            "'pausing', 'paused', 'stopping', 'archiving', 'archived', 'failed')",
            name="workspaces_observed_state_check",
        ),
        CheckConstraint("generation >= 1", name="workspaces_generation_check"),
        CheckConstraint("version >= 1", name="workspaces_version_check"),
        CheckConstraint(
            "jsonb_typeof(quota) = 'object'",
            name="workspaces_quota_object_check",
        ),
        UniqueConstraint("tenant_id", "id", name="workspaces_id_tenant_uq"),
        ForeignKeyConstraint(
            ["restored_from_snapshot_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspace_snapshots.id",
                f"{GLOBAL_SCHEMA}.workspace_snapshots.tenant_id",
            ],
            name="workspaces_restored_snapshot_tenant_fk",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.resource_registry.id",
                f"{GLOBAL_SCHEMA}.resource_registry.tenant_id",
            ],
            name="workspaces_resource_tenant_fk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["template_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspace_templates.id",
                f"{GLOBAL_SCHEMA}.workspace_templates.tenant_id",
            ],
            name="workspaces_template_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["parent_workspace_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.id",
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
            ],
            name="workspaces_parent_tenant_fk",
            ondelete="RESTRICT",
        ),
        Index(
            "workspaces_tenant_observed_idx",
            "tenant_id",
            "observed_state",
            "created_at",
        ),
        Index(
            "workspaces_tenant_owner_idx",
            "tenant_id",
            "owner_user_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        _UUID,
        primary_key=True,
    )
    tenant_id: Mapped[str] = mapped_column(
        _UUID,
        ForeignKey(f"{GLOBAL_SCHEMA}.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    template_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    owner_user_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    parent_workspace_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    restored_from_snapshot_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    desired_state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'stopped'"),
    )
    observed_state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'stopped'"),
    )
    generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    quota: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=_EMPTY_JSON,
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class WorkspaceMembership(Base):
    """Live Workspace membership; tenant membership alone grants no access."""

    __tablename__ = "workspace_memberships"
    __table_args__ = (
        CheckConstraint(
            "role IN ('viewer', 'member', 'operator', 'maintainer', 'owner')",
            name="workspace_memberships_role_check",
        ),
        CheckConstraint(
            "state IN ('active', 'suspended', 'revoked')",
            name="workspace_memberships_state_check",
        ),
        CheckConstraint("version >= 1", name="workspace_memberships_version_check"),
        ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.id",
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
            ],
            name="workspace_memberships_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        Index(
            "workspace_memberships_active_user_uq",
            "tenant_id",
            "workspace_id",
            "user_id",
            unique=True,
            postgresql_where=text("state IN ('active', 'suspended')"),
        ),
        Index(
            "workspace_memberships_tenant_user_idx",
            "tenant_id",
            "user_id",
            "state",
        ),
    )

    id: Mapped[str] = mapped_column(
        _UUID,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    user_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'active'"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    created_by_user_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class ResourceScopeBinding(Base):
    """Structured scope ownership for resources governed by P34.4."""

    __tablename__ = "resource_scope_bindings"
    __table_args__ = (
        CheckConstraint(
            "scope_class IN ('platform_internal', 'tenant_shared', 'user_private', "
            "'workspace_private', 'workspace_shared', 'run_ephemeral')",
            name="resource_scope_bindings_scope_check",
        ),
        CheckConstraint(
            "(scope_class = 'platform_internal' AND user_id IS NULL "
            "AND workspace_id IS NULL AND run_id IS NULL) OR "
            "(scope_class = 'tenant_shared' AND user_id IS NULL "
            "AND workspace_id IS NULL AND run_id IS NULL) OR "
            "(scope_class = 'user_private' AND user_id IS NOT NULL "
            "AND workspace_id IS NULL AND run_id IS NULL) OR "
            "(scope_class IN ('workspace_private', 'workspace_shared') "
            "AND user_id IS NULL AND workspace_id IS NOT NULL AND run_id IS NULL) OR "
            "(scope_class = 'run_ephemeral' AND user_id IS NULL "
            "AND workspace_id IS NOT NULL AND run_id IS NOT NULL)",
            name="resource_scope_bindings_identity_check",
        ),
        CheckConstraint("version >= 1", name="resource_scope_bindings_version_check"),
        ForeignKeyConstraint(
            ["resource_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.resource_registry.id",
                f"{GLOBAL_SCHEMA}.resource_registry.tenant_id",
            ],
            name="resource_scope_bindings_resource_tenant_fk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.id",
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
            ],
            name="resource_scope_bindings_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["run_id", "tenant_id", "workspace_id"],
            [
                f"{GLOBAL_SCHEMA}.workspace_runs.id",
                f"{GLOBAL_SCHEMA}.workspace_runs.tenant_id",
                f"{GLOBAL_SCHEMA}.workspace_runs.workspace_id",
            ],
            name="resource_scope_bindings_run_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        Index(
            "resource_scope_bindings_workspace_idx",
            "tenant_id",
            "workspace_id",
            "scope_class",
        ),
        Index(
            "resource_scope_bindings_user_idx",
            "tenant_id",
            "user_id",
            "scope_class",
        ),
    )

    resource_id: Mapped[str] = mapped_column(_UUID, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    scope_class: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    run_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class WorkspaceScopeGrant(Base):
    """Explicit, revocable cross-scope projection into one Workspace."""

    __tablename__ = "workspace_scope_grants"
    __table_args__ = (
        CheckConstraint(
            "source_scope IN ('user_private', 'workspace_private', "
            "'workspace_shared', 'tenant_shared')",
            name="workspace_scope_grants_source_scope_check",
        ),
        CheckConstraint(
            "source_scope <> 'tenant_shared' OR source_owner_id IS NULL",
            name="workspace_scope_grants_tenant_owner_check",
        ),
        CheckConstraint(
            "source_scope = 'tenant_shared' OR source_owner_id IS NOT NULL",
            name="workspace_scope_grants_source_owner_check",
        ),
        CheckConstraint(
            "state IN ('active', 'revoked', 'expired')",
            name="workspace_scope_grants_state_check",
        ),
        CheckConstraint(
            "cardinality(actions) BETWEEN 1 AND 32",
            name="workspace_scope_grants_actions_check",
        ),
        CheckConstraint(
            "actions <@ ARRAY['resource.list', 'resource.read']::varchar[]",
            name="workspace_scope_grants_actions_allowlist_check",
        ),
        CheckConstraint("version >= 1", name="workspace_scope_grants_version_check"),
        ForeignKeyConstraint(
            ["target_workspace_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.id",
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
            ],
            name="workspace_scope_grants_target_tenant_fk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["resource_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.resource_registry.id",
                f"{GLOBAL_SCHEMA}.resource_registry.tenant_id",
            ],
            name="workspace_scope_grants_resource_tenant_fk",
            ondelete="CASCADE",
        ),
        Index(
            "workspace_scope_grants_target_idx",
            "tenant_id",
            "target_workspace_id",
            "state",
            "expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        _UUID,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    target_workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    source_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    source_owner_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    resource_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    actions: Mapped[list[str]] = mapped_column(ARRAY(String(100)), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'active'"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class WorkspaceRun(Base):
    """Short-lived execution intent, never a long-term authorization container."""

    __tablename__ = "workspace_runs"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('batch', 'interactive')",
            name="workspace_runs_kind_check",
        ),
        CheckConstraint(
            "desired_state IN ('queued', 'running', 'paused', 'stopped', 'cancelled')",
            name="workspace_runs_desired_state_check",
        ),
        CheckConstraint(
            "observed_state IN ('queued', 'leased', 'starting', 'running', "
            "'pausing', 'paused', 'stopping', 'stopped', 'succeeded', 'failed', "
            "'cancelled')",
            name="workspace_runs_observed_state_check",
        ),
        CheckConstraint("generation >= 1", name="workspace_runs_generation_check"),
        CheckConstraint(
            "next_fencing_token >= 1",
            name="workspace_runs_fencing_check",
        ),
        CheckConstraint("version >= 1", name="workspace_runs_version_check"),
        ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.id",
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
            ],
            name="workspace_runs_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "id", name="workspace_runs_id_tenant_uq"),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "id",
            name="workspace_runs_id_workspace_tenant_uq",
        ),
        Index(
            "workspace_runs_workspace_state_idx",
            "tenant_id",
            "workspace_id",
            "observed_state",
            "created_at",
        ),
        Index(
            "workspace_runs_one_active_uq",
            "tenant_id",
            "workspace_id",
            unique=True,
            postgresql_where=text(
                "observed_state IN ('leased', 'starting', 'running', 'pausing', 'stopping')"
            ),
        ),
        ForeignKeyConstraint(
            ["id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.resource_registry.id",
                f"{GLOBAL_SCHEMA}.resource_registry.tenant_id",
            ],
            name="workspace_runs_resource_tenant_fk",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[str] = mapped_column(
        _UUID,
        primary_key=True,
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    desired_state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'queued'"),
    )
    observed_state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'queued'"),
    )
    next_fencing_token: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("1"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_instance_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    workload_identity_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_result_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by_user_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class RunLease(Base):
    """Pull lease with a monotonically increasing fencing token."""

    __tablename__ = "run_leases"
    __table_args__ = (
        CheckConstraint(
            "state IN ('active', 'expired', 'revoked', 'completed')",
            name="run_leases_state_check",
        ),
        CheckConstraint("generation >= 1", name="run_leases_generation_check"),
        CheckConstraint("fencing_token >= 1", name="run_leases_fencing_check"),
        CheckConstraint(
            "node_fencing_token >= 1",
            name="run_leases_node_fencing_check",
        ),
        CheckConstraint(
            "heartbeat_at <= expires_at",
            name="run_leases_heartbeat_expiry_check",
        ),
        ForeignKeyConstraint(
            ["run_id", "tenant_id", "workspace_id"],
            [
                f"{GLOBAL_SCHEMA}.workspace_runs.id",
                f"{GLOBAL_SCHEMA}.workspace_runs.tenant_id",
                f"{GLOBAL_SCHEMA}.workspace_runs.workspace_id",
            ],
            name="run_leases_run_tenant_fk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["node_id", "tenant_id", "workspace_id"],
            [
                f"{GLOBAL_SCHEMA}.workspace_nodes.id",
                f"{GLOBAL_SCHEMA}.workspace_nodes.tenant_id",
                f"{GLOBAL_SCHEMA}.workspace_nodes.workspace_id",
            ],
            name="run_leases_node_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "run_id",
            "fencing_token",
            name="run_leases_fencing_uq",
        ),
        UniqueConstraint("id", "tenant_id", name="run_leases_id_tenant_uq"),
        Index(
            "run_leases_run_state_idx",
            "tenant_id",
            "run_id",
            "state",
            "expires_at",
        ),
        Index(
            "run_leases_one_active_uq",
            "tenant_id",
            "run_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
    )

    id: Mapped[str] = mapped_column(
        _UUID,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    run_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    node_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    node_fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'active'"),
    )
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class WorkspaceSnapshot(Base):
    """Metadata-only snapshot; active identities and handles are never captured."""

    __tablename__ = "workspace_snapshots"
    __table_args__ = (
        CheckConstraint("source_generation >= 1", name="workspace_snapshots_generation_check"),
        CheckConstraint(
            "manifest_digest ~ '^[0-9a-f]{64}$'",
            name="workspace_snapshots_digest_check",
        ),
        CheckConstraint(
            "state IN ('building', 'ready', 'failed', 'revoked')",
            name="workspace_snapshots_state_check",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.id",
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
            ],
            name="workspace_snapshots_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "id", name="workspace_snapshots_id_tenant_uq"),
        ForeignKeyConstraint(
            ["id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.resource_registry.id",
                f"{GLOBAL_SCHEMA}.resource_registry.tenant_id",
            ],
            name="workspace_snapshots_resource_tenant_fk",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[str] = mapped_column(
        _UUID,
        primary_key=True,
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    source_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=_EMPTY_JSON,
    )
    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'building'"),
    )
    created_by_user_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class WorkspaceNode(Base):
    """Trusted member-device daemon identity; never a Sandbox identity."""

    __tablename__ = "workspace_nodes"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'active', 'suspended', 'revoked')",
            name="workspace_nodes_state_check",
        ),
        CheckConstraint(
            "attestation_state IN ('pending', 'verified', 'expired', 'rejected')",
            name="workspace_nodes_attestation_state_check",
        ),
        CheckConstraint(
            "identity_digest ~ '^[0-9a-f]{64}$'",
            name="workspace_nodes_identity_digest_check",
        ),
        CheckConstraint("fencing_token >= 1", name="workspace_nodes_fencing_check"),
        CheckConstraint("version >= 1", name="workspace_nodes_version_check"),
        ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.id",
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
            ],
            name="workspace_nodes_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "id", name="workspace_nodes_id_tenant_uq"),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "id",
            name="workspace_nodes_id_workspace_tenant_uq",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "identity_digest",
            name="workspace_nodes_identity_uq",
        ),
        Index(
            "workspace_nodes_workspace_state_idx",
            "tenant_id",
            "workspace_id",
            "state",
            "attestation_state",
        ),
    )

    id: Mapped[str] = mapped_column(
        _UUID,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    owner_user_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    identity_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'pending'"),
    )
    attestation_state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'pending'"),
    )
    fencing_token: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("1"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class NodeAttestation(Base):
    """Bounded, replay-resistant attestation evidence digest."""

    __tablename__ = "node_attestations"
    __table_args__ = (
        CheckConstraint(
            "state IN ('verified', 'expired', 'rejected', 'revoked')",
            name="node_attestations_state_check",
        ),
        CheckConstraint(
            "nonce_digest ~ '^[0-9a-f]{64}$' AND evidence_digest ~ '^[0-9a-f]{64}$'",
            name="node_attestations_digest_check",
        ),
        ForeignKeyConstraint(
            ["node_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspace_nodes.id",
                f"{GLOBAL_SCHEMA}.workspace_nodes.tenant_id",
            ],
            name="node_attestations_node_tenant_fk",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "nonce_digest", name="node_attestations_nonce_uq"),
    )

    id: Mapped[str] = mapped_column(
        _UUID,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    node_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    nonce_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    verifier: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PeerGrant(Base):
    """Explicit node-to-node permission within exactly one Workspace."""

    __tablename__ = "peer_grants"
    __table_args__ = (
        CheckConstraint(
            "source_node_id <> target_node_id",
            name="peer_grants_distinct_nodes_check",
        ),
        CheckConstraint(
            "state IN ('active', 'revoked', 'expired')",
            name="peer_grants_state_check",
        ),
        CheckConstraint(
            "cardinality(actions) BETWEEN 1 AND 32",
            name="peer_grants_actions_check",
        ),
        CheckConstraint("fencing_token >= 1", name="peer_grants_fencing_check"),
        ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.id",
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
            ],
            name="peer_grants_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_node_id", "tenant_id", "workspace_id"],
            [
                f"{GLOBAL_SCHEMA}.workspace_nodes.id",
                f"{GLOBAL_SCHEMA}.workspace_nodes.tenant_id",
                f"{GLOBAL_SCHEMA}.workspace_nodes.workspace_id",
            ],
            name="peer_grants_source_tenant_fk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["target_node_id", "tenant_id", "workspace_id"],
            [
                f"{GLOBAL_SCHEMA}.workspace_nodes.id",
                f"{GLOBAL_SCHEMA}.workspace_nodes.tenant_id",
                f"{GLOBAL_SCHEMA}.workspace_nodes.workspace_id",
            ],
            name="peer_grants_target_tenant_fk",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "id", name="peer_grants_id_tenant_uq"),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "id",
            name="peer_grants_id_workspace_tenant_uq",
        ),
        Index(
            "peer_grants_workspace_state_idx",
            "tenant_id",
            "workspace_id",
            "state",
            "expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        _UUID,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    source_node_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    target_node_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    actions: Mapped[list[str]] = mapped_column(ARRAY(String(100)), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'active'"),
    )
    fencing_token: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("1"),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class ServiceAdvertisement(Base):
    """Logical service publication without host/IP/provider locator exposure."""

    __tablename__ = "service_advertisements"
    __table_args__ = (
        CheckConstraint(
            "protocol IN ('https', 'git', 'artifact', 'event')",
            name="service_advertisements_protocol_check",
        ),
        CheckConstraint(
            "state IN ('active', 'revoked', 'expired')",
            name="service_advertisements_state_check",
        ),
        CheckConstraint("logical_port BETWEEN 1 AND 65535", name="service_port_check"),
        CheckConstraint("generation >= 1", name="service_generation_check"),
        ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.id",
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
            ],
            name="service_advertisements_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["node_id", "tenant_id", "workspace_id"],
            [
                f"{GLOBAL_SCHEMA}.workspace_nodes.id",
                f"{GLOBAL_SCHEMA}.workspace_nodes.tenant_id",
                f"{GLOBAL_SCHEMA}.workspace_nodes.workspace_id",
            ],
            name="service_advertisements_node_tenant_fk",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "id", name="service_advertisements_id_tenant_uq"),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "id",
            name="service_advertisements_id_workspace_tenant_uq",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "node_id",
            "service_key",
            "generation",
            name="service_advertisements_generation_uq",
        ),
    )

    id: Mapped[str] = mapped_column(
        _UUID,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    node_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    service_key: Mapped[str] = mapped_column(String(100), nullable=False)
    protocol: Mapped[str] = mapped_column(String(16), nullable=False)
    logical_port: Mapped[int] = mapped_column(Integer, nullable=False)
    actions: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)),
        nullable=False,
        server_default=_EMPTY_ARRAY,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'active'"),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class NetworkLeaseCursor(Base):
    """Monotonic logical fencing allocator for one peer/service/requester tuple."""

    __tablename__ = "network_lease_cursors"
    __table_args__ = (
        CheckConstraint(
            "next_fencing_token >= 1",
            name="network_lease_cursors_next_fencing_check",
        ),
        CheckConstraint(
            "current_fencing_token IS NULL OR current_fencing_token >= 1",
            name="network_lease_cursors_current_fencing_check",
        ),
        CheckConstraint("version >= 1", name="network_lease_cursors_version_check"),
        ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.id",
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
            ],
            name="network_lease_cursors_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["peer_grant_id", "tenant_id", "workspace_id"],
            [
                f"{GLOBAL_SCHEMA}.peer_grants.id",
                f"{GLOBAL_SCHEMA}.peer_grants.tenant_id",
                f"{GLOBAL_SCHEMA}.peer_grants.workspace_id",
            ],
            name="network_lease_cursors_peer_tenant_fk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["service_id", "tenant_id", "workspace_id"],
            [
                f"{GLOBAL_SCHEMA}.service_advertisements.id",
                f"{GLOBAL_SCHEMA}.service_advertisements.tenant_id",
                f"{GLOBAL_SCHEMA}.service_advertisements.workspace_id",
            ],
            name="network_lease_cursors_service_tenant_fk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["requester_node_id", "tenant_id", "workspace_id"],
            [
                f"{GLOBAL_SCHEMA}.workspace_nodes.id",
                f"{GLOBAL_SCHEMA}.workspace_nodes.tenant_id",
                f"{GLOBAL_SCHEMA}.workspace_nodes.workspace_id",
            ],
            name="network_lease_cursors_requester_tenant_fk",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "peer_grant_id",
            "service_id",
            "requester_node_id",
            name="network_lease_cursors_tuple_uq",
        ),
    )

    id: Mapped[str] = mapped_column(
        _UUID,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    peer_grant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    service_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    requester_node_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    next_fencing_token: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("1"),
    )
    current_fencing_token: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class NetworkLease(Base):
    """Short-lived authorization to one logical service over an overlay provider."""

    __tablename__ = "network_leases"
    __table_args__ = (
        CheckConstraint(
            "state IN ('active', 'revoked', 'expired', 'consumed')",
            name="network_leases_state_check",
        ),
        CheckConstraint("fencing_token >= 1", name="network_leases_fencing_check"),
        ForeignKeyConstraint(
            ["peer_grant_id", "tenant_id", "workspace_id"],
            [
                f"{GLOBAL_SCHEMA}.peer_grants.id",
                f"{GLOBAL_SCHEMA}.peer_grants.tenant_id",
                f"{GLOBAL_SCHEMA}.peer_grants.workspace_id",
            ],
            name="network_leases_peer_tenant_fk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["service_id", "tenant_id", "workspace_id"],
            [
                f"{GLOBAL_SCHEMA}.service_advertisements.id",
                f"{GLOBAL_SCHEMA}.service_advertisements.tenant_id",
                f"{GLOBAL_SCHEMA}.service_advertisements.workspace_id",
            ],
            name="network_leases_service_tenant_fk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.id",
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
            ],
            name="network_leases_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["requester_node_id", "tenant_id", "workspace_id"],
            [
                f"{GLOBAL_SCHEMA}.workspace_nodes.id",
                f"{GLOBAL_SCHEMA}.workspace_nodes.tenant_id",
                f"{GLOBAL_SCHEMA}.workspace_nodes.workspace_id",
            ],
            name="network_leases_requester_tenant_fk",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "peer_grant_id",
            "service_id",
            "fencing_token",
            name="network_leases_fencing_uq",
        ),
        Index(
            "network_leases_expiry_idx",
            "tenant_id",
            "state",
            "expires_at",
        ),
        Index(
            "network_leases_one_active_uq",
            "tenant_id",
            "peer_grant_id",
            "service_id",
            "requester_node_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
    )

    id: Mapped[str] = mapped_column(
        _UUID,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    peer_grant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    service_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    requester_node_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'active'"),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class WorkspaceAuthority(Base):
    """Single canonical writer epoch for synthetic P34.4 collaboration."""

    __tablename__ = "workspace_authorities"
    __table_args__ = (
        CheckConstraint("epoch >= 1", name="workspace_authorities_epoch_check"),
        CheckConstraint(
            "state IN ('active', 'offline', 'revoked')",
            name="workspace_authorities_state_check",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.id",
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
            ],
            name="workspace_authorities_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["authority_node_id", "tenant_id", "workspace_id"],
            [
                f"{GLOBAL_SCHEMA}.workspace_nodes.id",
                f"{GLOBAL_SCHEMA}.workspace_nodes.tenant_id",
                f"{GLOBAL_SCHEMA}.workspace_nodes.workspace_id",
            ],
            name="workspace_authorities_node_tenant_fk",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "epoch",
            name="workspace_authorities_epoch_uq",
        ),
        Index(
            "workspace_authorities_current_idx",
            "tenant_id",
            "workspace_id",
            "state",
            "epoch",
        ),
        Index(
            "workspace_authorities_one_active_uq",
            "tenant_id",
            "workspace_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
    )

    id: Mapped[str] = mapped_column(
        _UUID,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    authority_node_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class CollaborationArtifact(Base):
    """Content-addressed synthetic artifact metadata; no canonical data payload."""

    __tablename__ = "collaboration_artifacts"
    __table_args__ = (
        CheckConstraint(
            "content_digest ~ '^[0-9a-f]{64}$'",
            name="collaboration_artifacts_digest_check",
        ),
        CheckConstraint("size_bytes >= 0", name="collaboration_artifacts_size_check"),
        CheckConstraint(
            "state IN ('available', 'revoked')",
            name="collaboration_artifacts_state_check",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.id",
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
            ],
            name="collaboration_artifacts_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["created_by_node_id", "tenant_id", "workspace_id"],
            [
                f"{GLOBAL_SCHEMA}.workspace_nodes.id",
                f"{GLOBAL_SCHEMA}.workspace_nodes.tenant_id",
                f"{GLOBAL_SCHEMA}.workspace_nodes.workspace_id",
            ],
            name="collaboration_artifacts_node_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "content_digest",
            name="collaboration_artifacts_digest_uq",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "id",
            name="collaboration_artifacts_id_workspace_tenant_uq",
        ),
    )

    id: Mapped[str] = mapped_column(
        _UUID,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    authority_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    artifact_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=_EMPTY_JSON,
    )
    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'available'"),
    )
    created_by_node_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class CollaborationEvent(Base):
    """Append-only synthetic Git/artifact/event journal serialized by authority."""

    __tablename__ = "collaboration_events"
    __table_args__ = (
        CheckConstraint("sequence >= 1", name="collaboration_events_sequence_check"),
        CheckConstraint("authority_epoch >= 1", name="collaboration_events_epoch_check"),
        CheckConstraint(
            "event_type IN ('git_ref', 'artifact_published', 'draft_promoted')",
            name="collaboration_events_type_check",
        ),
        CheckConstraint(
            "event_digest ~ '^[0-9a-f]{64}$'",
            name="collaboration_events_digest_check",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.id",
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
            ],
            name="collaboration_events_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["authority_node_id", "tenant_id", "workspace_id"],
            [
                f"{GLOBAL_SCHEMA}.workspace_nodes.id",
                f"{GLOBAL_SCHEMA}.workspace_nodes.tenant_id",
                f"{GLOBAL_SCHEMA}.workspace_nodes.workspace_id",
            ],
            name="collaboration_events_node_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["artifact_id", "tenant_id", "workspace_id"],
            [
                f"{GLOBAL_SCHEMA}.collaboration_artifacts.id",
                f"{GLOBAL_SCHEMA}.collaboration_artifacts.tenant_id",
                f"{GLOBAL_SCHEMA}.collaboration_artifacts.workspace_id",
            ],
            name="collaboration_events_artifact_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["parent_event_id", "tenant_id", "workspace_id"],
            [
                f"{GLOBAL_SCHEMA}.collaboration_events.id",
                f"{GLOBAL_SCHEMA}.collaboration_events.tenant_id",
                f"{GLOBAL_SCHEMA}.collaboration_events.workspace_id",
            ],
            name="collaboration_events_parent_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "sequence",
            name="collaboration_events_sequence_uq",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "event_digest",
            name="collaboration_events_digest_uq",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "id",
            name="collaboration_events_id_workspace_tenant_uq",
        ),
    )

    id: Mapped[str] = mapped_column(
        _UUID,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    authority_node_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    authority_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    event_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    parent_event_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    event_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=_EMPTY_JSON,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


__all__ = [
    "CollaborationArtifact",
    "CollaborationEvent",
    "NetworkLease",
    "NetworkLeaseCursor",
    "NodeAttestation",
    "PeerGrant",
    "ResourceScopeBinding",
    "RunLease",
    "ServiceAdvertisement",
    "Workspace",
    "WorkspaceAuthority",
    "WorkspaceMembership",
    "WorkspaceNode",
    "WorkspaceRun",
    "WorkspaceScopeGrant",
    "WorkspaceSnapshot",
    "WorkspaceTemplate",
]
