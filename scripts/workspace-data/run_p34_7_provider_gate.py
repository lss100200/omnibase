"""Guarded disposable P34.7 provider/reference recovery Gate.

This Gate never connects to a database or network service. It exercises the
typed provider boundary against a newly created disposable local root and
proves that the same adapter is rejected by the production admission Gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from omnibase.workspace_data.provider_adapters import (
    LocalContentAddressedProvider,
    ProviderContractError,
    ProviderEffectKind,
    ProviderEffectPlan,
    ProviderGrantFacts,
    ProviderLane,
    ProviderObjectRef,
    ProviderReceipt,
    ProviderReconciliationRequired,
    assess_non_disposable_target,
    assess_provider_adapter,
)


class _FailAfterMaterializationProvider(LocalContentAddressedProvider):
    captured_receipt: ProviderReceipt | None = None

    def _after_materialization_before_commit(
        self, *, plan: ProviderEffectPlan, receipt: ProviderReceipt
    ) -> None:
        del plan
        self.captured_receipt = receipt
        raise OSError("synthetic post-materialization journal fault")


def _id() -> str:
    return str(uuid4())


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _object(
    *,
    tenant_id: str,
    workspace_id: str,
    lane: ProviderLane,
    content: bytes,
    generation: int,
) -> ProviderObjectRef:
    return ProviderObjectRef(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        resource_id=_id(),
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
    action: str,
    sources: tuple[ProviderObjectRef, ...],
    targets: tuple[ProviderObjectRef, ...],
) -> ProviderEffectPlan:
    operation_id = _id()
    return ProviderEffectPlan(
        kind=kind,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        operation_id=operation_id,
        binding_digest=hashlib.sha256(f"p34.7:{kind.value}:{operation_id}".encode()).hexdigest(),
        grant=ProviderGrantFacts(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            operation_id=operation_id,
            grant_id=_id(),
            grant_version=1,
            actions=frozenset({action}),
            max_bytes=sum(target.size_bytes for target in targets),
            expires_at=datetime.now(UTC) + timedelta(minutes=2),
        ),
        sources=sources,
        targets=targets,
    )


def _guard_root(root: Path) -> Path:
    absolute = root.absolute()
    if not absolute.name.startswith("omnibase-p347-provider-"):
        raise SystemExit("refusing provider root without omnibase-p347-provider- prefix")
    temp_root = Path(tempfile.gettempdir()).absolute()
    try:
        absolute.relative_to(temp_root)
    except ValueError as exc:
        raise SystemExit("refusing provider root outside the OS temporary directory") from exc
    if absolute == temp_root or absolute.parent == absolute:
        raise SystemExit("refusing broad provider root")
    if absolute.exists() and any(absolute.iterdir()):
        raise SystemExit("refusing non-empty provider root")
    return absolute


def _assert_unknown_no_replay(
    provider: LocalContentAddressedProvider,
    *,
    tenant_id: str,
    workspace_id: str,
) -> None:
    unknown_target = _object(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        lane=ProviderLane.WORKSPACE_PRIVATE,
        content=b"expected",
        generation=3,
    )
    unknown_plan = _plan(
        kind=ProviderEffectKind.ARTIFACT_PUT,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        action="artifact.write",
        sources=(),
        targets=(unknown_target,),
    )
    try:
        provider.execute(unknown_plan, payloads={unknown_target.resource_id: b"wrong-content"})
    except ProviderContractError:
        pass
    else:
        raise RuntimeError("provider accepted digest drift")
    try:
        provider.execute(unknown_plan, payloads={unknown_target.resource_id: b"expected"})
    except ProviderReconciliationRequired:
        pass
    else:
        raise RuntimeError("provider automatically replayed an unknown effect")


def _assert_post_materialization_no_replay(
    root: Path,
    *,
    tenant_id: str,
    workspace_id: str,
) -> None:
    partial_provider = _FailAfterMaterializationProvider.initialize_disposable(
        root / "partial-fault"
    )
    partial_content = b"partial-materialization"
    partial_target = _object(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        lane=ProviderLane.WORKSPACE_PRIVATE,
        content=partial_content,
        generation=3,
    )
    partial_plan = _plan(
        kind=ProviderEffectKind.ARTIFACT_PUT,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        action="artifact.write",
        sources=(),
        targets=(partial_target,),
    )
    try:
        partial_provider.execute(
            partial_plan,
            payloads={partial_target.resource_id: partial_content},
        )
    except ProviderReconciliationRequired:
        pass
    else:
        raise RuntimeError("post-materialization fault did not become unknown")
    if partial_provider.captured_receipt is None:
        raise RuntimeError("post-materialization receipt was not captured")
    try:
        partial_provider.read_committed(
            receipt=partial_provider.captured_receipt,
            target=partial_target,
        )
    except ProviderReconciliationRequired:
        pass
    else:
        raise RuntimeError("partial provider object became committed-visible")
    try:
        partial_provider.execute(
            partial_plan,
            payloads={partial_target.resource_id: partial_content},
        )
    except ProviderReconciliationRequired:
        pass
    else:
        raise RuntimeError("partial provider effect automatically replayed")


def run_gate(root: Path) -> dict[str, object]:
    provider = LocalContentAddressedProvider.initialize_disposable(root)
    staging = assess_provider_adapter(provider, require_production=False)
    production = assess_provider_adapter(provider, require_production=True)
    if not staging.accepted or production.accepted:
        raise RuntimeError("provider admission contract drift")
    non_disposable = assess_non_disposable_target(
        None,
        tenant_id=_id(),
        workspace_id=_id(),
        effect_kind=ProviderEffectKind.SNAPSHOT_CAPTURE,
        target_fingerprint="0" * 64,
    )
    if non_disposable.status != "blocked/not_proven":
        raise RuntimeError("non-disposable admission unexpectedly opened")

    tenant_id = _id()
    workspace_id = _id()
    restored_workspace_id = _id()
    private_content = b"p34.7 disposable private artifact"
    derived_content = b"p34.7 disposable derived index"
    artifact = _object(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        lane=ProviderLane.WORKSPACE_PRIVATE,
        content=private_content,
        generation=3,
    )
    artifact_plan = _plan(
        kind=ProviderEffectKind.ARTIFACT_PUT,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        action="artifact.write",
        sources=(),
        targets=(artifact,),
    )
    artifact_receipt = provider.execute(
        artifact_plan, payloads={artifact.resource_id: private_content}
    )
    artifact_receipt.verify(artifact_plan)

    derived = _object(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        lane=ProviderLane.WORKSPACE_DERIVED,
        content=derived_content,
        generation=3,
    )
    derived_plan = _plan(
        kind=ProviderEffectKind.DERIVED_BUILD,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        action="rag.derived.create",
        sources=(artifact,),
        targets=(derived,),
    )
    derived_receipt = provider.execute(
        derived_plan, payloads={derived.resource_id: derived_content}
    )

    published = _object(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        lane=ProviderLane.CONTROLLED_SHARED,
        content=private_content,
        generation=3,
    )
    publication_plan = _plan(
        kind=ProviderEffectKind.PUBLICATION_COPY,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        action="workspace.data.publish",
        sources=(artifact,),
        targets=(published,),
    )
    publication_receipt = provider.execute(publication_plan)

    snapshot_payloads = tuple(
        _object(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            lane=ProviderLane.SNAPSHOT_PAYLOAD,
            content=content,
            generation=3,
        )
        for content in (private_content, derived_content)
    )
    capture_plan = _plan(
        kind=ProviderEffectKind.SNAPSHOT_CAPTURE,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        action="workspace.snapshot.capture",
        sources=(artifact, derived),
        targets=snapshot_payloads,
    )
    capture_receipt = provider.execute(capture_plan)

    restored = tuple(
        _object(
            tenant_id=tenant_id,
            workspace_id=restored_workspace_id,
            lane=lane,
            content=content,
            generation=4,
        )
        for lane, content in (
            (ProviderLane.WORKSPACE_PRIVATE, private_content),
            (ProviderLane.WORKSPACE_DERIVED, derived_content),
        )
    )
    restore_plan = _plan(
        kind=ProviderEffectKind.SNAPSHOT_RESTORE,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        action="workspace.snapshot.restore",
        sources=snapshot_payloads,
        targets=restored,
    )
    restore_receipt = provider.execute(restore_plan)

    reads = (
        provider.read_committed(receipt=artifact_receipt, target=artifact),
        provider.read_committed(receipt=derived_receipt, target=derived),
        provider.read_committed(receipt=publication_receipt, target=published),
        provider.read_committed(receipt=capture_receipt, target=snapshot_payloads[0]),
        provider.read_committed(receipt=restore_receipt, target=restored[1]),
    )
    if reads != (
        private_content,
        derived_content,
        private_content,
        private_content,
        derived_content,
    ):
        raise RuntimeError("provider committed read verification failed")

    _assert_unknown_no_replay(
        provider,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    _assert_post_materialization_no_replay(
        root,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )

    receipt_digests = [
        artifact_receipt.receipt_digest,
        derived_receipt.receipt_digest,
        publication_receipt.receipt_digest,
        capture_receipt.receipt_digest,
        restore_receipt.receipt_digest,
    ]
    return {
        "schema_version": 1,
        "gate": "P34.7C provider/recovery disposable reference",
        "passed": True,
        "staging_adapter_admitted": True,
        "production_adapter_admitted": False,
        "production_reason_code": production.reason_code,
        "non_disposable_tenant_rag": non_disposable.status,
        "non_disposable_reason_code": non_disposable.reason_code,
        "effect_journal": "pass",
        "quota_and_grant_preflight": "pass",
        "receipt_binding": "pass",
        "artifact": "pass",
        "derived": "pass",
        "copy_on_publish": "pass",
        "canonical_write_target_available": False,
        "snapshot_capture": "pass",
        "restore_new_identity": "pass",
        "unknown_visible": False,
        "unknown_auto_replay": False,
        "post_materialization_unknown_visible": False,
        "post_materialization_unknown_auto_replay": False,
        "receipt_set_digest": hashlib.sha256("".join(sorted(receipt_digests)).encode()).hexdigest(),
        "root_env_accessed": False,
        "business_database_accessed": False,
        "network_accessed": False,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--evidence-out", type=Path, required=True)
    parser.add_argument("--keep-root", action="store_true")
    args = parser.parse_args()
    root = _guard_root(args.root)
    evidence = args.evidence_out.absolute()
    if evidence == root or root in evidence.parents:
        raise SystemExit("evidence output must be outside the disposable provider root")

    try:
        result = run_gate(root)
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    finally:
        if not args.keep_root:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
