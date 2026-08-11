"""P5.6P personal first-party instruction-Skill persistence.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-12

The migration is global-control-plane only.  It creates immutable exact-digest
Skill versions and Workspace + AgentVersion installations.  Database checks
and triggers independently retain the personal posture: first-party,
instruction-only, no tools/capabilities/network/secrets and zero tool budget.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | sa.Column | None = None
depends_on: str | sa.Column | None = None

_SCHEMA = "omnibase_meta"
_UUID = postgresql.UUID(as_uuid=False)
_JSONB = postgresql.JSONB(astext_type=sa.Text())
_SHA256 = "~ '^[0-9a-f]{64}$'"
_TABLES = (
    "skill_definitions",
    "skill_versions",
    "workspace_agent_skill_installations",
)


def _migration_schema_scope() -> str:
    config = op.get_context().config
    if config is None:
        raise RuntimeError("migration configuration is unavailable")
    scope = config.attributes.get("migration_schema_scope")
    if scope not in {"global", "tenant"}:
        raise RuntimeError(f"unsupported migration_schema_scope: {scope!r}")
    return scope


def _id_column() -> sa.Column:
    return sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _tenant_id_column() -> sa.Column:
    return sa.Column(
        "tenant_id",
        _UUID,
        sa.ForeignKey(f"{_SCHEMA}.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )


def _created_at_column() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("clock_timestamp()"),
    )


def _create_definitions() -> None:
    op.create_table(
        "skill_definitions",
        _id_column(),
        _tenant_id_column(),
        sa.Column("stable_logical_key", sa.String(length=96), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("definition_state", sa.String(length=16), nullable=False),
        sa.Column("installation_scopes", _JSONB, nullable=False),
        sa.Column("first_party", sa.Boolean(), nullable=False),
        sa.Column("created_by", _UUID, nullable=False),
        _created_at_column(),
        sa.CheckConstraint("first_party IS TRUE", name="skill_definitions_first_party_check"),
        sa.CheckConstraint(
            "definition_state IN ('active', 'disabled', 'revoked')",
            name="skill_definitions_state_check",
        ),
        sa.CheckConstraint(
            "installation_scopes = '[\"workspace\"]'::jsonb",
            name="skill_definitions_workspace_scope_check",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="skill_definitions_id_tenant_uq"),
        sa.UniqueConstraint(
            "tenant_id", "stable_logical_key", name="skill_definitions_tenant_key_uq"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "skill_definitions_tenant_state_key_idx",
        "skill_definitions",
        ["tenant_id", "definition_state", "stable_logical_key"],
        schema=_SCHEMA,
    )


def _create_versions() -> None:
    op.create_table(
        "skill_versions",
        _id_column(),
        _tenant_id_column(),
        sa.Column("definition_id", _UUID, nullable=False),
        sa.Column("semantic_version", sa.String(length=64), nullable=False),
        sa.Column("version_state", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("manifest_payload", _JSONB, nullable=False),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("instructions_digest", sa.String(length=64), nullable=False),
        sa.Column("required_tool_ids", _JSONB, nullable=False),
        sa.Column("capability_requirements", _JSONB, nullable=False),
        sa.Column("network_policy", sa.String(length=16), nullable=False),
        sa.Column("secrets_allowed", sa.Boolean(), nullable=False),
        sa.Column("max_tool_calls", sa.Integer(), nullable=False),
        sa.Column("rollback_version_id", _UUID, nullable=True),
        sa.Column("created_by", _UUID, nullable=False),
        _created_at_column(),
        sa.CheckConstraint(
            "version_state IN ('sealed', 'revoked')", name="skill_versions_state_check"
        ),
        sa.CheckConstraint("kind = 'instruction'", name="skill_versions_kind_check"),
        sa.CheckConstraint("network_policy = 'deny'", name="skill_versions_network_check"),
        sa.CheckConstraint("secrets_allowed IS FALSE", name="skill_versions_secrets_check"),
        sa.CheckConstraint("max_tool_calls = 0", name="skill_versions_tool_budget_check"),
        sa.CheckConstraint(
            "required_tool_ids = '[]'::jsonb", name="skill_versions_required_tools_check"
        ),
        sa.CheckConstraint(
            "capability_requirements = '[]'::jsonb", name="skill_versions_capabilities_check"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(manifest_payload) = 'object'",
            name="skill_versions_manifest_object_check",
        ),
        sa.CheckConstraint(
            "manifest_payload ->> 'kind' = 'instruction' "
            "AND manifest_payload ->> 'network_policy' = 'deny' "
            "AND manifest_payload ->> 'secrets_allowed' = 'false' "
            "AND manifest_payload -> 'required_tool_ids' = jsonb_build_array() "
            "AND manifest_payload -> 'capability_requirements' = jsonb_build_array() "
            "AND manifest_payload -> 'budget' ->> 'max_tool_calls' = '0'",
            name="skill_versions_manifest_posture_check",
        ),
        sa.CheckConstraint(
            "char_length(instructions) BETWEEN 1 AND 16000",
            name="skill_versions_instructions_length_check",
        ),
        sa.CheckConstraint(
            f"manifest_digest {_SHA256}", name="skill_versions_manifest_digest_check"
        ),
        sa.CheckConstraint(
            f"instructions_digest {_SHA256}", name="skill_versions_instructions_digest_check"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="skill_versions_id_tenant_uq"),
        sa.UniqueConstraint(
            "tenant_id",
            "definition_id",
            "semantic_version",
            name="skill_versions_definition_semver_uq",
        ),
        sa.ForeignKeyConstraint(
            ["definition_id", "tenant_id"],
            [f"{_SCHEMA}.skill_definitions.id", f"{_SCHEMA}.skill_definitions.tenant_id"],
            name="skill_versions_definition_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rollback_version_id", "tenant_id"],
            [f"{_SCHEMA}.skill_versions.id", f"{_SCHEMA}.skill_versions.tenant_id"],
            name="skill_versions_rollback_tenant_fk",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "skill_versions_tenant_definition_state_idx",
        "skill_versions",
        ["tenant_id", "definition_id", "version_state"],
        schema=_SCHEMA,
    )


def _create_installations() -> None:
    op.create_table(
        "workspace_agent_skill_installations",
        _id_column(),
        _tenant_id_column(),
        sa.Column("owner_user_id", _UUID, nullable=False),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("agent_version_id", _UUID, nullable=False),
        sa.Column("skill_definition_id", _UUID, nullable=False),
        sa.Column("skill_version_id", _UUID, nullable=False),
        sa.Column("skill_manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("installation_state", sa.String(length=16), nullable=False),
        sa.Column("previous_installation_id", _UUID, nullable=True),
        sa.Column("installed_by", _UUID, nullable=False),
        _created_at_column(),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "installation_state IN ('installed', 'disabled', 'superseded', 'revoked')",
            name="skill_installations_state_check",
        ),
        sa.CheckConstraint(
            f"skill_manifest_digest {_SHA256}", name="skill_installations_manifest_digest_check"
        ),
        sa.CheckConstraint(
            "(installation_state = 'installed' AND disabled_at IS NULL AND revoked_at IS NULL) OR "
            "(installation_state IN ('disabled', 'superseded') AND disabled_at IS NOT NULL "
            "AND revoked_at IS NULL) OR "
            "(installation_state = 'revoked' AND revoked_at IS NOT NULL)",
            name="skill_installations_state_shape_check",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="skill_installations_id_tenant_uq"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="skill_installations_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["agent_version_id", "tenant_id"],
            [f"{_SCHEMA}.agent_versions.id", f"{_SCHEMA}.agent_versions.tenant_id"],
            name="skill_installations_agent_version_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["skill_definition_id", "tenant_id"],
            [f"{_SCHEMA}.skill_definitions.id", f"{_SCHEMA}.skill_definitions.tenant_id"],
            name="skill_installations_definition_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["skill_version_id", "tenant_id"],
            [f"{_SCHEMA}.skill_versions.id", f"{_SCHEMA}.skill_versions.tenant_id"],
            name="skill_installations_version_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["previous_installation_id", "tenant_id"],
            [
                f"{_SCHEMA}.workspace_agent_skill_installations.id",
                f"{_SCHEMA}.workspace_agent_skill_installations.tenant_id",
            ],
            name="skill_installations_previous_tenant_fk",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "skill_installations_one_live_uq",
        "workspace_agent_skill_installations",
        ["tenant_id", "workspace_id", "agent_version_id", "skill_definition_id"],
        unique=True,
        postgresql_where=sa.text("installation_state = 'installed'"),
        schema=_SCHEMA,
    )
    op.create_index(
        "skill_installations_resolution_idx",
        "workspace_agent_skill_installations",
        ["tenant_id", "workspace_id", "agent_version_id", "installation_state"],
        schema=_SCHEMA,
    )


def _install_triggers() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION omnibase_meta.skill_definition_guard()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE tenant_schema text; owner_valid boolean;
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'skill definition deletion is forbidden' USING ERRCODE = '55000';
                END IF;
                IF TG_OP = 'INSERT' THEN
                    IF NEW.definition_state <> 'active' OR NEW.first_party IS NOT TRUE
                       OR NEW.installation_scopes <> '["workspace"]'::jsonb THEN
                        RAISE EXCEPTION 'skill definition personal posture invalid'
                            USING ERRCODE = '55000';
                    END IF;
                    SELECT schema_name INTO tenant_schema FROM omnibase_meta.tenants
                     WHERE id = NEW.tenant_id AND is_active IS TRUE;
                    IF tenant_schema IS NULL OR tenant_schema !~ '^tenant_[a-z0-9]{8}$' THEN
                        RAISE EXCEPTION 'skill definition tenant schema invalid'
                            USING ERRCODE = '55000';
                    END IF;
                    EXECUTE format(
                        'SELECT EXISTS (SELECT 1 FROM %I.users WHERE id = $1 AND is_active IS TRUE AND is_tenant_admin IS TRUE)',
                        tenant_schema
                    ) INTO owner_valid USING NEW.created_by;
                    IF owner_valid IS NOT TRUE THEN
                        RAISE EXCEPTION 'skill definition owner invalid' USING ERRCODE = '55000';
                    END IF;
                    RETURN NEW;
                END IF;
                IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                   OR NEW.stable_logical_key IS DISTINCT FROM OLD.stable_logical_key
                   OR NEW.display_name IS DISTINCT FROM OLD.display_name
                   OR NEW.description IS DISTINCT FROM OLD.description
                   OR NEW.installation_scopes IS DISTINCT FROM OLD.installation_scopes
                   OR NEW.first_party IS DISTINCT FROM OLD.first_party
                   OR NEW.created_by IS DISTINCT FROM OLD.created_by
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION 'skill definition identity is immutable' USING ERRCODE = '55000';
                END IF;
                IF OLD.definition_state <> 'active'
                   OR NEW.definition_state NOT IN ('disabled', 'revoked') THEN
                    RAISE EXCEPTION 'invalid skill definition transition' USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END;
            $$;
            CREATE TRIGGER skill_definitions_state_guard
            BEFORE INSERT OR UPDATE OR DELETE ON omnibase_meta.skill_definitions
            FOR EACH ROW EXECUTE FUNCTION omnibase_meta.skill_definition_guard();

            CREATE OR REPLACE FUNCTION omnibase_meta.skill_version_guard()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE rollback_row omnibase_meta.skill_versions%ROWTYPE;
                    definition_row omnibase_meta.skill_definitions%ROWTYPE;
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'skill version deletion is forbidden' USING ERRCODE = '55000';
                END IF;
                IF TG_OP = 'UPDATE' THEN
                    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                       OR NEW.definition_id IS DISTINCT FROM OLD.definition_id
                       OR NEW.semantic_version IS DISTINCT FROM OLD.semantic_version
                       OR NEW.kind IS DISTINCT FROM OLD.kind
                       OR NEW.manifest_payload IS DISTINCT FROM OLD.manifest_payload
                       OR NEW.manifest_digest IS DISTINCT FROM OLD.manifest_digest
                       OR NEW.instructions IS DISTINCT FROM OLD.instructions
                       OR NEW.instructions_digest IS DISTINCT FROM OLD.instructions_digest
                       OR NEW.required_tool_ids IS DISTINCT FROM OLD.required_tool_ids
                       OR NEW.capability_requirements IS DISTINCT FROM OLD.capability_requirements
                       OR NEW.network_policy IS DISTINCT FROM OLD.network_policy
                       OR NEW.secrets_allowed IS DISTINCT FROM OLD.secrets_allowed
                       OR NEW.max_tool_calls IS DISTINCT FROM OLD.max_tool_calls
                       OR NEW.rollback_version_id IS DISTINCT FROM OLD.rollback_version_id
                       OR NEW.created_by IS DISTINCT FROM OLD.created_by
                       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                        RAISE EXCEPTION 'sealed skill version is immutable' USING ERRCODE = '55000';
                    END IF;
                    IF OLD.version_state <> 'sealed' OR NEW.version_state <> 'revoked' THEN
                        RAISE EXCEPTION 'invalid skill version transition' USING ERRCODE = '55000';
                    END IF;
                    RETURN NEW;
                END IF;
                IF NEW.rollback_version_id IS NOT NULL THEN
                    SELECT * INTO rollback_row FROM omnibase_meta.skill_versions
                     WHERE id = NEW.rollback_version_id AND tenant_id = NEW.tenant_id;
                    IF NOT FOUND OR rollback_row.definition_id IS DISTINCT FROM NEW.definition_id
                       OR rollback_row.version_state <> 'sealed'
                       OR rollback_row.id IS NOT DISTINCT FROM NEW.id THEN
                        RAISE EXCEPTION 'skill rollback version binding invalid' USING ERRCODE = '55000';
                    END IF;
                END IF;
                SELECT * INTO definition_row FROM omnibase_meta.skill_definitions
                 WHERE id = NEW.definition_id AND tenant_id = NEW.tenant_id;
                IF NEW.version_state <> 'sealed' OR definition_row.id IS NULL
                   OR definition_row.definition_state <> 'active'
                   OR definition_row.first_party IS NOT TRUE
                   OR definition_row.created_by IS DISTINCT FROM NEW.created_by THEN
                    RAISE EXCEPTION 'skill version owner or definition binding invalid'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END;
            $$;
            CREATE TRIGGER skill_versions_seal_guard
            BEFORE INSERT OR UPDATE OR DELETE ON omnibase_meta.skill_versions
            FOR EACH ROW EXECUTE FUNCTION omnibase_meta.skill_version_guard();

            CREATE OR REPLACE FUNCTION omnibase_meta.skill_installation_guard()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE workspace_row omnibase_meta.workspaces%ROWTYPE;
                    agent_version_row omnibase_meta.agent_versions%ROWTYPE;
                    definition_row omnibase_meta.skill_definitions%ROWTYPE;
                    version_row omnibase_meta.skill_versions%ROWTYPE;
                    previous_row omnibase_meta.workspace_agent_skill_installations%ROWTYPE;
                    previous_version omnibase_meta.skill_versions%ROWTYPE;
                    tenant_schema text;
                    owner_valid boolean;
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'skill installation deletion is forbidden' USING ERRCODE = '55000';
                END IF;
                IF TG_OP = 'UPDATE' THEN
                    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                       OR NEW.owner_user_id IS DISTINCT FROM OLD.owner_user_id
                       OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
                       OR NEW.agent_version_id IS DISTINCT FROM OLD.agent_version_id
                       OR NEW.skill_definition_id IS DISTINCT FROM OLD.skill_definition_id
                       OR NEW.skill_version_id IS DISTINCT FROM OLD.skill_version_id
                       OR NEW.skill_manifest_digest IS DISTINCT FROM OLD.skill_manifest_digest
                       OR NEW.previous_installation_id IS DISTINCT FROM OLD.previous_installation_id
                       OR NEW.installed_by IS DISTINCT FROM OLD.installed_by
                       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                        RAISE EXCEPTION 'skill installation identity is immutable' USING ERRCODE = '55000';
                    END IF;
                    IF (OLD.installation_state = 'installed'
                        AND NEW.installation_state IN ('disabled', 'superseded', 'revoked'))
                       OR (OLD.installation_state = 'disabled'
                           AND NEW.installation_state = 'revoked') THEN
                        RETURN NEW;
                    END IF;
                    RAISE EXCEPTION 'invalid skill installation transition' USING ERRCODE = '55000';
                END IF;
                SELECT * INTO workspace_row FROM omnibase_meta.workspaces
                 WHERE id = NEW.workspace_id AND tenant_id = NEW.tenant_id;
                SELECT * INTO agent_version_row FROM omnibase_meta.agent_versions
                 WHERE id = NEW.agent_version_id AND tenant_id = NEW.tenant_id;
                SELECT * INTO definition_row FROM omnibase_meta.skill_definitions
                 WHERE id = NEW.skill_definition_id AND tenant_id = NEW.tenant_id;
                SELECT * INTO version_row FROM omnibase_meta.skill_versions
                 WHERE id = NEW.skill_version_id AND tenant_id = NEW.tenant_id;
                IF NEW.installation_state <> 'installed'
                   OR NEW.disabled_at IS NOT NULL OR NEW.revoked_at IS NOT NULL
                   OR workspace_row.id IS NULL OR definition_row.id IS NULL OR version_row.id IS NULL
                   OR agent_version_row.id IS NULL
                   OR workspace_row.owner_user_id IS DISTINCT FROM NEW.owner_user_id
                   OR NEW.installed_by IS DISTINCT FROM NEW.owner_user_id
                   OR definition_row.definition_state <> 'active'
                   OR definition_row.first_party IS NOT TRUE
                   OR definition_row.installation_scopes <> '["workspace"]'::jsonb
                   OR version_row.definition_id IS DISTINCT FROM definition_row.id
                   OR version_row.version_state <> 'sealed'
                   OR version_row.kind <> 'instruction'
                   OR version_row.required_tool_ids <> '[]'::jsonb
                   OR version_row.capability_requirements <> '[]'::jsonb
                   OR version_row.network_policy <> 'deny'
                   OR version_row.secrets_allowed IS NOT FALSE
                   OR version_row.max_tool_calls <> 0
                   OR version_row.manifest_digest IS DISTINCT FROM NEW.skill_manifest_digest
                   OR agent_version_row.version_state <> 'sealed'
                   OR NOT EXISTS (
                       SELECT 1 FROM omnibase_meta.workspace_agent_bindings agent_binding
                        WHERE agent_binding.tenant_id = NEW.tenant_id
                          AND agent_binding.workspace_id = NEW.workspace_id
                          AND agent_binding.agent_version_id = NEW.agent_version_id
                          AND agent_binding.binding_state = 'installed'
                          AND agent_binding.agent_version_digest = agent_version_row.manifest_digest
                   ) OR NOT EXISTS (
                       SELECT 1 FROM omnibase_meta.workspace_memberships membership
                        WHERE membership.tenant_id = NEW.tenant_id
                          AND membership.workspace_id = NEW.workspace_id
                          AND membership.user_id = NEW.owner_user_id
                          AND membership.role = 'owner'
                          AND membership.state = 'active'
                   ) THEN
                    RAISE EXCEPTION 'skill installation exact binding invalid' USING ERRCODE = '55000';
                END IF;
                IF jsonb_typeof(
                    version_row.manifest_payload -> 'supported_agent_version_digests'
                ) IS DISTINCT FROM 'array' THEN
                    RAISE EXCEPTION 'skill supported AgentVersion digest shape invalid'
                        USING ERRCODE = '55000';
                END IF;
                IF jsonb_array_length(
                    version_row.manifest_payload -> 'supported_agent_version_digests'
                ) > 0 AND NOT (
                    version_row.manifest_payload -> 'supported_agent_version_digests'
                    ? agent_version_row.manifest_digest
                ) THEN
                    RAISE EXCEPTION 'skill AgentVersion digest is not supported'
                        USING ERRCODE = '55000';
                END IF;
                SELECT schema_name INTO tenant_schema FROM omnibase_meta.tenants
                 WHERE id = NEW.tenant_id AND is_active IS TRUE;
                IF tenant_schema IS NULL OR tenant_schema !~ '^tenant_[a-z0-9]{8}$' THEN
                    RAISE EXCEPTION 'skill installation tenant schema invalid' USING ERRCODE = '55000';
                END IF;
                EXECUTE format(
                    'SELECT EXISTS (SELECT 1 FROM %I.users WHERE id = $1 AND is_active IS TRUE AND is_tenant_admin IS TRUE)',
                    tenant_schema
                ) INTO owner_valid USING NEW.owner_user_id;
                IF owner_valid IS NOT TRUE THEN
                    RAISE EXCEPTION 'skill installation owner invalid' USING ERRCODE = '55000';
                END IF;
                IF NEW.previous_installation_id IS NOT NULL THEN
                    SELECT * INTO previous_row
                      FROM omnibase_meta.workspace_agent_skill_installations
                     WHERE id = NEW.previous_installation_id AND tenant_id = NEW.tenant_id;
                    SELECT * INTO previous_version FROM omnibase_meta.skill_versions
                     WHERE id = previous_row.skill_version_id AND tenant_id = NEW.tenant_id;
                    IF previous_row.id IS NULL OR previous_version.id IS NULL
                       OR previous_row.owner_user_id IS DISTINCT FROM NEW.owner_user_id
                       OR previous_row.workspace_id IS DISTINCT FROM NEW.workspace_id
                       OR previous_row.agent_version_id IS DISTINCT FROM NEW.agent_version_id
                       OR previous_row.skill_definition_id IS DISTINCT FROM NEW.skill_definition_id
                       OR previous_row.installation_state NOT IN ('disabled', 'superseded')
                       OR previous_version.rollback_version_id IS DISTINCT FROM NEW.skill_version_id THEN
                        RAISE EXCEPTION 'skill rollback installation binding invalid' USING ERRCODE = '55000';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$;
            CREATE TRIGGER workspace_agent_skill_installations_state_guard
            BEFORE INSERT OR UPDATE OR DELETE
            ON omnibase_meta.workspace_agent_skill_installations
            FOR EACH ROW EXECUTE FUNCTION omnibase_meta.skill_installation_guard();
            """
        )
    )


def _assert_downgrade_safe() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        statement = sa.text(
            f"SELECT EXISTS (SELECT 1 FROM omnibase_meta.{table} LIMIT 1)"  # noqa: S608 -- closed _TABLES tuple
        )
        populated = bind.execute(statement).scalar_one()
        if populated:
            raise RuntimeError(
                "0014 populated downgrade is forbidden; use a forward fix or restore into a new omnibase_restore_* database"
            )


def upgrade() -> None:
    if _migration_schema_scope() == "tenant":
        return
    _create_definitions()
    _create_versions()
    _create_installations()
    _install_triggers()


def downgrade() -> None:
    if _migration_schema_scope() == "tenant":
        return
    _assert_downgrade_safe()
    op.execute(sa.text("DROP FUNCTION IF EXISTS omnibase_meta.skill_installation_guard() CASCADE"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS omnibase_meta.skill_version_guard() CASCADE"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS omnibase_meta.skill_definition_guard() CASCADE"))
    op.drop_table("workspace_agent_skill_installations", schema=_SCHEMA)
    op.drop_table("skill_versions", schema=_SCHEMA)
    op.drop_table("skill_definitions", schema=_SCHEMA)
