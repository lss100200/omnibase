"""Closed P34.3 DDL plan contracts; no SQL fragments or physical names."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from omnibase.controlled_data.contracts import ColumnDefinition, DisplayName

DDLKind = Literal[
    "create_table",
    "add_nullable_column",
    "rename_table_display",
    "rename_column_display",
    "create_btree_index",
]
RiskLevel = Literal["R0", "R1", "R2", "R3", "R4"]
Hash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class DDLStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class CreateTablePlanDefinition(DDLStrictModel):
    display_name: DisplayName
    columns: list[ColumnDefinition] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def unique_columns(self) -> CreateTablePlanDefinition:
        ids = [column.id for column in self.columns]
        if len(ids) != len(set(ids)):
            raise ValueError("column logical IDs must be unique")
        return self


class AddNullableColumnPlanDefinition(DDLStrictModel):
    column: ColumnDefinition

    @model_validator(mode="after")
    def require_nullable(self) -> AddNullableColumnPlanDefinition:
        if self.column.nullable is not True:
            raise ValueError("added columns must be nullable")
        return self


class RenameTableDisplayPlanDefinition(DDLStrictModel):
    before_display_name: DisplayName
    display_name: DisplayName


class RenameColumnDisplayPlanDefinition(DDLStrictModel):
    column_id: UUID
    before_display_name: DisplayName
    display_name: DisplayName


class CreateBtreeIndexPlanDefinition(DDLStrictModel):
    index_id: UUID
    display_name: DisplayName
    column_ids: list[UUID] = Field(min_length=1, max_length=8)
    method: Literal["btree"] = "btree"

    @model_validator(mode="after")
    def unique_columns(self) -> CreateBtreeIndexPlanDefinition:
        if len(self.column_ids) != len(set(self.column_ids)):
            raise ValueError("index column logical IDs must be unique")
        return self


DDLDefinition = (
    CreateTablePlanDefinition
    | AddNullableColumnPlanDefinition
    | RenameTableDisplayPlanDefinition
    | RenameColumnDisplayPlanDefinition
    | CreateBtreeIndexPlanDefinition
)


class DDLPlan(DDLStrictModel):
    """Server-owned persisted plan projection, never a public request DTO."""

    tenant_id: UUID
    workspace_id: UUID | None = None
    resource_id: UUID
    table_binding_id: UUID
    authorization_context_id: UUID
    operation_id: UUID
    kind: DDLKind
    base_version: int = Field(ge=1)
    request_hash: Hash
    definition: DDLDefinition

    @model_validator(mode="after")
    def definition_matches_kind(self) -> DDLPlan:
        valid = (
            (self.kind == "create_table" and isinstance(self.definition, CreateTablePlanDefinition))
            or (
                self.kind == "add_nullable_column"
                and isinstance(self.definition, AddNullableColumnPlanDefinition)
            )
            or (
                self.kind == "rename_table_display"
                and isinstance(self.definition, RenameTableDisplayPlanDefinition)
            )
            or (
                self.kind == "rename_column_display"
                and isinstance(self.definition, RenameColumnDisplayPlanDefinition)
            )
            or (
                self.kind == "create_btree_index"
                and isinstance(self.definition, CreateBtreeIndexPlanDefinition)
            )
        )
        if not valid:
            raise ValueError(f"{self.kind} has the wrong closed definition")
        return self


@dataclass(frozen=True, slots=True)
class TrustedColumnLocator:
    id: UUID
    physical_name: str
    state: str = "active"
    display_name: str = "Column"


@dataclass(frozen=True, slots=True)
class TrustedTableLocator:
    tenant_id: UUID
    workspace_id: UUID | None
    resource_id: UUID
    table_binding_id: UUID
    schema_name: str
    physical_table_name: str
    resource_version: int
    state: str
    policy_class: str
    display_name: str = "Table"
    columns: tuple[TrustedColumnLocator, ...] = ()


@dataclass(frozen=True, slots=True)
class LiveAuthorization:
    tenant_id: UUID
    workspace_id: UUID | None
    actor_user_id: UUID
    actions: frozenset[str]
    resource_ids: frozenset[UUID]
    source_version: int
    checked_at: datetime
    active: bool = True


@dataclass(frozen=True, slots=True)
class TrustedAuthorizationSnapshot:
    id: UUID
    tenant_id: UUID
    workspace_id: UUID | None
    actor_user_id: UUID
    actions: frozenset[str]
    resource_ids: frozenset[UUID]
    source_version: int
    snapshot_hash: str
    expires_at: datetime
    live_recheck_required: bool = True


@dataclass(frozen=True, slots=True)
class ApprovalGrant:
    id: UUID
    tenant_id: UUID
    workspace_id: UUID | None
    requester_id: UUID
    resource_id: UUID
    operation_id: UUID
    grant_id: UUID
    action: str
    request_hash: str
    resource_version: int
    risk_level: RiskLevel
    required_approver_role: Literal["tenant_admin", "platform_admin"]
    state: str
    version: int
    decided_by_actor_type: Literal["user", "system"]
    decided_by_actor_id: UUID
    expires_at: datetime
    consumed_at: datetime


@dataclass(frozen=True, slots=True)
class ValidatedDDLPlan:
    plan: DDLPlan
    locator: TrustedTableLocator
    risk_level: RiskLevel
    requires_approval: bool
    approval_policy_threshold: RiskLevel
    plan_digest: str


@dataclass(frozen=True, slots=True)
class AuthorizedDDLPlan:
    validated: ValidatedDDLPlan
    authorization_context_id: UUID
    authorization_source_version: int
    authorization_snapshot_hash: str
    approval_id: UUID | None
    approval_version: int | None
    authorized_at: datetime


def canonical_plan_hash(plan: DDLPlan) -> str:
    value = plan.model_dump(mode="json", exclude={"request_hash"})
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "AddNullableColumnPlanDefinition",
    "ApprovalGrant",
    "AuthorizedDDLPlan",
    "CreateBtreeIndexPlanDefinition",
    "CreateTablePlanDefinition",
    "DDLDefinition",
    "DDLKind",
    "DDLPlan",
    "DDLStrictModel",
    "LiveAuthorization",
    "RenameColumnDisplayPlanDefinition",
    "RenameTableDisplayPlanDefinition",
    "RiskLevel",
    "TrustedAuthorizationSnapshot",
    "TrustedColumnLocator",
    "TrustedTableLocator",
    "ValidatedDDLPlan",
    "canonical_plan_hash",
]
