"""Offline P5.2A Agent Task / Run / Lease / Fencing ledger contract preflight.

P5.2A freezes the offline contract for the P5.2 Agent Task ledger **without**
any ORM, Alembic migration, database table, FastAPI invocation route, Browser
or workload SDK, Agent Runtime, Planner, Executor, dispatcher, scheduler,
worker, Celery task, polling or heartbeat loop, model or tool call, or
background coroutine.  It is a pure offline contract gate: strict DTOs,
closed-set enums, canonical hashing over raw UTF-8 bytes, offline validators
and fail-closed negative semantics.

The contract freezes:

- the AgentTask / Invocation identity hierarchy down to the existing P34.4
  Workspace Run, RuntimeInstance and WorkloadIdentity (no second identity
  system);
- the closed state machines for AgentTask, AgentStep, AgentAttempt and
  provider effects, including terminal immutability and ``unknown``
  no-replay;
- Task Lease independence from (and dependence on) the P34.4 Run Lease,
  Node attestation, Workspace generation and capability grant expiries;
- monotonic Task fencing and attempt numbering with no revival of stale
  holders, old leases, old runtime/workload identities or terminal Runs;
- strict budget ledgers over the twelve logical dimensions with
  limit/reserved/committed/released/remaining invariants;
- eight canonical hash profiles (task_create, task_cancel, task_pause,
  task_resume_request, attempt_claim, attempt_heartbeat, attempt_finish,
  reconciliation_request) with exact-replay and stable-conflict semantics;
- identity stage rules stating which fields are required, not yet generated,
  immutable once generated, Core-generated, or not submittable by the
  Browser or by a workload.

The three Phase 5 feature gates stay disabled and P5.2A remains
``blocked/not_proven`` while P34.7, P5.0 and P5.1 production are not
``ready``.  This module never reads the root ``.env``, never connects to a
database or network, never imports SQLAlchemy/FastAPI/Celery and never
starts anything.
"""

from __future__ import annotations

import json
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from itertools import pairwise
from pathlib import Path

from omnibase.production.composition import (
    AdmissionState,
    ConfigurationError,
    EvidenceReference,
    EvidenceStatus,
    GitSourceProvenance,
    SourceScope,
    build_git_source_provenance,
)
from omnibase.production.phase5_admission import (
    _REVISION_LINE,
    P347FormalState,
    _canonical_json,
    _only_keys,
    _relative_repo_path,
    _safe_repo_dir,
    _safe_repo_file,
    _sha256_bytes,
    _strict_list,
    _strict_object,
    _strict_string,
    discover_migration_head,
    resolve_feature_gates,
)
from omnibase.production.phase5_registry_contract import (
    _closed_state,
    _strict_digest,
    _strict_positive_int,
    _strict_timestamp,
    _strict_uuid,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_LOGICAL_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_LOGICAL_REF_RE = re.compile(r"^[a-z0-9][a-z0-9_:-]{1,127}$")
# Server-owned ceiling for any single budget value or integer identity field.
_MAX_INT = (1 << 63) - 1

# Server-owned ceilings; a P5.2A contract may only tighten these.
_DEFAULT_BUDGET_CEILINGS = {
    "input_tokens": 50_000_000,
    "output_tokens": 20_000_000,
    "reasoning_tokens": 20_000_000,
    "total_tokens": 80_000_000,
    "cost_micros": 50_000_000,
    "model_calls": 10_000,
    "tool_calls": 5_000,
    "wall_clock_ms": 7_200_000,
    "artifact_bytes": 1_073_741_824,
    "sandbox_jobs": 1_000,
    "max_attempts": 100,
    "max_parallel_steps": 16,
}
_DEFAULT_DEADLINE_CEILING_SECONDS = 7 * 24 * 3600  # 7 days
# P34.4 Run Lease TTL domain is [5, 300] seconds; Task Lease must not outlive it.
_DEFAULT_TASK_LEASE_TTL_CEILING_SECONDS = 300

_FORBIDDEN_LOGICAL_TOKENS = frozenset({"all", "any", "*"})

_TASK_TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})
_ATTEMPT_TERMINAL_STATES = frozenset({"committed", "failed", "unknown", "cancelled"})
_EFFECT_TERMINAL_STATES = frozenset({"committed", "failed", "unknown"})
_AGENT_RUN_TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})

_ALLOWED_TASK_STATES = frozenset(
    {
        "created",
        "planning",
        "awaiting_approval",
        "scheduled",
        "running",
        "paused",
        "blocked_unknown",
        "succeeded",
        "failed",
        "cancelled",
    }
)
_ALLOWED_STEP_STATES = frozenset(
    {"pending", "ready", "running", "succeeded", "failed", "cancelled"}
)
_ALLOWED_ATTEMPT_STATES = frozenset(
    {
        "pending",
        "ready",
        "leased",
        "dispatching",
        "running",
        "committed",
        "failed",
        "unknown",
        "cancelled",
    }
)
_ALLOWED_EFFECT_STATES = frozenset({"reserved", "dispatching", "committed", "failed", "unknown"})
_ALLOWED_AGENT_RUN_STATES = frozenset(
    {"created", "leased", "running", "paused", "succeeded", "failed", "cancelled"}
)
_ALLOWED_LEASE_STATES = frozenset({"active", "expired", "revoked", "completed"})
_ALLOWED_BUDGET_DIMENSIONS = frozenset(_DEFAULT_BUDGET_CEILINGS)
_ALLOWED_COMMITTED_EVIDENCE_KINDS = frozenset({"operation_ledger", "effect_ledger", "audit_event"})

_IDENTITY_FIELD_UNIVERSE = frozenset(
    {
        "tenant_id",
        "workspace_id",
        "workspace_generation",
        "actor_user_id",
        "agent_definition_id",
        "agent_version_id",
        "agent_version_digest",
        "workspace_agent_binding_id",
        "task_id",
        "task_generation",
        "plan_id",
        "plan_version",
        "plan_digest",
        "step_id",
        "attempt_id",
        "attempt_number",
        "workspace_run_id",
        "runtime_instance_id",
        "workload_identity_thumbprint",
        "node_id",
        "node_fencing_token",
        "run_lease_id",
        "run_fencing_token",
        "task_lease_id",
        "task_fencing_token",
        "operation_id",
        "request_hash",
        "deadline",
        "lease_expiry",
        "resource_scope_digest",
        "budget_policy_digest",
        "expected_previous_state",
        "cancellation_target",
        "reconciliation_target",
        "effect_id",
        "checkpoint_id",
    }
)

_ALLOWED_IDENTITY_STAGES = frozenset(
    {
        "task_create",
        "task_run_claim",
        "attempt_claim",
        "attempt_heartbeat",
        "attempt_finish",
        "task_cancel",
        "task_pause",
        "task_resume_request",
        "reconciliation_request",
    }
)

_HASH_PROFILE_FIELDS: dict[str, frozenset[str]] = {
    "task_create": frozenset(
        {
            "operation",
            "task_id",
            "tenant_id",
            "workspace_id",
            "workspace_generation",
            "agent_definition_id",
            "agent_version_id",
            "agent_version_digest",
            "workspace_agent_binding_id",
            "resource_scope_digest",
            "budget_policy_digest",
            "deadline",
        }
    ),
    "task_cancel": frozenset(
        {
            "operation",
            "tenant_id",
            "workspace_id",
            "workspace_generation",
            "task_id",
            "task_generation",
            "expected_previous_state",
            "cancellation_target",
        }
    ),
    "task_pause": frozenset(
        {
            "operation",
            "tenant_id",
            "workspace_id",
            "workspace_generation",
            "task_id",
            "task_generation",
            "expected_previous_state",
        }
    ),
    "task_resume_request": frozenset(
        {
            "operation",
            "tenant_id",
            "workspace_id",
            "workspace_generation",
            "task_id",
            "task_generation",
            "expected_previous_state",
        }
    ),
    "attempt_claim": frozenset(
        {
            "operation",
            "tenant_id",
            "workspace_id",
            "workspace_generation",
            "task_id",
            "task_generation",
            "agent_run_id",
            "step_id",
            "attempt_id",
            "attempt_number",
            "expected_previous_state",
            "run_lease_id",
            "run_fencing_token",
            "node_id",
            "node_fencing_token",
            "agent_version_digest",
            "resource_scope_digest",
            "budget_policy_digest",
            "deadline",
        }
    ),
    "attempt_heartbeat": frozenset(
        {
            "operation",
            "tenant_id",
            "workspace_id",
            "workspace_generation",
            "task_id",
            "task_generation",
            "agent_run_id",
            "step_id",
            "attempt_id",
            "attempt_number",
            "run_lease_id",
            "run_fencing_token",
            "node_id",
            "node_fencing_token",
            "task_lease_id",
            "task_fencing_token",
            "agent_version_digest",
            "resource_scope_digest",
            "budget_policy_digest",
        }
    ),
    "attempt_finish": frozenset(
        {
            "operation",
            "tenant_id",
            "workspace_id",
            "workspace_generation",
            "task_id",
            "task_generation",
            "agent_run_id",
            "step_id",
            "attempt_id",
            "attempt_number",
            "run_lease_id",
            "run_fencing_token",
            "node_id",
            "node_fencing_token",
            "task_lease_id",
            "task_fencing_token",
            "agent_version_digest",
            "resource_scope_digest",
            "budget_policy_digest",
            "expected_previous_state",
            "outcome",
            "result_digest",
            "budget_ledger",
        }
    ),
    "reconciliation_request": frozenset(
        {
            "operation",
            "tenant_id",
            "workspace_id",
            "workspace_generation",
            "task_id",
            "task_generation",
            "attempt_id",
            "reconciliation_target",
        }
    ),
}

_TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"planning"}),
    "planning": frozenset({"awaiting_approval", "failed", "cancelled"}),
    "awaiting_approval": frozenset({"scheduled", "cancelled"}),
    "scheduled": frozenset({"running", "paused", "blocked_unknown", "cancelled"}),
    "running": frozenset({"paused", "blocked_unknown", "succeeded", "failed", "cancelled"}),
    "paused": frozenset({"running", "failed", "cancelled"}),
    "blocked_unknown": frozenset({"failed", "cancelled"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}

_STEP_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"ready", "cancelled"}),
    "ready": frozenset({"running", "cancelled"}),
    "running": frozenset({"succeeded", "failed", "cancelled"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}

_ATTEMPT_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"ready", "cancelled"}),
    "ready": frozenset({"leased", "cancelled"}),
    "leased": frozenset({"dispatching", "failed", "cancelled"}),
    "dispatching": frozenset({"running", "committed", "failed", "unknown"}),
    "running": frozenset({"committed", "failed", "unknown", "cancelled"}),
    "committed": frozenset(),
    "failed": frozenset(),
    "unknown": frozenset(),
    "cancelled": frozenset(),
}

_EFFECT_TRANSITIONS: dict[str, frozenset[str]] = {
    "reserved": frozenset({"dispatching", "failed", "unknown"}),
    "dispatching": frozenset({"committed", "failed", "unknown"}),
    "committed": frozenset(),
    "failed": frozenset(),
    "unknown": frozenset(),
}

