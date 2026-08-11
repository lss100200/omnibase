"""Pure source/contract tests for the P5.5B tenant Memory migration."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "omnibase"
    / "migrations"
    / "versions"
    / "0013_memory_context_capsules.py"
)
SOURCE = MIGRATION.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

EXPECTED_TABLES = {
    "memory_candidates",
    "memories",
    "memory_versions",
    "memory_review_evidence",
    "context_capsules",
    "context_capsule_items",
    "memory_effects",
    "memory_tombstones",
    "memory_embeddings_v1",
    "memory_embeddings_v2",
}


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
    tables: set[str] = set()
    for node in ast.walk(TREE):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "create_table" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            tables.add(first.value)
    return tables


def test_revision_chain_and_scope_are_exact() -> None:
    assert _assigned_string("revision") == "0013"
    assert _assigned_string("down_revision") == "0012"
    scope = ast.get_source_segment(SOURCE, _function("_migration_schema_scope"))
    assert scope is not None
    assert '{"global", "tenant"}' in scope
    assert "unsupported migration_schema_scope" in scope


def test_global_upgrade_is_an_explicit_noop_and_tenant_upgrade_is_closed() -> None:
    upgrade = ast.get_source_segment(SOURCE, _function("upgrade"))
    assert upgrade is not None
    assert 'if _migration_schema_scope() == "global":\n        return' in upgrade
    assert upgrade.index("return") < upgrade.index("_create_candidates()")
    for helper in (
        "_create_candidates()",
        "_create_memories_and_versions()",
        "_create_review_and_capsules()",
        "_create_effects_and_tombstones()",
        '_create_embedding_lane("memory_embeddings_v1", 1024)',
        '_create_embedding_lane("memory_embeddings_v2", 1536)',
        "_install_tenant_triggers()",
    ):
        assert helper in upgrade


def test_exact_table_set_and_independent_vector_lanes_are_declared() -> None:
    assert _create_table_literals() == EXPECTED_TABLES - {
        "memory_embeddings_v1",
        "memory_embeddings_v2",
    }
    assert "_MEMORY_VECTOR_LANE_VERSIONS = (1, 2)" in SOURCE
    assert "Vector(dimension)" in SOURCE
    assert '"memory_embeddings_v1", 1024' in SOURCE
    assert '"memory_embeddings_v2", 1536' in SOURCE
    assert "memory_embeddings_v1_embedding_hnsw_idx" not in SOURCE
    assert 'f"{table}_embedding_hnsw_idx"' in SOURCE
    assert 'postgresql_ops={"embedding": "vector_cosine_ops"}' in SOURCE


def test_local_user_fk_and_tenant_schema_binding_are_both_required() -> None:
    assert SOURCE.count('sa.ForeignKey("users.id", ondelete="RESTRICT")') >= 7
    assert 'sa.ForeignKey(f"{_GLOBAL_SCHEMA}.tenants.id", ondelete="RESTRICT")' in SOURCE
    assert "memory_assert_tenant_schema_binding" in SOURCE
    assert "t.id = NEW.tenant_id AND t.schema_name = TG_TABLE_SCHEMA" in SOURCE
    assert "t.schema_name = current_schema()" not in SOURCE
    assert "memory tenant_id does not match current tenant schema" in SOURCE
    assert "FOREACH table_name IN ARRAY" in SOURCE


@pytest.mark.parametrize(
    "binding",
    [
        "memory_candidates_workspace_tenant_fk",
        "memory_candidates_version_tenant_fk",
        "memory_candidates_task_tenant_fk",
        "memory_candidates_source_capsule_tenant_fk",
        "memory_candidates_resource_tenant_fk",
        "memory_candidates_active_memory_tenant_fk",
        "memory_candidates_accept_operation_tenant_fk",
        "memory_candidates_accept_approval_tenant_fk",
        "memories_candidate_tenant_fk",
        "memories_current_version_tenant_fk",
        "memories_deletion_effect_tenant_fk",
        "memory_versions_memory_tenant_fk",
        "memory_versions_resource_tenant_fk",
        "memory_review_memory_tenant_fk",
        "memories_review_evidence_tenant_fk",
        "context_capsules_workspace_tenant_fk",
        "context_capsules_agent_version_tenant_fk",
        "context_capsules_task_tenant_fk",
        "context_capsule_items_capsule_tenant_fk",
        "context_capsule_items_memory_version_fk",
        "context_capsule_items_review_tenant_fk",
        "context_capsule_items_resource_tenant_fk",
        "memory_effects_operation_tenant_fk",
        "memory_effects_memory_tenant_fk",
        "memory_effects_candidate_tenant_fk",
        "memory_tombstones_memory_tenant_fk",
        "memory_tombstones_effect_tenant_fk",
        "memory_tombstones_workspace_tenant_fk",
    ],
)
def test_composite_tenant_foreign_key_is_present(binding: str) -> None:
    assert binding in SOURCE


def test_scope_and_state_closed_sets_cover_persistent_records() -> None:
    for scope in (
        "user_private",
        "workspace_private",
        "agent_private",
        "controlled_shared",
    ):
        assert scope in SOURCE
    assert "memories_scope_shape_check" in SOURCE
    assert "context_capsule_items_scope_shape_check" in SOURCE
    assert "scope = 'controlled_shared'" in SOURCE
    assert "review_evidence_id IS NOT NULL" in SOURCE
    assert (
        "lifecycle_state IN ('candidate', 'awaiting_confirmation', 'accepted', 'rejected', 'superseded')"
        in SOURCE
    )
    assert "lifecycle_state IN ('active', 'blocked', 'deletion_pending', 'deleted')" in SOURCE
    assert "state IN ('pending', 'committed', 'failed', 'unknown')" in SOURCE
    assert "state IN ('pending', 'completed')" in SOURCE


def test_candidate_task_and_controlled_shared_review_are_revalidated_by_trigger() -> None:
    for binding in (
        "t.id = NEW.task_id",
        "t.tenant_id = NEW.tenant_id",
        "t.workspace_id = NEW.workspace_id",
        "t.agent_version_id = NEW.agent_version_id",
        "t.actor_user_id = NEW.owner_user_id",
    ):
        assert binding in SOURCE
    for binding in (
        "capsule.id = NEW.source_capsule_id",
        "capsule.tenant_id = NEW.tenant_id",
        "source_capsule.owner_user_id IS DISTINCT FROM NEW.owner_user_id",
        "source_capsule.workspace_id IS DISTINCT FROM NEW.workspace_id",
        "source_capsule.agent_version_id IS DISTINCT FROM NEW.agent_version_id",
        "source_capsule.task_id IS DISTINCT FROM NEW.task_id",
        "source_capsule.invocation_id IS DISTINCT FROM NEW.invocation_id",
        "source_capsule.memory_policy_id IS DISTINCT FROM NEW.memory_policy_id",
        "memory candidate source capsule binding drifted",
    ):
        assert binding in SOURCE
    for binding in (
        "review.decision <> 'approved'",
        "review.reviewer_user_id IS DISTINCT FROM memory.owner_user_id",
        "review.workspace_id IS DISTINCT FROM NEW.workspace_id",
        "review.memory_id IS DISTINCT FROM NEW.memory_id",
        "review.memory_version IS DISTINCT FROM NEW.memory_version",
        "review.content_sha256 IS DISTINCT FROM NEW.content_sha256",
        "review.evidence_sha256 IS DISTINCT FROM NEW.review_evidence_sha256",
        "memory.review_evidence_id IS DISTINCT FROM review.id",
        "review.reviewed_at < version.created_at",
        "review.reviewed_at > capsule.issued_at",
    ):
        assert binding in SOURCE


def test_immutable_evidence_and_tombstone_gated_payload_deletion_are_database_enforced() -> None:
    for trigger in (
        "memory_review_evidence_append_only",
        "context_capsules_append_only",
        "context_capsule_items_append_only",
        "memory_tombstones_state_guard",
        "memory_versions_payload_guard",
        "memory_embeddings_v1_payload_guard",
        "memory_embeddings_v2_payload_guard",
        "memory_effects_state_guard",
    ):
        assert trigger in SOURCE
    assert "memory version deletion requires exact pending tombstone" in SOURCE
    assert "memory embedding deletion requires exact pending tombstone" in SOURCE
    assert "memory identity cannot be deleted; use a tombstone" in SOURCE
    assert "invalid memory effect transition" in SOURCE


def test_candidate_insert_must_begin_candidate_without_acceptance_evidence() -> None:
    for fragment in (
        "NEW.lifecycle_state <> 'candidate'",
        "NEW.active_memory_id IS NOT NULL",
        "NEW.acceptance_operation_id IS NOT NULL",
        "NEW.acceptance_approval_id IS NOT NULL",
        "NEW.confirmed_by_user_id IS NOT NULL",
        "NEW.confirmed_at IS NOT NULL",
        "NEW.confirmation_sha256 IS NOT NULL",
        "memory candidate insert must begin unaccepted",
    ):
        assert fragment in SOURCE


def test_candidate_acceptance_requires_consumed_owner_approval_and_operation() -> None:
    for fragment in (
        "OLD.lifecycle_state NOT IN ('candidate', 'awaiting_confirmation')",
        "NEW.confirmed_by_user_id IS DISTINCT FROM NEW.owner_user_id",
        "operation.actor_type = 'agent'",
        "operation.actor_id = task.agent_definition_id",
        "operation.resource_id = NEW.source_resource_id",
        "operation.resource_version = NEW.source_resource_version",
        "operation.approval_id = NEW.acceptance_approval_id",
        "operation.kind = 'memory.candidate.accept'",
        "operation.state = 'succeeded'",
        "operation.request_hash = NEW.confirmation_sha256",
        "approval.operation_id = NEW.acceptance_operation_id",
        "approval.action = 'memory.candidate.accept'",
        "approval.requester_type = 'agent'",
        "approval.requester_id = task.agent_definition_id",
        "approval.resource_id = NEW.source_resource_id",
        "approval.resource_version = NEW.source_resource_version",
        "approval.state = 'consumed'",
        "approval.decided_by_actor_type = 'user'",
        "approval.decided_by_actor_id = NEW.owner_user_id",
        "approval.consumed_at IS NOT NULL",
        "approval.request_hash = NEW.confirmation_sha256",
        "approval.decided_at <= NEW.confirmed_at",
        "approval.consumed_at <= NEW.confirmed_at",
        "owner.id = NEW.owner_user_id AND owner.is_active IS TRUE",
    ):
        assert fragment in SOURCE


def test_candidate_source_capsule_binding_is_persistent_and_immutable() -> None:
    assert 'sa.Column("source_capsule_id", _UUID, nullable=False)' in SOURCE
    assert '"memory_candidates_source_capsule_tenant_fk"' in SOURCE
    assert "NEW.source_capsule_id IS DISTINCT FROM OLD.source_capsule_id" in SOURCE


def test_deferred_candidate_self_activation_is_blocked() -> None:
    assert SOURCE.count("CREATE CONSTRAINT TRIGGER") >= 2
    assert SOURCE.count("DEFERRABLE INITIALLY DEFERRED") >= 2
    assert "memory_candidates_publication_binding" in SOURCE
    assert "memories_candidate_publication_binding" in SOURCE
    assert "candidate.lifecycle_state <> 'accepted'" in SOURCE


def test_memory_candidate_binding_is_bidirectional_and_exact() -> None:
    assert "memory insert must begin active at version one" in SOURCE
    for fragment in (
        "candidate.tenant_id IS DISTINCT FROM memory.tenant_id",
        "candidate.owner_user_id IS DISTINCT FROM memory.owner_user_id",
        "candidate.requested_scope IS DISTINCT FROM memory.scope",
        "candidate.active_memory_id IS DISTINCT FROM memory.id",
        "memory.created_from_candidate_id IS DISTINCT FROM candidate.id",
        "candidate.content_sha256 IS DISTINCT FROM initial_version.content_sha256",
        "candidate.source_resource_id IS DISTINCT FROM initial_version.source_resource_id",
        "candidate.source_resource_version IS DISTINCT FROM initial_version.source_resource_version",
        "memory.current_version <> 1",
    ):
        assert fragment in SOURCE


def test_controlled_shared_review_requires_owner_and_valid_time_window() -> None:
    for fragment in (
        "review.decision <> 'approved'",
        "review.reviewer_user_id IS DISTINCT FROM memory.owner_user_id",
        "memory.review_evidence_id IS DISTINCT FROM review.id",
        "review.memory_id IS DISTINCT FROM memory.id",
        "review.memory_version IS DISTINCT FROM memory.current_version",
        "review.content_sha256 IS DISTINCT FROM current_version.content_sha256",
        "review.reviewed_at < current_version.created_at",
        "review.reviewed_at < version.created_at",
        "review.reviewed_at > capsule.issued_at",
        "owner.id = review.reviewer_user_id",
        "owner.is_active IS TRUE",
    ):
        assert fragment in SOURCE


def test_append_only_review_evidence_does_not_block_payload_version_erasure() -> None:
    assert 'name="memory_review_memory_tenant_fk"' in SOURCE
    assert 'name="memory_review_version_tenant_fk"' not in SOURCE
    for fragment in (
        "memory_review_evidence_insert_binding",
        "memory review evidence binding drifted",
        "version.content_sha256 IS DISTINCT FROM NEW.content_sha256",
        "NEW.reviewed_at < version.created_at",
        "memory.owner_user_id IS DISTINCT FROM NEW.reviewer_user_id",
    ):
        assert fragment in SOURCE


def test_deletion_enters_pending_before_payload_erasure() -> None:
    assert "'deletion_pending'" in SOURCE
    assert "OLD.lifecycle_state IN ('active', 'blocked')" in SOURCE
    assert "NEW.lifecycle_state = 'deletion_pending'" in SOURCE
    assert "OLD.lifecycle_state = 'deletion_pending' AND NEW.lifecycle_state = 'deleted'" in SOURCE
    assert "memory_exact_pending_delete" in SOURCE


def test_tombstone_binds_exact_committed_delete_effect() -> None:
    for fragment in (
        "memory.lifecycle_state <> 'deletion_pending'",
        "memory.current_version IS DISTINCT FROM NEW.last_memory_version",
        "memory.deletion_effect_id IS DISTINCT FROM NEW.deletion_effect_id",
        "effect.memory_id IS DISTINCT FROM memory.id",
        "effect.owner_user_id IS DISTINCT FROM memory.owner_user_id",
        "effect.workspace_id IS DISTINCT FROM memory.workspace_id",
        "effect.effect_kind <> 'delete'",
        "effect.state <> 'committed'",
        "effect.request_sha256 IS DISTINCT FROM NEW.request_sha256",
        "effect.result_sha256 IS DISTINCT FROM NEW.result_sha256",
        "NEW.deletion_sha256 IS DISTINCT FROM effect.result_sha256",
    ):
        assert fragment in SOURCE


def test_memory_version_crypto_erasure_removes_payload_row() -> None:
    assert "memory version is immutable" in SOURCE
    assert "memory version deletion requires exact pending tombstone" in SOURCE
    assert "current_version IS NULL OR current_version >= 1" in SOURCE
    assert "lifecycle_state = 'deleted' AND current_version IS NULL" in SOURCE
    assert "memory_candidates_payload_parity_check" in SOURCE
    assert 'ondelete="NO ACTION",\n        deferrable=True,\n        initially="DEFERRED"' in SOURCE


def test_embedding_delete_requires_pending_exact_tombstone() -> None:
    assert "memory embedding is immutable" in SOURCE
    assert "memory embedding deletion requires exact pending tombstone" in SOURCE
    assert SOURCE.count("memory_exact_pending_delete(OLD.memory_id, OLD.tenant_id)") >= 2


def test_tombstone_completion_requires_all_payload_removed() -> None:
    for fragment in (
        "memory tombstone cannot complete before crypto-erasure",
        "memory tombstone completion lost exact delete binding",
        "SELECT 1 FROM memory_versions stored_version",
        "candidate.content_ciphertext IS NOT NULL",
        "candidate.content_nonce IS NOT NULL",
        "FROM memory_embeddings_v1 embedding",
        "FROM memory_embeddings_v2 embedding",
    ):
        assert fragment in SOURCE


def test_memory_deleted_requires_completed_tombstone() -> None:
    for fragment in (
        "tombstone.state = 'completed'",
        "tombstone.completed_at IS NOT NULL",
        "tombstone.completed_at <= NEW.deleted_at",
        "memory deletion requires a completed exact tombstone",
    ):
        assert fragment in SOURCE


def test_tombstone_identity_is_immutable() -> None:
    assert "memory tombstone identity is append-only" in SOURCE
    assert "memory tombstone identity is immutable" in SOURCE
    assert "OLD.state <> 'pending' OR NEW.state <> 'completed'" in SOURCE


def test_populated_tenant_and_global_downgrade_fail_closed() -> None:
    downgrade = ast.get_source_segment(SOURCE, _function("downgrade"))
    global_guard = ast.get_source_segment(SOURCE, _function("_assert_global_downgrade_safe"))
    dependency_drop = ast.get_source_segment(
        SOURCE, _function("_drop_empty_tenant_global_dependencies")
    )
    assert downgrade is not None
    assert global_guard is not None
    assert dependency_drop is not None
    assert "_assert_global_downgrade_safe()" in downgrade
    assert "_drop_empty_tenant_global_dependencies()" in downgrade
    assert "0013 populated tenant downgrade is forbidden" in downgrade
    assert "schema_name FROM omnibase_meta.tenants" in global_guard
    assert "tenant memory table set is incomplete" in global_guard
    assert "0013 populated downgrade is forbidden" in global_guard
    assert "constraint_row.contype = 'f'" in dependency_drop
    assert "target_schema.nspname = :global_schema" in dependency_drop
    assert "_GLOBAL_DEPENDENCY_REVISIONS" in dependency_drop
    assert "introduced_revision <= target_revision" in dependency_drop
    assert "DROP CONSTRAINT" in dependency_drop
    assert "op.drop_table" not in dependency_drop
    assert "omnibase_restore_*" in downgrade
    assert "omnibase_restore_*" in global_guard


def test_migration_does_not_import_runtime_api_network_or_secret_stack() -> None:
    imports: set[str] = set()
    for node in ast.walk(TREE):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    forbidden = (
        "fastapi",
        "requests",
        "httpx",
        "subprocess",
        "omnibase.agent_alpha",
        "omnibase.model_gateway",
        "omnibase.task_ledger",
    )
    assert not any(name.startswith(forbidden) for name in imports)
    assert ".env" not in SOURCE
    assert "AGENT_RUNTIME_ENABLED" not in SOURCE
    assert "AGENT_PLANNER_ENABLED" not in SOURCE
    assert "MULTI_AGENT_ENABLED" not in SOURCE


def test_constraint_and_trigger_names_fit_postgresql_identifier_limit() -> None:
    names = re.findall(r'name=(?:f)?["\']([^"\']+)["\']', SOURCE)
    literal_names = [name for name in names if "{" not in name]
    assert literal_names
    assert all(len(name) <= 63 for name in literal_names)
