"""Focused pure tests for the P5.5B ORM and transaction service contract."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

import pytest

from omnibase.agent_memory.models import (
    ContextCapsuleItemModel,
    ContextCapsuleModel,
    MemoryCandidateModel,
    MemoryEffectModel,
    MemoryEmbeddingV1Model,
    MemoryEmbeddingV2Model,
    MemoryModel,
    MemoryReviewEvidenceModel,
    MemoryTombstoneModel,
    MemoryVersionModel,
)
from omnibase.agent_memory.service import (
    CandidateDraft,
    OwnerMemoryOperation,
    _logical_export,
    _validate_draft,
    candidate_confirmation_sha256,
)

SERVICE_SOURCE = (
    Path(__file__).resolve().parents[1] / "src" / "omnibase" / "agent_memory" / "service.py"
).read_text(encoding="utf-8")

IDS = {
    name: f"00000000-0000-4000-8000-{index:012d}"
    for index, name in enumerate(
        (
            "tenant",
            "owner",
            "workspace",
            "version",
            "task",
            "invocation",
            "capsule",
            "policy",
            "resource",
            "operation",
            "candidate",
            "memory",
        ),
        start=1,
    )
}
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def _draft(
    *,
    requested_scope: str = "workspace_private",
    sensitivity: str = "personal",
    evidence_reference_ids: tuple[str, ...] | None = None,
    requires_user_confirmation: bool = False,
) -> CandidateDraft:
    return CandidateDraft(
        tenant_id=IDS["tenant"],
        owner_user_id=IDS["owner"],
        workspace_id=IDS["workspace"],
        agent_version_id=IDS["version"],
        task_id=IDS["task"],
        invocation_id=IDS["invocation"],
        source_capsule_id=IDS["capsule"],
        memory_policy_id=IDS["policy"],
        requested_scope=requested_scope,
        sensitivity=sensitivity,
        content_ciphertext=b"ciphertext",
        content_nonce=b"nonce",
        content_key_version=1,
        content_sha256=DIGEST_A,
        source_resource_id=IDS["resource"],
        source_resource_version=1,
        evidence_reference_ids=evidence_reference_ids or (IDS["resource"],),
        confidence_millis=900,
        retention_days=30,
        requires_user_confirmation=requires_user_confirmation,
        operation_id=IDS["operation"],
        operation_expected_version=1,
        request_sha256=DIGEST_B,
        request_id="p55b-test-request",
    )


def _candidate() -> MemoryCandidateModel:
    return MemoryCandidateModel(
        id=IDS["candidate"],
        tenant_id=IDS["tenant"],
        owner_user_id=IDS["owner"],
        workspace_id=IDS["workspace"],
        agent_version_id=IDS["version"],
        task_id=IDS["task"],
        invocation_id=IDS["invocation"],
        source_capsule_id=IDS["capsule"],
        memory_policy_id=IDS["policy"],
        requested_scope="workspace_private",
        sensitivity="personal",
        lifecycle_state="candidate",
        content_ciphertext=b"ciphertext",
        content_nonce=b"nonce",
        content_key_version=1,
        content_sha256=DIGEST_A,
        source_resource_id=IDS["resource"],
        source_resource_version=1,
        evidence_reference_ids=[IDS["resource"]],
        confidence_millis=900,
        retention_days=30,
        requires_user_confirmation=False,
        contains_secret=False,
        inferred_sensitive_categories=[],
        candidate_created_by="agent",
    )


def test_orm_maps_exact_migration_0013_table_set() -> None:
    models = (
        MemoryCandidateModel,
        MemoryModel,
        MemoryVersionModel,
        MemoryReviewEvidenceModel,
        ContextCapsuleModel,
        ContextCapsuleItemModel,
        MemoryEffectModel,
        MemoryTombstoneModel,
        MemoryEmbeddingV1Model,
        MemoryEmbeddingV2Model,
    )
    assert {model.__tablename__ for model in models} == {
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


def test_candidate_draft_rejects_sensitive_or_shared_without_confirmation() -> None:
    with pytest.raises(ValueError, match="require Owner confirmation"):
        _validate_draft(_draft(requested_scope="controlled_shared"))
    with pytest.raises(ValueError, match="require Owner confirmation"):
        _validate_draft(_draft(sensitivity="restricted"))


def test_candidate_draft_rejects_duplicate_or_noncanonical_evidence() -> None:
    with pytest.raises(ValueError, match="non-empty and unique"):
        _validate_draft(_draft(evidence_reference_ids=(IDS["resource"], IDS["resource"])))
    with pytest.raises(ValueError, match="canonical UUID"):
        _validate_draft(_draft(evidence_reference_ids=("AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",)))


def test_confirmation_digest_binds_source_capsule_and_exact_candidate() -> None:
    candidate = _candidate()
    first = candidate_confirmation_sha256(candidate)
    assert len(first) == 64
    candidate.source_capsule_id = "00000000-0000-4000-8000-999999999999"
    second = candidate_confirmation_sha256(candidate)
    assert first != second


def test_export_is_canonical_logical_metadata_without_ciphertext_or_locators() -> None:
    now = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    memory = MemoryModel(
        id=IDS["memory"],
        tenant_id=IDS["tenant"],
        owner_user_id=IDS["owner"],
        workspace_id=IDS["workspace"],
        agent_version_id=None,
        scope="workspace_private",
        sensitivity="personal",
        lifecycle_state="active",
        current_version=1,
        created_from_candidate_id=IDS["candidate"],
        review_evidence_id=None,
        deletion_effect_id=None,
        created_at=now,
        updated_at=now,
    )
    version = MemoryVersionModel(
        id=IDS["version"],
        tenant_id=IDS["tenant"],
        memory_id=IDS["memory"],
        version=1,
        content_ciphertext=b"must-not-export",
        content_nonce=b"must-not-export",
        content_key_version=7,
        content_sha256=DIGEST_A,
        source_resource_id=IDS["resource"],
        source_resource_version=2,
        evidence_reference_ids=[IDS["resource"]],
        token_count=12,
        created_at=now,
    )
    payload = _logical_export(memory, version)
    parsed = json.loads(payload)
    assert payload.endswith(b"\n")
    assert parsed["content_sha256"] == DIGEST_A
    assert "ciphertext" not in payload.decode("utf-8")
    assert "nonce" not in payload.decode("utf-8")
    assert "schema" not in payload.decode("utf-8")
    assert "table" not in payload.decode("utf-8")
    assert "database" not in payload.decode("utf-8")


def test_owner_operation_contract_is_immutable() -> None:
    operation = OwnerMemoryOperation(
        tenant_id=IDS["tenant"],
        memory_id=IDS["memory"],
        owner_user_id=IDS["owner"],
        operation_id=IDS["operation"],
        operation_expected_version=1,
        request_sha256=DIGEST_A,
        request_id="p55b-owner-operation",
    )
    with pytest.raises(FrozenInstanceError):
        operation.__setattr__("memory_id", IDS["candidate"])


def test_service_reuses_atomic_control_plane_and_never_commits() -> None:
    assert "authorize_operation(" in SERVICE_SOURCE
    assert SERVICE_SOURCE.count("append_audit_event(") >= 4
    assert ".commit(" not in SERVICE_SOURCE
    assert "session.commit" not in SERVICE_SOURCE
    assert "AGENT_RUNTIME_ENABLED" not in SERVICE_SOURCE
    assert "fastapi" not in SERVICE_SOURCE
    assert "httpx" not in SERVICE_SOURCE
    assert "subprocess" not in SERVICE_SOURCE
