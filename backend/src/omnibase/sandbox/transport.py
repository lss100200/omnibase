"""Independent Runner transport seam for P34.5A2.

The main backend owns no Docker/container socket and does not execute a command.
It may only dispatch a fully authorized, durably reserved plan to a separately
authenticated Runner transport.  The transport remains unavailable by default.
"""

from __future__ import annotations

from typing import NoReturn, Protocol

from omnibase.sandbox.contracts import SandboxError, SandboxUnavailable
from omnibase.sandbox.host import VerifiedRunnerHost
from omnibase.sandbox.runner import (
    RunnerExecutionPlan,
    RunnerReceipt,
    RunnerTerminationPlan,
)


class RunnerOutcomeUnknown(SandboxError):
    """Dispatch may have crossed the Runner boundary; replay is prohibited."""


class RunnerTransport(Protocol):
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


def _unavailable() -> NoReturn:
    raise SandboxUnavailable("sandbox_runner_transport_unavailable")


class UnavailableRunnerTransport:
    """Production-safe default; never performs network or runtime I/O."""

    def execute(
        self,
        *,
        plan: RunnerExecutionPlan,
        host: VerifiedRunnerHost,
    ) -> RunnerReceipt:
        del plan, host
        _unavailable()

    def terminate(
        self,
        *,
        plan: RunnerTerminationPlan,
        host: VerifiedRunnerHost,
    ) -> RunnerReceipt:
        del plan, host
        _unavailable()


__all__ = [
    "RunnerOutcomeUnknown",
    "RunnerTransport",
    "UnavailableRunnerTransport",
]
