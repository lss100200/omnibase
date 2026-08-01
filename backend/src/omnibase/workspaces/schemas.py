"""Strict public DTOs for the P34.4 Workspace control plane."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Role = Literal["viewer", "member", "operator", "maintainer", "owner"]
ScopeClass = Literal[
    "user_private",
    "workspace_private",
    "workspace_shared",
    "tenant_shared",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class WorkspaceCreate(StrictModel):
    display_name: DisplayName
    template_id: UUID
    parent_workspace_id: UUID | None = None
    quota: dict[str, int] = Field(default_factory=dict)


class WorkspaceRead(ReadModel):
    id: UUID
    template_id: UUID
    owner_user_id: UUID
    parent_workspace_id: UUID | None
    restored_from_snapshot_id: UUID | None
    display_name: str
    desired_state: str
    observed_state: str
    generation: int
    version: int
    quota: dict[str, object]
    last_error_code: str | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkspaceList(BaseModel):
    items: list[WorkspaceRead]
    total: int


class MembershipWrite(StrictModel):
    user_id: UUID
    role: Role
    expected_version: int | None = Field(default=None, ge=1)


class MembershipRead(ReadModel):
    id: UUID
    workspace_id: UUID
    user_id: UUID
    role: str
    state: str
    version: int
    created_at: datetime
    updated_at: datetime


class MembershipList(BaseModel):
    items: list[MembershipRead]
    total: int


class ScopeGrantCreate(StrictModel):
    source_scope: ScopeClass
    source_owner_id: UUID | None = None
    resource_id: UUID
    actions: list[Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.:-]{1,99}$")]] = Field(
        min_length=1, max_length=32
    )
    expires_at: datetime | None = None


class ScopeGrantRead(ReadModel):
    id: UUID
    target_workspace_id: UUID
    source_scope: str
    source_owner_id: UUID | None
    resource_id: UUID
    actions: list[str]
    state: str
    version: int
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class LifecycleRequest(StrictModel):
    expected_version: int = Field(ge=1)


class RunCreate(StrictModel):
    kind: Literal["batch", "interactive"] = "batch"
    expected_workspace_generation: int = Field(ge=1)
    request_digest: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class RunRead(ReadModel):
    id: UUID
    workspace_id: UUID
    kind: str
    generation: int
    desired_state: str
    observed_state: str
    version: int
    request_digest: str
    last_result_digest: str | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime


class RunList(BaseModel):
    items: list[RunRead]
    total: int


class SnapshotCreate(StrictModel):
    expected_workspace_generation: int = Field(ge=1)
    manifest_digest: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    metadata: dict[str, object] = Field(default_factory=dict)


class SnapshotRead(ReadModel):
    id: UUID
    workspace_id: UUID
    source_generation: int
    manifest_digest: str
    snapshot_metadata: dict[str, object]
    state: str
    created_at: datetime


class RestoreRequest(StrictModel):
    snapshot_id: UUID
    display_name: DisplayName


class TemplateRead(ReadModel):
    id: UUID
    template_key: str
    version: int
    display_name: str
    digest: str
    template_spec: dict[str, object]
    state: str
    created_at: datetime


class TemplateCreate(StrictModel):
    template_key: Annotated[
        str,
        StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{1,99}$"),
    ]
    version: int = Field(ge=1)
    display_name: DisplayName
    template_spec: dict[str, object]
    supersedes_template_id: UUID | None = None


class TemplateList(BaseModel):
    items: list[TemplateRead]
    total: int


__all__ = [
    "LifecycleRequest",
    "MembershipList",
    "MembershipRead",
    "MembershipWrite",
    "RestoreRequest",
    "RunCreate",
    "RunList",
    "RunRead",
    "ScopeGrantCreate",
    "ScopeGrantRead",
    "SnapshotCreate",
    "SnapshotRead",
    "TemplateCreate",
    "TemplateList",
    "TemplateRead",
    "WorkspaceCreate",
    "WorkspaceList",
    "WorkspaceRead",
]
