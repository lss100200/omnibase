"""Headscale/Tailscale-compatible Overlay adapter orchestration.

The adapter never talks to Headscale directly.  It verifies fresh OmniBase
control-plane facts, obtains opaque short-lived credential references, records
mutation idempotency in an injected durable ledger, and dispatches a fully
fenced command to separately attested member Node Daemons.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from omnibase.workspaces.overlay_adapters.contracts import (
    CredentialReferenceIssuer,
    NodeDaemonAttestor,
    OverlayAction,
    OverlayBindingVerifier,
    OverlayDaemonCommand,
    OverlayDaemonReceipt,
    OverlayLogicalServicePublication,
    OverlayOperationIntent,
    OverlayOperationLedger,
    OverlayOutcomeUnknown,
    OverlayRejected,
    OverlayState,
    RejectingCredentialReferenceIssuer,
    RejectingNodeDaemonAttestor,
    RejectingOverlayBindingVerifier,
    RejectingOverlayOperationLedger,
    VerifiedNodeDaemon,
    VerifiedOverlayBinding,
    overlay_operation_binding_digest,
)
from omnibase.workspaces.overlay_adapters.transport import (
    OverlayDaemonTransport,
    UnavailableOverlayDaemonTransport,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class HeadscaleOverlayAdapter:
    """Fail-closed adapter for a self-hosted Headscale control plane."""

    def __init__(
        self,
        *,
        binding_verifier: OverlayBindingVerifier | None = None,
        daemon_attestor: NodeDaemonAttestor | None = None,
        credential_issuer: CredentialReferenceIssuer | None = None,
        operation_ledger: OverlayOperationLedger | None = None,
        transport: OverlayDaemonTransport | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._binding_verifier = binding_verifier or RejectingOverlayBindingVerifier()
        self._daemon_attestor = daemon_attestor or RejectingNodeDaemonAttestor()
        self._credential_issuer = credential_issuer or RejectingCredentialReferenceIssuer()
        self._operation_ledger = operation_ledger or RejectingOverlayOperationLedger()
        self._transport = transport or UnavailableOverlayDaemonTransport()
        self._clock = clock

    def _proofs(
        self,
        *,
        intent: OverlayOperationIntent,
        action: OverlayAction,
    ) -> tuple[datetime, VerifiedOverlayBinding, VerifiedNodeDaemon, VerifiedNodeDaemon]:
        now = self._clock()
        binding = self._binding_verifier.verify(intent=intent, action=action)
        binding.verify_intent(intent, now=now)
        source_daemon = self._daemon_attestor.attest(
            binding=binding,
            node_id=binding.source_node_id,
        )
        source_daemon.verify_binding(
            binding,
            expected_node_id=binding.source_node_id,
            expected_fencing_token=binding.source_node_fencing_token,
            now=now,
        )
        target_daemon = self._daemon_attestor.attest(
            binding=binding,
            node_id=binding.target_node_id,
        )
        target_daemon.verify_binding(
            binding,
            expected_node_id=binding.target_node_id,
            expected_fencing_token=binding.target_node_fencing_token,
            now=now,
        )
        return now, binding, source_daemon, target_daemon

    def _command(
        self,
        *,
        intent: OverlayOperationIntent,
        action: OverlayAction,
        now: datetime,
        binding: VerifiedOverlayBinding,
        source_daemon: VerifiedNodeDaemon,
        target_daemon: VerifiedNodeDaemon,
    ) -> OverlayDaemonCommand:
        credential = None
        if action in {OverlayAction.ACTIVATE, OverlayAction.ROTATE}:
            credential = self._credential_issuer.issue(
                binding=binding,
                operation_id=intent.operation_id,
                action=action,
            )
            if credential.provider != "headscale":
                raise OverlayRejected("overlay_credential_provider_rejected")
            credential.verify_binding(
                binding,
                operation_id=intent.operation_id,
                action=action,
                now=now,
            )
            if credential.rotation_generation <= binding.live_credential_generation:
                raise OverlayRejected("overlay_credential_rotation_not_monotonic")
        return OverlayDaemonCommand(
            action=action,
            operation_id=intent.operation_id,
            provider="headscale",
            binding=binding,
            source_daemon=source_daemon,
            target_daemon=target_daemon,
            credential=credential,
            requested_at=now,
        )

    @staticmethod
    def _verify_receipt(
        *,
        command: OverlayDaemonCommand,
        receipt: OverlayDaemonReceipt,
        required_state: OverlayState | None,
    ) -> OverlayDaemonReceipt:
        receipt.verify_command(command)
        if receipt.state is OverlayState.UNKNOWN and command.action is not OverlayAction.STATUS:
            raise OverlayOutcomeUnknown("overlay_node_daemon_outcome_unknown")
        if required_state is not None and receipt.state is not required_state:
            if receipt.state is OverlayState.OFFLINE:
                raise OverlayOutcomeUnknown("overlay_node_daemon_offline_during_mutation")
            raise OverlayRejected("overlay_node_daemon_state_rejected")
        return receipt

    @staticmethod
    def _verify_replay(
        *,
        intent: OverlayOperationIntent,
        action: OverlayAction,
        binding: VerifiedOverlayBinding,
        now: datetime,
        receipt: OverlayDaemonReceipt,
        required_state: OverlayState,
    ) -> OverlayDaemonReceipt:
        receipt.verify_live_replay(
            action=action,
            operation_id=intent.operation_id,
            provider="headscale",
            binding=binding,
            now=now,
        )
        if receipt.state is not required_state:
            raise OverlayRejected("overlay_operation_replay_state_rejected")
        return receipt

    def _mutate(
        self,
        *,
        intent: OverlayOperationIntent,
        action: OverlayAction,
        required_state: OverlayState,
    ) -> OverlayDaemonReceipt:
        now, binding, source_daemon, target_daemon = self._proofs(
            intent=intent,
            action=action,
        )
        operation_digest = overlay_operation_binding_digest(
            intent=intent,
            action=action,
            binding=binding,
        )
        replay = self._operation_ledger.replay(
            operation_id=intent.operation_id,
            action=action,
            operation_binding_digest=operation_digest,
        )
        if replay is not None:
            return self._verify_replay(
                intent=intent,
                action=action,
                binding=binding,
                now=now,
                receipt=replay,
                required_state=required_state,
            )
        reservation = self._operation_ledger.reserve(
            operation_id=intent.operation_id,
            action=action,
            operation_binding_digest=operation_digest,
        )
        if reservation.replayed:
            assert reservation.receipt is not None
            return self._verify_replay(
                intent=intent,
                action=action,
                binding=binding,
                now=now,
                receipt=reservation.receipt,
                required_state=required_state,
            )
        command = self._command(
            intent=intent,
            action=action,
            now=now,
            binding=binding,
            source_daemon=source_daemon,
            target_daemon=target_daemon,
        )
        transport_method = {
            OverlayAction.ACTIVATE: self._transport.activate,
            OverlayAction.ROTATE: self._transport.rotate,
            OverlayAction.REVOKE: self._transport.revoke,
        }[action]
        receipt = transport_method(command=command)
        verified = self._verify_receipt(
            command=command,
            receipt=receipt,
            required_state=required_state,
        )
        self._operation_ledger.commit(reservation=reservation, receipt=verified)
        return verified

    def activate(self, *, intent: OverlayOperationIntent) -> OverlayDaemonReceipt:
        return self._mutate(
            intent=intent,
            action=OverlayAction.ACTIVATE,
            required_state=OverlayState.ACTIVE,
        )

    def rotate(self, *, intent: OverlayOperationIntent) -> OverlayDaemonReceipt:
        return self._mutate(
            intent=intent,
            action=OverlayAction.ROTATE,
            required_state=OverlayState.ACTIVE,
        )

    def revoke(self, *, intent: OverlayOperationIntent) -> OverlayDaemonReceipt:
        return self._mutate(
            intent=intent,
            action=OverlayAction.REVOKE,
            required_state=OverlayState.REVOKED,
        )

    def _status_command(
        self,
        *,
        intent: OverlayOperationIntent,
    ) -> tuple[OverlayDaemonCommand, OverlayDaemonReceipt]:
        now, binding, source_daemon, target_daemon = self._proofs(
            intent=intent,
            action=OverlayAction.STATUS,
        )
        command = self._command(
            intent=intent,
            action=OverlayAction.STATUS,
            now=now,
            binding=binding,
            source_daemon=source_daemon,
            target_daemon=target_daemon,
        )
        receipt = self._transport.status(command=command)
        return command, self._verify_receipt(
            command=command,
            receipt=receipt,
            required_state=None,
        )

    def status(self, *, intent: OverlayOperationIntent) -> OverlayDaemonReceipt:
        _, receipt = self._status_command(intent=intent)
        return receipt

    def require_active(self, *, intent: OverlayOperationIntent) -> OverlayDaemonReceipt:
        """Return only a current active receipt; offline/revoked is never authorization."""

        receipt = self.status(intent=intent)
        if not receipt.usable:
            raise OverlayRejected("overlay_network_lease_not_usable")
        return receipt

    def publish_logical_service(
        self,
        *,
        intent: OverlayOperationIntent,
    ) -> OverlayLogicalServicePublication:
        """Publish only a logical Broker DTO after a fresh active status check."""

        command, receipt = self._status_command(intent=intent)
        if not receipt.usable:
            raise OverlayRejected("overlay_network_lease_not_usable")
        binding = command.binding
        publication = OverlayLogicalServicePublication(
            tenant_id=binding.tenant_id,
            workspace_id=binding.workspace_id,
            service_id=binding.service_id,
            publisher_node_id=binding.target_node_id,
            network_lease_id=binding.network_lease_id,
            logical_name=binding.service_logical_name,
            application_protocol=binding.service_protocol,
            transport_protocol=binding.service_transport_protocol,
            logical_port=binding.service_port,
            workspace_generation=binding.workspace_generation,
            service_version=binding.service_generation,
            publisher_node_fencing_token=binding.target_node_fencing_token,
            network_fencing_token=binding.network_fencing_token,
            binding_digest=binding.verification_digest,
            active_receipt_digest=receipt.receipt_digest,
            published_at=receipt.observed_at,
            expires_at=min(
                binding.expires_at,
                command.source_daemon.expires_at,
                command.target_daemon.expires_at,
            ),
            publication_digest="0" * 64,
        )
        return replace(publication, publication_digest=publication.expected_digest())


__all__ = ["HeadscaleOverlayAdapter"]
