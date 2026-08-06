"""Tenant user profiles and encrypted model-provider credentials.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-05

Global migration scope is an explicit no-op. Tenant scope installs only
user-owned presentation preferences and encrypted provider configuration;
it does not activate tools, Planner, multi-Agent orchestration or a Sandbox.
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | None = None
depends_on: str | None = None

_UUID = postgresql.UUID(as_uuid=False)
_TENANT_SCHEMA_PATTERN = re.compile(r"^tenant_[a-z0-9]{8,12}$")


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    return bool(
        bind.execute(
            sa.text("SELECT to_regclass(current_schema() || '.' || :name) IS NOT NULL"),
            {"name": name},
        ).scalar_one()
    )


def _assert_bootstrap_shape() -> None:
    """Fail closed if pre-Alembic bootstrap tables are partial or drifted."""
    bind = op.get_bind()
    required_columns = {
        "user_profiles": {
            "user_id",
            "display_name",
            "locale",
            "theme",
            "assistant_name",
            "assistant_tone",
            "assistant_instructions",
            "version",
            "created_at",
            "updated_at",
        },
        "model_provider_credentials": {
            "id",
            "user_id",
            "display_name",
            "provider_id",
            "base_url",
            "model_id",
            "encrypted_api_key",
            "key_nonce",
            "key_version",
            "key_fingerprint",
            "is_default",
            "is_active",
            "version",
            "last_test_status",
            "last_test_latency_ms",
            "last_tested_at",
            "revoked_at",
            "created_at",
            "updated_at",
        },
    }
    for table_name, expected in required_columns.items():
        actual = set(
            bind.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = :table"
                ),
                {"table": table_name},
            ).scalars()
        )
        if actual != expected:
            raise RuntimeError(f"0012 bootstrap table shape drifted: {table_name}")

    required_constraints = {
        "user_profiles_theme_check",
        "user_profiles_assistant_tone_check",
        "user_profiles_version_check",
        "user_profiles_instructions_length_check",
        "model_provider_credentials_version_check",
        "model_provider_credentials_key_version_check",
        "model_provider_credentials_test_status_check",
        "model_provider_credentials_latency_check",
        "model_provider_credentials_active_revoked_check",
    }
    actual_constraints = set(
        bind.execute(
            sa.text(
                "SELECT conname FROM pg_constraint c "
                "JOIN pg_namespace n ON n.oid = c.connamespace "
                "WHERE n.nspname = current_schema() "
                "AND conname = ANY(CAST(:names AS text[]))"
            ),
            {"names": sorted(required_constraints)},
        ).scalars()
    )
    if actual_constraints != required_constraints:
        raise RuntimeError("0012 bootstrap constraint set drifted")

    required_indexes = {
        "model_provider_credentials_user_idx",
        "model_provider_credentials_one_default_uq",
    }
    actual_indexes = set(
        bind.execute(
            sa.text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = current_schema() "
                "AND indexname = ANY(CAST(:names AS text[]))"
            ),
            {"names": sorted(required_indexes)},
        ).scalars()
    )
    if actual_indexes != required_indexes:
        raise RuntimeError("0012 bootstrap index set drifted")


def _migration_schema_scope() -> str:
    config = op.get_context().config
    if config is None:
        raise RuntimeError("migration configuration is unavailable")
    scope = config.attributes.get("migration_schema_scope")
    if scope not in {"global", "tenant"}:
        raise RuntimeError(f"unsupported migration_schema_scope: {scope!r}")
    return str(scope)


def _assert_global_downgrade_safe() -> None:
    """Reject before the global revision moves when any tenant owns 0012 data."""
    bind = op.get_bind()
    tenant_schemas = bind.execute(
        sa.text("SELECT schema_name FROM omnibase_meta.tenants ORDER BY schema_name")
    ).scalars()
    for raw_schema_name in tenant_schemas:
        schema_name = str(raw_schema_name)
        if _TENANT_SCHEMA_PATTERN.fullmatch(schema_name) is None:
            raise RuntimeError(
                "0012 downgrade refused: tenant registry contains an invalid schema name"
            )
        table_names = set(
            bind.execute(
                sa.text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :schema_name "
                    "AND table_name IN ('user_profiles', 'model_provider_credentials')"
                ),
                {"schema_name": schema_name},
            ).scalars()
        )
        expected_tables = {"user_profiles", "model_provider_credentials"}
        if table_names != expected_tables:
            raise RuntimeError(
                "0012 downgrade refused: tenant profile/provider table set is incomplete"
            )
        populated = bind.execute(
            sa.text(
                f'SELECT EXISTS (SELECT 1 FROM "{schema_name}".user_profiles) '  # noqa: S608 -- strict server-owned identifier
                f'OR EXISTS (SELECT 1 FROM "{schema_name}".model_provider_credentials)'
            )
        ).scalar_one()
        if populated:
            raise RuntimeError(
                "0012 downgrade refused before global revision change: user profile or "
                "provider credential data exists; use a forward fix or restore into a "
                "new omnibase_restore_* database"
            )


def upgrade() -> None:
    if _migration_schema_scope() == "global":
        return

    profile_exists = _table_exists("user_profiles")
    credential_exists = _table_exists("model_provider_credentials")
    if profile_exists != credential_exists:
        raise RuntimeError("0012 bootstrap tables are only partially present")
    if profile_exists:
        _assert_bootstrap_shape()
        return

    op.create_table(
        "user_profiles",
        sa.Column(
            "user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False, server_default="zh-CN"),
        sa.Column("theme", sa.String(length=16), nullable=False, server_default="system"),
        sa.Column("assistant_name", sa.String(length=80), nullable=False, server_default="Omni"),
        sa.Column(
            "assistant_tone",
            sa.String(length=16),
            nullable=False,
            server_default="balanced",
        ),
        sa.Column("assistant_instructions", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "theme IN ('system', 'light', 'dark')", name="user_profiles_theme_check"
        ),
        sa.CheckConstraint(
            "assistant_tone IN ('concise', 'balanced', 'detailed')",
            name="user_profiles_assistant_tone_check",
        ),
        sa.CheckConstraint("version >= 1", name="user_profiles_version_check"),
        sa.CheckConstraint(
            "char_length(assistant_instructions) <= 4000",
            name="user_profiles_instructions_length_check",
        ),
    )

    op.create_table(
        "model_provider_credentials",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("model_id", sa.String(length=200), nullable=False),
        sa.Column("encrypted_api_key", sa.LargeBinary(), nullable=True),
        sa.Column("key_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("key_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("key_fingerprint", sa.String(length=24), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_test_status", sa.String(length=32), nullable=True),
        sa.Column("last_test_latency_ms", sa.Integer(), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("version >= 1", name="model_provider_credentials_version_check"),
        sa.CheckConstraint("key_version >= 1", name="model_provider_credentials_key_version_check"),
        sa.CheckConstraint(
            "last_test_status IS NULL OR last_test_status IN "
            "('passed', 'auth_failed', 'timeout', 'identity_mismatch', 'unreachable', 'failed')",
            name="model_provider_credentials_test_status_check",
        ),
        sa.CheckConstraint(
            "last_test_latency_ms IS NULL OR last_test_latency_ms >= 0",
            name="model_provider_credentials_latency_check",
        ),
        sa.CheckConstraint(
            "(is_active AND revoked_at IS NULL) OR (NOT is_active)",
            name="model_provider_credentials_active_revoked_check",
        ),
    )
    op.create_index(
        "model_provider_credentials_user_idx",
        "model_provider_credentials",
        ["user_id", "created_at"],
    )
    op.create_index(
        "model_provider_credentials_one_default_uq",
        "model_provider_credentials",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_active AND is_default AND revoked_at IS NULL"),
    )


def downgrade() -> None:
    if _migration_schema_scope() == "global":
        _assert_global_downgrade_safe()
        return
    bind = op.get_bind()
    populated = bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM user_profiles) "
            "OR EXISTS (SELECT 1 FROM model_provider_credentials)"
        )
    ).scalar_one()
    if populated:
        raise RuntimeError(
            "0012 downgrade refused: user profile or provider credential data exists; "
            "use a forward fix or restore into a new omnibase_restore_* database"
        )
    op.drop_index(
        "model_provider_credentials_one_default_uq",
        table_name="model_provider_credentials",
    )
    op.drop_index(
        "model_provider_credentials_user_idx",
        table_name="model_provider_credentials",
    )
    op.drop_table("model_provider_credentials")
    op.drop_table("user_profiles")
