"""Disposable Headscale + real mTLS Node-Daemon transport Gate for P34.5C."""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from omnibase.workspaces.overlay_adapters import (
    HeadscaleOverlayAdapter,
    HttpOverlayDaemonTransport,
    MtlsHttpJsonClient,
    OverlayAction,
    OverlayOperationIntent,
    OverlayOutcomeUnknown,
    OverlayPublicationMode,
    OverlayRejected,
    OverlayState,
    OverlaySubjectKind,
    OverlayUnavailable,
    ShortLivedCredentialReference,
    SqliteOverlayOperationLedger,
    VerifiedNodeDaemon,
    VerifiedOverlayBinding,
)

pytestmark = pytest.mark.integration

IDS = {
    "tenant": "10000000-0000-4000-8000-000000000001",
    "workspace": "10000000-0000-4000-8000-000000000002",
    "peer": "10000000-0000-4000-8000-000000000003",
    "service": "10000000-0000-4000-8000-000000000004",
    "lease": "10000000-0000-4000-8000-000000000005",
    "source": "10000000-0000-4000-8000-000000000006",
    "target": "10000000-0000-4000-8000-000000000007",
    "source_daemon": "10000000-0000-4000-8000-000000000008",
    "target_daemon": "10000000-0000-4000-8000-000000000009",
    "activate": "10000000-0000-4000-8000-000000000010",
    "status": "10000000-0000-4000-8000-000000000011",
    "rotate": "10000000-0000-4000-8000-000000000012",
    "ambiguous": "10000000-0000-4000-8000-000000000013",
    "reconnect": "10000000-0000-4000-8000-000000000014",
    "revoke": "10000000-0000-4000-8000-000000000015",
    "revoked_status": "10000000-0000-4000-8000-000000000016",
    "stale": "10000000-0000-4000-8000-000000000017",
}


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is required by the disposable Overlay Gate")
    return value


