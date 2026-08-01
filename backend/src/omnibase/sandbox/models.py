"""Global persistence models for P34.5 durable Sandbox dispatch state."""

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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnibase.db.models import GLOBAL_SCHEMA, Base

_UUID = UUID(as_uuid=False)
_ACTIONS_SQL = (
    "'sandbox.prepare', 'sandbox.create', 'sandbox.start', 'sandbox.exec', "
    "'sandbox.cancel', 'sandbox.logs', 'sandbox.stats', 'sandbox.snapshot', "
    "'sandbox.restore', 'sandbox.stop', 'sandbox.destroy', "
    "'sandbox.control.emergency_stop', 'sandbox.control.emergency_destroy'"
)
_STATES_SQL = (
    "'accepted', 'authorized', 'dispatching', 'succeeded', 'failed', "
    "'ambiguous', 'reconciliation_required', 'reconciled_succeeded', "
    "'reconciled_failed'"
)


class SandboxOperation(Base):
    """Mutable current pointer backed by immutable transition history."""

    __tablename__ = "sandbox_operations"
    __table_args__ = (
        CheckConstraint(f"action IN ({_ACTIONS_SQL})", name="sandbox_operations_action_check"),
        CheckConstraint(f"state IN ({_STATES_SQL})", name="sandbox_operations_state_check"),
        CheckConstraint(
            "request_digest ~ '^[0-9a-f]{64}$'",
            name="sandbox_operations_request_digest_check",
        ),
        CheckConstraint(
            "spec_digest IS NULL OR spec_digest ~ '^[0-9a-f]{64}$'",
            name="sandbox_operations_spec_digest_check",
        ),
        CheckConstraint(
            "version >= 1 AND workspace_generation >= 1 AND "
            "run_fencing_token >= 1 AND node_fencing_token >= 1",
            name="sandbox_operations_version_fencing_check",
        ),
        CheckConstraint(
            "((action IN ('sandbox.control.emergency_stop', "
            "'sandbox.control.emergency_destroy')) "
            "AND capability_grant_id IS NULL) OR "
            "((action NOT IN ('sandbox.control.emergency_stop', "
            "'sandbox.control.emergency_destroy')) "
            "AND capability_grant_id IS NOT NULL)",
            name="sandbox_operations_capability_binding_check",
        ),
        UniqueConstraint("operation_id", "tenant_id", name="sandbox_operations_id_tenant_uq"),
        ForeignKeyConstraint(
            ["capability_grant_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.capability_grants.id",
                f"{GLOBAL_SCHEMA}.capability_grants.tenant_id",
            ],
            name="sandbox_operations_grant_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
                f"{GLOBAL_SCHEMA}.workspaces.id",
            ],
            name="sandbox_operations_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "run_id"],
            [
                f"{GLOBAL_SCHEMA}.workspace_runs.tenant_id",
                f"{GLOBAL_SCHEMA}.workspace_runs.workspace_id",
                f"{GLOBAL_SCHEMA}.workspace_runs.id",
            ],
            name="sandbox_operations_run_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        Index(
            "sandbox_operations_tenant_state_created_idx",
            "tenant_id",
            "state",
            "created_at",
        ),
        Index(
            "sandbox_operations_tenant_run_created_idx",
            "tenant_id",
            "run_id",
            "created_at",
        ),
        {"comment": "P34.5 durable dispatch pointer; transition history is append-only"},
    )

    operation_id: Mapped[str] = mapped_column(_UUID, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        _UUID,
        ForeignKey(f"{GLOBAL_SCHEMA}.tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    run_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    runtime_instance_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    capability_grant_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    spec_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    workspace_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    run_fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    node_fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'accepted'"),
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
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


class SandboxOperationTransitionModel(Base):
    """Append-only transition evidence for a Sandbox operation."""

    __tablename__ = "sandbox_operation_transitions"
    __table_args__ = (
        CheckConstraint(f"state IN ({_STATES_SQL})", name="sandbox_transitions_state_check"),
        CheckConstraint("sequence >= 1", name="sandbox_transitions_sequence_check"),
        CheckConstraint(
            "reason_code ~ '^[a-z][a-z0-9_]{2,99}$'",
            name="sandbox_transitions_reason_check",
        ),
        CheckConstraint(
            "evidence_digest IS NULL OR evidence_digest ~ '^[0-9a-f]{64}$'",
            name="sandbox_transitions_evidence_check",
        ),
        ForeignKeyConstraint(
            ["operation_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.sandbox_operations.operation_id",
                f"{GLOBAL_SCHEMA}.sandbox_operations.tenant_id",
            ],
            name="sandbox_transitions_operation_tenant_fk",
            ondelete="RESTRICT",
        ),
        Index(
            "sandbox_transitions_tenant_recorded_idx",
            "tenant_id",
            "recorded_at",
        ),
        {"comment": "P34.5 append-only dispatch and reconciliation evidence"},
    )

    operation_id: Mapped[str] = mapped_column(_UUID, primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


__all__ = ["SandboxOperation", "SandboxOperationTransitionModel"]
