"""P5.6P personal instruction-Skill persistence models.

All rows live in the global control plane.  Migration 0014 is authoritative
for cross-tenant foreign keys, immutable SkillVersion payloads and installation
state transitions.  The ORM deliberately exposes no relationships or cascades.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
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
_SHA256 = "~ '^[0-9a-f]{64}$'"


class SkillDefinitionModel(Base):
    """Tenant-owned first-party Skill logical identity."""

    __tablename__ = "skill_definitions"
    __table_args__ = (
        CheckConstraint("first_party IS TRUE", name="skill_definitions_first_party_check"),
        CheckConstraint(
            "definition_state IN ('active', 'disabled', 'revoked')",
            name="skill_definitions_state_check",
        ),
        CheckConstraint(
            "installation_scopes = '[\"workspace\"]'::jsonb",
            name="skill_definitions_workspace_scope_check",
        ),
        UniqueConstraint("tenant_id", "id", name="skill_definitions_id_tenant_uq"),
        UniqueConstraint(
            "tenant_id",
            "stable_logical_key",
            name="skill_definitions_tenant_key_uq",
        ),
        Index(
            "skill_definitions_tenant_state_key_idx",
            "tenant_id",
            "definition_state",
            "stable_logical_key",
        ),
    )

    id: Mapped[str] = mapped_column(_UUID, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    stable_logical_key: Mapped[str] = mapped_column(String(96), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    definition_state: Mapped[str] = mapped_column(String(16), nullable=False)
    installation_scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    first_party: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_by: Mapped[str] = mapped_column(_UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )


class SkillVersionModel(Base):
    """Immutable, exact-digest instruction-only SkillVersion."""

    __tablename__ = "skill_versions"
    __table_args__ = (
        CheckConstraint(
            "version_state IN ('sealed', 'revoked')",
            name="skill_versions_state_check",
        ),
        CheckConstraint("kind = 'instruction'", name="skill_versions_kind_check"),
        CheckConstraint("network_policy = 'deny'", name="skill_versions_network_check"),
        CheckConstraint("secrets_allowed IS FALSE", name="skill_versions_secrets_check"),
        CheckConstraint("max_tool_calls = 0", name="skill_versions_tool_budget_check"),
        CheckConstraint(
            "required_tool_ids = '[]'::jsonb",
            name="skill_versions_required_tools_check",
        ),
        CheckConstraint(
            "capability_requirements = '[]'::jsonb",
            name="skill_versions_capabilities_check",
        ),
        CheckConstraint(
            "jsonb_typeof(manifest_payload) = 'object'",
            name="skill_versions_manifest_object_check",
        ),
        CheckConstraint(
            "manifest_payload ->> 'kind' = 'instruction' "
            "AND manifest_payload ->> 'network_policy' = 'deny' "
            "AND manifest_payload ->> 'secrets_allowed' = 'false' "
            "AND manifest_payload -> 'required_tool_ids' = jsonb_build_array() "
            "AND manifest_payload -> 'capability_requirements' = jsonb_build_array() "
            "AND manifest_payload -> 'budget' ->> 'max_tool_calls' = '0'",
            name="skill_versions_manifest_posture_check",
        ),
        CheckConstraint(
            "char_length(instructions) BETWEEN 1 AND 16000",
            name="skill_versions_instructions_length_check",
        ),
        CheckConstraint(f"manifest_digest {_SHA256}", name="skill_versions_manifest_digest_check"),
        CheckConstraint(
            f"instructions_digest {_SHA256}",
            name="skill_versions_instructions_digest_check",
        ),
        UniqueConstraint("tenant_id", "id", name="skill_versions_id_tenant_uq"),
        UniqueConstraint(
            "tenant_id",
            "definition_id",
            "semantic_version",
            name="skill_versions_definition_semver_uq",
        ),
        ForeignKeyConstraint(
            ["definition_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.skill_definitions.id",
                f"{GLOBAL_SCHEMA}.skill_definitions.tenant_id",
            ],
            name="skill_versions_definition_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["rollback_version_id", "tenant_id"],
            [f"{GLOBAL_SCHEMA}.skill_versions.id", f"{GLOBAL_SCHEMA}.skill_versions.tenant_id"],
            name="skill_versions_rollback_tenant_fk",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index(
            "skill_versions_tenant_definition_state_idx",
            "tenant_id",
            "definition_id",
            "version_state",
        ),
    )

    id: Mapped[str] = mapped_column(_UUID, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    definition_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    semantic_version: Mapped[str] = mapped_column(String(64), nullable=False)
    version_state: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    manifest_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    instructions_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    required_tool_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    capability_requirements: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    network_policy: Mapped[str] = mapped_column(String(16), nullable=False)
    secrets_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    max_tool_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    rollback_version_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    created_by: Mapped[str] = mapped_column(_UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )


class WorkspaceAgentSkillInstallationModel(Base):
    """Exact Workspace + AgentVersion installation of one SkillVersion."""

    __tablename__ = "workspace_agent_skill_installations"
    __table_args__ = (
        CheckConstraint(
            "installation_state IN ('installed', 'disabled', 'superseded', 'revoked')",
            name="skill_installations_state_check",
        ),
        CheckConstraint(
            f"skill_manifest_digest {_SHA256}",
            name="skill_installations_manifest_digest_check",
        ),
        CheckConstraint(
            "(installation_state = 'installed' AND disabled_at IS NULL AND revoked_at IS NULL) OR "
            "(installation_state IN ('disabled', 'superseded') AND disabled_at IS NOT NULL "
            "AND revoked_at IS NULL) OR "
            "(installation_state = 'revoked' AND revoked_at IS NOT NULL)",
            name="skill_installations_state_shape_check",
        ),
        UniqueConstraint("tenant_id", "id", name="skill_installations_id_tenant_uq"),
        ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [f"{GLOBAL_SCHEMA}.workspaces.id", f"{GLOBAL_SCHEMA}.workspaces.tenant_id"],
            name="skill_installations_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["agent_version_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.agent_versions.id",
                f"{GLOBAL_SCHEMA}.agent_versions.tenant_id",
            ],
            name="skill_installations_agent_version_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["skill_definition_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.skill_definitions.id",
                f"{GLOBAL_SCHEMA}.skill_definitions.tenant_id",
            ],
            name="skill_installations_definition_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["skill_version_id", "tenant_id"],
            [f"{GLOBAL_SCHEMA}.skill_versions.id", f"{GLOBAL_SCHEMA}.skill_versions.tenant_id"],
            name="skill_installations_version_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["previous_installation_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspace_agent_skill_installations.id",
                f"{GLOBAL_SCHEMA}.workspace_agent_skill_installations.tenant_id",
            ],
            name="skill_installations_previous_tenant_fk",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index(
            "skill_installations_one_live_uq",
            "tenant_id",
            "workspace_id",
            "agent_version_id",
            "skill_definition_id",
            unique=True,
            postgresql_where=text("installation_state = 'installed'"),
        ),
        Index(
            "skill_installations_resolution_idx",
            "tenant_id",
            "workspace_id",
            "agent_version_id",
            "installation_state",
        ),
    )

    id: Mapped[str] = mapped_column(_UUID, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    owner_user_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    agent_version_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    skill_definition_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    skill_version_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    skill_manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    installation_state: Mapped[str] = mapped_column(String(16), nullable=False)
    previous_installation_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    installed_by: Mapped[str] = mapped_column(_UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "SkillDefinitionModel",
    "SkillVersionModel",
    "WorkspaceAgentSkillInstallationModel",
]