def _wait_headscale_health(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname != "headscale" or parsed.query:
        raise AssertionError("Headscale health URL is outside the disposable project")
    last_error: BaseException | None = None
    for _ in range(40):
        connection = HTTPConnection(parsed.hostname, parsed.port or 80, timeout=1.0)
        try:
            connection.request("GET", parsed.path or "/")
            response = connection.getresponse()
            if response.status == 200:
                decoded = json.loads(response.read(4096))
                assert decoded == {"status": "pass"}
                return
        except BaseException as exc:  # pragma: no cover - startup diagnostics
            last_error = exc
        finally:
            connection.close()
        time.sleep(0.25)
    raise AssertionError(f"Headscale health did not become ready: {last_error!r}")


def _intent(operation_id: str) -> OverlayOperationIntent:
    return OverlayOperationIntent(
        operation_id=operation_id,
        tenant_id=IDS["tenant"],
        workspace_id=IDS["workspace"],
        peer_grant_id=IDS["peer"],
        service_id=IDS["service"],
        network_lease_id=IDS["lease"],
        source_node_id=IDS["source"],
        target_node_id=IDS["target"],
        source_subject_kind=OverlaySubjectKind.TRUSTED_NODE_DAEMON,
        target_subject_kind=OverlaySubjectKind.TRUSTED_NODE_DAEMON,
        publication_mode=OverlayPublicationMode.BROKER_LOGICAL_SERVICE,
    )


class MutableBindingVerifier:
    def __init__(self) -> None:
        self.service_generation = 4
        self.live_credential_generation = 0

    def verify(
        self,
        *,
        intent: OverlayOperationIntent,
        action: OverlayAction,
    ) -> VerifiedOverlayBinding:
        del action
        now = datetime.now(UTC)
        digest_material = (
            f"{intent.operation_id}:{self.service_generation}:" f"{self.live_credential_generation}"
        ).encode()
        import hashlib

        return VerifiedOverlayBinding(
            tenant_id=intent.tenant_id,
            workspace_id=intent.workspace_id,
            peer_grant_id=intent.peer_grant_id,
            service_id=intent.service_id,
            network_lease_id=intent.network_lease_id,
            source_node_id=intent.source_node_id,
            target_node_id=intent.target_node_id,
            workspace_generation=4,
            service_generation=self.service_generation,
            peer_fencing_token=7,
            network_fencing_token=11,
            source_node_fencing_token=13,
            target_node_fencing_token=17,
            service_logical_name="workspace.git",
            service_protocol="git",
            service_transport_protocol="tcp",
            service_port=8443,
            live_credential_generation=self.live_credential_generation,
            verified_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(seconds=30),
            verification_digest=hashlib.sha256(digest_material).hexdigest(),
        )


class LiveDaemonAttestor:
    def attest(
        self,
        *,
        binding: VerifiedOverlayBinding,
        node_id: str,
    ) -> VerifiedNodeDaemon:
        now = datetime.now(UTC)
        source = node_id == binding.source_node_id
        return VerifiedNodeDaemon(
            daemon_id=IDS["source_daemon"] if source else IDS["target_daemon"],
            node_id=node_id,
            node_fencing_token=(
                binding.source_node_fencing_token if source else binding.target_node_fencing_token
            ),
            identity_thumbprint_digest=("a" if source else "b") * 64,
            attestation_digest=("c" if source else "d") * 64,
            verified_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(seconds=20),
        )


class MutableCredentialIssuer:
    def __init__(self) -> None:
        self.generation = 1
        self.calls = 0

    def issue(
        self,
        *,
        binding: VerifiedOverlayBinding,
        operation_id: str,
        action: OverlayAction,
    ) -> ShortLivedCredentialReference:
        self.calls += 1
        now = datetime.now(UTC)
        return ShortLivedCredentialReference(
            reference=(
                "omnibase-secret://overlay/leases/"
                f"{binding.network_lease_id}/rotation/{self.generation}"
            ),
            provider="headscale",
            operation_id=operation_id,
            action=action,
            network_lease_id=binding.network_lease_id,
            binding_digest=binding.verification_digest,
            rotation_generation=self.generation,
            issued_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(seconds=15),
            reference_digest=("e" if action is OverlayAction.ACTIVATE else "f") * 64,
        )


def _adapter(
    *,
    verifier: MutableBindingVerifier,
    issuer: MutableCredentialIssuer,
    ledger_path: Path,
    transport: HttpOverlayDaemonTransport,
) -> HeadscaleOverlayAdapter:
    return HeadscaleOverlayAdapter(
        binding_verifier=verifier,
        daemon_attestor=LiveDaemonAttestor(),
        credential_issuer=issuer,
        operation_ledger=SqliteOverlayOperationLedger(database_path=str(ledger_path)),
        transport=transport,
    )


def _daemon_state(*, client: MtlsHttpJsonClient, base_url: str) -> dict[str, object]:
    response = client.request_json(
        method="POST",
        url=f"{base_url}/test/state",
        payload={"probe": True},
        timeout_seconds=2.0,
    )
    assert response.status_code == 200
    return dict(response.body)


def _provider_records(state: dict[str, object]) -> dict[str, bool]:
    records = state.get("provider_records")
    assert isinstance(records, list)
    decoded: dict[str, bool] = {}
    for record in records:
        assert isinstance(record, dict)
        record_id = record.get("record_id")
        active = record.get("active")
        assert isinstance(record_id, str)
        assert isinstance(active, bool)
        decoded[record_id] = active
    return decoded


def test_disposable_headscale_and_mtls_node_daemon_lifecycle() -> None:
    if (
        os.environ.get("OMNIBASE_OVERLAY_GATE_EXPECT_OFFLINE") == "1"
        or os.environ.get("OMNIBASE_OVERLAY_GATE_EXPECT_RECONNECTED") == "1"
    ):
        pytest.skip("lifecycle is not part of the offline/reconnect probe")
    base_url = _required_env("OMNIBASE_OVERLAY_GATE_BASE_URL")
    _wait_headscale_health(_required_env("OMNIBASE_OVERLAY_GATE_HEADSCALE_HEALTH_URL"))
    client = MtlsHttpJsonClient(
        ca_file=_required_env("OMNIBASE_OVERLAY_GATE_CA_FILE"),
        certificate_file=_required_env("OMNIBASE_OVERLAY_GATE_CERT_FILE"),
        private_key_file=_required_env("OMNIBASE_OVERLAY_GATE_KEY_FILE"),
    )
    transport = HttpOverlayDaemonTransport(base_url=base_url, client=client)
    ledger_dir = Path(_required_env("OMNIBASE_OVERLAY_GATE_LEDGER_DIR"))
    ledger_dir.chmod(0o700)
    ledger_path = ledger_dir / "overlay-operations.sqlite3"
    if ledger_path.exists():
        ledger_path.unlink()

    verifier = MutableBindingVerifier()
    issuer = MutableCredentialIssuer()
    adapter = _adapter(
        verifier=verifier,
        issuer=issuer,
        ledger_path=ledger_path,
        transport=transport,
    )

    activate = adapter.activate(intent=_intent(IDS["activate"]))
    assert activate.state is OverlayState.ACTIVE
    assert activate.credential_generation == 1
    state_after_activate = _daemon_state(client=client, base_url=base_url)
    assert state_after_activate["request_count"] == 1
    activate_records = _provider_records(state_after_activate)
    assert len(activate_records) == 1
    activated_record_id = next(iter(activate_records))
    assert activate_records[activated_record_id] is True
    assert state_after_activate["provider_current_record_id"] == activated_record_id
    assert state_after_activate["provider_api_mutation_count"] == 1

    replay_adapter = _adapter(
        verifier=verifier,
        issuer=issuer,
        ledger_path=ledger_path,
        transport=transport,
    )
    assert replay_adapter.activate(intent=_intent(IDS["activate"])) == activate
    assert issuer.calls == 1
    state_after_replay = _daemon_state(client=client, base_url=base_url)
    assert state_after_replay["request_count"] == 1
    assert state_after_replay["provider_api_mutation_count"] == 1
    assert _provider_records(state_after_replay) == activate_records

    assert adapter.status(intent=_intent(IDS["status"])).state is OverlayState.ACTIVE

    verifier.live_credential_generation = 1
    issuer.generation = 2
    rotated = adapter.rotate(intent=_intent(IDS["rotate"]))
    assert rotated.credential_generation == 2
    state_after_rotate = _daemon_state(client=client, base_url=base_url)
    rotate_records = _provider_records(state_after_rotate)
    assert len(rotate_records) == 2
    rotated_record_id = str(state_after_rotate["provider_current_record_id"])
    assert rotated_record_id != activated_record_id
    assert rotate_records[activated_record_id] is False
    assert rotate_records[rotated_record_id] is True
    assert state_after_rotate["provider_api_mutation_count"] == 3

    stale = MutableBindingVerifier()
    stale.service_generation = 3
    stale.live_credential_generation = 2
    stale_adapter = _adapter(
        verifier=stale,
        issuer=issuer,
        ledger_path=ledger_path,
        transport=transport,
    )
    with pytest.raises(OverlayRejected, match="binding_conflict"):
        stale_adapter.status(intent=_intent(IDS["stale"]))

    client.request_json(
        method="POST",
        url=f"{base_url}/test/fault",
        payload={"mode": "drop_after_commit"},
        timeout_seconds=2.0,
    )
    verifier.service_generation = 5
    verifier.live_credential_generation = 2
    issuer.generation = 3
    state_before_ambiguous = _daemon_state(client=client, base_url=base_url)
    with pytest.raises(OverlayOutcomeUnknown, match="outcome_unknown"):
        adapter.rotate(intent=_intent(IDS["ambiguous"]))
    with pytest.raises(OverlayOutcomeUnknown, match="operation_outcome_unknown"):
        adapter.rotate(intent=_intent(IDS["ambiguous"]))
    state_after_ambiguous = _daemon_state(client=client, base_url=base_url)
    ambiguous_records = _provider_records(state_after_ambiguous)
    ambiguous_record_id = str(state_after_ambiguous["provider_current_record_id"])
    assert len(ambiguous_records) == 3
    assert ambiguous_record_id not in {activated_record_id, rotated_record_id}
    assert ambiguous_records[rotated_record_id] is False
    assert ambiguous_records[ambiguous_record_id] is True
    assert int(state_after_ambiguous["provider_api_mutation_count"]) == (
        int(state_before_ambiguous["provider_api_mutation_count"]) + 2
    )
    assert int(state_after_ambiguous["request_count"]) == (
        int(state_before_ambiguous["request_count"]) + 1
    )

    verifier.live_credential_generation = 3
    assert adapter.status(intent=_intent(IDS["reconnect"])).state is OverlayState.ACTIVE

    verifier.service_generation = 6
    revoked = adapter.revoke(intent=_intent(IDS["revoke"]))
    assert revoked.state is OverlayState.REVOKED
    assert adapter.status(intent=_intent(IDS["revoked_status"])).state is OverlayState.REVOKED
    final_state = _daemon_state(client=client, base_url=base_url)
    final_records = _provider_records(final_state)
    assert final_records[ambiguous_record_id] is False
    assert final_state["state"] == "revoked"
    assert final_state["provider_api_mutation_count"] == 6
    with pytest.raises(OverlayRejected, match="not_usable"):
        adapter.publish_logical_service(intent=_intent(IDS["revoked_status"]))

    with pytest.raises(ValueError, match="published by the logical Broker"):
        replace(
            _intent(IDS["status"]),
            publication_mode=OverlayPublicationMode.DIRECT_ENDPOINT,
        )

    serialized_receipts = repr((activate, rotated, revoked)).lower()
    serialized_state = json.dumps(final_state, sort_keys=True).lower()
    assert "preauth" not in serialized_receipts
    assert "provider_record" not in serialized_receipts
    assert "authorization" not in serialized_state
    assert "api_key" not in serialized_state
    assert "preauth" not in serialized_state
    evidence = {
        "activate_created_real_record": activate_records[activated_record_id],
        "ambiguous_mutation_not_replayed": (
            int(state_after_ambiguous["request_count"])
            == int(state_before_ambiguous["request_count"]) + 1
        ),
        "provider_api_mutation_count": int(final_state["provider_api_mutation_count"]),
        "provider_record_count": len(final_records),
        "receipts_redacted": "provider_record" not in serialized_receipts,
        "revoke_expired_current_record": not final_records[ambiguous_record_id],
        "rotate_created_active_record": rotate_records[rotated_record_id],
        "rotate_expired_old_record": not rotate_records[activated_record_id],
        "status_used_headscale_truth": (
            state_after_activate["state"] == "active" and final_state["state"] == "revoked"
        ),
    }
    print(f"P34_5_PROVIDER_EVIDENCE={json.dumps(evidence, sort_keys=True)}")


def test_mtls_node_daemon_offline_is_fail_closed() -> None:
    if os.environ.get("OMNIBASE_OVERLAY_GATE_EXPECT_OFFLINE") != "1":
        pytest.skip("offline probe is run only after the disposable daemon is stopped")
    base_url = _required_env("OMNIBASE_OVERLAY_GATE_BASE_URL")
    client = MtlsHttpJsonClient(
        ca_file=_required_env("OMNIBASE_OVERLAY_GATE_CA_FILE"),
        certificate_file=_required_env("OMNIBASE_OVERLAY_GATE_CERT_FILE"),
        private_key_file=_required_env("OMNIBASE_OVERLAY_GATE_KEY_FILE"),
    )
    adapter = HeadscaleOverlayAdapter(
        binding_verifier=MutableBindingVerifier(),
        daemon_attestor=LiveDaemonAttestor(),
        transport=HttpOverlayDaemonTransport(base_url=base_url, client=client),
    )
    with pytest.raises(OverlayUnavailable, match="node_daemon_offline"):
        adapter.status(intent=_intent(IDS["status"]))


def test_mtls_node_daemon_reconnects_after_container_restart() -> None:
    if os.environ.get("OMNIBASE_OVERLAY_GATE_EXPECT_RECONNECTED") != "1":
        pytest.skip("reconnect probe is run only after the daemon is restarted")
    base_url = _required_env("OMNIBASE_OVERLAY_GATE_BASE_URL")
    client = MtlsHttpJsonClient(
        ca_file=_required_env("OMNIBASE_OVERLAY_GATE_CA_FILE"),
        certificate_file=_required_env("OMNIBASE_OVERLAY_GATE_CERT_FILE"),
        private_key_file=_required_env("OMNIBASE_OVERLAY_GATE_KEY_FILE"),
    )
    response = client.request_json(
        method="POST",
        url=f"{base_url}/test/state",
        payload={"probe": True},
        timeout_seconds=2.0,
    )
    assert response.status_code == 200
    assert response.body["state"] in {"active", "revoked"}
