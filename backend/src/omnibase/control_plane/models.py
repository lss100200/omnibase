"""Global persistence models for the Phase 3-4 control plane.

These records live exclusively in ``omnibase_meta``. They contain logical
identifiers and policy/audit state; tenant-schema users, workspaces, runs, and
other actors are intentionally not represented by physical foreign keys.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnibase.db.models import GLOBAL_SCHEMA, Base

_UUID = UUID(as_uuid=False)
_EMPTY_JSON = text("'{}'::jsonb")


class ResourceRecord(Base):
    """Logical resource registry entry.

    ``physical_locator`` is adapter-internal and deliberately excluded from
    ``repr`` so logs and debugging output cannot disclose physical locations.
    """

    __tablename__ = "resource_registry"
    __table_args__ = (
        CheckConstraint(
            "kind ~ '^[a-z][a-z0-9_]{1,63}$'",
            name="resource_registry_kind_check",
        ),
        CheckConstraint(
            "owner_type IN ('user', 'workspace', 'agent', 'system')",
            name="resource_registry_owner_type_check",
        ),
        CheckConstraint(
            "(owner_type = 'system' AND owner_id IS NULL) "
            "OR (owner_type IN ('user', 'workspace', 'agent') "
            "AND owner_id IS NOT NULL)",
            name="resource_registry_owner_identity_check",
        ),
        CheckConstraint(
            "state IN ('active', 'provisioning', 'stopped', 'starting', 'running', "
            "'pausing', 'paused', 'snapshotting', 'stopping', 'archiving', "
            "'archived', 'purge_pending', 'purged', 'failed')",
            name="resource_registry_state_check",
        ),
        CheckConstraint(
            "policy_class IN ('system_internal', 'canonical_readonly', "
            "'tenant_managed', 'controlled_shared', 'workspace_private', "
            "'workspace_derived')",
            name="resource_registry_policy_class_check",
        ),
        CheckConstraint("version >= 1", name="resource_registry_version_check"),
        Index(
            "resource_registry_tenant_kind_state_idx",
            "tenant_id",
            "kind",
            "state",
        ),
        Index(
            "resource_registry_tenant_owner_idx",
            "tenant_id",
            "owner_type",
            "owner_id",
        ),
        Index(
            "resource_registry_tenant_parent_idx",
            "tenant_id",
            "parent_id",
        ),
        Index(
            "resource_registry_tenant_policy_idx",
            "tenant_id",
            "policy_class",
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
    kind: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Application-registered logical kind; DB enforces only safe naming",
    )
    owner_type: Mapped[str] = mapped_column(String(20), nullable=False)
    owner_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    parent_id: Mapped[str | None] = mapped_column(
        _UUID,
        ForeignKey(f"{GLOBAL_SCHEMA}.resource_registry.id", ondelete="SET NULL"),
        nullable=True,
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'active'"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    policy_class: Mapped[str] = mapped_column(String(32), nullable=False)
    physical_locator: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Adapter-internal locator; never expose through public DTOs or logs",
    )
    resource_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=_EMPTY_JSON,
    )
    created_by_actor_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
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

    def __repr__(self) -> str:
        return (
            f"<ResourceRecord id={self.id} tenant_id={self.tenant_id} "
            f"kind={self.kind!r} state={self.state!r}>"
        )


class ResourceLineage(Base):
    """Append-only logical relationship between source and derived resources."""

    __tablename__ = "resource_lineage"
    __table_args__ = (
        CheckConstraint(
            "relation IN ('derived_from', 'transformed_from', 'snapshot_of', "
            "'restored_from', 'published_from')",
            name="resource_lineage_relation_check",
        ),
        CheckConstraint(
            "source_version >= 1",
            name="resource_lineage_source_version_check",
        ),
        CheckConstraint(
            "source_resource_id <> derived_resource_id",
            name="resource_lineage_distinct_resources_check",
        ),
        UniqueConstraint(
            "tenant_id",
            "source_resource_id",
            "derived_resource_id",
            "relation",
            name="resource_lineage_edge_uq",
        ),
        Index(
            "resource_lineage_tenant_source_idx",
            "tenant_id",
            "source_resource_id",
        ),
        Index(
            "resource_lineage_tenant_derived_idx",
            "tenant_id",
            "derived_resource_id",
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
    source_resource_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    derived_resource_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    transform_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by_operation_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class AuditEvent(Base):
    """Append-only, security-sensitive control-plane audit event."""

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('user', 'workspace', 'run', 'agent', 'system')",
            name="audit_events_actor_type_check",
        ),
        CheckConstraint(
            "decision IN ('allowed', 'denied', 'error')",
            name="audit_events_decision_check",
        ),
        CheckConstraint(
            "risk_level IN ('R0', 'R1', 'R2', 'R3', 'R4')",
            name="audit_events_risk_level_check",
        ),
        CheckConstraint(
            "status_code IS NULL OR (status_code >= 100 AND status_code <= 599)",
            name="audit_events_status_code_check",
        ),
        CheckConstraint(
            "before_version IS NULL OR before_version >= 1",
            name="audit_events_before_version_check",
        ),
        CheckConstraint(
            "after_version IS NULL OR after_version >= 1",
            name="audit_events_after_version_check",
        ),
        CheckConstraint(
            "row_count IS NULL OR row_count >= 0",
            name="audit_events_row_count_check",
        ),
        CheckConstraint(
            "bytes_in IS NULL OR bytes_in >= 0",
            name="audit_events_bytes_in_check",
        ),
        CheckConstraint(
            "bytes_out IS NULL OR bytes_out >= 0",
            name="audit_events_bytes_out_check",
        ),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="audit_events_duration_check",
        ),
        Index("audit_events_tenant_created_idx", "tenant_id", "created_at"),
        Index(
            "audit_events_tenant_actor_idx",
            "tenant_id",
            "actor_type",
            "actor_id",
            "created_at",
        ),
        Index(
            "audit_events_tenant_resource_idx",
            "tenant_id",
            "resource_id",
            "created_at",
        ),
        Index("audit_events_request_id_idx", "request_id"),
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
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    run_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    grant_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    resource_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    approval_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    operation_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(2), nullable=False)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    before_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    after_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    row_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bytes_in: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bytes_out: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    details: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=_EMPTY_JSON,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class OperationRecord(Base):
    """Durable state for bounded control-plane operations."""

    __tablename__ = "operations"
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('user', 'workspace', 'agent', 'system')",
            name="operations_actor_type_check",
        ),
        CheckConstraint(
            "state IN ('pending_approval', 'queued', 'running', 'cancelling', "
            "'succeeded', 'failed', 'cancelled', 'compensating', 'compensated')",
            name="operations_state_check",
        ),
        CheckConstraint(
            "risk_level IN ('R0', 'R1', 'R2', 'R3', 'R4')",
            name="operations_risk_level_check",
        ),
        CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="operations_progress_check",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="operations_attempt_count_check",
        ),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="operations_request_hash_check",
        ),
        CheckConstraint(
            "resource_version IS NULL OR resource_version >= 1",
            name="operations_resource_version_check",
        ),
        CheckConstraint(
            "risk_level NOT IN ('R2', 'R3', 'R4') "
            "OR state IN ('pending_approval', 'failed', 'cancelled') "
            "OR approval_id IS NOT NULL",
            name="operations_high_risk_approval_check",
        ),
        CheckConstraint("version >= 1", name="operations_version_check"),
        Index(
            "operations_tenant_state_created_idx",
            "tenant_id",
            "state",
            "created_at",
        ),
        Index(
            "operations_tenant_workspace_created_idx",
            "tenant_id",
            "workspace_id",
            "created_at",
        ),
        Index(
            "operations_tenant_resource_created_idx",
            "tenant_id",
            "resource_id",
            "created_at",
        ),
        Index(
            "operations_tenant_request_hash_idx",
            "tenant_id",
            "request_hash",
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
    workspace_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    run_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    resource_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    resource_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approval_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    request_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Immutable lowercase SHA-256 digest of the authorized request",
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'queued'"),
    )
    risk_level: Mapped[str] = mapped_column(String(2), nullable=False)
    progress: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_ref: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    operation_metadata: Mapped[dict[str, object]] = mapped_column(
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class ApprovalRequest(Base):
    """Human approval bound to an exact action, resource version, and request hash."""

    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(
            "requester_type IN ('user', 'workspace', 'run', 'agent', 'system')",
            name="approval_requests_requester_type_check",
        ),
        CheckConstraint(
            "requester_type = 'system' OR requester_id IS NOT NULL",
            name="approval_requests_requester_identity_check",
        ),
        CheckConstraint(
            "state IN ('draft', 'pending', 'approved', 'rejected', 'expired', "
            "'cancelled', 'consumed')",
            name="approval_requests_state_check",
        ),
        CheckConstraint(
            "risk_level IN ('R0', 'R1', 'R2', 'R3', 'R4')",
            name="approval_requests_risk_level_check",
        ),
        CheckConstraint(
            "resource_version IS NULL OR resource_version >= 1",
            name="approval_requests_resource_version_check",
        ),
        CheckConstraint(
            "decided_by_actor_type IS NULL " "OR decided_by_actor_type IN ('user', 'system')",
            name="approval_requests_decider_type_check",
        ),
        CheckConstraint(
            "(decided_by_actor_type IS NULL AND decided_by_actor_id IS NULL) "
            "OR (decided_by_actor_type IS NOT NULL "
            "AND decided_by_actor_id IS NOT NULL)",
            name="approval_requests_decider_identity_pair_check",
        ),
        CheckConstraint(
            "state NOT IN ('approved', 'rejected', 'consumed') "
            "OR (decided_by_actor_type IS NOT NULL "
            "AND decided_by_actor_id IS NOT NULL)",
            name="approval_requests_decided_state_identity_check",
        ),
        CheckConstraint(
            "required_approver_role IN ('tenant_admin', 'platform_admin')",
            name="approval_requests_required_role_check",
        ),
        CheckConstraint(
            "(risk_level = 'R4' AND required_approver_role = 'platform_admin') "
            "OR (risk_level IN ('R0', 'R1', 'R2', 'R3') "
            "AND required_approver_role = 'tenant_admin')",
            name="approval_requests_risk_role_check",
        ),
        CheckConstraint(
            "state NOT IN ('pending', 'approved', 'rejected', 'consumed') "
            "OR grant_id IS NOT NULL",
            name="approval_requests_committed_grant_check",
        ),
        CheckConstraint(
            "risk_level NOT IN ('R2', 'R3', 'R4') "
            "OR state NOT IN ('pending', 'approved', 'rejected', 'consumed') "
            "OR operation_id IS NOT NULL",
            name="approval_requests_high_risk_operation_check",
        ),
        CheckConstraint("version >= 1", name="approval_requests_version_check"),
        Index(
            "approval_requests_tenant_state_created_idx",
            "tenant_id",
            "state",
            "created_at",
        ),
        Index(
            "approval_requests_tenant_requester_idx",
            "tenant_id",
            "requester_type",
            "requester_id",
            "created_at",
        ),
        Index(
            "approval_requests_tenant_resource_idx",
            "tenant_id",
            "resource_id",
            "created_at",
        ),
        Index(
            "approval_requests_tenant_role_state_created_idx",
            "tenant_id",
            "required_approver_role",
            "state",
            "created_at",
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
    requester_type: Mapped[str] = mapped_column(String(20), nullable=False)
    requester_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    run_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    resource_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    operation_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    grant_id: Mapped[str | None] = mapped_column(
        _UUID,
        nullable=True,
        comment="Capability grant bound when the approval is created; immutable thereafter",
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(2), nullable=False)
    required_approver_role: Mapped[str] = mapped_column(String(20), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'draft'"),
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    decided_by_actor_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    decided_by_actor_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_metadata: Mapped[dict[str, object]] = mapped_column(
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class IdempotencyRecord(Base):
    """Tenant-scoped mutation replay record."""

    __tablename__ = "idempotency_records"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'completed', 'failed')",
            name="idempotency_records_state_check",
        ),
        CheckConstraint("version >= 1", name="idempotency_records_version_check"),
        UniqueConstraint(
            "tenant_id",
            "actor_scope",
            "operation_name",
            "key",
            name="idempotency_records_scope_key_uq",
        ),
        Index(
            "idempotency_records_tenant_state_created_idx",
            "tenant_id",
            "state",
            "created_at",
        ),
        Index(
            "idempotency_records_tenant_expires_idx",
            "tenant_id",
            "expires_at",
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
    actor_scope: Mapped[str] = mapped_column(String(128), nullable=False)
    operation_name: Mapped[str] = mapped_column(String(100), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'pending'"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    response_ref: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    operation_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
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


__all__ = [
    "ApprovalRequest",
    "AuditEvent",
    "IdempotencyRecord",
    "OperationRecord",
    "ResourceLineage",
    "ResourceRecord",
]
