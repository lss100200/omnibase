"""Typed P34.4 component seams.

The production defaults reject work.  P34.4 fake implementations only move
logical metadata and never execute code, open sockets, or contact data stores.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class WorkspaceComponentUnavailable(RuntimeError):
    """A later-phase runtime/network component is intentionally unavailable."""


@dataclass(frozen=True)
class ReconcileResult:
    observed_state: str
    reason_code: str


class WorkspaceReconciler(Protocol):
    def reconcile(
        self,
        *,
        workspace_id: str,
        generation: int,
        desired_state: str,
        observed_state: str,
    ) -> ReconcileResult: ...


class UnavailableWorkspaceReconciler:
    """Production-safe default until P34.5 installs a real runtime path."""

    def reconcile(
        self,
        *,
        workspace_id: str,
        generation: int,
        desired_state: str,
        observed_state: str,
    ) -> ReconcileResult:
        del workspace_id, generation, desired_state, observed_state
        raise WorkspaceComponentUnavailable("workspace_reconciler_unavailable")


class FakeMetadataWorkspaceReconciler:
    """Deterministic metadata-only test implementation; it runs no workload."""

    _OBSERVED = {
        "stopped": "stopped",
        "running": "running",
        "paused": "paused",
        "archived": "archived",
    }

    def reconcile(
        self,
        *,
        workspace_id: str,
        generation: int,
        desired_state: str,
        observed_state: str,
    ) -> ReconcileResult:
        del workspace_id, generation, observed_state
        target = self._OBSERVED.get(desired_state)
        if target is None:
            raise ValueError("unsupported desired state")
        return ReconcileResult(
            observed_state=target,
            reason_code="metadata_only_no_runtime",
        )


__all__ = [
    "FakeMetadataWorkspaceReconciler",
    "ReconcileResult",
    "UnavailableWorkspaceReconciler",
    "WorkspaceComponentUnavailable",
    "WorkspaceReconciler",
]
