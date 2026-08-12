"""Allow audited zero-item ContextCapsules for personal Memory bootstrap.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-12

The migration is tenant-schema only. It changes no table shape and grants no
new runtime authority: a fresh invocation that selects no Memory may persist a
zero-token Capsule as provenance for a later Owner-approved first Memory.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | sa.Column | None = None
depends_on: str | sa.Column | None = None

_CONSTRAINT = "context_capsules_tokens_check"


def _migration_schema_scope() -> str:
    config = op.get_context().config
    if config is None:
        raise RuntimeError("migration configuration is unavailable")
    scope = config.attributes.get("migration_schema_scope")
    if scope not in {"global", "tenant"}:
        raise RuntimeError(f"unsupported migration_schema_scope: {scope!r}")
    return scope


def upgrade() -> None:
    if _migration_schema_scope() == "global":
        return
    op.drop_constraint(_CONSTRAINT, "context_capsules", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "context_capsules",
        "max_tokens >= 1 AND total_tokens BETWEEN 0 AND max_tokens",
    )


def downgrade() -> None:
    if _migration_schema_scope() == "global":
        return
    bind = op.get_bind()
    if bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM context_capsules WHERE total_tokens = 0)")
    ).scalar_one():
        raise RuntimeError(
            "0015 downgrade refused: zero-item ContextCapsules require forward-fix or restore-new"
        )
    op.drop_constraint(_CONSTRAINT, "context_capsules", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "context_capsules",
        "max_tokens >= 1 AND total_tokens BETWEEN 1 AND max_tokens",
    )
