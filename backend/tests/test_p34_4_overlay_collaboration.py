"""P34.4 fail-closed overlay and synthetic collaboration harness tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from omnibase.workspaces import collaboration, overlay, service
from omnibase.workspaces.models import NetworkLease, NetworkLeaseCursor
from omnibase.workspaces.service import (
    LeaseRejected,
    WorkspaceConflict,
    WorkspaceNotFound,
)


def _result(*, one: object | None = None, optional: object | None = None) -> MagicMock:
    result = MagicMock()
    result.scalar_one.return_value = one
    result.scalar_one_or_none.return_value = optional
    return result


def test_fake_overlay_provider_is_an_independent_in_memory_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket

    monkeypatch.setattr(
        socket,
        "socket",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network touched")),
    )
    provider = overlay.FakeLocalPeerOverlayProvider()

    provider.activate(
        workspace_id="workspace-a",
        source_node_id="node-a",
        target_node_id="node-b",
        service_id="service-a",
        lease_id="lease-a",
    )
    assert provider.active_lease_ids == {"lease-a"}

    provider.revoke(lease_id="lease-a")
    assert provider.active_lease_ids == set()


def test_unavailable_overlay_provider_fails_closed_for_activation_and_revoke() -> None:
    provider = overlay.UnavailablePeerOverlayProvider()

    with pytest.raises(overlay.OverlayUnavailable, match="provider_unavailable"):
        provider.activate(
            workspace_id="workspace-a",
            source_node_id="node-a",
            target_node_id="node-b",
            service_id="service-a",
            lease_id="lease-a",
        )
    with pytest.raises(overlay.OverlayUnavailable, match="provider_unavailable"):
        provider.revoke(lease_id="lease-a")


def test_logical_network_lease_issuance_allocates_cursor_without_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    peer = SimpleNamespace(
        id="peer-a",
        state="active",
        expires_at=now + timedelta(seconds=60),
        target_node_id="node-b",
        source_node_id="node-a",
        actions=["service.consume"],
    )
    service = SimpleNamespace(
        id="service-a",
        state="active",
        expires_at=now + timedelta(seconds=60),
        node_id="node-a",
        generation=4,
    )
    workspace = SimpleNamespace(generation=4)
    active_node_calls: list[tuple[str, bool]] = []

    def resolve_active_node(*args: object, **kwargs: object) -> None:
        del args
        active_node_calls.append((str(kwargs["node_id"]), bool(kwargs["lock"])))

    monkeypatch.setattr(overlay, "_db_now", lambda session: now)
    monkeypatch.setattr(overlay, "_get_active_node", resolve_active_node)
    provider_activate = MagicMock(
        side_effect=AssertionError("logical issuance called an overlay provider")
    )
    monkeypatch.setattr(
        overlay.FakeLocalPeerOverlayProvider,
        "activate",
        provider_activate,
    )
    session = MagicMock()
    session.execute.side_effect = [
        _result(optional=peer),
        _result(optional=service),
        _result(optional=workspace),
        _result(optional=peer),
        _result(optional=service),
        _result(optional=None),
        _result(optional=None),
    ]

    lease = overlay.acquire_network_lease(
        session,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        peer_grant_id="peer-a",
        service_id="service-a",
        requester_node_id="node-b",
        ttl_seconds=30,
    )

    assert active_node_calls == [("node-a", True), ("node-b", True)]
    cursor = session.add.call_args_list[0].args[0]
    assert isinstance(cursor, NetworkLeaseCursor)
    assert cursor.current_fencing_token == 1
    assert cursor.next_fencing_token == 2
    assert cursor.version == 2
    assert isinstance(lease, NetworkLease)
    assert lease.fencing_token == 1
    assert lease.state == "active"
    assert lease.expires_at == now + timedelta(seconds=30)
    assert session.flush.call_count == 2
    provider_activate.assert_not_called()


def test_network_lease_rejects_an_old_cursor_fencing_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    lease = SimpleNamespace(
        state="active",
        expires_at=now + timedelta(seconds=30),
        peer_grant_id="peer-a",
        service_id="service-a",
        requester_node_id="node-b",
        fencing_token=7,
    )
    workspace = SimpleNamespace(generation=4)
    peer = SimpleNamespace(state="active", expires_at=now + timedelta(seconds=30))
    advertised_service = SimpleNamespace(
        state="active",
        expires_at=now + timedelta(seconds=30),
        generation=4,
        node_id="node-a",
    )
    cursor = SimpleNamespace(current_fencing_token=8)
    monkeypatch.setattr(overlay, "_db_now", lambda session: now)
    resolve_active_node = MagicMock()
    monkeypatch.setattr(overlay, "_get_active_node", resolve_active_node)
    session = MagicMock()
    session.execute.side_effect = [
        _result(optional=lease),
        _result(optional=workspace),
        _result(optional=peer),
        _result(optional=advertised_service),
        _result(optional=cursor),
    ]

    with pytest.raises(LeaseRejected, match="stale or incorrectly fenced"):
        overlay.validate_network_lease(
            session,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            lease_id="lease-a",
            requester_node_id="node-b",
            fencing_token=7,
        )

    resolve_active_node.assert_not_called()


def test_get_active_attested_node_rejects_expired_attestation() -> None:
    now = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    node = SimpleNamespace(
        state="active",
        attestation_state="verified",
        revoked_at=None,
    )
    session = MagicMock()
    session.execute.side_effect = [
        _result(optional=node),
        _result(one=now),
        _result(optional=None),
    ]

    with pytest.raises(WorkspaceNotFound, match="workspace node not found"):
        service.get_active_attested_node(
            session,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            node_id="node-a",
        )

    attestation_statement = str(session.execute.call_args_list[2].args[0])
    assert "node_attestations" in attestation_statement
    assert "expires_at" in attestation_statement


def test_claim_authority_locks_workspace_before_rejecting_revoked_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = SimpleNamespace(id="workspace-a")
    session = MagicMock()
    session.execute.side_effect = [_result(optional=workspace)]

    def authorize_actor(*args: object, **kwargs: object) -> None:
        del args
        assert session.execute.call_count == 1
        assert kwargs["lock"] is True

    def reject_node(*args: object, **kwargs: object) -> None:
        del args
        assert session.execute.call_count == 1
        assert kwargs["lock"] is True
        raise WorkspaceNotFound("workspace node not found")

    authorize = MagicMock(side_effect=authorize_actor)
    active_node = MagicMock(side_effect=reject_node)
    monkeypatch.setattr(collaboration, "authorize_workspace_action", authorize)
    monkeypatch.setattr(collaboration, "_active_node", active_node)

    with pytest.raises(WorkspaceNotFound, match="workspace node not found"):
        collaboration.claim_workspace_authority(
            session,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            node_id="node-a",
            actor_user_id="maintainer-a",
        )

    workspace_statement = str(session.execute.call_args_list[0].args[0])
    assert "workspaces" in workspace_statement
    assert "FOR UPDATE" in workspace_statement
    authorize.assert_called_once_with(
        session,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="maintainer-a",
        action="workspace.nodes.manage",
        lock=True,
    )
    active_node.assert_called_once_with(
        session,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        node_id="node-a",
        lock=True,
    )
    session.add.assert_not_called()


def test_create_peer_grant_locks_workspace_before_rejecting_revoked_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = SimpleNamespace(id="workspace-a")
    session = MagicMock()
    session.execute.side_effect = [_result(optional=workspace)]

    def authorize_actor(*args: object, **kwargs: object) -> None:
        del args
        assert session.execute.call_count == 1
        assert kwargs["lock"] is True

    def reject_node(*args: object, **kwargs: object) -> None:
        del args
        assert session.execute.call_count == 1
        assert kwargs["node_id"] == "node-a"
        assert kwargs["lock"] is True
        raise WorkspaceNotFound("workspace node not found")

    authorize = MagicMock(side_effect=authorize_actor)
    active_node = MagicMock(side_effect=reject_node)
    monkeypatch.setattr(overlay, "authorize_workspace_action", authorize)
    monkeypatch.setattr(overlay, "_get_active_node", active_node)

    with pytest.raises(WorkspaceNotFound, match="workspace node not found"):
        overlay.create_peer_grant(
            session,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            actor_user_id="maintainer-a",
            source_node_id="node-a",
            target_node_id="node-b",
            actions=["service.consume"],
            expires_at=datetime(2026, 8, 1, 9, 1, tzinfo=UTC),
        )

    workspace_statement = str(session.execute.call_args_list[0].args[0])
    assert "workspaces" in workspace_statement
    assert "FOR UPDATE" in workspace_statement
    authorize.assert_called_once_with(
        session,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="maintainer-a",
        action="workspace.nodes.manage",
        lock=True,
    )
    active_node.assert_called_once_with(
        session,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        node_id="node-a",
        lock=True,
    )
    session.add.assert_not_called()


def test_publish_service_locks_workspace_before_rejecting_revoked_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = SimpleNamespace(id="workspace-a", generation=4)
    session = MagicMock()
    session.execute.side_effect = [_result(optional=workspace)]

    def reject_node(*args: object, **kwargs: object) -> None:
        del args
        assert session.execute.call_count == 1
        assert kwargs["lock"] is True
        raise WorkspaceNotFound("workspace node not found")

    active_node = MagicMock(side_effect=reject_node)
    monkeypatch.setattr(overlay, "_get_active_node", active_node)

    with pytest.raises(WorkspaceNotFound, match="workspace node not found"):
        overlay.publish_service(
            session,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            node_id="node-a",
            service_key="collaboration",
            protocol="event",
            logical_port=8443,
            actions=["service.consume"],
            generation=4,
            ttl_seconds=30,
        )

    workspace_statement = str(session.execute.call_args_list[0].args[0])
    assert "workspaces" in workspace_statement
    assert "FOR UPDATE" in workspace_statement
    active_node.assert_called_once_with(
        session,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        node_id="node-a",
        lock=True,
    )
    session.add.assert_not_called()


def test_node_revocation_fences_identity_and_cascades_all_logical_authorizations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    node = SimpleNamespace(
        state="active",
        attestation_state="verified",
        revoked_at=None,
        fencing_token=4,
        version=6,
    )
    authorize = MagicMock()
    monkeypatch.setattr(overlay, "authorize_workspace_action", authorize)
    workspace = SimpleNamespace(id="workspace-a")
    session = MagicMock()
    session.execute.side_effect = [
        _result(optional=workspace),
        _result(optional=node),
        _result(one=now),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
    ]

    result = overlay.revoke_node(
        session,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        node_id="node-a",
        actor_user_id="maintainer-a",
    )

    assert result is not None
    assert node.state == "revoked"
    assert node.attestation_state == "rejected"
    assert node.fencing_token == 5
    assert node.version == 7
    assert node.revoked_at == now
    authorize.assert_called_once_with(
        session,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="maintainer-a",
        action="workspace.nodes.manage",
        lock=True,
    )
    assert "workspaces" in str(session.execute.call_args_list[0].args[0])
    assert "workspace_nodes" in str(session.execute.call_args_list[1].args[0])
    statements = "\n".join(str(call.args[0]) for call in session.execute.call_args_list[3:])
    for table in (
        "node_attestations",
        "peer_grants",
        "service_advertisements",
        "network_leases",
        "workspace_authorities",
    ):
        assert table in statements


def _envelope(**changes: object) -> collaboration.SyncEnvelope:
    values: dict[str, object] = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "node_id": "node-a",
        "authority_epoch": 3,
        "sequence": 1,
        "event_type": "git_ref",
        "event_digest": "a" * 64,
        "previous_digest": "0" * 64,
    }
    values.update(changes)
    return collaboration.SyncEnvelope(**values)  # type: ignore[arg-type]


def test_fake_collaboration_transport_rejects_same_sequence_with_new_digest() -> None:
    transport = collaboration.FakeLocalCollaborationTransport()
    first = _envelope()
    transport.publish(first)
    transport.publish(first)

    with pytest.raises(WorkspaceConflict, match="different digest"):
        transport.publish(_envelope(event_digest="b" * 64))

    assert transport.read(workspace_id="workspace-a", after_sequence=0) == [first]


@pytest.mark.parametrize(
    "changes",
    [
        {"authority_epoch": 0},
        {"sequence": 0},
        {"event_type": "raw_file"},
        {"event_digest": "NOT-A-DIGEST"},
        {"artifact_digest": "b" * 64},
        {
            "event_type": "artifact_published",
            "artifact_digest": None,
            "artifact_size_bytes": None,
            "artifact_media_type": None,
        },
    ],
)
def test_sync_envelope_contract_rejects_invalid_or_overbroad_metadata(
    changes: dict[str, object],
) -> None:
    with pytest.raises(WorkspaceConflict):
        collaboration.validate_sync_envelope(_envelope(**changes))


@pytest.mark.parametrize(
    "authority_changes",
    [
        {"state": "offline"},
        {"epoch": 2},
        {"authority_node_id": "node-b"},
        {"lease_expires_at": datetime(2026, 8, 1, 9, 0, tzinfo=UTC)},
    ],
)
def test_offline_expired_or_stale_authority_cannot_commit(
    authority_changes: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    values = {
        "state": "active",
        "authority_node_id": "node-a",
        "epoch": 3,
        "lease_expires_at": now + timedelta(seconds=30),
    }
    values.update(authority_changes)
    authority = SimpleNamespace(**values)
    workspace = SimpleNamespace(id="workspace-a")
    active_node = MagicMock()
    monkeypatch.setattr(collaboration, "_active_node", active_node)
    session = MagicMock()
    session.execute.side_effect = [
        _result(optional=workspace),
        _result(one=now),
        _result(optional=authority),
    ]

    with pytest.raises(LeaseRejected, match="offline, expired, revoked, or stale"):
        collaboration.commit_sync_envelope(
            session,
            authority_id="authority-a",
            envelope=_envelope(),
        )

    assert session.execute.call_count == 3
    active_node.assert_called_once_with(
        session,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        node_id="node-a",
        lock=True,
    )
    session.add.assert_not_called()


def test_same_sequence_conflicting_digest_is_rejected_before_append(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    authority = SimpleNamespace(
        state="active",
        authority_node_id="node-a",
        epoch=3,
        lease_expires_at=now + timedelta(seconds=30),
    )
    workspace = SimpleNamespace(id="workspace-a")
    existing = SimpleNamespace(event_digest="b" * 64)
    active_node = MagicMock()
    monkeypatch.setattr(collaboration, "_active_node", active_node)
    session = MagicMock()
    session.execute.side_effect = [
        _result(optional=workspace),
        _result(one=now),
        _result(optional=authority),
        _result(optional=existing),
    ]

    with pytest.raises(WorkspaceConflict, match="conflicting digest"):
        collaboration.commit_sync_envelope(
            session,
            authority_id="authority-a",
            envelope=_envelope(),
        )

    active_node.assert_called_once_with(
        session,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        node_id="node-a",
        lock=True,
    )
    session.add.assert_not_called()


@pytest.mark.parametrize(
    ("sequence", "previous_digest"),
    [(3, "b" * 64), (2, "c" * 64)],
)
def test_collaboration_commit_rejects_sequence_or_digest_chain_drift(
    sequence: int,
    previous_digest: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    authority = SimpleNamespace(
        state="active",
        authority_node_id="node-a",
        epoch=3,
        lease_expires_at=now + timedelta(seconds=30),
    )
    workspace = SimpleNamespace(id="workspace-a")
    latest = SimpleNamespace(sequence=1, event_digest="b" * 64)
    active_node = MagicMock()
    monkeypatch.setattr(collaboration, "_active_node", active_node)
    session = MagicMock()
    session.execute.side_effect = [
        _result(optional=workspace),
        _result(one=now),
        _result(optional=authority),
        _result(optional=None),
        _result(optional=latest),
    ]

    with pytest.raises(WorkspaceConflict, match="sequence or previous digest drifted"):
        collaboration.commit_sync_envelope(
            session,
            authority_id="authority-a",
            envelope=_envelope(sequence=sequence, previous_digest=previous_digest),
        )

    active_node.assert_called_once_with(
        session,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        node_id="node-a",
        lock=True,
    )
    session.add.assert_not_called()


@pytest.mark.parametrize("lease_seconds", [4, 301])
def test_authority_heartbeat_rejects_unsafe_lease_duration_before_database_access(
    lease_seconds: int,
) -> None:
    session = MagicMock()

    with pytest.raises(LeaseRejected, match="duration is outside the safe range"):
        collaboration.heartbeat_workspace_authority(
            session,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            authority_id="authority-a",
            node_id="node-a",
            epoch=3,
            lease_seconds=lease_seconds,
        )

    session.execute.assert_not_called()


@pytest.mark.parametrize("lease_seconds", [5, 300])
def test_authority_heartbeat_accepts_safe_lease_duration_boundaries(
    lease_seconds: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    workspace = SimpleNamespace(id="workspace-a")
    authority = SimpleNamespace(
        state="active",
        authority_node_id="node-a",
        epoch=3,
        lease_expires_at=now + timedelta(seconds=30),
    )
    active_node = MagicMock()
    monkeypatch.setattr(collaboration, "_active_node", active_node)
    session = MagicMock()
    session.execute.side_effect = [
        _result(optional=workspace),
        _result(optional=authority),
        _result(one=now),
    ]

    result = collaboration.heartbeat_workspace_authority(
        session,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        authority_id="authority-a",
        node_id="node-a",
        epoch=3,
        lease_seconds=lease_seconds,
    )

    assert result is authority
    assert authority.lease_expires_at == now + timedelta(seconds=lease_seconds)
    active_node.assert_called_once_with(
        session,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        node_id="node-a",
        lock=True,
    )
    session.flush.assert_called_once_with()
