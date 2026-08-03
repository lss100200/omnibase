"""P5.1B Agent Registry persistence foundation (global control plane).

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-03

Creates ``agent_definitions``, ``agent_versions`` and
``workspace_agent_bindings`` in ``omnibase_meta`` with tenant-bound composite
foreign keys, closed-set CHECK constraints, a partial unique index for a
single live binding per workspace/definition, and BEFORE INSERT/UPDATE
triggers that enforce state machines, sealed-version immutability and
cross-row/cross-tenant integrity at the database layer.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

_SCHEMA = "omnibase_meta"
_UUID = postgresql.UUID(as_uuid=False)
_JSONB = postgresql.JSONB(astext_type=sa.Text())
_RISK_LEVELS = "('low', 'medium', 'high', 'critical')"
_DEFINITION_STATES = "('draft', 'active', 'disabled', 'revoked')"
_VERSION_STATES = "('draft', 'sealed', 'deprecated', 'revoked')"
_BINDING_STATES = "('pending_approval', 'installed', 'disabled', 'superseded', 'revoked')"
_SHA256 = "~ '^[0-9a-f]{64}$'"

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | sa.Column | None = None
depends_on: str | sa.Column | None = None


def _migration_schema_scope() -> str:
    config = op.get_context().config
    if config is None:
        raise RuntimeError("migration configuration is unavailable")
    scope = config.attributes.get("migration_schema_scope")
    if scope not in {"global", "tenant"}:
        raise RuntimeError(f"unsupported migration_schema_scope: {scope!r}")
    return scope


def _id_column(*, generated: bool = True) -> sa.Column:
    return sa.Column(
        "id",
        _UUID,
        primary_key=True,
        server_default=sa.text("gen_random_uuid()") if generated else None,
    )


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
        server_default=sa.text("now()"),
    )


def _json_array_check(column: str, *, min_items: int) -> str:
    return (
        f"jsonb_typeof({column}) = 'array' AND jsonb_array_length({column}) >= {min_items} "
        f"AND NOT ({column} @> '[\"*\"]'::jsonb) AND NOT ({column} @> '[\"all\"]'::jsonb)"
    )


def _create_agent_definitions_table() -> None:
    op.create_table(
        "agent_definitions",
        _id_column(),
        _tenant_id_column(),
        sa.Column("stable_logical_key", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("installation_scopes", _JSONB, nullable=False),
        sa.Column("definition_state", sa.String(length=16), nullable=False),
        sa.Column("created_by", _UUID, nullable=False),
        _created_at_column(),
        sa.Column("metadata_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            f"risk_level IN {_RISK_LEVELS}",
            name="agent_definitions_risk_level_check",
        ),
        sa.CheckConstraint(
            f"definition_state IN {_DEFINITION_STATES}",
            name="agent_definitions_definition_state_check",
        ),
        sa.CheckConstraint(
            _json_array_check("installation_scopes", min_items=1),
            name="agent_definitions_installation_scopes_check",
        ),
        sa.CheckConstraint(
            "metadata_version >= 1",
            name="agent_definitions_metadata_version_check",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "stable_logical_key",
            name="agent_definitions_tenant_key_uq",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="agent_definitions_id_tenant_uq",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "agent_definitions_tenant_state_key_idx",
        "agent_definitions",
        ["tenant_id", "definition_state", "stable_logical_key"],
        schema=_SCHEMA,
    )


def _create_agent_versions_table() -> None:
    op.create_table(
        "agent_versions",
        _id_column(),
        _tenant_id_column(),
        sa.Column("definition_id", _UUID, nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("version_state", sa.String(length=16), nullable=False),
        sa.Column("manifest_payload", _JSONB, nullable=False),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("model_policy_id", _UUID, nullable=False),
        sa.Column("instructions_digest", sa.String(length=64), nullable=False),
        sa.Column("max_context_tokens", sa.Integer(), nullable=False),
        sa.Column("allowed_tool_ids", _JSONB, nullable=False),
        sa.Column("input_schema", _JSONB, nullable=False),
        sa.Column("output_schema", _JSONB, nullable=False),
        sa.Column("memory_policy_id", _UUID, nullable=True),
        sa.Column("max_concurrency", sa.Integer(), nullable=False),
        sa.Column("default_budget", _JSONB, nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("created_by", _UUID, nullable=False),
        _created_at_column(),
        sa.CheckConstraint(
            f"version_state IN {_VERSION_STATES}",
            name="agent_versions_version_state_check",
        ),
        sa.CheckConstraint(
            f"risk_level IN {_RISK_LEVELS}",
            name="agent_versions_risk_level_check",
        ),
        sa.CheckConstraint(
            f"manifest_digest {_SHA256}",
            name="agent_versions_manifest_digest_check",
        ),
        sa.CheckConstraint(
            f"instructions_digest {_SHA256}",
            name="agent_versions_instructions_digest_check",
        ),
        sa.CheckConstraint(
            "max_context_tokens >= 1",
            name="agent_versions_max_context_tokens_check",
        ),
        sa.CheckConstraint(
            "max_concurrency >= 1",
            name="agent_versions_max_concurrency_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(manifest_payload) = 'object'",
            name="agent_versions_manifest_payload_object_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_schema) = 'object'",
            name="agent_versions_input_schema_object_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(output_schema) = 'object'",
            name="agent_versions_output_schema_object_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(default_budget) = 'object'",
            name="agent_versions_default_budget_object_check",
        ),
        sa.CheckConstraint(
            _json_array_check("allowed_tool_ids", min_items=0),
            name="agent_versions_allowed_tool_ids_check",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "definition_id",
            "version",
            name="agent_versions_definition_version_uq",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="agent_versions_id_tenant_uq",
        ),
        sa.ForeignKeyConstraint(
            ["definition_id", "tenant_id"],
            [f"{_SCHEMA}.agent_definitions.id", f"{_SCHEMA}.agent_definitions.tenant_id"],
            name="agent_versions_definition_tenant_fk",
            ondelete="RESTRICT",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "agent_versions_tenant_definition_state_idx",
        "agent_versions",
        ["tenant_id", "definition_id", "version_state"],
        schema=_SCHEMA,
    )


def _create_workspace_agent_bindings_table() -> None:
    op.create_table(
        "workspace_agent_bindings",
        _id_column(),
        _tenant_id_column(),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("workspace_generation", sa.Integer(), nullable=False),
        sa.Column("agent_definition_id", _UUID, nullable=False),
        sa.Column("agent_version_id", _UUID, nullable=False),
        sa.Column("agent_version_digest", sa.String(length=64), nullable=False),
        sa.Column("binding_state", sa.String(length=16), nullable=False),
        sa.Column("resource_scopes", _JSONB, nullable=False),
        sa.Column("default_budget_policy", _JSONB, nullable=False),
        sa.Column("installed_by", _UUID, nullable=False),
        sa.Column("approval_id", _UUID, nullable=True),
        _created_at_column(),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", _UUID, nullable=True),
        sa.CheckConstraint(
            f"binding_state IN {_BINDING_STATES}",
            name="agent_bindings_binding_state_check",
        ),
        sa.CheckConstraint(
            "workspace_generation >= 1",
            name="agent_bindings_workspace_generation_check",
        ),
        sa.CheckConstraint(
            f"agent_version_digest {_SHA256}",
            name="agent_bindings_agent_version_digest_check",
        ),
        sa.CheckConstraint(
            _json_array_check("resource_scopes", min_items=1),
            name="agent_bindings_resource_scopes_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(default_budget_policy) = 'object'",
            name="agent_bindings_default_budget_object_check",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="agent_bindings_id_tenant_uq",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="agent_bindings_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["agent_definition_id", "tenant_id"],
            [f"{_SCHEMA}.agent_definitions.id", f"{_SCHEMA}.agent_definitions.tenant_id"],
            name="agent_bindings_definition_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["agent_version_id", "tenant_id"],
            [f"{_SCHEMA}.agent_versions.id", f"{_SCHEMA}.agent_versions.tenant_id"],
            name="agent_bindings_version_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id", "tenant_id"],
            [f"{_SCHEMA}.approval_requests.id", f"{_SCHEMA}.approval_requests.tenant_id"],
            name="agent_bindings_approval_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by", "tenant_id"],
            [
                f"{_SCHEMA}.workspace_agent_bindings.id",
                f"{_SCHEMA}.workspace_agent_bindings.tenant_id",
            ],
            name="agent_bindings_superseded_tenant_fk",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "agent_bindings_tenant_workspace_state_idx",
        "workspace_agent_bindings",
        ["tenant_id", "workspace_id", "binding_state"],
        schema=_SCHEMA,
    )
    op.create_index(
        "agent_bindings_tenant_workspace_definition_idx",
        "workspace_agent_bindings",
        ["tenant_id", "workspace_id", "agent_definition_id"],
        schema=_SCHEMA,
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX agent_bindings_live_workspace_definition_uq
        ON {_SCHEMA}.workspace_agent_bindings
          (tenant_id, workspace_id, agent_definition_id)
        WHERE binding_state IN ('pending_approval', 'installed')
        """
    )


