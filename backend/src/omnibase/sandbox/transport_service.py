"""Authenticated local/dev transport and independent Runner protocol service."""

from __future__ import annotations

import threading

from omnibase.sandbox.host import VerifiedRunnerHost
from omnibase.sandbox.runner import RunnerExecutionPlan, RunnerReceipt, RunnerTerminationPlan
from omnibase.sandbox.runner_service import (
    AttestedLinuxSandboxRunner,
    HostBoundSandboxRunner,
)
from omnibase.sandbox.runtime_driver import (
    execution_binding_digest,
    termination_binding_digest,
)
from omnibase.sandbox.transport import RunnerOutcomeUnknown
from omnibase.sandbox.transport_auth import (
    HmacRunnerTransportAuthenticator,
    RejectingRunnerTransportAuthenticator,
    RunnerTransportAuthenticator,
    RunnerTransportEnvelope,
)


class AuthenticatedRunnerService:
    """Runner-side service: authenticate, reject replay, then invoke the driver."""

    def __init__(
        self,
        *,
        authenticator: RunnerTransportAuthenticator | None = None,
        runner: HostBoundSandboxRunner | None = None,
    ) -> None:
        self._authenticator = authenticator or RejectingRunnerTransportAuthenticator()
        self._runner = runner or AttestedLinuxSandboxRunner()

    def execute(
        self,
        *,
        envelope: RunnerTransportEnvelope,
        plan: RunnerExecutionPlan,
        host: VerifiedRunnerHost,
    ) -> RunnerReceipt:
        binding_digest = execution_binding_digest(plan, host)
        self._authenticator.verify(
            envelope,
            expected_runner_id=host.runner_id,
            expected_node_id=host.node_id,
            expected_operation_id=plan.request.operation_id,
            expected_action=plan.request.action.value,
            expected_payload_digest=binding_digest,
            expected_host_evidence_digest=host.evidence_digest,
            expected_runner_identity_thumbprint=host.runner_identity_thumbprint,
        )
        receipt = self._runner.execute(plan=plan, host=host)
        try:
            receipt.verify_bound_result(
                operation_id=plan.request.operation_id,
                binding_digest=binding_digest,
                runner_id=host.runner_id,
                runtime_instance_id=plan.request.runtime_instance_id,
            )
        except ValueError as exc:
            raise RunnerOutcomeUnknown("sandbox_runner_receipt_binding_rejected") from exc
        return receipt

    def terminate(
        self,
        *,
        envelope: RunnerTransportEnvelope,
        plan: RunnerTerminationPlan,
        host: VerifiedRunnerHost,
    ) -> RunnerReceipt:
        request = plan.authorization.request
        binding_digest = termination_binding_digest(plan, host)
        self._authenticator.verify(
            envelope,
            expected_runner_id=host.runner_id,
            expected_node_id=host.node_id,
            expected_operation_id=request.operation_id,
            expected_action=request.action.value,
            expected_payload_digest=binding_digest,
            expected_host_evidence_digest=host.evidence_digest,
            expected_runner_identity_thumbprint=host.runner_identity_thumbprint,
        )
        receipt = self._runner.terminate(plan=plan, host=host)
        try:
            receipt.verify_bound_result(
                operation_id=request.operation_id,
                binding_digest=binding_digest,
                runner_id=host.runner_id,
                runtime_instance_id=request.runtime_instance_id,
            )
        except ValueError as exc:
            raise RunnerOutcomeUnknown("sandbox_runner_receipt_binding_rejected") from exc
        return receipt


class AuthenticatedLocalRunnerTransport:
    """In-process local/dev adapter that exercises the full signed protocol."""

    def __init__(
        self,
        *,
        signer: HmacRunnerTransportAuthenticator,
        service: AuthenticatedRunnerService,
    ) -> None:
        self._signer = signer
        self._service = service
        self._lock = threading.Lock()
        self._sequence = 0

    def _next_sequence(self) -> int:
        with self._lock:
            self._sequence += 1
            return self._sequence

    def execute(
        self,
        *,
        plan: RunnerExecutionPlan,
        host: VerifiedRunnerHost,
    ) -> RunnerReceipt:
        binding_digest = execution_binding_digest(plan, host)
        envelope = self._signer.sign(
            runner_id=host.runner_id,
            node_id=host.node_id,
            operation_id=plan.request.operation_id,
            action=plan.request.action.value,
            payload_digest=binding_digest,
            host_evidence_digest=host.evidence_digest,
            sequence=self._next_sequence(),
        )
        return self._service.execute(envelope=envelope, plan=plan, host=host)

    def terminate(
        self,
        *,
        plan: RunnerTerminationPlan,
        host: VerifiedRunnerHost,
    ) -> RunnerReceipt:
        request = plan.authorization.request
        binding_digest = termination_binding_digest(plan, host)
        envelope = self._signer.sign(
            runner_id=host.runner_id,
            node_id=host.node_id,
            operation_id=request.operation_id,
            action=request.action.value,
            payload_digest=binding_digest,
            host_evidence_digest=host.evidence_digest,
            sequence=self._next_sequence(),
        )
        return self._service.terminate(envelope=envelope, plan=plan, host=host)


__all__ = [
    "AuthenticatedLocalRunnerTransport",
    "AuthenticatedRunnerService",
]
