"""Strict internal contracts for P34.6 Workspace data services."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
MediaType = Annotated[str, StringConstraints(min_length=1, max_length=100)]
DisplayName = Annotated[str, StringConstraints(min_length=1, max_length=200)]
IdempotencyKey = Annotated[str, StringConstraints(min_length=1, max_length=128)]
RequestId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{1,64}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArtifactStageRequest(StrictModel):
    workspace_id: UUID
    source_run_id: UUID | None = None
    expected_workspace_generation: int = Field(ge=1)
    display_name: DisplayName
    content_digest: Digest
    size_bytes: int = Field(ge=0)
    media_type: MediaType
    idempotency_key: IdempotencyKey
    request_id: RequestId


class DerivedIndexStartRequest(StrictModel):
    workspace_id: UUID
    source_resource_id: UUID
    source_version: int = Field(ge=1)
    display_name: DisplayName
    index_profile_digest: Digest
    idempotency_key: IdempotencyKey
    request_id: RequestId


class PublicationTargetScope(StrEnum):
    WORKSPACE_SHARED = "workspace_shared"
    TENANT_SHARED = "tenant_shared"


class PublicationRequest(StrictModel):
    source_workspace_id: UUID
    source_resource_id: UUID
    source_version: int = Field(ge=1)
    source_manifest_digest: Digest
    target_scope: PublicationTargetScope
    target_workspace_id: UUID | None = None
    display_name: DisplayName
    idempotency_key: IdempotencyKey
    request_id: RequestId

    @model_validator(mode="after")
    def validate_target(self) -> PublicationRequest:
        if self.target_scope is PublicationTargetScope.WORKSPACE_SHARED:
            if self.target_workspace_id is None:
                raise ValueError("workspace_shared publication requires target_workspace_id")
        elif self.target_workspace_id is not None:
            raise ValueError("tenant_shared publication cannot include target_workspace_id")
        return self


class SnapshotItemKind(StrEnum):
    PRIVATE_TABLE = "private_table"
    ARTIFACT = "artifact"
    DERIVED_INDEX = "derived_index"


class SnapshotInventoryItem(StrictModel):
    source_resource_id: UUID
    source_version: int = Field(ge=1)
    item_kind: SnapshotItemKind
    content_digest: Digest
    payload_artifact_id: UUID
    size_bytes: int = Field(ge=0)


class SnapshotInventoryRequest(StrictModel):
    workspace_id: UUID
    expected_workspace_generation: int = Field(ge=1)
    items: list[SnapshotInventoryItem] = Field(min_length=1, max_length=1000)
    idempotency_key: IdempotencyKey
    request_id: RequestId

    @model_validator(mode="after")
    def unique_resources(self) -> SnapshotInventoryRequest:
        ids = [item.source_resource_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("snapshot inventory resource IDs must be unique")
        return self


class SnapshotRestoreRequest(StrictModel):
    source_workspace_id: UUID
    snapshot_id: UUID
    display_name: DisplayName
    idempotency_key: IdempotencyKey
    request_id: RequestId


class EffectOutcome(StrEnum):
    COMMITTED = "committed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class RestoredWorkspaceData(StrictModel):
    workspace_id: UUID
    source_snapshot_id: UUID
    resource_ids: list[UUID]
    state: Literal["provisioning"] = "provisioning"


__all__ = [
    "ArtifactStageRequest",
    "DerivedIndexStartRequest",
    "Digest",
    "EffectOutcome",
    "PublicationRequest",
    "PublicationTargetScope",
    "RestoredWorkspaceData",
    "SnapshotInventoryItem",
    "SnapshotInventoryRequest",
    "SnapshotItemKind",
    "SnapshotRestoreRequest",
]
