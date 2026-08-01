"""P34.4 member-node and overlay control-plane contracts.

No implementation in this module opens a real network connection.  The fake
provider is a deterministic harness; production defaults must reject until a
P34.5 adapter is explicitly installed and separately threat-tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from omnibase.workspaces.models import (
    NetworkLease,
    NetworkLeaseCursor,
    NodeAttestation,
    PeerGrant,
    RunLease,
    ServiceAdvertisement,
    Workspace,
    WorkspaceAuthority,
    WorkspaceNode,
    WorkspaceRun,
)
from omnibase.workspaces.service import (
    LeaseRejected,
    WorkspaceConflict,
    WorkspaceNotFound,
    authorize_workspace_action,
    get_active_attested_node,
)

_PEER_ACTIONS = frozenset(
    {"peer.connect", "service.publish", "service.consume", "sync.read", "sync.write"}
)
_SERVICE_PROTOCOLS = frozenset({"https", "git", "artifact", "event"})


@dataclass(frozen=True)
class TrustedNodeAttestation:
    """Trusted typed output from an attestor, never constructed from Browser JSON."""

    tenant_id: str
    workspace_id: str
    owner_user_id: str
    identity_digest: str
    nonce_digest: str
    evidence_digest: str
    verifier: str
    verified_at: datetime
    expires_at: datetime


class PeerOverlayProvider(Protocol):
    def activate(
        self,
        *,
        workspace_id: str,
        source_node_id: str,
        target_node_id: str,
        service_id: str,
        lease_id: str,
    ) -> None: ...

    def revoke(self, *, lease_id: str) -> None: ...


class OverlayUnavailable(RuntimeError):
    """Real overlay adapters remain frozen until P34.5."""


class UnavailablePeerOverlayProvider:
    def activate(
        self,
        *,
        workspace_id: str,
        source_node_id: str,
        target_node_id: str,
        service_id: str,
        lease_id: str,
    ) -> None:
        del workspace_id, source_node_id, target_node_id, service_id, lease_id
        raise OverlayUnavailable("peer_overlay_provider_unavailable")

    def revoke(self, *, lease_id: str) -> None:
        del lease_id
        raise OverlayUnavailable("peer_overlay_provider_unavailable")


class FakeLocalPeerOverlayProvider:
    """In-memory logical connectivity ledger; never touches sockets or routes."""

    def __init__(self) -> None:
        self.active_lease_ids: set[str] = set()

    def activate(
        self,
        *,
        workspace_id: str,
        source_node_id: str,
        target_node_id: str,
        service_id: str,
        lease_id: str,
    ) -> None:
        del workspace_id, source_node_id, target_node_id, service_id
        self.active_lease_ids.add(lease_id)

    def revoke(self, *, lease_id: str) -> None:
        self.active_lease_ids.discard(lease_id)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _db_now(session: Session) -> datetime:
    return _aware(session.execute(select(func.now())).scalar_one())


def _validate_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise WorkspaceConflict(f"{field} must be a lowercase SHA-256 digest")


def register_attested_node(
    session: Session,
    *,
    attestation: TrustedNodeAttestation,
    actor_user_id: str,
    display_name: str,
) -> WorkspaceNode:
    if attestation.owner_user_id != actor_user_id:
        raise WorkspaceNotFound("attested node identity does not match the actor")
    _validate_sha256(attestation.identity_digest, "identity_digest")
    _validate_sha256(attestation.nonce_digest, "nonce_digest")
    _validate_sha256(attestation.evidence_digest, "evidence_digest")
    workspace = session.execute(
        select(Workspace)
        .where(
            Workspace.id == attestation.workspace_id,
            Workspace.tenant_id == attestation.tenant_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if workspace is None:
        raise WorkspaceNotFound("workspace not found")
    authorize_workspace_action(
        session,
        tenant_id=attestation.tenant_id,
        workspace_id=attestation.workspace_id,
        user_id=actor_user_id,
        action="workspace.nodes.manage",
        lock=True,
    )
    now = _db_now(session)
    if _aware(attestation.verified_at) > now or _aware(attestation.expires_at) <= now:
        raise WorkspaceConflict("node attestation window is invalid")
    node = WorkspaceNode(
        tenant_id=attestation.tenant_id,
        workspace_id=attestation.workspace_id,
        owner_user_id=actor_user_id,
        display_name=display_name,
        identity_digest=attestation.identity_digest,
        state="active",
        attestation_state="verified",
        last_seen_at=now,
    )
    session.add(node)
    session.flush()
    session.add(
        NodeAttestation(
            tenant_id=attestation.tenant_id,
            node_id=node.id,
            nonce_digest=attestation.nonce_digest,
            evidence_digest=attestation.evidence_digest,
            verifier=attestation.verifier,
            state="verified",
            verified_at=_aware(attestation.verified_at),
            expires_at=_aware(attestation.expires_at),
        )
    )
    session.flush()
    return node


def _get_active_node(
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


def heartbeat_node(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    node_id: str,
    expected_fencing_token: int,
) -> WorkspaceNode:
    node = _get_active_node(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        node_id=node_id,
        lock=True,
    )
    if node.fencing_token != expected_fencing_token:
        raise LeaseRejected("node fencing token is stale")
    node.last_seen_at = _db_now(session)
    node.version += 1
    session.flush()
    return node


def revoke_node(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    node_id: str,
    actor_user_id: str,
) -> WorkspaceNode:
    workspace = session.execute(
        select(Workspace)
        .where(
            Workspace.id == workspace_id,
            Workspace.tenant_id == tenant_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if workspace is None:
        raise WorkspaceNotFound("workspace not found")
    authorize_workspace_action(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=actor_user_id,
        action="workspace.nodes.manage",
        lock=True,
    )
    node = session.execute(
        select(WorkspaceNode)
        .where(
            WorkspaceNode.id == node_id,
            WorkspaceNode.tenant_id == tenant_id,
            WorkspaceNode.workspace_id == workspace_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if node is None:
        raise WorkspaceNotFound("workspace node not found")
    if node.state == "revoked":
        return node
    now = _db_now(session)
    node.state = "revoked"
    node.attestation_state = "rejected"
    node.revoked_at = now
    node.fencing_token += 1
    node.version += 1
    session.execute(
        update(NodeAttestation)
        .where(
            NodeAttestation.tenant_id == tenant_id,
            NodeAttestation.node_id == node_id,
            NodeAttestation.state == "verified",
        )
        .values(state="revoked")
    )
    session.execute(
        update(PeerGrant)
        .where(
            PeerGrant.tenant_id == tenant_id,
            PeerGrant.workspace_id == workspace_id,
            PeerGrant.state == "active",
            or_(PeerGrant.source_node_id == node_id, PeerGrant.target_node_id == node_id),
        )
        .values(state="revoked", revoked_at=now, fencing_token=PeerGrant.fencing_token + 1)
    )
    service_ids = select(ServiceAdvertisement.id).where(
        ServiceAdvertisement.tenant_id == tenant_id,
        ServiceAdvertisement.workspace_id == workspace_id,
        ServiceAdvertisement.node_id == node_id,
    )
    peer_ids = select(PeerGrant.id).where(
        PeerGrant.tenant_id == tenant_id,
        PeerGrant.workspace_id == workspace_id,
        or_(PeerGrant.source_node_id == node_id, PeerGrant.target_node_id == node_id),
    )
    affected_run_ids = list(
        session.scalars(
            select(RunLease.run_id).where(
                RunLease.tenant_id == tenant_id,
                RunLease.workspace_id == workspace_id,
                RunLease.node_id == node_id,
                RunLease.state == "active",
            )
        )
    )
    session.execute(
        update(RunLease)
        .where(
            RunLease.tenant_id == tenant_id,
            RunLease.workspace_id == workspace_id,
            RunLease.node_id == node_id,
            RunLease.state == "active",
        )
        .values(state="revoked", revoked_at=now)
    )
    if affected_run_ids:
        session.execute(
            update(WorkspaceRun)
            .where(
                WorkspaceRun.tenant_id == tenant_id,
                WorkspaceRun.workspace_id == workspace_id,
                WorkspaceRun.id.in_(affected_run_ids),
                WorkspaceRun.observed_state == "leased",
            )
            .values(
                observed_state="queued",
                next_fencing_token=WorkspaceRun.next_fencing_token + 1,
                version=WorkspaceRun.version + 1,
                last_error_code="node_revoked_before_start",
            )
        )
        session.execute(
            update(WorkspaceRun)
            .where(
                WorkspaceRun.tenant_id == tenant_id,
                WorkspaceRun.workspace_id == workspace_id,
                WorkspaceRun.id.in_(affected_run_ids),
                WorkspaceRun.observed_state.in_(
                    ("starting", "running", "pausing", "paused", "stopping")
                ),
            )
            .values(
                desired_state="stopped",
                observed_state="failed",
                next_fencing_token=WorkspaceRun.next_fencing_token + 1,
                version=WorkspaceRun.version + 1,
                last_error_code="node_revoked",
            )
        )
    session.execute(
        update(ServiceAdvertisement)
        .where(
            ServiceAdvertisement.tenant_id == tenant_id,
            ServiceAdvertisement.workspace_id == workspace_id,
            ServiceAdvertisement.node_id == node_id,
            ServiceAdvertisement.state == "active",
        )
        .values(state="revoked", revoked_at=now)
    )
    session.execute(
        update(NetworkLease)
        .where(
            NetworkLease.tenant_id == tenant_id,
            NetworkLease.workspace_id == workspace_id,
            NetworkLease.state == "active",
            or_(
                NetworkLease.requester_node_id == node_id,
                NetworkLease.service_id.in_(service_ids),
                NetworkLease.peer_grant_id.in_(peer_ids),
            ),
        )
        .values(state="revoked", revoked_at=now)
    )
    session.execute(
        update(WorkspaceAuthority)
        .where(
            WorkspaceAuthority.tenant_id == tenant_id,
            WorkspaceAuthority.workspace_id == workspace_id,
            WorkspaceAuthority.authority_node_id == node_id,
            WorkspaceAuthority.state == "active",
        )
        .values(state="revoked")
    )
    session.flush()
    return node


def create_peer_grant(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: str,
    source_node_id: str,
    target_node_id: str,
    actions: list[str],
    expires_at: datetime,
) -> PeerGrant:
    if source_node_id == target_node_id:
        raise WorkspaceConflict("self peer grants are forbidden")
    if not actions or not set(actions).issubset(_PEER_ACTIONS):
        raise WorkspaceConflict("peer grant contains unsupported actions")
    expires_at = _aware(expires_at)
    workspace = session.execute(
        select(Workspace)
        .where(Workspace.id == workspace_id, Workspace.tenant_id == tenant_id)
        .with_for_update()
    ).scalar_one_or_none()
    if workspace is None:
        raise WorkspaceNotFound("workspace not found")
    authorize_workspace_action(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=actor_user_id,
        action="workspace.nodes.manage",
        lock=True,
    )
    for current_node_id in sorted({source_node_id, target_node_id}):
        _get_active_node(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            node_id=current_node_id,
            lock=True,
        )
    if expires_at <= _db_now(session):
        raise WorkspaceConflict("peer grant expiry must be in the future")
    grant = PeerGrant(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        source_node_id=source_node_id,
        target_node_id=target_node_id,
        actions=sorted(set(actions)),
        state="active",
        expires_at=expires_at,
        created_by_user_id=actor_user_id,
    )
    session.add(grant)
    session.flush()
    return grant


def publish_service(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    node_id: str,
    service_key: str,
    protocol: str,
    logical_port: int,
    actions: list[str],
    generation: int,
    ttl_seconds: int,
) -> ServiceAdvertisement:
    if ttl_seconds < 5 or ttl_seconds > 300:
        raise LeaseRejected("service advertisement duration is outside the safe range")
    workspace = session.execute(
        select(Workspace)
        .where(
            Workspace.id == workspace_id,
            Workspace.tenant_id == tenant_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if workspace is None or workspace.generation != generation:
        raise LeaseRejected("service advertisement generation is stale")
    _get_active_node(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        node_id=node_id,
        lock=True,
    )
    if protocol not in _SERVICE_PROTOCOLS:
        raise WorkspaceConflict("service protocol is not allowlisted")
    if not service_key or len(service_key) > 100 or any(char in service_key for char in "*/\\:"):
        raise WorkspaceConflict("logical service key is invalid")
    if logical_port < 1 or logical_port > 65535:
        raise WorkspaceConflict("logical service port is invalid")
    if not actions or not set(actions).issubset({"sync.read", "sync.write", "service.consume"}):
        raise WorkspaceConflict("service actions are not allowlisted")
    now = _db_now(session)
    peer = session.execute(
        select(PeerGrant)
        .where(
            PeerGrant.tenant_id == tenant_id,
            PeerGrant.workspace_id == workspace_id,
            PeerGrant.source_node_id == node_id,
            PeerGrant.state == "active",
            PeerGrant.expires_at > now,
            PeerGrant.actions.contains(["service.publish"]),
        )
        .with_for_update()
    ).first()
    if peer is None:
        raise WorkspaceNotFound("active service publication grant not found")
    service = ServiceAdvertisement(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        node_id=node_id,
        service_key=service_key,
        protocol=protocol,
        logical_port=logical_port,
        actions=sorted(set(actions)),
        generation=generation,
        state="active",
        expires_at=now + timedelta(seconds=ttl_seconds),
    )
    session.add(service)
    session.flush()
    return service


def acquire_network_lease(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    peer_grant_id: str,
    service_id: str,
    requester_node_id: str,
    ttl_seconds: int,
) -> NetworkLease:
    """Issue a logical lease only; P34.4 never activates a real/fake provider here."""
    if ttl_seconds < 5 or ttl_seconds > 300:
        raise LeaseRejected("network lease duration is outside the safe range")
    now = _db_now(session)
    located_peer = session.execute(
        select(PeerGrant).where(
            PeerGrant.id == peer_grant_id,
            PeerGrant.tenant_id == tenant_id,
            PeerGrant.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    located_service = session.execute(
        select(ServiceAdvertisement).where(
            ServiceAdvertisement.id == service_id,
            ServiceAdvertisement.tenant_id == tenant_id,
            ServiceAdvertisement.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if located_peer is None or located_service is None:
        raise LeaseRejected("peer or service authorization is unavailable")
    workspace = session.execute(
        select(Workspace)
        .where(
            Workspace.id == workspace_id,
            Workspace.tenant_id == tenant_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if workspace is None:
        raise LeaseRejected("workspace authorization is unavailable")
    for current_node_id in sorted(
        {located_peer.source_node_id, located_peer.target_node_id, requester_node_id}
    ):
        _get_active_node(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            node_id=current_node_id,
            lock=True,
        )
    peer = session.execute(
        select(PeerGrant)
        .where(
            PeerGrant.id == peer_grant_id,
            PeerGrant.tenant_id == tenant_id,
            PeerGrant.workspace_id == workspace_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    service = session.execute(
        select(ServiceAdvertisement)
        .where(
            ServiceAdvertisement.id == service_id,
            ServiceAdvertisement.tenant_id == tenant_id,
            ServiceAdvertisement.workspace_id == workspace_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if (
        peer is None
        or service is None
        or peer.state != "active"
        or peer.expires_at <= now
        or service.state != "active"
        or service.expires_at <= now
        or requester_node_id != peer.target_node_id
        or service.node_id != peer.source_node_id
        or service.generation != workspace.generation
        or "service.consume" not in peer.actions
    ):
        raise LeaseRejected("peer or service authorization is unavailable")
    cursor = session.execute(
        select(NetworkLeaseCursor)
        .where(
            NetworkLeaseCursor.tenant_id == tenant_id,
            NetworkLeaseCursor.workspace_id == workspace_id,
            NetworkLeaseCursor.peer_grant_id == peer.id,
            NetworkLeaseCursor.service_id == service.id,
            NetworkLeaseCursor.requester_node_id == requester_node_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if cursor is None:
        cursor = NetworkLeaseCursor(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            peer_grant_id=peer.id,
            service_id=service.id,
            requester_node_id=requester_node_id,
            next_fencing_token=1,
            version=1,
        )
        session.add(cursor)
        session.flush()
    existing = session.execute(
        select(NetworkLease)
        .where(
            NetworkLease.tenant_id == tenant_id,
            NetworkLease.workspace_id == workspace_id,
            NetworkLease.peer_grant_id == peer.id,
            NetworkLease.service_id == service.id,
            NetworkLease.requester_node_id == requester_node_id,
            NetworkLease.state == "active",
        )
        .with_for_update()
    ).scalar_one_or_none()
    if existing is not None:
        if existing.expires_at > now:
            raise LeaseRejected("network lease is already active")
        existing.state = "expired"
        existing.revoked_at = now
    token = cursor.next_fencing_token
    cursor.next_fencing_token += 1
    cursor.current_fencing_token = token
    cursor.version += 1
    lease = NetworkLease(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        peer_grant_id=peer.id,
        service_id=service.id,
        requester_node_id=requester_node_id,
        fencing_token=token,
        state="active",
        expires_at=min(now + timedelta(seconds=ttl_seconds), peer.expires_at, service.expires_at),
    )
    session.add(lease)
    session.flush()
    return lease


def validate_network_lease(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    lease_id: str,
    requester_node_id: str,
    fencing_token: int,
) -> NetworkLease:
    now = _db_now(session)
    lease = session.execute(
        select(NetworkLease).where(
            NetworkLease.id == lease_id,
            NetworkLease.tenant_id == tenant_id,
            NetworkLease.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if lease is None or lease.state != "active" or lease.expires_at <= now:
        raise LeaseRejected("network lease is unavailable")
    workspace = session.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    peer = session.execute(
        select(PeerGrant).where(
            PeerGrant.id == lease.peer_grant_id,
            PeerGrant.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    service = session.execute(
        select(ServiceAdvertisement).where(
            ServiceAdvertisement.id == lease.service_id,
            ServiceAdvertisement.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    cursor = session.execute(
        select(NetworkLeaseCursor).where(
            NetworkLeaseCursor.tenant_id == tenant_id,
            NetworkLeaseCursor.workspace_id == workspace_id,
            NetworkLeaseCursor.peer_grant_id == lease.peer_grant_id,
            NetworkLeaseCursor.service_id == lease.service_id,
            NetworkLeaseCursor.requester_node_id == lease.requester_node_id,
        )
    ).scalar_one_or_none()
    if (
        workspace is None
        or peer is None
        or service is None
        or cursor is None
        or peer.state != "active"
        or service.state != "active"
        or peer.expires_at <= now
        or service.expires_at <= now
        or service.generation != workspace.generation
        or lease.requester_node_id != requester_node_id
        or lease.fencing_token != fencing_token
        or cursor.current_fencing_token != fencing_token
    ):
        raise LeaseRejected("network lease is stale or incorrectly fenced")
    for current_node_id in sorted({service.node_id, requester_node_id}):
        try:
            _get_active_node(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                node_id=current_node_id,
            )
        except WorkspaceNotFound as exc:
            raise LeaseRejected("network lease holder is unavailable") from exc
    return lease


__all__ = [
    "FakeLocalPeerOverlayProvider",
    "OverlayUnavailable",
    "PeerOverlayProvider",
    "TrustedNodeAttestation",
    "UnavailablePeerOverlayProvider",
    "acquire_network_lease",
    "create_peer_grant",
    "heartbeat_node",
    "publish_service",
    "register_attested_node",
    "revoke_node",
    "validate_network_lease",
]
