"""Append-only in-memory model for durable sandbox operation semantics."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from itertools import pairwise
from typing import NoReturn, Protocol
from uuid import UUID

from omnibase.sandbox.contracts import SandboxConflict, SandboxUnavailable, utc_now

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{2,99}$")
_WORKLOAD_ACTIONS = frozenset(
    {
        "sandbox.prepare",
        "sandbox.create",
        "sandbox.start",
        "sandbox.exec",
        "sandbox.cancel",
        "sandbox.logs",
        "sandbox.stats",
        "sandbox.snapshot",
        "sandbox.restore",
        "sandbox.stop",
        "sandbox.destroy",
    }
)
_CONTROL_ACTIONS = frozenset(
    {
        "sandbox.control.emergency_stop",
        "sandbox.control.emergency_destroy",
    }
)


class SandboxOperationState(StrEnum):
    ACCEPTED = "accepted"
    AUTHORIZED = "authorized"
    DISPATCHING = "dispatching"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    RECONCILED_SUCCEEDED = "reconciled_succeeded"
    RECONCILED_FAILED = "reconciled_failed"


_TERMINAL = frozenset(
    {
        SandboxOperationState.SUCCEEDED,
        SandboxOperationState.FAILED,
        SandboxOperationState.RECONCILED_SUCCEEDED,
        SandboxOperationState.RECONCILED_FAILED,
    }
)
_TRANSITIONS = {
    SandboxOperationState.ACCEPTED: frozenset(
        {SandboxOperationState.AUTHORIZED, SandboxOperationState.FAILED}
    ),
    SandboxOperationState.AUTHORIZED: frozenset(
        {SandboxOperationState.DISPATCHING, SandboxOperationState.FAILED}
    ),
    SandboxOperationState.DISPATCHING: frozenset(
        {
            SandboxOperationState.SUCCEEDED,
            SandboxOperationState.FAILED,
            SandboxOperationState.AMBIGUOUS,
        }
    ),
    SandboxOperationState.AMBIGUOUS: frozenset({SandboxOperationState.RECONCILIATION_REQUIRED}),
    SandboxOperationState.RECONCILIATION_REQUIRED: frozenset(
        {
            SandboxOperationState.RECONCILED_SUCCEEDED,
            SandboxOperationState.RECONCILED_FAILED,
        }
    ),
}


def transition_allowed(
    current: SandboxOperationState,
    target: SandboxOperationState,
) -> bool:
    return target in _TRANSITIONS.get(current, frozenset())


@dataclass(frozen=True, slots=True)
class SandboxOperationIntent:
    operation_id: UUID
    tenant_id: UUID
    workspace_id: UUID
    run_id: UUID
    runtime_instance_id: UUID
    capability_grant_id: UUID | None
    workspace_generation: int
    run_fencing_token: int
    node_fencing_token: int
    action: str
    request_digest: str
    spec_digest: str | None = None

    def __post_init__(self) -> None:
        identifiers = (
            self.operation_id,
            self.tenant_id,
            self.workspace_id,
            self.run_id,
            self.runtime_instance_id,
        )
        if any(not isinstance(value, UUID) for value in identifiers):
            raise TypeError("operation intent identifiers must be UUID values")
        if self.capability_grant_id is not None and not isinstance(self.capability_grant_id, UUID):
            raise TypeError("capability_grant_id must be UUID or None")
        for name, value in (
            ("workspace_generation", self.workspace_generation),
            ("run_fencing_token", self.run_fencing_token),
            ("node_fencing_token", self.node_fencing_token),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.action not in _WORKLOAD_ACTIONS | _CONTROL_ACTIONS:
            raise ValueError("operation action is invalid")
        if (self.action in _CONTROL_ACTIONS) != (self.capability_grant_id is None):
            raise ValueError("operation capability binding is invalid")
        if _SHA256_RE.fullmatch(self.request_digest) is None:
            raise ValueError("request_digest must be sha256")
        if self.spec_digest is not None and _SHA256_RE.fullmatch(self.spec_digest) is None:
            raise ValueError("spec_digest must be sha256")


@dataclass(frozen=True, slots=True)
class SandboxOperationTransition:
    sequence: int
    state: SandboxOperationState
    recorded_at: datetime
    reason_code: str
    evidence_digest: str | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
        ):
            raise ValueError("operation transition sequence must be positive")
        if not isinstance(self.state, SandboxOperationState):
            raise TypeError("operation transition state is invalid")
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() is None:
            raise ValueError("operation transition time must be timezone-aware")
        if _CODE_RE.fullmatch(self.reason_code) is None:
            raise ValueError("operation reason_code is invalid")
        if self.evidence_digest is not None and _SHA256_RE.fullmatch(self.evidence_digest) is None:
            raise ValueError("operation evidence_digest must be sha256")


@dataclass(frozen=True, slots=True)
class SandboxOperationRecord:
    intent: SandboxOperationIntent
    transitions: tuple[SandboxOperationTransition, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.intent, SandboxOperationIntent):
            raise TypeError("operation intent is invalid")
        if not isinstance(self.transitions, tuple) or not self.transitions:
            raise ValueError("operation transitions must be a non-empty tuple")
        if any(not isinstance(item, SandboxOperationTransition) for item in self.transitions):
            raise TypeError("operation transition is invalid")
        if [item.sequence for item in self.transitions] != list(
            range(1, len(self.transitions) + 1)
        ):
            raise ValueError("operation transition sequence is invalid")
        if self.transitions[0].state is not SandboxOperationState.ACCEPTED:
            raise ValueError("operation must start in accepted state")
        for previous, current in pairwise(self.transitions):
            if not transition_allowed(previous.state, current.state):
                raise ValueError("operation transition history is invalid")

    @property
    def state(self) -> SandboxOperationState:
        return self.transitions[-1].state


class SandboxOperationStore(Protocol):
    """Durable operation seam required before any Runner dispatch."""

    def begin(self, intent: SandboxOperationIntent) -> SandboxOperationRecord: ...

    def authorize(
        self,
        operation_id: UUID,
        *,
        evidence_digest: str,
    ) -> SandboxOperationRecord: ...

    def claim_dispatch(self, operation_id: UUID) -> SandboxOperationRecord: ...

    def succeed(
        self,
        operation_id: UUID,
        *,
        evidence_digest: str,
    ) -> SandboxOperationRecord: ...

    def fail(self, operation_id: UUID, *, reason_code: str) -> SandboxOperationRecord: ...

    def mark_ambiguous(self, operation_id: UUID) -> SandboxOperationRecord: ...

    def require_reconciliation(self, operation_id: UUID) -> SandboxOperationRecord: ...

    def reconcile(
        self,
        operation_id: UUID,
        *,
        succeeded: bool,
        evidence_digest: str,
    ) -> SandboxOperationRecord: ...

    def get(self, operation_id: UUID) -> SandboxOperationRecord: ...


def _store_unavailable() -> NoReturn:
    raise SandboxUnavailable("sandbox_operation_store_unavailable")


class UnavailableSandboxOperationStore:
    """Production-safe default until append-only durable storage is installed."""

    def begin(self, intent: SandboxOperationIntent) -> SandboxOperationRecord:
        del intent
        _store_unavailable()

    def authorize(
        self,
        operation_id: UUID,
        *,
        evidence_digest: str,
    ) -> SandboxOperationRecord:
        del operation_id, evidence_digest
        _store_unavailable()

    def claim_dispatch(self, operation_id: UUID) -> SandboxOperationRecord:
        del operation_id
        _store_unavailable()

    def succeed(
        self,
        operation_id: UUID,
        *,
        evidence_digest: str,
    ) -> SandboxOperationRecord:
        del operation_id, evidence_digest
        _store_unavailable()

    def fail(self, operation_id: UUID, *, reason_code: str) -> SandboxOperationRecord:
        del operation_id, reason_code
        _store_unavailable()

    def mark_ambiguous(self, operation_id: UUID) -> SandboxOperationRecord:
        del operation_id
        _store_unavailable()

    def require_reconciliation(self, operation_id: UUID) -> SandboxOperationRecord:
        del operation_id
        _store_unavailable()

    def reconcile(
        self,
        operation_id: UUID,
        *,
        succeeded: bool,
        evidence_digest: str,
    ) -> SandboxOperationRecord:
        del operation_id, succeeded, evidence_digest
        _store_unavailable()

    def get(self, operation_id: UUID) -> SandboxOperationRecord:
        del operation_id
        _store_unavailable()


class InMemorySandboxOperationStore:
    """Testable durable-operation contract; production storage is still pending."""

    def __init__(self, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._clock = clock
        self._records: dict[UUID, SandboxOperationRecord] = {}

    def begin(self, intent: SandboxOperationIntent) -> SandboxOperationRecord:
        existing = self._records.get(intent.operation_id)
        if existing is not None:
            if existing.intent != intent:
                raise SandboxConflict("sandbox_operation_payload_drift")
            return existing
        transition = self._transition_value(
            sequence=1,
            state=SandboxOperationState.ACCEPTED,
            reason_code="operation_accepted",
            evidence_digest=None,
        )
        record = SandboxOperationRecord(intent=intent, transitions=(transition,))
        self._records[intent.operation_id] = record
        return record

    def authorize(self, operation_id: UUID, *, evidence_digest: str) -> SandboxOperationRecord:
        return self._append(
            operation_id,
            SandboxOperationState.AUTHORIZED,
            reason_code="operation_authorized",
            evidence_digest=evidence_digest,
        )

    def claim_dispatch(self, operation_id: UUID) -> SandboxOperationRecord:
        return self._append(
            operation_id,
            SandboxOperationState.DISPATCHING,
            reason_code="provider_dispatch_started",
        )

    def succeed(self, operation_id: UUID, *, evidence_digest: str) -> SandboxOperationRecord:
        return self._append(
            operation_id,
            SandboxOperationState.SUCCEEDED,
            reason_code="provider_succeeded",
            evidence_digest=evidence_digest,
        )

    def fail(self, operation_id: UUID, *, reason_code: str) -> SandboxOperationRecord:
        return self._append(operation_id, SandboxOperationState.FAILED, reason_code=reason_code)

    def mark_ambiguous(self, operation_id: UUID) -> SandboxOperationRecord:
        return self._append(
            operation_id,
            SandboxOperationState.AMBIGUOUS,
            reason_code="provider_outcome_ambiguous",
        )

    def require_reconciliation(self, operation_id: UUID) -> SandboxOperationRecord:
        return self._append(
            operation_id,
            SandboxOperationState.RECONCILIATION_REQUIRED,
            reason_code="provider_reconciliation_required",
        )

    def reconcile(
        self,
        operation_id: UUID,
        *,
        succeeded: bool,
        evidence_digest: str,
    ) -> SandboxOperationRecord:
        return self._append(
            operation_id,
            (
                SandboxOperationState.RECONCILED_SUCCEEDED
                if succeeded
                else SandboxOperationState.RECONCILED_FAILED
            ),
            reason_code=(
                "provider_reconciled_succeeded" if succeeded else "provider_reconciled_failed"
            ),
            evidence_digest=evidence_digest,
        )

    def get(self, operation_id: UUID) -> SandboxOperationRecord:
        try:
            return self._records[operation_id]
        except KeyError as exc:
            raise SandboxConflict("sandbox_operation_not_found") from exc

    def _append(
        self,
        operation_id: UUID,
        state: SandboxOperationState,
        *,
        reason_code: str,
        evidence_digest: str | None = None,
    ) -> SandboxOperationRecord:
        record = self.get(operation_id)
        if record.state in _TERMINAL:
            raise SandboxConflict("sandbox_operation_terminal")
        if not transition_allowed(record.state, state):
            raise SandboxConflict("sandbox_operation_transition_rejected")
        transition = self._transition_value(
            sequence=len(record.transitions) + 1,
            state=state,
            reason_code=reason_code,
            evidence_digest=evidence_digest,
        )
        updated = SandboxOperationRecord(
            intent=record.intent,
            transitions=(*record.transitions, transition),
        )
        self._records[operation_id] = updated
        return updated

    def _transition_value(
        self,
        *,
        sequence: int,
        state: SandboxOperationState,
        reason_code: str,
        evidence_digest: str | None,
    ) -> SandboxOperationTransition:
        recorded_at = self._clock()
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise ValueError("operation clock must be timezone-aware")
        return SandboxOperationTransition(
            sequence=sequence,
            state=state,
            recorded_at=recorded_at,
            reason_code=reason_code,
            evidence_digest=evidence_digest,
        )


__all__ = [
    "InMemorySandboxOperationStore",
    "SandboxOperationIntent",
    "SandboxOperationRecord",
    "SandboxOperationState",
    "SandboxOperationStore",
    "SandboxOperationTransition",
    "UnavailableSandboxOperationStore",
    "transition_allowed",
]
