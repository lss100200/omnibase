"""Global persistence models for the P34.2 capability ledger.

The capability plane stores only logical identifiers, public verification
keys, grants, counters, and revocations.  Private signing keys, physical
resource locators, credentials, SQL, and content never belong in these rows.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnibase.db.models import GLOBAL_SCHEMA, Base

_UUID = UUID(as_uuid=False)
_DEFAULT_CONSTRAINTS = text("'{\"timeout_ms\": 2000}'::jsonb")


class CapabilitySigningKey(Base):
    """Server-managed public verification key registry.

    Private key material is deliberately absent.  The issuer receives private
    key material from its secret provider and proves it matches this record.
    """

    __tablename__ = "capability_signing_keys"
    __table_args__ = (
        CheckConstraint(
            "algorithm = 'RS256'",
            name="capability_signing_keys_algorithm_check",
        ),
        CheckConstraint(
            "state IN ('active', 'retired', 'revoked')",
            name="capability_signing_keys_state_check",
        ),
        CheckConstraint(
            "kid ~ '^[A-Za-z0-9._-]{8,64}$'",
            name="capability_signing_keys_kid_check",
        ),
        CheckConstraint(
            "public_key_sha256 ~ '^[0-9a-f]{64}$'",
            name="capability_signing_keys_fingerprint_check",
        ),
        CheckConstraint(
            "public_key_pem LIKE '-----BEGIN PUBLIC KEY-----%'",
            name="capability_signing_keys_public_pem_check",
        ),
        CheckConstraint(
            "expires_at > not_before",
            name="capability_signing_keys_window_check",
        ),
        Index("capability_signing_keys_state_window_idx", "state", "not_before", "expires_at"),
        {"comment": "P34.2 server-side verification keys; public material only"},
    )

    kid: Mapped[str] = mapped_column(String(64), primary_key=True)
    algorithm: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'RS256'")
    )
    public_key_pem: Mapped[str] = mapped_column(Text, nullable=False)
    public_key_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CapabilityGrant(Base):
    """Online authority for a short-lived, workload-bound capability token."""

    __tablename__ = "capability_grants"
    __table_args__ = (
        CheckConstraint(
            "state IN ('active', 'revoked', 'expired')",
            name="capability_grants_state_check",
        ),
        CheckConstraint(
            "cardinality(actions) > 0 AND ((actions <@ ARRAY["
            "'data.schema.read', 'data.rows.read', 'rag.search', "
            "'rag.citation.read']::varchar[] AND workload_identity_digest IS NULL) "
            "OR (actions <@ ARRAY["
            "'sandbox.prepare', 'sandbox.create', 'sandbox.start', "
            "'sandbox.exec', 'sandbox.cancel', 'sandbox.logs', "
            "'sandbox.stats', 'sandbox.snapshot', 'sandbox.restore', "
            "'sandbox.stop', 'sandbox.destroy']::varchar[] AND "
            "workload_identity_digest IS NOT NULL AND "
            "workload_identity_digest ~ '^[0-9a-f]{64}$' AND "
            "cardinality(resource_ids) = 1 AND delegation_depth = 0 AND "
            "delegation_depth_limit = 0))",
            name="capability_grants_action_profile_check",
        ),
        CheckConstraint(
            "cardinality(resource_ids) > 0",
            name="capability_grants_resources_check",
        ),
        CheckConstraint("version >= 1", name="capability_grants_version_check"),
        CheckConstraint(
            "delegation_depth >= 0 AND delegation_depth_limit >= delegation_depth "
            "AND delegation_depth_limit <= 8",
            name="capability_grants_delegation_depth_check",
        ),
        CheckConstraint(
            "approval_id IS NULL",
            name="capability_grants_p34_2_no_approval_check",
        ),
        CheckConstraint(
            "max_calls > 0 AND max_bytes > 0 AND max_cost_units > 0",
            name="capability_grants_budget_check",
        ),
        CheckConstraint(
            "expires_at > not_before",
            name="capability_grants_window_check",
        ),
        CheckConstraint(
            "created_by_actor_type = 'system' AND created_by_actor_id IS NOT NULL",
            name="capability_grants_trusted_issuer_check",
        ),
        CheckConstraint(
            "(delegation_depth = 0 AND parent_grant_id IS NULL) OR "
            "(delegation_depth > 0 AND parent_grant_id IS NOT NULL)",
            name="capability_grants_parent_depth_check",
        ),
        CheckConstraint(
            "(state = 'revoked' AND revoked_at IS NOT NULL) OR "
            "(state <> 'revoked' AND revoked_at IS NULL)",
            name="capability_grants_revoked_at_check",
        ),
        CheckConstraint(
            "jsonb_typeof(constraints) = 'object'",
            name="capability_grants_constraints_object_check",
        ),
        CheckConstraint(
            "constraints ? 'timeout_ms' AND "
            "jsonb_typeof(constraints -> 'timeout_ms') = 'number' AND "
            "(constraints ->> 'timeout_ms')::numeric = "
            "trunc((constraints ->> 'timeout_ms')::numeric) AND "
            "(constraints ->> 'timeout_ms')::numeric BETWEEN 1 AND 5000",
            name="capability_grants_timeout_constraint_check",
        ),
        UniqueConstraint("id", "tenant_id", name="capability_grants_id_tenant_uq"),
        ForeignKeyConstraint(
            ["parent_grant_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.capability_grants.id",
                f"{GLOBAL_SCHEMA}.capability_grants.tenant_id",
            ],
            name="capability_grants_parent_tenant_fk",
            ondelete="RESTRICT",
        ),
        Index("capability_grants_tenant_workspace_idx", "tenant_id", "workspace_id", "state"),
        Index("capability_grants_runtime_idx", "runtime_instance_id", "state", "expires_at"),
        Index("capability_grants_parent_idx", "parent_grant_id"),
        {"comment": "P34.2 online capability authority; logical IDs only"},
    )

    id: Mapped[str] = mapped_column(
        _UUID, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(
        _UUID,
        ForeignKey(f"{GLOBAL_SCHEMA}.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    runtime_instance_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workload_identity_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_user_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    parent_grant_id: Mapped[str | None] = mapped_column(
        _UUID,
        nullable=True,
    )
    actions: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False)
    resource_ids: Mapped[list[str]] = mapped_column(ARRAY(_UUID), nullable=False)
    constraints: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=_DEFAULT_CONSTRAINTS
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    max_calls: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_cost_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delegation_depth: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    delegation_depth_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    approval_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    created_by_actor_type: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'system'")
    )
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
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CapabilityUsage(Base):
    """One atomic aggregate budget row per grant."""

    __tablename__ = "capability_usage"
    __table_args__ = (
        CheckConstraint(
            "calls >= 0 AND bytes_in >= 0 AND bytes_out >= 0 AND cost_units >= 0",
            name="capability_usage_nonnegative_check",
        ),
        ForeignKeyConstraint(
            ["grant_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.capability_grants.id",
                f"{GLOBAL_SCHEMA}.capability_grants.tenant_id",
            ],
            name="capability_usage_grant_tenant_fk",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "grant_id", name="capability_usage_tenant_grant_uq"),
        Index("capability_usage_tenant_updated_idx", "tenant_id", "updated_at"),
    )

    grant_id: Mapped[str] = mapped_column(
        _UUID,
        primary_key=True,
    )
    tenant_id: Mapped[str] = mapped_column(
        _UUID,
        ForeignKey(f"{GLOBAL_SCHEMA}.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    calls: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    bytes_in: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    bytes_out: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    cost_units: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class CapabilityUsageReservation(Base):
    """Append-only idempotent budget reservation for one Sandbox operation."""

    __tablename__ = "capability_usage_reservations"
    __table_args__ = (
        CheckConstraint(
            "action IN ('sandbox.prepare', 'sandbox.create', 'sandbox.start', "
            "'sandbox.exec', 'sandbox.cancel', 'sandbox.logs', 'sandbox.stats', "
            "'sandbox.snapshot', 'sandbox.restore', 'sandbox.stop', 'sandbox.destroy')",
            name="capability_usage_reservations_action_check",
        ),
        CheckConstraint(
            "calls = 1 AND cost_units = 1",
            name="capability_usage_reservations_budget_check",
        ),
        ForeignKeyConstraint(
            ["grant_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.capability_grants.id",
                f"{GLOBAL_SCHEMA}.capability_grants.tenant_id",
            ],
            name="capability_usage_reservations_grant_tenant_fk",
            ondelete="RESTRICT",
        ),
        Index(
            "capability_usage_reservations_tenant_grant_created_idx",
            "tenant_id",
            "grant_id",
            "created_at",
        ),
        {"comment": "P34.5 append-only idempotent capability budget reservations"},
    )

    operation_id: Mapped[str] = mapped_column(_UUID, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        _UUID,
        ForeignKey(f"{GLOBAL_SCHEMA}.tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    grant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    runtime_instance_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    calls: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    cost_units: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("1"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class CapabilityRevocation(Base):
    """Append-only online deny record for an entire grant or one token JTI."""

    __tablename__ = "capability_revocations"
    __table_args__ = (
        CheckConstraint(
            "token_jti IS NULL OR token_jti ~ '^[A-Za-z0-9._-]{16,128}$'",
            name="capability_revocations_jti_check",
        ),
        CheckConstraint(
            "reason_code ~ '^[a-z][a-z0-9_.:-]{1,63}$'",
            name="capability_revocations_reason_check",
        ),
        CheckConstraint(
            "actor_type IN ('user', 'system') AND actor_id IS NOT NULL",
            name="capability_revocations_actor_check",
        ),
        ForeignKeyConstraint(
            ["grant_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.capability_grants.id",
                f"{GLOBAL_SCHEMA}.capability_grants.tenant_id",
            ],
            name="capability_revocations_grant_tenant_fk",
            ondelete="RESTRICT",
        ),
        Index(
            "capability_revocations_grant_wide_uq",
            "grant_id",
            unique=True,
            postgresql_where=text("token_jti IS NULL"),
        ),
        Index(
            "capability_revocations_grant_jti_uq",
            "grant_id",
            "token_jti",
            unique=True,
            postgresql_where=text("token_jti IS NOT NULL"),
        ),
        Index("capability_revocations_tenant_created_idx", "tenant_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        _UUID, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(
        _UUID,
        ForeignKey(f"{GLOBAL_SCHEMA}.tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    grant_id: Mapped[str] = mapped_column(
        _UUID,
        nullable=False,
    )
    token_jti: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


__all__ = [
    "CapabilityGrant",
    "CapabilityRevocation",
    "CapabilitySigningKey",
    "CapabilityUsage",
    "CapabilityUsageReservation",
]
