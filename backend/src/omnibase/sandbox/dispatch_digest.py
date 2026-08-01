"""Canonical durable bindings for P34.5 Sandbox dispatch.

These helpers are intentionally pure: Core can recompute request and execution
specification digests before reserving a durable operation without importing a
runtime, process, filesystem, socket, or container implementation.
"""

from __future__ import annotations

import hashlib
import json

from omnibase.sandbox.contracts import (
    SandboxCommandSpec,
    SandboxOperationRequest,
    SandboxRuntimeHandle,
    SandboxRuntimeSpec,
)
from omnibase.sandbox.host import VerifiedRunnerHost
from omnibase.sandbox.runner import RunnerExecutionPlan, RunnerIsolationProfile


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sandbox_request_digest(request: SandboxOperationRequest) -> str:
    """Bind every caller-supplied claim that the online verifier rechecks."""
    if not isinstance(request, SandboxOperationRequest):
        raise TypeError("request must be SandboxOperationRequest")
    return _digest(
        {
            "action": request.action.value,
            "capability_grant_id": str(request.capability_grant_id),
            "lease_id": str(request.lease_id),
            "node_fencing_token": request.node_fencing_token,
            "node_id": str(request.node_id),
            "operation_id": str(request.operation_id),
            "run_fencing_token": request.run_fencing_token,
            "run_id": str(request.run_id),
            "runtime_instance_id": str(request.runtime_instance_id),
            "tenant_id": str(request.tenant_id),
            "workspace_generation": request.workspace_generation,
            "workspace_id": str(request.workspace_id),
            "workload_identity_thumbprint": request.workload_identity_thumbprint,
        }
    )


def sandbox_execution_spec_digest(
    *,
    runtime_handle: SandboxRuntimeHandle,
    runtime_spec: SandboxRuntimeSpec,
    command: SandboxCommandSpec,
    isolation_profile: RunnerIsolationProfile,
) -> str:
    """Bind the exact runtime, command, limits, and isolation policy to execute."""
    if not isinstance(runtime_handle, SandboxRuntimeHandle):
        raise TypeError("runtime_handle must be SandboxRuntimeHandle")
    if not isinstance(runtime_spec, SandboxRuntimeSpec):
        raise TypeError("runtime_spec must be SandboxRuntimeSpec")
    if not isinstance(command, SandboxCommandSpec):
        raise TypeError("command must be SandboxCommandSpec")
    if not isinstance(isolation_profile, RunnerIsolationProfile):
        raise TypeError("isolation_profile must be RunnerIsolationProfile")
    return _digest(
        {
            "command": {
                "argv": list(command.argv),
                "cwd": command.cwd.value,
                "max_output_bytes": command.max_output_bytes,
                "timeout_seconds": command.timeout_seconds,
            },
            "isolation_profile_digest": isolation_profile.digest(),
            "runtime_handle": str(runtime_handle.value),
            "runtime_spec": {
                "isolation": {
                    "allow_devices": runtime_spec.isolation.allow_devices,
                    "allow_host_mounts": runtime_spec.isolation.allow_host_mounts,
                    "allow_runtime_socket": runtime_spec.isolation.allow_runtime_socket,
                    "drop_all_capabilities": runtime_spec.isolation.drop_all_capabilities,
                    "no_new_privileges": runtime_spec.isolation.no_new_privileges,
                    "read_only_root": runtime_spec.isolation.read_only_root,
                    "run_as_gid": runtime_spec.isolation.run_as_gid,
                    "run_as_uid": runtime_spec.isolation.run_as_uid,
                },
                "limits": {
                    "cpu_millis": runtime_spec.limits.cpu_millis,
                    "inodes": runtime_spec.limits.inodes,
                    "memory_bytes": runtime_spec.limits.memory_bytes,
                    "output_bytes": runtime_spec.limits.output_bytes,
                    "pids": runtime_spec.limits.pids,
                    "wall_time_seconds": runtime_spec.limits.wall_time_seconds,
                    "writable_bytes": runtime_spec.limits.writable_bytes,
                },
                "network": {
                    "allowed_service_ids": [
                        str(item) for item in runtime_spec.network.allowed_service_ids
                    ],
                    "direct_overlay": runtime_spec.network.direct_overlay,
                    "mode": runtime_spec.network.mode.value,
                },
                "policy_digest": runtime_spec.policy_digest,
                "template_digest": runtime_spec.template_digest,
            },
        }
    )


