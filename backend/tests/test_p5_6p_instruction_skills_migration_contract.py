"""Pure-source contract tests for migration 0014 personal instruction Skills."""

from __future__ import annotations

import ast
from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "omnibase"
    / "migrations"
    / "versions"
    / "0014_p5_6p_personal_instruction_skills.py"
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


def _function(name: str) -> ast.FunctionDef:
    for node in TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing function: {name}")


def _create_table_literals() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(TREE):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_table"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            names.add(node.args[0].value)
    return names


def test_revision_scope_and_exact_global_table_set() -> None:
    assert _assigned_string("revision") == "0014"
    assert _assigned_string("down_revision") == "0013"
    assert _create_table_literals() == {
        "skill_definitions",
        "skill_versions",
        "workspace_agent_skill_installations",
    }
    upgrade = ast.get_source_segment(SOURCE, _function("upgrade"))
    assert upgrade is not None
    assert 'if _migration_schema_scope() == "tenant":\n        return' in upgrade


def test_database_closes_personal_non_escalating_posture() -> None:
    for assertion in (
        "first_party IS TRUE",
        "kind = 'instruction'",
        "network_policy = 'deny'",
        "secrets_allowed IS FALSE",
        "max_tool_calls = 0",
        "required_tool_ids = '[]'::jsonb",
        "capability_requirements = '[]'::jsonb",
        "skill_versions_manifest_posture_check",
        "char_length(instructions) BETWEEN 1 AND 16000",
        "definition_row.first_party IS NOT TRUE",
        "version_row.kind <> 'instruction'",
        "version_row.network_policy <> 'deny'",
        "version_row.secrets_allowed IS NOT FALSE",
        "version_row.max_tool_calls <> 0",
    ):
        assert assertion in SOURCE


def test_exact_tenant_workspace_agent_and_version_bindings_are_present() -> None:
    for binding in (
        "skill_versions_definition_tenant_fk",
        "skill_versions_rollback_tenant_fk",
        "skill_installations_workspace_tenant_fk",
        "skill_installations_agent_version_tenant_fk",
        "skill_installations_definition_tenant_fk",
        "skill_installations_version_tenant_fk",
        "skill_installations_previous_tenant_fk",
        "skill_installations_one_live_uq",
        "workspace_agent_bindings agent_binding",
        "agent_version_row omnibase_meta.agent_versions%ROWTYPE",
        "agent_binding.agent_version_digest = agent_version_row.manifest_digest",
        "agent_binding.workspace_id = NEW.workspace_id",
        "agent_binding.agent_version_id = NEW.agent_version_id",
        "agent_binding.binding_state = 'installed'",
        "workspace_row.owner_user_id IS DISTINCT FROM NEW.owner_user_id",
        "previous_row.workspace_id IS DISTINCT FROM NEW.workspace_id",
        "previous_row.agent_version_id IS DISTINCT FROM NEW.agent_version_id",
        "previous_version.rollback_version_id IS DISTINCT FROM NEW.skill_version_id",
        "membership.role = 'owner'",
        "membership.state = 'active'",
        "supported_agent_version_digests",
        "skill AgentVersion digest is not supported",
    ):
        assert binding in SOURCE


def test_versions_are_immutable_and_installation_lifecycle_is_closed() -> None:
    assert "sealed skill version is immutable" in SOURCE
    assert "skill installation identity is immutable" in SOURCE
    assert "('installed', 'disabled', 'superseded', 'revoked')" in SOURCE
    assert "OLD.installation_state = 'installed'" in SOURCE
    assert "OLD.installation_state = 'disabled'" in SOURCE
    assert "invalid skill installation transition" in SOURCE
    assert "skill_installations_state_shape_check" in SOURCE
    assert "NEW.installation_state <> 'installed'" in SOURCE


def test_populated_downgrade_fails_closed() -> None:
    downgrade = ast.get_source_segment(SOURCE, _function("downgrade"))
    assert downgrade is not None
    assert "_assert_downgrade_safe()" in downgrade
    assert "0014 populated downgrade is forbidden" in SOURCE
