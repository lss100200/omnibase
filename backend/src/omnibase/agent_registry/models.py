"""P5.1B Agent Registry persistence ORM models (global control plane).

These models persist the P5.1A offline contracts.  They are internal only:
no Browser API, router, SDK or runtime surface exists.  Cross-tenant
references are blocked by composite ``(id, tenant_id)`` foreign keys, and
state machines / sealed-version immutability / array semantics are enforced
by database triggers installed by migration ``0010``, never by ORM
discipline alone.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
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
_RISK_LEVELS = "('low', 'medium', 'high', 'critical')"
_DEFINITION_STATES = "('draft', 'active', 'disabled', 'revoked')"
_VERSION_STATES = "('draft', 'sealed', 'deprecated', 'revoked')"
_BINDING_STATES = "('pending_approval', 'installed', 'disabled', 'superseded', 'revoked')"
_SHA256_CHECK = "digest ~ '^[0-9a-f]{64}$'"


def _json_array_check(column: str, *, min_items: int) -> str:
    return (
        f"jsonb_typeof({column}) = 'array' AND jsonb_array_length({column}) >= {min_items} "
        f"AND NOT ({column} @> '[\"*\"]'::jsonb) AND NOT ({column} @> '[\"all\"]'::jsonb)"
    )


class AgentDefinitionModel(Base):
    """AgentDefinition logical identity with tenant-bound natural key."""

    __tablename__ = "agent_definitions"
    __table_args__ = (
        CheckConstraint(
            f"risk_level IN {_RISK_LEVELS}",
            name="agent_definitions_risk_level_check",
        ),
        CheckConstraint(
            f"definition_state IN {_DEFINITION_STATES}",
            name="agent_definitions_definition_state_check",
        ),
        CheckConstraint(
            _json_array_check("installation_scopes", min_items=1),
            name="agent_definitions_installation_scopes_check",
        ),
        CheckConstraint(
            "metadata_version >= 1",
            name="agent_definitions_metadata_version_check",
        ),
        UniqueConstraint(
            "tenant_id",
            "stable_logical_key",
            name="agent_definitions_tenant_key_uq",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name="agent_definitions_id_tenant_uq",
        ),
        Index(
            "agent_definitions_tenant_state_key_idx",
            "tenant_id",
            "definition_state",
            "stable_logical_key",
        ),
    )

    id: Mapped[str] = mapped_column(_UUID, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    stable_logical_key: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    installation_scopes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    definition_state: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by: Mapped[str] = mapped_column(_UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    metadata_version: Mapped[int] = mapped_column(Integer, nullable=False)

    def to_registry_dict(self) -> dict[str, object]:
        """Stable logical projection used by the internal service/replay."""
        return {
            "agent_definition_id": self.id,
            "tenant_id": self.tenant_id,
            "stable_logical_key": self.stable_logical_key,
            "display_name": self.display_name,
            "description": self.description,
            "risk_level": self.risk_level,
            "installation_scopes": list(self.installation_scopes),
            "definition_state": self.definition_state,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "metadata_version": self.metadata_version,
        }


class AgentVersionModel(Base):
    """Immutable AgentVersion manifest with database-enforced seal guard."""

    __tablename__ = "agent_versions"
    __table_args__ = (
        CheckConstraint(
            f"version_state IN {_VERSION_STATES}",
            name="agent_versions_version_state_check",
        ),
        CheckConstraint(
            f"risk_level IN {_RISK_LEVELS}",
            name="agent_versions_risk_level_check",
        ),
        CheckConstraint(
            _SHA256_CHECK,
            name="agent_versions_manifest_digest_check",
        ),
        CheckConstraint(
            "instructions_digest ~ '^[0-9a-f]{64}$'",
            name="agent_versions_instructions_digest_check",
        ),
        CheckConstraint(
            "max_context_tokens >= 1",
            name="agent_versions_max_context_tokens_check",
        ),
        CheckConstraint(
            "max_concurrency >= 1",
            name="agent_versions_max_concurrency_check",
        ),
        CheckConstraint(
            "jsonb_typeof(manifest_payload) = 'object'",
            name="agent_versions_manifest_payload_object_check",
        ),
        CheckConstraint(
            "jsonb_typeof(input_schema) = 'object'",
            name="agent_versions_input_schema_object_check",
        ),
        CheckConstraint(
            "jsonb_typeof(output_schema) = 'object'",
            name="agent_versions_output_schema_object_check",
        ),
        CheckConstraint(
            "jsonb_typeof(default_budget) = 'object'",
            name="agent_versions_default_budget_object_check",
        ),
        CheckConstraint(
            _json_array_check("allowed_tool_ids", min_items=0),
            name="agent_versions_allowed_tool_ids_check",
        ),
        UniqueConstraint(
            "tenant_id",
            "definition_id",
            "version",
            name="agent_versions_definition_version_uq",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name="agent_versions_id_tenant_uq",
        ),
        ForeignKeyConstraint(
            ["definition_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.agent_definitions.id",
                f"{GLOBAL_SCHEMA}.agent_definitions.tenant_id",
            ],
            name="agent_versions_definition_tenant_fk",
            ondelete="RESTRICT",
        ),
        Index(
            "agent_versions_tenant_definition_state_idx",
            "tenant_id",
            "definition_id",
            "version_state",
        ),
    )

    id: Mapped[str] = mapped_column(_UUID, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    definition_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    version_state: Mapped[str] = mapped_column(String(16), nullable=False)
    manifest_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    model_policy_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    instructions_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    max_context_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    allowed_tool_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    memory_policy_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False)
    default_budget: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by: Mapped[str] = mapped_column(_UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    def to_registry_dict(self) -> dict[str, object]:
        return {
            "agent_version_id": self.id,
            "tenant_id": self.tenant_id,
            "agent_definition_id": self.definition_id,
            "version": self.version,
            "version_state": self.version_state,
            "manifest_digest": self.manifest_digest,
            "model_policy_id": self.model_policy_id,
            "instructions_digest": self.instructions_digest,
            "max_context_tokens": self.max_context_tokens,
            "allowed_tool_ids": list(self.allowed_tool_ids),
            "max_concurrency": self.max_concurrency,
            "default_budget": self.default_budget,
            "risk_level": self.risk_level,
            "memory_policy_id": self.memory_policy_id,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class WorkspaceAgentBindingModel(Base):
    """Exact AgentVersion binding to a Workspace generation."""

    __tablename__ = "workspace_agent_bindings"
    __table_args__ = (
        CheckConstraint(
            f"binding_state IN {_BINDING_STATES}",
            name="agent_bindings_binding_state_check",
        ),
        CheckConstraint(
            "workspace_generation >= 1",
            name="agent_bindings_workspace_generation_check",
        ),
        CheckConstraint(
            "agent_version_digest ~ '^[0-9a-f]{64}$'",
            name="agent_bindings_agent_version_digest_check",
        ),
        CheckConstraint(
            _json_array_check("resource_scopes", min_items=1),
            name="agent_bindings_resource_scopes_check",
        ),
        CheckConstraint(
            "jsonb_typeof(default_budget_policy) = 'object'",
            name="agent_bindings_default_budget_object_check",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name="agent_bindings_id_tenant_uq",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.id",
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
            ],
            name="agent_bindings_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["agent_definition_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.agent_definitions.id",
                f"{GLOBAL_SCHEMA}.agent_definitions.tenant_id",
            ],
            name="agent_bindings_definition_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["agent_version_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.agent_versions.id",
                f"{GLOBAL_SCHEMA}.agent_versions.tenant_id",
            ],
            name="agent_bindings_version_tenant_fk",
            ondelete="RESTRICT",
        ),
        Index(
            "agent_bindings_tenant_workspace_state_idx",
            "tenant_id",
            "workspace_id",
            "binding_state",
        ),
        Index(
            "agent_bindings_tenant_workspace_definition_idx",
            "tenant_id",
            "workspace_id",
            "agent_definition_id",
        ),
    )

    id: Mapped[str] = mapped_column(_UUID, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_definition_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    agent_version_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    agent_version_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    binding_state: Mapped[str] = mapped_column(String(16), nullable=False)
    resource_scopes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    default_budget_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    installed_by: Mapped[str] = mapped_column(_UUID, nullable=False)
    approval_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    superseded_by: Mapped[str | None] = mapped_column(_UUID, nullable=True)

    def to_registry_dict(self) -> dict[str, object]:
        return {
            "workspace_agent_binding_id": self.id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "workspace_generation": self.workspace_generation,
            "agent_definition_id": self.agent_definition_id,
            "agent_version_id": self.agent_version_id,
            "agent_version_digest": self.agent_version_digest,
            "binding_state": self.binding_state,
            "resource_scopes": list(self.resource_scopes),
            "default_budget_policy": self.default_budget_policy,
            "installed_by": self.installed_by,
            "approval_id": self.approval_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "disabled_at": self.disabled_at.isoformat() if self.disabled_at else None,
            "superseded_by": self.superseded_by,
        }


__all__ = [
    "AgentDefinitionModel",
    "AgentVersionModel",
    "WorkspaceAgentBindingModel",
]