def runner_execution_binding_digest(
    plan: RunnerExecutionPlan,
    host: VerifiedRunnerHost,
) -> str:
    """Bind a Runner receipt to the exact authorized execution and host proof."""
    if not isinstance(plan, RunnerExecutionPlan):
        raise TypeError("plan must be RunnerExecutionPlan")
    if not isinstance(host, VerifiedRunnerHost):
        raise TypeError("host must be VerifiedRunnerHost")
    request = plan.request
    return _digest(
        {
            "action": request.action.value,
            "argv": list(plan.command.argv),
            "authorization": plan.authorization.verification_digest,
            "command_cwd": plan.command.cwd.value,
            "command_max_output_bytes": plan.command.max_output_bytes,
            "command_timeout_seconds": plan.command.timeout_seconds,
            "host_evidence": host.evidence_digest,
            "isolation_profile": plan.isolation_profile.digest(),
            "lease_id": str(request.lease_id),
            "node_fencing_token": request.node_fencing_token,
            "node_id": str(request.node_id),
            "operation_id": str(request.operation_id),
            "run_fencing_token": request.run_fencing_token,
            "run_id": str(request.run_id),
            "runner_id": str(host.runner_id),
            "runtime_handle": str(plan.runtime_handle.value),
            "runtime_instance_id": str(request.runtime_instance_id),
            "runtime_spec": {
                "isolation": {
                    "allow_devices": plan.runtime_spec.isolation.allow_devices,
                    "allow_host_mounts": plan.runtime_spec.isolation.allow_host_mounts,
                    "allow_runtime_socket": plan.runtime_spec.isolation.allow_runtime_socket,
                    "drop_all_capabilities": plan.runtime_spec.isolation.drop_all_capabilities,
                    "no_new_privileges": plan.runtime_spec.isolation.no_new_privileges,
                    "read_only_root": plan.runtime_spec.isolation.read_only_root,
                    "run_as_gid": plan.runtime_spec.isolation.run_as_gid,
                    "run_as_uid": plan.runtime_spec.isolation.run_as_uid,
                },
                "limits": {
                    "cpu_millis": plan.runtime_spec.limits.cpu_millis,
                    "inodes": plan.runtime_spec.limits.inodes,
                    "memory_bytes": plan.runtime_spec.limits.memory_bytes,
                    "output_bytes": plan.runtime_spec.limits.output_bytes,
                    "pids": plan.runtime_spec.limits.pids,
                    "wall_time_seconds": plan.runtime_spec.limits.wall_time_seconds,
                    "writable_bytes": plan.runtime_spec.limits.writable_bytes,
                },
                "network": {
                    "allowed_service_ids": [
                        str(item) for item in plan.runtime_spec.network.allowed_service_ids
                    ],
                    "direct_overlay": plan.runtime_spec.network.direct_overlay,
                    "mode": plan.runtime_spec.network.mode.value,
                },
                "policy_digest": plan.runtime_spec.policy_digest,
                "template_digest": plan.runtime_spec.template_digest,
            },
            "tenant_id": str(request.tenant_id),
            "workspace_generation": request.workspace_generation,
            "workspace_id": str(request.workspace_id),
            "workload_identity": request.workload_identity_thumbprint,
        }
    )


__all__ = [
    "runner_execution_binding_digest",
    "sandbox_execution_spec_digest",
    "sandbox_request_digest",
]
