"""Tenant-scoped operation payload storage for P34.3 controlled data."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnibase.db.tenant import TenantBase


class ControlledDataOperationPayload(TenantBase):
    """Sensitive normalized mutation payload kept inside one tenant schema."""

    __tablename__ = "controlled_data_operation_payloads"
    __table_args__ = (
        CheckConstraint(
            "payload_kind IN ('crud_mutation', 'schema_change', 'compensation')",
            name="controlled_data_operation_payloads_kind_check",
        ),
        CheckConstraint(
            "state IN ('pending', 'claimed', 'applied', 'compensated', 'discarded')",
            name="controlled_data_operation_payloads_state_check",
        ),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="controlled_data_operation_payloads_request_hash_check",
        ),
        CheckConstraint(
            "jsonb_typeof(normalized_payload) = 'object' "
            "AND NOT (normalized_payload ? 'sql') "
            "AND NOT (normalized_payload ? 'raw_sql')",
            name="controlled_data_operation_payloads_no_sql_check",
        ),
        Index(
            "controlled_data_operation_payloads_operation_idx",
            "operation_id",
            "created_at",
        ),
        Index(
            "controlled_data_operation_payloads_state_expiry_idx",
            "state",
            "expires_at",
        ),
        {"comment": "Tenant-private normalized payload; never exposed through public DTOs"},
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    operation_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    plan_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    payload_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    normalized_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["ControlledDataOperationPayload"]
