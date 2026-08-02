"""Safe gateway audit sink; request bodies and physical locators are excluded."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

from omnibase.capability_gateway.contracts import VerifiedCapability
from omnibase.control_plane.service import append_audit_event


@dataclass(frozen=True)
class GatewayAuditRecord:
    request_id: str
    action: str
    decision: str
    status_code: int
    input_hash: str
    resource_id: str
    reason_code: str
    duration_ms: int
    bytes_in: int
    bytes_out: int | None = None
    row_count: int | None = None
    risk_level: str = "R0"
    operation_id: str | None = None


@runtime_checkable
class GatewayAuditSink(Protocol):
    def append(
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        record: GatewayAuditRecord,
    ) -> None: ...


class ControlPlaneGatewayAuditSink:
    def append(
        self,
        session: Session,
        *,
        capability: VerifiedCapability,
        record: GatewayAuditRecord,
    ) -> None:
        append_audit_event(
            session,
            tenant_id=capability.tenant_id,
            request_id=record.request_id,
            actor_type="workspace",
            actor_id=capability.workspace_id,
            workspace_id=capability.workspace_id,
            # runtime_instance_id is not a logical run_id and must not be
            # written into that foreign-reference slot.
            run_id=None,
            grant_id=capability.grant_id,
            resource_id=record.resource_id,
            operation_id=record.operation_id,
            action=record.action,
            decision=record.decision,
            risk_level=record.risk_level,
            input_hash=record.input_hash,
            status_code=record.status_code,
            row_count=record.row_count,
            bytes_in=record.bytes_in,
            bytes_out=record.bytes_out,
            duration_ms=record.duration_ms,
            details={"reason_code": record.reason_code},
        )


__all__ = ["ControlPlaneGatewayAuditSink", "GatewayAuditRecord", "GatewayAuditSink"]
