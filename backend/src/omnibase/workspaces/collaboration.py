"""P34.4D synthetic collaboration contracts and authority-fenced harness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from omnibase.workspaces.models import (
    CollaborationArtifact,
    CollaborationEvent,
    Workspace,
    WorkspaceAuthority,
    WorkspaceNode,
)
from omnibase.workspaces.service import (
    LeaseRejected,
    WorkspaceConflict,
    authorize_workspace_action,
    get_active_attested_node,
)

_ZERO_DIGEST = "0" * 64
_EVENT_TYPES = frozenset({"git_ref", "artifact_published", "draft_promoted"})


@dataclass(frozen=True)
class SyncEnvelope:
    """Metadata-only envelope: no file bytes, paths, credentials, SQL, or RAG text."""

    tenant_id: str
    workspace_id: str
    node_id: str
    authority_epoch: int
    sequence: int
    event_type: str
    event_digest: str
    previous_digest: str
    artifact_digest: str | None = None
    artifact_size_bytes: int | None = None
    artifact_media_type: str | None = None


class CollaborationTransport(Protocol):
    def publish(self, envelope: SyncEnvelope) -> None: ...

    def read(self, *, workspace_id: str, after_sequence: int) -> list[SyncEnvelope]: ...


class FakeLocalCollaborationTransport:
    """In-memory append-only transport used only by tests and the local harness."""

    def __init__(self) -> None:
        self._events: dict[str, dict[int, SyncEnvelope]] = {}

    def publish(self, envelope: SyncEnvelope) -> None:
        workspace = self._events.setdefault(envelope.workspace_id, {})
        existing = workspace.get(envelope.sequence)
        if existing is not None and existing.event_digest != envelope.event_digest:
            raise WorkspaceConflict("same sequence carries a different digest")
        workspace[envelope.sequence] = envelope

    def read(self, *, workspace_id: str, after_sequence: int) -> list[SyncEnvelope]:
        workspace = self._events.get(workspace_id, {})
        return [workspace[key] for key in sorted(workspace) if key > after_sequence]


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _db_now(session: Session) -> datetime:
    return _aware(session.execute(select(func.now())).scalar_one())


def _validate_digest(value: str, field: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise WorkspaceConflict(f"{field} must be a lowercase SHA-256 digest")


def _active_node(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    node_id: str,
    lock: bool = False,
) -> WorkspaceNode:
    return get_active_attested_node(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        node_id=node_id,
        lock=lock,
    )


def claim_workspace_authority(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    node_id: str,
    actor_user_id: str,
    lease_seconds: int = 30,
    explicit_takeover: bool = False,
) -> WorkspaceAuthority:
    """Claim a single writer epoch; no automatic election or dual writer."""
    if lease_seconds < 5 or lease_seconds > 300:
        raise LeaseRejected("authority lease duration is outside the safe range")
    workspace = session.execute(
        select(Workspace)
        .where(Workspace.id == workspace_id, Workspace.tenant_id == tenant_id)
        .with_for_update()
    ).scalar_one_or_none()
    if workspace is None:
        raise LeaseRejected("workspace authority is unavailable")
    authorize_workspace_action(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=actor_user_id,
        action="workspace.nodes.manage",
        lock=True,
    )
    _active_node(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        node_id=node_id,
        lock=True,
    )
    now = _db_now(session)
    active = session.execute(
        select(WorkspaceAuthority)
        .where(
            WorkspaceAuthority.tenant_id == tenant_id,
            WorkspaceAuthority.workspace_id == workspace_id,
            WorkspaceAuthority.state == "active",
        )
        .with_for_update()
    ).scalar_one_or_none()
    if active is not None:
        if active.lease_expires_at > now:
            if active.authority_node_id == node_id:
                return active
            raise LeaseRejected("workspace authority is still online")
        active.state = "offline"
        if not explicit_takeover:
            raise LeaseRejected("expired authority requires an explicit takeover")
    maximum = session.execute(
        select(func.coalesce(func.max(WorkspaceAuthority.epoch), 0)).where(
            WorkspaceAuthority.tenant_id == tenant_id,
            WorkspaceAuthority.workspace_id == workspace_id,
        )
    ).scalar_one()
    authority = WorkspaceAuthority(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        authority_node_id=node_id,
        epoch=int(maximum) + 1,
        state="active",
        lease_expires_at=now + timedelta(seconds=lease_seconds),
    )
    session.add(authority)
    session.flush()
    return authority


def heartbeat_workspace_authority(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    authority_id: str,
    node_id: str,
    epoch: int,
    lease_seconds: int = 30,
) -> WorkspaceAuthority:
    if lease_seconds < 5 or lease_seconds > 300:
        raise LeaseRejected("authority lease duration is outside the safe range")
    workspace = session.execute(
        select(Workspace)
        .where(Workspace.id == workspace_id, Workspace.tenant_id == tenant_id)
        .with_for_update()
    ).scalar_one_or_none()
    if workspace is None:
        raise LeaseRejected("authority lease is expired, revoked, or stale")
    _active_node(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        node_id=node_id,
        lock=True,
    )
    authority = session.execute(
        select(WorkspaceAuthority)
        .where(
            WorkspaceAuthority.id == authority_id,
            WorkspaceAuthority.tenant_id == tenant_id,
            WorkspaceAuthority.workspace_id == workspace_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    now = _db_now(session)
    if (
        authority is None
        or authority.state != "active"
        or authority.authority_node_id != node_id
        or authority.epoch != epoch
        or authority.lease_expires_at <= now
    ):
        raise LeaseRejected("authority lease is expired, revoked, or stale")
    authority.lease_expires_at = now + timedelta(seconds=lease_seconds)
    session.flush()
    return authority


def mark_workspace_authority_offline(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    authority_id: str,
    node_id: str,
    epoch: int,
) -> WorkspaceAuthority:
    authority = session.execute(
        select(WorkspaceAuthority)
        .where(
            WorkspaceAuthority.id == authority_id,
            WorkspaceAuthority.tenant_id == tenant_id,
            WorkspaceAuthority.workspace_id == workspace_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if authority is None or authority.authority_node_id != node_id or authority.epoch != epoch:
        raise LeaseRejected("authority identity is stale")
    if authority.state == "active":
        authority.state = "offline"
        session.flush()
    return authority


def validate_sync_envelope(envelope: SyncEnvelope) -> None:
    if envelope.authority_epoch < 1 or envelope.sequence < 1:
        raise WorkspaceConflict("authority epoch and sequence must be positive")
    if envelope.event_type not in _EVENT_TYPES:
        raise WorkspaceConflict("unsupported collaboration event type")
    _validate_digest(envelope.event_digest, "event_digest")
    _validate_digest(envelope.previous_digest, "previous_digest")
    if envelope.event_type == "artifact_published":
        if (
            envelope.artifact_digest is None
            or envelope.artifact_size_bytes is None
            or envelope.artifact_media_type is None
            or envelope.artifact_size_bytes < 0
        ):
            raise WorkspaceConflict("artifact metadata is incomplete")
        _validate_digest(envelope.artifact_digest, "artifact_digest")
    elif any(
        value is not None
        for value in (
            envelope.artifact_digest,
            envelope.artifact_size_bytes,
            envelope.artifact_media_type,
        )
    ):
        raise WorkspaceConflict("non-artifact events cannot carry artifact metadata")


def commit_sync_envelope(
    session: Session,
    *,
    authority_id: str,
    envelope: SyncEnvelope,
) -> CollaborationEvent:
    """Append one canonical event only for the current unexpired authority epoch."""
    validate_sync_envelope(envelope)
    workspace = session.execute(
        select(Workspace)
        .where(
            Workspace.id == envelope.workspace_id,
            Workspace.tenant_id == envelope.tenant_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if workspace is None:
        raise LeaseRejected("authority is offline, expired, revoked, or stale")
    _active_node(
        session,
        tenant_id=envelope.tenant_id,
        workspace_id=envelope.workspace_id,
        node_id=envelope.node_id,
        lock=True,
    )
    now = _db_now(session)
    authority = session.execute(
        select(WorkspaceAuthority)
        .where(
            WorkspaceAuthority.id == authority_id,
            WorkspaceAuthority.tenant_id == envelope.tenant_id,
            WorkspaceAuthority.workspace_id == envelope.workspace_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if (
        authority is None
        or authority.state != "active"
        or authority.authority_node_id != envelope.node_id
        or authority.epoch != envelope.authority_epoch
        or authority.lease_expires_at <= now
    ):
        raise LeaseRejected("authority is offline, expired, revoked, or stale")
    existing = session.execute(
        select(CollaborationEvent).where(
            CollaborationEvent.tenant_id == envelope.tenant_id,
            CollaborationEvent.workspace_id == envelope.workspace_id,
            CollaborationEvent.sequence == envelope.sequence,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.event_digest == envelope.event_digest:
            return existing
        raise WorkspaceConflict("same sequence carries a conflicting digest")
    latest = session.execute(
        select(CollaborationEvent)
        .where(
            CollaborationEvent.tenant_id == envelope.tenant_id,
            CollaborationEvent.workspace_id == envelope.workspace_id,
        )
        .order_by(CollaborationEvent.sequence.desc())
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()
    expected_sequence = 1 if latest is None else latest.sequence + 1
    expected_previous = _ZERO_DIGEST if latest is None else latest.event_digest
    if envelope.sequence != expected_sequence or envelope.previous_digest != expected_previous:
        raise WorkspaceConflict("collaboration sequence or previous digest drifted")
    artifact: CollaborationArtifact | None = None
    if envelope.event_type == "artifact_published":
        artifact = session.execute(
            select(CollaborationArtifact).where(
                CollaborationArtifact.tenant_id == envelope.tenant_id,
                CollaborationArtifact.workspace_id == envelope.workspace_id,
                CollaborationArtifact.content_digest == envelope.artifact_digest,
            )
        ).scalar_one_or_none()
        if artifact is None:
            artifact = CollaborationArtifact(
                tenant_id=envelope.tenant_id,
                workspace_id=envelope.workspace_id,
                authority_epoch=envelope.authority_epoch,
                content_digest=envelope.artifact_digest,
                size_bytes=envelope.artifact_size_bytes,
                media_type=envelope.artifact_media_type,
                artifact_metadata={"synthetic_harness": True},
                state="available",
                created_by_node_id=envelope.node_id,
            )
            session.add(artifact)
            session.flush()
    event = CollaborationEvent(
        tenant_id=envelope.tenant_id,
        workspace_id=envelope.workspace_id,
        authority_node_id=envelope.node_id,
        authority_epoch=envelope.authority_epoch,
        sequence=envelope.sequence,
        event_type=envelope.event_type,
        event_digest=envelope.event_digest,
        artifact_id=artifact.id if artifact is not None else None,
        parent_event_id=latest.id if latest is not None else None,
        event_metadata={"synthetic_harness": True},
    )
    session.add(event)
    session.flush()
    return event


__all__ = [
    "CollaborationTransport",
    "FakeLocalCollaborationTransport",
    "SyncEnvelope",
    "claim_workspace_authority",
    "commit_sync_envelope",
    "heartbeat_workspace_authority",
    "mark_workspace_authority_offline",
    "validate_sync_envelope",
]
