"""Fail-closed P34.5A sandbox contracts.

This module deliberately contains no runtime integration.  It defines the
strict, server-owned objects that a future Linux runner/provider must accept.
Public browser DTOs, raw capability tokens, host paths, environment mappings,
shell command strings and provider credentials do not belong in this layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Protocol
from uuid import UUID

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{2,99}$")
_MAX_ARG_COUNT = 128
_MAX_ARG_BYTES = 4096
_RESERVED_PATH_PARTS = frozenset(
    {
        ".aws",
        ".env",
        ".kube",
        ".ssh",
        "docker.sock",
        "podman.sock",
    }
)


class SandboxError(RuntimeError):
    """Base class for stable, code-only sandbox failures."""


class SandboxUnavailable(SandboxError):
    """A required P34.5 runtime component is not installed."""


class SandboxRejected(SandboxError):
    """A request failed a sandbox security contract."""


class SandboxConflict(SandboxRejected):
    """A request conflicts with the current runtime state or fencing."""


class SandboxExecutionDisabled(SandboxRejected):
    """Untrusted code execution remains intentionally unavailable."""


class SandboxAction(StrEnum):
    PREPARE = "sandbox.prepare"
    CREATE = "sandbox.create"
    START = "sandbox.start"
    EXEC = "sandbox.exec"
    CANCEL = "sandbox.cancel"
    LOGS = "sandbox.logs"
    STATS = "sandbox.stats"
    SNAPSHOT = "sandbox.snapshot"
    RESTORE = "sandbox.restore"
    STOP = "sandbox.stop"
    DESTROY = "sandbox.destroy"


class SandboxRuntimeState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    STOPPED = "stopped"
    DESTROYED = "destroyed"


class SandboxNetworkMode(StrEnum):
    DENY_ALL = "deny_all"


def _require_strict_int(value: int, *, name: str, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} is outside the safe range")


def _require_sha256(value: str, *, name: str) -> None:
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase sha256 digest")


def _require_aware_utc(value: datetime, *, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class SandboxRelativePath:
    """A canonical workspace-relative POSIX path.

    The contract intentionally rejects Windows separators, drive paths,
    traversal, reserved credential directories and runtime control sockets.
    A real provider must additionally resolve paths beneath its private runtime
    root and re-check the result after following no links.
    """

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError("sandbox path must be a string")
        if not self.value or len(self.value.encode("utf-8")) > 1024:
            raise ValueError("sandbox path length is outside the safe range")
        if (
            "\x00" in self.value
            or "\\" in self.value
            or self.value.startswith("/")
            or ":" in self.value
        ):
            raise ValueError("sandbox path must be a relative POSIX path")
        path = PurePosixPath(self.value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("sandbox path is not canonical")
        if any(part.casefold() in _RESERVED_PATH_PARTS for part in path.parts):
            raise ValueError("sandbox path targets a reserved location")
        if path.as_posix() != self.value:
            raise ValueError("sandbox path is not canonical")


@dataclass(frozen=True, slots=True)
class SandboxResourceLimits:
    """Closed resource budget passed to a future provider."""

    cpu_millis: int
    memory_bytes: int
    pids: int
    writable_bytes: int
    inodes: int
    wall_time_seconds: int
    output_bytes: int

    def __post_init__(self) -> None:
        _require_strict_int(self.cpu_millis, name="cpu_millis", minimum=10, maximum=8_000)
        _require_strict_int(
            self.memory_bytes,
            name="memory_bytes",
            minimum=16 * 1024 * 1024,
            maximum=32 * 1024 * 1024 * 1024,
        )
        _require_strict_int(self.pids, name="pids", minimum=1, maximum=1_024)
        _require_strict_int(
            self.writable_bytes,
            name="writable_bytes",
            minimum=1024 * 1024,
            maximum=128 * 1024 * 1024 * 1024,
        )
        _require_strict_int(self.inodes, name="inodes", minimum=1, maximum=2_000_000)
        _require_strict_int(
            self.wall_time_seconds,
            name="wall_time_seconds",
            minimum=1,
            maximum=86_400,
        )
        _require_strict_int(
            self.output_bytes,
            name="output_bytes",
            minimum=0,
            maximum=64 * 1024 * 1024,
        )


@dataclass(frozen=True, slots=True)
class SandboxNetworkPolicy:
    """P34.5A only permits a fully disconnected runtime."""

    mode: SandboxNetworkMode = SandboxNetworkMode.DENY_ALL
    allowed_service_ids: tuple[UUID, ...] = ()
    direct_overlay: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.allowed_service_ids, tuple) or not all(
            isinstance(item, UUID) for item in self.allowed_service_ids
        ):
            raise TypeError("allowed_service_ids must be an immutable UUID tuple")
        if self.mode is not SandboxNetworkMode.DENY_ALL:
            raise ValueError("sandbox network must remain deny_all")
        if self.allowed_service_ids:
            raise ValueError("P34.5A cannot allow network services")
        if self.direct_overlay:
            raise ValueError("sandbox cannot join the member overlay")


@dataclass(frozen=True, slots=True)
class SandboxIsolationPolicy:
    """Security properties every later runtime adapter must preserve."""

    run_as_uid: int
    run_as_gid: int
    read_only_root: bool = True
    no_new_privileges: bool = True
    drop_all_capabilities: bool = True
    allow_host_mounts: bool = False
    allow_runtime_socket: bool = False
    allow_devices: bool = False

    def __post_init__(self) -> None:
        _require_strict_int(self.run_as_uid, name="run_as_uid", minimum=10_000, maximum=2**31 - 1)
        _require_strict_int(self.run_as_gid, name="run_as_gid", minimum=10_000, maximum=2**31 - 1)
        required_true = {
            "read_only_root": self.read_only_root,
            "no_new_privileges": self.no_new_privileges,
            "drop_all_capabilities": self.drop_all_capabilities,
        }
        if any(value is not True for value in required_true.values()):
            raise ValueError("sandbox isolation controls cannot be disabled")
        prohibited = {
            "allow_host_mounts": self.allow_host_mounts,
            "allow_runtime_socket": self.allow_runtime_socket,
            "allow_devices": self.allow_devices,
        }
        if any(value is not False for value in prohibited.values()):
            raise ValueError("sandbox host capabilities are prohibited")


@dataclass(frozen=True, slots=True)
class SandboxRuntimeSpec:
    template_digest: str
    policy_digest: str
    limits: SandboxResourceLimits
    network: SandboxNetworkPolicy
    isolation: SandboxIsolationPolicy

    def __post_init__(self) -> None:
        if not isinstance(self.limits, SandboxResourceLimits):
            raise TypeError("limits must be SandboxResourceLimits")
        if not isinstance(self.network, SandboxNetworkPolicy):
            raise TypeError("network must be SandboxNetworkPolicy")
        if not isinstance(self.isolation, SandboxIsolationPolicy):
            raise TypeError("isolation must be SandboxIsolationPolicy")
        _require_sha256(self.template_digest, name="template_digest")
        _require_sha256(self.policy_digest, name="policy_digest")


@dataclass(frozen=True, slots=True)
class SandboxCommandSpec:
    """Structured command request with no shell, environment or host path."""

    argv: tuple[str, ...]
    cwd: SandboxRelativePath
    timeout_seconds: int
    max_output_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.cwd, SandboxRelativePath):
            raise TypeError("cwd must be SandboxRelativePath")
        if not isinstance(self.argv, tuple):
            raise TypeError("argv must be an immutable tuple")
        if not self.argv or len(self.argv) > _MAX_ARG_COUNT:
            raise ValueError("argv count is outside the safe range")
        for argument in self.argv:
            if not isinstance(argument, str):
                raise TypeError("argv entries must be strings")
            if not argument or "\x00" in argument:
                raise ValueError("argv entries cannot be empty or contain NUL")
            if len(argument.encode("utf-8")) > _MAX_ARG_BYTES:
                raise ValueError("argv entry is too large")
        _require_strict_int(
            self.timeout_seconds,
            name="timeout_seconds",
            minimum=1,
            maximum=3_600,
        )
        _require_strict_int(
            self.max_output_bytes,
            name="max_output_bytes",
            minimum=0,
            maximum=64 * 1024 * 1024,
        )


@dataclass(frozen=True, slots=True)
class SandboxOperationRequest:
    """Untrusted operation claims that must be verified on every call.

    This is not a bearer capability and is never sufficient by possession.
    The verifier must compare every field against current server-owned
    Workspace/Run/Node/lease and capability state.
    """

    operation_id: UUID
    action: SandboxAction
    tenant_id: UUID
    workspace_id: UUID
    run_id: UUID
    runtime_instance_id: UUID
    capability_grant_id: UUID
    node_id: UUID
    lease_id: UUID
    workspace_generation: int
    run_fencing_token: int
    node_fencing_token: int
    workload_identity_thumbprint: str

    def __post_init__(self) -> None:
        identifiers = {
            "operation_id": self.operation_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "run_id": self.run_id,
            "runtime_instance_id": self.runtime_instance_id,
            "capability_grant_id": self.capability_grant_id,
            "node_id": self.node_id,
            "lease_id": self.lease_id,
        }
        if any(not isinstance(value, UUID) for value in identifiers.values()):
            raise TypeError("sandbox identifiers must be UUID values")
        if not isinstance(self.action, SandboxAction):
            raise TypeError("action must be SandboxAction")
        _require_strict_int(
            self.workspace_generation,
            name="workspace_generation",
            minimum=1,
            maximum=2**63 - 1,
        )
        _require_strict_int(
            self.run_fencing_token,
            name="run_fencing_token",
            minimum=1,
            maximum=2**63 - 1,
        )
        _require_strict_int(
            self.node_fencing_token,
            name="node_fencing_token",
            minimum=1,
            maximum=2**63 - 1,
        )
        _require_sha256(
            self.workload_identity_thumbprint,
            name="workload_identity_thumbprint",
        )


@dataclass(frozen=True, slots=True)
class VerifiedSandboxAuthorization:
    """Server-owned result of a live lease/capability verification."""

    request: SandboxOperationRequest
    verified_at: datetime
    expires_at: datetime
    verification_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.request, SandboxOperationRequest):
            raise TypeError("request must be SandboxOperationRequest")
        _require_aware_utc(self.verified_at, name="verified_at")
        _require_aware_utc(self.expires_at, name="expires_at")
        if self.expires_at <= self.verified_at:
            raise ValueError("verified authorization is already expired")
        _require_sha256(self.verification_digest, name="verification_digest")


class SandboxAuthorizer(Protocol):
    """Online verifier for live lease, fencing, identity and capability state."""

    def authorize(self, request: SandboxOperationRequest) -> VerifiedSandboxAuthorization: ...


class RejectingSandboxAuthorizer:
    """Production-safe default until trusted P34.4/P34.2 wiring exists."""

    def authorize(self, request: SandboxOperationRequest) -> VerifiedSandboxAuthorization:
        del request
        raise SandboxUnavailable("sandbox_authorizer_unavailable")


@dataclass(frozen=True, slots=True)
class SandboxRuntimeHandle:
    """Internal provider locator; never a public DTO or authorization fact."""

    value: UUID = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise TypeError("runtime handle must be a UUID")


@dataclass(frozen=True, slots=True)
class SandboxRuntimeView:
    handle: SandboxRuntimeHandle
    tenant_id: UUID
    workspace_id: UUID
    run_id: UUID
    runtime_instance_id: UUID
    workspace_generation: int
    workload_identity_thumbprint: str
    state: SandboxRuntimeState
    reason_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.handle, SandboxRuntimeHandle):
            raise TypeError("handle must be SandboxRuntimeHandle")
        if any(
            not isinstance(value, UUID)
            for value in (
                self.tenant_id,
                self.workspace_id,
                self.run_id,
                self.runtime_instance_id,
            )
        ):
            raise TypeError("runtime view identifiers must be UUID values")
        _require_strict_int(
            self.workspace_generation,
            name="workspace_generation",
            minimum=1,
            maximum=2**63 - 1,
        )
        _require_sha256(
            self.workload_identity_thumbprint,
            name="workload_identity_thumbprint",
        )
        if not isinstance(self.state, SandboxRuntimeState):
            raise TypeError("state must be SandboxRuntimeState")
        if not isinstance(self.reason_code, str):
            raise TypeError("reason_code must be a string")
        if _REASON_CODE_RE.fullmatch(self.reason_code) is None:
            raise ValueError("reason_code is invalid")


@dataclass(frozen=True, slots=True)
class SandboxLogPage:
    chunks: tuple[str, ...]
    next_cursor: str | None
    truncated: bool


@dataclass(frozen=True, slots=True)
class SandboxStats:
    state: SandboxRuntimeState
    cpu_millis_used: int
    memory_bytes_used: int
    pids_used: int
    writable_bytes_used: int


@dataclass(frozen=True, slots=True)
class SandboxSnapshot:
    snapshot_id: UUID
    tenant_id: UUID
    workspace_id: UUID
    source_run_id: UUID
    source_runtime_instance_id: UUID
    source_generation: int
    source_workload_identity_thumbprint: str
    manifest_digest: str
    metadata_only: bool = True

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, UUID)
            for value in (
                self.snapshot_id,
                self.tenant_id,
                self.workspace_id,
                self.source_run_id,
                self.source_runtime_instance_id,
            )
        ):
            raise TypeError("snapshot identifiers must be UUID values")
        _require_strict_int(
            self.source_generation,
            name="source_generation",
            minimum=1,
            maximum=2**63 - 1,
        )
        _require_sha256(
            self.source_workload_identity_thumbprint,
            name="source_workload_identity_thumbprint",
        )
        _require_sha256(self.manifest_digest, name="manifest_digest")
        if self.metadata_only is not True:
            raise ValueError("A0 snapshots must remain metadata-only")


class SandboxProvider(Protocol):
    """Provider seam for P34.5; implementations must remain fail-closed."""

    def prepare(
        self,
        *,
        request: SandboxOperationRequest,
        spec: SandboxRuntimeSpec,
    ) -> str: ...

    def create(
        self,
        *,
        request: SandboxOperationRequest,
        spec: SandboxRuntimeSpec,
        prepared_digest: str,
    ) -> SandboxRuntimeView: ...

    def start(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
    ) -> SandboxRuntimeView: ...

    def exec(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
        command: SandboxCommandSpec,
    ) -> None: ...

    def cancel(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
        execution_id: UUID,
    ) -> None: ...

    def logs(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
        cursor: str | None,
        byte_limit: int,
    ) -> SandboxLogPage: ...

    def stats(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
    ) -> SandboxStats: ...

    def snapshot(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
    ) -> SandboxSnapshot: ...

    def restore_new_generation(
        self,
        *,
        request: SandboxOperationRequest,
        snapshot: SandboxSnapshot,
        spec: SandboxRuntimeSpec,
    ) -> SandboxRuntimeView: ...

    def stop(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
    ) -> SandboxRuntimeView: ...

    def destroy(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
    ) -> SandboxRuntimeView: ...


def utc_now() -> datetime:
    """Small injectable clock default for deterministic provider tests."""

    return datetime.now(UTC)


__all__ = [
    "RejectingSandboxAuthorizer",
    "SandboxAction",
    "SandboxAuthorizer",
    "SandboxCommandSpec",
    "SandboxConflict",
    "SandboxError",
    "SandboxExecutionDisabled",
    "SandboxIsolationPolicy",
    "SandboxLogPage",
    "SandboxNetworkMode",
    "SandboxNetworkPolicy",
    "SandboxOperationRequest",
    "SandboxProvider",
    "SandboxRejected",
    "SandboxRelativePath",
    "SandboxResourceLimits",
    "SandboxRuntimeHandle",
    "SandboxRuntimeSpec",
    "SandboxRuntimeState",
    "SandboxRuntimeView",
    "SandboxSnapshot",
    "SandboxStats",
    "SandboxUnavailable",
    "VerifiedSandboxAuthorization",
    "utc_now",
]
