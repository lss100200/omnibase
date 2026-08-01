"""Add queued/processing states and error_detail column

Adds the 'queued' and 'processing' lifecycle states for async ingest,
adds a bounded error_detail column for safe failure persistence, and
preserves the 'pending' state for initial compatibility.

The full state machine is:
    pending -> queued -> processing -> indexed | failed
    failed  -> queued (retry)
    indexed -> queued (re-index)

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-30 12:00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Converge documents onto the asynchronous lifecycle contract."""
    if op.get_context().config.attributes.get("migration_schema_scope") != "tenant":
        return

    bind = op.get_bind()
    table_exists = bind.execute(
        sa.text("SELECT to_regclass(current_schema() || '.documents') IS NOT NULL")
    ).scalar()
    if not table_exists:
        return

    op.execute("ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_status_check")
    op.execute("UPDATE documents SET status = 'queued' WHERE status = 'parsed'")
    op.execute(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS error_detail VARCHAR(1000)"
    )
    op.create_check_constraint(
        "documents_status_check",
        "documents",
        sa.text("status IN ('pending', 'queued', 'processing', 'indexed', 'failed')"),
    )


def downgrade() -> None:
    """Revert the lifecycle contract for tenant schemas only."""
    if op.get_context().config.attributes.get("migration_schema_scope") != "tenant":
        return

    bind = op.get_bind()
    table_exists = bind.execute(
        sa.text("SELECT to_regclass(current_schema() || '.documents') IS NOT NULL")
    ).scalar()
    if not table_exists:
        return

    op.execute("ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_status_check")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS error_detail")
    op.create_check_constraint(
        "documents_status_check",
        "documents",
        sa.text("status IN ('pending', 'parsed', 'failed', 'indexed')"),
    )
