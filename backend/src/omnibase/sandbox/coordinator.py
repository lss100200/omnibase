"""Fail-closed execution dispatch coordinator for P34.5A2.

This component orders durable reservation, live workload authorization, Runner
host attestation and independent transport dispatch.  It deliberately contains
no process, container, filesystem, socket or HTTP implementation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from omnibase.sandbox.contracts import (
    RejectingSandboxAuthorizer,
    SandboxAuthorizer,
    SandboxCommandSpec,
    SandboxConflict,
    SandboxError,
    SandboxOperationRequest,
    SandboxRejected,
    SandboxRuntimeHandle,
    SandboxRuntimeSpec,
    utc_now,
)
from omnibase.sandbox.host import (
    RejectingRunnerHostAttestor,
    RunnerHostAttestor,
    VerifiedRunnerHost,
)
from omnibase.sandbox.operations import (
    SandboxOperationIntent,
    SandboxOperationRecord,
    SandboxOperationState,
    SandboxOperationStore,
    UnavailableSandboxOperationStore,
)
from omnibase.sandbox.runner import (
    RunnerExecutionPlan,
    RunnerIsolationProfile,
    RunnerReceipt,
)
from omnibase.sandbox.transport import (
    RunnerOutcomeUnknown,
    RunnerTransport,
    UnavailableRunnerTransport,
)

_TERMINAL = frozenset(
    {
        SandboxOperationState.SUCCEEDED,
        SandboxOperationState.FAILED,
        SandboxOperationState.RECONCILED_SUCCEEDED,
        SandboxOperationState.RECONCILED_FAILED,
    }
)


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _authorization_evidence(
    *,
    authorization_digest: str,
    host: VerifiedRunnerHost,
) -> str:
    return _digest(
        {
            "authorization": authorization_digest,
            "host_evidence": host.evidence_digest,
            "isolation_profile": host.isolation_profile_digest,
            "node_fencing_token": host.node_fencing_token,
            "runner_id": str(host.runner_id),
            "runner_identity": host.runner_identity_thumbprint,
        }
    )


@dataclass(frozen=True, slots=True)
class SandboxDispatchResult:
    record: SandboxOperationRecord
    receipt: RunnerReceipt | None
    replayed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.record, SandboxOperationRecord):
            raise TypeError("record must be SandboxOperationRecord")
        if (
            self.receipt is not None
            and self.receipt.operation_id != self.record.intent.operation_id
        ):
            raise ValueError("receipt operation binding mismatch")
        if self.replayed and self.receipt is not None:
            raise ValueError("replayed result cannot invent a Runner receipt")


class SandboxExecutionCoordinator:
    """Order security checks and prohibit replay after uncertain dispatch."""

    def __init__(
        self,
        *,
        operation_store: SandboxOperationStore | None = None,
        authorizer: SandboxAuthorizer | None = None,
        host_attestor: RunnerHostAttestor | None = None,
        transport: RunnerTransport | None = None,
        clock=utc_now,
    ) -> None:
        self._operation_store = operation_store or UnavailableSandboxOperationStore()
        self._authorizer = authorizer or RejectingSandboxAuthorizer()
        self._host_attestor = host_attestor or RejectingRunnerHostAttestor()
        self._transport = transport or UnavailableRunnerTransport()
        self._clock = clock

    def execute(
        self,
        *,
        intent: SandboxOperationIntent,
        request: SandboxOperationRequest,
        runtime_handle: SandboxRuntimeHandle,
        runtime_spec: SandboxRuntimeSpec,
        command: SandboxCommandSpec,
        isolation_profile: RunnerIsolationProfile,
    ) -> SandboxDispatchResult:
        if intent.operation_id != request.operation_id:
            raise ValueError("dispatch operation binding mismatch")
        if intent.action != request.action.value or request.action.value != "sandbox.exec":
            raise ValueError("dispatch action binding mismatch")
        if intent.spec_digest is None:
            raise ValueError("dispatch requires a spec digest")
        record = self._operation_store.begin(intent)
        replay = self._replay_or_reject(record)
        if replay is not None:
            return replay
        host, evidence_digest, plan = self._build_plan(
            record=record,
            intent=intent,
            request=request,
            runtime_handle=runtime_handle,
            runtime_spec=runtime_spec,
            command=command,
            isolation_profile=isolation_profile,
        )
        self._record_authorization(record, evidence_digest=evidence_digest)
        return self._dispatch(plan=plan, host=host)

    def _replay_or_reject(
        self,
        record: SandboxOperationRecord,
    ) -> SandboxDispatchResult | None:
        if record.state in _TERMINAL:
            return SandboxDispatchResult(record=record, receipt=None, replayed=True)
        if record.state in {
            SandboxOperationState.AMBIGUOUS,
            SandboxOperationState.RECONCILIATION_REQUIRED,
        }:
            raise SandboxConflict("sandbox_operation_reconciliation_required")
        if record.state is SandboxOperationState.DISPATCHING:
            self._operation_store.mark_ambiguous(record.intent.operation_id)
            raise SandboxConflict("sandbox_operation_reconciliation_required")
        if record.state not in {
            SandboxOperationState.ACCEPTED,
            SandboxOperationState.AUTHORIZED,
        }:
            raise SandboxConflict("sandbox_operation_not_dispatchable")
        return None

    def _build_plan(
        self,
        *,
        record: SandboxOperationRecord,
        intent: SandboxOperationIntent,
        request: SandboxOperationRequest,
        runtime_handle: SandboxRuntimeHandle,
        runtime_spec: SandboxRuntimeSpec,
        command: SandboxCommandSpec,
        isolation_profile: RunnerIsolationProfile,
    ) -> tuple[VerifiedRunnerHost, str, RunnerExecutionPlan]:
        try:
            authorization = self._authorizer.authorize(request)
            host = self._host_attestor.attest(
                request=request,
                isolation_profile=isolation_profile,
            )
            now = self._clock()
            host.verify_binding(
                request=request,
                isolation_profile=isolation_profile,
                now=now,
            )
            evidence_digest = _authorization_evidence(
                authorization_digest=authorization.verification_digest,
                host=host,
            )
            plan = RunnerExecutionPlan(
                intent=intent,
                request=request,
                authorization=authorization,
                runtime_handle=runtime_handle,
                runtime_spec=runtime_spec,
                command=command,
                isolation_profile=isolation_profile,
            )
        except SandboxError:
            self._operation_store.fail(
                intent.operation_id,
                reason_code="dispatch_authorization_rejected",
            )
            raise
        return host, evidence_digest, plan

    def _record_authorization(
        self,
        record: SandboxOperationRecord,
        *,
        evidence_digest: str,
    ) -> None:
        if record.state is SandboxOperationState.ACCEPTED:
            self._operation_store.authorize(
                record.intent.operation_id,
                evidence_digest=evidence_digest,
            )
        elif record.transitions[-1].evidence_digest != evidence_digest:
            self._operation_store.fail(
                record.intent.operation_id,
                reason_code="dispatch_authorization_drift",
            )
            raise SandboxRejected("sandbox_dispatch_authorization_drift")

    def _dispatch(
        self,
        *,
        plan: RunnerExecutionPlan,
        host: VerifiedRunnerHost,
    ) -> SandboxDispatchResult:
        operation_id = plan.intent.operation_id
        self._operation_store.claim_dispatch(operation_id)
        try:
            receipt = self._transport.execute(plan=plan, host=host)
        except RunnerOutcomeUnknown:
            self._operation_store.mark_ambiguous(operation_id)
            raise
        except Exception as exc:
            self._operation_store.mark_ambiguous(operation_id)
            raise RunnerOutcomeUnknown("sandbox_runner_outcome_unknown") from exc

        if receipt.operation_id != operation_id:
            self._operation_store.mark_ambiguous(operation_id)
            raise RunnerOutcomeUnknown("sandbox_runner_receipt_binding_rejected")
        succeeded = self._operation_store.succeed(
            operation_id,
            evidence_digest=receipt.evidence_digest,
        )
        return SandboxDispatchResult(record=succeeded, receipt=receipt, replayed=False)


__all__ = [
    "SandboxDispatchResult",
    "SandboxExecutionCoordinator",
]
