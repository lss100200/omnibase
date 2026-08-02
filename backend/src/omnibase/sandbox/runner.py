"""Fail-closed P34.5A1-A2 Runner and termination contracts.

This module does not start processes or contact a container runtime.  It only
defines what a later, independently deployed Linux Runner must receive after
authorization and durable-operation reservation have succeeded.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import NoReturn, Protocol
from uuid import UUID

from omnibase.sandbox.contracts import (
    SandboxCommandSpec,
    SandboxOperationRequest,
    SandboxRuntimeHandle,
    SandboxRuntimeSpec,
    SandboxUnavailable,
    VerifiedSandboxAuthorization,
)
from omnibase.sandbox.control import VerifiedSandboxControlAuthorization
from omnibase.sandbox.operations import SandboxOperationIntent

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RunnerPlatform(StrEnum):
    LINUX = "linux"


@dataclass(frozen=True, slots=True)
class RunnerIsolationProfile:
    platform: RunnerPlatform
    cgroup_v2: bool
    user_namespace: bool
    pid_namespace: bool
    mount_namespace: bool
    network_namespace: bool
    seccomp_profile_digest: str
    lsm_profile_digest: str
    bounded_kill_seconds: int

    def __post_init__(self) -> None:
        if self.platform is not RunnerPlatform.LINUX:
            raise ValueError("sandbox runner must target Linux")
        controls = (
            self.cgroup_v2,
            self.user_namespace,
            self.pid_namespace,
            self.mount_namespace,
            self.network_namespace,
        )
        if any(value is not True for value in controls):
            raise ValueError("sandbox runner isolation controls cannot be disabled")
        if _SHA256_RE.fullmatch(self.seccomp_profile_digest) is None:
            raise ValueError("seccomp_profile_digest must be sha256")
        if _SHA256_RE.fullmatch(self.lsm_profile_digest) is None:
            raise ValueError("lsm_profile_digest must be sha256")
        if (
            isinstance(self.bounded_kill_seconds, bool)
            or not isinstance(self.bounded_kill_seconds, int)
            or self.bounded_kill_seconds < 1
            or self.bounded_kill_seconds > 30
        ):
            raise ValueError("bounded_kill_seconds is outside the safe range")

    def digest(self) -> str:
        payload = json.dumps(
            {
                "bounded_kill_seconds": self.bounded_kill_seconds,
                "cgroup_v2": self.cgroup_v2,
                "lsm_profile_digest": self.lsm_profile_digest,
                "mount_namespace": self.mount_namespace,
                "network_namespace": self.network_namespace,
                "pid_namespace": self.pid_namespace,
                "platform": self.platform.value,
                "seccomp_profile_digest": self.seccomp_profile_digest,
                "user_namespace": self.user_namespace,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class RunnerExecutionPlan:
    intent: SandboxOperationIntent
    request: SandboxOperationRequest
    authorization: VerifiedSandboxAuthorization
    runtime_handle: SandboxRuntimeHandle
    runtime_spec: SandboxRuntimeSpec
    command: SandboxCommandSpec
    isolation_profile: RunnerIsolationProfile

    def __post_init__(self) -> None:
        if self.intent.operation_id != self.request.operation_id:
            raise ValueError("runner operation binding mismatch")
        if self.intent.action != self.request.action.value:
            raise ValueError("runner action binding mismatch")
        if self.authorization.request != self.request:
            raise ValueError("runner authorization binding mismatch")
        if self.request.action.value != "sandbox.exec":
            raise ValueError("runner execution plan requires sandbox.exec")
        if self.intent.spec_digest is None:
            raise ValueError("runner execution plan requires a spec digest")


@dataclass(frozen=True, slots=True)
class RunnerTerminationPlan:
    intent: SandboxOperationIntent
    authorization: VerifiedSandboxControlAuthorization
    isolation_profile: RunnerIsolationProfile

    def __post_init__(self) -> None:
        if self.intent.operation_id != self.authorization.request.operation_id:
            raise ValueError("termination operation binding mismatch")
        if self.intent.action != self.authorization.request.action.value:
            raise ValueError("termination action binding mismatch")


@dataclass(frozen=True, slots=True)
class RunnerReceipt:
    operation_id: UUID
    evidence_digest: str
    reason_code: str
    binding_digest: str | None = None
    runner_id: UUID | None = None
    runtime_instance_id: UUID | None = None
    exit_code: int | None = None
    truncated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, UUID):
            raise TypeError("runner receipt operation_id must be UUID")
        if _SHA256_RE.fullmatch(self.evidence_digest) is None:
            raise ValueError("runner receipt evidence_digest must be sha256")
        if not self.reason_code.startswith("runner_") or len(self.reason_code) > 100:
            raise ValueError("runner receipt reason_code is invalid")
        binding_values = (self.binding_digest, self.runner_id, self.runtime_instance_id)
        if any(value is not None for value in binding_values) and (
            self.binding_digest is None
            or _SHA256_RE.fullmatch(self.binding_digest) is None
            or not isinstance(self.runner_id, UUID)
            or not isinstance(self.runtime_instance_id, UUID)
        ):
            raise ValueError("runner receipt binding is incomplete")
        if self.exit_code is not None and (
            isinstance(self.exit_code, bool)
            or not isinstance(self.exit_code, int)
            or self.exit_code < -255
            or self.exit_code > 255
        ):
            raise ValueError("runner receipt exit_code is invalid")
        if not isinstance(self.truncated, bool):
            raise TypeError("runner receipt truncated must be bool")

    def verify_bound_result(
        self,
        *,
        operation_id: UUID,
        binding_digest: str,
        runner_id: UUID,
        runtime_instance_id: UUID,
    ) -> None:
        if (
            self.operation_id != operation_id
            or self.binding_digest != binding_digest
            or self.runner_id != runner_id
            or self.runtime_instance_id != runtime_instance_id
        ):
            raise ValueError("runner receipt binding mismatch")


class SandboxRunner(Protocol):
    def execute(self, plan: RunnerExecutionPlan) -> RunnerReceipt: ...

    def terminate(self, plan: RunnerTerminationPlan) -> RunnerReceipt: ...


def _unavailable() -> NoReturn:
    raise SandboxUnavailable("sandbox_runner_unavailable")


class UnavailableSandboxRunner:
    """Production-safe default until the independent Linux Runner passes Gate."""

    def execute(self, plan: RunnerExecutionPlan) -> RunnerReceipt:
        del plan
        _unavailable()

    def terminate(self, plan: RunnerTerminationPlan) -> RunnerReceipt:
        del plan
        _unavailable()


__all__ = [
    "RunnerExecutionPlan",
    "RunnerIsolationProfile",
    "RunnerPlatform",
    "RunnerReceipt",
    "RunnerTerminationPlan",
    "SandboxRunner",
    "UnavailableSandboxRunner",
]
