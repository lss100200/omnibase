"""Trusted Runner-host attestation contracts for P34.5A2.

The Core API does not probe a host or trust a Runner's self-description.  A
production deployment must inject an attestor backed by a trusted Node/Runner
control channel.  Missing attestation remains a hard failure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from omnibase.sandbox.contracts import (
    SandboxOperationRequest,
    SandboxRejected,
    SandboxUnavailable,
)
from omnibase.sandbox.runner import RunnerIsolationProfile

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _require_aware(value: datetime, *, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class VerifiedRunnerHost:
    """Server-owned proof that one fenced Runner satisfies a target profile."""

    runner_id: UUID
    node_id: UUID
    node_fencing_token: int
    runner_identity_thumbprint: str
    isolation_profile_digest: str
    verified_at: datetime
    expires_at: datetime
    evidence_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.runner_id, UUID) or not isinstance(self.node_id, UUID):
            raise TypeError("runner host identifiers must be UUID values")
        if (
            isinstance(self.node_fencing_token, bool)
            or not isinstance(self.node_fencing_token, int)
            or self.node_fencing_token < 1
        ):
            raise ValueError("node_fencing_token must be a positive integer")
        for name, value in (
            ("runner_identity_thumbprint", self.runner_identity_thumbprint),
            ("isolation_profile_digest", self.isolation_profile_digest),
            ("evidence_digest", self.evidence_digest),
        ):
            if _SHA256_RE.fullmatch(value) is None:
                raise ValueError(f"{name} must be sha256")
        _require_aware(self.verified_at, name="verified_at")
        _require_aware(self.expires_at, name="expires_at")
        if self.expires_at <= self.verified_at:
            raise ValueError("runner host attestation is already expired")

    def verify_binding(
        self,
        *,
        request: SandboxOperationRequest,
        isolation_profile: RunnerIsolationProfile,
        now: datetime,
    ) -> None:
        _require_aware(now, name="clock")
        if (
            self.node_id != request.node_id
            or self.node_fencing_token != request.node_fencing_token
            or self.isolation_profile_digest != isolation_profile.digest()
            or self.verified_at > now
            or self.expires_at <= now
        ):
            raise SandboxRejected("sandbox_runner_host_attestation_rejected")


class RunnerHostAttestor(Protocol):
    """Trusted control-plane adapter; never implemented from request claims."""

    def attest(
        self,
        *,
        request: SandboxOperationRequest,
        isolation_profile: RunnerIsolationProfile,
    ) -> VerifiedRunnerHost: ...


class RejectingRunnerHostAttestor:
    def attest(
        self,
        *,
        request: SandboxOperationRequest,
        isolation_profile: RunnerIsolationProfile,
    ) -> VerifiedRunnerHost:
        del request, isolation_profile
        raise SandboxUnavailable("sandbox_runner_host_attestor_unavailable")


__all__ = [
    "RejectingRunnerHostAttestor",
    "RunnerHostAttestor",
    "VerifiedRunnerHost",
]
