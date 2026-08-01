"""Strict logical DTOs for the P34.3 controlled-data foundation.

Public contracts expose logical UUIDs and display metadata only.  Physical
table/column/index names, tenant schema names, locators, credentials, and SQL
are intentionally absent and rejected as extra fields.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from omnibase.controlled_data.types import LogicalDataType, validate_type_spec

DisplayName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
BindingPolicy = Literal["workspace_private", "tenant_managed", "controlled_shared"]
AuthorizationSource = Literal["capability", "user_rbac"]
SchemaChangeKind = Literal[
    "create_table",
    "add_nullable_column",
    "rename_table_display",
    "rename_column_display",
    "create_btree_index",
]


class StrictModel(BaseModel):
    """Base contract that rejects unknown or future fields by default."""

    model_config = ConfigDict(extra="forbid", strict=True)


class ControlledTypeSpec(StrictModel):
    type: LogicalDataType
    args: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_closed_type_args(self) -> ControlledTypeSpec:
        normalized = validate_type_spec(self.type, self.args)
        self.args = dict(normalized.args)
        return self


class ColumnDefinition(StrictModel):
    id: UUID
    display_name: DisplayName
    data_type: ControlledTypeSpec
    nullable: bool


class CreateTableDefinition(StrictModel):
    resource_id: UUID
    workspace_id: UUID | None = None
    display_name: DisplayName
    policy_class: BindingPolicy
    columns: list[ColumnDefinition] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_workspace_policy(self) -> CreateTableDefinition:
        if self.policy_class == "workspace_private" and self.workspace_id is None:
            raise ValueError("workspace_private tables require workspace_id")
        column_ids = [column.id for column in self.columns]
        if len(column_ids) != len(set(column_ids)):
            raise ValueError("column logical IDs must be unique")
        return self


class AuthorizationSnapshot(StrictModel):
    source: AuthorizationSource
    actor_user_id: UUID
    grant_id: UUID | None = None
    roles: frozenset[Literal["tenant_admin", "platform_admin"]] = Field(default_factory=frozenset)
    actions: frozenset[str] = Field(min_length=1, max_length=32)
    resource_ids: frozenset[UUID] = Field(min_length=1, max_length=256)
    source_version: int = Field(ge=1)
    expires_at: datetime
    live_recheck_required: Literal[True] = True

    @model_validator(mode="after")
    def validate_source_binding(self) -> AuthorizationSnapshot:
        if self.source == "capability" and self.grant_id is None:
            raise ValueError("capability authorization requires grant_id")
        if self.source == "user_rbac" and self.grant_id is not None:
            raise ValueError("user_rbac authorization cannot carry grant_id")
        return self


def narrow_authorization_snapshot(
    parent: AuthorizationSnapshot,
    *,
    actions: frozenset[str],
    resource_ids: frozenset[UUID],
) -> AuthorizationSnapshot:
    """Create a subset snapshot; attempts to enlarge authority fail closed."""
    if not actions or not actions.issubset(parent.actions):
        raise ValueError("authorization actions must be a non-empty subset")
    if not resource_ids or not resource_ids.issubset(parent.resource_ids):
        raise ValueError("authorization resources must be a non-empty subset")
    return parent.model_copy(update={"actions": actions, "resource_ids": resource_ids})


class SchemaChangePlanCreate(StrictModel):
    table_binding_id: UUID | None = None
    authorization_context_id: UUID
    operation_id: UUID
    kind: SchemaChangeKind
    base_version: int | None = Field(default=None, ge=1)
    request_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    definition: CreateTableDefinition | dict[str, object]

    @model_validator(mode="after")
    def validate_target_shape(self) -> SchemaChangePlanCreate:
        if self.kind == "create_table" and not isinstance(self.definition, CreateTableDefinition):
            raise ValueError("create_table requires a CreateTableDefinition")
        if self.kind != "create_table" and self.table_binding_id is None:
            raise ValueError("non-create schema changes require table_binding_id")
        return self


class DataColumnBindingRead(StrictModel):
    id: UUID
    display_name: str
    data_type: LogicalDataType
    type_args: dict[str, object]
    nullable: bool
    ordinal: int
    state: Literal["pending", "active", "archived"]
    version: int


class DataIndexBindingRead(StrictModel):
    id: UUID
    display_name: str
    column_ids: list[UUID]
    method: Literal["btree"]
    state: Literal["pending", "active", "archived"]
    version: int


class DataTableBindingRead(StrictModel):
    id: UUID
    resource_id: UUID
    workspace_id: UUID | None
    display_name: str
    policy_class: BindingPolicy
    state: Literal["pending", "active", "archived"]
    resource_version: int
    version: int
    columns: list[DataColumnBindingRead] = Field(default_factory=list)
    indexes: list[DataIndexBindingRead] = Field(default_factory=list)


__all__ = [
    "AuthorizationSnapshot",
    "AuthorizationSource",
    "BindingPolicy",
    "ColumnDefinition",
    "ControlledTypeSpec",
    "CreateTableDefinition",
    "DataColumnBindingRead",
    "DataIndexBindingRead",
    "DataTableBindingRead",
    "DisplayName",
    "SchemaChangeKind",
    "SchemaChangePlanCreate",
    "StrictModel",
    "narrow_authorization_snapshot",
]
