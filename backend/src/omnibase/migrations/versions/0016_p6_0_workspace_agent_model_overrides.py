"""Add tenant-owned Workspace Agent model overrides.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-13

The table stores only logical selection metadata. Provider secrets remain in
``model_provider_credentials`` and global Workspace/Registry identifiers are
revalidated by application services rather than exposed as physical locators.
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | sa.Column | None = None
depends_on: str | sa.Column | None = None

_TABLE = "workspace_agent_model_overrides"
_CREDENTIAL_UNIQUE = "model_provider_credentials_id_user_uq"
_TENANT_SCHEMA_PATTERN = re.compile(r"^tenant_[a-z0-9]{8,12}$")


def _migration_schema_scope() -> str:
    config = op.get_context().config
    if config is None:
        raise RuntimeError("migration configuration is unavailable")
    scope = config.attributes.get("migration_schema_scope")
    if scope not in {"global", "tenant"}:
        raise RuntimeError(f"unsupported migration_schema_scope: {scope!r}")
    return scope


def _assert_global_downgrade_safe() -> None:
    """Require every retained tenant to have completed 0016 downgrade."""
    bind = op.get_bind()
    tenant_schemas = bind.execute(
        sa.text("SELECT schema_name FROM omnibase_meta.tenants ORDER BY schema_name")
    ).scalars()
    for raw_schema_name in tenant_schemas:
        schema_name = str(raw_schema_name)
        if _TENANT_SCHEMA_PATTERN.fullmatch(schema_name) is None:
            raise RuntimeError(
                "0016 downgrade refused: tenant registry contains an invalid schema name"
            )
        version_table_exists = bind.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :schema_name AND table_name = 'alembic_version')"
            ),
            {"schema_name": schema_name},
        ).scalar_one()
        if not version_table_exists:
            raise RuntimeError(
                "0016 downgrade refused before global revision change: tenant migration "
                "head is unavailable"
            )
        tenant_head = bind.execute(
            sa.text(
                f'SELECT version_num FROM "{schema_name}".alembic_version'  # noqa: S608 -- strict server-owned identifier
            )
        ).scalar_one()
        if tenant_head != "0015":
            raise RuntimeError(
                "0016 downgrade refused before global revision change: every tenant "
                "migration head must be exactly 0015"
            )
        residual_table = bind.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :schema_name AND table_name = :table_name)"
            ),
            {"schema_name": schema_name, "table_name": _TABLE},
        ).scalar_one()
        residual_unique = bind.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM pg_constraint constraint_row "
                "JOIN pg_class table_row ON table_row.oid = constraint_row.conrelid "
                "JOIN pg_namespace schema_row ON schema_row.oid = table_row.relnamespace "
                "WHERE schema_row.nspname = :schema_name "
                "AND table_row.relname = 'model_provider_credentials' "
                "AND constraint_row.contype = 'u' "
                "AND constraint_row.conname = :constraint_name)"
            ),
            {"schema_name": schema_name, "constraint_name": _CREDENTIAL_UNIQUE},
        ).scalar_one()
        if residual_table or residual_unique:
            raise RuntimeError(
                "0016 downgrade refused before global revision change: tenant 0016 "
                "table or credential ownership constraint remains"
            )


def upgrade() -> None:
    if _migration_schema_scope() == "global":
        return
    op.create_unique_constraint(
        _CREDENTIAL_UNIQUE,
        "model_provider_credentials",
        ["id", "user_id"],
    )
    op.create_table(
        _TABLE,
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("agent_version_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("employee_role_id", sa.String(length=16), nullable=False),
        sa.Column("credential_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("model_id", sa.String(length=200), nullable=True),
        sa.Column("family_override", sa.String(length=32), nullable=True),
        sa.Column("last_test_status", sa.String(length=32), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tested_configuration_digest", sa.String(length=64), nullable=True),
        sa.Column("tested_endpoint_policy_digest", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "employee_role_id IN "
            "('parent', 'product', 'ux', 'frontend', 'backend', 'data', "
            "'security', 'qa', 'operations', 'docs')",
            name="workspace_agent_model_overrides_role_check",
        ),
        sa.CheckConstraint(
            "credential_id IS NOT NULL OR model_id IS NOT NULL",
            name="workspace_agent_model_overrides_selection_check",
        ),
        sa.CheckConstraint(
            "model_id IS NULL OR char_length(btrim(model_id)) BETWEEN 1 AND 200",
            name="workspace_agent_model_overrides_model_id_check",
        ),
        sa.CheckConstraint(
            "family_override IS NULL OR family_override IN "
            "('deepseek', 'glm', 'kimi', 'openai', 'anthropic', 'generic')",
            name="workspace_agent_model_overrides_family_check",
        ),
        sa.CheckConstraint(
            "last_test_status IS NULL OR last_test_status IN "
            "('passed', 'auth_failed', 'timeout', 'identity_mismatch', 'unreachable', 'failed')",
            name="workspace_agent_model_overrides_test_status_check",
        ),
        sa.CheckConstraint(
            "tested_configuration_digest IS NULL OR "
            "tested_configuration_digest ~ '^[0-9a-f]{64}$'",
            name="workspace_agent_model_overrides_test_digest_check",
        ),
        sa.CheckConstraint(
            "tested_endpoint_policy_digest IS NULL OR "
            "tested_endpoint_policy_digest ~ '^[0-9a-f]{64}$'",
            name="workspace_agent_model_overrides_endpoint_digest_check",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="workspace_agent_model_overrides_version_check",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["credential_id", "user_id"],
            ["model_provider_credentials.id", "model_provider_credentials.user_id"],
            name="workspace_agent_model_overrides_credential_user_fk",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "workspace_agent_model_overrides_scope_uq",
        _TABLE,
        ["user_id", "workspace_id", "agent_version_id", "employee_role_id"],
        unique=True,
    )
    op.create_index(
        "workspace_agent_model_overrides_credential_idx",
        _TABLE,
        ["user_id", "credential_id"],
        unique=False,
    )


def downgrade() -> None:
    if _migration_schema_scope() == "global":
        _assert_global_downgrade_safe()
        return
    bind = op.get_bind()
    if bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM workspace_agent_model_overrides)")
    ).scalar_one():
        raise RuntimeError(
            "0016 downgrade refused: Workspace Agent model overrides require "
            "forward-fix or restore-new"
        )
    op.drop_index("workspace_agent_model_overrides_credential_idx", table_name=_TABLE)
    op.drop_index("workspace_agent_model_overrides_scope_uq", table_name=_TABLE)
    op.drop_table(_TABLE)
    op.drop_constraint(
        _CREDENTIAL_UNIQUE,
        "model_provider_credentials",
        type_="unique",
    )
