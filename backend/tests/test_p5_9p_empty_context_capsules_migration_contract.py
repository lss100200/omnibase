"""Pure-source contract tests for migration 0015 zero-item Capsules."""

from __future__ import annotations

import ast
from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "omnibase"
    / "migrations"
    / "versions"
    / "0015_p5_9p_empty_context_capsules.py"
)
SOURCE = MIGRATION.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _assigned_string(name: str) -> str:
    for node in TREE.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            assert isinstance(node.value, ast.Constant)
            assert isinstance(node.value.value, str)
            return node.value.value
    raise AssertionError(f"missing assignment: {name}")


def test_revision_is_tenant_only_forward_fix() -> None:
    assert _assigned_string("revision") == "0015"
    assert _assigned_string("down_revision") == "0014"
    assert 'if _migration_schema_scope() == "global":\n        return' in SOURCE


def test_only_context_capsule_token_floor_is_relaxed() -> None:
    assert '"context_capsules"' in SOURCE
    assert '"max_tokens >= 1 AND total_tokens BETWEEN 0 AND max_tokens"' in SOURCE
    assert "create_table" not in SOURCE
    assert "add_column" not in SOURCE


def test_populated_zero_item_downgrade_fails_closed() -> None:
    assert "SELECT 1 FROM context_capsules WHERE total_tokens = 0" in SOURCE
    assert (
        "0015 downgrade refused: zero-item ContextCapsules require forward-fix or restore-new"
        in SOURCE
    )
    assert '"max_tokens >= 1 AND total_tokens BETWEEN 1 AND max_tokens"' in SOURCE
