"""Typed P34.4 component seams.

The production defaults reject work.  P34.4 fake implementations only move
logical metadata and never execute code, open sockets, or contact data stores.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class WorkspaceComponentUnavailable(RuntimeError):
    """A later-phase runtime/network component is intentionally unavailable."""


@dataclass(frozen=True)
class ReconcileResult:
    observed_state: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class VerifiedRunLeaseFacts:
    """Server-owned P34.4 facts suitable for a P34.5 verifier adapter."""

    tenant_id: str
    workspace_id: str
    run_id: str
    runtime_instance_id: str
    node_id: str
    lease_id: str
    workspace_generation: int
    run_fencing_token: int
    node_fencing_token: int
    workload_identity_digest: str
    verified_at: datetime
    expires_at: datetime
    verification_digest: str

    def __post_init__(self) -> None:
        for identifier_name, identifier_value in (
            ("tenant_id", self.tenant_id),
            ("workspace_id", self.workspace_id),
            ("run_id", self.run_id),
            ("runtime_instance_id", self.runtime_instance_id),
            ("node_id", self.node_id),
            ("lease_id", self.lease_id),
        ):
            try:
                UUID(identifier_value)
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValueError(f"{identifier_name} must be a UUID string") from exc
        for fencing_name, fencing_value in (
            ("workspace_generation", self.workspace_generation),
            ("run_fencing_token", self.run_fencing_token),
            ("node_fencing_token", self.node_fencing_token),
        ):
            if (
                isinstance(fencing_value, bool)
                or not isinstance(fencing_value, int)
                or fencing_value < 1
            ):
                raise ValueError(f"{fencing_name} must be a positive integer")
        if _SHA256_RE.fullmatch(self.workload_identity_digest) is None:
            raise ValueError("workload_identity_digest must be sha256")
        if _SHA256_RE.fullmatch(self.verification_digest) is None:
            raise ValueError("verification_digest must be sha256")
        if self.verified_at.tzinfo is None or self.verified_at.utcoffset() is None:
            raise ValueError("verified_at must be timezone-aware")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        if self.expires_at <= self.verified_at:
            raise ValueError("verified run lease is already expired")


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
    "VerifiedRunLeaseFacts",
    "WorkspaceComponentUnavailable",
    "WorkspaceReconciler",
]
