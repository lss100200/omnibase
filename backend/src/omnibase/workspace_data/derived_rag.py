"""Isolated derived-RAG metadata lifecycle for P34.6.

Only the dedicated Workspace-derived registry and tenant chunk lane are
addressed here.  Canonical document, embedding and index tables are neither
imported nor mutated.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from omnibase.control_plane.models import ResourceRecord
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

_DERIVED_CREATE_ACTION = "rag.derived.create"


def _normalize_generation(generation: str) -> str:
    try:
        normalized = str(UUID(generation))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("generation must be a UUID") from exc
    if generation != normalized:
        raise ValueError("generation must use canonical UUID text")
    return normalized


def _derived_effect_binding(index: WorkspaceDerivedIndex) -> dict[str, object]:
    return {
        "generation": index.generation,
        "index_profile_digest": index.index_profile_digest,
        "resource_id": index.id,
        "source_resource_id": index.source_resource_id,
        "source_version": index.source_version,
        "workspace_id": index.workspace_id,
    }


def _lock_index_by_operation(
    session: Session,
    *,
    tenant_id: str,
    operation_id: str,
) -> WorkspaceDerivedIndex | None:
    return session.execute(
        select(WorkspaceDerivedIndex)
        .where(
            WorkspaceDerivedIndex.tenant_id == tenant_id,
            WorkspaceDerivedIndex.operation_id == operation_id,
        )
        .with_for_update()
    ).scalar_one_or_none()


def _lock_index(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    derived_index_id: str,
    operation_id: str | None = None,
) -> WorkspaceDerivedIndex:
    filters = [
        WorkspaceDerivedIndex.id == derived_index_id,
        WorkspaceDerivedIndex.tenant_id == tenant_id,
        WorkspaceDerivedIndex.workspace_id == workspace_id,
    ]
    if operation_id is not None:
        filters.append(WorkspaceDerivedIndex.operation_id == operation_id)
    index = session.execute(
        select(WorkspaceDerivedIndex).where(*filters).with_for_update()
    ).scalar_one_or_none()
    if index is None:
        raise WorkspaceDataNotFound("workspace derived index not found")
    return index


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
            WorkspaceDataEffect.effect_kind == "derived_build",
        )
        .with_for_update()
    ).scalar_one_or_none()
    if effect is None:
        raise WorkspaceDataConflict("workspace derived effect is missing")
    return effect


def _readable_source_digest(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    source: ResourceRecord,
) -> str:
    if source.policy_class == "canonical_readonly":
        raise WorkspaceDataNotFound("canonical resources cannot enter the derived write lane")
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
    raise WorkspaceDataNotFound("workspace derived source is not readable")


def _validate_finish_inputs(
    *,
    target_state: str,
    manifest_digest: str | None,
    chunk_count: int | None,
    reason_code: str | None,
) -> None:
    if target_state not in {"ready", "failed", "unknown"}:
        raise ValueError("unsupported derived final state")
    if target_state == "ready":
        if manifest_digest is None or chunk_count is None:
            raise ValueError("ready derived index requires manifest_digest and chunk_count")
        require_digest(manifest_digest, "manifest_digest")
        if chunk_count < 0:
            raise ValueError("chunk_count must be non-negative")
    elif manifest_digest is not None or chunk_count is not None:
        raise ValueError("non-ready derived index cannot publish a manifest")
    if target_state != "ready" and reason_code is None:
        raise ValueError("failed or unknown derived index requires reason_code")


def _return_exact_finished_replay(
    index: WorkspaceDerivedIndex,
    effect: WorkspaceDataEffect,
    *,
    target_state: str,
    effect_state: str,
    manifest_digest: str | None,
    chunk_count: int | None,
    receipt_digest: str | None,
    reason_code: str | None,
) -> WorkspaceDerivedIndex:
    if index.state != target_state or effect.state != effect_state:
        raise WorkspaceDataConflict("terminal workspace derived index cannot transition")
    if target_state == "ready" and (
        index.manifest_digest != manifest_digest or index.chunk_count != chunk_count
    ):
        raise WorkspaceDataConflict("workspace derived result drift")
    transition_effect(
        effect,
        target_state=effect_state,
        receipt_digest=receipt_digest,
        reason_code=reason_code,
    )
    return index


def start_derived_index(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    operation_id: str,
    actor_id: str,
    display_name: str,
    source_resource_id: str,
    source_version: int,
    source_content_digest: str,
    index_profile_digest: str,
    generation: str,
) -> WorkspaceDerivedIndex:
    """Start one new derived generation; no canonical table is writable here."""

    require_digest(source_content_digest, "source_content_digest")
    require_digest(index_profile_digest, "index_profile_digest")
    generation = _normalize_generation(generation)
    if not display_name or len(display_name) > 200:
        raise ValueError("display_name must contain 1-200 characters")
    operation = lock_operation(session, tenant_id=tenant_id, operation_id=operation_id)
    if (
        operation.workspace_id != workspace_id
        or operation.kind != _DERIVED_CREATE_ACTION
        or operation.state not in {"queued", "running"}
        or operation.resource_id not in {None, workspace_id, source_resource_id}
    ):
        raise WorkspaceDataConflict("workspace derived operation binding is invalid")

    existing = _lock_index_by_operation(
        session,
        tenant_id=tenant_id,
        operation_id=operation_id,
    )
    if existing is not None:
        if existing.state == "unknown":
            raise WorkspaceDataConflict("unknown workspace derived effect requires reconciliation")
        if (
            existing.workspace_id,
            existing.source_resource_id,
            existing.source_version,
            existing.index_profile_digest,
            existing.generation,
        ) != (
            workspace_id,
            source_resource_id,
            source_version,
            index_profile_digest,
            generation,
        ):
            raise WorkspaceDataConflict("workspace derived operation binding drift")
        effect = _lock_effect(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            operation_id=operation_id,
            resource_id=existing.id,
        )
        if effect.state == "unknown":
            raise WorkspaceDataConflict("unknown workspace derived effect requires reconciliation")
        if effect.binding_digest != canonical_digest(_derived_effect_binding(existing)):
            raise WorkspaceDataConflict("workspace derived effect binding drift")
        return existing

    source = require_workspace_resource(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        resource_id=source_resource_id,
        expected_version=source_version,
    )
    if source.policy_class not in {"workspace_private", "workspace_derived"}:
        raise WorkspaceDataNotFound("workspace derived source is not readable")
    if (
        _readable_source_digest(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            source=source,
        )
        != source_content_digest
    ):
        raise WorkspaceDataConflict("workspace derived source digest changed")

    resource = register_resource(
        session,
        tenant_id=tenant_id,
        kind="derived_index",
        owner_type="workspace",
        owner_id=workspace_id,
        display_name=display_name,
        policy_class="workspace_derived",
        state="provisioning",
        metadata={
            "generation": generation,
            "index_profile_digest": index_profile_digest,
            "source_resource_id": source_resource_id,
            "source_version": source_version,
        },
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
    index = WorkspaceDerivedIndex(
        id=resource.id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        source_resource_id=source_resource_id,
        source_version=source_version,
        operation_id=operation_id,
        generation=generation,
        index_profile_digest=index_profile_digest,
        manifest_digest=None,
        chunk_count=0,
        state="building",
        version=1,
        created_by_actor_id=actor_id,
    )
    session.add(index)
    session.flush()
    append_resource_lineage(
        session,
        tenant_id=tenant_id,
        source_resource_id=source_resource_id,
        derived_resource_id=resource.id,
        relation="derived_from",
        source_version=source_version,
        transform_digest=f"sha256:{index_profile_digest}",
        created_by_operation_id=operation_id,
    )
    create_effect(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        operation_id=operation_id,
        effect_kind="derived_build",
        binding=_derived_effect_binding(index),
        resource_id=index.id,
    )
    return index


def finish_derived_index(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    derived_index_id: str,
    operation_id: str,
    target_state: str,
    manifest_digest: str | None = None,
    chunk_count: int | None = None,
    receipt_digest: str | None = None,
    reason_code: str | None = None,
) -> WorkspaceDerivedIndex:
    """Finish a build once; ``unknown`` is terminal and never auto-replayed."""

    _validate_finish_inputs(
        target_state=target_state,
        manifest_digest=manifest_digest,
        chunk_count=chunk_count,
        reason_code=reason_code,
    )

    index = _lock_index(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        derived_index_id=derived_index_id,
        operation_id=operation_id,
    )
    resource = lock_resource(session, tenant_id=tenant_id, resource_id=derived_index_id)
    if (
        index.workspace_id != workspace_id
        or resource.kind != "derived_index"
        or resource.owner_type != "workspace"
        or resource.owner_id != workspace_id
        or resource.policy_class != "workspace_derived"
    ):
        raise WorkspaceDataNotFound("workspace derived index not found")
    effect = _lock_effect(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        operation_id=operation_id,
        resource_id=derived_index_id,
    )
    if effect.binding_digest != canonical_digest(_derived_effect_binding(index)):
        raise WorkspaceDataConflict("workspace derived effect binding drift")
    effect_state = "committed" if target_state == "ready" else target_state
    if index.state != "building":
        return _return_exact_finished_replay(
            index,
            effect,
            target_state=target_state,
            effect_state=effect_state,
            manifest_digest=manifest_digest,
            chunk_count=chunk_count,
            receipt_digest=receipt_digest,
            reason_code=reason_code,
        )
    transition_effect(
        effect,
        target_state=effect_state,
        receipt_digest=receipt_digest,
        reason_code=reason_code,
    )
    index.state = target_state
    index.manifest_digest = manifest_digest
    index.chunk_count = 0 if chunk_count is None else chunk_count
    index.version += 1
    if target_state == "ready":
        resource.state = "active"
        resource.version += 1
    elif target_state == "failed":
        resource.state = "failed"
        resource.version += 1
    session.flush()
    return index


def get_ready_derived_index(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    derived_index_id: str,
) -> WorkspaceDerivedIndex:
    """Return a ready index only; all other states use IDOR-safe not-found."""

    index = session.execute(
        select(WorkspaceDerivedIndex).where(
            WorkspaceDerivedIndex.id == derived_index_id,
            WorkspaceDerivedIndex.tenant_id == tenant_id,
            WorkspaceDerivedIndex.workspace_id == workspace_id,
            WorkspaceDerivedIndex.state == "ready",
        )
    ).scalar_one_or_none()
    if index is None:
        raise WorkspaceDataNotFound("workspace derived index not found")
    resource = lock_resource(session, tenant_id=tenant_id, resource_id=derived_index_id)
    if (
        resource.kind != "derived_index"
        or resource.owner_type != "workspace"
        or resource.owner_id != workspace_id
        or resource.policy_class != "workspace_derived"
        or resource.state != "active"
    ):
        raise WorkspaceDataNotFound("workspace derived index not found")
    return index


def revoke_derived_index(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    derived_index_id: str,
) -> WorkspaceDerivedIndex:
    """Revoke ready visibility without deleting evidence or canonical data."""

    index = _lock_index(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        derived_index_id=derived_index_id,
    )
    resource = lock_resource(session, tenant_id=tenant_id, resource_id=derived_index_id)
    if (
        resource.kind != "derived_index"
        or resource.owner_type != "workspace"
        or resource.owner_id != workspace_id
        or resource.policy_class != "workspace_derived"
    ):
        raise WorkspaceDataNotFound("workspace derived index not found")
    if index.state == "revoked":
        return index
    if index.state != "ready":
        raise WorkspaceDataConflict("only a ready derived index can be revoked")
    index.state = "revoked"
    index.version += 1
    resource.state = "archived"
    resource.version += 1
    session.flush()
    return index


__all__ = [
    "finish_derived_index",
    "get_ready_derived_index",
    "revoke_derived_index",
    "start_derived_index",
]
