"""Public, read-only DTOs for the Phase 3-4 control plane.

The control-plane persistence model contains adapter-internal fields.  Public
schemas intentionally expose logical identifiers only; in particular,
``ResourceRecord.physical_locator`` is never part of an API response.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

ResourceKind = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$", max_length=64),
]
ResourceState = Literal[
    "active",
    "provisioning",
    "stopped",
    "starting",
    "running",
    "pausing",
    "paused",
    "snapshotting",
    "stopping",
    "archiving",
    "archived",
    "purge_pending",
    "purged",
    "failed",
]
OperationState = Literal[
    "pending_approval",
    "queued",
    "running",
    "cancelling",
    "compensating",
    "compensated",
    "succeeded",
    "failed",
    "cancelled",
]
ApprovalState = Literal[
    "draft",
    "pending",
    "approved",
    "rejected",
    "expired",
    "cancelled",
    "consumed",
]
RiskLevel = Literal["R0", "R1", "R2", "R3", "R4"]


class _ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ResourceRead(_ReadModel):
    id: str
    kind: ResourceKind
    owner_type: Literal["user", "workspace", "agent", "system"]
    owner_id: str | None
    parent_id: str | None
    display_name: str
    state: ResourceState
    version: int
    policy_class: Literal[
        "system_internal",
        "canonical_readonly",
        "tenant_managed",
        "controlled_shared",
        "workspace_private",
        "workspace_derived",
    ]
    created_at: datetime
    updated_at: datetime


class ResourceList(BaseModel):
    items: list[ResourceRead]
    total: int


class OperationRead(_ReadModel):
    id: str
    workspace_id: str | None
    run_id: str | None
    actor_type: Literal["user", "workspace", "agent", "system"]
    actor_id: str | None
    resource_id: str | None
    approval_id: str | None
    kind: str
    state: OperationState
    risk_level: RiskLevel
    resource_version: int | None
    progress: int
    attempt_count: int
    version: int
    deadline_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class OperationList(BaseModel):
    items: list[OperationRead]
    total: int


class ApprovalRead(_ReadModel):
    id: str
    requester_type: Literal["user", "workspace", "run", "agent", "system"]
    requester_id: str | None
    workspace_id: str | None
    run_id: str | None
    resource_id: str | None
    operation_id: str | None
    grant_id: str | None
    action: str
    risk_level: RiskLevel
    state: ApprovalState
    resource_version: int | None
    required_approver_role: Literal["tenant_admin", "platform_admin"]
    version: int
    decided_by_actor_type: Literal["user", "system"] | None
    decided_by_actor_id: str | None
    expires_at: datetime
    decided_at: datetime | None
    consumed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApprovalList(BaseModel):
    items: list[ApprovalRead]
    total: int


class AuditEventRead(_ReadModel):
    id: str
    request_id: str
    actor_type: Literal["user", "workspace", "run", "agent", "system"]
    actor_id: str | None
    workspace_id: str | None
    run_id: str | None
    grant_id: str | None
    resource_id: str | None
    approval_id: str | None
    operation_id: str | None
    action: str
    decision: Literal["allowed", "denied", "error"]
    risk_level: RiskLevel
    input_hash: str | None
    before_version: int | None
    after_version: int | None
    status_code: int | None
    row_count: int | None
    bytes_in: int | None
    bytes_out: int | None
    duration_ms: int | None
    created_at: datetime


class ResourceLineageRead(_ReadModel):
    id: str
    source_resource_id: str
    derived_resource_id: str
    relation: Literal[
        "derived_from",
        "transformed_from",
        "snapshot_of",
        "restored_from",
        "published_from",
    ]
    source_version: int
    transform_digest: str | None
    created_by_operation_id: str | None
    created_at: datetime


class ResourceLineageList(BaseModel):
    items: list[ResourceLineageRead]
    total: int


class AuditEventList(BaseModel):
    items: list[AuditEventRead]
    total: int


__all__ = [
    "ApprovalList",
    "ApprovalRead",
    "ApprovalState",
    "AuditEventList",
    "AuditEventRead",
    "OperationList",
    "OperationRead",
    "OperationState",
    "ResourceKind",
    "ResourceLineageList",
    "ResourceLineageRead",
    "ResourceList",
    "ResourceRead",
    "ResourceState",
    "RiskLevel",
]