_AGENT_RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"leased"}),
    "leased": frozenset({"running", "failed", "cancelled"}),
    "running": frozenset({"paused", "succeeded", "failed", "cancelled"}),
    "paused": frozenset({"running", "failed", "cancelled"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


class TaskLedgerContractError(ConfigurationError):
    """A P5.2A task ledger contract is unsafe, malformed or drifted."""


class TaskState(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    BLOCKED_UNKNOWN = "blocked_unknown"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepState(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AttemptState(StrEnum):
    PENDING = "pending"
    READY = "ready"
    LEASED = "leased"
    DISPATCHING = "dispatching"
    RUNNING = "running"
    COMMITTED = "committed"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


class EffectState(StrEnum):
    RESERVED = "reserved"
    DISPATCHING = "dispatching"
    COMMITTED = "committed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class AgentRunState(StrEnum):
    CREATED = "created"
    LEASED = "leased"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LeaseState(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    COMPLETED = "completed"


class BudgetDimension(StrEnum):
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"
    REASONING_TOKENS = "reasoning_tokens"
    TOTAL_TOKENS = "total_tokens"
    COST_MICROS = "cost_micros"
    MODEL_CALLS = "model_calls"
    TOOL_CALLS = "tool_calls"
    WALL_CLOCK_MS = "wall_clock_ms"
    ARTIFACT_BYTES = "artifact_bytes"
    SANDBOX_JOBS = "sandbox_jobs"
    MAX_ATTEMPTS = "max_attempts"
    MAX_PARALLEL_STEPS = "max_parallel_steps"


class FieldOrigin(StrEnum):
    BROWSER = "browser"
    CORE = "core"
    WORKLOAD = "workload"


class IdentityStage(StrEnum):
    TASK_CREATE = "task_create"
    TASK_RUN_CLAIM = "task_run_claim"
    ATTEMPT_CLAIM = "attempt_claim"
    ATTEMPT_HEARTBEAT = "attempt_heartbeat"
    ATTEMPT_FINISH = "attempt_finish"
    TASK_CANCEL = "task_cancel"
    TASK_PAUSE = "task_pause"
    TASK_RESUME_REQUEST = "task_resume_request"
    RECONCILIATION_REQUEST = "reconciliation_request"


class CommittedEvidenceKind(StrEnum):
    OPERATION_LEDGER = "operation_ledger"
    EFFECT_LEDGER = "effect_ledger"
    AUDIT_EVENT = "audit_event"
    MODEL_OUTPUT = "model_output"
    PROVIDER_RECEIPT = "provider_receipt"


class ReplayClass(StrEnum):
    NOT_A_REPLAY = "not_a_replay"
    EXACT_REPLAY = "exact_replay"
    STABLE_CONFLICT = "stable_conflict"


def _parse_utc_timestamp(text: str, *, name: str) -> datetime:
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise TaskLedgerContractError(f"{name} must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TaskLedgerContractError(f"{name} must be an ISO-8601 UTC timestamp")
    return parsed.astimezone(UTC)


def _strict_non_negative_int(value: object, *, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise TaskLedgerContractError(f"{name} must be a non-negative integer")
    if value > _MAX_INT:
        raise TaskLedgerContractError(f"{name} exceeds the maximum integer")
    return value


def _strict_logical_ref(value: object, *, name: str) -> str:
    text = _strict_string(value, name=name)
    if _LOGICAL_REF_RE.fullmatch(text) is None:
        raise TaskLedgerContractError(
            f"{name} must be a plain logical reference without wildcards or path tricks"
        )
    lowered = text.lower()
    if any(token in lowered for token in ("..", "%", "\\", "?", "#")) or any(
        text.split(":", 1)[0] == token for token in _FORBIDDEN_LOGICAL_TOKENS
    ):
        raise TaskLedgerContractError(
            f"{name} must be a plain logical reference without wildcards or path tricks"
        )
    return text


def _closed_field_set(value: object, *, name: str, universe: frozenset[str]) -> frozenset[str]:
    fields = frozenset(
        _strict_string(item, name=f"{name}[]") for item in _strict_list(value, name=name)
    )
    unknown = sorted(fields - universe)
    if unknown:
        raise TaskLedgerContractError(
            f"{name} references unknown identity fields: {', '.join(unknown)}"
        )
    return fields


# ---------------------------------------------------------------------------
# State machine validators
# ---------------------------------------------------------------------------


def _validate_transition(
    current: str, target: str, *, table: Mapping[str, frozenset[str]], kind: str
) -> None:
    allowed = table.get(current)
    if allowed is None:
        raise TaskLedgerContractError(f"{kind} state {current!r} is not a closed-set state")
    if target not in allowed:
        if not allowed:
            raise TaskLedgerContractError(
                f"terminal {kind} state cannot transition: {current} -> {target}"
            )
        raise TaskLedgerContractError(
            f"{kind} state transition is not allowed: {current} -> {target}"
        )


def validate_task_transition(current: TaskState, target: TaskState) -> None:
    """Reject terminal Task state resurrection and unknown transitions."""
    _validate_transition(current.value, target.value, table=_TASK_TRANSITIONS, kind="task")


def validate_step_transition(current: StepState, target: StepState) -> None:
    _validate_transition(current.value, target.value, table=_STEP_TRANSITIONS, kind="step")


def validate_attempt_transition(current: AttemptState, target: AttemptState) -> None:
    """Terminal Attempts never resume; ``unknown`` is never replayed."""
    if current is AttemptState.UNKNOWN:
        raise TaskLedgerContractError(
            "unknown attempt state cannot be replayed; reconciliation requires a new attempt"
        )
    _validate_transition(current.value, target.value, table=_ATTEMPT_TRANSITIONS, kind="attempt")


def validate_effect_transition(current: EffectState, target: EffectState) -> None:
    if current is EffectState.UNKNOWN:
        raise TaskLedgerContractError(
            "unknown effect state cannot be replayed; reconciliation is required"
        )
    _validate_transition(current.value, target.value, table=_EFFECT_TRANSITIONS, kind="effect")


def validate_agent_run_transition(current: AgentRunState, target: AgentRunState) -> None:
    _validate_transition(
        current.value, target.value, table=_AGENT_RUN_TRANSITIONS, kind="agent run"
    )


def validate_retry(
    *,
    previous_attempt_number: int,
    previous_task_fencing: int,
    new_attempt_number: int,
    new_task_fencing: int,
) -> None:
    """A retry always creates a new Attempt with a strictly higher Task fencing."""
    if new_attempt_number <= previous_attempt_number:
        raise TaskLedgerContractError("retry attempt number must increase monotonically")
    if new_task_fencing <= previous_task_fencing:
        raise TaskLedgerContractError("retry task fencing must increase monotonically")


def validate_cancel_target(state: AttemptState) -> None:
    """Cancel only blocks new dispatch; crossed provider boundaries reconcile."""
    if state in (AttemptState.DISPATCHING, AttemptState.RUNNING):
        raise TaskLedgerContractError(
            "cancel cannot confirm a dispatched attempt; the attempt enters reconciliation"
        )


def validate_cancel_attempt(*, state: AttemptState, has_unknown_effect: bool) -> None:
    """Cancel must never disguise an unknown provider outcome as cancelled success."""
    if has_unknown_effect:
        raise TaskLedgerContractError(
            "cancellation cannot disguise an unknown provider outcome; "
            "the attempt enters reconciliation"
        )
    validate_cancel_target(state)


def validate_committed_evidence(kind: CommittedEvidenceKind) -> None:
    """Only durable ledger/audit records are committed evidence, never model output."""
    if kind is CommittedEvidenceKind.MODEL_OUTPUT:
        raise TaskLedgerContractError("model output is not authoritative committed evidence")
    if kind is CommittedEvidenceKind.PROVIDER_RECEIPT:
        raise TaskLedgerContractError("a provider receipt alone is not committed evidence")
    if kind.value not in _ALLOWED_COMMITTED_EVIDENCE_KINDS:
        raise TaskLedgerContractError(f"unknown committed evidence kind: {kind.value}")


def validate_identity_restart(
    *,
    new_lease_id: str,
    new_runtime_instance_id: str | None,
    new_workload_thumbprint: str | None,
    previous_lease_id: str | None = None,
    previous_runtime_instance_id: str | None = None,
    previous_workload_thumbprint: str | None = None,
) -> None:
    """Checkpoint/resume always creates new lease and runtime/workload identities."""
    if previous_lease_id is not None and new_lease_id == previous_lease_id:
        raise TaskLedgerContractError(
            "resume must create a new task lease, never restore the old lease"
        )
    if (
        previous_runtime_instance_id is not None
        and new_runtime_instance_id == previous_runtime_instance_id
    ):
        raise TaskLedgerContractError(
            "resume must create a new runtime identity, never restore the old runtime"
        )
    if (
        previous_workload_thumbprint is not None
        and new_workload_thumbprint == previous_workload_thumbprint
    ):
        raise TaskLedgerContractError(
            "resume must create a new workload identity, never restore the old workload identity"
        )


# ---------------------------------------------------------------------------
# Canonical hash profiles
# ---------------------------------------------------------------------------


def hash_payload_for_profile(profile: str, payload: Mapping[str, object]) -> dict[str, object]:
    """Validate one closed hash profile payload and return its canonical field set."""
    allowed = _HASH_PROFILE_FIELDS.get(profile)
    if allowed is None:
        raise TaskLedgerContractError(f"unknown hash profile: {profile}")
    data = _strict_object(payload, name=f"hash_payload[{profile}]")
    missing = sorted(allowed - set(data))
    if missing:
        raise TaskLedgerContractError(
            f"hash profile {profile} is missing fields: {', '.join(missing)}"
        )
    unexpected = sorted(set(data) - allowed)
    if unexpected:
        raise TaskLedgerContractError(
            f"hash profile {profile} has unexpected fields: {', '.join(unexpected)}"
        )
    return data


def compute_request_hash(profile: str, payload: Mapping[str, object]) -> str:
    """Compute the canonical SHA-256 for one closed hash profile.

    The payload must contain every stable field of the profile and nothing
    else.  Server timestamps and per-request random UUIDs are excluded by the
    profile field sets, so an exact replay produces the identical digest.
    """
    canonical = hash_payload_for_profile(profile, payload)
    return _sha256_bytes(_canonical_json(canonical))


def validate_request_hash(profile: str, payload: Mapping[str, object], declared: str) -> None:
    """Reject any caller-provided digest that drifts from the canonical profile."""
    computed = compute_request_hash(profile, payload)
    if _SHA256_RE.fullmatch(declared) is None:
        raise TaskLedgerContractError("request_hash must be a lowercase 64-character SHA-256")
    if declared != computed:
        raise TaskLedgerContractError(
            "request_hash does not match the canonical hash profile; caller-provided "
            "digests are never accepted"
        )


def classify_replay(
    *,
    same_idempotency_key: bool,
    same_operation: bool,
    same_payload_digest: bool,
) -> ReplayClass:
    """Classify an incoming request against the recorded idempotency fact."""
    if not same_idempotency_key:
        return ReplayClass.NOT_A_REPLAY
    if same_operation and same_payload_digest:
        return ReplayClass.EXACT_REPLAY
    raise TaskLedgerContractError(
        "idempotency key was reused with a different operation or payload (stable conflict)"
    )


# ---------------------------------------------------------------------------
# Budget contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BudgetDimensionLedger:
    dimension: BudgetDimension
    limit: int
    reserved: int
    committed: int
    released: int

    @classmethod
    def from_mapping(
        cls, value: object, *, name: str, ceilings: Mapping[str, int]
    ) -> BudgetDimensionLedger:
        data = _strict_object(value, name=name)
        _only_keys(data, {"dimension", "limit", "reserved", "committed", "released"}, name=name)
        dimension_text = _strict_string(data.get("dimension"), name=f"{name}.dimension")
        if dimension_text not in _ALLOWED_BUDGET_DIMENSIONS:
            raise TaskLedgerContractError(
                f"{name}.dimension is an unknown budget dimension: {dimension_text}"
            )
        limit = _strict_positive_int(data.get("limit"), name=f"{name}.limit")
        if limit > ceilings[dimension_text]:
            raise TaskLedgerContractError(
                f"{name}.limit exceeds the server-owned ceiling for {dimension_text}"
            )
        reserved = _strict_non_negative_int(data.get("reserved"), name=f"{name}.reserved")
        committed = _strict_non_negative_int(data.get("committed"), name=f"{name}.committed")
        released = _strict_non_negative_int(data.get("released"), name=f"{name}.released")
        if reserved < committed:
            raise TaskLedgerContractError(f"{name}.reserved must not be less than {name}.committed")
        if reserved > limit:
            raise TaskLedgerContractError(f"{name}.reserved must not exceed {name}.limit")
        if released > committed:
            raise TaskLedgerContractError(f"{name}.released must not exceed {name}.committed")
        return cls(
            dimension=BudgetDimension(dimension_text),
            limit=limit,
            reserved=reserved,
            committed=committed,
            released=released,
        )

    @property
    def remaining(self) -> int:
        return self.limit - self.reserved

    def to_dict(self) -> dict[str, object]:
        return {
            "dimension": self.dimension.value,
            "limit": self.limit,
            "reserved": self.reserved,
            "committed": self.committed,
            "released": self.released,
            "remaining": self.remaining,
        }


@dataclass(frozen=True, slots=True)
class BudgetLedgerSnapshot:
    schema_version: int
    dimensions: tuple[BudgetDimensionLedger, ...]
    policy_digest: str

    @classmethod
    def from_mapping(cls, value: object, *, ceilings: Mapping[str, int]) -> BudgetLedgerSnapshot:
        data = _strict_object(value, name="budget_ledger")
        _only_keys(data, {"schema_version", "dimensions", "policy_digest"}, name="budget_ledger")
        if data.get("schema_version") != 1:
            raise TaskLedgerContractError("budget_ledger.schema_version must be 1")
        dimensions = tuple(
            BudgetDimensionLedger.from_mapping(
                item, name="budget_ledger.dimensions[]", ceilings=ceilings
            )
            for item in _strict_list(data.get("dimensions"), name="budget_ledger.dimensions")
        )
        if len(dimensions) != len(_ALLOWED_BUDGET_DIMENSIONS) or {
            item.dimension for item in dimensions
        } != {BudgetDimension(item) for item in _ALLOWED_BUDGET_DIMENSIONS}:
            raise TaskLedgerContractError(
                "budget_ledger.dimensions must cover the closed set of budget dimensions exactly once"
            )
        return cls(
            schema_version=1,
            dimensions=dimensions,
            policy_digest=_strict_digest(
                data.get("policy_digest"), name="budget_ledger.policy_digest"
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "dimensions": [item.to_dict() for item in self.dimensions],
            "policy_digest": self.policy_digest,
        }


# ---------------------------------------------------------------------------
# Identity DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentTaskInvocation:
    """The AgentTask / Invocation identity; ``invocation_id`` aliases ``task_id``."""

    schema_version: int
    task_id: str
    tenant_id: str
    workspace_id: str
    workspace_generation: int
    actor_user_id: str
    agent_definition_id: str
    agent_version_id: str
    agent_version_digest: str
    workspace_agent_binding_id: str
    task_generation: int
    plan_id: str | None
    plan_version: str | None
    plan_digest: str | None
    deadline: str
    state: TaskState
    resource_scope_digest: str
    budget_policy_digest: str
    request_hash: str
    created_by: str
    created_at: str

    @classmethod
    def from_mapping(
        cls, value: object, *, deadline_ceiling_seconds: int | None = None
    ) -> AgentTaskInvocation:
        data = _strict_object(value, name="task")
        _only_keys(
            data,
            {
                "schema_version",
                "task_id",
                "invocation_id",
                "tenant_id",
                "workspace_id",
                "workspace_generation",
                "actor_user_id",
                "agent_definition_id",
                "agent_version_id",
                "agent_version_digest",
                "workspace_agent_binding_id",
                "task_generation",
                "plan_id",
                "plan_version",
                "plan_digest",
                "deadline",
                "state",
                "resource_scope_digest",
                "budget_policy_digest",
                "request_hash",
                "created_by",
                "created_at",
            },
            name="task",
        )
        if data.get("schema_version") != 1:
            raise TaskLedgerContractError("task.schema_version must be 1")
        if ("task_id" in data) == ("invocation_id" in data):
            raise TaskLedgerContractError(
                "task must provide exactly one of task_id or invocation_id"
            )
        task_id = _strict_uuid(data.get("task_id", data.get("invocation_id")), name="task.task_id")
        plan_id = data.get("plan_id")
        plan_version = data.get("plan_version")
        plan_digest = data.get("plan_digest")
        if plan_id is not None:
            plan_id = _strict_uuid(plan_id, name="task.plan_id")
        if plan_version is not None:
            plan_version = _strict_string(plan_version, name="task.plan_version")
        if plan_digest is not None:
            plan_digest = _strict_digest(plan_digest, name="task.plan_digest")
        provided_plan_fields = sum(
            field is not None for field in (plan_id, plan_version, plan_digest)
        )
        if provided_plan_fields not in (0, 3):
            raise TaskLedgerContractError(
                "task plan identity fields must be provided together or not at all"
            )
        task = cls(
            schema_version=1,
            task_id=task_id,
            tenant_id=_strict_uuid(data.get("tenant_id"), name="task.tenant_id"),
            workspace_id=_strict_uuid(data.get("workspace_id"), name="task.workspace_id"),
            workspace_generation=_strict_positive_int(
                data.get("workspace_generation"), name="task.workspace_generation"
            ),
            actor_user_id=_strict_uuid(data.get("actor_user_id"), name="task.actor_user_id"),
            agent_definition_id=_strict_uuid(
                data.get("agent_definition_id"), name="task.agent_definition_id"
            ),
            agent_version_id=_strict_uuid(
                data.get("agent_version_id"), name="task.agent_version_id"
            ),
            agent_version_digest=_strict_digest(
                data.get("agent_version_digest"), name="task.agent_version_digest"
            ),
            workspace_agent_binding_id=_strict_uuid(
                data.get("workspace_agent_binding_id"),
                name="task.workspace_agent_binding_id",
            ),
            task_generation=_strict_positive_int(
                data.get("task_generation"), name="task.task_generation"
            ),
            plan_id=plan_id,
            plan_version=plan_version,
            plan_digest=plan_digest,
            deadline=_strict_timestamp(data.get("deadline"), name="task.deadline"),
            state=TaskState(
                _closed_state(data.get("state"), name="task.state", allowed=_ALLOWED_TASK_STATES)
            ),
            resource_scope_digest=_strict_digest(
                data.get("resource_scope_digest"), name="task.resource_scope_digest"
            ),
            budget_policy_digest=_strict_digest(
                data.get("budget_policy_digest"), name="task.budget_policy_digest"
            ),
            request_hash=_strict_digest(data.get("request_hash"), name="task.request_hash"),
            created_by=_strict_uuid(data.get("created_by"), name="task.created_by"),
            created_at=_strict_timestamp(data.get("created_at"), name="task.created_at"),
        )
        task._verify_create_request_hash()
        task._validate_deadline_ceiling(deadline_ceiling_seconds)
        return task

    def _verify_create_request_hash(self) -> None:
        payload = {
            "operation": "agent.task.create",
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "workspace_generation": self.workspace_generation,
            "agent_definition_id": self.agent_definition_id,
            "agent_version_id": self.agent_version_id,
            "agent_version_digest": self.agent_version_digest,
            "workspace_agent_binding_id": self.workspace_agent_binding_id,
            "resource_scope_digest": self.resource_scope_digest,
            "budget_policy_digest": self.budget_policy_digest,
            "deadline": self.deadline,
        }
        validate_request_hash("task_create", payload, self.request_hash)

    def _validate_deadline_ceiling(self, ceiling_seconds: int | None = None) -> None:
        ceiling = ceiling_seconds or _DEFAULT_DEADLINE_CEILING_SECONDS
        created = _parse_utc_timestamp(self.created_at, name="task.created_at")
        deadline = _parse_utc_timestamp(self.deadline, name="task.deadline")
        if deadline <= created:
            raise TaskLedgerContractError("task.deadline must be after task.created_at")
        if (deadline - created).total_seconds() > ceiling:
            raise TaskLedgerContractError(
                f"task deadline exceeds the configured ceiling of {ceiling} seconds"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "workspace_generation": self.workspace_generation,
            "actor_user_id": self.actor_user_id,
            "agent_definition_id": self.agent_definition_id,
            "agent_version_id": self.agent_version_id,
            "agent_version_digest": self.agent_version_digest,
            "workspace_agent_binding_id": self.workspace_agent_binding_id,
            "task_generation": self.task_generation,
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "plan_digest": self.plan_digest,
            "deadline": self.deadline,
            "state": self.state.value,
            "resource_scope_digest": self.resource_scope_digest,
            "budget_policy_digest": self.budget_policy_digest,
            "request_hash": self.request_hash,
            "created_by": self.created_by,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class AgentRunBinding:
    """The AgentRun binding to an existing P34.4 Workspace Run identity."""

    schema_version: int
    agent_run_id: str
    task_id: str
    tenant_id: str
    workspace_id: str
    workspace_generation: int
    workspace_run_id: str
    runtime_instance_id: str | None
    workload_identity_thumbprint: str | None
    node_id: str | None
    node_fencing_token: int | None
    run_lease_id: str | None
    run_fencing_token: int | None
    state: AgentRunState
    created_at: str

    @classmethod
    def from_mapping(cls, value: object) -> AgentRunBinding:
        data = _strict_object(value, name="run")
        _only_keys(
            data,
            {
                "schema_version",
                "agent_run_id",
                "task_id",
                "tenant_id",
                "workspace_id",
                "workspace_generation",
                "workspace_run_id",
                "runtime_instance_id",
                "workload_identity_thumbprint",
                "node_id",
                "node_fencing_token",
                "run_lease_id",
                "run_fencing_token",
                "state",
                "created_at",
            },
            name="run",
        )
        if data.get("schema_version") != 1:
            raise TaskLedgerContractError("run.schema_version must be 1")
        runtime_instance_id = data.get("runtime_instance_id")
        if runtime_instance_id is not None:
            runtime_instance_id = _strict_uuid(runtime_instance_id, name="run.runtime_instance_id")
        workload_thumbprint = data.get("workload_identity_thumbprint")
        if workload_thumbprint is not None:
            workload_thumbprint = _strict_digest(
                workload_thumbprint, name="run.workload_identity_thumbprint"
            )
        node_id = data.get("node_id")
        if node_id is not None:
            node_id = _strict_uuid(node_id, name="run.node_id")
        run_lease_id = data.get("run_lease_id")
        if run_lease_id is not None:
            run_lease_id = _strict_uuid(run_lease_id, name="run.run_lease_id")
        node_fencing = data.get("node_fencing_token")
        run_fencing = data.get("run_fencing_token")
        if node_fencing is not None:
            node_fencing = _strict_positive_int(node_fencing, name="run.node_fencing_token")
        if run_fencing is not None:
            run_fencing = _strict_positive_int(run_fencing, name="run.run_fencing_token")
        state = AgentRunState(
            _closed_state(data.get("state"), name="run.state", allowed=_ALLOWED_AGENT_RUN_STATES)
        )
        run = cls(
            schema_version=1,
            agent_run_id=_strict_uuid(data.get("agent_run_id"), name="run.agent_run_id"),
            task_id=_strict_uuid(data.get("task_id"), name="run.task_id"),
            tenant_id=_strict_uuid(data.get("tenant_id"), name="run.tenant_id"),
            workspace_id=_strict_uuid(data.get("workspace_id"), name="run.workspace_id"),
            workspace_generation=_strict_positive_int(
                data.get("workspace_generation"), name="run.workspace_generation"
            ),
            workspace_run_id=_strict_uuid(
                data.get("workspace_run_id"), name="run.workspace_run_id"
            ),
            runtime_instance_id=runtime_instance_id,
            workload_identity_thumbprint=workload_thumbprint,
            node_id=node_id,
            node_fencing_token=node_fencing,
            run_lease_id=run_lease_id,
            run_fencing_token=run_fencing,
            state=state,
            created_at=_strict_timestamp(data.get("created_at"), name="run.created_at"),
        )
        run._validate_identity_binding()
        return run

    def _validate_identity_binding(self) -> None:
        # The four run-binding fields form one strict group and the two
        # runtime/workload identity fields form another.  Within each group
        # all fields are present together or all are absent; the group is
        # required exactly while the AgentRun is bound to a live P34.4 Run
        # (leased/running/paused) and absent before binding and after the
        # terminal cleanup.
        binding_fields = (
            self.run_lease_id,
            self.run_fencing_token,
            self.node_id,
            self.node_fencing_token,
        )
        binding_present = all(field is not None for field in binding_fields)
        binding_absent = all(field is None for field in binding_fields)
        if not binding_present and not binding_absent:
            raise TaskLedgerContractError(
                "agent run binding fields (run_lease_id, run_fencing_token, node_id, "
                "node_fencing_token) must be all-or-none"
            )
        identity_fields = (self.runtime_instance_id, self.workload_identity_thumbprint)
        identity_present = all(field is not None for field in identity_fields)
        identity_absent = all(field is None for field in identity_fields)
        if not identity_present and not identity_absent:
            raise TaskLedgerContractError(
                "runtime_instance_id and workload_identity_thumbprint must be provided together"
            )
        if self.state.value in _AGENT_RUN_TERMINAL_STATES:
            if binding_present or identity_present:
                raise TaskLedgerContractError(
                    "terminal agent run must not retain run binding or runtime/workload identity"
                )
            return
        if self.state is AgentRunState.CREATED:
            if binding_present or identity_present:
                raise TaskLedgerContractError(
                    "created agent run must not carry run binding or runtime/workload identity"
                )
            return
        if not binding_present or not identity_present:
            raise TaskLedgerContractError(
                "leased, running and paused agent runs require the full run binding group "
                "and runtime/workload identity"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "agent_run_id": self.agent_run_id,
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "workspace_generation": self.workspace_generation,
            "workspace_run_id": self.workspace_run_id,
            "runtime_instance_id": self.runtime_instance_id,
            "workload_identity_thumbprint": self.workload_identity_thumbprint,
            "node_id": self.node_id,
            "node_fencing_token": self.node_fencing_token,
            "run_lease_id": self.run_lease_id,
            "run_fencing_token": self.run_fencing_token,
            "state": self.state.value,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class AgentStep:
    schema_version: int
    step_id: str
    task_id: str
    agent_run_id: str
    step_number: int
    plan_id: str
    plan_version: str
    plan_digest: str
    dependencies: tuple[str, ...]
    state: StepState
    created_at: str

    @classmethod
    def from_mapping(cls, value: object) -> AgentStep:
        data = _strict_object(value, name="step")
        _only_keys(
            data,
            {
                "schema_version",
                "step_id",
                "task_id",
                "agent_run_id",
                "step_number",
                "plan_id",
                "plan_version",
                "plan_digest",
                "dependencies",
                "state",
                "created_at",
            },
            name="step",
        )
        if data.get("schema_version") != 1:
            raise TaskLedgerContractError("step.schema_version must be 1")
        step_id = _strict_uuid(data.get("step_id"), name="step.step_id")
        dependencies = tuple(
            _strict_logical_ref(item, name="step.dependencies[]")
            for item in _strict_list(data.get("dependencies"), name="step.dependencies")
        )
        if len(dependencies) != len(set(dependencies)):
            raise TaskLedgerContractError("step.dependencies must not contain duplicates")
        if step_id in dependencies:
            raise TaskLedgerContractError("step.dependencies must not reference the step itself")
        return cls(
            schema_version=1,
            step_id=step_id,
            task_id=_strict_uuid(data.get("task_id"), name="step.task_id"),
            agent_run_id=_strict_uuid(data.get("agent_run_id"), name="step.agent_run_id"),
            step_number=_strict_positive_int(data.get("step_number"), name="step.step_number"),
            plan_id=_strict_uuid(data.get("plan_id"), name="step.plan_id"),
            plan_version=_strict_string(data.get("plan_version"), name="step.plan_version"),
            plan_digest=_strict_digest(data.get("plan_digest"), name="step.plan_digest"),
            dependencies=dependencies,
            state=StepState(
                _closed_state(data.get("state"), name="step.state", allowed=_ALLOWED_STEP_STATES)
            ),
            created_at=_strict_timestamp(data.get("created_at"), name="step.created_at"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "step_id": self.step_id,
            "task_id": self.task_id,
            "agent_run_id": self.agent_run_id,
            "step_number": self.step_number,
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "plan_digest": self.plan_digest,
            "dependencies": list(self.dependencies),
            "state": self.state.value,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class AgentAttempt:
    schema_version: int
    attempt_id: str
    task_id: str
    step_id: str
    agent_run_id: str
    attempt_number: int
    state: AttemptState
    task_lease_id: str | None
    task_fencing_token: int | None
    expected_previous_state: TaskState
    deadline: str
    created_at: str

    @classmethod
    def from_mapping(cls, value: object) -> AgentAttempt:
        data = _strict_object(value, name="attempt")
        _only_keys(
            data,
            {
                "schema_version",
                "attempt_id",
                "task_id",
                "step_id",
                "agent_run_id",
                "attempt_number",
                "state",
                "task_lease_id",
                "task_fencing_token",
                "expected_previous_state",
                "deadline",
                "created_at",
            },
            name="attempt",
        )
        if data.get("schema_version") != 1:
            raise TaskLedgerContractError("attempt.schema_version must be 1")
        task_lease_id = data.get("task_lease_id")
        if task_lease_id is not None:
            task_lease_id = _strict_uuid(task_lease_id, name="attempt.task_lease_id")
        task_fencing = data.get("task_fencing_token")
        if task_fencing is not None:
            task_fencing = _strict_positive_int(task_fencing, name="attempt.task_fencing_token")
        if (task_lease_id is None) != (task_fencing is None):
            raise TaskLedgerContractError(
                "attempt.task_lease_id and attempt.task_fencing_token must be provided together"
            )
        attempt = cls(
            schema_version=1,
            attempt_id=_strict_uuid(data.get("attempt_id"), name="attempt.attempt_id"),
            task_id=_strict_uuid(data.get("task_id"), name="attempt.task_id"),
            step_id=_strict_uuid(data.get("step_id"), name="attempt.step_id"),
            agent_run_id=_strict_uuid(data.get("agent_run_id"), name="attempt.agent_run_id"),
            attempt_number=_strict_positive_int(
                data.get("attempt_number"), name="attempt.attempt_number"
            ),
            state=AttemptState(
                _closed_state(
                    data.get("state"), name="attempt.state", allowed=_ALLOWED_ATTEMPT_STATES
                )
            ),
            task_lease_id=task_lease_id,
            task_fencing_token=task_fencing,
            expected_previous_state=TaskState(
                _closed_state(
                    data.get("expected_previous_state"),
                    name="attempt.expected_previous_state",
                    allowed=_ALLOWED_TASK_STATES,
                )
            ),
            deadline=_strict_timestamp(data.get("deadline"), name="attempt.deadline"),
            created_at=_strict_timestamp(data.get("created_at"), name="attempt.created_at"),
        )
        attempt._validate_state_fields()
        return attempt

    def _validate_state_fields(self) -> None:
        if self.state in (AttemptState.PENDING, AttemptState.READY) and (
            self.task_lease_id is not None or self.task_fencing_token is not None
        ):
            raise TaskLedgerContractError(
                "pre-dispatch attempt must not carry a task lease or fencing token"
            )
        if self.state in (AttemptState.LEASED, AttemptState.DISPATCHING, AttemptState.RUNNING) and (
            self.task_lease_id is None or self.task_fencing_token is None
        ):
            raise TaskLedgerContractError(
                "leased, dispatching and running attempts require a task lease and task fencing token"
            )
        if self.state.value in _ATTEMPT_TERMINAL_STATES and (
            self.task_lease_id is not None or self.task_fencing_token is not None
        ):
            raise TaskLedgerContractError(
                "terminal attempt must not retain a task lease or fencing token; "
                "the historical lease record is the immutable reference"
            )
        created = _parse_utc_timestamp(self.created_at, name="attempt.created_at")
        deadline = _parse_utc_timestamp(self.deadline, name="attempt.deadline")
        if deadline <= created:
            raise TaskLedgerContractError("attempt.deadline must be after attempt.created_at")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "attempt_id": self.attempt_id,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "agent_run_id": self.agent_run_id,
            "attempt_number": self.attempt_number,
            "state": self.state.value,
            "task_lease_id": self.task_lease_id,
            "task_fencing_token": self.task_fencing_token,
            "expected_previous_state": self.expected_previous_state.value,
            "deadline": self.deadline,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class TaskLeaseContract:
    """The Task Lease: independent of the Run Lease but bounded by it."""

    schema_version: int
    task_lease_id: str
    task_id: str
    attempt_id: str
    agent_run_id: str
    run_lease_id: str
    run_fencing_token: int
    node_id: str
    node_fencing_token: int
    workspace_generation: int
    task_fencing_token: int
    state: LeaseState
    expires_at: str
    heartbeat_at: str | None
    created_at: str

    @classmethod
    def from_mapping(
        cls, value: object, *, task_lease_ttl_ceiling_seconds: int | None = None
    ) -> TaskLeaseContract:
        data = _strict_object(value, name="task_lease")
        _only_keys(
            data,
            {
                "schema_version",
                "task_lease_id",
                "task_id",
                "attempt_id",
                "agent_run_id",
                "run_lease_id",
                "run_fencing_token",
                "node_id",
                "node_fencing_token",
                "workspace_generation",
                "task_fencing_token",
                "state",
                "expires_at",
                "heartbeat_at",
                "created_at",
            },
            name="task_lease",
        )
        if data.get("schema_version") != 1:
            raise TaskLedgerContractError("task_lease.schema_version must be 1")
        heartbeat_at = data.get("heartbeat_at")
        if heartbeat_at is not None:
            heartbeat_at = _strict_timestamp(heartbeat_at, name="task_lease.heartbeat_at")
        lease = cls(
            schema_version=1,
            task_lease_id=_strict_uuid(data.get("task_lease_id"), name="task_lease.task_lease_id"),
            task_id=_strict_uuid(data.get("task_id"), name="task_lease.task_id"),
            attempt_id=_strict_uuid(data.get("attempt_id"), name="task_lease.attempt_id"),
            agent_run_id=_strict_uuid(data.get("agent_run_id"), name="task_lease.agent_run_id"),
            run_lease_id=_strict_uuid(data.get("run_lease_id"), name="task_lease.run_lease_id"),
            run_fencing_token=_strict_positive_int(
                data.get("run_fencing_token"), name="task_lease.run_fencing_token"
            ),
            node_id=_strict_uuid(data.get("node_id"), name="task_lease.node_id"),
            node_fencing_token=_strict_positive_int(
                data.get("node_fencing_token"), name="task_lease.node_fencing_token"
            ),
            workspace_generation=_strict_positive_int(
                data.get("workspace_generation"), name="task_lease.workspace_generation"
            ),
            task_fencing_token=_strict_positive_int(
                data.get("task_fencing_token"), name="task_lease.task_fencing_token"
            ),
            state=LeaseState(
                _closed_state(
                    data.get("state"), name="task_lease.state", allowed=_ALLOWED_LEASE_STATES
                )
            ),
            expires_at=_strict_timestamp(data.get("expires_at"), name="task_lease.expires_at"),
            heartbeat_at=heartbeat_at,
            created_at=_strict_timestamp(data.get("created_at"), name="task_lease.created_at"),
        )
        lease._validate_state_fields(task_lease_ttl_ceiling_seconds)
        return lease

    def _validate_state_fields(self, ttl_ceiling_seconds: int | None = None) -> None:
        if self.state is LeaseState.ACTIVE:
            created = _parse_utc_timestamp(self.created_at, name="task_lease.created_at")
            expires = _parse_utc_timestamp(self.expires_at, name="task_lease.expires_at")
            if expires <= created:
                raise TaskLedgerContractError("active task lease must expire after creation")
            ceiling = ttl_ceiling_seconds or _DEFAULT_TASK_LEASE_TTL_CEILING_SECONDS
            if (expires - created).total_seconds() > ceiling:
                raise TaskLedgerContractError(
                    "task lease TTL exceeds the configured ceiling of " f"{ceiling} seconds"
                )
        if self.state is LeaseState.COMPLETED and self.heartbeat_at is None:
            raise TaskLedgerContractError("completed task lease must record a final heartbeat_at")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "task_lease_id": self.task_lease_id,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "agent_run_id": self.agent_run_id,
            "run_lease_id": self.run_lease_id,
            "run_fencing_token": self.run_fencing_token,
            "node_id": self.node_id,
            "node_fencing_token": self.node_fencing_token,
            "workspace_generation": self.workspace_generation,
            "task_fencing_token": self.task_fencing_token,
            "state": self.state.value,
            "expires_at": self.expires_at,
            "heartbeat_at": self.heartbeat_at,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class LeaseExpiryBounds:
    """The five expiries a Task Lease may never outlive (earliest wins)."""

    task_deadline: str
    run_lease_expiry: str
    node_attestation_expiry: str
    capability_grant_expiry: str
    workspace_policy_expiry: str
    task_lease_expiry: str

    @classmethod
    def from_mapping(cls, value: object) -> LeaseExpiryBounds:
        data = _strict_object(value, name="lease_expiry_bounds")
        _only_keys(
            data,
            {
                "task_deadline",
                "run_lease_expiry",
                "node_attestation_expiry",
                "capability_grant_expiry",
                "workspace_policy_expiry",
                "task_lease_expiry",
            },
            name="lease_expiry_bounds",
        )
        bounds = cls(
            task_deadline=_strict_timestamp(
                data.get("task_deadline"), name="lease_expiry_bounds.task_deadline"
            ),
            run_lease_expiry=_strict_timestamp(
                data.get("run_lease_expiry"), name="lease_expiry_bounds.run_lease_expiry"
            ),
            node_attestation_expiry=_strict_timestamp(
                data.get("node_attestation_expiry"),
                name="lease_expiry_bounds.node_attestation_expiry",
            ),
            capability_grant_expiry=_strict_timestamp(
                data.get("capability_grant_expiry"),
                name="lease_expiry_bounds.capability_grant_expiry",
            ),
            workspace_policy_expiry=_strict_timestamp(
                data.get("workspace_policy_expiry"),
                name="lease_expiry_bounds.workspace_policy_expiry",
            ),
            task_lease_expiry=_strict_timestamp(
                data.get("task_lease_expiry"), name="lease_expiry_bounds.task_lease_expiry"
            ),
        )
        bounds._validate_ttl_bounds()
        return bounds

    def _validate_ttl_bounds(self) -> None:
        bounds = {
            "task deadline": self.task_deadline,
            "run lease expiry": self.run_lease_expiry,
            "node attestation expiry": self.node_attestation_expiry,
            "capability grant expiry": self.capability_grant_expiry,
            "workspace policy expiry": self.workspace_policy_expiry,
        }
        task_lease = _parse_utc_timestamp(self.task_lease_expiry, name="task_lease_expiry")
        for label, raw in bounds.items():
            if _parse_utc_timestamp(raw, name=label) < task_lease:
                raise TaskLedgerContractError(
                    f"task lease expiry must not be later than the {label}"
                )

    def to_dict(self) -> dict[str, str]:
        return {
            "task_deadline": self.task_deadline,
            "run_lease_expiry": self.run_lease_expiry,
            "node_attestation_expiry": self.node_attestation_expiry,
            "capability_grant_expiry": self.capability_grant_expiry,
            "workspace_policy_expiry": self.workspace_policy_expiry,
            "task_lease_expiry": self.task_lease_expiry,
        }


@dataclass(frozen=True, slots=True)
class ProviderEffect:
    """A provider-boundary effect; ``unknown`` is terminal and never replayed."""

    schema_version: int
    effect_id: str
    attempt_id: str
    state: EffectState
    operation_id: str
    request_hash: str
    result_digest: str | None
    created_at: str

    @classmethod
    def from_mapping(cls, value: object) -> ProviderEffect:
        data = _strict_object(value, name="effect")
        _only_keys(
            data,
            {
                "schema_version",
                "effect_id",
                "attempt_id",
                "state",
                "operation_id",
                "request_hash",
                "result_digest",
                "created_at",
            },
            name="effect",
        )
        if data.get("schema_version") != 1:
            raise TaskLedgerContractError("effect.schema_version must be 1")
        result_digest = data.get("result_digest")
        if result_digest is not None:
            result_digest = _strict_digest(result_digest, name="effect.result_digest")
        effect = cls(
            schema_version=1,
            effect_id=_strict_uuid(data.get("effect_id"), name="effect.effect_id"),
            attempt_id=_strict_uuid(data.get("attempt_id"), name="effect.attempt_id"),
            state=EffectState(
                _closed_state(
                    data.get("state"), name="effect.state", allowed=_ALLOWED_EFFECT_STATES
                )
            ),
            operation_id=_strict_uuid(data.get("operation_id"), name="effect.operation_id"),
            request_hash=_strict_digest(data.get("request_hash"), name="effect.request_hash"),
            result_digest=result_digest,
            created_at=_strict_timestamp(data.get("created_at"), name="effect.created_at"),
        )
        effect._validate_state_fields()
        return effect

    def _validate_state_fields(self) -> None:
        if self.state is EffectState.COMMITTED and self.result_digest is None:
            raise TaskLedgerContractError("committed effect requires a committed result_digest")
        if self.state is not EffectState.COMMITTED and self.result_digest is not None:
            raise TaskLedgerContractError("result_digest is only allowed on a committed effect")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "effect_id": self.effect_id,
            "attempt_id": self.attempt_id,
            "state": self.state.value,
            "operation_id": self.operation_id,
            "request_hash": self.request_hash,
            "result_digest": self.result_digest,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class CheckpointReference:
    """A checkpoint references only committed logical state, never runtime state."""

    schema_version: int
    checkpoint_id: str
    task_id: str
    attempt_id: str
    committed_plan_version: str
    committed_plan_digest: str
    committed_attempt_results: tuple[str, ...]
    budget_ledger: BudgetLedgerSnapshot
    created_at: str

    @classmethod
    def from_mapping(cls, value: object, *, ceilings: Mapping[str, int]) -> CheckpointReference:
        data = _strict_object(value, name="checkpoint")
        _only_keys(
            data,
            {
                "schema_version",
                "checkpoint_id",
                "task_id",
                "attempt_id",
                "committed_plan_version",
                "committed_plan_digest",
                "committed_attempt_results",
                "budget_ledger",
                "created_at",
            },
            name="checkpoint",
        )
        if data.get("schema_version") != 1:
            raise TaskLedgerContractError("checkpoint.schema_version must be 1")
        results = tuple(
            _strict_logical_ref(item, name="checkpoint.committed_attempt_results[]")
            for item in _strict_list(
                data.get("committed_attempt_results"),
                name="checkpoint.committed_attempt_results",
            )
        )
        if not results or len(results) != len(set(results)):
            raise TaskLedgerContractError(
                "checkpoint.committed_attempt_results must be non-empty and unique"
            )
        return cls(
            schema_version=1,
            checkpoint_id=_strict_uuid(data.get("checkpoint_id"), name="checkpoint.checkpoint_id"),
            task_id=_strict_uuid(data.get("task_id"), name="checkpoint.task_id"),
            attempt_id=_strict_uuid(data.get("attempt_id"), name="checkpoint.attempt_id"),
            committed_plan_version=_strict_string(
                data.get("committed_plan_version"),
                name="checkpoint.committed_plan_version",
            ),
            committed_plan_digest=_strict_digest(
                data.get("committed_plan_digest"), name="checkpoint.committed_plan_digest"
            ),
            committed_attempt_results=results,
            budget_ledger=BudgetLedgerSnapshot.from_mapping(
                data.get("budget_ledger"), ceilings=ceilings
            ),
            created_at=_strict_timestamp(data.get("created_at"), name="checkpoint.created_at"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "checkpoint_id": self.checkpoint_id,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "committed_plan_version": self.committed_plan_version,
            "committed_plan_digest": self.committed_plan_digest,
            "committed_attempt_results": list(self.committed_attempt_results),
            "budget_ledger": self.budget_ledger.to_dict(),
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class IdentityStageRules:
    """Field availability rules for one lifecycle stage (closed-set table)."""

    stage: IdentityStage
    required_fields: frozenset[str]
    not_yet_generated_fields: frozenset[str]
    immutable_fields: frozenset[str]
    core_generated_fields: frozenset[str]
    browser_submittable_fields: frozenset[str]
    workload_submittable_fields: frozenset[str]
    forbidden_fields: frozenset[str]

    @classmethod
    def from_mapping(cls, value: object) -> IdentityStageRules:
        data = _strict_object(value, name="identity_stages[]")
        _only_keys(
            data,
            {
                "stage",
                "required_fields",
                "not_yet_generated_fields",
                "immutable_fields",
                "core_generated_fields",
                "browser_submittable_fields",
                "workload_submittable_fields",
                "forbidden_fields",
            },
            name="identity_stages[]",
        )
        stage_text = _strict_string(data.get("stage"), name="identity_stages[].stage")
        if stage_text not in _ALLOWED_IDENTITY_STAGES:
            raise TaskLedgerContractError(f"unknown identity stage: {stage_text}")
        required = _closed_field_set(
            data.get("required_fields"),
            name="identity_stages[].required_fields",
            universe=_IDENTITY_FIELD_UNIVERSE,
        )
        not_yet = _closed_field_set(
            data.get("not_yet_generated_fields"),
            name="identity_stages[].not_yet_generated_fields",
            universe=_IDENTITY_FIELD_UNIVERSE,
        )
        immutable = _closed_field_set(
            data.get("immutable_fields"),
            name="identity_stages[].immutable_fields",
            universe=_IDENTITY_FIELD_UNIVERSE,
        )
        core_generated = _closed_field_set(
            data.get("core_generated_fields"),
            name="identity_stages[].core_generated_fields",
            universe=_IDENTITY_FIELD_UNIVERSE,
        )
        browser_submittable = _closed_field_set(
            data.get("browser_submittable_fields"),
            name="identity_stages[].browser_submittable_fields",
            universe=_IDENTITY_FIELD_UNIVERSE,
        )
        workload_submittable = _closed_field_set(
            data.get("workload_submittable_fields"),
            name="identity_stages[].workload_submittable_fields",
            universe=_IDENTITY_FIELD_UNIVERSE,
        )
        forbidden = _closed_field_set(
            data.get("forbidden_fields"),
            name="identity_stages[].forbidden_fields",
            universe=_IDENTITY_FIELD_UNIVERSE,
        )
        rules = cls(
            stage=IdentityStage(stage_text),
            required_fields=required,
            not_yet_generated_fields=not_yet,
            immutable_fields=immutable,
            core_generated_fields=core_generated,
            browser_submittable_fields=browser_submittable,
            workload_submittable_fields=workload_submittable,
            forbidden_fields=forbidden,
        )
        rules._validate_consistency()
        return rules

    def _validate_consistency(self) -> None:
        if self.required_fields & self.not_yet_generated_fields:
            raise TaskLedgerContractError(
                f"identity stage {self.stage.value} marks a required field as not yet generated"
            )
        if self.required_fields & self.forbidden_fields:
            raise TaskLedgerContractError(
                f"identity stage {self.stage.value} marks a required field as forbidden"
            )
        if self.not_yet_generated_fields & self.immutable_fields:
            raise TaskLedgerContractError(
                f"identity stage {self.stage.value} marks a not-yet-generated field as immutable"
            )
        if self.core_generated_fields & (
            self.browser_submittable_fields | self.workload_submittable_fields
        ):
            raise TaskLedgerContractError(
                f"identity stage {self.stage.value} marks a core-generated field as submittable"
            )
        if self.forbidden_fields & (
            self.browser_submittable_fields | self.workload_submittable_fields
        ):
            raise TaskLedgerContractError(
                f"identity stage {self.stage.value} marks a forbidden field as submittable"
            )

    def validate_submission(self, origin: FieldOrigin, submitted: frozenset[str]) -> None:
        """Reject Browser/workload submission of fields the stage reserves for Core."""
        if origin is FieldOrigin.CORE:
            submittable = self.required_fields | self.core_generated_fields
        elif origin is FieldOrigin.BROWSER:
            submittable = self.browser_submittable_fields
        else:
            submittable = self.workload_submittable_fields
        for field in sorted(submitted):
            if field not in _IDENTITY_FIELD_UNIVERSE:
                raise TaskLedgerContractError(f"unknown identity field: {field}")
            if field in self.forbidden_fields:
                raise TaskLedgerContractError(f"{field} is forbidden at {self.stage.value}")
            if origin is not FieldOrigin.CORE and field in self.core_generated_fields:
                raise TaskLedgerContractError(
                    f"{field} is core-generated and must not be submitted by the {origin.value}"
                )
            if field in self.not_yet_generated_fields:
                raise TaskLedgerContractError(f"{field} is not yet generated at {self.stage.value}")
            if field not in self.required_fields and field not in submittable:
                raise TaskLedgerContractError(
                    f"{field} is not submittable by the {origin.value} at {self.stage.value}"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage.value,
            "required_fields": sorted(self.required_fields),
            "not_yet_generated_fields": sorted(self.not_yet_generated_fields),
            "immutable_fields": sorted(self.immutable_fields),
            "core_generated_fields": sorted(self.core_generated_fields),
            "browser_submittable_fields": sorted(self.browser_submittable_fields),
            "workload_submittable_fields": sorted(self.workload_submittable_fields),
            "forbidden_fields": sorted(self.forbidden_fields),
        }


# ---------------------------------------------------------------------------
# Contract configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BudgetCeilings:
    values: tuple[tuple[str, int], ...]

    @classmethod
    def from_mapping(cls, value: object) -> BudgetCeilings:
        data = _strict_object(value, name="budget_ceilings")
        _only_keys(data, set(_DEFAULT_BUDGET_CEILINGS), name="budget_ceilings")
        parsed: list[tuple[str, int]] = []
        for dimension, ceiling in _DEFAULT_BUDGET_CEILINGS.items():
            parsed_value = _strict_positive_int(
                data.get(dimension), name=f"budget_ceilings.{dimension}"
            )
            if parsed_value > ceiling:
                raise TaskLedgerContractError(
                    f"budget_ceilings.{dimension} may only tighten the server-owned ceiling {ceiling}"
                )
            parsed.append((dimension, parsed_value))
        return cls(values=tuple(parsed))

    def as_mapping(self) -> dict[str, int]:
        return dict(self.values)


@dataclass(frozen=True, slots=True)
class LedgerContracts:
    tasks: tuple[AgentTaskInvocation, ...]
    runs: tuple[AgentRunBinding, ...]
    steps: tuple[AgentStep, ...]
    attempts: tuple[AgentAttempt, ...]
    task_leases: tuple[TaskLeaseContract, ...]
    effects: tuple[ProviderEffect, ...]
    checkpoints: tuple[CheckpointReference, ...]
    budget_ledgers: tuple[BudgetLedgerSnapshot, ...]
    lease_expiry_bounds: LeaseExpiryBounds

    @classmethod
    def from_mapping(
        cls,
        value: object,
        *,
        ceilings: Mapping[str, int],
        deadline_ceiling_seconds: int | None = None,
        task_lease_ttl_ceiling_seconds: int | None = None,
    ) -> LedgerContracts:
        data = _strict_object(value, name="ledger_contracts")
        _only_keys(
            data,
            {
                "tasks",
                "runs",
                "steps",
                "attempts",
                "task_leases",
                "effects",
                "checkpoints",
                "budget_ledgers",
                "lease_expiry_bounds",
            },
            name="ledger_contracts",
        )
        tasks = tuple(
            AgentTaskInvocation.from_mapping(
                item, deadline_ceiling_seconds=deadline_ceiling_seconds
            )
            for item in _strict_list(data.get("tasks"), name="ledger_contracts.tasks")
        )
        runs = tuple(
            AgentRunBinding.from_mapping(item)
            for item in _strict_list(data.get("runs"), name="ledger_contracts.runs")
        )
        steps = tuple(
            AgentStep.from_mapping(item)
            for item in _strict_list(data.get("steps"), name="ledger_contracts.steps")
        )
        attempts = tuple(
            AgentAttempt.from_mapping(item)
            for item in _strict_list(data.get("attempts"), name="ledger_contracts.attempts")
        )
        task_leases = tuple(
            TaskLeaseContract.from_mapping(
                item, task_lease_ttl_ceiling_seconds=task_lease_ttl_ceiling_seconds
            )
            for item in _strict_list(data.get("task_leases"), name="ledger_contracts.task_leases")
        )
        effects = tuple(
            ProviderEffect.from_mapping(item)
            for item in _strict_list(data.get("effects"), name="ledger_contracts.effects")
        )
        checkpoints = tuple(
            CheckpointReference.from_mapping(item, ceilings=ceilings)
            for item in _strict_list(data.get("checkpoints"), name="ledger_contracts.checkpoints")
        )
        budget_ledgers = tuple(
            BudgetLedgerSnapshot.from_mapping(item, ceilings=ceilings)
            for item in _strict_list(
                data.get("budget_ledgers"), name="ledger_contracts.budget_ledgers"
            )
        )
        if not tasks or not runs or not steps or not attempts or not task_leases:
            raise TaskLedgerContractError(
                "ledger_contracts must include at least one task, run, step, attempt and task lease"
            )
        return cls(
            tasks=tasks,
            runs=runs,
            steps=steps,
            attempts=attempts,
            task_leases=task_leases,
            effects=effects,
            checkpoints=checkpoints,
            budget_ledgers=budget_ledgers,
            lease_expiry_bounds=LeaseExpiryBounds.from_mapping(data.get("lease_expiry_bounds")),
        )


def _parse_gate_block(value: object) -> dict[str, object]:
    gates = _strict_object(value, name="feature_gates")
    _only_keys(
        gates,
        {"agent_runtime_enabled", "agent_planner_enabled", "multi_agent_enabled"},
        name="feature_gates",
    )
    if (
        gates.get("agent_runtime_enabled") is not False
        or gates.get("agent_planner_enabled") is not False
        or gates.get("multi_agent_enabled") is not False
    ):
        raise TaskLedgerContractError(
            "P5.2A contract requires every Phase 5 feature gate to be disabled"
        )
    return gates


def _parse_upstream_block(
    value: object, *, sub_key: str, name: str
) -> tuple[P347FormalState, str, str]:
    block = _strict_object(value, name=name)
    _only_keys(block, {"formal_state", sub_key}, name=name)
    state = _parse_formal_state(block.get("formal_state"), name=f"{name}.formal_state")
    reference = _strict_object(block.get(sub_key), name=f"{name}.{sub_key}")
    _only_keys(reference, {"path", "sha256"}, name=f"{name}.{sub_key}")
    path = _strict_string(reference.get("path"), name=f"{name}.{sub_key}.path")
    digest = _strict_digest(reference.get("sha256"), name=f"{name}.{sub_key}.sha256")
    return state, path, digest


def _parse_limit_blocks(data: Mapping[str, object]) -> tuple[BudgetCeilings, int, int]:
    ceilings = BudgetCeilings.from_mapping(data.get("budget_ceilings"))
    deadline_ceiling = _strict_positive_int(
        data.get("deadline_ceiling_seconds"), name="deadline_ceiling_seconds"
    )
    if deadline_ceiling > _DEFAULT_DEADLINE_CEILING_SECONDS:
        raise TaskLedgerContractError(
            "deadline_ceiling_seconds may only tighten the server-owned ceiling "
            f"{_DEFAULT_DEADLINE_CEILING_SECONDS}"
        )
    lease_ttl_ceiling = _strict_positive_int(
        data.get("task_lease_ttl_ceiling_seconds"),
        name="task_lease_ttl_ceiling_seconds",
    )
    if lease_ttl_ceiling > _DEFAULT_TASK_LEASE_TTL_CEILING_SECONDS:
        raise TaskLedgerContractError(
            "task_lease_ttl_ceiling_seconds may only tighten the server-owned ceiling "
            f"{_DEFAULT_TASK_LEASE_TTL_CEILING_SECONDS}"
        )
    return ceilings, deadline_ceiling, lease_ttl_ceiling


def _parse_closed_tables(
    data: Mapping[str, object],
) -> tuple[tuple[str, ...], tuple[IdentityStageRules, ...]]:
    hash_profiles = tuple(
        _strict_string(item, name="hash_profiles[]")
        for item in _strict_list(data.get("hash_profiles"), name="hash_profiles")
    )
    if set(hash_profiles) != set(_HASH_PROFILE_FIELDS) or len(hash_profiles) != len(
        _HASH_PROFILE_FIELDS
    ):
        raise TaskLedgerContractError(
            "hash_profiles must cover the closed set of hash profiles exactly once"
        )
    identity_stages = tuple(
        IdentityStageRules.from_mapping(item)
        for item in _strict_list(data.get("identity_stages"), name="identity_stages")
    )
    if {rules.stage for rules in identity_stages} != set(IdentityStage) or len(
        identity_stages
    ) != len(_ALLOWED_IDENTITY_STAGES):
        raise TaskLedgerContractError(
            "identity_stages must cover the closed set of identity stages exactly once"
        )
    return hash_profiles, identity_stages


def _parse_path_lists(data: Mapping[str, object]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    forbidden = tuple(
        _relative_repo_path(item, name="forbidden_source_paths[]")
        for item in _strict_list(data.get("forbidden_source_paths"), name="forbidden_source_paths")
    )
    if not forbidden or len(forbidden) != len(set(forbidden)):
        raise TaskLedgerContractError("forbidden_source_paths must be non-empty and unique")
    baseline = tuple(
        _strict_string(item, name="baseline_migration_revisions[]")
        for item in _strict_list(
            data.get("baseline_migration_revisions"),
            name="baseline_migration_revisions",
        )
    )
    if not baseline or len(baseline) != len(set(baseline)):
        raise TaskLedgerContractError("baseline_migration_revisions must be non-empty and unique")
    return forbidden, baseline


def _parse_sealed_and_openapi(
    data: Mapping[str, object],
) -> tuple[tuple[tuple[str, str, str], ...], str, str]:
    sealed = tuple(
        _parse_sealed_contract(item)
        for item in _strict_list(data.get("sealed_contracts"), name="sealed_contracts")
    )
    if not sealed or len({name for name, _, _ in sealed}) != len(sealed):
        raise TaskLedgerContractError("sealed_contracts must be non-empty with unique names")
    openapi = _strict_object(data.get("openapi_snapshot"), name="openapi_snapshot")
    _only_keys(openapi, {"path", "sha256"}, name="openapi_snapshot")
    return (
        sealed,
        _relative_repo_path(openapi.get("path"), name="openapi_snapshot.path"),
        _strict_digest(openapi.get("sha256"), name="openapi_snapshot.sha256"),
    )


def _parse_evidence_block(data: Mapping[str, object]) -> tuple[EvidenceReference, ...]:
    evidence = tuple(
        EvidenceReference.from_mapping(item)
        for item in _strict_list(data.get("evidence"), name="evidence")
    )
    if not evidence or len({item.evidence_id for item in evidence}) != len(evidence):
        raise TaskLedgerContractError("evidence IDs must be non-empty and unique")
    return evidence


def _parse_critical_block(value: object) -> int:
    critical = _strict_object(value, name="critical_veto")
    _only_keys(critical, {"expected"}, name="critical_veto")
    if critical.get("expected") != 0:
        raise TaskLedgerContractError("critical_veto.expected must be exactly 0")
    return 0


@dataclass(frozen=True, slots=True)
class TaskLedgerContractConfig:
    schema_version: int
    phase: str
    activation_requested: bool
    feature_gates: dict[str, object]
    p34_7_formal_state: P347FormalState
    p34_7_decision: str
    p34_7_decision_sha256: str
    p5_0_formal_state: P347FormalState
    p5_0_admission_path: str
    p5_0_admission_sha256: str
    p5_1_formal_state: P347FormalState
    p5_1_registry_contract_path: str
    p5_1_registry_contract_sha256: str
    source: SourceScope
    evidence: tuple[EvidenceReference, ...]
    ceilings: BudgetCeilings
    deadline_ceiling_seconds: int
    task_lease_ttl_ceiling_seconds: int
    hash_profiles: tuple[str, ...]
    identity_stages: tuple[IdentityStageRules, ...]
    forbidden_source_paths: tuple[str, ...]
    baseline_migration_revisions: tuple[str, ...]
    sealed_contracts: tuple[tuple[str, str, str], ...]
    openapi_snapshot_path: str
    openapi_snapshot_sha256: str
    ledger_contracts: LedgerContracts
    critical_veto: int

    @classmethod
    def from_mapping(cls, value: object) -> TaskLedgerContractConfig:
        data = _strict_object(value, name="configuration")
        _only_keys(
            data,
            {
                "schema_version",
                "phase",
                "activation_requested",
                "feature_gates",
                "p34_7",
                "p5_0",
                "p5_1",
                "source",
                "evidence",
                "budget_ceilings",
                "deadline_ceiling_seconds",
                "task_lease_ttl_ceiling_seconds",
                "hash_profiles",
                "identity_stages",
                "forbidden_source_paths",
                "baseline_migration_revisions",
                "sealed_contracts",
                "openapi_snapshot",
                "ledger_contracts",
                "critical_veto",
            },
            name="configuration",
        )
        if data.get("schema_version") != 1:
            raise TaskLedgerContractError("configuration.schema_version must be 1")
        if data.get("phase") != "P5.2A":
            raise TaskLedgerContractError("configuration.phase must be P5.2A")
        if data.get("activation_requested") is not False:
            raise TaskLedgerContractError(
                "P5.2A contract requires activation_requested to be false"
            )
        gates = _parse_gate_block(data.get("feature_gates"))
        p34_7_state, p34_7_decision, p34_7_sha256 = _parse_upstream_block(
            data.get("p34_7"), sub_key="decision", name="p34_7"
        )
        p5_0_state, p5_0_admission, p5_0_sha256 = _parse_upstream_block(
            data.get("p5_0"), sub_key="admission_contract", name="p5_0"
        )
        p5_1_state, p5_1_registry, p5_1_sha256 = _parse_upstream_block(
            data.get("p5_1"), sub_key="registry_contract", name="p5_1"
        )
        ceilings, deadline_ceiling, lease_ttl_ceiling = _parse_limit_blocks(data)
        hash_profiles, identity_stages = _parse_closed_tables(data)
        forbidden, baseline = _parse_path_lists(data)
        sealed, openapi_path, openapi_sha256 = _parse_sealed_and_openapi(data)
        evidence = _parse_evidence_block(data)
        critical_veto = _parse_critical_block(data.get("critical_veto"))
        ledger = LedgerContracts.from_mapping(
            data.get("ledger_contracts"),
            ceilings=ceilings.as_mapping(),
            deadline_ceiling_seconds=deadline_ceiling,
            task_lease_ttl_ceiling_seconds=lease_ttl_ceiling,
        )
        config = cls(
            schema_version=1,
            phase="P5.2A",
            activation_requested=False,
            feature_gates=gates,
            p34_7_formal_state=p34_7_state,
            p34_7_decision=_relative_repo_path(p34_7_decision, name="p34_7.decision.path"),
            p34_7_decision_sha256=p34_7_sha256,
            p5_0_formal_state=p5_0_state,
            p5_0_admission_path=_relative_repo_path(
                p5_0_admission, name="p5_0.admission_contract.path"
            ),
            p5_0_admission_sha256=p5_0_sha256,
            p5_1_formal_state=p5_1_state,
            p5_1_registry_contract_path=_relative_repo_path(
                p5_1_registry, name="p5_1.registry_contract.path"
            ),
            p5_1_registry_contract_sha256=p5_1_sha256,
            source=SourceScope.from_mapping(data.get("source")),
            evidence=evidence,
            ceilings=ceilings,
            deadline_ceiling_seconds=deadline_ceiling,
            task_lease_ttl_ceiling_seconds=lease_ttl_ceiling,
            hash_profiles=tuple(sorted(hash_profiles)),
            identity_stages=identity_stages,
            forbidden_source_paths=forbidden,
            baseline_migration_revisions=baseline,
            sealed_contracts=sealed,
            openapi_snapshot_path=openapi_path,
            openapi_snapshot_sha256=openapi_sha256,
            ledger_contracts=ledger,
            critical_veto=critical_veto,
        )
        config._validate_ledger_references()
        return config

    def _validate_ledger_references(self) -> None:
        ledger = self.ledger_contracts
        tasks_by_id = {task.task_id: task for task in ledger.tasks}
        runs_by_id = {run.agent_run_id: run for run in ledger.runs}
        steps_by_id = {step.step_id: step for step in ledger.steps}
        attempts_by_id = {attempt.attempt_id: attempt for attempt in ledger.attempts}
        leases_by_id = {lease.task_lease_id: lease for lease in ledger.task_leases}
        if len(tasks_by_id) != len(ledger.tasks):
            raise TaskLedgerContractError("task IDs must be unique")
        if len(runs_by_id) != len(ledger.runs):
            raise TaskLedgerContractError("agent run IDs must be unique")
        if len(steps_by_id) != len(ledger.steps):
            raise TaskLedgerContractError("step IDs must be unique")
        if len(attempts_by_id) != len(ledger.attempts):
            raise TaskLedgerContractError("attempt IDs must be unique")
        if len(leases_by_id) != len(ledger.task_leases):
            raise TaskLedgerContractError("task lease IDs must be unique")

        self._validate_run_references(ledger, tasks_by_id)
        self._validate_step_references(ledger, tasks_by_id, runs_by_id, steps_by_id)
        attempts_by_step = self._validate_attempt_references(
            ledger, tasks_by_id, runs_by_id, steps_by_id, leases_by_id
        )
        self._validate_attempt_ordering(attempts_by_step)
        self._validate_task_fencing_monotonic(ledger.attempts)
        self._validate_task_lease_references(
            ledger, tasks_by_id, attempts_by_id, runs_by_id, leases_by_id
        )
        self._validate_effect_references(ledger, attempts_by_id)
        self._validate_checkpoint_references(ledger, tasks_by_id, attempts_by_id)

    @staticmethod
    def _validate_run_references(
        ledger: LedgerContracts, tasks_by_id: Mapping[str, AgentTaskInvocation]
    ) -> None:
        for run in ledger.runs:
            task = tasks_by_id.get(run.task_id)
            if task is None:
                raise TaskLedgerContractError(
                    f"agent run {run.agent_run_id} references an unknown task {run.task_id}"
                )
            if run.tenant_id != task.tenant_id or run.workspace_id != task.workspace_id:
                raise TaskLedgerContractError(
                    f"agent run {run.agent_run_id} crosses the tenant/workspace boundary of "
                    f"task {run.task_id}"
                )
            if run.workspace_generation != task.workspace_generation:
                raise TaskLedgerContractError(
                    f"agent run {run.agent_run_id} binds a stale workspace generation"
                )

    @staticmethod
    def _validate_step_references(
        ledger: LedgerContracts,
        tasks_by_id: Mapping[str, AgentTaskInvocation],
        runs_by_id: Mapping[str, AgentRunBinding],
        steps_by_id: Mapping[str, AgentStep],
    ) -> None:
        steps_by_task: dict[str, list[AgentStep]] = {}
        for step in ledger.steps:
            bound_task = tasks_by_id.get(step.task_id)
            bound_run = runs_by_id.get(step.agent_run_id)
            if bound_task is None:
                raise TaskLedgerContractError(
                    f"step {step.step_id} references an unknown task {step.task_id}"
                )
            if bound_run is None or bound_run.task_id != step.task_id:
                raise TaskLedgerContractError(
                    f"step {step.step_id} crosses the task/run binding boundary"
                )
            if bound_task.plan_id is None:
                raise TaskLedgerContractError(
                    f"step {step.step_id} requires a task with an immutable plan identity"
                )
            if (
                step.plan_id != bound_task.plan_id
                or step.plan_version != bound_task.plan_version
                or step.plan_digest != bound_task.plan_digest
            ):
                raise TaskLedgerContractError(
                    f"step {step.step_id} plan identity drifts from the task plan identity"
                )
            for dependency in step.dependencies:
                bound_dependency = steps_by_id.get(dependency)
                if bound_dependency is None:
                    raise TaskLedgerContractError(
                        f"step {step.step_id} references an unknown dependency step {dependency}"
                    )
                if (
                    bound_dependency.task_id != step.task_id
                    or bound_dependency.agent_run_id != step.agent_run_id
                ):
                    raise TaskLedgerContractError(
                        f"step {step.step_id} references a cross-task or cross-run "
                        f"dependency step {dependency}"
                    )
            steps_by_task.setdefault(step.task_id, []).append(step)
        for task_steps in steps_by_task.values():
            numbers = [step.step_number for step in task_steps]
            if len(numbers) != len(set(numbers)):
                raise TaskLedgerContractError("step_number values must be unique within the task")
        TaskLedgerContractConfig._validate_step_dag(ledger.steps)

    @staticmethod
    def _validate_step_dag(steps: tuple[AgentStep, ...]) -> None:
        """Reject dependency cycles over the resolved same-task step graph."""
        by_id = {step.step_id: step for step in steps}

        def visit(step: AgentStep, visiting: set[str], visited: set[str]) -> None:
            if step.step_id in visited:
                return
            if step.step_id in visiting:
                raise TaskLedgerContractError(
                    f"step dependency graph contains a cycle at step {step.step_id}"
                )
            visiting.add(step.step_id)
            for dependency in step.dependencies:
                target = by_id.get(dependency)
                if target is not None:
                    visit(target, visiting, visited)
            visiting.remove(step.step_id)
            visited.add(step.step_id)

        visited: set[str] = set()
        for step in steps:
            visit(step, set(), visited)

    @staticmethod
    def _validate_attempt_references(
        ledger: LedgerContracts,
        tasks_by_id: Mapping[str, AgentTaskInvocation],
        runs_by_id: Mapping[str, AgentRunBinding],
        steps_by_id: Mapping[str, AgentStep],
        leases_by_id: Mapping[str, TaskLeaseContract],
    ) -> dict[tuple[str, str], list[AgentAttempt]]:
        attempts_by_step: dict[tuple[str, str], list[AgentAttempt]] = {}
        for attempt in ledger.attempts:
            bound_task = tasks_by_id.get(attempt.task_id)
            bound_run = runs_by_id.get(attempt.agent_run_id)
            bound_step = steps_by_id.get(attempt.step_id)
            if bound_task is None or bound_run is None or bound_step is None:
                raise TaskLedgerContractError(
                    f"attempt {attempt.attempt_id} references an unknown task, run or step"
                )
            if bound_run.task_id != attempt.task_id or bound_step.task_id != attempt.task_id:
                raise TaskLedgerContractError(
                    f"attempt {attempt.attempt_id} crosses the task/run/step binding boundary"
                )
            if bound_step.agent_run_id != attempt.agent_run_id:
                raise TaskLedgerContractError(
                    f"attempt {attempt.attempt_id} binds a step from a different agent run"
                )
            if attempt.task_lease_id is not None:
                bound_lease = leases_by_id.get(attempt.task_lease_id)
                if bound_lease is None:
                    raise TaskLedgerContractError(
                        f"attempt {attempt.attempt_id} references an unknown task lease "
                        f"{attempt.task_lease_id}"
                    )
                if bound_lease.attempt_id != attempt.attempt_id:
                    raise TaskLedgerContractError(
                        f"attempt {attempt.attempt_id} references a task lease bound to a "
                        f"different attempt {bound_lease.attempt_id}"
                    )
                if (
                    bound_lease.task_id != attempt.task_id
                    or bound_lease.agent_run_id != attempt.agent_run_id
                ):
                    raise TaskLedgerContractError(
                        f"attempt {attempt.attempt_id} task lease crosses the task/run "
                        "binding boundary"
                    )
            task_deadline = _parse_utc_timestamp(bound_task.deadline, name="task.deadline")
            attempt_deadline = _parse_utc_timestamp(attempt.deadline, name="attempt.deadline")
            if attempt_deadline > task_deadline:
                raise TaskLedgerContractError(
                    f"attempt {attempt.attempt_id} deadline must not be later than the "
                    "task deadline"
                )
            attempts_by_step.setdefault((attempt.task_id, attempt.step_id), []).append(attempt)
        return attempts_by_step

    @staticmethod
    def _validate_attempt_ordering(
        attempts_by_step: Mapping[tuple[str, str], list[AgentAttempt]],
    ) -> None:
        # attempt_number is a per-(task_id, step_id) sequence: every Step of a
        # Task restarts at 1 and a retry of the same Step increases it.
        for attempts in attempts_by_step.values():
            ordered = sorted(attempts, key=lambda item: item.attempt_number)
            for previous, current in pairwise(ordered):
                validate_retry(
                    previous_attempt_number=previous.attempt_number,
                    previous_task_fencing=previous.task_fencing_token or 0,
                    new_attempt_number=current.attempt_number,
                    new_task_fencing=current.task_fencing_token or 0,
                )

    @staticmethod
    def _validate_task_fencing_monotonic(attempts: tuple[AgentAttempt, ...]) -> None:
        # task_fencing_token is a Task-wide monotonically increasing sequence:
        # every Task Lease claim of the Task (across all Steps) must use a
        # strictly higher token, so an old holder can never resubmit.
        fenced = sorted(
            [
                (attempt.created_at, attempt.task_fencing_token)
                for attempt in attempts
                if attempt.task_fencing_token is not None
            ],
            key=lambda item: item[0],
        )
        for (_, previous_token), (_, current_token) in pairwise(fenced):
            if current_token <= previous_token:
                raise TaskLedgerContractError(
                    "task fencing must increase monotonically across the task attempts"
                )

    @staticmethod
    def _validate_task_lease_references(
        ledger: LedgerContracts,
        tasks_by_id: Mapping[str, AgentTaskInvocation],
        attempts_by_id: Mapping[str, AgentAttempt],
        runs_by_id: Mapping[str, AgentRunBinding],
        leases_by_id: Mapping[str, TaskLeaseContract],
    ) -> None:
        active_leases_by_attempt: dict[str, list[TaskLeaseContract]] = {}
        for lease in ledger.task_leases:
            if lease.state is LeaseState.ACTIVE:
                active_leases_by_attempt.setdefault(lease.attempt_id, []).append(lease)
        for attempt_id, active_leases in active_leases_by_attempt.items():
            if len(active_leases) > 1:
                raise TaskLedgerContractError(
                    f"attempt {attempt_id} must have at most one active task lease"
                )
        for lease in ledger.task_leases:
            TaskLedgerContractConfig._validate_one_task_lease(
                lease, ledger, tasks_by_id, attempts_by_id, runs_by_id
            )

    @staticmethod
    def _validate_one_task_lease(
        lease: TaskLeaseContract,
        ledger: LedgerContracts,
        tasks_by_id: Mapping[str, AgentTaskInvocation],
        attempts_by_id: Mapping[str, AgentAttempt],
        runs_by_id: Mapping[str, AgentRunBinding],
    ) -> None:
        bound_task = tasks_by_id.get(lease.task_id)
        bound_attempt = attempts_by_id.get(lease.attempt_id)
        bound_run = runs_by_id.get(lease.agent_run_id)
        if bound_task is None or bound_attempt is None or bound_run is None:
            raise TaskLedgerContractError(
                f"task lease {lease.task_lease_id} references an unknown task, attempt or run"
            )
        if bound_attempt.task_id != lease.task_id or bound_run.task_id != lease.task_id:
            raise TaskLedgerContractError(
                f"task lease {lease.task_lease_id} crosses the task binding boundary"
            )
        if bound_attempt.agent_run_id != lease.agent_run_id:
            raise TaskLedgerContractError(
                f"task lease {lease.task_lease_id} binds an attempt from a different agent run"
            )
        if (
            bound_attempt.state
            in (AttemptState.LEASED, AttemptState.DISPATCHING, AttemptState.RUNNING)
            and bound_attempt.task_lease_id != lease.task_lease_id
        ):
            raise TaskLedgerContractError(
                f"task lease {lease.task_lease_id} is not the current lease of its attempt; "
                "an active attempt must point back to exactly this lease"
            )
        if lease.run_lease_id != bound_run.run_lease_id:
            raise TaskLedgerContractError(
                f"task lease {lease.task_lease_id} does not bind the current run lease"
            )
        if lease.run_fencing_token != bound_run.run_fencing_token:
            raise TaskLedgerContractError(
                f"task lease {lease.task_lease_id} binds a stale run fencing token"
            )
        if (
            lease.node_id != bound_run.node_id
            or lease.node_fencing_token != bound_run.node_fencing_token
        ):
            raise TaskLedgerContractError(
                f"task lease {lease.task_lease_id} does not bind the current node fencing"
            )
        if lease.workspace_generation != bound_run.workspace_generation:
            raise TaskLedgerContractError(
                f"task lease {lease.task_lease_id} binds a stale workspace generation"
            )
        if bound_attempt.task_fencing_token is not None and (
            lease.task_fencing_token != bound_attempt.task_fencing_token
        ):
            raise TaskLedgerContractError(
                f"task lease {lease.task_lease_id} binds a task fencing token that "
                "does not match the attempt"
            )
        if lease.expires_at != ledger.lease_expiry_bounds.task_lease_expiry:
            raise TaskLedgerContractError(
                f"task lease {lease.task_lease_id} expiry disagrees with the "
                "lease_expiry_bounds contract"
            )
        attempt_deadline = _parse_utc_timestamp(bound_attempt.deadline, name="attempt.deadline")
        task_deadline = _parse_utc_timestamp(bound_task.deadline, name="task.deadline")
        lease_expiry = _parse_utc_timestamp(lease.expires_at, name="task_lease.expires_at")
        if lease_expiry > attempt_deadline:
            raise TaskLedgerContractError(
                f"task lease {lease.task_lease_id} expiry must not be later than the "
                "attempt deadline"
            )
        if lease_expiry > task_deadline:
            raise TaskLedgerContractError(
                f"task lease {lease.task_lease_id} expiry must not be later than the "
                "task deadline"
            )
        if lease.state is not LeaseState.ACTIVE and bound_attempt.state in (
            AttemptState.LEASED,
            AttemptState.DISPATCHING,
            AttemptState.RUNNING,
        ):
            raise TaskLedgerContractError(
                f"task lease {lease.task_lease_id} referenced by a leased, dispatching "
                "or running attempt must be active"
            )

    @staticmethod
    def _validate_effect_references(
        ledger: LedgerContracts, attempts_by_id: Mapping[str, AgentAttempt]
    ) -> None:
        for effect in ledger.effects:
            if effect.attempt_id not in attempts_by_id:
                raise TaskLedgerContractError(
                    f"effect {effect.effect_id} references an unknown attempt {effect.attempt_id}"
                )

    @staticmethod
    def _validate_checkpoint_references(
        ledger: LedgerContracts,
        tasks_by_id: Mapping[str, AgentTaskInvocation],
        attempts_by_id: Mapping[str, AgentAttempt],
    ) -> None:
        for checkpoint in ledger.checkpoints:
            if checkpoint.task_id not in tasks_by_id:
                raise TaskLedgerContractError(
                    f"checkpoint {checkpoint.checkpoint_id} references an unknown task"
                )
            bound_attempt = attempts_by_id.get(checkpoint.attempt_id)
            if bound_attempt is None or bound_attempt.task_id != checkpoint.task_id:
                raise TaskLedgerContractError(
                    f"checkpoint {checkpoint.checkpoint_id} crosses the task binding boundary"
                )
            if bound_attempt.state is not AttemptState.COMMITTED:
                raise TaskLedgerContractError(
                    f"checkpoint {checkpoint.checkpoint_id} must reference a committed attempt"
                )
            bound_task = tasks_by_id[checkpoint.task_id]
            if checkpoint.budget_ledger.policy_digest != bound_task.budget_policy_digest:
                raise TaskLedgerContractError(
                    f"checkpoint {checkpoint.checkpoint_id} budget ledger drifts from the "
                    "task budget policy"
                )

    def canonical_digest(self) -> str:
        return _sha256_bytes(_canonical_json(self.to_dict()))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "phase": self.phase,
            "activation_requested": self.activation_requested,
            "feature_gates": self.feature_gates,
            "p34_7": {
                "formal_state": self.p34_7_formal_state.value,
                "decision": {
                    "path": self.p34_7_decision,
                    "sha256": self.p34_7_decision_sha256,
                },
            },
            "p5_0": {
                "formal_state": self.p5_0_formal_state.value,
                "admission_contract": {
                    "path": self.p5_0_admission_path,
                    "sha256": self.p5_0_admission_sha256,
                },
            },
            "p5_1": {
                "formal_state": self.p5_1_formal_state.value,
                "registry_contract": {
                    "path": self.p5_1_registry_contract_path,
                    "sha256": self.p5_1_registry_contract_sha256,
                },
            },
            "source": {
                "expected_repository": self.source.expected_repository,
                "tracked_pathspecs": list(self.source.tracked_pathspecs),
                "require_clean_checkout": self.source.require_clean_checkout,
            },
            "evidence": [
                {
                    "id": item.evidence_id,
                    "status": item.status.value,
                    "path": item.path,
                    "sha256": item.sha256,
                    "assertions": dict(item.assertions),
                    "required_for_activation": item.required_for_activation,
                }
                for item in self.evidence
            ],
            "budget_ceilings": self.ceilings.as_mapping(),
            "deadline_ceiling_seconds": self.deadline_ceiling_seconds,
            "task_lease_ttl_ceiling_seconds": self.task_lease_ttl_ceiling_seconds,
            "hash_profiles": list(self.hash_profiles),
            "identity_stages": [rules.to_dict() for rules in self.identity_stages],
            "forbidden_source_paths": list(self.forbidden_source_paths),
            "baseline_migration_revisions": list(self.baseline_migration_revisions),
            "sealed_contracts": [
                {"name": name, "path": path, "sha256": digest}
                for name, path, digest in self.sealed_contracts
            ],
            "openapi_snapshot": {
                "path": self.openapi_snapshot_path,
                "sha256": self.openapi_snapshot_sha256,
            },
            "ledger_contracts": {
                "tasks": [item.to_dict() for item in self.ledger_contracts.tasks],
                "runs": [item.to_dict() for item in self.ledger_contracts.runs],
                "steps": [item.to_dict() for item in self.ledger_contracts.steps],
                "attempts": [item.to_dict() for item in self.ledger_contracts.attempts],
                "task_leases": [item.to_dict() for item in self.ledger_contracts.task_leases],
                "effects": [item.to_dict() for item in self.ledger_contracts.effects],
                "checkpoints": [item.to_dict() for item in self.ledger_contracts.checkpoints],
                "budget_ledgers": [item.to_dict() for item in self.ledger_contracts.budget_ledgers],
                "lease_expiry_bounds": self.ledger_contracts.lease_expiry_bounds.to_dict(),
            },
            "critical_veto": {"expected": self.critical_veto},
        }


def _parse_formal_state(value: object, *, name: str) -> P347FormalState:
    text = _strict_string(value, name=name)
    try:
        return P347FormalState(text)
    except ValueError as exc:
        raise TaskLedgerContractError(f"{name} has an invalid state") from exc


def _parse_sealed_contract(value: object) -> tuple[str, str, str]:
    data = _strict_object(value, name="sealed_contracts[]")
    _only_keys(data, {"name", "path", "sha256"}, name="sealed_contracts[]")
    name = _strict_string(data.get("name"), name="sealed_contracts[].name")
    path = _relative_repo_path(data.get("path"), name="sealed_contracts[].path")
    digest = _strict_digest(data.get("sha256"), name="sealed_contracts[].sha256")
    return name, path, digest


# ---------------------------------------------------------------------------
# Report and gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TaskLedgerContractReport:
    state: AdmissionState
    activation_allowed: bool
    contract_valid: bool
    configuration_sha256: str
    feature_gates: dict[str, bool]
    p34_7_formal_state: str
    p5_0_formal_state: str
    p5_1_formal_state: str
    source: GitSourceProvenance | None
    passed_evidence: tuple[str, ...]
    blockers: tuple[str, ...]
    vetoes: tuple[str, ...]
    migration_head: str | None
    evidence_scope: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "gate": "P5.2A Agent Task ledger contract preflight",
            "state": self.state.value,
            "activation_allowed": self.activation_allowed,
            "contract_valid": self.contract_valid,
            "configuration_sha256": self.configuration_sha256,
            "feature_gates": self.feature_gates,
            "p34_7_formal_state": self.p34_7_formal_state,
            "p5_0_formal_state": self.p5_0_formal_state,
            "p5_1_formal_state": self.p5_1_formal_state,
            "source": None if self.source is None else self.source.to_dict(),
            "passed_evidence": list(self.passed_evidence),
            "blockers": list(self.blockers),
            "vetoes": list(self.vetoes),
            "migration_head": self.migration_head,
            "task_ledger_orm_created": False,
            "task_ledger_migration_created": False,
            "agent_invocation_api_exposed": False,
            "agent_runtime_created": False,
            "planner_created": False,
            "executor_created": False,
            "scheduler_or_worker_started": False,
            "model_or_tool_invoked": False,
            "task_execution_activated": False,
            "root_env_accessed": False,
            "business_database_accessed": False,
            "business_database_migrated": False,
            "external_network_accessed": False,
            "verification_evidence": self.evidence_scope,
        }


class TaskLedgerContractGate:
    """Offline P5.2A preflight; never ready until P34.7/P5.0/P5.1 are ready."""

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root.resolve(strict=True)

    def validate_only(self, config: TaskLedgerContractConfig) -> TaskLedgerContractReport:
        blockers = [
            "formal Phase 5 task ledger contract verification was not executed",
            "Agent Task/Run/Step/Attempt persistence ledger is not implemented",
            "Agent Runtime is not implemented",
            "Task execution is not authorized",
        ]
        if config.p34_7_formal_state is not P347FormalState.READY:
            blockers.append(f"P34.7 formal state is not ready: {config.p34_7_formal_state.value}")
        if config.p5_0_formal_state is not P347FormalState.READY:
            blockers.append(
                f"P5.0 admission formal state is not ready: {config.p5_0_formal_state.value}"
            )
        if config.p5_1_formal_state is not P347FormalState.READY:
            blockers.append(
                f"P5.1 production formal state is not ready: {config.p5_1_formal_state.value}"
            )
        blockers.extend(
            f"{item.evidence_id}: {item.status.value}"
            for item in config.evidence
            if item.required_for_activation and item.status is not EvidenceStatus.PASSED
        )
        return TaskLedgerContractReport(
            state=AdmissionState.BLOCKED,
            activation_allowed=False,
            contract_valid=True,
            configuration_sha256=config.canonical_digest(),
            feature_gates=_feature_gates_dict(config),
            p34_7_formal_state=config.p34_7_formal_state.value,
            p5_0_formal_state=config.p5_0_formal_state.value,
            p5_1_formal_state=config.p5_1_formal_state.value,
            source=None,
            passed_evidence=(),
            blockers=tuple(blockers),
            vetoes=(),
            migration_head=None,
            evidence_scope={
                "mode": "validate_only",
                "static_source_boundary": {
                    "checked": False,
                    "migration_head_verified": False,
                    "forbidden_paths_verified": False,
                    "openapi_snapshot_verified": False,
                },
                "import_ast_analysis": "proven_by_tests_not_by_gate",
                "gate_execution": {
                    "contract_parsed": True,
                    "feature_gates_resolved": False,
                    "sealed_digests_verified": False,
                    "evidence_references_verified": False,
                },
                "direct_runtime_execution": "not_executed_by_gate",
            },
        )

    def verify(
        self,
        config: TaskLedgerContractConfig,
        *,
        source: GitSourceProvenance | None = None,
        gate_values: Mapping[str, object] | None = None,
    ) -> TaskLedgerContractReport:
        provenance = source or build_git_source_provenance(self._repo_root, config.source)
        blockers: list[str] = []
        vetoes: list[str] = []
        if config.source.require_clean_checkout and not provenance.clean:
            vetoes.append("Phase 5 task ledger contract requires a clean checkout")
        if config.activation_requested:
            vetoes.append("P5.2A activation must never be requested")
        try:
            gates = resolve_feature_gates(gate_values or {})
        except ConfigurationError as exc:
            vetoes.append(f"feature gates: {exc}")
            resolved = {
                "agent_runtime_enabled": False,
                "agent_planner_enabled": False,
                "multi_agent_enabled": False,
            }
        else:
            resolved = gates.to_dict()
            if gates.any_enabled:
                # Unlike P5.0/P5.1A (blocker), an unexpectedly true gate is a
                # veto for P5.2A: the ledger contract itself is void.
                vetoes.append("Phase 5 feature gates must remain disabled; a true gate is a veto")
            else:
                blockers.append(
                    "Agent Runtime gate remains disabled: task execution is not authorized"
                )
        if config.p34_7_formal_state is not P347FormalState.READY:
            blockers.append(f"P34.7 formal state is not ready: {config.p34_7_formal_state.value}")
        if config.p5_0_formal_state is not P347FormalState.READY:
            blockers.append(
                f"P5.0 admission formal state is not ready: {config.p5_0_formal_state.value}"
            )
        if config.p5_1_formal_state is not P347FormalState.READY:
            blockers.append(
                f"P5.1 production formal state is not ready: {config.p5_1_formal_state.value}"
            )
        blockers.extend(
            f"{item.evidence_id}: {item.status.value}"
            for item in config.evidence
            if item.required_for_activation and item.status is not EvidenceStatus.PASSED
        )
        blockers.extend(
            [
                "Agent Task/Run/Step/Attempt persistence ledger is not implemented",
                "Agent Runtime is not implemented",
                "Task execution is not authorized",
            ]
        )
        source_boundary_ok = False
        try:
            migration_head = self._verify_source_boundaries(config)
        except (
            TaskLedgerContractError,
            ConfigurationError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            OSError,
        ) as exc:
            vetoes.append(f"source boundaries: {exc}")
            migration_head = None
        else:
            source_boundary_ok = True
        sealed_ok = True
        try:
            self._verify_sealed_files(config)
        except (
            TaskLedgerContractError,
            ConfigurationError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            OSError,
        ) as exc:
            vetoes.append(f"sealed contracts: {exc}")
            sealed_ok = False
        gates_resolved = not any(veto.startswith("feature gates:") for veto in vetoes)
        state = AdmissionState.INVALID if vetoes else AdmissionState.BLOCKED
        return TaskLedgerContractReport(
            state=state,
            activation_allowed=False,
            contract_valid=not vetoes,
            configuration_sha256=config.canonical_digest(),
            feature_gates=resolved,
            p34_7_formal_state=config.p34_7_formal_state.value,
            p5_0_formal_state=config.p5_0_formal_state.value,
            p5_1_formal_state=config.p5_1_formal_state.value,
            source=provenance,
            passed_evidence=(),
            blockers=tuple(blockers),
            vetoes=tuple(vetoes),
            migration_head=migration_head,
            evidence_scope={
                "mode": "verify",
                "static_source_boundary": {
                    "checked": True,
                    "migration_head_verified": source_boundary_ok,
                    "forbidden_paths_verified": source_boundary_ok,
                    "openapi_snapshot_verified": source_boundary_ok,
                },
                "import_ast_analysis": "proven_by_tests_not_by_gate",
                "gate_execution": {
                    "contract_parsed": True,
                    "feature_gates_resolved": gates_resolved,
                    "sealed_digests_verified": sealed_ok,
                    "evidence_references_verified": True,
                },
                "direct_runtime_execution": "not_executed_by_gate",
            },
        )

    def _verify_source_boundaries(self, config: TaskLedgerContractConfig) -> str:
        migration_head = discover_migration_head(
            self._repo_root, "backend/src/omnibase/migrations/versions"
        )
        if migration_head != config.baseline_migration_revisions[-1]:
            raise TaskLedgerContractError(
                f"migration head is {migration_head}, expected {config.baseline_migration_revisions[-1]}"
            )
        current_revisions = _discover_migration_revisions(
            self._repo_root, "backend/src/omnibase/migrations/versions"
        )
        if set(current_revisions) != set(config.baseline_migration_revisions):
            raise TaskLedgerContractError(
                "migration revision set drifted from the sealed baseline (a new revision "
                "such as 0011 would be a veto)"
            )
        for relative in config.forbidden_source_paths:
            candidate = self._repo_root / relative
            if _lexists(candidate):
                raise TaskLedgerContractError(
                    f"forbidden source path exists: {relative} (attempted runtime/ORM/API)"
                )
        openapi_path = _safe_repo_file(self._repo_root, config.openapi_snapshot_path)
        content = openapi_path.read_bytes()
        if _sha256_bytes(content) != config.openapi_snapshot_sha256:
            raise TaskLedgerContractError("openapi snapshot SHA-256 drifted")
        payload = json.loads(content.decode("utf-8"))
        paths = payload.get("paths")
        if isinstance(paths, dict):
            for path_name in paths:
                lowered = path_name.lower()
                if any(
                    marker in lowered
                    for marker in ("agent-invocations", "agent-tasks", "/gateway/v1/agent")
                ):
                    raise TaskLedgerContractError(
                        f"openapi snapshot exposes an agent invocation endpoint: {path_name}"
                    )
        return migration_head

    def _verify_sealed_files(self, config: TaskLedgerContractConfig) -> None:
        for name, relative, digest in config.sealed_contracts:
            path = _safe_repo_file(self._repo_root, relative)
            if _sha256_bytes(path.read_bytes()) != digest:
                raise TaskLedgerContractError(f"sealed contract drifted: {name}")
        for relative, digest in (
            (config.p34_7_decision, config.p34_7_decision_sha256),
            (config.p5_0_admission_path, config.p5_0_admission_sha256),
            (config.p5_1_registry_contract_path, config.p5_1_registry_contract_sha256),
        ):
            path = _safe_repo_file(self._repo_root, relative)
            if _sha256_bytes(path.read_bytes()) != digest:
                raise TaskLedgerContractError(f"sealed reference drifted: {relative}")


def _discover_migration_revisions(repo_root: Path, directory: str) -> tuple[str, ...]:
    """Parse every revision id in the migration directory without importing files."""
    versions_dir = _safe_repo_dir(repo_root, directory)
    root = repo_root.resolve(strict=True)
    revisions: list[str] = []
    for discovered_path in sorted(versions_dir.glob("*.py")):
        relative_path = discovered_path.relative_to(root).as_posix()
        path = _safe_repo_file(root, relative_path)
        match = _REVISION_LINE.search(path.read_text(encoding="utf-8"))
        if match is None:
            raise TaskLedgerContractError(f"migration file has no revision id: {path.name}")
        revisions.append(match.group(1))
    if not revisions:
        raise TaskLedgerContractError(f"migration directory contains no revisions: {directory}")
    if len(revisions) != len(set(revisions)):
        raise TaskLedgerContractError("migration chain contains duplicate revision ids")
    return tuple(revisions)


def _lexists(path: Path) -> bool:
    try:
        path.lstat()
    except OSError:
        return False
    return True


def _feature_gates_dict(config: TaskLedgerContractConfig) -> dict[str, bool]:
    del config
    return {
        "agent_runtime_enabled": False,
        "agent_planner_enabled": False,
        "multi_agent_enabled": False,
    }


def load_task_ledger_contract_config(path: Path) -> TaskLedgerContractConfig:
    metadata = path.lstat()
    is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
    if stat.S_ISLNK(metadata.st_mode) or is_reparse or not stat.S_ISREG(metadata.st_mode):
        raise TaskLedgerContractError(
            "P5.2A contract configuration must be a regular non-link file"
        )

    def _reject_constant(value: str) -> None:
        raise TaskLedgerContractError(f"contract contains a non-finite number: {value}")

    payload = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    return TaskLedgerContractConfig.from_mapping(payload)


__all__ = [
    "AgentAttempt",
    "AgentRunBinding",
    "AgentRunState",
    "AgentStep",
    "AgentTaskInvocation",
    "AttemptState",
    "BudgetCeilings",
    "BudgetDimension",
    "BudgetDimensionLedger",
    "BudgetLedgerSnapshot",
    "CheckpointReference",
    "CommittedEvidenceKind",
    "EffectState",
    "FieldOrigin",
    "IdentityStage",
    "IdentityStageRules",
    "LeaseExpiryBounds",
    "LeaseState",
    "LedgerContracts",
    "ProviderEffect",
    "ReplayClass",
    "StepState",
    "TaskLeaseContract",
    "TaskLedgerContractConfig",
    "TaskLedgerContractError",
    "TaskLedgerContractGate",
    "TaskLedgerContractReport",
    "TaskState",
    "classify_replay",
    "compute_request_hash",
    "hash_payload_for_profile",
    "load_task_ledger_contract_config",
    "validate_agent_run_transition",
    "validate_attempt_transition",
    "validate_cancel_attempt",
    "validate_cancel_target",
    "validate_committed_evidence",
    "validate_effect_transition",
    "validate_identity_restart",
    "validate_request_hash",
    "validate_retry",
    "validate_step_transition",
    "validate_task_transition",
]
