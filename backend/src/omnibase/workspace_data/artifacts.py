"""Caller-owned Artifact metadata lifecycle for P34.6.

This module records logical, content-addressed metadata and a durable pending
effect.  It deliberately does not call object storage: the caller must commit
the pending database transaction before crossing the provider boundary and
then finalize the effect in a new caller-owned transaction.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from omnibase.control_plane.models import OperationRecord, ResourceRecord
from omnibase.control_plane.service import append_resource_lineage, register_resource
from omnibase.workspace_data.models import (
    WorkspaceArtifact,
    WorkspaceDataEffect,
    WorkspaceDerivedIndex,
)
from omnibase.workspace_data.service import (
    WorkspaceDataConflict,
    WorkspaceDataNotFound,
    canonical_digest,
    create_effect,
    lock_operation,
    lock_resource,
    require_digest,
    require_workspace_resource,
    transition_effect,
)
from omnibase.workspaces.models import ResourceScopeBinding

_ARTIFACT_ACTION = "artifact.write"
_LINEAGE_RELATIONS = frozenset({"derived_from", "transformed_from"})


def _artifact_effect_binding(artifact: WorkspaceArtifact) -> dict[str, object]:
    return {
        "content_digest": artifact.content_digest,
        "media_type": artifact.media_type,
        "resource_id": artifact.id,
        "size_bytes": artifact.size_bytes,
        "source_generation": artifact.source_generation,
        "workspace_id": artifact.workspace_id,
    }


def _lock_artifact_by_operation(
    session: Session,
    *,
    tenant_id: str,
    operation_id: str,
) -> WorkspaceArtifact | None:
    return session.execute(
        select(WorkspaceArtifact)
        .where(
            WorkspaceArtifact.tenant_id == tenant_id,
            WorkspaceArtifact.operation_id == operation_id,
        )
        .with_for_update()
    ).scalar_one_or_none()


def _lock_artifact(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    artifact_id: str,
    operation_id: str,
) -> WorkspaceArtifact:
    artifact = session.execute(
        select(WorkspaceArtifact)
        .where(
            WorkspaceArtifact.id == artifact_id,
            WorkspaceArtifact.tenant_id == tenant_id,
            WorkspaceArtifact.workspace_id == workspace_id,
            WorkspaceArtifact.operation_id == operation_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if artifact is None:
        raise WorkspaceDataNotFound("workspace artifact not found")
    return artifact


def _lock_effect(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    operation_id: str,
    resource_id: str,
) -> WorkspaceDataEffect:
    effect = session.execute(
        select(WorkspaceDataEffect)
        .where(
            WorkspaceDataEffect.tenant_id == tenant_id,
            WorkspaceDataEffect.workspace_id == workspace_id,
            WorkspaceDataEffect.operation_id == operation_id,
            WorkspaceDataEffect.resource_id == resource_id,
            WorkspaceDataEffect.effect_kind == "artifact_put",
        )
        .with_for_update()
    ).scalar_one_or_none()
    if effect is None:
        raise WorkspaceDataConflict("workspace artifact effect is missing")
    return effect


def _source_digest(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    source: ResourceRecord,
) -> str:
    if source.kind == "artifact" and source.policy_class == "workspace_private":
        artifact = session.execute(
            select(WorkspaceArtifact).where(
                WorkspaceArtifact.id == source.id,
                WorkspaceArtifact.tenant_id == tenant_id,
                WorkspaceArtifact.workspace_id == workspace_id,
                WorkspaceArtifact.state == "available",
            )
        ).scalar_one_or_none()
        if artifact is not None:
            return artifact.content_digest
    if source.kind == "derived_index" and source.policy_class == "workspace_derived":
        derived = session.execute(
            select(WorkspaceDerivedIndex).where(
                WorkspaceDerivedIndex.id == source.id,
                WorkspaceDerivedIndex.tenant_id == tenant_id,
                WorkspaceDerivedIndex.workspace_id == workspace_id,
                WorkspaceDerivedIndex.state == "ready",
            )
        ).scalar_one_or_none()
        if derived is not None and derived.manifest_digest is not None:
            return derived.manifest_digest
    raise WorkspaceDataNotFound("workspace source is not readable")


def _validate_stage_replay(
    artifact: WorkspaceArtifact,
    *,
    workspace_id: str,
    content_digest: str,
    size_bytes: int,
    media_type: str,
    source_generation: int,
    source_run_id: str | None,
) -> None:
    if artifact.state == "unknown":
        raise WorkspaceDataConflict("unknown workspace artifact requires reconciliation")
    if (
        artifact.workspace_id,
        artifact.content_digest,
        artifact.size_bytes,
        artifact.media_type,
        artifact.source_generation,
        artifact.source_run_id,
    ) != (
        workspace_id,
        content_digest,
        size_bytes,
        media_type,
        source_generation,
        source_run_id,
    ):
        raise WorkspaceDataConflict("workspace artifact operation binding drift")


def _validate_stage_inputs(
    *,
    display_name: str,
    content_digest: str,
    size_bytes: int,
    media_type: str,
    source_generation: int,
    source_resource_id: str | None,
    source_version: int | None,
    source_content_digest: str | None,
    lineage_relation: str,
) -> None:
    require_digest(content_digest, "content_digest")
    if size_bytes < 0:
        raise ValueError("size_bytes must be non-negative")
    if not media_type or len(media_type) > 100:
        raise ValueError("media_type must contain 1-100 characters")
    if source_generation < 1:
        raise ValueError("source_generation must be at least one")
    if not display_name or len(display_name) > 200:
        raise ValueError("display_name must contain 1-200 characters")
    source_fields = (source_resource_id, source_version, source_content_digest)
    if any(value is not None for value in source_fields) and not all(
        value is not None for value in source_fields
    ):
        raise ValueError("source resource, version, and digest must be supplied together")
    if source_generation > 1 and source_resource_id is None:
        raise ValueError("a new artifact generation requires a source resource")
    if lineage_relation not in _LINEAGE_RELATIONS:
        raise ValueError("unsupported artifact lineage relation")


def _validate_artifact_operation(operation: OperationRecord, *, workspace_id: str) -> None:
    if (
        operation.workspace_id != workspace_id
        or operation.kind != _ARTIFACT_ACTION
        or operation.state not in {"queued", "running"}
        or operation.resource_id not in {None, workspace_id}
    ):
        raise WorkspaceDataConflict("workspace artifact operation binding is invalid")


def stage_artifact(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    operation_id: str,
    actor_id: str,
    display_name: str,
    content_digest: str,
    size_bytes: int,
    media_type: str,
    source_generation: int = 1,
    source_run_id: str | None = None,
    retention_until: datetime | None = None,
    source_resource_id: str | None = None,
    source_version: int | None = None,
    source_content_digest: str | None = None,
    lineage_relation: str = "transformed_from",
) -> WorkspaceArtifact:
    """Create one new immutable Artifact identity and its pending put effect.

    An exact call while the same operation is still staged is idempotent.  A
    changed binding, a terminal ``unknown`` effect, or a request to overwrite
    an existing identity fails closed.
    """

    _validate_stage_inputs(
        display_name=display_name,
        content_digest=content_digest,
        size_bytes=size_bytes,
        media_type=media_type,
        source_generation=source_generation,
        source_resource_id=source_resource_id,
        source_version=source_version,
        source_content_digest=source_content_digest,
        lineage_relation=lineage_relation,
    )

    operation = lock_operation(session, tenant_id=tenant_id, operation_id=operation_id)
    _validate_artifact_operation(operation, workspace_id=workspace_id)

    existing = _lock_artifact_by_operation(
        session,
        tenant_id=tenant_id,
        operation_id=operation_id,
    )
    if existing is not None:
        _validate_stage_replay(
            existing,
            workspace_id=workspace_id,
            content_digest=content_digest,
            size_bytes=size_bytes,
            media_type=media_type,
            source_generation=source_generation,
            source_run_id=source_run_id,
        )
        effect = _lock_effect(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            operation_id=operation_id,
            resource_id=existing.id,
        )
        if effect.state == "unknown":
            raise WorkspaceDataConflict("unknown workspace artifact requires reconciliation")
        if effect.binding_digest != canonical_digest(_artifact_effect_binding(existing)):
            raise WorkspaceDataConflict("workspace artifact effect binding drift")
        return existing

    source: ResourceRecord | None = None
    if source_resource_id is not None:
        assert source_version is not None
        assert source_content_digest is not None
        require_digest(source_content_digest, "source_content_digest")
        source = require_workspace_resource(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            resource_id=source_resource_id,
            expected_version=source_version,
        )
        if (
            _source_digest(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                source=source,
            )
            != source_content_digest
        ):
            raise WorkspaceDataConflict("workspace artifact source digest changed")

    resource = register_resource(
        session,
        tenant_id=tenant_id,
        kind="artifact",
        owner_type="workspace",
        owner_id=workspace_id,
        display_name=display_name,
        policy_class="workspace_private",
        state="provisioning",
        metadata={"source_generation": source_generation},
        created_by_actor_id=actor_id,
    )
    session.add(
        ResourceScopeBinding(
            resource_id=resource.id,
            tenant_id=tenant_id,
            scope_class="workspace_private",
            workspace_id=workspace_id,
            version=1,
        )
    )
    artifact = WorkspaceArtifact(
        id=resource.id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        source_run_id=source_run_id,
        source_generation=source_generation,
        operation_id=operation_id,
        content_digest=content_digest,
        size_bytes=size_bytes,
        media_type=media_type,
        state="staging",
        version=1,
        retention_until=retention_until,
        created_by_actor_id=actor_id,
    )
    session.add(artifact)
    session.flush()
    if source is not None:
        append_resource_lineage(
            session,
            tenant_id=tenant_id,
            source_resource_id=source.id,
            derived_resource_id=resource.id,
            relation=lineage_relation,
            source_version=source.version,
            transform_digest=f"sha256:{content_digest}",
            created_by_operation_id=operation_id,
        )
    create_effect(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        operation_id=operation_id,
        effect_kind="artifact_put",
        binding=_artifact_effect_binding(artifact),
        resource_id=artifact.id,
    )
    return artifact


def finalize_artifact(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    artifact_id: str,
    operation_id: str,
    target_state: str,
    receipt_digest: str | None = None,
    reason_code: str | None = None,
) -> WorkspaceArtifact:
    """Finalize a staged provider effect without replaying ambiguous outcomes."""

    if target_state not in {"available", "failed", "unknown"}:
        raise ValueError("unsupported artifact final state")
    artifact = _lock_artifact(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        artifact_id=artifact_id,
        operation_id=operation_id,
    )
    resource = lock_resource(session, tenant_id=tenant_id, resource_id=artifact_id)
    if (
        artifact.workspace_id != workspace_id
        or resource.kind != "artifact"
        or resource.owner_type != "workspace"
        or resource.owner_id != workspace_id
        or resource.policy_class != "workspace_private"
    ):
        raise WorkspaceDataNotFound("workspace artifact not found")
    effect = _lock_effect(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        operation_id=operation_id,
        resource_id=artifact_id,
    )
    if effect.binding_digest != canonical_digest(_artifact_effect_binding(artifact)):
        raise WorkspaceDataConflict("workspace artifact effect binding drift")

    effect_state = "committed" if target_state == "available" else target_state
    if artifact.state != "staging":
        if artifact.state == target_state and effect.state == effect_state:
            transition_effect(
                effect,
                target_state=effect_state,
                receipt_digest=receipt_digest,
                reason_code=reason_code,
            )
            return artifact
        raise WorkspaceDataConflict("terminal workspace artifact cannot transition")
    if target_state != "available" and reason_code is None:
        raise ValueError("failed or unknown artifact requires reason_code")
    transition_effect(
        effect,
        target_state=effect_state,
        receipt_digest=receipt_digest,
        reason_code=reason_code,
    )
    artifact.state = target_state
    artifact.version += 1
    if target_state == "available":
        resource.state = "active"
        resource.version += 1
    elif target_state == "failed":
        resource.state = "failed"
        resource.version += 1
    session.flush()
    return artifact


__all__ = ["finalize_artifact", "stage_artifact"]
