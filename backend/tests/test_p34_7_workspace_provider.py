"""P34.7 typed provider boundary and disposable reference-adapter Gate tests."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from omnibase.workspace_data.provider_adapters import (
    LocalContentAddressedProvider,
    ProviderAdmissionDenied,
    ProviderContractError,
    ProviderEffectKind,
    ProviderEffectPlan,
    ProviderGrantFacts,
    ProviderLane,
    ProviderObjectRef,
    ProviderReceipt,
    ProviderReconciliationRequired,
    VerifiedNonDisposableAdmissionFacts,
    assess_non_disposable_target,
    assess_provider_adapter,
)


def _id() -> str:
    return str(uuid4())


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _grant(
    *,
    tenant_id: str,
    workspace_id: str,
    operation_id: str,
    action: str,
    max_bytes: int = 1024 * 1024,
) -> ProviderGrantFacts:
    return ProviderGrantFacts(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        operation_id=operation_id,
        grant_id=_id(),
        grant_version=1,
        actions=frozenset({action}),
        max_bytes=max_bytes,
        expires_at=datetime.now(UTC) + timedelta(minutes=2),
    )


def _ref(
    *,
    tenant_id: str,
    workspace_id: str,
    resource_id: str,
    lane: ProviderLane,
    content: bytes,
    generation: int = 1,
) -> ProviderObjectRef:
    return ProviderObjectRef(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        resource_id=resource_id,
        resource_version=1,
        lane=lane,
        content_digest=_digest(content),
        size_bytes=len(content),
        workspace_generation=generation,
    )


def _plan(
    *,
    kind: ProviderEffectKind,
    tenant_id: str,
    workspace_id: str,
    sources: tuple[ProviderObjectRef, ...],
    targets: tuple[ProviderObjectRef, ...],
    action: str,
    operation_id: str | None = None,
    max_bytes: int = 1024 * 1024,
) -> ProviderEffectPlan:
    actual_operation_id = operation_id or _id()
    return ProviderEffectPlan(
        kind=kind,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        operation_id=actual_operation_id,
        binding_digest=hashlib.sha256(f"{kind.value}:{actual_operation_id}".encode()).hexdigest(),
        grant=_grant(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            operation_id=actual_operation_id,
            action=action,
            max_bytes=max_bytes,
        ),
        sources=sources,
        targets=targets,
    )


def _provider(tmp_path: Path) -> LocalContentAddressedProvider:
    return LocalContentAddressedProvider.initialize_disposable(tmp_path / "provider")


class _FailAfterMaterializationProvider(LocalContentAddressedProvider):
    captured_receipt: ProviderReceipt | None = None

    def _after_materialization_before_commit(
        self, *, plan: ProviderEffectPlan, receipt: ProviderReceipt
    ) -> None:
        del plan
        self.captured_receipt = receipt
        raise OSError("synthetic post-materialization journal fault")


def test_local_reference_passes_staging_gate_but_never_production_gate(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path)
    staging = assess_provider_adapter(provider, require_production=False)
    production = assess_provider_adapter(provider, require_production=True)

    assert staging.accepted is True
    assert staging.reason_code == "accepted"
    assert production.accepted is False
    assert production.reason_code == "production_evidence_not_admitted"
    assert production.provider_kind == "local_content_addressed_reference"

    unmarked = tmp_path / "unmarked"
    unmarked.mkdir()
    with pytest.raises(ProviderAdmissionDenied, match="marker"):
        LocalContentAddressedProvider(unmarked)


def test_non_disposable_target_is_blocked_without_exact_data_owner_admission() -> None:
    tenant_id = _id()
    workspace_id = _id()
    fingerprint = "f" * 64
    blocked = assess_non_disposable_target(
        None,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        effect_kind=ProviderEffectKind.SNAPSHOT_CAPTURE,
        target_fingerprint=fingerprint,
    )
    assert blocked.status == "blocked/not_proven"
    assert blocked.reason_code == "data_owner_authorization_missing"

    now = datetime.now(UTC)
    facts = VerifiedNonDisposableAdmissionFacts(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        authorization_id=_id(),
        data_owner_user_id=_id(),
        target_fingerprint=fingerprint,
        allowed_effects=frozenset({ProviderEffectKind.SNAPSHOT_CAPTURE}),
        verified_at=now,
        expires_at=now + timedelta(minutes=2),
        data_owner_approved=True,
    )
    admitted = assess_non_disposable_target(
        facts,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        effect_kind=ProviderEffectKind.SNAPSHOT_CAPTURE,
        target_fingerprint=fingerprint,
        now=now + timedelta(seconds=1),
    )
    assert admitted.admitted is True
    assert admitted.status == "admitted"

    mismatched = assess_non_disposable_target(
        facts,
        tenant_id=tenant_id,
        workspace_id=_id(),
        effect_kind=ProviderEffectKind.SNAPSHOT_CAPTURE,
        target_fingerprint=fingerprint,
        now=now + timedelta(seconds=1),
    )
    assert mismatched.status == "blocked/not_proven"


def test_artifact_and_derived_are_content_bound_and_exact_commit_replay_is_read_only(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path)
    tenant_id = _id()
    workspace_id = _id()
    artifact_content = b"workspace-private artifact"
    artifact = _ref(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        resource_id=_id(),
        lane=ProviderLane.WORKSPACE_PRIVATE,
        content=artifact_content,
    )
    artifact_plan = _plan(
        kind=ProviderEffectKind.ARTIFACT_PUT,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        sources=(),
        targets=(artifact,),
        action="artifact.write",
    )
    receipt = provider.execute(artifact_plan, payloads={artifact.resource_id: artifact_content})
    receipt.verify(artifact_plan)
    assert provider.read_committed(receipt=receipt, target=artifact) == artifact_content

    replay = provider.execute(
        artifact_plan, payloads={artifact.resource_id: b"must-not-be-written"}
    )
    assert replay == receipt
    assert provider.read_committed(receipt=replay, target=artifact) == artifact_content

    derived_content = b"derived index payload"
    derived = _ref(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        resource_id=_id(),
        lane=ProviderLane.WORKSPACE_DERIVED,
        content=derived_content,
    )
    derived_plan = _plan(
        kind=ProviderEffectKind.DERIVED_BUILD,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        sources=(artifact,),
        targets=(derived,),
        action="rag.derived.create",
    )
    derived_receipt = provider.execute(
        derived_plan, payloads={derived.resource_id: derived_content}
    )
    assert provider.read_committed(receipt=derived_receipt, target=derived) == derived_content


def test_publication_is_copy_on_publish_and_canonical_target_is_unrepresentable(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path)
    tenant_id = _id()
    workspace_id = _id()
    content = b"immutable source"
    source = _ref(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        resource_id=_id(),
        lane=ProviderLane.WORKSPACE_PRIVATE,
        content=content,
    )
    put = _plan(
        kind=ProviderEffectKind.ARTIFACT_PUT,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        sources=(),
        targets=(source,),
        action="artifact.write",
    )
    source_receipt = provider.execute(put, payloads={source.resource_id: content})

    target = _ref(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        resource_id=_id(),
        lane=ProviderLane.CONTROLLED_SHARED,
        content=content,
    )
    publication = _plan(
        kind=ProviderEffectKind.PUBLICATION_COPY,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        sources=(source,),
        targets=(target,),
        action="workspace.data.publish",
    )
    target_receipt = provider.execute(publication)
    assert provider.read_committed(receipt=target_receipt, target=target) == content
    assert provider.read_committed(receipt=source_receipt, target=source) == content
    assert source.resource_id != target.resource_id

    forbidden_target = _ref(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        resource_id=_id(),
        lane=ProviderLane.WORKSPACE_PRIVATE,
        content=content,
    )
    with pytest.raises(ProviderContractError, match="target lane"):
        _plan(
            kind=ProviderEffectKind.PUBLICATION_COPY,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            sources=(source,),
            targets=(forbidden_target,),
            action="workspace.data.publish",
        )

    assert "canonical_readonly" not in {lane.value for lane in ProviderLane}


def test_snapshot_capture_and_restore_require_new_identity_and_generation(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path)
    tenant_id = _id()
    source_workspace_id = _id()
    restored_workspace_id = _id()
    content = b"snapshot payload"
    source = _ref(
        tenant_id=tenant_id,
        workspace_id=source_workspace_id,
        resource_id=_id(),
        lane=ProviderLane.WORKSPACE_PRIVATE,
        content=content,
        generation=4,
    )
    source_plan = _plan(
        kind=ProviderEffectKind.ARTIFACT_PUT,
        tenant_id=tenant_id,
        workspace_id=source_workspace_id,
        sources=(),
        targets=(source,),
        action="artifact.write",
    )
    provider.execute(source_plan, payloads={source.resource_id: content})

    snapshot_payload = _ref(
        tenant_id=tenant_id,
        workspace_id=source_workspace_id,
        resource_id=_id(),
        lane=ProviderLane.SNAPSHOT_PAYLOAD,
        content=content,
        generation=4,
    )
    capture = _plan(
        kind=ProviderEffectKind.SNAPSHOT_CAPTURE,
        tenant_id=tenant_id,
        workspace_id=source_workspace_id,
        sources=(source,),
        targets=(snapshot_payload,),
        action="workspace.snapshot.capture",
    )
    provider.execute(capture)

    restored = _ref(
        tenant_id=tenant_id,
        workspace_id=restored_workspace_id,
        resource_id=_id(),
        lane=ProviderLane.WORKSPACE_PRIVATE,
        content=content,
        generation=5,
    )
    restore = _plan(
        kind=ProviderEffectKind.SNAPSHOT_RESTORE,
        tenant_id=tenant_id,
        workspace_id=source_workspace_id,
        sources=(snapshot_payload,),
        targets=(restored,),
        action="workspace.snapshot.restore",
    )
    restore_receipt = provider.execute(restore)
    assert provider.read_committed(receipt=restore_receipt, target=restored) == content
    assert restored.workspace_id != source.workspace_id
    assert restored.resource_id not in {source.resource_id, snapshot_payload.resource_id}
    assert restored.workspace_generation > source.workspace_generation

    same_identity = _ref(
        tenant_id=tenant_id,
        workspace_id=source_workspace_id,
        resource_id=_id(),
        lane=ProviderLane.WORKSPACE_PRIVATE,
        content=content,
        generation=5,
    )
    with pytest.raises(ProviderContractError, match="new workspace identity"):
        _plan(
            kind=ProviderEffectKind.SNAPSHOT_RESTORE,
            tenant_id=tenant_id,
            workspace_id=source_workspace_id,
            sources=(snapshot_payload,),
            targets=(same_identity,),
            action="workspace.snapshot.restore",
        )


def test_revoked_expired_cross_scope_and_over_quota_grants_fail_before_effect(
    tmp_path: Path,
) -> None:
    _provider(tmp_path)
    tenant_id = _id()
    workspace_id = _id()
    operation_id = _id()
    content = b"quota-bound"
    target = _ref(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        resource_id=_id(),
        lane=ProviderLane.WORKSPACE_PRIVATE,
        content=content,
    )
    with pytest.raises(ProviderContractError, match="quota"):
        _plan(
            kind=ProviderEffectKind.ARTIFACT_PUT,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            sources=(),
            targets=(target,),
            action="artifact.write",
            operation_id=operation_id,
            max_bytes=len(content) - 1,
        )

    base = _grant(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        operation_id=operation_id,
        action="artifact.write",
    )
    for grant, match in (
        (
            replace(base, expires_at=datetime.now(UTC) - timedelta(seconds=1)),
            "expired",
        ),
        (replace(base, revoked=True), "revoked"),
        (replace(base, workspace_id=_id()), "scope binding"),
    ):
        with pytest.raises(ProviderContractError, match=match):
            ProviderEffectPlan(
                kind=ProviderEffectKind.ARTIFACT_PUT,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                operation_id=operation_id,
                binding_digest="a" * 64,
                grant=grant,
                sources=(),
                targets=(target,),
            )

    assert list((tmp_path / "provider" / "journal").iterdir()) == []


def test_digest_failure_becomes_unknown_invisible_and_never_auto_replays(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path)
    tenant_id = _id()
    workspace_id = _id()
    expected = b"expected bytes"
    target = _ref(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        resource_id=_id(),
        lane=ProviderLane.WORKSPACE_PRIVATE,
        content=expected,
    )
    plan = _plan(
        kind=ProviderEffectKind.ARTIFACT_PUT,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        sources=(),
        targets=(target,),
        action="artifact.write",
    )
    with pytest.raises(ProviderContractError, match="digest or size"):
        provider.execute(plan, payloads={target.resource_id: b"different"})
    with pytest.raises(ProviderReconciliationRequired, match="reconciliation"):
        provider.execute(plan, payloads={target.resource_id: expected})

    binding_files = list((tmp_path / "provider" / "bindings").rglob("*.json"))
    object_files = list((tmp_path / "provider" / "objects").rglob("*.blob"))
    assert binding_files == []
    assert object_files == []


def test_post_materialization_unknown_remains_invisible_and_never_replays(
    tmp_path: Path,
) -> None:
    provider = _FailAfterMaterializationProvider.initialize_disposable(tmp_path / "provider")
    tenant_id = _id()
    workspace_id = _id()
    content = b"physically-materialized-but-not-committed"
    target = _ref(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        resource_id=_id(),
        lane=ProviderLane.WORKSPACE_PRIVATE,
        content=content,
    )
    plan = _plan(
        kind=ProviderEffectKind.ARTIFACT_PUT,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        sources=(),
        targets=(target,),
        action="artifact.write",
    )
    with pytest.raises(ProviderReconciliationRequired, match="reconciliation"):
        provider.execute(plan, payloads={target.resource_id: content})
    assert provider.captured_receipt is not None
    with pytest.raises(ProviderReconciliationRequired, match="not committed-visible"):
        provider.read_committed(receipt=provider.captured_receipt, target=target)
    with pytest.raises(ProviderReconciliationRequired, match="reconciliation"):
        provider.execute(plan, payloads={target.resource_id: content})

    assert list((tmp_path / "provider" / "bindings").rglob("*.json"))
    assert list((tmp_path / "provider" / "objects").rglob("*.blob"))