def _create_triggers() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_SCHEMA}.agent_definitions_state_guard()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            item text;
            seen text[] := '{{}}';
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF OLD.definition_state = 'revoked' AND NEW.definition_state <> 'revoked' THEN
                    RAISE EXCEPTION 'agent_definition revoked is terminal' USING ERRCODE = '55000';
                END IF;
                IF OLD.definition_state = 'disabled'
                   AND NEW.definition_state NOT IN ('disabled', 'revoked') THEN
                    RAISE EXCEPTION 'agent_definition disabled may only transition to revoked'
                        USING ERRCODE = '55000';
                END IF;
            END IF;
            FOREACH item IN ARRAY (SELECT ARRAY(SELECT jsonb_array_elements_text(NEW.installation_scopes)))
            LOOP
                IF item !~ '^[a-z0-9][a-z0-9_-]{{1,63}}$' THEN
                    RAISE EXCEPTION 'agent_definition installation scope is not a logical identifier'
                        USING ERRCODE = '55000';
                END IF;
                IF item = ANY (seen) THEN
                    RAISE EXCEPTION 'agent_definition installation scopes must not contain duplicates'
                        USING ERRCODE = '55000';
                END IF;
                seen := seen || item;
            END LOOP;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER agent_definitions_state_guard
        BEFORE INSERT OR UPDATE ON {_SCHEMA}.agent_definitions
        FOR EACH ROW EXECUTE FUNCTION {_SCHEMA}.agent_definitions_state_guard();
        """
    )
    seal_guard_sql = """
        CREATE OR REPLACE FUNCTION omnibase_meta.agent_versions_seal_guard()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            definition_risk text;
            definition_status text;
            item text;
            seen text[] := '{}';
        BEGIN
            IF TG_OP = 'UPDATE' AND OLD.version_state IN ('sealed', 'deprecated', 'revoked') THEN
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                   OR NEW.created_by IS DISTINCT FROM OLD.created_by
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
                   OR NEW.manifest_payload <> OLD.manifest_payload
                   OR NEW.manifest_digest <> OLD.manifest_digest
                   OR NEW.model_policy_id <> OLD.model_policy_id
                   OR NEW.instructions_digest <> OLD.instructions_digest
                   OR NEW.max_context_tokens <> OLD.max_context_tokens
                   OR NEW.allowed_tool_ids <> OLD.allowed_tool_ids
                   OR NEW.input_schema <> OLD.input_schema
                   OR NEW.output_schema <> OLD.output_schema
                   OR NEW.memory_policy_id IS DISTINCT FROM OLD.memory_policy_id
                   OR NEW.max_concurrency <> OLD.max_concurrency
                   OR NEW.default_budget <> OLD.default_budget
                   OR NEW.risk_level <> OLD.risk_level
                   OR NEW.definition_id <> OLD.definition_id
                   OR NEW.version <> OLD.version THEN
                    RAISE EXCEPTION 'sealed agent_version content is immutable'
                        USING ERRCODE = '55000';
                END IF;
                IF OLD.version_state = 'revoked' AND NEW.version_state <> 'revoked' THEN
                    RAISE EXCEPTION 'agent_version revoked is terminal' USING ERRCODE = '55000';
                END IF;
                IF OLD.version_state = 'deprecated'
                   AND NEW.version_state NOT IN ('deprecated', 'revoked') THEN
                    RAISE EXCEPTION 'agent_version deprecated may only transition to revoked'
                        USING ERRCODE = '55000';
                END IF;
                IF OLD.version_state = 'sealed'
                   AND NEW.version_state NOT IN ('sealed', 'deprecated', 'revoked') THEN
                    RAISE EXCEPTION 'agent_version sealed may only transition to deprecated or revoked'
                        USING ERRCODE = '55000';
                END IF;
            END IF;
            SELECT risk_level, definition_state INTO definition_risk, definition_status
            FROM omnibase_meta.agent_definitions
            WHERE id = NEW.definition_id AND tenant_id = NEW.tenant_id;
            IF definition_risk IS NULL THEN
                RAISE EXCEPTION 'agent_version references an unknown agent_definition'
                    USING ERRCODE = '55000';
            END IF;
            IF NEW.version_state = 'sealed' AND definition_status <> 'active' THEN
                RAISE EXCEPTION 'sealed agent_version requires an active agent_definition'
                    USING ERRCODE = '55000';
            END IF;
            IF (CASE NEW.risk_level WHEN 'low' THEN 0 WHEN 'medium' THEN 1
                    WHEN 'high' THEN 2 WHEN 'critical' THEN 3 END)
               < (CASE definition_risk WHEN 'low' THEN 0 WHEN 'medium' THEN 1
                    WHEN 'high' THEN 2 WHEN 'critical' THEN 3 END) THEN
                RAISE EXCEPTION 'agent_version must not downgrade the definition risk level'
                    USING ERRCODE = '55000';
            END IF;
            FOREACH item IN ARRAY (SELECT ARRAY(SELECT jsonb_array_elements_text(NEW.allowed_tool_ids)))
            LOOP
                IF item !~ '^[a-z0-9][a-z0-9_-]{1,63}$' THEN
                    RAISE EXCEPTION 'agent_version tool id is not a logical identifier'
                        USING ERRCODE = '55000';
                END IF;
                IF item = ANY (seen) THEN
                    RAISE EXCEPTION 'agent_version tool ids must not contain duplicates'
                        USING ERRCODE = '55000';
                END IF;
                seen := seen || item;
            END LOOP;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER agent_versions_seal_guard
        BEFORE INSERT OR UPDATE ON omnibase_meta.agent_versions
        FOR EACH ROW EXECUTE FUNCTION omnibase_meta.agent_versions_seal_guard();
        """
    op.execute(seal_guard_sql)
    binding_guard_sql = """
        CREATE OR REPLACE FUNCTION omnibase_meta.agent_bindings_integrity_guard()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            definition_row record;
            version_row record;
            approval_row record;
            item text;
            seen text[] := '{}';
        BEGIN
            SELECT id, tenant_id, definition_state, risk_level, installation_scopes
              INTO definition_row
            FROM omnibase_meta.agent_definitions
            WHERE id = NEW.agent_definition_id AND tenant_id = NEW.tenant_id;
            IF definition_row.id IS NULL THEN
                RAISE EXCEPTION 'agent_binding references an unknown agent_definition'
                    USING ERRCODE = '55000';
            END IF;
            SELECT id, tenant_id, definition_id, version_state, manifest_digest, risk_level
              INTO version_row
            FROM omnibase_meta.agent_versions
            WHERE id = NEW.agent_version_id AND tenant_id = NEW.tenant_id;
            IF version_row.id IS NULL THEN
                RAISE EXCEPTION 'agent_binding references an unknown agent_version'
                    USING ERRCODE = '55000';
            END IF;
            IF version_row.definition_id <> NEW.agent_definition_id THEN
                RAISE EXCEPTION 'agent_binding binds a version from a different definition'
                    USING ERRCODE = '55000';
            END IF;
            IF version_row.manifest_digest <> NEW.agent_version_digest THEN
                RAISE EXCEPTION 'agent_binding binds a drifted version digest'
                    USING ERRCODE = '55000';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF definition_row.definition_state <> 'active' THEN
                    RAISE EXCEPTION 'agent_binding requires an active definition'
                        USING ERRCODE = '55000';
                END IF;
                IF version_row.version_state <> 'sealed' THEN
                    RAISE EXCEPTION 'agent_binding requires a sealed version'
                        USING ERRCODE = '55000';
                END IF;
                IF NOT (definition_row.installation_scopes @> '["workspace"]'::jsonb) THEN
                    RAISE EXCEPTION 'agent_binding requires workspace installation scope'
                        USING ERRCODE = '55000';
                END IF;
                IF (CASE version_row.risk_level WHEN 'low' THEN 0 WHEN 'medium' THEN 1
                        WHEN 'high' THEN 2 WHEN 'critical' THEN 3 END) >= 2
                   AND NEW.approval_id IS NULL THEN
                        RAISE EXCEPTION 'high or critical risk binding requires an approval'
                            USING ERRCODE = '55000';
                END IF;
                IF NEW.approval_id IS NOT NULL THEN
                    SELECT id, state, expires_at, consumed_at, requester_type,
                           requester_id, action, workspace_id, risk_level
                      INTO approval_row
                    FROM omnibase_meta.approval_requests
                    WHERE id = NEW.approval_id AND tenant_id = NEW.tenant_id;
                    IF approval_row.id IS NULL OR approval_row.state <> 'approved'
                       OR approval_row.consumed_at IS NOT NULL
                       OR approval_row.expires_at IS NOT NULL AND approval_row.expires_at <= now()
                       OR approval_row.requester_type <> 'user'
                       OR approval_row.requester_id IS DISTINCT FROM NEW.installed_by
                       OR approval_row.action <> 'agent.install'
                       OR approval_row.workspace_id IS DISTINCT FROM NEW.workspace_id
                       OR approval_row.risk_level <> CASE version_row.risk_level
                            WHEN 'low' THEN 'R1' WHEN 'medium' THEN 'R2'
                            WHEN 'high' THEN 'R3' WHEN 'critical' THEN 'R4' END THEN
                        RAISE EXCEPTION 'agent_binding approval is not valid'
                            USING ERRCODE = '55000';
                    END IF;
                END IF;
            END IF;
            IF TG_OP = 'UPDATE' THEN
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                   OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
                   OR NEW.workspace_generation IS DISTINCT FROM OLD.workspace_generation
                   OR NEW.agent_definition_id IS DISTINCT FROM OLD.agent_definition_id
                   OR NEW.agent_version_id IS DISTINCT FROM OLD.agent_version_id
                   OR NEW.agent_version_digest IS DISTINCT FROM OLD.agent_version_digest
                   OR NEW.resource_scopes IS DISTINCT FROM OLD.resource_scopes
                   OR NEW.default_budget_policy IS DISTINCT FROM OLD.default_budget_policy
                   OR NEW.installed_by IS DISTINCT FROM OLD.installed_by
                   OR NEW.approval_id IS DISTINCT FROM OLD.approval_id
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION 'agent_binding identity and installation payload are immutable'
                        USING ERRCODE = '55000';
                END IF;
                IF OLD.binding_state IN ('superseded', 'revoked') AND NEW.binding_state <> OLD.binding_state THEN
                    RAISE EXCEPTION 'agent_binding terminal state is immutable'
                        USING ERRCODE = '55000';
                END IF;
                IF OLD.binding_state = 'installed'
                   AND NEW.binding_state NOT IN ('installed', 'disabled', 'superseded', 'revoked') THEN
                    RAISE EXCEPTION 'agent_binding installed has an invalid transition'
                        USING ERRCODE = '55000';
                END IF;
                IF OLD.binding_state = 'pending_approval'
                   AND NEW.binding_state NOT IN ('pending_approval', 'installed', 'disabled', 'revoked') THEN
                    RAISE EXCEPTION 'agent_binding pending_approval has an invalid transition'
                        USING ERRCODE = '55000';
                END IF;
                IF OLD.binding_state = 'disabled'
                   AND NEW.binding_state NOT IN ('disabled', 'superseded', 'revoked') THEN
                    RAISE EXCEPTION 'agent_binding disabled has an invalid transition'
                        USING ERRCODE = '55000';
                END IF;
                IF NEW.binding_state = 'disabled' AND NEW.disabled_at IS NULL THEN
                    RAISE EXCEPTION 'agent_binding disabled requires disabled_at'
                        USING ERRCODE = '55000';
                END IF;
                IF NEW.binding_state NOT IN ('disabled', 'revoked') AND NEW.disabled_at IS NOT NULL THEN
                    RAISE EXCEPTION 'agent_binding disabled_at requires disabled or revoked state'
                        USING ERRCODE = '55000';
                END IF;
                IF NEW.binding_state = 'superseded' AND NEW.superseded_by IS NULL THEN
                    RAISE EXCEPTION 'agent_binding superseded requires superseded_by'
                        USING ERRCODE = '55000';
                END IF;
                IF NEW.binding_state <> 'superseded' AND NEW.superseded_by IS NOT NULL THEN
                    RAISE EXCEPTION 'agent_binding superseded_by requires superseded state'
                        USING ERRCODE = '55000';
                END IF;
                IF NEW.superseded_by = NEW.id THEN
                    RAISE EXCEPTION 'agent_binding must not supersede itself'
                        USING ERRCODE = '55000';
                END IF;
            END IF;
            FOREACH item IN ARRAY (SELECT ARRAY(SELECT jsonb_array_elements_text(NEW.resource_scopes)))
            LOOP
                IF item !~ '^[a-z0-9][a-z0-9_-]{1,63}$' THEN
                    RAISE EXCEPTION 'agent_binding resource scope is not a logical identifier'
                        USING ERRCODE = '55000';
                END IF;
                IF item = ANY (seen) THEN
                    RAISE EXCEPTION 'agent_binding resource scopes must not contain duplicates'
                        USING ERRCODE = '55000';
                END IF;
                seen := seen || item;
            END LOOP;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER agent_bindings_integrity_guard
        BEFORE INSERT OR UPDATE ON omnibase_meta.workspace_agent_bindings
        FOR EACH ROW EXECUTE FUNCTION omnibase_meta.agent_bindings_integrity_guard();
        """
    op.execute(binding_guard_sql)


def upgrade() -> None:
    """Create P5.1B Agent Registry persistence in the global schema."""
    if _migration_schema_scope() == "tenant":
        return
    _create_agent_definitions_table()
    _create_agent_versions_table()
    _create_workspace_agent_bindings_table()
    _create_triggers()


def _downgrade_global() -> None:
    populated_guard_sql = """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM omnibase_meta.agent_definitions)
               OR EXISTS (SELECT 1 FROM omnibase_meta.agent_versions)
               OR EXISTS (SELECT 1 FROM omnibase_meta.workspace_agent_bindings) THEN
                RAISE EXCEPTION 'P5.1B downgrade refused' USING ERRCODE = '55000';
            END IF;
        END $$;
        """
    op.execute(populated_guard_sql)
    op.execute(
        f"DROP TRIGGER IF EXISTS agent_bindings_integrity_guard ON {_SCHEMA}.workspace_agent_bindings"
    )
    op.execute(f"DROP TRIGGER IF EXISTS agent_versions_seal_guard ON {_SCHEMA}.agent_versions")
    op.execute(
        f"DROP TRIGGER IF EXISTS agent_definitions_state_guard ON {_SCHEMA}.agent_definitions"
    )
    op.execute(f"DROP FUNCTION IF EXISTS {_SCHEMA}.agent_bindings_integrity_guard()")
    op.execute(f"DROP FUNCTION IF EXISTS {_SCHEMA}.agent_versions_seal_guard()")
    op.execute(f"DROP FUNCTION IF EXISTS {_SCHEMA}.agent_definitions_state_guard()")
    op.execute("DROP INDEX IF EXISTS " f"{_SCHEMA}.agent_bindings_live_workspace_definition_uq")
    op.drop_index(
        "agent_bindings_tenant_workspace_definition_idx",
        table_name="workspace_agent_bindings",
        schema=_SCHEMA,
    )
    op.drop_index(
        "agent_bindings_tenant_workspace_state_idx",
        table_name="workspace_agent_bindings",
        schema=_SCHEMA,
    )
    op.drop_index(
        "agent_versions_tenant_definition_state_idx",
        table_name="agent_versions",
        schema=_SCHEMA,
    )
    op.drop_index(
        "agent_definitions_tenant_state_key_idx",
        table_name="agent_definitions",
        schema=_SCHEMA,
    )
    op.drop_table("workspace_agent_bindings", schema=_SCHEMA)
    op.drop_table("agent_versions", schema=_SCHEMA)
    op.drop_table("agent_definitions", schema=_SCHEMA)


def downgrade() -> None:
    """Drop P5.1B persistence; populated downgrade fails closed."""
    if _migration_schema_scope() == "tenant":
        return
    _downgrade_global()
