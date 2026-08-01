"""Rejecting and metadata-only P34.5A sandbox provider implementations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import NoReturn
from uuid import UUID, uuid4

from omnibase.sandbox.contracts import (
    SandboxAction,
    SandboxAuthorizer,
    SandboxCommandSpec,
    SandboxConflict,
    SandboxExecutionDisabled,
    SandboxLogPage,
    SandboxOperationRequest,
    SandboxRejected,
    SandboxRuntimeHandle,
    SandboxRuntimeSpec,
    SandboxRuntimeState,
    SandboxRuntimeView,
    SandboxSnapshot,
    SandboxStats,
    SandboxUnavailable,
    VerifiedSandboxAuthorization,
    utc_now,
)


def _digest_json(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _spec_digest(spec: SandboxRuntimeSpec) -> str:
    return _digest_json(
        {
            "isolation": {
                "allow_devices": spec.isolation.allow_devices,
                "allow_host_mounts": spec.isolation.allow_host_mounts,
                "allow_runtime_socket": spec.isolation.allow_runtime_socket,
                "drop_all_capabilities": spec.isolation.drop_all_capabilities,
                "no_new_privileges": spec.isolation.no_new_privileges,
                "read_only_root": spec.isolation.read_only_root,
                "run_as_gid": spec.isolation.run_as_gid,
                "run_as_uid": spec.isolation.run_as_uid,
            },
            "limits": {
                "cpu_millis": spec.limits.cpu_millis,
                "inodes": spec.limits.inodes,
                "memory_bytes": spec.limits.memory_bytes,
                "output_bytes": spec.limits.output_bytes,
                "pids": spec.limits.pids,
                "wall_time_seconds": spec.limits.wall_time_seconds,
                "writable_bytes": spec.limits.writable_bytes,
            },
            "network": {
                "allowed_service_ids": [str(item) for item in spec.network.allowed_service_ids],
                "direct_overlay": spec.network.direct_overlay,
                "mode": spec.network.mode.value,
            },
            "policy_digest": spec.policy_digest,
            "template_digest": spec.template_digest,
        }
    )


def _unavailable() -> NoReturn:
    raise SandboxUnavailable("sandbox_provider_unavailable")


class UnavailableSandboxProvider:
    """Production-safe default: every operation is rejected without side effects."""

    def prepare(
        self,
        *,
        request: SandboxOperationRequest,
        spec: SandboxRuntimeSpec,
    ) -> str:
        del request, spec
        _unavailable()

    def create(
        self,
        *,
        request: SandboxOperationRequest,
        spec: SandboxRuntimeSpec,
        prepared_digest: str,
    ) -> SandboxRuntimeView:
        del request, spec, prepared_digest
        _unavailable()

    def start(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
    ) -> SandboxRuntimeView:
        del request, handle
        _unavailable()

    def exec(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
        command: SandboxCommandSpec,
    ) -> None:
        del request, handle, command
        _unavailable()

    def cancel(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
        execution_id: UUID,
    ) -> None:
        del request, handle, execution_id
        _unavailable()

    def logs(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
        cursor: str | None,
        byte_limit: int,
    ) -> SandboxLogPage:
        del request, handle, cursor, byte_limit
        _unavailable()

    def stats(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
    ) -> SandboxStats:
        del request, handle
        _unavailable()

    def snapshot(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
    ) -> SandboxSnapshot:
        del request, handle
        _unavailable()

    def restore_new_generation(
        self,
        *,
        request: SandboxOperationRequest,
        snapshot: SandboxSnapshot,
        spec: SandboxRuntimeSpec,
    ) -> SandboxRuntimeView:
        del request, snapshot, spec
        _unavailable()

    def stop(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
    ) -> SandboxRuntimeView:
        del request, handle
        _unavailable()

    def destroy(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
    ) -> SandboxRuntimeView:
        del request, handle
        _unavailable()


@dataclass(frozen=True, slots=True)
class _AuthorizationRecord:
    tenant_id: UUID
    workspace_id: UUID
    run_id: UUID
    node_id: UUID
    lease_id: UUID
    workspace_generation: int
    run_fencing_token: int
    node_fencing_token: int
    workload_identity_thumbprint: str
    allowed_actions: frozenset[SandboxAction]
    expires_at: datetime
    revoked: bool = False


class InMemorySandboxAuthorizer:
    """Explicit test-only authority ledger.

    It models live server-owned lease/capability facts.  It never accepts a raw
    token, never persists credentials and is not suitable for production.
    """

    def __init__(self, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._clock = clock
        self._records: dict[UUID, _AuthorizationRecord] = {}

    def install(
        self,
        *,
        request: SandboxOperationRequest,
        allowed_actions: frozenset[SandboxAction],
        expires_at: datetime,
    ) -> None:
        now = self._clock()
        if expires_at.tzinfo is None or expires_at.utcoffset() is None or expires_at <= now:
            raise ValueError("authorization expiry must be a future aware datetime")
        if not allowed_actions:
            raise ValueError("authorization must allow at least one action")
        self._records[request.lease_id] = _AuthorizationRecord(
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            run_id=request.run_id,
            node_id=request.node_id,
            lease_id=request.lease_id,
            workspace_generation=request.workspace_generation,
            run_fencing_token=request.run_fencing_token,
            node_fencing_token=request.node_fencing_token,
            workload_identity_thumbprint=request.workload_identity_thumbprint,
            allowed_actions=allowed_actions,
            expires_at=expires_at,
        )

    def revoke(self, lease_id: UUID) -> None:
        record = self._records.get(lease_id)
        if record is None:
            return
        self._records[lease_id] = _AuthorizationRecord(
            tenant_id=record.tenant_id,
            workspace_id=record.workspace_id,
            run_id=record.run_id,
            node_id=record.node_id,
            lease_id=record.lease_id,
            workspace_generation=record.workspace_generation,
            run_fencing_token=record.run_fencing_token,
            node_fencing_token=record.node_fencing_token,
            workload_identity_thumbprint=record.workload_identity_thumbprint,
            allowed_actions=record.allowed_actions,
            expires_at=record.expires_at,
            revoked=True,
        )

    def authorize(self, request: SandboxOperationRequest) -> VerifiedSandboxAuthorization:
        record = self._records.get(request.lease_id)
        if record is None or record.revoked:
            raise SandboxRejected("sandbox_authorization_rejected")
        now = self._clock()
        if record.expires_at <= now:
            raise SandboxRejected("sandbox_authorization_expired")
        expected = (
            record.tenant_id,
            record.workspace_id,
            record.run_id,
            record.node_id,
            record.lease_id,
            record.workspace_generation,
            record.run_fencing_token,
            record.node_fencing_token,
            record.workload_identity_thumbprint,
        )
        supplied = (
            request.tenant_id,
            request.workspace_id,
            request.run_id,
            request.node_id,
            request.lease_id,
            request.workspace_generation,
            request.run_fencing_token,
            request.node_fencing_token,
            request.workload_identity_thumbprint,
        )
        if supplied != expected or request.action not in record.allowed_actions:
            raise SandboxRejected("sandbox_authorization_rejected")
        verification_digest = _digest_json(
            {
                "action": request.action.value,
                "lease_id": str(request.lease_id),
                "node_fencing_token": request.node_fencing_token,
                "operation_id": str(request.operation_id),
                "run_fencing_token": request.run_fencing_token,
                "workspace_generation": request.workspace_generation,
                "workload_identity_thumbprint": request.workload_identity_thumbprint,
            }
        )
        return VerifiedSandboxAuthorization(
            request=request,
            verified_at=now,
            expires_at=record.expires_at,
            verification_digest=verification_digest,
        )


@dataclass(slots=True)
class _RuntimeRecord:
    handle: SandboxRuntimeHandle
    tenant_id: UUID
    workspace_id: UUID
    run_id: UUID
    node_id: UUID
    lease_id: UUID
    workspace_generation: int
    run_fencing_token: int
    node_fencing_token: int
    workload_identity_thumbprint: str
    spec: SandboxRuntimeSpec
    state: SandboxRuntimeState


class FakeInMemorySandboxProvider:
    """Metadata-only lifecycle harness.

    It creates no process, container, file, socket, network, mount or provider
    resource.  In particular, ``exec`` and ``cancel`` remain hard-disabled.
    """

    def __init__(self, *, authorizer: SandboxAuthorizer) -> None:
        self._authorizer = authorizer
        self._prepared: dict[str, SandboxRuntimeSpec] = {}
        self._runtimes: dict[UUID, _RuntimeRecord] = {}
        self._snapshots: dict[UUID, SandboxSnapshot] = {}

    def _authorize(
        self,
        request: SandboxOperationRequest,
        expected_action: SandboxAction,
    ) -> VerifiedSandboxAuthorization:
        if request.action is not expected_action:
            raise SandboxRejected("sandbox_action_mismatch")
        return self._authorizer.authorize(request)

    def _runtime(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
    ) -> _RuntimeRecord:
        record = self._runtimes.get(handle.value)
        if record is None:
            raise SandboxRejected("sandbox_runtime_not_found")
        binding = (
            record.tenant_id,
            record.workspace_id,
            record.run_id,
            record.node_id,
            record.lease_id,
            record.workspace_generation,
            record.run_fencing_token,
            record.node_fencing_token,
            record.workload_identity_thumbprint,
        )
        supplied = (
            request.tenant_id,
            request.workspace_id,
            request.run_id,
            request.node_id,
            request.lease_id,
            request.workspace_generation,
            request.run_fencing_token,
            request.node_fencing_token,
            request.workload_identity_thumbprint,
        )
        if binding != supplied:
            raise SandboxRejected("sandbox_runtime_not_found")
        return record

    @staticmethod
    def _view(record: _RuntimeRecord, *, reason_code: str) -> SandboxRuntimeView:
        return SandboxRuntimeView(
            handle=record.handle,
            tenant_id=record.tenant_id,
            workspace_id=record.workspace_id,
            run_id=record.run_id,
            workspace_generation=record.workspace_generation,
            workload_identity_thumbprint=record.workload_identity_thumbprint,
            state=record.state,
            reason_code=reason_code,
        )

    def prepare(
        self,
        *,
        request: SandboxOperationRequest,
        spec: SandboxRuntimeSpec,
    ) -> str:
        self._authorize(request, SandboxAction.PREPARE)
        prepared_digest = _spec_digest(spec)
        self._prepared[prepared_digest] = spec
        return prepared_digest

    def create(
        self,
        *,
        request: SandboxOperationRequest,
        spec: SandboxRuntimeSpec,
        prepared_digest: str,
    ) -> SandboxRuntimeView:
        self._authorize(request, SandboxAction.CREATE)
        if self._prepared.get(prepared_digest) != spec or _spec_digest(spec) != prepared_digest:
            raise SandboxRejected("sandbox_prepared_spec_mismatch")
        if any(record.run_id == request.run_id for record in self._runtimes.values()):
            raise SandboxConflict("sandbox_run_already_has_runtime")
        handle = SandboxRuntimeHandle(uuid4())
        record = _RuntimeRecord(
            handle=handle,
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            run_id=request.run_id,
            node_id=request.node_id,
            lease_id=request.lease_id,
            workspace_generation=request.workspace_generation,
            run_fencing_token=request.run_fencing_token,
            node_fencing_token=request.node_fencing_token,
            workload_identity_thumbprint=request.workload_identity_thumbprint,
            spec=spec,
            state=SandboxRuntimeState.CREATED,
        )
        self._runtimes[handle.value] = record
        return self._view(record, reason_code="metadata_only_runtime_created")

    def start(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
    ) -> SandboxRuntimeView:
        self._authorize(request, SandboxAction.START)
        record = self._runtime(request=request, handle=handle)
        if record.state is SandboxRuntimeState.DESTROYED:
            raise SandboxConflict("sandbox_runtime_destroyed")
        if record.state is SandboxRuntimeState.STOPPED:
            raise SandboxConflict("sandbox_runtime_stopped")
        record.state = SandboxRuntimeState.RUNNING
        return self._view(record, reason_code="metadata_only_runtime_running")

    def exec(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
        command: SandboxCommandSpec,
    ) -> None:
        self._authorize(request, SandboxAction.EXEC)
        record = self._runtime(request=request, handle=handle)
        del command
        if record.state is not SandboxRuntimeState.RUNNING:
            raise SandboxConflict("sandbox_runtime_not_running")
        raise SandboxExecutionDisabled("sandbox_execution_not_unlocked")

    def cancel(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
        execution_id: UUID,
    ) -> None:
        self._authorize(request, SandboxAction.CANCEL)
        self._runtime(request=request, handle=handle)
        del execution_id
        raise SandboxExecutionDisabled("sandbox_execution_not_unlocked")

    def logs(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
        cursor: str | None,
        byte_limit: int,
    ) -> SandboxLogPage:
        self._authorize(request, SandboxAction.LOGS)
        record = self._runtime(request=request, handle=handle)
        if isinstance(byte_limit, bool) or not isinstance(byte_limit, int):
            raise TypeError("byte_limit must be an integer")
        if byte_limit < 0 or byte_limit > min(record.spec.limits.output_bytes, 1024 * 1024):
            raise SandboxRejected("sandbox_log_limit_rejected")
        if cursor is not None:
            raise SandboxRejected("sandbox_log_cursor_rejected")
        return SandboxLogPage(chunks=(), next_cursor=None, truncated=False)

    def stats(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
    ) -> SandboxStats:
        self._authorize(request, SandboxAction.STATS)
        record = self._runtime(request=request, handle=handle)
        return SandboxStats(
            state=record.state,
            cpu_millis_used=0,
            memory_bytes_used=0,
            pids_used=0,
            writable_bytes_used=0,
        )

    def snapshot(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
    ) -> SandboxSnapshot:
        self._authorize(request, SandboxAction.SNAPSHOT)
        record = self._runtime(request=request, handle=handle)
        if record.state is not SandboxRuntimeState.STOPPED:
            raise SandboxConflict("sandbox_snapshot_requires_stopped_runtime")
        snapshot_id = uuid4()
        manifest_digest = _digest_json(
            {
                "metadata_only": True,
                "run_id": str(record.run_id),
                "snapshot_id": str(snapshot_id),
                "spec_digest": _spec_digest(record.spec),
                "workspace_generation": record.workspace_generation,
            }
        )
        snapshot = SandboxSnapshot(
            snapshot_id=snapshot_id,
            tenant_id=record.tenant_id,
            workspace_id=record.workspace_id,
            source_run_id=record.run_id,
            source_generation=record.workspace_generation,
            source_workload_identity_thumbprint=record.workload_identity_thumbprint,
            manifest_digest=manifest_digest,
        )
        self._snapshots[snapshot.snapshot_id] = snapshot
        return snapshot

    def restore_new_generation(
        self,
        *,
        request: SandboxOperationRequest,
        snapshot: SandboxSnapshot,
        spec: SandboxRuntimeSpec,
    ) -> SandboxRuntimeView:
        self._authorize(request, SandboxAction.RESTORE)
        if self._snapshots.get(snapshot.snapshot_id) != snapshot:
            raise SandboxRejected("sandbox_snapshot_not_found")
        if (
            request.tenant_id != snapshot.tenant_id
            or request.workspace_id != snapshot.workspace_id
            or request.run_id == snapshot.source_run_id
            or request.workspace_generation <= snapshot.source_generation
            or request.workload_identity_thumbprint == snapshot.source_workload_identity_thumbprint
        ):
            raise SandboxRejected("sandbox_restore_identity_reuse_rejected")
        if any(record.run_id == request.run_id for record in self._runtimes.values()):
            raise SandboxConflict("sandbox_run_already_has_runtime")
        handle = SandboxRuntimeHandle(uuid4())
        record = _RuntimeRecord(
            handle=handle,
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            run_id=request.run_id,
            node_id=request.node_id,
            lease_id=request.lease_id,
            workspace_generation=request.workspace_generation,
            run_fencing_token=request.run_fencing_token,
            node_fencing_token=request.node_fencing_token,
            workload_identity_thumbprint=request.workload_identity_thumbprint,
            spec=spec,
            state=SandboxRuntimeState.CREATED,
        )
        self._runtimes[handle.value] = record
        return self._view(record, reason_code="metadata_only_restore_created")

    def stop(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
    ) -> SandboxRuntimeView:
        self._authorize(request, SandboxAction.STOP)
        record = self._runtime(request=request, handle=handle)
        if record.state is SandboxRuntimeState.DESTROYED:
            raise SandboxConflict("sandbox_runtime_destroyed")
        record.state = SandboxRuntimeState.STOPPED
        return self._view(record, reason_code="metadata_only_runtime_stopped")

    def destroy(
        self,
        *,
        request: SandboxOperationRequest,
        handle: SandboxRuntimeHandle,
    ) -> SandboxRuntimeView:
        self._authorize(request, SandboxAction.DESTROY)
        record = self._runtime(request=request, handle=handle)
        record.state = SandboxRuntimeState.DESTROYED
        return self._view(record, reason_code="metadata_only_runtime_destroyed")


__all__ = [
    "FakeInMemorySandboxProvider",
    "InMemorySandboxAuthorizer",
    "UnavailableSandboxProvider",
]
