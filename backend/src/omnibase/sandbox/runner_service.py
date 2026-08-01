"""Host-bound independent Runner service for P34.5A4."""

from __future__ import annotations

from typing import Protocol

from omnibase.sandbox.host import VerifiedRunnerHost
from omnibase.sandbox.runner import (
    RunnerExecutionPlan,
    RunnerReceipt,
    RunnerTerminationPlan,
)
from omnibase.sandbox.runtime_driver import (
    LinuxRuntimeDriver,
    UnavailableLinuxRuntimeDriver,
)


class HostBoundSandboxRunner(Protocol):
    """Runner boundary after transport authentication and replay checks."""

    def execute(
        self,
        *,
        plan: RunnerExecutionPlan,
        host: VerifiedRunnerHost,
    ) -> RunnerReceipt: ...

    def terminate(
        self,
        *,
        plan: RunnerTerminationPlan,
        host: VerifiedRunnerHost,
    ) -> RunnerReceipt: ...


class AttestedLinuxSandboxRunner:
    """Translate an attested Linux RuntimeDriver receipt into the Runner contract."""

    def __init__(self, *, runtime_driver: LinuxRuntimeDriver | None = None) -> None:
        self._runtime_driver = runtime_driver or UnavailableLinuxRuntimeDriver()

    def execute(
        self,
        *,
        plan: RunnerExecutionPlan,
        host: VerifiedRunnerHost,
    ) -> RunnerReceipt:
        result = self._runtime_driver.execute(plan=plan, host=host)
        return RunnerReceipt(
            operation_id=result.operation_id,
            evidence_digest=result.evidence_digest,
            reason_code=result.reason_code,
            binding_digest=result.binding_digest,
            runner_id=result.runner_id,
            runtime_instance_id=result.runtime_instance_id,
            exit_code=result.exit_code,
            truncated=result.truncated,
        )

    def terminate(
        self,
        *,
        plan: RunnerTerminationPlan,
        host: VerifiedRunnerHost,
    ) -> RunnerReceipt:
        result = self._runtime_driver.terminate(plan=plan, host=host)
        return RunnerReceipt(
            operation_id=result.operation_id,
            evidence_digest=result.evidence_digest,
            reason_code=result.reason_code,
            binding_digest=result.binding_digest,
            runner_id=result.runner_id,
            runtime_instance_id=result.runtime_instance_id,
            exit_code=result.exit_code,
            truncated=result.truncated,
        )


__all__ = [
    "AttestedLinuxSandboxRunner",
    "HostBoundSandboxRunner",
]
