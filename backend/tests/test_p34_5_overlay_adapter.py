"""P34.5C Headscale adapter and trusted Node Daemon protocol tests."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta

import pytest

from omnibase.sandbox.contracts import SandboxRejected, SandboxUnavailable
from omnibase.sandbox.overlay_publication import (
    RejectingOverlayLogicalServiceMapper,
    VerifiedOverlayLogicalServiceMapper,
)
from omnibase.workspaces.overlay_adapters import (
    CliOverlayDaemonTransport,
    CliResult,
    HeadscaleOverlayAdapter,
    HttpJsonResponse,
    HttpOverlayDaemonTransport,
    InMemoryOverlayOperationLedger,
    OverlayAction,
    OverlayDaemonCommand,
    OverlayDaemonReceipt,
    OverlayOperationIntent,
    OverlayOutcomeUnknown,
    OverlayPublicationMode,
    OverlayRejected,
    OverlayState,
    OverlaySubjectKind,
    OverlayUnavailable,
    ShortLivedCredentialReference,
    VerifiedNodeDaemon,
    VerifiedOverlayBinding,
)

NOW = datetime(2026, 8, 2, 9, 0, tzinfo=UTC)
IDS = {
    "tenant": "00000000-0000-4000-8000-000000000001",
    "workspace": "00000000-0000-4000-8000-000000000002",
    "peer": "00000000-0000-4000-8000-000000000003",
    "service": "00000000-0000-4000-8000-000000000004",
    "lease": "00000000-0000-4000-8000-000000000005",
    "source": "00000000-0000-4000-8000-000000000006",
    "target": "00000000-0000-4000-8000-000000000007",
    "source_daemon": "00000000-0000-4000-8000-000000000008",
    "target_daemon": "00000000-0000-4000-8000-000000000009",
    "activate_op": "00000000-0000-4000-8000-000000000010",
    "rotate_op": "00000000-0000-4000-8000-000000000011",
    "revoke_op": "00000000-0000-4000-8000-000000000012",
    "status_op": "00000000-0000-4000-8000-000000000013",
}


def _intent(
    operation_id: str = IDS["activate_op"],
    *,
    source_kind: OverlaySubjectKind = OverlaySubjectKind.TRUSTED_NODE_DAEMON,
    publication_mode: OverlayPublicationMode = (OverlayPublicationMode.BROKER_LOGICAL_SERVICE),
) -> OverlayOperationIntent:
    return OverlayOperationIntent(
        operation_id=operation_id,
        tenant_id=IDS["tenant"],
        workspace_id=IDS["workspace"],
        peer_grant_id=IDS["peer"],
        service_id=IDS["service"],
        network_lease_id=IDS["lease"],
        source_node_id=IDS["source"],
        target_node_id=IDS["target"],
        source_subject_kind=source_kind,
        target_subject_kind=OverlaySubjectKind.TRUSTED_NODE_DAEMON,
        publication_mode=publication_mode,
    )


def _binding() -> VerifiedOverlayBinding:
    return VerifiedOverlayBinding(
        tenant_id=IDS["tenant"],
        workspace_id=IDS["workspace"],
        peer_grant_id=IDS["peer"],
        service_id=IDS["service"],
        network_lease_id=IDS["lease"],
        source_node_id=IDS["source"],
        target_node_id=IDS["target"],
        workspace_generation=4,
        service_generation=4,
        peer_fencing_token=7,
        network_fencing_token=11,
        source_node_fencing_token=13,
        target_node_fencing_token=17,
        service_logical_name="workspace.git",
        service_protocol="https",
        service_transport_protocol="tcp",
        service_port=8443,
        live_credential_generation=0,
        verified_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=60),
        verification_digest="a" * 64,
    )


def _daemon(*, node_id: str, fencing_token: int) -> VerifiedNodeDaemon:
    daemon_id = IDS["source_daemon"] if node_id == IDS["source"] else IDS["target_daemon"]
    return VerifiedNodeDaemon(
        daemon_id=daemon_id,
        node_id=node_id,
        node_fencing_token=fencing_token,
        identity_thumbprint_digest="b" * 64,
        attestation_digest="c" * 64,
        verified_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=45),
    )


def _credential(
    *,
    generation: int = 1,
    operation_id: str = IDS["activate_op"],
    action: OverlayAction = OverlayAction.ACTIVATE,
) -> ShortLivedCredentialReference:
    return ShortLivedCredentialReference(
        reference=(f"omnibase-secret://overlay/leases/{IDS['lease']}/rotation/{generation}"),
        provider="headscale",
        operation_id=operation_id,
        action=action,
        network_lease_id=IDS["lease"],
        binding_digest="a" * 64,
        rotation_generation=generation,
        issued_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=30),
        reference_digest="d" * 64,
    )


class FakeBindingVerifier:
    def __init__(self, binding: VerifiedOverlayBinding | None = None) -> None:
        self.binding = binding or _binding()
        self.actions: list[OverlayAction] = []

    def verify(
        self,
        *,
        intent: OverlayOperationIntent,
        action: OverlayAction,
    ) -> VerifiedOverlayBinding:
        del intent
        self.actions.append(action)
        return self.binding


class FakeDaemonAttestor:
    def __init__(self, *, stale_source: bool = False) -> None:
        self.stale_source = stale_source

    def attest(
        self,
        *,
        binding: VerifiedOverlayBinding,
        node_id: str,
    ) -> VerifiedNodeDaemon:
        if node_id == binding.source_node_id:
            token = binding.source_node_fencing_token - int(self.stale_source)
        else:
            token = binding.target_node_fencing_token
        return _daemon(node_id=node_id, fencing_token=token)


class FakeCredentialIssuer:
    def __init__(self, *, generation: int = 1) -> None:
        self.generation = generation
        self.calls = 0

    def issue(
        self,
        *,
        binding: VerifiedOverlayBinding,
        operation_id: str,
        action: OverlayAction,
    ) -> ShortLivedCredentialReference:
        del binding
        self.calls += 1
        return _credential(
            generation=self.generation,
            operation_id=operation_id,
            action=action,
        )


def _receipt(
    command: OverlayDaemonCommand,
    *,
    state: OverlayState,
) -> OverlayDaemonReceipt:
    binding = command.binding
    return OverlayDaemonReceipt(
        action=command.action,
        operation_id=command.operation_id,
        provider=command.provider,
        state=state,
        network_lease_id=binding.network_lease_id,
        binding_digest=binding.verification_digest,
        workspace_generation=binding.workspace_generation,
        service_generation=binding.service_generation,
        peer_fencing_token=binding.peer_fencing_token,
        network_fencing_token=binding.network_fencing_token,
        source_node_fencing_token=binding.source_node_fencing_token,
        target_node_fencing_token=binding.target_node_fencing_token,
        source_daemon_attestation_digest=command.source_daemon.attestation_digest,
        target_daemon_attestation_digest=command.target_daemon.attestation_digest,
        credential_generation=(
            command.credential.rotation_generation if command.credential else None
        ),
        observed_at=NOW,
        receipt_digest="e" * 64,
    )


class FakeDaemonTransport:
    """Stateful local fixture; no socket, route, subprocess, or Headscale account."""

    def __init__(self) -> None:
        self.state = OverlayState.REVOKED
        self.offline = False
        self.commands: list[OverlayDaemonCommand] = []
        self.completed: dict[str, tuple[OverlayDaemonCommand, OverlayDaemonReceipt]] = {}

    def _apply(
        self,
        *,
        command: OverlayDaemonCommand,
        state: OverlayState,
    ) -> OverlayDaemonReceipt:
        self.commands.append(command)
        if self.offline:
            return _receipt(command, state=OverlayState.OFFLINE)
        previous = self.completed.get(command.operation_id)
        if previous is not None:
            previous_command, previous_receipt = previous
            if previous_command != command:
                raise OverlayRejected("fake_daemon_idempotency_drift")
            return previous_receipt
        receipt = _receipt(command, state=state)
        self.state = state
        self.completed[command.operation_id] = (command, receipt)
        return receipt

    def activate(self, *, command: OverlayDaemonCommand) -> OverlayDaemonReceipt:
        return self._apply(command=command, state=OverlayState.ACTIVE)

    def rotate(self, *, command: OverlayDaemonCommand) -> OverlayDaemonReceipt:
        return self._apply(command=command, state=OverlayState.ACTIVE)

    def revoke(self, *, command: OverlayDaemonCommand) -> OverlayDaemonReceipt:
        return self._apply(command=command, state=OverlayState.REVOKED)

    def status(self, *, command: OverlayDaemonCommand) -> OverlayDaemonReceipt:
        self.commands.append(command)
        if self.offline:
            return _receipt(command, state=OverlayState.OFFLINE)
        return _receipt(command, state=self.state)


def _adapter(
    *,
    transport: FakeDaemonTransport | None = None,
    attestor: FakeDaemonAttestor | None = None,
    credential_generation: int = 1,
    binding: VerifiedOverlayBinding | None = None,
    ledger: InMemoryOverlayOperationLedger | None = None,
    credential_issuer: FakeCredentialIssuer | None = None,
) -> tuple[HeadscaleOverlayAdapter, FakeDaemonTransport]:
    selected_transport = transport or FakeDaemonTransport()
    return (
        HeadscaleOverlayAdapter(
            binding_verifier=FakeBindingVerifier(binding),
            daemon_attestor=attestor or FakeDaemonAttestor(),
            credential_issuer=credential_issuer
            or FakeCredentialIssuer(generation=credential_generation),
            operation_ledger=ledger or InMemoryOverlayOperationLedger(),
            transport=selected_transport,
            clock=lambda: NOW,
        ),
        selected_transport,
    )


def test_production_defaults_reject_before_any_overlay_side_effect() -> None:
    adapter = HeadscaleOverlayAdapter(clock=lambda: NOW)

    with pytest.raises(OverlayUnavailable, match="binding_verifier_unavailable"):
        adapter.activate(intent=_intent())


def test_unavailable_transport_remains_the_default_after_all_proofs_pass() -> None:
    adapter = HeadscaleOverlayAdapter(
        binding_verifier=FakeBindingVerifier(),
        daemon_attestor=FakeDaemonAttestor(),
        credential_issuer=FakeCredentialIssuer(),
        operation_ledger=InMemoryOverlayOperationLedger(),
        clock=lambda: NOW,
    )

    with pytest.raises(OverlayUnavailable, match="transport_unavailable"):
        adapter.activate(intent=_intent())


def test_missing_durable_operation_ledger_rejects_before_transport() -> None:
    daemon = FakeDaemonTransport()
    adapter = HeadscaleOverlayAdapter(
        binding_verifier=FakeBindingVerifier(),
        daemon_attestor=FakeDaemonAttestor(),
        credential_issuer=FakeCredentialIssuer(),
        transport=daemon,
        clock=lambda: NOW,
    )

    with pytest.raises(OverlayUnavailable, match="operation_ledger_unavailable"):
        adapter.activate(intent=_intent())
    assert daemon.commands == []


@pytest.mark.parametrize(
    ("source_kind", "publication_mode", "message"),
    [
        (
            OverlaySubjectKind.SANDBOX,
            OverlayPublicationMode.BROKER_LOGICAL_SERVICE,
            "Sandbox cannot become an Overlay peer",
        ),
        (
            OverlaySubjectKind.TRUSTED_NODE_DAEMON,
            OverlayPublicationMode.DIRECT_ENDPOINT,
            "published by the logical Broker",
        ),
    ],
)
def test_sandbox_peers_and_direct_endpoint_publication_are_impossible(
    source_kind: OverlaySubjectKind,
    publication_mode: OverlayPublicationMode,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _intent(source_kind=source_kind, publication_mode=publication_mode)


def test_activate_is_exactly_idempotent_and_carries_only_logical_service_state() -> None:
    adapter, daemon = _adapter()
    intent = _intent()

    first = adapter.activate(intent=intent)
    second = adapter.activate(intent=intent)

    assert first == second
    assert first.usable is True
    assert len(daemon.completed) == 1
    command = daemon.commands[-1]
    assert command.provider == "headscale"
    assert command.binding.service_id == IDS["service"]
    assert command.binding.service_protocol == "https"
    assert command.credential is not None
    assert command.credential.reference.startswith("omnibase-secret://overlay/")
    assert not hasattr(command, "auth_key")
    assert not hasattr(command.binding, "ip_address")
    assert not hasattr(command.binding, "provider_handle")


def test_durable_ledger_replays_across_adapter_instances_without_transport() -> None:
    ledger = InMemoryOverlayOperationLedger()
    issuer = FakeCredentialIssuer()
    first_daemon = FakeDaemonTransport()
    first_adapter, _ = _adapter(
        transport=first_daemon,
        ledger=ledger,
        credential_issuer=issuer,
    )

    first = first_adapter.activate(intent=_intent())

    replay_daemon = FakeDaemonTransport()
    replay_adapter, _ = _adapter(
        transport=replay_daemon,
        ledger=ledger,
        credential_issuer=issuer,
    )
    replay = replay_adapter.activate(intent=_intent())

    assert replay == first
    assert len(first_daemon.commands) == 1
    assert replay_daemon.commands == []
    assert issuer.calls == 1


def test_same_operation_id_with_action_or_live_binding_drift_is_rejected() -> None:
    ledger = InMemoryOverlayOperationLedger()
    adapter, daemon = _adapter(ledger=ledger)
    adapter.activate(intent=_intent())

    with pytest.raises(OverlayRejected, match="operation_binding_conflict"):
        adapter.revoke(intent=_intent())

    drifted_adapter, _ = _adapter(
        ledger=ledger,
        transport=daemon,
        binding=replace(_binding(), service_generation=5),
    )
    with pytest.raises(OverlayRejected, match="operation_binding_conflict"):
        drifted_adapter.activate(intent=_intent())


def test_stale_node_daemon_fencing_rejects_before_transport() -> None:
    daemon = FakeDaemonTransport()
    adapter, _ = _adapter(
        transport=daemon,
        attestor=FakeDaemonAttestor(stale_source=True),
    )

    with pytest.raises(OverlayRejected, match="daemon_attestation_rejected"):
        adapter.activate(intent=_intent())

    assert daemon.commands == []


def test_binding_generation_drift_rejects_before_transport() -> None:
    daemon = FakeDaemonTransport()
    drifted = replace(_binding(), workspace_id="00000000-0000-4000-8000-000000000099")
    adapter, _ = _adapter(transport=daemon, binding=drifted)

    with pytest.raises(OverlayRejected, match="overlay_binding_rejected"):
        adapter.activate(intent=_intent())

    assert daemon.commands == []


def test_offline_activation_is_durably_unknown_and_never_auto_replayed() -> None:
    daemon = FakeDaemonTransport()
    daemon.offline = True
    adapter, _ = _adapter(transport=daemon)
    intent = _intent()

    with pytest.raises(OverlayOutcomeUnknown, match="offline_during_mutation"):
        adapter.activate(intent=intent)

    assert daemon.completed == {}
    daemon.offline = False
    with pytest.raises(OverlayOutcomeUnknown, match="operation_outcome_unknown"):
        adapter.activate(intent=intent)
    assert daemon.completed == {}
    assert len(daemon.commands) == 1


def test_rotation_must_advance_and_revocation_remains_idempotent() -> None:
    adapter, daemon = _adapter()
    adapter.activate(intent=_intent())

    live_generation = replace(_binding(), live_credential_generation=1)
    bad_rotation, _ = _adapter(
        transport=daemon,
        credential_generation=1,
        binding=live_generation,
    )
    with pytest.raises(OverlayRejected, match="rotation_not_monotonic"):
        bad_rotation.rotate(intent=_intent(IDS["rotate_op"]))

    rotating, _ = _adapter(
        transport=daemon,
        credential_generation=2,
        binding=live_generation,
    )
    rotated = rotating.rotate(intent=_intent(IDS["rotate_op"]))
    assert rotated.credential_generation == 2
    assert rotated.state is OverlayState.ACTIVE

    revoked_first = rotating.revoke(intent=_intent(IDS["revoke_op"]))
    revoked_second = rotating.revoke(intent=_intent(IDS["revoke_op"]))
    assert revoked_first == revoked_second
    assert revoked_first.state is OverlayState.REVOKED
    with pytest.raises(OverlayRejected, match="not_usable"):
        rotating.require_active(intent=_intent(IDS["status_op"]))


def test_rotation_generation_is_monotonic_relative_to_live_generation() -> None:
    live = replace(_binding(), live_credential_generation=41)
    daemon = FakeDaemonTransport()
    rejected, _ = _adapter(
        transport=daemon,
        binding=live,
        credential_generation=41,
    )
    with pytest.raises(OverlayRejected, match="rotation_not_monotonic"):
        rejected.rotate(intent=_intent(IDS["rotate_op"]))
    assert daemon.commands == []

    accepted, _ = _adapter(
        transport=daemon,
        binding=live,
        credential_generation=42,
    )
    receipt = accepted.rotate(intent=_intent(IDS["rotate_op"]))
    assert receipt.credential_generation == 42


def test_active_overlay_publication_maps_to_broker_logical_service_without_secrets() -> None:
    adapter, _ = _adapter()
    adapter.activate(intent=_intent())

    publication = adapter.publish_logical_service(
        intent=_intent(IDS["status_op"]),
    )
    serialized = json.dumps(asdict(publication), sort_keys=True, default=str)
    for forbidden in (
        "address",
        "route",
        "credential",
        "provider_handle",
        "auth_key",
        "tskey-",
        "runtime_instance",
    ):
        assert forbidden not in serialized.lower()

    logical_service = VerifiedOverlayLogicalServiceMapper(
        clock=lambda: NOW,
    ).map_publication(publication)
    assert str(logical_service.service_id) == IDS["service"]
    assert str(logical_service.publisher_node_id) == IDS["target"]
    assert logical_service.logical_name == "workspace.git"
    assert logical_service.protocol.value == "tcp"
    assert logical_service.logical_port == 8443
    assert not hasattr(logical_service, "address")
    assert not hasattr(logical_service, "route")
    assert not hasattr(logical_service, "credential")


def test_overlay_publication_mapping_is_fail_closed_and_digest_bound() -> None:
    adapter, _ = _adapter()
    adapter.activate(intent=_intent())
    publication = adapter.publish_logical_service(intent=_intent(IDS["status_op"]))

    with pytest.raises(SandboxUnavailable, match="mapper_unavailable"):
        RejectingOverlayLogicalServiceMapper().map_publication(publication)

    tampered = replace(publication, logical_port=9443)
    with pytest.raises(SandboxRejected, match="publication_rejected"):
        VerifiedOverlayLogicalServiceMapper(clock=lambda: NOW).map_publication(tampered)


def test_status_is_read_only_idempotent_and_offline_is_never_usable() -> None:
    adapter, daemon = _adapter()
    adapter.activate(intent=_intent())
    status_intent = _intent(IDS["status_op"])

    first = adapter.status(intent=status_intent)
    second = adapter.status(intent=status_intent)
    assert first == second
    assert first.usable is True

    daemon.offline = True
    offline = adapter.status(intent=status_intent)
    assert offline.state is OverlayState.OFFLINE
    assert offline.usable is False
    with pytest.raises(OverlayRejected, match="not_usable"):
        adapter.require_active(intent=status_intent)

    daemon.offline = False
    daemon.state = OverlayState.UNKNOWN
    unknown = adapter.status(intent=status_intent)
    assert unknown.state is OverlayState.UNKNOWN
    assert unknown.usable is False
    with pytest.raises(OverlayRejected, match="not_usable"):
        adapter.require_active(intent=status_intent)


def test_headscale_adapter_rejects_a_cross_provider_credential_reference() -> None:
    class WrongProviderIssuer(FakeCredentialIssuer):
        def issue(
            self,
            *,
            binding: VerifiedOverlayBinding,
            operation_id: str,
            action: OverlayAction,
        ) -> ShortLivedCredentialReference:
            credential = super().issue(
                binding=binding,
                operation_id=operation_id,
                action=action,
            )
            return replace(credential, provider="other_overlay")

    daemon = FakeDaemonTransport()
    adapter = HeadscaleOverlayAdapter(
        binding_verifier=FakeBindingVerifier(),
        daemon_attestor=FakeDaemonAttestor(),
        credential_issuer=WrongProviderIssuer(),
        operation_ledger=InMemoryOverlayOperationLedger(),
        transport=daemon,
        clock=lambda: NOW,
    )

    with pytest.raises(OverlayRejected, match="credential_provider_rejected"):
        adapter.activate(intent=_intent())
    assert daemon.commands == []


def test_raw_or_url_embedded_credentials_are_rejected() -> None:
    with pytest.raises(ValueError, match="opaque Overlay secret reference"):
        replace(_credential(), reference="raw-provider-key-material")
    with pytest.raises(ValueError, match="opaque Overlay secret reference"):
        replace(
            _credential(),
            reference="omnibase-secret://user:secret@overlay/leases/x?key=raw",
        )


def _command(*, action: OverlayAction = OverlayAction.ACTIVATE) -> OverlayDaemonCommand:
    binding = _binding()
    return OverlayDaemonCommand(
        action=action,
        operation_id=(IDS["activate_op"] if action is OverlayAction.ACTIVATE else IDS["status_op"]),
        provider="headscale",
        binding=binding,
        source_daemon=_daemon(
            node_id=binding.source_node_id,
            fencing_token=binding.source_node_fencing_token,
        ),
        target_daemon=_daemon(
            node_id=binding.target_node_id,
            fencing_token=binding.target_node_fencing_token,
        ),
        credential=(
            _credential(operation_id=IDS["activate_op"], action=action)
            if action is OverlayAction.ACTIVATE
            else None
        ),
        requested_at=NOW,
    )


def _receipt_body(command: OverlayDaemonCommand, state: OverlayState) -> dict[str, object]:
    receipt = _receipt(command, state=state)
    return {
        "action": receipt.action.value,
        "operation_id": receipt.operation_id,
        "provider": receipt.provider,
        "state": receipt.state.value,
        "network_lease_id": receipt.network_lease_id,
        "binding_digest": receipt.binding_digest,
        "workspace_generation": receipt.workspace_generation,
        "service_generation": receipt.service_generation,
        "peer_fencing_token": receipt.peer_fencing_token,
        "network_fencing_token": receipt.network_fencing_token,
        "source_node_fencing_token": receipt.source_node_fencing_token,
        "target_node_fencing_token": receipt.target_node_fencing_token,
        "source_daemon_attestation_digest": receipt.source_daemon_attestation_digest,
        "target_daemon_attestation_digest": receipt.target_daemon_attestation_digest,
        "credential_generation": receipt.credential_generation,
        "observed_at": receipt.observed_at.isoformat(),
        "receipt_digest": receipt.receipt_digest,
    }


class FakeHttpDaemon:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, Mapping[str, object], float]] = []
        self.timeout = False

    def request_json(
        self,
        *,
        method: str,
        url: str,
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> HttpJsonResponse:
        self.requests.append((method, url, payload, timeout_seconds))
        if self.timeout:
            raise TimeoutError
        command = _command(
            action=OverlayAction(str(payload["action"])),
        )
        return HttpJsonResponse(
            status_code=200,
            body=_receipt_body(
                command,
                OverlayState.ACTIVE
                if command.action is OverlayAction.ACTIVATE
                else OverlayState.REVOKED,
            ),
        )


def test_http_transport_uses_injected_mtls_client_and_reference_only_payload() -> None:
    client = FakeHttpDaemon()
    transport = HttpOverlayDaemonTransport(
        base_url="https://node-daemon.internal",
        client=client,
    )
    command = _command()

    first = transport.activate(command=command)
    second = transport.activate(command=command)

    assert first == second
    method, url, payload, timeout = client.requests[0]
    assert (method, url, timeout) == (
        "POST",
        "https://node-daemon.internal/v1/overlay/activate",
        5.0,
    )
    serialized = json.dumps(payload, sort_keys=True)
    assert "omnibase-secret://overlay/" in serialized
    assert "tskey-" not in serialized
    assert "auth_key" not in serialized
    assert "broker_logical_service" in serialized
    assert "sandbox" not in serialized.lower()


def test_http_transport_never_auto_replays_ambiguous_mutation() -> None:
    client = FakeHttpDaemon()
    client.timeout = True
    transport = HttpOverlayDaemonTransport(
        base_url="https://node-daemon.internal",
        client=client,
    )

    with pytest.raises(OverlayOutcomeUnknown, match="outcome_unknown"):
        transport.activate(command=_command())
    assert len(client.requests) == 1

    with pytest.raises(OverlayUnavailable, match="offline"):
        transport.status(command=_command(action=OverlayAction.STATUS))
    assert len(client.requests) == 2


class FakeCliDaemon:
    def __init__(self, command: OverlayDaemonCommand) -> None:
        self.command = command
        self.calls: list[tuple[Sequence[str], str, float]] = []

    def run(
        self,
        *,
        argv: Sequence[str],
        stdin: str,
        timeout_seconds: float,
    ) -> CliResult:
        self.calls.append((argv, stdin, timeout_seconds))
        return CliResult(
            exit_code=0,
            stdout=json.dumps(_receipt_body(self.command, OverlayState.ACTIVE)),
            stderr="",
        )


def test_cli_transport_is_fixed_path_no_shell_and_passes_json_on_stdin() -> None:
    command = _command()
    runner = FakeCliDaemon(command)
    transport = CliOverlayDaemonTransport(
        executable_path="/usr/local/libexec/omnibase-node-daemon",
        runner=runner,
    )

    receipt = transport.activate(command=command)

    assert receipt.state is OverlayState.ACTIVE
    argv, stdin, timeout = runner.calls[0]
    assert tuple(argv) == (
        "/usr/local/libexec/omnibase-node-daemon",
        "overlay",
        "activate",
        "--input-json",
        "-",
    )
    assert timeout == 5.0
    assert "omnibase-secret://overlay/" in stdin
    assert "tskey-" not in stdin
    assert "shell" not in stdin
