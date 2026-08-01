"""Independent, fail-closed emergency control authorization for P34.5."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from omnibase.sandbox.contracts import (
    SandboxRejected,
    SandboxRuntimeHandle,
    SandboxUnavailable,
    utc_now,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{2,99}$")


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _aware(value: datetime, *, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


class SandboxControlAction(StrEnum):
    EMERGENCY_STOP = "sandbox.control.emergency_stop"
    EMERGENCY_DESTROY = "sandbox.control.emergency_destroy"


@dataclass(frozen=True, slots=True)
class SandboxControlRequest:
    """Trusted-controller request; never accepted from a workload capability."""

    operation_id: UUID
    action: SandboxControlAction
    controller_id: UUID
    controller_identity_thumbprint: str
    tenant_id: UUID
    workspace_id: UUID
    run_id: UUID
    node_id: UUID
    runtime_handle: SandboxRuntimeHandle
    workspace_generation: int
    run_fencing_token: int
    node_fencing_token: int
    reason_code: str
    deadline_at: datetime

    def __post_init__(self) -> None:
        identifiers = (
            self.operation_id,
            self.controller_id,
            self.tenant_id,
            self.workspace_id,
            self.run_id,
            self.node_id,
        )
        if any(not isinstance(value, UUID) for value in identifiers):
            raise TypeError("sandbox control identifiers must be UUID values")
        if not isinstance(self.action, SandboxControlAction):
            raise TypeError("action must be SandboxControlAction")
        if not isinstance(self.runtime_handle, SandboxRuntimeHandle):
            raise TypeError("runtime_handle must be SandboxRuntimeHandle")
        if _SHA256_RE.fullmatch(self.controller_identity_thumbprint) is None:
            raise ValueError("controller identity thumbprint must be sha256")
        for name, value in (
            ("workspace_generation", self.workspace_generation),
            ("run_fencing_token", self.run_fencing_token),
            ("node_fencing_token", self.node_fencing_token),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if _REASON_RE.fullmatch(self.reason_code) is None:
            raise ValueError("reason_code is invalid")
        _aware(self.deadline_at, name="deadline_at")


@dataclass(frozen=True, slots=True)
class VerifiedSandboxControlAuthorization:
    request: SandboxControlRequest
    verified_at: datetime
    expires_at: datetime
    verification_digest: str

    def __post_init__(self) -> None:
        _aware(self.verified_at, name="verified_at")
        _aware(self.expires_at, name="expires_at")
        if self.expires_at <= self.verified_at:
            raise ValueError("control authorization is already expired")
        if _SHA256_RE.fullmatch(self.verification_digest) is None:
            raise ValueError("verification_digest must be sha256")


class SandboxControlAuthorizer(Protocol):
    def authorize(self, request: SandboxControlRequest) -> VerifiedSandboxControlAuthorization: ...


class RejectingSandboxControlAuthorizer:
    def authorize(self, request: SandboxControlRequest) -> VerifiedSandboxControlAuthorization:
        del request
        raise SandboxUnavailable("sandbox_control_authorizer_unavailable")


@dataclass(frozen=True, slots=True)
class _ControlRecord:
    request: SandboxControlRequest
    expires_at: datetime
    revoked: bool = False


class InMemorySandboxControlAuthorizer:
    """Test-only server-owned controller ledger; never production wiring."""

    def __init__(self, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._clock = clock
        self._records: dict[tuple[UUID, UUID, SandboxControlAction], _ControlRecord] = {}

    def install(self, *, request: SandboxControlRequest, expires_at: datetime) -> None:
        now = self._clock()
        _aware(now, name="clock")
        _aware(expires_at, name="expires_at")
        if expires_at <= now or expires_at > request.deadline_at:
            raise ValueError("control expiry must be future and within deadline")
        key = (request.controller_id, request.runtime_handle.value, request.action)
        self._records[key] = _ControlRecord(
            request=request,
            expires_at=expires_at,
        )

    def revoke(self, controller_id: UUID) -> None:
        matching = [key for key in self._records if key[0] == controller_id]
        for key in matching:
            record = self._records[key]
            self._records[key] = _ControlRecord(
                request=record.request,
                expires_at=record.expires_at,
                revoked=True,
            )

    def authorize(self, request: SandboxControlRequest) -> VerifiedSandboxControlAuthorization:
        key = (request.controller_id, request.runtime_handle.value, request.action)
        record = self._records.get(key)
        now = self._clock()
        if (
            record is None
            or record.revoked
            or record.expires_at <= now
            or request.deadline_at <= now
        ):
            raise SandboxRejected("sandbox_control_authorization_rejected")
        expected = record.request
        expected_binding = (
            expected.operation_id,
            expected.action,
            expected.controller_id,
            expected.controller_identity_thumbprint,
            expected.tenant_id,
            expected.workspace_id,
            expected.run_id,
            expected.node_id,
            expected.runtime_handle,
            expected.workspace_generation,
            expected.run_fencing_token,
            expected.node_fencing_token,
            expected.reason_code,
            expected.deadline_at,
        )
        supplied_binding = (
            request.operation_id,
            request.action,
            request.controller_id,
            request.controller_identity_thumbprint,
            request.tenant_id,
            request.workspace_id,
            request.run_id,
            request.node_id,
            request.runtime_handle,
            request.workspace_generation,
            request.run_fencing_token,
            request.node_fencing_token,
            request.reason_code,
            request.deadline_at,
        )
        if expected_binding != supplied_binding:
            raise SandboxRejected("sandbox_control_authorization_rejected")
        digest = _digest(
            {
                "action": request.action.value,
                "controller_id": str(request.controller_id),
                "node_fencing_token": request.node_fencing_token,
                "operation_id": str(request.operation_id),
                "run_fencing_token": request.run_fencing_token,
                "runtime_handle": str(request.runtime_handle.value),
                "workspace_generation": request.workspace_generation,
            }
        )
        return VerifiedSandboxControlAuthorization(
            request=request,
            verified_at=now,
            expires_at=min(record.expires_at, request.deadline_at),
            verification_digest=digest,
        )


__all__ = [
    "InMemorySandboxControlAuthorizer",
    "RejectingSandboxControlAuthorizer",
    "SandboxControlAction",
    "SandboxControlAuthorizer",
    "SandboxControlRequest",
    "VerifiedSandboxControlAuthorization",
]
