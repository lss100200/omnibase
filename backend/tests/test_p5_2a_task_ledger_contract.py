"""P5.2A offline Agent Task / Run / Lease / Fencing ledger contract preflight tests.

The negative matrix follows the P5.2A task contract: each negative fixture
asserts a stable reason code, never just "an exception was raised".
"""

from __future__ import annotations

import ast
import hashlib
import json
import runpy
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from omnibase.production.composition import AdmissionState, ConfigurationError, GitSourceProvenance
from omnibase.production.phase5_task_ledger_contract import (
    AgentAttempt,
    AgentRunBinding,
    AgentRunState,
    AgentStep,
    AgentTaskInvocation,
    AttemptState,
    BudgetDimensionLedger,
    BudgetLedgerSnapshot,
    CheckpointReference,
    CommittedEvidenceKind,
    EffectState,
    FieldOrigin,
    IdentityStageRules,
    LeaseExpiryBounds,
    ProviderEffect,
    ReplayClass,
    StepState,
    TaskLeaseContract,
    TaskLedgerContractConfig,
    TaskLedgerContractError,
    TaskLedgerContractGate,
    TaskState,
    classify_replay,
    compute_request_hash,
    load_task_ledger_contract_config,
    validate_agent_run_transition,
    validate_attempt_transition,
    validate_cancel_attempt,
    validate_cancel_target,
    validate_committed_evidence,
    validate_effect_transition,
    validate_identity_restart,
    validate_retry,
    validate_step_transition,
    validate_task_transition,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "deployment" / "production" / "phase5-task-ledger-contract.example.json"
VALIDATOR_PATH = REPO_ROOT / "scripts" / "production" / "validate_p5_2a_task_ledger_contract.py"

TASK_ID = "00000000-0000-0000-0000-0000000000e1"
TENANT_ID = "00000000-0000-0000-0000-00000000000a"
WORKSPACE_ID = "66666666-6666-6666-6666-666666666666"
ACTOR_ID = "00000000-0000-0000-0000-0000000000aa"
DEFINITION_ID = "00000000-0000-0000-0000-000000000001"
VERSION_ID = "11111111-1111-1111-1111-111111111111"
VERSION_DIGEST = "4b5a26ba3980e80216db50d8d069a6c052ca472954c33247baa1b81ec69f91ca"
BINDING_ID = "55555555-5555-5555-5555-555555555555"
PLAN_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
STEP_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
STEP_2_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbc2"
RUN_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
WORKSPACE_RUN_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
NODE_ID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
RUN_LEASE_ID = "ffffffff-ffff-ffff-ffff-ffffffffffff"
TASK_LEASE_ID = "77777777-7777-7777-7777-777777777777"
LEASE_2_ID = "77777777-7777-7777-7777-777777777778"
LEASE_3_ID = "77777777-7777-7777-7777-777777777779"
ATTEMPT_1_ID = "88888888-8888-8888-8888-888888888881"
ATTEMPT_2_ID = "88888888-8888-8888-8888-888888888882"
ATTEMPT_3_ID = "88888888-8888-8888-8888-888888888883"
ATTEMPT_4_ID = "88888888-8888-8888-8888-888888888884"
EFFECT_ID = "99999999-9999-9999-9999-999999999991"
CHECKPOINT_ID = "99999999-9999-9999-9999-999999999992"
OPERATION_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaab"
RUNTIME_INSTANCE_ID = "12345678-1234-1234-1234-123456789012"
RESOURCE_SCOPE_DIGEST = "c1" * 32
BUDGET_POLICY_DIGEST = "c2" * 32
PLAN_DIGEST = "c3" * 32
WORKLOAD_THUMBPRINT = "d0" * 32
EFFECT_REQUEST_HASH = "c4" * 32


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _task_mapping() -> dict[str, object]:
    mapping: dict[str, object] = {
        "schema_version": 1,
        "task_id": TASK_ID,
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "workspace_generation": 1,
        "actor_user_id": ACTOR_ID,
        "agent_definition_id": DEFINITION_ID,
        "agent_version_id": VERSION_ID,
        "agent_version_digest": VERSION_DIGEST,
        "workspace_agent_binding_id": BINDING_ID,
        "task_generation": 1,
        "plan_id": PLAN_ID,
        "plan_version": "1",
        "plan_digest": PLAN_DIGEST,
        "deadline": "2026-08-03T12:00:00Z",
        "state": "scheduled",
        "resource_scope_digest": RESOURCE_SCOPE_DIGEST,
        "budget_policy_digest": BUDGET_POLICY_DIGEST,
        "request_hash": "0" * 64,
        "created_by": ACTOR_ID,
        "created_at": "2026-08-03T00:00:00Z",
    }
    mapping["request_hash"] = compute_request_hash(
        "task_create",
        {
            "operation": "agent.task.create",
            "task_id": TASK_ID,
            "tenant_id": TENANT_ID,
            "workspace_id": WORKSPACE_ID,
            "workspace_generation": 1,
            "agent_definition_id": DEFINITION_ID,
            "agent_version_id": VERSION_ID,
            "agent_version_digest": VERSION_DIGEST,
            "workspace_agent_binding_id": BINDING_ID,
            "resource_scope_digest": RESOURCE_SCOPE_DIGEST,
            "budget_policy_digest": BUDGET_POLICY_DIGEST,
            "deadline": "2026-08-03T12:00:00Z",
        },
    )
    return mapping


def _run_mapping() -> dict[str, object]:
    return {
        "schema_version": 1,
        "agent_run_id": RUN_ID,
        "task_id": TASK_ID,
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "workspace_generation": 1,
        "workspace_run_id": WORKSPACE_RUN_ID,
        "runtime_instance_id": RUNTIME_INSTANCE_ID,
        "workload_identity_thumbprint": WORKLOAD_THUMBPRINT,
        "node_id": NODE_ID,
        "node_fencing_token": 1,
        "run_lease_id": RUN_LEASE_ID,
        "run_fencing_token": 3,
        "state": "running",
        "created_at": "2026-08-03T00:01:00Z",
    }


def _step_mapping() -> dict[str, object]:
    return {
        "schema_version": 1,
        "step_id": STEP_ID,
        "task_id": TASK_ID,
        "agent_run_id": RUN_ID,
        "step_number": 1,
        "plan_id": PLAN_ID,
        "plan_version": "1",
        "plan_digest": PLAN_DIGEST,
        "dependencies": [],
        "state": "running",
        "created_at": "2026-08-03T00:02:00Z",
    }


def _attempt_mapping(attempt_id: str = ATTEMPT_2_ID, attempt_number: int = 2) -> dict[str, object]:
    return {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "task_id": TASK_ID,
        "step_id": STEP_ID,
        "agent_run_id": RUN_ID,
        "attempt_number": attempt_number,
        "state": "running",
        "task_lease_id": TASK_LEASE_ID,
        "task_fencing_token": 9,
        "expected_previous_state": "running",
        "deadline": "2026-08-03T12:00:00Z",
        "created_at": "2026-08-03T00:10:00Z",
    }


def _attempt_1_mapping() -> dict[str, object]:
    mapping = _attempt_mapping(ATTEMPT_1_ID, 1)
    mapping["state"] = "committed"
    mapping["task_lease_id"] = None
    mapping["task_fencing_token"] = None
    mapping["created_at"] = "2026-08-03T00:05:00Z"
    return mapping


def _lease_mapping() -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_lease_id": TASK_LEASE_ID,
        "task_id": TASK_ID,
        "attempt_id": ATTEMPT_2_ID,
        "agent_run_id": RUN_ID,
        "run_lease_id": RUN_LEASE_ID,
        "run_fencing_token": 3,
        "node_id": NODE_ID,
        "node_fencing_token": 1,
        "workspace_generation": 1,
        "task_fencing_token": 9,
        "state": "active",
        "expires_at": "2026-08-03T00:15:00Z",
        "heartbeat_at": None,
        # claimed after the attempt is created; TTL stays within 300s. The
        # claim instant is later than LEASE_2's 00:10:00Z so the Task-wide
        # fencing chronology (3 -> 9) is strictly increasing on the lease
        # ledger, which is the authoritative fencing source.
        "created_at": "2026-08-03T00:14:00Z",
    }


def _step_2_mapping() -> dict[str, object]:
    return {
        "schema_version": 1,
        "step_id": STEP_2_ID,
        "task_id": TASK_ID,
        "agent_run_id": RUN_ID,
        "step_number": 2,
        "plan_id": PLAN_ID,
        "plan_version": "1",
        "plan_digest": PLAN_DIGEST,
        "dependencies": [STEP_ID],
        "state": "ready",
        "created_at": "2026-08-03T00:03:00Z",
    }


def _attempt_3_mapping() -> dict[str, object]:
    return {
        "schema_version": 1,
        "attempt_id": ATTEMPT_3_ID,
        "task_id": TASK_ID,
        "step_id": STEP_2_ID,
        "agent_run_id": RUN_ID,
        "attempt_number": 1,
        "state": "running",
        "task_lease_id": LEASE_2_ID,
        "task_fencing_token": 3,
        "expected_previous_state": "running",
        "deadline": "2026-08-03T12:00:00Z",
        "created_at": "2026-08-03T00:06:00Z",
    }


def _lease_2_mapping() -> dict[str, object]:
    mapping = _lease_mapping()
    mapping["task_lease_id"] = LEASE_2_ID
    mapping["attempt_id"] = ATTEMPT_3_ID
    mapping["task_fencing_token"] = 3
    # claim happens after the attempt is created; TTL stays within 300s
    mapping["created_at"] = "2026-08-03T00:10:00Z"
    return mapping


def _effect_mapping() -> dict[str, object]:
    return {
        "schema_version": 1,
        "effect_id": EFFECT_ID,
        "attempt_id": ATTEMPT_2_ID,
        "state": "reserved",
        "operation_id": OPERATION_ID,
        "request_hash": EFFECT_REQUEST_HASH,
        "result_digest": None,
        "created_at": "2026-08-03T00:11:00Z",
    }


def _budget_dimension_mappings() -> list[dict[str, object]]:
    return [
        {
            "dimension": "input_tokens",
            "limit": 1000000,
            "reserved": 10000,
            "committed": 8000,
            "released": 2000,
        },
        {
            "dimension": "output_tokens",
            "limit": 500000,
            "reserved": 5000,
            "committed": 4000,
            "released": 1000,
        },
        {
            "dimension": "reasoning_tokens",
            "limit": 500000,
            "reserved": 2000,
            "committed": 1000,
            "released": 500,
        },
        {
            "dimension": "total_tokens",
            "limit": 2000000,
            "reserved": 20000,
            "committed": 16000,
            "released": 4000,
        },
        {
            "dimension": "cost_micros",
            "limit": 1000000,
            "reserved": 10000,
            "committed": 8000,
            "released": 2000,
        },
        {"dimension": "model_calls", "limit": 100, "reserved": 10, "committed": 8, "released": 2},
        {"dimension": "tool_calls", "limit": 50, "reserved": 5, "committed": 4, "released": 1},
        {
            "dimension": "wall_clock_ms",
            "limit": 3600000,
            "reserved": 60000,
            "committed": 50000,
            "released": 10000,
        },
        {
            "dimension": "artifact_bytes",
            "limit": 104857600,
            "reserved": 1048576,
            "committed": 524288,
            "released": 262144,
        },
        {"dimension": "sandbox_jobs", "limit": 10, "reserved": 1, "committed": 1, "released": 0},
        {"dimension": "max_attempts", "limit": 3, "reserved": 2, "committed": 1, "released": 0},
        {
            "dimension": "max_parallel_steps",
            "limit": 2,
            "reserved": 1,
            "committed": 1,
            "released": 0,
        },
    ]


def _budget_ledger_mapping() -> dict[str, object]:
    return {
        "schema_version": 1,
        "dimensions": _budget_dimension_mappings(),
        "policy_digest": BUDGET_POLICY_DIGEST,
    }


def _checkpoint_mapping() -> dict[str, object]:
    return {
        "schema_version": 1,
        "checkpoint_id": CHECKPOINT_ID,
        "task_id": TASK_ID,
        "attempt_id": ATTEMPT_1_ID,
        "committed_plan_version": "1",
        "committed_plan_digest": PLAN_DIGEST,
        "committed_attempt_results": [f"result:attempt:{ATTEMPT_1_ID}:1"],
        "budget_ledger": _budget_ledger_mapping(),
        "created_at": "2026-08-03T00:12:00Z",
    }


def _expiry_bounds_mapping() -> dict[str, str]:
    return {
        "task_deadline": "2026-08-03T12:00:00Z",
        "run_lease_expiry": "2026-08-03T01:00:00Z",
        "node_attestation_expiry": "2026-08-03T01:00:00Z",
        "capability_grant_expiry": "2026-08-03T00:45:00Z",
        "workspace_policy_expiry": "2026-08-03T02:00:00Z",
        "task_lease_expiry": "2026-08-03T00:15:00Z",
    }


def _ceilings() -> dict[str, int]:
    return {
        "input_tokens": 10000000,
        "output_tokens": 5000000,
        "reasoning_tokens": 5000000,
        "total_tokens": 20000000,
        "cost_micros": 10000000,
        "model_calls": 5000,
        "tool_calls": 2000,
        "wall_clock_ms": 3600000,
        "artifact_bytes": 1073741824,
        "sandbox_jobs": 500,
        "max_attempts": 32,
        "max_parallel_steps": 8,
    }


_TASK_CREATE_REQUIRED = [
    "tenant_id",
    "workspace_id",
    "workspace_generation",
    "actor_user_id",
    "agent_definition_id",
    "agent_version_id",
    "agent_version_digest",
    "workspace_agent_binding_id",
    "task_id",
    "deadline",
    "resource_scope_digest",
    "budget_policy_digest",
]
_TASK_CREATE_NOT_YET = [
    "task_generation",
    "plan_id",
    "plan_version",
    "plan_digest",
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
    "effect_id",
    "checkpoint_id",
]
_TASK_CREATE_IMMUTABLE = [
    "tenant_id",
    "workspace_id",
    "workspace_generation",
    "agent_definition_id",
    "agent_version_id",
    "agent_version_digest",
    "workspace_agent_binding_id",
    "task_id",
]
_TASK_BOUNDARY = [
    "tenant_id",
    "workspace_id",
    "workspace_generation",
    "task_id",
    "task_generation",
]
_WORKLOAD_BOUNDARY = [
    "runtime_instance_id",
    "workload_identity_thumbprint",
    "workspace_run_id",
    "node_id",
    "node_fencing_token",
    "run_lease_id",
    "run_fencing_token",
    "plan_id",
    "plan_version",
    "plan_digest",
    "effect_id",
    "checkpoint_id",
]
_BROWSER_AGENT_IDENTITY = [
    "actor_user_id",
    "agent_definition_id",
    "agent_version_id",
    "agent_version_digest",
    "workspace_agent_binding_id",
    "resource_scope_digest",
    "budget_policy_digest",
    "tenant_id",
    "workspace_id",
    "workspace_generation",
    "task_id",
    "task_generation",
]
_BROWSER_LIFECYCLE_FORBIDDEN = [
    "runtime_instance_id",
    "workload_identity_thumbprint",
    "workspace_run_id",
    "node_id",
    "node_fencing_token",
    "run_lease_id",
    "run_fencing_token",
    "task_lease_id",
    "task_fencing_token",
    "effect_id",
    "checkpoint_id",
    "deadline",
    "plan_id",
    "plan_version",
    "plan_digest",
    "step_id",
    "attempt_id",
    "attempt_number",
    "actor_user_id",
    "agent_definition_id",
    "agent_version_id",
    "agent_version_digest",
    "workspace_agent_binding_id",
    "resource_scope_digest",
    "budget_policy_digest",
    "reconciliation_target",
]
_RECONCILIATION_FORBIDDEN = [
    "runtime_instance_id",
    "workload_identity_thumbprint",
    "workspace_run_id",
    "node_id",
    "node_fencing_token",
    "run_lease_id",
    "run_fencing_token",
    "task_lease_id",
    "task_fencing_token",
    "effect_id",
    "checkpoint_id",
    "deadline",
    "plan_id",
    "plan_version",
    "plan_digest",
    "step_id",
    "attempt_number",
    "expected_previous_state",
    "cancellation_target",
    "actor_user_id",
    "agent_definition_id",
    "agent_version_id",
    "agent_version_digest",
    "workspace_agent_binding_id",
    "resource_scope_digest",
    "budget_policy_digest",
]


def _stage(
    stage: str,
    *,
    required: list[str],
    not_yet: list[str] | None = None,
    immutable: list[str] | None = None,
    core: list[str] | None = None,
    browser: list[str] | None = None,
    workload: list[str] | None = None,
    forbidden: list[str] | None = None,
) -> dict[str, object]:
    return {
        "stage": stage,
        "required_fields": sorted(required),
        "not_yet_generated_fields": sorted(not_yet or []),
        "immutable_fields": sorted(immutable or []),
        "core_generated_fields": sorted(core or ["operation_id", "request_hash"]),
        "browser_submittable_fields": sorted(browser or []),
        "workload_submittable_fields": sorted(workload or []),
        "forbidden_fields": sorted(forbidden or []),
    }


def _identity_stage_mappings() -> list[dict[str, object]]:
    return [
        _stage(
            "task_create",
            required=_TASK_CREATE_REQUIRED,
            not_yet=_TASK_CREATE_NOT_YET,
            immutable=_TASK_CREATE_IMMUTABLE,
            core=["request_hash"],
            browser=_TASK_CREATE_REQUIRED,
        ),
        _stage(
            "task_run_claim",
            required=[
                "task_id",
                "workspace_run_id",
                "node_id",
                "node_fencing_token",
                "run_lease_id",
                "run_fencing_token",
            ],
            immutable=["task_id", "workspace_generation", "workspace_run_id"],
            core=[
                "runtime_instance_id",
                "workload_identity_thumbprint",
                "operation_id",
                "request_hash",
            ],
            forbidden=[
                "task_lease_id",
                "task_fencing_token",
                "plan_id",
                "plan_version",
                "plan_digest",
                "effect_id",
                "checkpoint_id",
                "attempt_id",
                "attempt_number",
                "step_id",
                "expected_previous_state",
                "cancellation_target",
                "reconciliation_target",
                "deadline",
                "resource_scope_digest",
                "budget_policy_digest",
                "actor_user_id",
                "agent_definition_id",
                "agent_version_id",
                "agent_version_digest",
                "workspace_agent_binding_id",
            ],
        ),
        _stage(
            "attempt_claim",
            required=[
                "task_id",
                "task_generation",
                "step_id",
                "attempt_id",
                "attempt_number",
                "expected_previous_state",
                "deadline",
                "run_lease_id",
                "run_fencing_token",
                "node_fencing_token",
            ],
            not_yet=["task_lease_id", "task_fencing_token"],
            immutable=[
                "task_id",
                "task_generation",
                "step_id",
                "attempt_id",
                "attempt_number",
                "workspace_generation",
            ],
            core=["task_lease_id", "task_fencing_token", "operation_id", "request_hash"],
            workload=[
                "task_id",
                "task_generation",
                "step_id",
                "attempt_id",
                "attempt_number",
                "expected_previous_state",
                "deadline",
                "run_lease_id",
                "run_fencing_token",
                "node_fencing_token",
            ],
            forbidden=[
                "runtime_instance_id",
                "workload_identity_thumbprint",
                "workspace_run_id",
                "node_id",
                "plan_id",
                "plan_version",
                "plan_digest",
                "effect_id",
                "checkpoint_id",
                "cancellation_target",
                "reconciliation_target",
            ],
        ),
        _stage(
            "attempt_heartbeat",
            required=[
                "task_id",
                "task_generation",
                "step_id",
                "attempt_id",
                "attempt_number",
                "task_lease_id",
                "task_fencing_token",
            ],
            immutable=[
                "task_id",
                "task_generation",
                "step_id",
                "attempt_id",
                "attempt_number",
                "task_lease_id",
                "task_fencing_token",
                "workspace_generation",
            ],
            workload=[
                "task_id",
                "task_generation",
                "step_id",
                "attempt_id",
                "attempt_number",
                "task_lease_id",
                "task_fencing_token",
            ],
            forbidden=[
                *_WORKLOAD_BOUNDARY,
                "deadline",
                "cancellation_target",
                "reconciliation_target",
                "expected_previous_state",
            ],
        ),
        _stage(
            "attempt_finish",
            required=[
                "task_id",
                "task_generation",
                "step_id",
                "attempt_id",
                "attempt_number",
                "task_lease_id",
                "task_fencing_token",
                "expected_previous_state",
            ],
            immutable=[
                "task_id",
                "task_generation",
                "step_id",
                "attempt_id",
                "attempt_number",
                "task_lease_id",
                "task_fencing_token",
                "workspace_generation",
            ],
            workload=[
                "task_id",
                "task_generation",
                "step_id",
                "attempt_id",
                "attempt_number",
                "task_lease_id",
                "task_fencing_token",
                "expected_previous_state",
            ],
            forbidden=[
                *_WORKLOAD_BOUNDARY,
                "deadline",
                "cancellation_target",
                "reconciliation_target",
            ],
        ),
        _stage(
            "task_cancel",
            required=[
                "task_id",
                "task_generation",
                "expected_previous_state",
                "cancellation_target",
            ],
            immutable=["task_id", "task_generation", "workspace_generation"],
            browser=[*_TASK_BOUNDARY, "expected_previous_state", "cancellation_target"],
            forbidden=_BROWSER_LIFECYCLE_FORBIDDEN,
        ),
        _stage(
            "task_pause",
            required=["task_id", "task_generation", "expected_previous_state"],
            immutable=["task_id", "task_generation", "workspace_generation"],
            browser=[*_TASK_BOUNDARY, "expected_previous_state"],
            forbidden=_BROWSER_LIFECYCLE_FORBIDDEN,
        ),
        _stage(
            "task_resume_request",
            required=["task_id", "task_generation", "expected_previous_state"],
            immutable=["task_id", "task_generation", "workspace_generation"],
            browser=[*_TASK_BOUNDARY, "expected_previous_state"],
            forbidden=_BROWSER_LIFECYCLE_FORBIDDEN,
        ),
        _stage(
            "reconciliation_request",
            required=["task_id", "task_generation", "attempt_id", "reconciliation_target"],
            immutable=["task_id", "task_generation", "attempt_id", "workspace_generation"],
            forbidden=_RECONCILIATION_FORBIDDEN,
        ),
    ]


def _contract_mapping() -> dict[str, object]:
    return {
        "schema_version": 1,
        "phase": "P5.2A",
        "activation_requested": False,
        "feature_gates": {
            "agent_runtime_enabled": False,
            "agent_planner_enabled": False,
            "multi_agent_enabled": False,
        },
        "p34_7": {
            "formal_state": "blocked/not_proven",
            "decision": {
                "path": "docs/evidence/p34-7/production-readiness-decision.md",
                "sha256": "1" * 64,
            },
        },
        "p5_0": {
            "formal_state": "blocked/not_proven",
            "admission_contract": {
                "path": "deployment/production/phase5-admission.example.json",
                "sha256": "2" * 64,
            },
        },
        "p5_1": {
            "formal_state": "blocked/not_proven",
            "registry_contract": {
                "path": "deployment/production/phase5-registry-contract.example.json",
                "sha256": "3" * 64,
            },
        },
        "source": {
            "expected_repository": "https://github.com/lss100200/omnibase.git",
            "require_clean_checkout": True,
            "tracked_pathspecs": [
                ".gitattributes",
                "AGENTS.md",
                "backend/pyproject.toml",
                "backend/uv.lock",
                "backend/src/omnibase/production",
                "deployment/production",
                "scripts/production",
            ],
        },
        "evidence": [
            {
                "id": "phase5_task_ledger_production_evidence",
                "status": "not_proven",
                "path": None,
                "sha256": None,
                "assertions": {},
                "required_for_activation": True,
            }
        ],
        "budget_ceilings": _ceilings(),
        "deadline_ceiling_seconds": 43200,
        "task_lease_ttl_ceiling_seconds": 300,
        "hash_profiles": [
            "attempt_claim",
            "attempt_finish",
            "attempt_heartbeat",
            "reconciliation_request",
            "task_cancel",
            "task_create",
            "task_pause",
            "task_resume_request",
        ],
        "identity_stages": _identity_stage_mappings(),
        "forbidden_source_paths": [
            "backend/src/omnibase/agent_runtime",
            "backend/src/omnibase/agent_tasks",
            "backend/src/omnibase/agent_task_ledger",
            "backend/src/omnibase/agent_invocations",
            "backend/src/omnibase/planner",
            "backend/src/omnibase/executor",
            "backend/src/omnibase/dispatcher",
            "backend/src/omnibase/scheduler",
            "backend/src/omnibase/multi_agent",
        ],
        "baseline_migration_revisions": [
            "0001",
            "0002",
            "0003",
            "0004",
            "0005",
            "0006",
            "0007",
            "0008",
            "0009",
            "0010",
            "0011",
            "0012",
            "0013",
            "0014",
            "0015",
            "0016",
        ],
        "sealed_contracts": [
            {
                "name": "task_ledger_contract_doc",
                "path": "docs/phase-5-task-ledger-contract.md",
                "sha256": "4" * 64,
            },
            {
                "name": "task_ledger_contract_module",
                "path": "backend/src/omnibase/production/phase5_task_ledger_contract.py",
                "sha256": "5" * 64,
            },
            {
                "name": "task_ledger_tests",
                "path": "backend/tests/test_p5_2a_task_ledger_contract.py",
                "sha256": "6" * 64,
            },
            {
                "name": "threat_model",
                "path": "docs/phase-5-threat-model.md",
                "sha256": "7" * 64,
            },
            {
                "name": "maintainer_map",
                "path": "docs/maintainers/maintenance-map.json",
                "sha256": "8" * 64,
            },
            {
                "name": "security_invariants",
                "path": "docs/maintainers/security-invariants.md",
                "sha256": "9" * 64,
            },
        ],
        "openapi_snapshot": {
            "path": "sdk/contracts/p34-2-openapi.snapshot.json",
            "sha256": "a" * 64,
        },
        "ledger_contracts": {
            "tasks": [_task_mapping()],
            "runs": [_run_mapping()],
            "steps": [_step_mapping(), _step_2_mapping()],
            "attempts": [_attempt_1_mapping(), _attempt_mapping(), _attempt_3_mapping()],
            "task_leases": [_lease_mapping(), _lease_2_mapping()],
            "effects": [_effect_mapping()],
            "checkpoints": [_checkpoint_mapping()],
            "budget_ledgers": [_budget_ledger_mapping()],
            "lease_expiry_bounds": _expiry_bounds_mapping(),
        },
        "critical_veto": {"expected": 0},
    }


def _source(*, clean: bool = True) -> GitSourceProvenance:
    return GitSourceProvenance(
        git_commit="a" * 40,
        git_tree="b" * 40,
        remote_origin="https://github.com/lss100200/omnibase.git",
        clean=clean,
        dirty_paths=()
        if clean
        else (" M backend/src/omnibase/production/phase5_task_ledger_contract.py",),
        file_count=1,
        files=(("AGENTS.md", 1, "c" * 64),),
        manifest_sha256="d" * 64,
    )


def _write_file(repo: Path, relative: str, content: str | bytes) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8", newline="")


def _build_synthetic_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    if repo.exists():
        shutil.rmtree(repo)
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "P5.2A test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "core.autocrlf", "false"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "remote",
            "add",
            "origin",
            "https://github.com/lss100200/omnibase.git",
        ],
        check=True,
        capture_output=True,
    )
    _write_file(repo, ".gitignore", ".env\n")
    for revision, down in (
        ("0001", None),
        ("0002", "0001"),
        ("0003", "0002"),
        ("0004", "0003"),
        ("0005", "0004"),
        ("0006", "0005"),
        ("0007", "0006"),
        ("0008", "0007"),
        ("0009", "0008"),
        ("0010", "0009"),
        ("0011", "0010"),
        ("0012", "0011"),
        ("0013", "0012"),
        ("0014", "0013"),
        ("0015", "0014"),
        ("0016", "0015"),
    ):
        _write_file(
            repo,
            f"backend/src/omnibase/migrations/versions/{revision}_migration.py",
            f'revision: str = "{revision}"\n' f"down_revision: str | None = {down!r}\n",
        )
    _write_file(
        repo,
        "sdk/contracts/p34-2-openapi.snapshot.json",
        json.dumps({"openapi": "3.1.0", "paths": {"/gateway/v1/data/schema/read": {}}}),
    )
    _write_file(repo, "docs/phase-5-task-ledger-contract.md", "# contract\n")
    _write_file(repo, "docs/phase-5-threat-model.md", "# threat model\n")
    _write_file(repo, "docs/maintainers/maintenance-map.json", "{}\n")
    _write_file(repo, "docs/maintainers/security-invariants.md", "# invariants\n")
    _write_file(repo, "backend/tests/test_p5_2a_task_ledger_contract.py", "# tests\n")
    _write_file(
        repo,
        "backend/src/omnibase/production/phase5_task_ledger_contract.py",
        "# module\n",
    )
    _write_file(repo, "deployment/production/phase5-admission.example.json", "{}\n")
    _write_file(repo, "deployment/production/phase5-registry-contract.example.json", "{}\n")
    _write_file(repo, "docs/evidence/p34-7/production-readiness-decision.md", "# decision\n")
    _write_file(repo, "backend/src/omnibase/production/source.py", "VALUE = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "synthetic base"], check=True, capture_output=True
    )
    return repo


def _synthetic_config(tmp_path: Path, repo: Path | None = None) -> TaskLedgerContractConfig:
    repo = repo or _build_synthetic_repo(tmp_path)
    mapping = _contract_mapping()

    def seal(relative: str, content: str) -> str:
        _write_file(repo, relative, content)
        return _digest((repo / relative).read_text(encoding="utf-8"))

    mapping["p34_7"]["decision"]["sha256"] = seal(
        "docs/evidence/p34-7/production-readiness-decision.md",
        "# P34.7 decision\nP34.7 production total Gate: BLOCKED / NOT_PROVEN\n",
    )
    mapping["p5_0"]["admission_contract"]["sha256"] = seal(
        "deployment/production/phase5-admission.example.json",
        json.dumps({"activation_requested": False}),
    )
    mapping["p5_1"]["registry_contract"]["sha256"] = seal(
        "deployment/production/phase5-registry-contract.example.json",
        json.dumps({"phase": "P5.1A"}),
    )
    mapping["openapi_snapshot"]["sha256"] = _digest(
        (repo / "sdk/contracts/p34-2-openapi.snapshot.json").read_text(encoding="utf-8")
    )
    sealed = [
        ("task_ledger_contract_doc", "docs/phase-5-task-ledger-contract.md", "# contract\n"),
        (
            "task_ledger_contract_module",
            "backend/src/omnibase/production/phase5_task_ledger_contract.py",
            "# module\n",
        ),
        ("task_ledger_tests", "backend/tests/test_p5_2a_task_ledger_contract.py", "# tests\n"),
        ("threat_model", "docs/phase-5-threat-model.md", "# threat model\n"),
        ("maintainer_map", "docs/maintainers/maintenance-map.json", "{}\n"),
        ("security_invariants", "docs/maintainers/security-invariants.md", "# invariants\n"),
    ]
    mapping["sealed_contracts"] = [
        {"name": name, "path": path, "sha256": seal(path, content)}
        for name, path, content in sealed
    ]
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "sealed fixtures"], check=True, capture_output=True
    )
    return TaskLedgerContractConfig.from_mapping(mapping)


def _synthetic_config_with_evidence(
    tmp_path: Path,
    *,
    evidence: list[dict[str, object]],
) -> tuple[TaskLedgerContractConfig, Path]:
    """Build a sealed synthetic config whose evidence block is fully customizable.

    Mirrors ``_synthetic_config`` but lets the caller supply passed/not_proven
    evidence references (with real paths/digests/assertions) so the verify path
    exercises real evidence verification instead of the checked-in not_proven
    reference. The caller must write any passed-evidence file itself and seal
    the digest into the evidence block before calling this helper.
    """
    repo = _build_synthetic_repo(tmp_path)
    mapping = _contract_mapping()
    mapping["evidence"] = evidence

    def seal(relative: str, content: str) -> str:
        _write_file(repo, relative, content)
        return _digest((repo / relative).read_text(encoding="utf-8"))

    mapping["p34_7"]["decision"]["sha256"] = seal(
        "docs/evidence/p34-7/production-readiness-decision.md",
        "# P34.7 decision\nP34.7 production total Gate: BLOCKED / NOT_PROVEN\n",
    )
    mapping["p5_0"]["admission_contract"]["sha256"] = seal(
        "deployment/production/phase5-admission.example.json",
        json.dumps({"activation_requested": False}),
    )
    mapping["p5_1"]["registry_contract"]["sha256"] = seal(
        "deployment/production/phase5-registry-contract.example.json",
        json.dumps({"phase": "P5.1A"}),
    )
    mapping["openapi_snapshot"]["sha256"] = _digest(
        (repo / "sdk/contracts/p34-2-openapi.snapshot.json").read_text(encoding="utf-8")
    )
    sealed = [
        ("task_ledger_contract_doc", "docs/phase-5-task-ledger-contract.md", "# contract\n"),
        (
            "task_ledger_contract_module",
            "backend/src/omnibase/production/phase5_task_ledger_contract.py",
            "# module\n",
        ),
        ("task_ledger_tests", "backend/tests/test_p5_2a_task_ledger_contract.py", "# tests\n"),
        ("threat_model", "docs/phase-5-threat-model.md", "# threat model\n"),
        ("maintainer_map", "docs/maintainers/maintenance-map.json", "{}\n"),
        ("security_invariants", "docs/maintainers/security-invariants.md", "# invariants\n"),
    ]
    mapping["sealed_contracts"] = [
        {"name": name, "path": path, "sha256": seal(path, content)}
        for name, path, content in sealed
    ]
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "sealed fixtures"], check=True, capture_output=True
    )
    return TaskLedgerContractConfig.from_mapping(mapping), repo


# ---------------------------------------------------------------------------
# Positive fixtures
# ---------------------------------------------------------------------------


def test_checked_in_contract_is_valid_but_explicitly_blocked() -> None:
    config = (
        load_task_ledger_contract_config(CONFIG_PATH)
        if CONFIG_PATH.exists()
        else TaskLedgerContractConfig.from_mapping(_contract_mapping())
    )

    report = TaskLedgerContractGate(REPO_ROOT).validate_only(config)

    assert report.state is AdmissionState.BLOCKED
    assert report.activation_allowed is False
    assert report.contract_valid is True
    assert any("P34.7 formal state is not ready" in item for item in report.blockers)
    assert any("P5.0 admission formal state is not ready" in item for item in report.blockers)
    assert any("P5.1 production formal state is not ready" in item for item in report.blockers)
    assert any("persistence ledger is not implemented" in item for item in report.blockers)
    assert any("Agent Runtime is not implemented" in item for item in report.blockers)


def test_identity_dtos_parse_positive() -> None:
    task = AgentTaskInvocation.from_mapping(_task_mapping())
    assert task.state is TaskState.SCHEDULED
    assert task.task_generation == 1

    run = AgentRunBinding.from_mapping(_run_mapping())
    assert run.runtime_instance_id == RUNTIME_INSTANCE_ID
    assert run.node_fencing_token == 1

    step = AgentStep.from_mapping(_step_mapping())
    assert step.state is StepState.RUNNING

    attempt = AgentAttempt.from_mapping(_attempt_mapping())
    assert attempt.attempt_number == 2
    assert attempt.task_fencing_token == 9

    lease = TaskLeaseContract.from_mapping(_lease_mapping())
    assert lease.task_fencing_token == 9

    effect = ProviderEffect.from_mapping(_effect_mapping())
    assert effect.state is EffectState.RESERVED

    checkpoint = CheckpointReference.from_mapping(_checkpoint_mapping(), ceilings=_ceilings())
    assert checkpoint.committed_plan_version == "1"

    bounds = LeaseExpiryBounds.from_mapping(_expiry_bounds_mapping())
    assert bounds.task_lease_expiry == "2026-08-03T00:15:00Z"


def test_budget_ledger_invariants_and_remaining() -> None:
    ledger = BudgetLedgerSnapshot.from_mapping(_budget_ledger_mapping(), ceilings=_ceilings())
    assert len(ledger.dimensions) == 12
    input_tokens = next(
        item for item in ledger.dimensions if item.dimension.value == "input_tokens"
    )
    assert input_tokens.remaining == input_tokens.limit - input_tokens.reserved
    for item in ledger.dimensions:
        assert item.reserved >= item.committed
        assert item.reserved <= item.limit
        assert item.released <= item.committed
        assert item.remaining >= 0


def test_budget_dimension_ledger_positive() -> None:
    ledger = BudgetDimensionLedger.from_mapping(
        {"dimension": "tool_calls", "limit": 50, "reserved": 5, "committed": 4, "released": 1},
        name="budget",
        ceilings=_ceilings(),
    )
    assert ledger.remaining == 45


def test_hash_profile_determinism_and_closed_fields() -> None:
    payload = {
        "operation": "agent.task.cancel",
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "workspace_generation": 1,
        "task_id": TASK_ID,
        "task_generation": 1,
        "expected_previous_state": "scheduled",
        "cancellation_target": "attempt:" + ATTEMPT_2_ID,
    }
    first = compute_request_hash("task_cancel", payload)
    second = compute_request_hash("task_cancel", dict(payload))
    assert first == second
    with pytest.raises(TaskLedgerContractError, match="has unexpected fields"):
        compute_request_hash("task_cancel", {**payload, "server_timestamp": "2026-08-03T00:00:00Z"})
    with pytest.raises(TaskLedgerContractError, match="is missing fields"):
        compute_request_hash(
            "task_cancel", {key: value for key, value in payload.items() if key != "task_id"}
        )
    with pytest.raises(TaskLedgerContractError, match="unknown hash profile"):
        compute_request_hash("task_resurrect", payload)


def test_exact_replay_classification() -> None:
    assert (
        classify_replay(same_idempotency_key=False, same_operation=True, same_payload_digest=True)
        is ReplayClass.NOT_A_REPLAY
    )
    assert (
        classify_replay(same_idempotency_key=True, same_operation=True, same_payload_digest=True)
        is ReplayClass.EXACT_REPLAY
    )
    with pytest.raises(
        TaskLedgerContractError, match="reused with a different operation or payload"
    ):
        classify_replay(same_idempotency_key=True, same_operation=False, same_payload_digest=True)
    with pytest.raises(
        TaskLedgerContractError, match="reused with a different operation or payload"
    ):
        classify_replay(same_idempotency_key=True, same_operation=True, same_payload_digest=False)


def test_state_machine_transitions_positive() -> None:
    validate_task_transition(TaskState.SCHEDULED, TaskState.RUNNING)
    validate_attempt_transition(AttemptState.DISPATCHING, AttemptState.RUNNING)
    validate_effect_transition(EffectState.RESERVED, EffectState.DISPATCHING)
    validate_step_transition(StepState.PENDING, StepState.READY)
    validate_retry(
        previous_attempt_number=1, previous_task_fencing=5, new_attempt_number=2, new_task_fencing=9
    )
    validate_cancel_attempt(state=AttemptState.LEASED, has_unknown_effect=False)


def test_identity_stage_submission_positive() -> None:
    rules = IdentityStageRules.from_mapping(_identity_stage_mappings()[0])
    rules.validate_submission(FieldOrigin.BROWSER, frozenset({"tenant_id", "task_id", "deadline"}))
    rules.validate_submission(FieldOrigin.CORE, frozenset({"request_hash"}))


def test_request_hash_verification_positive() -> None:
    task = AgentTaskInvocation.from_mapping(_task_mapping())
    assert task.request_hash == compute_request_hash(
        "task_create",
        {
            "operation": "agent.task.create",
            "task_id": TASK_ID,
            "tenant_id": TENANT_ID,
            "workspace_id": WORKSPACE_ID,
            "workspace_generation": 1,
            "agent_definition_id": DEFINITION_ID,
            "agent_version_id": VERSION_ID,
            "agent_version_digest": VERSION_DIGEST,
            "workspace_agent_binding_id": BINDING_ID,
            "resource_scope_digest": RESOURCE_SCOPE_DIGEST,
            "budget_policy_digest": BUDGET_POLICY_DIGEST,
            "deadline": "2026-08-03T12:00:00Z",
        },
    )


# ---------------------------------------------------------------------------
# Negative matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        # 1. extra field
        (lambda d: d.update({"unexpected_top_level": True}), "unexpected fields"),
        (lambda d: d.update({"provider_url": "https://api.provider.example"}), "unexpected fields"),
        (lambda d: d.update({"api_key": "sk-live"}), "unexpected fields"),
        # 2. unknown state
        (lambda d: d.update({"state": "dispatching"}), "unknown or malformed state"),
        # 3. case-drifted state
        (lambda d: d.update({"state": "SCHEDULED"}), "unknown or malformed state"),
        (lambda d: d.update({"state": " scheduled"}), "unknown or malformed state"),
        # 4. boolean-as-int
        (lambda d: d.update({"workspace_generation": True}), "must be a positive integer"),
        # 5. fractional integer
        (lambda d: d.update({"workspace_generation": 1.5}), "must be a positive integer"),
        # 11. invalid UUID
        (lambda d: d.update({"task_id": "not-a-uuid"}), "strict lowercase UUID"),
        (
            lambda d: d.update({"task_id": "ABCDEFAB-CDEF-ABCD-EFAB-CDEFABCDEFAB"}),
            "strict lowercase UUID",
        ),
        # 12. invalid digest
        (lambda d: d.update({"agent_version_digest": "abc"}), "lowercase 64-character SHA-256"),
        (lambda d: d.update({"agent_version_digest": "A" * 64}), "lowercase 64-character SHA-256"),
        # 13. naive datetime
        (
            lambda d: d.update({"deadline": "2026-08-03T12:00:00"}),
            "must be an ISO-8601 UTC timestamp",
        ),
        (lambda d: d.update({"deadline": "2026-08-03"}), "must be an ISO-8601 UTC timestamp"),
        # 17. missing Workspace generation
        (lambda d: d.pop("workspace_generation"), "must be a positive integer"),
        # 28. caller request_hash override
        (lambda d: d.update({"request_hash_override": "0" * 64}), "unexpected fields"),
        # 32. provider fields
        (lambda d: d.update({"provider_base_url": "https://api.example"}), "unexpected fields"),
        # 33. DATABASE_URL
        (lambda d: d.update({"database_url": "postgresql://u:p@h/db"}), "unexpected fields"),
        # 34. PostgreSQL physical locator
        (lambda d: d.update({"database_schema": "tenant_42"}), "unexpected fields"),
        (lambda d: d.update({"database_table": "agent_tasks"}), "unexpected fields"),
        # 35. host path
        (lambda d: d.update({"host_path": "/var/run"}), "unexpected fields"),
        # 36. Docker socket
        (lambda d: d.update({"docker_socket": "/var/run/docker.sock"}), "unexpected fields"),
        # 31. Browser JWT material in a workload/identity DTO
        (lambda d: d.update({"authorization": "Bearer x"}), "unexpected fields"),
        (lambda d: d.update({"browser_jwt": "eyJ"}), "unexpected fields"),
    ],
)
def test_task_negative_fixtures(mutation, message: str) -> None:
    mapping = _task_mapping()
    mutation(mapping)
    with pytest.raises(ConfigurationError, match=message):
        AgentTaskInvocation.from_mapping(mapping)


def test_request_hash_drift_is_rejected() -> None:
    mapping = _task_mapping()
    mapping["request_hash"] = "0" * 64
    with pytest.raises(TaskLedgerContractError, match="does not match the canonical hash profile"):
        AgentTaskInvocation.from_mapping(mapping)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        # 4/5. boolean/fractional fencing
        (lambda r: r.update({"run_fencing_token": True}), "must be a positive integer"),
        (lambda r: r.update({"node_fencing_token": 1.5}), "must be a positive integer"),
        # 19. missing Node fencing
        (
            lambda r: r.update({"node_fencing_token": None}),
            "must be all-or-none",
        ),
        # 20. missing Run fencing
        (
            lambda r: r.update({"run_fencing_token": None}),
            "must be all-or-none",
        ),
        # 30. workload credential as a plain field
        (lambda r: r.update({"workload_token": "raw-token"}), "unexpected fields"),
        (lambda r: r.update({"certificate_private_key": "-----BEGIN"}), "unexpected fields"),
    ],
)
def test_run_negative_fixtures(mutation, message: str) -> None:
    mapping = _run_mapping()
    mutation(mapping)
    with pytest.raises(ConfigurationError, match=message):
        AgentRunBinding.from_mapping(mapping)


def test_terminal_run_must_not_retain_runtime_identity() -> None:
    mapping = _run_mapping()
    mapping["state"] = "succeeded"
    with pytest.raises(TaskLedgerContractError, match="terminal agent run must not retain"):
        AgentRunBinding.from_mapping(mapping)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda s: s.update({"dependencies": [STEP_ID]}), "must not reference the step itself"),
        (lambda s: s.update({"dependencies": [STEP_ID, STEP_ID]}), "must not contain duplicates"),
        (lambda s: s.update({"dependencies": ["*"]}), "without wildcards or path tricks"),
        (lambda s: s.update({"dependencies": ["all"]}), "without wildcards or path tricks"),
        (lambda s: s.update({"dependencies": ["../../etc"]}), "without wildcards or path tricks"),
        (lambda s: s.update({"state": "committed"}), "unknown or malformed state"),
    ],
)
def test_step_negative_fixtures(mutation, message: str) -> None:
    mapping = _step_mapping()
    mutation(mapping)
    with pytest.raises(ConfigurationError, match=message):
        AgentStep.from_mapping(mapping)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda a: a.update({"attempt_number": 0}), "must be a positive integer"),
        # 21. missing Task fencing
        (lambda a: a.update({"task_fencing_token": None}), "must be provided together"),
        (lambda a: a.update({"task_lease_id": None}), "must be provided together"),
        (
            lambda a: a.update(
                {"state": "dispatching", "task_lease_id": None, "task_fencing_token": None}
            ),
            "leased, dispatching and running attempts require",
        ),
        (
            lambda a: a.update(
                {"state": "leased", "task_lease_id": None, "task_fencing_token": None}
            ),
            "leased, dispatching and running attempts require",
        ),
        (
            lambda a: a.update(
                {"state": "running", "task_lease_id": None, "task_fencing_token": None}
            ),
            "leased, dispatching and running attempts require",
        ),
        (
            lambda a: a.update({"state": "pending", "task_lease_id": TASK_LEASE_ID}),
            "pre-dispatch attempt must not carry",
        ),
        (
            lambda a: a.update({"state": "ready", "task_lease_id": TASK_LEASE_ID}),
            "pre-dispatch attempt must not carry",
        ),
        (
            lambda a: a.update(
                {"state": "committed", "task_lease_id": TASK_LEASE_ID, "task_fencing_token": 9}
            ),
            "terminal attempt must not retain",
        ),
        (
            lambda a: a.update(
                {"state": "unknown", "task_lease_id": TASK_LEASE_ID, "task_fencing_token": 9}
            ),
            "terminal attempt must not retain",
        ),
        (
            lambda a: a.update(
                {"state": "cancelled", "task_lease_id": TASK_LEASE_ID, "task_fencing_token": 9}
            ),
            "terminal attempt must not retain",
        ),
        (
            lambda a: a.update({"expected_previous_state": "suspended"}),
            "unknown or malformed state",
        ),
    ],
)
def test_attempt_negative_fixtures(mutation, message: str) -> None:
    mapping = _attempt_mapping()
    mutation(mapping)
    with pytest.raises(ConfigurationError, match=message):
        AgentAttempt.from_mapping(mapping)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        # 21. missing Task fencing
        (lambda lease: lease.update({"task_fencing_token": None}), "must be a positive integer"),
        (lambda lease: lease.update({"run_fencing_token": 0}), "must be a positive integer"),
        (lambda lease: lease.update({"node_fencing_token": -1}), "must be a positive integer"),
        (
            lambda lease: lease.update({"state": "active", "expires_at": "2026-08-03T00:00:00Z"}),
            "must expire after creation",
        ),
        (
            lambda lease: lease.update({"state": "completed", "heartbeat_at": None}),
            "must record a final heartbeat_at",
        ),
        (lambda lease: lease.update({"pid": 1234}), "unexpected fields"),
        (lambda lease: lease.update({"provider_handle": "job-42"}), "unexpected fields"),
        (lambda lease: lease.update({"socket": "/run/omnibase.sock"}), "unexpected fields"),
    ],
)
def test_task_lease_negative_fixtures(mutation, message: str) -> None:
    mapping = _lease_mapping()
    mutation(mapping)
    with pytest.raises(ConfigurationError, match=message):
        TaskLeaseContract.from_mapping(mapping)


def test_task_lease_ttl_ceiling_is_enforced() -> None:
    mapping = _lease_mapping()
    mapping["expires_at"] = "2026-08-03T00:20:00Z"
    with pytest.raises(TaskLedgerContractError, match="exceeds the configured ceiling"):
        TaskLeaseContract.from_mapping(mapping)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda e: e.update({"state": "committed", "result_digest": None}),
            "requires a committed result_digest",
        ),
        (
            lambda e: e.update({"state": "reserved", "result_digest": "0" * 64}),
            "only allowed on a committed effect",
        ),
        (lambda e: e.update({"state": "succeeded"}), "unknown or malformed state"),
        (lambda e: e.update({"result_digest": "not-a-digest"}), "lowercase 64-character SHA-256"),
        (lambda e: e.update({"model_output": "raw completion"}), "unexpected fields"),
    ],
)
def test_effect_negative_fixtures(mutation, message: str) -> None:
    mapping = _effect_mapping()
    mutation(mapping)
    with pytest.raises(ConfigurationError, match=message):
        ProviderEffect.from_mapping(mapping)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda c: c.update({"committed_attempt_results": []}), "non-empty and unique"),
        (
            lambda c: c.update({"committed_attempt_results": ["*"]}),
            "without wildcards or path tricks",
        ),
        (
            lambda c: c.update({"committed_attempt_results": ["../../etc"]}),
            "without wildcards or path tricks",
        ),
        # 37. PID/socket/provider handle in a checkpoint
        (lambda c: c.update({"pid": 1234}), "unexpected fields"),
        (lambda c: c.update({"socket": "tcp://1.2.3.4:9000"}), "unexpected fields"),
        (lambda c: c.update({"provider_handle": "job-42"}), "unexpected fields"),
        (lambda c: c.update({"lease_token": "raw"}), "unexpected fields"),
        (lambda c: c.update({"host_path": "/var/lib"}), "unexpected fields"),
    ],
)
def test_checkpoint_negative_fixtures(mutation, message: str) -> None:
    mapping = _checkpoint_mapping()
    mutation(mapping)
    with pytest.raises(ConfigurationError, match=message):
        CheckpointReference.from_mapping(mapping, ceilings=_ceilings())


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        # 6. negative budget
        (lambda b: b["dimensions"][0].update({"reserved": -1}), "must be a non-negative integer"),
        (lambda b: b["dimensions"][0].update({"committed": -5}), "must be a non-negative integer"),
        # 7. budget overflow
        (lambda b: b["dimensions"][0].update({"reserved": 1 << 63}), "exceeds the maximum integer"),
        # 8. unknown budget dimension
        (
            lambda b: b["dimensions"][0].update({"dimension": "gpu_hours"}),
            "unknown budget dimension",
        ),
        # 9/10. wildcard dimension
        (lambda b: b["dimensions"][0].update({"dimension": "*"}), "unknown budget dimension"),
        # 4. boolean-as-int budget
        (lambda b: b["dimensions"][0].update({"limit": True}), "must be a positive integer"),
        # 5. fractional budget
        (lambda b: b["dimensions"][0].update({"reserved": 1.5}), "must be a non-negative integer"),
        (
            lambda b: b["dimensions"][0].update({"limit": 99999999}),
            "exceeds the server-owned ceiling",
        ),
        (lambda b: b["dimensions"].pop(0), "must cover the closed set"),
        (lambda b: b["dimensions"].append(dict(b["dimensions"][0])), "must cover the closed set"),
        (lambda b: b.update({"policy_digest": "short"}), "lowercase 64-character SHA-256"),
    ],
)
def test_budget_negative_fixtures(mutation, message: str) -> None:
    mapping = _budget_ledger_mapping()
    mutation(mapping)
    with pytest.raises(ConfigurationError, match=message):
        BudgetLedgerSnapshot.from_mapping(mapping, ceilings=_ceilings())


def test_budget_invariant_reserved_below_committed_is_rejected() -> None:
    mapping = _budget_ledger_mapping()
    mapping["dimensions"][0]["reserved"] = 1
    mapping["dimensions"][0]["committed"] = 2
    with pytest.raises(TaskLedgerContractError, match="must not be less than"):
        BudgetLedgerSnapshot.from_mapping(mapping, ceilings=_ceilings())


def test_budget_invariant_reserved_above_limit_is_rejected() -> None:
    mapping = _budget_ledger_mapping()
    mapping["dimensions"][0]["reserved"] = 1000001
    with pytest.raises(TaskLedgerContractError, match="must not exceed"):
        BudgetLedgerSnapshot.from_mapping(mapping, ceilings=_ceilings())


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        # 14. expiry later than deadline
        (
            lambda b: b.update(
                {
                    "task_deadline": "2026-08-03T00:10:00Z",
                    "task_lease_expiry": "2026-08-03T00:15:00Z",
                }
            ),
            "task lease expiry must not be later than the task deadline",
        ),
        # 15. Task Lease expiry later than Run Lease
        (
            lambda b: b.update({"run_lease_expiry": "2026-08-03T00:10:00Z"}),
            "task lease expiry must not be later than the run lease expiry",
        ),
        # 16. Task Lease expiry later than Grant
        (
            lambda b: b.update({"capability_grant_expiry": "2026-08-03T00:10:00Z"}),
            "task lease expiry must not be later than the capability grant expiry",
        ),
        (
            lambda b: b.update({"node_attestation_expiry": "2026-08-03T00:10:00Z"}),
            "task lease expiry must not be later than the node attestation expiry",
        ),
        (
            lambda b: b.update({"workspace_policy_expiry": "2026-08-03T00:10:00Z"}),
            "task lease expiry must not be later than the workspace policy expiry",
        ),
    ],
)
def test_lease_ttl_bound_negatives(mutation, message: str) -> None:
    mapping = _expiry_bounds_mapping()
    mutation(mapping)
    with pytest.raises(TaskLedgerContractError, match=message):
        LeaseExpiryBounds.from_mapping(mapping)


def test_contract_level_negatives() -> None:
    # 18. stale Workspace generation
    mapping = _contract_mapping()
    mapping["ledger_contracts"]["runs"][0]["workspace_generation"] = 2
    with pytest.raises(TaskLedgerContractError, match="binds a stale workspace generation"):
        TaskLedgerContractConfig.from_mapping(mapping)

    # 22. Task fencing regression (same step, later attempt with lower fencing)
    mapping = _contract_mapping()
    attempts = mapping["ledger_contracts"]["attempts"]
    third = _attempt_mapping(ATTEMPT_4_ID, 3)
    third["task_lease_id"] = LEASE_3_ID
    third["task_fencing_token"] = 5
    attempts.append(third)
    lease3 = _lease_mapping()
    lease3["task_lease_id"] = LEASE_3_ID
    lease3["attempt_id"] = ATTEMPT_4_ID
    lease3["task_fencing_token"] = 5
    lease3["created_at"] = "2026-08-03T00:10:00Z"
    mapping["ledger_contracts"]["task_leases"].append(lease3)
    with pytest.raises(TaskLedgerContractError, match="retry task fencing must increase"):
        TaskLedgerContractConfig.from_mapping(mapping)

    # 23. Attempt number regression / duplicate (now caught by the contiguous
    # from-1 sequence check before the retry fencing rule)
    mapping = _contract_mapping()
    attempts = mapping["ledger_contracts"]["attempts"]
    second = _attempt_mapping()
    second["attempt_number"] = 1
    attempts[1] = second
    with pytest.raises(
        TaskLedgerContractError, match="must form a contiguous sequence starting at 1"
    ):
        TaskLedgerContractConfig.from_mapping(mapping)

    # 39. retry reusing the old attempt identity
    mapping = _contract_mapping()
    attempts = mapping["ledger_contracts"]["attempts"]
    attempts.append(dict(attempts[1]))
    with pytest.raises(TaskLedgerContractError, match="attempt IDs must be unique"):
        TaskLedgerContractConfig.from_mapping(mapping)

    # 41. lease not bound to the current run lease
    mapping = _contract_mapping()
    mapping["ledger_contracts"]["task_leases"][0]["run_lease_id"] = (
        "99999999-9999-9999-9999-999999999999"
    )
    with pytest.raises(TaskLedgerContractError, match="does not bind the current run lease"):
        TaskLedgerContractConfig.from_mapping(mapping)

    # 41b. stale run fencing token
    mapping = _contract_mapping()
    mapping["ledger_contracts"]["task_leases"][0]["run_fencing_token"] = 2
    with pytest.raises(TaskLedgerContractError, match="binds a stale run fencing token"):
        TaskLedgerContractConfig.from_mapping(mapping)

    # lease/node fencing mismatch
    mapping = _contract_mapping()
    mapping["ledger_contracts"]["task_leases"][0]["node_fencing_token"] = 2
    with pytest.raises(TaskLedgerContractError, match="does not bind the current node fencing"):
        TaskLedgerContractConfig.from_mapping(mapping)

    # checkpoint must reference a committed attempt
    mapping = _contract_mapping()
    mapping["ledger_contracts"]["checkpoints"][0]["attempt_id"] = ATTEMPT_2_ID
    with pytest.raises(TaskLedgerContractError, match="must reference a committed attempt"):
        TaskLedgerContractConfig.from_mapping(mapping)

    # checkpoint budget drift
    mapping = _contract_mapping()
    mapping["ledger_contracts"]["checkpoints"][0]["budget_ledger"]["policy_digest"] = "0" * 64
    with pytest.raises(TaskLedgerContractError, match="budget ledger drifts"):
        TaskLedgerContractConfig.from_mapping(mapping)

    # lease expiry disagrees with the bounds contract (still after creation and
    # within the TTL ceiling so the dedicated reason code fires)
    mapping = _contract_mapping()
    mapping["ledger_contracts"]["task_leases"][0]["expires_at"] = "2026-08-03T00:14:30Z"
    with pytest.raises(TaskLedgerContractError, match="expiry disagrees"):
        TaskLedgerContractConfig.from_mapping(mapping)

    # cross tenant/workspace run binding
    mapping = _contract_mapping()
    mapping["ledger_contracts"]["runs"][0]["tenant_id"] = "00000000-0000-0000-0000-00000000000b"
    with pytest.raises(TaskLedgerContractError, match="crosses the tenant/workspace boundary"):
        TaskLedgerContractConfig.from_mapping(mapping)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda d: d.update({"schema_version": 2}), "schema_version must be 1"),
        (lambda d: d.update({"phase": "P5.2"}), "phase must be P5.2A"),
        # activation_requested=true is a veto-level contract violation
        (lambda d: d.update({"activation_requested": True}), "activation_requested to be false"),
        (lambda d: d.update({"unexpected_top_level": True}), "unexpected fields"),
        (
            lambda d: d["feature_gates"].update({"agent_runtime_enabled": True}),
            "every Phase 5 feature gate to be disabled",
        ),
        (
            lambda d: d["feature_gates"].update({"multi_agent_enabled": True}),
            "every Phase 5 feature gate to be disabled",
        ),
        (lambda d: d["critical_veto"].update({"expected": 1}), "exactly 0"),
        (lambda d: d["forbidden_source_paths"].append(".env"), "root .env"),
        (lambda d: d["hash_profiles"].pop(0), "closed set of hash profiles"),
        (lambda d: d["identity_stages"].pop(0), "closed set of identity stages"),
        (lambda d: d.update({"deadline_ceiling_seconds": 999999999}), "may only tighten"),
        (lambda d: d.update({"task_lease_ttl_ceiling_seconds": 9999}), "may only tighten"),
        (lambda d: d["budget_ceilings"].update({"input_tokens": 999999999}), "may only tighten"),
    ],
)
def test_unsafe_contracts_fail_closed(mutation, message: str) -> None:
    mapping = _contract_mapping()
    mutation(mapping)
    with pytest.raises(ConfigurationError, match=message):
        TaskLedgerContractConfig.from_mapping(mapping)


def test_nan_and_infinity_are_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "contract.json"
    config_path.write_text(
        json.dumps(_contract_mapping()).replace('"input_tokens": 10000000', '"input_tokens": NaN'),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="non-finite"):
        load_task_ledger_contract_config(config_path)

    config_path.write_text(
        json.dumps(_contract_mapping()).replace(
            '"input_tokens": 10000000', '"input_tokens": Infinity'
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="non-finite"):
        load_task_ledger_contract_config(config_path)


def test_symlink_configuration_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "real-contract.json"
    target.write_text(json.dumps(_contract_mapping()), encoding="utf-8")
    link = tmp_path / "link-contract.json"
    link.symlink_to(target)
    with pytest.raises(ConfigurationError, match="regular non-link"):
        load_task_ledger_contract_config(link)


def test_report_output_inside_repository_is_rejected(tmp_path: Path) -> None:
    script = VALIDATOR_PATH
    if not script.exists():
        pytest.skip("full public checkout is not mounted in the backend test image")
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--validate-only",
            "--output",
            str(REPO_ROOT / ".p52a-inside-report.json"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "outside the repository" in result.stdout
    assert not (REPO_ROOT / ".p52a-inside-report.json").exists()


def test_identity_stage_submission_negatives() -> None:
    rules = IdentityStageRules.from_mapping(_identity_stage_mappings()[0])
    # 29. Browser submits runtime_instance_id
    with pytest.raises(
        TaskLedgerContractError, match="runtime_instance_id is not yet generated at task_create"
    ):
        rules.validate_submission(FieldOrigin.BROWSER, frozenset({"runtime_instance_id"}))
    # 30. Browser submits workload credential
    with pytest.raises(
        TaskLedgerContractError,
        match="workload_identity_thumbprint is not yet generated at task_create",
    ):
        rules.validate_submission(FieldOrigin.BROWSER, frozenset({"workload_identity_thumbprint"}))
    # 31. Browser JWT enters the identity DTO
    with pytest.raises(TaskLedgerContractError, match="unknown identity field: browser_jwt"):
        rules.validate_submission(FieldOrigin.BROWSER, frozenset({"browser_jwt"}))
    # core-generated request_hash is not browser-submittable
    with pytest.raises(
        TaskLedgerContractError, match="core-generated and must not be submitted by the browser"
    ):
        rules.validate_submission(FieldOrigin.BROWSER, frozenset({"request_hash"}))


def test_identity_stage_rules_must_be_consistent() -> None:
    mapping = _identity_stage_mappings()[0]
    mapping = dict(mapping)
    mapping["required_fields"] = sorted(set(mapping["required_fields"]) | {"task_lease_id"})
    with pytest.raises(TaskLedgerContractError, match="required field as not yet generated"):
        IdentityStageRules.from_mapping(mapping)

    mapping = _identity_stage_mappings()[0]
    mapping = dict(mapping)
    mapping["core_generated_fields"] = ["request_hash", "task_id"]
    with pytest.raises(TaskLedgerContractError, match="core-generated field as submittable"):
        IdentityStageRules.from_mapping(mapping)

    mapping = _identity_stage_mappings()[0]
    mapping = dict(mapping)
    mapping["required_fields"] = sorted(set(mapping["required_fields"]) | {"not_a_real_field"})
    with pytest.raises(TaskLedgerContractError, match="unknown identity fields"):
        IdentityStageRules.from_mapping(mapping)


def test_terminal_state_resurrection_is_rejected() -> None:
    # 24. terminal state resurrection
    with pytest.raises(TaskLedgerContractError, match="terminal task state cannot transition"):
        validate_task_transition(TaskState.SUCCEEDED, TaskState.RUNNING)
    with pytest.raises(TaskLedgerContractError, match="terminal task state cannot transition"):
        validate_task_transition(TaskState.CANCELLED, TaskState.SCHEDULED)
    with pytest.raises(TaskLedgerContractError, match="terminal attempt state cannot transition"):
        validate_attempt_transition(AttemptState.COMMITTED, AttemptState.RUNNING)
    with pytest.raises(TaskLedgerContractError, match="terminal attempt state cannot transition"):
        validate_attempt_transition(AttemptState.CANCELLED, AttemptState.PENDING)
    with pytest.raises(TaskLedgerContractError, match="terminal effect state cannot transition"):
        validate_effect_transition(EffectState.COMMITTED, EffectState.DISPATCHING)
    with pytest.raises(TaskLedgerContractError, match="terminal agent run state cannot transition"):
        validate_agent_run_transition(AgentRunState.SUCCEEDED, AgentRunState.RUNNING)


def test_unknown_state_never_replays() -> None:
    # 25. unknown -> dispatching / running
    with pytest.raises(TaskLedgerContractError, match="unknown attempt state cannot be replayed"):
        validate_attempt_transition(AttemptState.UNKNOWN, AttemptState.DISPATCHING)
    with pytest.raises(TaskLedgerContractError, match="unknown attempt state cannot be replayed"):
        validate_attempt_transition(AttemptState.UNKNOWN, AttemptState.RUNNING)
    with pytest.raises(TaskLedgerContractError, match="unknown effect state cannot be replayed"):
        validate_effect_transition(EffectState.UNKNOWN, EffectState.DISPATCHING)
    with pytest.raises(TaskLedgerContractError, match="unknown effect state cannot be replayed"):
        validate_effect_transition(EffectState.UNKNOWN, EffectState.RESERVED)


def test_cancel_never_disguises_unknown_outcome() -> None:
    # 38. cancel must not mask unknown provider outcomes as cancelled success
    with pytest.raises(
        TaskLedgerContractError, match="cannot disguise an unknown provider outcome"
    ):
        validate_cancel_attempt(state=AttemptState.RUNNING, has_unknown_effect=True)
    with pytest.raises(TaskLedgerContractError, match="cannot confirm a dispatched attempt"):
        validate_cancel_attempt(state=AttemptState.DISPATCHING, has_unknown_effect=False)
    with pytest.raises(TaskLedgerContractError, match="cannot confirm a dispatched attempt"):
        validate_cancel_target(AttemptState.RUNNING)


def test_retry_must_create_new_attempt_with_higher_fencing() -> None:
    # 22/23/40. retry semantics
    with pytest.raises(TaskLedgerContractError, match="retry task fencing must increase"):
        validate_retry(
            previous_attempt_number=1,
            previous_task_fencing=9,
            new_attempt_number=2,
            new_task_fencing=9,
        )
    with pytest.raises(TaskLedgerContractError, match="retry attempt number must increase"):
        validate_retry(
            previous_attempt_number=2,
            previous_task_fencing=5,
            new_attempt_number=1,
            new_task_fencing=9,
        )
    with pytest.raises(TaskLedgerContractError, match="retry task fencing must increase"):
        validate_retry(
            previous_attempt_number=1,
            previous_task_fencing=5,
            new_attempt_number=2,
            new_task_fencing=3,
        )


def test_old_identity_restart_is_rejected() -> None:
    # 41. restore old Run Lease
    with pytest.raises(TaskLedgerContractError, match="never restore the old lease"):
        validate_identity_restart(
            new_lease_id=TASK_LEASE_ID,
            new_runtime_instance_id=RUNTIME_INSTANCE_ID,
            new_workload_thumbprint=WORKLOAD_THUMBPRINT,
            previous_lease_id=TASK_LEASE_ID,
            previous_runtime_instance_id=RUNTIME_INSTANCE_ID,
            previous_workload_thumbprint=WORKLOAD_THUMBPRINT,
        )
    # 42. restore old runtime/workload identity
    with pytest.raises(TaskLedgerContractError, match="never restore the old runtime"):
        validate_identity_restart(
            new_lease_id="99999999-9999-9999-9999-999999999999",
            new_runtime_instance_id=RUNTIME_INSTANCE_ID,
            new_workload_thumbprint=WORKLOAD_THUMBPRINT,
            previous_lease_id=None,
            previous_runtime_instance_id=RUNTIME_INSTANCE_ID,
        )
    with pytest.raises(TaskLedgerContractError, match="never restore the old workload identity"):
        validate_identity_restart(
            new_lease_id="99999999-9999-9999-9999-999999999999",
            new_runtime_instance_id="12345678-1234-1234-1234-123456789099",
            new_workload_thumbprint=WORKLOAD_THUMBPRINT,
            previous_lease_id=None,
            previous_workload_thumbprint=WORKLOAD_THUMBPRINT,
        )


def test_model_output_is_not_committed_evidence() -> None:
    # 43. model output as committed evidence
    with pytest.raises(TaskLedgerContractError, match="model output is not authoritative"):
        validate_committed_evidence(CommittedEvidenceKind.MODEL_OUTPUT)
    with pytest.raises(TaskLedgerContractError, match="provider receipt alone"):
        validate_committed_evidence(CommittedEvidenceKind.PROVIDER_RECEIPT)
    validate_committed_evidence(CommittedEvidenceKind.OPERATION_LEDGER)
    validate_committed_evidence(CommittedEvidenceKind.EFFECT_LEDGER)
    validate_committed_evidence(CommittedEvidenceKind.AUDIT_EVENT)


# ---------------------------------------------------------------------------
# Formal verification negatives
# ---------------------------------------------------------------------------


def test_dirty_checkout_is_a_veto(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path)
    report = TaskLedgerContractGate(tmp_path / "repo").verify(config, source=_source(clean=False))
    assert report.state is AdmissionState.INVALID
    assert any("requires a clean checkout" in veto for veto in report.vetoes)


def test_feature_gate_true_is_a_veto(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path)
    report = TaskLedgerContractGate(tmp_path / "repo").verify(
        config,
        source=_source(),
        gate_values={"AGENT_RUNTIME_ENABLED": "true"},
    )
    assert report.state is AdmissionState.INVALID
    assert any("a true gate is a veto" in veto for veto in report.vetoes)


@pytest.mark.parametrize("token", ["TRUE", "yes", "on", "1", " true"])
def test_feature_gate_truthy_tokens_are_vetoes(tmp_path: Path, token: str) -> None:
    config = _synthetic_config(tmp_path)
    report = TaskLedgerContractGate(tmp_path / "repo").verify(
        config,
        source=_source(),
        gate_values={"AGENT_RUNTIME_ENABLED": token},
    )
    assert report.state is AdmissionState.INVALID
    assert any(veto.startswith("feature gates:") for veto in report.vetoes)


def test_remote_origin_mismatch_is_rejected(tmp_path: Path) -> None:
    repo = _build_synthetic_repo(tmp_path)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "remote",
            "set-url",
            "origin",
            "https://github.com/other/omnibase.git",
        ],
        check=True,
        capture_output=True,
    )
    config = _synthetic_config(tmp_path, repo=repo)
    with pytest.raises(ConfigurationError, match="remote origin does not match"):
        TaskLedgerContractGate(repo).verify(config)


def test_attempted_migration_0017_is_a_veto(tmp_path: Path) -> None:
    repo = _build_synthetic_repo(tmp_path)
    _write_file(
        repo,
        "backend/src/omnibase/migrations/versions/0017_unapproved_runtime.py",
        'revision: str = "0017"\ndown_revision: str | None = "0016"\n',
    )
    config = _synthetic_config(tmp_path, repo=repo)

    report = TaskLedgerContractGate(repo).verify(config, source=_source())

    assert report.state is AdmissionState.INVALID
    assert any(
        "migration head is 0017" in veto or "migration revision set drifted" in veto
        for veto in report.vetoes
    )


def test_attempted_runtime_orm_router_packages_are_vetoes(tmp_path: Path) -> None:
    for index, forbidden in enumerate(
        (
            "backend/src/omnibase/agent_runtime",
            "backend/src/omnibase/agent_tasks",
            "backend/src/omnibase/agent_invocations",
            "backend/src/omnibase/planner",
            "backend/src/omnibase/executor",
            "backend/src/omnibase/dispatcher",
            "backend/src/omnibase/scheduler",
            "backend/src/omnibase/multi_agent",
        )
    ):
        repo_dir = tmp_path / f"repo-{index}"
        repo_dir.mkdir(parents=True, exist_ok=True)
        repo = _build_synthetic_repo(repo_dir)
        _write_file(repo, f"{forbidden}/__init__.py", "")
        config = _synthetic_config(repo_dir, repo=repo)
        report = TaskLedgerContractGate(repo).verify(config, source=_source())
        assert report.state is AdmissionState.INVALID, forbidden
        assert any("forbidden source path exists" in veto for veto in report.vetoes), forbidden


def test_openapi_snapshot_with_agent_invocation_endpoint_is_a_veto(tmp_path: Path) -> None:
    repo = _build_synthetic_repo(tmp_path)
    _write_file(
        repo,
        "sdk/contracts/p34-2-openapi.snapshot.json",
        json.dumps({"openapi": "3.1.0", "paths": {"/api/v1/agent-invocations": {}}}),
    )
    config = _synthetic_config(tmp_path, repo=repo)
    config = replace(
        config,
        openapi_snapshot_sha256=_digest(
            (repo / "sdk/contracts/p34-2-openapi.snapshot.json").read_text(encoding="utf-8")
        ),
    )

    report = TaskLedgerContractGate(repo).verify(config, source=_source())

    assert report.state is AdmissionState.INVALID
    assert any("exposes an agent invocation endpoint" in veto for veto in report.vetoes)


def test_sealed_contract_digest_drift_is_a_veto(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path)
    config = replace(
        config,
        sealed_contracts=(("threat_model", "docs/phase-5-threat-model.md", "0" * 64),)
        + config.sealed_contracts[1:],
    )
    report = TaskLedgerContractGate(tmp_path / "repo").verify(config, source=_source())
    assert report.state is AdmissionState.INVALID
    assert any("sealed contract drifted" in veto for veto in report.vetoes)


def test_symlink_sealed_contract_to_synthetic_env_is_rejected(tmp_path: Path) -> None:
    repo = _build_synthetic_repo(tmp_path)
    synthetic_env = repo / "synthetic.env"
    synthetic_env.write_text("SECRET=synthetic\n", encoding="utf-8")
    target = repo / "docs" / "phase-5-threat-model.md"
    target.unlink()
    target.symlink_to(synthetic_env)
    config = _synthetic_config(tmp_path, repo=repo)

    report = TaskLedgerContractGate(repo).verify(config, source=_source())

    assert report.state is AdmissionState.INVALID
    assert any("link or reparse point" in veto for veto in report.vetoes)


def test_not_proven_evidence_is_never_counted_as_passed(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path)
    report = TaskLedgerContractGate(tmp_path / "repo").verify(config, source=_source())
    assert report.passed_evidence == ()
    assert any("not_proven" in item for item in report.blockers)


def test_report_never_claims_runtime_activated(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path)
    report = TaskLedgerContractGate(tmp_path / "repo").verify(config, source=_source())
    payload = report.to_dict()
    assert payload["task_ledger_orm_created"] is False
    assert payload["task_ledger_migration_created"] is False
    assert payload["agent_invocation_api_exposed"] is False
    assert payload["agent_runtime_created"] is False
    assert payload["planner_created"] is False
    assert payload["executor_created"] is False
    assert payload["scheduler_or_worker_started"] is False
    assert payload["model_or_tool_invoked"] is False
    assert payload["task_execution_activated"] is False
    assert payload["root_env_accessed"] is False
    assert payload["business_database_accessed"] is False
    assert payload["business_database_migrated"] is False
    assert payload["external_network_accessed"] is False


def test_validate_only_never_returns_ready() -> None:
    config = (
        load_task_ledger_contract_config(CONFIG_PATH)
        if CONFIG_PATH.exists()
        else TaskLedgerContractConfig.from_mapping(_contract_mapping())
    )
    report = TaskLedgerContractGate(REPO_ROOT).validate_only(config)
    assert report.state is not AdmissionState.READY


def test_formal_gate_keeps_missing_proofs_blocked() -> None:
    if not CONFIG_PATH.exists():
        pytest.skip("full public checkout is not mounted in the backend test image")
    config = load_task_ledger_contract_config(CONFIG_PATH)
    report = TaskLedgerContractGate(REPO_ROOT).verify(config, source=_source())
    assert report.state is AdmissionState.BLOCKED
    assert report.activation_allowed is False
    assert report.vetoes == ()
    assert any("P34.7 formal state is not ready" in item for item in report.blockers)
    assert any("P5.0 admission formal state is not ready" in item for item in report.blockers)
    assert any("P5.1 production formal state is not ready" in item for item in report.blockers)
    assert any("Agent Runtime gate remains disabled" in item for item in report.blockers)
    assert any("not_proven" in item for item in report.blockers)


def test_validator_reads_server_owned_feature_gate_environment(monkeypatch) -> None:
    namespace = runpy.run_path(str(VALIDATOR_PATH), run_name="p5_2a_validator_test")
    monkeypatch.setenv("AGENT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("AGENT_PLANNER_ENABLED", "false")
    monkeypatch.setenv("MULTI_AGENT_ENABLED", "false")

    values = namespace["_server_gate_values"]()

    assert values == {
        "AGENT_RUNTIME_ENABLED": "true",
        "AGENT_PLANNER_ENABLED": "false",
        "MULTI_AGENT_ENABLED": "false",
    }


def test_validator_rejects_parent_symlink_for_config_and_output_symlink(tmp_path: Path) -> None:
    namespace = runpy.run_path(str(VALIDATOR_PATH), run_name="p5_2a_validator_path_test")
    repo = tmp_path / "repo"
    real_config_dir = repo / "real-config"
    real_config_dir.mkdir(parents=True)
    (real_config_dir / "contract.json").write_text("{}\n", encoding="utf-8")
    linked_config_dir = repo / "linked-config"
    linked_config_dir.symlink_to(real_config_dir, target_is_directory=True)
    safe_config_path = namespace["_safe_config_path"]
    safe_config_path.__globals__["REPO_ROOT"] = repo

    with pytest.raises(ConfigurationError, match="link or reparse point"):
        safe_config_path(linked_config_dir / "contract.json", repo)

    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    victim = tmp_path / "victim.json"
    victim.write_text("unchanged\n", encoding="utf-8")
    linked_output = report_dir / "report.json"
    linked_output.symlink_to(victim)
    write_report = namespace["_write_report"]
    write_report.__globals__["REPO_ROOT"] = repo

    with pytest.raises(ConfigurationError, match="link or reparse point"):
        write_report(linked_output, {"state": "blocked/not_proven"}, repo)
    assert victim.read_text(encoding="utf-8") == "unchanged\n"


def test_config_path_escaping_repo_is_rejected(tmp_path: Path) -> None:
    namespace = runpy.run_path(str(VALIDATOR_PATH), run_name="p5_2a_validator_escape_test")
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside" / "contract.json"
    outside.parent.mkdir()
    outside.write_text("{}\n", encoding="utf-8")
    safe_config_path = namespace["_safe_config_path"]
    safe_config_path.__globals__["REPO_ROOT"] = repo
    with pytest.raises(ConfigurationError, match="escaped the repository"):
        safe_config_path(outside, repo)


# ---------------------------------------------------------------------------
# Import and source-boundary constraints
# ---------------------------------------------------------------------------


def test_task_ledger_contract_module_has_no_runtime_imports() -> None:
    module_file = (
        REPO_ROOT / "backend" / "src" / "omnibase" / "production" / "phase5_task_ledger_contract.py"
    )
    if not module_file.exists():
        pytest.skip("full public checkout is not mounted in the backend test image")
    tree = ast.parse(module_file.read_text(encoding="utf-8"))
    forbidden_roots = {
        "sqlalchemy",
        "fastapi",
        "celery",
        "httpx",
        "requests",
        "subprocess",
        "socket",
        "redis",
        "minio",
        "psycopg",
        "aiopg",
        "asyncpg",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden_roots, alias.name
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in forbidden_roots, node.module


def test_no_agent_runtime_planner_or_executor_packages_exist() -> None:
    if not CONFIG_PATH.exists():
        pytest.skip("full public checkout is not mounted in the backend test image")
    for forbidden in (
        "agent_runtime",
        "agent_tasks",
        "agent_task_ledger",
        "agent_invocations",
        "planner",
        "executor",
        "dispatcher",
        "scheduler",
        "multi_agent",
    ):
        assert not (REPO_ROOT / "backend" / "src" / "omnibase" / forbidden).exists(), forbidden
    assert not (
        REPO_ROOT
        / "backend"
        / "src"
        / "omnibase"
        / "migrations"
        / "versions"
        / "0011_p5_2_task_ledger.py"
    ).exists()
    # P5.1B/P5.1C legitimately exist and stay untouched by P5.2A.
    registry = REPO_ROOT / "backend" / "src" / "omnibase" / "agent_registry"
    assert registry.is_dir()
    assert not (registry / "runtime.py").exists()


def test_validator_validate_only_exit_code_is_zero() -> None:
    script = VALIDATOR_PATH
    if not script.exists():
        pytest.skip("full public checkout is not mounted in the backend test image")
    result = subprocess.run(
        [sys.executable, str(script), "--validate-only"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["state"] == "blocked/not_proven"
    assert payload["contract_valid"] is True
    assert payload["activation_allowed"] is False


# ---------------------------------------------------------------------------
# Independent-review fixes: per-step retry scoping, bidirectional lease
# binding, all-or-none run binding, configured ceilings, Step DAG, deadline
# hierarchy, hash identity binding and report evidence scope.
# ---------------------------------------------------------------------------


# Independent-review fixes: per-step retry scoping, bidirectional lease
# binding, all-or-none run binding, configured ceilings, Step DAG, deadline
# hierarchy, hash identity binding and report evidence scope.
# ---------------------------------------------------------------------------


def test_two_steps_each_with_attempt_one_positive() -> None:
    # Two Steps of one Task each own an Attempt 1; attempt_number is scoped
    # per (task_id, step_id) and the Task-wide fencing stays monotonic.
    config = TaskLedgerContractConfig.from_mapping(_contract_mapping())
    steps = config.ledger_contracts.steps
    assert {step.step_id for step in steps} == {STEP_ID, STEP_2_ID}
    step_numbers = {step.step_id: step.step_number for step in steps}
    assert step_numbers[STEP_ID] == 1
    assert step_numbers[STEP_2_ID] == 2
    step1_attempts = [a for a in config.ledger_contracts.attempts if a.step_id == STEP_ID]
    step2_attempts = [a for a in config.ledger_contracts.attempts if a.step_id == STEP_2_ID]
    assert {a.attempt_number for a in step1_attempts} == {1, 2}
    assert {a.attempt_number for a in step2_attempts} == {1}
    assert len(config.ledger_contracts.task_leases) == 2
    # Task-wide fencing monotonic across steps on the authoritative lease
    # ledger: step2's claim (token 3 at 00:10:00Z) before step1's claim
    # (token 9 at 00:14:00Z) in lease created_at order.
    fenced = sorted(
        (lease for lease in config.ledger_contracts.task_leases),
        key=lambda lease: lease.created_at,
    )
    assert [lease.task_fencing_token for lease in fenced] == [3, 9]


def test_multi_step_dag_positive() -> None:
    config = TaskLedgerContractConfig.from_mapping(_contract_mapping())
    step2 = next(step for step in config.ledger_contracts.steps if step.step_id == STEP_2_ID)
    assert step2.dependencies == (STEP_ID,)
    assert step2.plan_id == PLAN_ID
    assert step2.plan_version == "1"
    assert step2.plan_digest == PLAN_DIGEST


def test_attempt_lease_bidirectional_binding_negatives() -> None:
    # 3a. attempt points at a lease bound to a different attempt
    mapping = _contract_mapping()
    attempts = mapping["ledger_contracts"]["attempts"]
    attempts[1]["task_lease_id"] = LEASE_2_ID
    with pytest.raises(
        TaskLedgerContractError, match="references a task lease bound to a different attempt"
    ):
        TaskLedgerContractConfig.from_mapping(mapping)

    # 3b. two active leases for the same attempt
    mapping = _contract_mapping()
    leases = mapping["ledger_contracts"]["task_leases"]
    second_active = _lease_mapping()
    second_active["task_lease_id"] = LEASE_3_ID
    second_active["attempt_id"] = ATTEMPT_2_ID
    leases.append(second_active)
    with pytest.raises(TaskLedgerContractError, match="at most one active task lease"):
        TaskLedgerContractConfig.from_mapping(mapping)

    # 3c. revoked/expired lease referenced as the current lease
    mapping = _contract_mapping()
    mapping["ledger_contracts"]["task_leases"][0]["state"] = "revoked"
    with pytest.raises(TaskLedgerContractError, match="must be active"):
        TaskLedgerContractConfig.from_mapping(mapping)

    mapping = _contract_mapping()
    mapping["ledger_contracts"]["task_leases"][0]["state"] = "expired"
    with pytest.raises(TaskLedgerContractError, match="must be active"):
        TaskLedgerContractConfig.from_mapping(mapping)


def test_attempt_lease_state_matrix_negatives() -> None:
    # dispatching without a lease is rejected by the state matrix
    mapping = _contract_mapping()
    attempts = mapping["ledger_contracts"]["attempts"]
    attempts[1]["state"] = "dispatching"
    attempts[1]["task_lease_id"] = None
    attempts[1]["task_fencing_token"] = None
    with pytest.raises(
        TaskLedgerContractError, match="leased, dispatching and running attempts require"
    ):
        TaskLedgerContractConfig.from_mapping(mapping)

    # running without a lease
    mapping = _contract_mapping()
    attempts = mapping["ledger_contracts"]["attempts"]
    attempts[1]["task_lease_id"] = None
    attempts[1]["task_fencing_token"] = None
    with pytest.raises(
        TaskLedgerContractError, match="leased, dispatching and running attempts require"
    ):
        TaskLedgerContractConfig.from_mapping(mapping)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        # only ID without fencing
        (lambda r: r.update({"run_fencing_token": None}), "must be all-or-none"),
        # only fencing without ID
        (lambda r: r.update({"run_lease_id": None}), "must be all-or-none"),
        # only the Run group without the Node group
        (
            lambda r: r.update({"node_id": None, "node_fencing_token": None}),
            "must be all-or-none",
        ),
        # only the Node group without the Run group
        (
            lambda r: r.update({"run_lease_id": None, "run_fencing_token": None}),
            "must be all-or-none",
        ),
        # created state must not carry any binding
        (lambda r: r.update({"state": "created"}), "created agent run must not carry"),
        # terminal state must not carry any binding
        (
            lambda r: r.update({"state": "cancelled"}),
            "terminal agent run must not retain",
        ),
        # runtime identity without workload thumbprint
        (
            lambda r: r.update({"workload_identity_thumbprint": None}),
            "must be provided together",
        ),
        # leased state missing the runtime/workload identity
        (
            lambda r: r.update({"state": "leased", "runtime_instance_id": None}),
            "must be provided together",
        ),
    ],
)
def test_agent_run_binding_all_or_none_negatives(mutation, message: str) -> None:
    mapping = _run_mapping()
    mutation(mapping)
    with pytest.raises(TaskLedgerContractError, match=message):
        AgentRunBinding.from_mapping(mapping)


def test_configured_ceilings_are_enforced() -> None:
    # Tightening deadline_ceiling_seconds to 60 must reject the 12h sample task.
    mapping = _contract_mapping()
    mapping["deadline_ceiling_seconds"] = 60
    with pytest.raises(TaskLedgerContractError, match="exceeds the configured ceiling"):
        TaskLedgerContractConfig.from_mapping(mapping)

    # Tightening task_lease_ttl_ceiling_seconds to 60 must reject the 5min lease.
    mapping = _contract_mapping()
    mapping["task_lease_ttl_ceiling_seconds"] = 60
    with pytest.raises(TaskLedgerContractError, match="exceeds the configured ceiling"):
        TaskLedgerContractConfig.from_mapping(mapping)


def test_step_plan_identity_and_dag_negatives() -> None:
    # plan_digest drift between the Step and its parent Task
    mapping = _contract_mapping()
    mapping["ledger_contracts"]["steps"][0]["plan_digest"] = "0" * 64
    with pytest.raises(TaskLedgerContractError, match="plan identity drifts"):
        TaskLedgerContractConfig.from_mapping(mapping)

    # unknown dependency step
    mapping = _contract_mapping()
    mapping["ledger_contracts"]["steps"][1]["dependencies"] = [
        "99999999-9999-9999-9999-999999999999"
    ]
    with pytest.raises(TaskLedgerContractError, match="unknown dependency step"):
        TaskLedgerContractConfig.from_mapping(mapping)

    # cross-task/cross-run dependency (a step of a different run)
    mapping = _contract_mapping()
    run2 = _run_mapping()
    run2["agent_run_id"] = "99999999-9999-9999-9999-999999999999"
    run2["workspace_run_id"] = "99999999-9999-9999-9999-999999999998"
    mapping["ledger_contracts"]["runs"].append(run2)
    step3 = _step_mapping()
    step3["step_id"] = "99999999-9999-9999-9999-999999999997"
    step3["agent_run_id"] = run2["agent_run_id"]
    step3["step_number"] = 3
    step3["dependencies"] = [STEP_ID]
    mapping["ledger_contracts"]["steps"].append(step3)
    with pytest.raises(TaskLedgerContractError, match="cross-task or cross-run"):
        TaskLedgerContractConfig.from_mapping(mapping)

    # dependency cycle
    mapping = _contract_mapping()
    mapping["ledger_contracts"]["steps"][0]["dependencies"] = [STEP_2_ID]
    with pytest.raises(TaskLedgerContractError, match="contains a cycle"):
        TaskLedgerContractConfig.from_mapping(mapping)

    # duplicate step_number within the task
    mapping = _contract_mapping()
    mapping["ledger_contracts"]["steps"][1]["step_number"] = 1
    with pytest.raises(TaskLedgerContractError, match="unique within the task"):
        TaskLedgerContractConfig.from_mapping(mapping)


def test_deadline_hierarchy_negatives() -> None:
    # attempt.deadline after the task deadline
    mapping = _contract_mapping()
    attempts = mapping["ledger_contracts"]["attempts"]
    attempts[1]["deadline"] = "2026-08-03T13:00:00Z"
    with pytest.raises(
        TaskLedgerContractError, match="deadline must not be later than the task deadline"
    ):
        TaskLedgerContractConfig.from_mapping(mapping)

    # task lease expiry after the attempt deadline
    mapping = _contract_mapping()
    attempts = mapping["ledger_contracts"]["attempts"]
    attempts[1]["deadline"] = "2026-08-03T00:12:00Z"
    with pytest.raises(
        TaskLedgerContractError, match="expiry must not be later than the attempt deadline"
    ):
        TaskLedgerContractConfig.from_mapping(mapping)

    # attempt.created_at not before its deadline
    mapping = _attempt_mapping()
    mapping["deadline"] = "2026-08-03T00:10:00Z"
    with pytest.raises(TaskLedgerContractError, match="attempt.deadline must be after"):
        AgentAttempt.from_mapping(mapping)


def test_hash_profile_identity_binding() -> None:
    claim_payload = {
        "operation": "agent.attempt.claim",
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "workspace_generation": 1,
        "task_id": TASK_ID,
        "task_generation": 1,
        "agent_run_id": RUN_ID,
        "step_id": STEP_ID,
        "attempt_id": ATTEMPT_2_ID,
        "attempt_number": 2,
        "expected_previous_state": "running",
        "run_lease_id": RUN_LEASE_ID,
        "run_fencing_token": 3,
        "node_id": NODE_ID,
        "node_fencing_token": 1,
        "agent_version_digest": VERSION_DIGEST,
        "resource_scope_digest": RESOURCE_SCOPE_DIGEST,
        "budget_policy_digest": BUDGET_POLICY_DIGEST,
        "deadline": "2026-08-03T12:00:00Z",
    }
    first = compute_request_hash("attempt_claim", claim_payload)
    assert compute_request_hash("attempt_claim", dict(claim_payload)) == first
    # any security-relevant immutable identity is required by the closed set
    with pytest.raises(TaskLedgerContractError, match="is missing fields"):
        compute_request_hash(
            "attempt_claim",
            {key: value for key, value in claim_payload.items() if key != "agent_run_id"},
        )
    with pytest.raises(TaskLedgerContractError, match="is missing fields"):
        compute_request_hash(
            "attempt_claim",
            {key: value for key, value in claim_payload.items() if key != "node_id"},
        )
    with pytest.raises(TaskLedgerContractError, match="is missing fields"):
        compute_request_hash(
            "attempt_claim",
            {key: value for key, value in claim_payload.items() if key != "agent_version_digest"},
        )
    heartbeat_payload = {
        "operation": "agent.attempt.heartbeat",
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "workspace_generation": 1,
        "task_id": TASK_ID,
        "task_generation": 1,
        "agent_run_id": RUN_ID,
        "step_id": STEP_ID,
        "attempt_id": ATTEMPT_2_ID,
        "attempt_number": 2,
        "run_lease_id": RUN_LEASE_ID,
        "run_fencing_token": 3,
        "node_id": NODE_ID,
        "node_fencing_token": 1,
        "task_lease_id": TASK_LEASE_ID,
        "task_fencing_token": 9,
        "agent_version_digest": VERSION_DIGEST,
        "resource_scope_digest": RESOURCE_SCOPE_DIGEST,
        "budget_policy_digest": BUDGET_POLICY_DIGEST,
    }
    compute_request_hash("attempt_heartbeat", heartbeat_payload)
    with pytest.raises(TaskLedgerContractError, match="is missing fields"):
        compute_request_hash(
            "attempt_heartbeat",
            {key: value for key, value in heartbeat_payload.items() if key != "task_lease_id"},
        )


def test_report_evidence_scope_semantics(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path)
    # validate_only never claims source-boundary or gate execution
    only = TaskLedgerContractGate(tmp_path / "repo").validate_only(config)
    scope = only.to_dict()["verification_evidence"]
    assert scope["mode"] == "validate_only"
    assert scope["static_source_boundary"]["checked"] is False
    assert scope["gate_execution"]["feature_gates_resolved"] is False
    assert scope["gate_execution"]["sealed_digests_verified"] is False
    assert scope["direct_runtime_execution"] == "not_executed_by_gate"
    assert scope["import_ast_analysis"] == "proven_by_tests_not_by_gate"

    # verify actually executed the static source boundary and sealed checks
    verified = TaskLedgerContractGate(tmp_path / "repo").verify(config, source=_source())
    scope = verified.to_dict()["verification_evidence"]
    assert scope["mode"] == "verify"
    assert scope["static_source_boundary"]["checked"] is True
    assert scope["static_source_boundary"]["migration_head_verified"] is True
    assert scope["gate_execution"]["feature_gates_resolved"] is True
    assert scope["gate_execution"]["sealed_digests_verified"] is True
    assert scope["direct_runtime_execution"] == "not_executed_by_gate"


# ---------------------------------------------------------------------------
# Round-2 independent review: task fencing scope, bidirectional lease binding,
# contiguous-from-1 attempt sequence, and real evidence verification scope.
# ---------------------------------------------------------------------------

TASK_2_ID = "00000000-0000-0000-0000-0000000000e2"
RUN_2_ID = "cccccccc-cccc-cccc-cccc-ccccccccccc2"
WORKSPACE_RUN_2_ID = "dddddddd-dddd-dddd-dddd-ddddddddddd2"
STEP_3_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbc3bb"
STEP_4_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbc4bb"
RUNTIME_INSTANCE_2_ID = "12345678-1234-1234-1234-123456789122"
RUN_LEASE_2_ID = "ffffffff-ffff-ffff-ffff-ffffffffff2f"
TASK_LEASE_4_ID = "77777777-7777-7777-7777-777777777774"
TASK_LEASE_5_ID = "77777777-7777-7777-7777-777777777775"
ATTEMPT_5_ID = "88888888-8888-8888-8888-888888888885"
ATTEMPT_6_ID = "88888888-8888-8888-8888-888888888886"
ATTEMPT_7_ID = "88888888-8888-8888-8888-888888888887"


def _task_2_mapping() -> dict[str, object]:
    mapping = _task_mapping()
    mapping["task_id"] = TASK_2_ID
    mapping["request_hash"] = compute_request_hash(
        "task_create",
        {
            "operation": "agent.task.create",
            "task_id": TASK_2_ID,
            "tenant_id": TENANT_ID,
            "workspace_id": WORKSPACE_ID,
            "workspace_generation": 1,
            "agent_definition_id": DEFINITION_ID,
            "agent_version_id": VERSION_ID,
            "agent_version_digest": VERSION_DIGEST,
            "workspace_agent_binding_id": BINDING_ID,
            "resource_scope_digest": RESOURCE_SCOPE_DIGEST,
            "budget_policy_digest": BUDGET_POLICY_DIGEST,
            "deadline": "2026-08-03T12:00:00Z",
        },
    )
    return mapping


def _run_2_mapping() -> dict[str, object]:
    return {
        "schema_version": 1,
        "agent_run_id": RUN_2_ID,
        "task_id": TASK_2_ID,
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "workspace_generation": 1,
        "workspace_run_id": WORKSPACE_RUN_2_ID,
        "runtime_instance_id": RUNTIME_INSTANCE_2_ID,
        "workload_identity_thumbprint": WORKLOAD_THUMBPRINT,
        "node_id": NODE_ID,
        "node_fencing_token": 1,
        "run_lease_id": RUN_LEASE_2_ID,
        "run_fencing_token": 4,
        "state": "running",
        "created_at": "2026-08-03T00:01:30Z",
    }


def _step_3_mapping() -> dict[str, object]:
    return {
        "schema_version": 1,
        "step_id": STEP_3_ID,
        "task_id": TASK_2_ID,
        "agent_run_id": RUN_2_ID,
        "step_number": 1,
        "plan_id": PLAN_ID,
        "plan_version": "1",
        "plan_digest": PLAN_DIGEST,
        "dependencies": [],
        "state": "running",
        "created_at": "2026-08-03T00:02:30Z",
    }


def _step_4_mapping() -> dict[str, object]:
    return {
        "schema_version": 1,
        "step_id": STEP_4_ID,
        "task_id": TASK_2_ID,
        "agent_run_id": RUN_2_ID,
        "step_number": 2,
        "plan_id": PLAN_ID,
        "plan_version": "1",
        "plan_digest": PLAN_DIGEST,
        "dependencies": [STEP_3_ID],
        "state": "running",
        "created_at": "2026-08-03T00:02:45Z",
    }


def _attempt_5_mapping() -> dict[str, object]:
    # Task B's first attempt reuses task_fencing_token=1, which Task A also
    # uses; both must be allowed because task fencing is per-Task.
    return {
        "schema_version": 1,
        "attempt_id": ATTEMPT_5_ID,
        "task_id": TASK_2_ID,
        "step_id": STEP_3_ID,
        "agent_run_id": RUN_2_ID,
        "attempt_number": 1,
        "state": "running",
        "task_lease_id": TASK_LEASE_4_ID,
        "task_fencing_token": 1,
        "expected_previous_state": "running",
        "deadline": "2026-08-03T12:00:00Z",
        "created_at": "2026-08-03T00:10:30Z",
    }


def _attempt_6_mapping() -> dict[str, object]:
    # Task B's second Step, also attempt 1 of that Step, but created later and
    # reusing token 1 (a Task-wide regression that per-Step ordering cannot see
    # because each Step sequence is just [1]).
    return {
        "schema_version": 1,
        "attempt_id": ATTEMPT_6_ID,
        "task_id": TASK_2_ID,
        "step_id": STEP_4_ID,
        "agent_run_id": RUN_2_ID,
        "attempt_number": 1,
        "state": "running",
        "task_lease_id": TASK_LEASE_5_ID,
        "task_fencing_token": 1,
        "expected_previous_state": "running",
        "deadline": "2026-08-03T12:00:00Z",
        "created_at": "2026-08-03T00:10:45Z",
    }


def _lease_4_mapping() -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_lease_id": TASK_LEASE_4_ID,
        "task_id": TASK_2_ID,
        "attempt_id": ATTEMPT_5_ID,
        "agent_run_id": RUN_2_ID,
        "run_lease_id": RUN_LEASE_2_ID,
        "run_fencing_token": 4,
        "node_id": NODE_ID,
        "node_fencing_token": 1,
        "workspace_generation": 1,
        "task_fencing_token": 1,
        "state": "active",
        "expires_at": "2026-08-03T00:15:00Z",
        "heartbeat_at": None,
        "created_at": "2026-08-03T00:10:30Z",
    }


def _lease_5_mapping() -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_lease_id": TASK_LEASE_5_ID,
        "task_id": TASK_2_ID,
        "attempt_id": ATTEMPT_6_ID,
        "agent_run_id": RUN_2_ID,
        "run_lease_id": RUN_LEASE_2_ID,
        "run_fencing_token": 4,
        "node_id": NODE_ID,
        "node_fencing_token": 1,
        "workspace_generation": 1,
        "task_fencing_token": 1,
        "state": "active",
        "expires_at": "2026-08-03T00:15:00Z",
        "heartbeat_at": None,
        "created_at": "2026-08-03T00:10:45Z",
    }


def _contract_with_two_tasks() -> dict[str, object]:
    """A second Task/Run/Step/Attempt/Lease whose fencing restarts at token 1."""
    mapping = _contract_mapping()
    mapping["ledger_contracts"]["tasks"].append(_task_2_mapping())
    mapping["ledger_contracts"]["runs"].append(_run_2_mapping())
    mapping["ledger_contracts"]["steps"].append(_step_3_mapping())
    mapping["ledger_contracts"]["attempts"].append(_attempt_5_mapping())
    mapping["ledger_contracts"]["task_leases"].append(_lease_4_mapping())
    return mapping


def test_task_fencing_is_scoped_per_task_positive() -> None:
    # Two independent Tasks may each start their task_fencing_token sequence at
    # 1; the contract must NOT flatten them into one shared system-wide or
    # Run-wide sequence. Task A uses tokens {3, 9} and Task B uses {1}.
    config = TaskLedgerContractConfig.from_mapping(_contract_with_two_tasks())
    fenced_by_task: dict[str, list[int]] = {}
    for attempt in config.ledger_contracts.attempts:
        if attempt.task_fencing_token is not None:
            fenced_by_task.setdefault(attempt.task_id, []).append(attempt.task_fencing_token)
    task_a = sorted(fenced_by_task[TASK_ID])
    task_b = sorted(fenced_by_task[TASK_2_ID])
    assert task_a == [3, 9]
    assert task_b == [1]


def test_task_fencing_regression_within_same_task_negative() -> None:
    # Within ONE Task, a later holder (by created_at) must use a strictly higher
    # fencing token; regressing within the same Task is rejected, independent of
    # any other Task's sequence. The regression is placed across two Steps of
    # Task B so the per-Step attempt-ordering rule cannot mask it; only the
    # Task-wide fencing rule sees [1, 1].
    mapping = _contract_with_two_tasks()
    # Add a second Step to Task B whose lone attempt reuses token 1 later.
    mapping["ledger_contracts"]["steps"].append(_step_4_mapping())
    mapping["ledger_contracts"]["attempts"].append(_attempt_6_mapping())
    mapping["ledger_contracts"]["task_leases"].append(_lease_5_mapping())
    with pytest.raises(
        TaskLedgerContractError,
        match=r"task fencing must increase monotonically within task " + TASK_2_ID,
    ):
        TaskLedgerContractConfig.from_mapping(mapping)


def test_task_fencing_uses_normalized_utc_order_negative() -> None:
    # Fencing chronology is the normalized UTC instant of task_lease.created_at
    # on the authoritative lease ledger, never the raw ISO-8601 string. Within
    # one Task, lease token 9 at 2026-08-03T00:10:00Z precedes lease token 3 at
    # 2026-08-02T23:11:00-01:00 (real UTC 2026-08-03T00:11:00Z): the real order
    # 9 -> 3 is a regression that string sorting would tidy into 3 -> 9 and
    # wrongly accept.
    mapping = _contract_mapping()
    leases = mapping["ledger_contracts"]["task_leases"]
    for lease in leases:
        if lease["task_lease_id"] == TASK_LEASE_ID:
            lease["created_at"] = "2026-08-03T00:10:00Z"
        if lease["task_lease_id"] == LEASE_2_ID:
            lease["created_at"] = "2026-08-02T23:11:00-01:00"
    with pytest.raises(
        TaskLedgerContractError,
        match=r"task fencing must increase monotonically within task " + TASK_ID,
    ):
        TaskLedgerContractConfig.from_mapping(mapping)


def test_task_fencing_mixed_offsets_positive() -> None:
    # Same Task, lease claim instants spelled with Z, +HH:MM and -HH:MM. The
    # real UTC order (3 at 00:10:00Z, 9 at 00:11:00Z, 15 at 00:13:00Z) strictly
    # matches the token order, so the ledger must accept it even though the raw
    # string order would differ (2026-08-02T23:13:00-01:00 sorts before
    # 2026-08-03T00:10:00Z lexicographically).
    mapping = _contract_mapping()
    leases = mapping["ledger_contracts"]["task_leases"]
    for lease in leases:
        if lease["task_lease_id"] == LEASE_2_ID:
            lease["created_at"] = "2026-08-03T00:10:00Z"  # Z
        if lease["task_lease_id"] == TASK_LEASE_ID:
            lease["created_at"] = "2026-08-03T01:11:00+01:00"  # +01:00 -> 00:11:00Z
    # A third fenced claim expressed with a -HH:MM offset, later in real UTC
    # time: STEP_2_ID now owns attempts 1 -> 2.
    attempt4 = _attempt_mapping(ATTEMPT_4_ID, 2)
    attempt4["step_id"] = STEP_2_ID
    attempt4["task_lease_id"] = LEASE_3_ID
    attempt4["task_fencing_token"] = 15
    attempt4["created_at"] = "2026-08-03T01:12:00+01:00"
    mapping["ledger_contracts"]["attempts"].append(attempt4)
    lease4 = _lease_2_mapping()
    lease4["task_lease_id"] = LEASE_3_ID
    lease4["attempt_id"] = ATTEMPT_4_ID
    lease4["task_fencing_token"] = 15
    lease4["created_at"] = "2026-08-02T23:13:00-01:00"  # -01:00 -> 00:13:00Z
    leases.append(lease4)
    config = TaskLedgerContractConfig.from_mapping(mapping)
    assert {lease.task_fencing_token for lease in config.ledger_contracts.task_leases} == {
        3,
        9,
        15,
    }


def test_task_fencing_equivalent_instants_fail_closed() -> None:
    # Two fenced claims of the same Task that normalize to the exact same UTC
    # instant (here spelled with Z and +01:00) have no trusted secondary
    # ordering field. The contract must fail closed instead of inventing an
    # order: the tokens are already increasing (3 -> 9), yet the ledger must
    # still reject rather than sort by token value, lease id or input order.
    mapping = _contract_mapping()
    leases = mapping["ledger_contracts"]["task_leases"]
    for lease in leases:
        if lease["task_lease_id"] == TASK_LEASE_ID:
            lease["task_fencing_token"] = 3
            lease["created_at"] = "2026-08-03T00:10:00Z"
        if lease["task_lease_id"] == LEASE_2_ID:
            lease["task_fencing_token"] = 9
            lease["created_at"] = "2026-08-03T01:10:00+01:00"
    # keep the attempts' tokens consistent with their active leases so the
    # ambiguity rule is the only thing under test
    for attempt in mapping["ledger_contracts"]["attempts"]:
        if attempt["attempt_id"] == ATTEMPT_2_ID:
            attempt["task_fencing_token"] = 3
        if attempt["attempt_id"] == ATTEMPT_3_ID:
            attempt["task_fencing_token"] = 9
    with pytest.raises(
        TaskLedgerContractError,
        match=r"task fencing chronology within task " + TASK_ID + r" is ambiguous",
    ):
        TaskLedgerContractConfig.from_mapping(mapping)


def test_task_fencing_different_tasks_mixed_offsets_positive() -> None:
    # Different Tasks own independent fencing sequences and may each start at
    # token 1, regardless of offset spellings: Task A uses Z and +01:00 (tokens
    # 3 -> 9 in UTC order) while Task B uses -01:00 and starts at token 1.
    mapping = _contract_with_two_tasks()
    leases = mapping["ledger_contracts"]["task_leases"]
    for lease in leases:
        if lease["task_lease_id"] == LEASE_2_ID:
            lease["created_at"] = "2026-08-03T00:10:00Z"  # Z
        if lease["task_lease_id"] == TASK_LEASE_ID:
            lease["created_at"] = "2026-08-03T01:11:00+01:00"  # +01:00 -> 00:11:00Z
        if lease["task_lease_id"] == TASK_LEASE_4_ID:
            lease["created_at"] = "2026-08-02T23:10:30-01:00"  # -01:00 -> 00:10:30Z
    config = TaskLedgerContractConfig.from_mapping(mapping)
    fenced_by_task: dict[str, list[int]] = {}
    for lease in config.ledger_contracts.task_leases:
        fenced_by_task.setdefault(lease.task_id, []).append(lease.task_fencing_token)
    assert sorted(fenced_by_task[TASK_ID]) == [3, 9]
    assert fenced_by_task[TASK_2_ID] == [1]


def _historical_lease_mapping(
    lease_id: str,
    token: int,
    created_at: str,
    state: str,
    *,
    heartbeat_at: str | None = None,
    task_id: str = TASK_ID,
    attempt_id: str = ATTEMPT_1_ID,
    agent_run_id: str = RUN_ID,
    run_lease_id: str = RUN_LEASE_ID,
    run_fencing_token: int = 3,
) -> dict[str, object]:
    """A completed/revoked/expired Task Lease bound to a terminal attempt whose
    task_lease_id/task_fencing_token were cleared: historical holder identity
    lives only in the append-only Task Lease ledger."""
    mapping = _lease_mapping()
    mapping["task_lease_id"] = lease_id
    mapping["task_id"] = task_id
    mapping["attempt_id"] = attempt_id
    mapping["agent_run_id"] = agent_run_id
    mapping["run_lease_id"] = run_lease_id
    mapping["run_fencing_token"] = run_fencing_token
    mapping["task_fencing_token"] = token
    mapping["state"] = state
    mapping["heartbeat_at"] = heartbeat_at
    mapping["created_at"] = created_at
    return mapping


def test_completed_history_high_then_active_low_negative() -> None:
    # A completed Task Lease (token 9 at 00:06:00Z) followed by an active claim
    # (token 3 at 00:10:00Z) is a fencing regression: the terminal attempt's
    # cleared lease/token fields do not erase its completed lease history.
    mapping = _contract_mapping()
    for lease in mapping["ledger_contracts"]["task_leases"]:
        if lease["task_lease_id"] == LEASE_2_ID:
            lease["created_at"] = "2026-08-03T00:12:00Z"
    mapping["ledger_contracts"]["task_leases"].append(
        _historical_lease_mapping(
            LEASE_3_ID,
            9,
            "2026-08-03T00:11:00Z",
            "completed",
            heartbeat_at="2026-08-03T00:11:30Z",
        )
    )
    with pytest.raises(
        TaskLedgerContractError,
        match=r"task fencing must increase monotonically within task " + TASK_ID,
    ):
        TaskLedgerContractConfig.from_mapping(mapping)


def test_revoked_history_high_then_active_low_negative() -> None:
    # A revoked Task Lease (token 9 at 00:06:00Z) followed by an active claim
    # (token 3 at 00:10:00Z) is a fencing regression and must be rejected.
    mapping = _contract_mapping()
    for lease in mapping["ledger_contracts"]["task_leases"]:
        if lease["task_lease_id"] == LEASE_2_ID:
            lease["created_at"] = "2026-08-03T00:12:00Z"
    mapping["ledger_contracts"]["task_leases"].append(
        _historical_lease_mapping(LEASE_3_ID, 9, "2026-08-03T00:11:00Z", "revoked")
    )
    with pytest.raises(
        TaskLedgerContractError,
        match=r"task fencing must increase monotonically within task " + TASK_ID,
    ):
        TaskLedgerContractConfig.from_mapping(mapping)


def test_expired_history_high_then_active_low_negative() -> None:
    # An expired Task Lease (token 9 at 00:06:00Z) followed by an active claim
    # (token 3 at 00:10:00Z) is a fencing regression and must be rejected.
    mapping = _contract_mapping()
    for lease in mapping["ledger_contracts"]["task_leases"]:
        if lease["task_lease_id"] == LEASE_2_ID:
            lease["created_at"] = "2026-08-03T00:12:00Z"
    mapping["ledger_contracts"]["task_leases"].append(
        _historical_lease_mapping(LEASE_3_ID, 9, "2026-08-03T00:11:00Z", "expired")
    )
    with pytest.raises(
        TaskLedgerContractError,
        match=r"task fencing must increase monotonically within task " + TASK_ID,
    ):
        TaskLedgerContractConfig.from_mapping(mapping)


def test_historical_and_active_strictly_increasing_positive() -> None:
    # One Task spanning multiple Steps/Attempts: completed token 3, revoked
    # token 9, expired token 15 and active token 21 with strictly increasing
    # real UTC chronology must be accepted on the lease ledger.
    mapping = _contract_mapping()
    mapping["ledger_contracts"]["attempts"] = [
        _attempt_1_mapping(),
        _attempt_3_mapping(),
    ]
    mapping["ledger_contracts"]["attempts"][1]["task_fencing_token"] = 21
    mapping["ledger_contracts"]["effects"] = []
    mapping["ledger_contracts"]["task_leases"] = [
        _historical_lease_mapping(
            LEASE_3_ID,
            3,
            "2026-08-03T00:10:30Z",
            "completed",
            heartbeat_at="2026-08-03T00:10:45Z",
        ),
        _historical_lease_mapping(TASK_LEASE_4_ID, 9, "2026-08-03T00:11:00Z", "revoked"),
        _historical_lease_mapping(TASK_LEASE_5_ID, 15, "2026-08-03T00:12:00Z", "expired"),
        _lease_2_mapping(),
    ]
    active = mapping["ledger_contracts"]["task_leases"][3]
    active["task_fencing_token"] = 21
    active["created_at"] = "2026-08-03T00:14:00Z"
    config = TaskLedgerContractConfig.from_mapping(mapping)
    fenced = sorted(lease.task_fencing_token for lease in config.ledger_contracts.task_leases)
    assert fenced == [3, 9, 15, 21]


def test_historical_leases_equivalent_utc_instants_negative() -> None:
    # Two historical leases of the same Task expressed with different offsets
    # normalize to the exact same UTC instant. Even though the tokens are
    # increasing (3 -> 9), the contract has no trusted secondary ordering field
    # and must fail closed.
    mapping = _contract_mapping()
    for lease in mapping["ledger_contracts"]["task_leases"]:
        if lease["task_lease_id"] == LEASE_2_ID:
            lease["created_at"] = "2026-08-03T00:12:00Z"
    mapping["ledger_contracts"]["task_leases"].append(
        _historical_lease_mapping(
            LEASE_3_ID,
            3,
            "2026-08-03T00:11:00Z",
            "completed",
            heartbeat_at="2026-08-03T00:11:30Z",
        )
    )
    mapping["ledger_contracts"]["task_leases"].append(
        _historical_lease_mapping(TASK_LEASE_4_ID, 9, "2026-08-03T01:11:00+01:00", "revoked")
    )
    with pytest.raises(
        TaskLedgerContractError,
        match=r"task fencing chronology within task " + TASK_ID + r" is ambiguous",
    ):
        TaskLedgerContractConfig.from_mapping(mapping)


def test_historical_lease_input_order_independent() -> None:
    # Reversing the task_leases array must not change accept/reject: chronology
    # comes from normalized UTC instants, never from input-array order.
    def _ambiguous() -> dict[str, object]:
        mapping = _contract_mapping()
        for lease in mapping["ledger_contracts"]["task_leases"]:
            if lease["task_lease_id"] == LEASE_2_ID:
                lease["created_at"] = "2026-08-03T00:12:00Z"
        mapping["ledger_contracts"]["task_leases"].append(
            _historical_lease_mapping(
                LEASE_3_ID,
                3,
                "2026-08-03T00:11:00Z",
                "completed",
                heartbeat_at="2026-08-03T00:11:30Z",
            )
        )
        mapping["ledger_contracts"]["task_leases"].append(
            _historical_lease_mapping(TASK_LEASE_4_ID, 9, "2026-08-03T01:11:00+01:00", "revoked")
        )
        return mapping

    forward = _ambiguous()
    reversed_mapping = _ambiguous()
    reversed_mapping["ledger_contracts"]["task_leases"] = list(
        reversed(reversed_mapping["ledger_contracts"]["task_leases"])
    )
    for mapping in (forward, reversed_mapping):
        with pytest.raises(
            TaskLedgerContractError,
            match=r"task fencing chronology within task " + TASK_ID + r" is ambiguous",
        ):
            TaskLedgerContractConfig.from_mapping(mapping)

    def _increasing() -> dict[str, object]:
        mapping = _contract_mapping()
        mapping["ledger_contracts"]["attempts"] = [
            _attempt_1_mapping(),
            _attempt_3_mapping(),
        ]
        mapping["ledger_contracts"]["attempts"][1]["task_fencing_token"] = 21
        mapping["ledger_contracts"]["effects"] = []
        mapping["ledger_contracts"]["task_leases"] = [
            _historical_lease_mapping(
                LEASE_3_ID,
                3,
                "2026-08-03T00:10:30Z",
                "completed",
                heartbeat_at="2026-08-03T00:10:45Z",
            ),
            _historical_lease_mapping(TASK_LEASE_4_ID, 9, "2026-08-03T00:11:00Z", "revoked"),
            _historical_lease_mapping(TASK_LEASE_5_ID, 15, "2026-08-03T00:12:00Z", "expired"),
            _lease_2_mapping(),
        ]
        active = mapping["ledger_contracts"]["task_leases"][3]
        active["task_fencing_token"] = 21
        active["created_at"] = "2026-08-03T00:14:00Z"
        return mapping

    positive = _increasing()
    reversed_positive = _increasing()
    reversed_positive["ledger_contracts"]["task_leases"] = list(
        reversed(reversed_positive["ledger_contracts"]["task_leases"])
    )
    for mapping in (positive, reversed_positive):
        TaskLedgerContractConfig.from_mapping(mapping)  # must not raise


def test_different_tasks_history_is_independent_positive() -> None:
    # Task A and Task B each start their fencing history at token 1; the
    # sequences are independent and must never be flattened into a system-wide
    # or Run-wide shared sequence.
    mapping = _contract_with_two_tasks()
    attempts = mapping["ledger_contracts"]["attempts"]
    # Task B's STEP_3 gains a terminal committed attempt (lease/token cleared)
    # before its running attempt number 2.
    attempt7 = _attempt_5_mapping()
    attempt7["attempt_id"] = ATTEMPT_7_ID
    attempt7["attempt_number"] = 1
    attempt7["state"] = "committed"
    attempt7["task_lease_id"] = None
    attempt7["task_fencing_token"] = None
    attempt7["created_at"] = "2026-08-03T00:05:30Z"
    attempts.append(attempt7)
    for attempt in attempts:
        if attempt["attempt_id"] == ATTEMPT_5_ID:
            attempt["attempt_number"] = 2
            attempt["task_fencing_token"] = 2
    leases = mapping["ledger_contracts"]["task_leases"]
    for lease in leases:
        if lease["task_lease_id"] == LEASE_2_ID:
            lease["created_at"] = "2026-08-03T00:12:00Z"
        if lease["task_lease_id"] == TASK_LEASE_ID:
            lease["created_at"] = "2026-08-03T00:14:00Z"
        if lease["task_lease_id"] == TASK_LEASE_4_ID:
            lease["task_fencing_token"] = 2
            lease["created_at"] = "2026-08-03T00:12:30Z"
    # Task A history: completed token 1 on the cleared terminal ATTEMPT_1.
    leases.append(
        _historical_lease_mapping(
            LEASE_3_ID,
            1,
            "2026-08-03T00:10:30Z",
            "completed",
            heartbeat_at="2026-08-03T00:10:45Z",
        )
    )
    # Task B history: completed token 1 on its new cleared terminal attempt.
    leases.append(
        _historical_lease_mapping(
            TASK_LEASE_5_ID,
            1,
            "2026-08-03T00:10:45Z",
            "completed",
            heartbeat_at="2026-08-03T00:11:00Z",
            task_id=TASK_2_ID,
            attempt_id=ATTEMPT_7_ID,
            agent_run_id=RUN_2_ID,
            run_lease_id=RUN_LEASE_2_ID,
            run_fencing_token=4,
        )
    )
    config = TaskLedgerContractConfig.from_mapping(mapping)
    fenced_by_task: dict[str, list[int]] = {}
    for lease in config.ledger_contracts.task_leases:
        fenced_by_task.setdefault(lease.task_id, []).append(lease.task_fencing_token)
    assert sorted(fenced_by_task[TASK_ID]) == [1, 3, 9]
    assert sorted(fenced_by_task[TASK_2_ID]) == [1, 2]


def test_terminal_attempt_token_absence_does_not_remove_history() -> None:
    # A terminal (committed) attempt correctly clears task_lease_id and
    # task_fencing_token; its history still lives in the append-only lease
    # ledger. Scanning only attempts would see just {3, 9} and accept; the
    # lease ledger sees 9 -> 3 -> 9 and rejects. Flipping only the historical
    # lease's token to 1 makes the same ledger accept, proving the history
    # lease participates in the chronology.
    committed = _attempt_1_mapping()
    assert committed["task_lease_id"] is None
    assert committed["task_fencing_token"] is None
    rejected = _contract_mapping()
    for lease in rejected["ledger_contracts"]["task_leases"]:
        if lease["task_lease_id"] == LEASE_2_ID:
            lease["created_at"] = "2026-08-03T00:12:00Z"
    rejected["ledger_contracts"]["task_leases"].append(
        _historical_lease_mapping(
            LEASE_3_ID,
            9,
            "2026-08-03T00:11:00Z",
            "completed",
            heartbeat_at="2026-08-03T00:11:30Z",
        )
    )
    with pytest.raises(
        TaskLedgerContractError,
        match=r"task fencing must increase monotonically within task " + TASK_ID,
    ):
        TaskLedgerContractConfig.from_mapping(rejected)
    accepted = _contract_mapping()
    for lease in accepted["ledger_contracts"]["task_leases"]:
        if lease["task_lease_id"] == LEASE_2_ID:
            lease["created_at"] = "2026-08-03T00:12:00Z"
    accepted["ledger_contracts"]["task_leases"].append(
        _historical_lease_mapping(
            LEASE_3_ID,
            1,
            "2026-08-03T00:11:00Z",
            "completed",
            heartbeat_at="2026-08-03T00:11:30Z",
        )
    )
    TaskLedgerContractConfig.from_mapping(accepted)


def test_backdated_task_lease_cannot_reorder_fencing_chronology() -> None:
    # A lower-token holder must not manufacture an earlier place in the
    # authoritative Lease chronology by backdating task_lease.created_at to a
    # time before its bound Attempt existed.  Without the Lease>=Attempt
    # temporal binding, the ledger below is tidied into a fake 3 -> 9 sequence
    # even though the real Attempt chronology is 9 -> 3.
    mapping = _contract_mapping()
    for attempt in mapping["ledger_contracts"]["attempts"]:
        if attempt["attempt_id"] == ATTEMPT_2_ID:
            attempt["task_fencing_token"] = 9
            attempt["created_at"] = "2026-08-03T00:10:00Z"
        if attempt["attempt_id"] == ATTEMPT_3_ID:
            attempt["task_fencing_token"] = 3
            attempt["created_at"] = "2026-08-03T00:20:00Z"
    for lease in mapping["ledger_contracts"]["task_leases"]:
        if lease["task_lease_id"] == TASK_LEASE_ID:
            lease["task_fencing_token"] = 9
            lease["created_at"] = "2026-08-03T00:14:00Z"
        if lease["task_lease_id"] == LEASE_2_ID:
            lease["task_fencing_token"] = 3
            lease["created_at"] = "2026-08-03T00:11:00Z"
    with pytest.raises(
        TaskLedgerContractError,
        match=r"task lease .* must not be created before its bound attempt",
    ):
        TaskLedgerContractConfig.from_mapping(mapping)


@pytest.mark.parametrize(
    ("state", "heartbeat_at"),
    [
        ("completed", "2026-08-03T00:08:00Z"),
        ("revoked", None),
        ("expired", None),
    ],
)
def test_historical_task_lease_cannot_bypass_ttl_ceiling(
    state: str, heartbeat_at: str | None
) -> None:
    # Historical rows were active leases when issued and therefore retain the
    # same server-owned TTL bound after transitioning to a terminal state.
    mapping = _contract_mapping()
    mapping["ledger_contracts"]["task_leases"].append(
        _historical_lease_mapping(
            LEASE_3_ID,
            1,
            "2026-08-03T00:06:00Z",  # expires at 00:15 -> 540s > 300s
            state,
            heartbeat_at=heartbeat_at,
        )
    )
    with pytest.raises(TaskLedgerContractError, match="TTL exceeds the configured ceiling"):
        TaskLedgerContractConfig.from_mapping(mapping)


@pytest.mark.parametrize("state", ["completed", "revoked", "expired"])
def test_historical_task_lease_must_expire_after_creation(state: str) -> None:
    mapping = _contract_mapping()
    historical = _historical_lease_mapping(
        LEASE_3_ID,
        1,
        "2026-08-03T00:14:00Z",
        state,
        heartbeat_at="2026-08-03T00:14:30Z" if state == "completed" else None,
    )
    historical["expires_at"] = "2026-08-03T00:13:00Z"
    mapping["ledger_contracts"]["task_leases"].append(historical)
    with pytest.raises(TaskLedgerContractError, match="must expire after creation"):
        TaskLedgerContractConfig.from_mapping(mapping)


@pytest.mark.parametrize(
    "heartbeat_at",
    ["2026-08-03T00:10:59Z", "2026-08-03T00:15:01Z"],
)
def test_task_lease_heartbeat_must_stay_within_lease_interval(heartbeat_at: str) -> None:
    mapping = _contract_mapping()
    mapping["ledger_contracts"]["task_leases"].append(
        _historical_lease_mapping(
            LEASE_3_ID,
            1,
            "2026-08-03T00:11:00Z",
            "completed",
            heartbeat_at=heartbeat_at,
        )
    )
    with pytest.raises(TaskLedgerContractError, match="heartbeat_at must be within"):
        TaskLedgerContractConfig.from_mapping(mapping)


def test_invalid_offset_minutes_60_negative() -> None:
    # Offset minutes are a closed set 00-59: +01:60 must be rejected explicitly
    # instead of being silently normalized by datetime.fromisoformat.
    mapping = _contract_mapping()
    mapping["ledger_contracts"]["attempts"][1]["created_at"] = "2026-08-03T00:10:00+01:60"
    with pytest.raises(TaskLedgerContractError, match="must be an ISO-8601 UTC timestamp"):
        TaskLedgerContractConfig.from_mapping(mapping)


def test_invalid_offset_minutes_99_negative() -> None:
    mapping = _contract_mapping()
    mapping["ledger_contracts"]["attempts"][1]["created_at"] = "2026-08-03T00:10:00+00:99"
    with pytest.raises(TaskLedgerContractError, match="must be an ISO-8601 UTC timestamp"):
        TaskLedgerContractConfig.from_mapping(mapping)


def test_utc_normalization_overflow_lower_bound_negative() -> None:
    # 0001-01-01T00:00:00+23:59 overflows the lower year bound when normalized
    # to UTC; the failure must convert to TaskLedgerContractError, never leak a
    # native OverflowError.
    mapping = _contract_mapping()
    mapping["ledger_contracts"]["attempts"][1]["created_at"] = "0001-01-01T00:00:00+23:59"
    with pytest.raises(TaskLedgerContractError, match="must be an ISO-8601 UTC timestamp"):
        TaskLedgerContractConfig.from_mapping(mapping)


def test_utc_normalization_overflow_upper_bound_negative() -> None:
    mapping = _contract_mapping()
    mapping["ledger_contracts"]["attempts"][1]["created_at"] = "9999-12-31T23:59:59-23:59"
    with pytest.raises(TaskLedgerContractError, match="must be an ISO-8601 UTC timestamp"):
        TaskLedgerContractConfig.from_mapping(mapping)


def test_timestamp_offset_spelling_positive_controls() -> None:
    # Z, +HH:MM and -HH:MM spellings with in-range offset fields are all valid
    # timestamp spellings and normalize to UTC.
    mapping = _contract_mapping()
    for attempt in mapping["ledger_contracts"]["attempts"]:
        if attempt["attempt_id"] == ATTEMPT_2_ID:
            attempt["created_at"] = "2026-08-03T02:10:00+02:00"
        if attempt["attempt_id"] == ATTEMPT_3_ID:
            attempt["created_at"] = "2026-08-02T22:06:00-02:00"
    config = TaskLedgerContractConfig.from_mapping(mapping)
    by_id = {attempt.attempt_id: attempt for attempt in config.ledger_contracts.attempts}
    assert by_id[ATTEMPT_2_ID].created_at == "2026-08-03T02:10:00+02:00"
    assert by_id[ATTEMPT_3_ID].created_at == "2026-08-02T22:06:00-02:00"


def test_active_task_lease_requires_active_attempt_negative() -> None:
    # An active Task Lease must bind exactly one attempt that is in an active
    # execution state (leased/dispatching/running) and points straight back at
    # it. The base fixture for STEP_2_ID is a single attempt (ATTEMPT_3) bound
    # to a single active lease (LEASE_2), so the per-Step ordering and the
    # Task-wide fencing rules never mask the lease-binding checks.

    # (a) active lease + ready attempt
    mapping = _contract_mapping()
    attempt3 = mapping["ledger_contracts"]["attempts"][2]
    attempt3["state"] = "ready"
    attempt3["task_lease_id"] = None
    attempt3["task_fencing_token"] = None
    with pytest.raises(
        TaskLedgerContractError, match="is active but its attempt .* is not in an active"
    ):
        TaskLedgerContractConfig.from_mapping(mapping)

    # (b) active lease + pending attempt
    mapping = _contract_mapping()
    attempt3 = mapping["ledger_contracts"]["attempts"][2]
    attempt3["state"] = "pending"
    attempt3["task_lease_id"] = None
    attempt3["task_fencing_token"] = None
    with pytest.raises(
        TaskLedgerContractError, match="is active but its attempt .* is not in an active"
    ):
        TaskLedgerContractConfig.from_mapping(mapping)

    # (c) active lease + terminal attempt
    mapping = _contract_mapping()
    attempt3 = mapping["ledger_contracts"]["attempts"][2]
    attempt3["state"] = "failed"
    attempt3["task_lease_id"] = None
    attempt3["task_fencing_token"] = None
    with pytest.raises(
        TaskLedgerContractError, match="is active but its attempt .* is not in an active"
    ):
        TaskLedgerContractConfig.from_mapping(mapping)

    # (d) active lease whose running attempt points back at it but binds a
    # task fencing token that disagrees with the active lease's token (4 vs 3,
    # both below the later token 9 so Task-wide fencing stays monotonic).
    mapping = _contract_mapping()
    attempt3 = mapping["ledger_contracts"]["attempts"][2]
    attempt3["task_lease_id"] = LEASE_2_ID  # points back at LEASE_2 (correct)
    attempt3["task_fencing_token"] = 4  # LEASE_2 carries token 3 -> mismatch
    with pytest.raises(
        TaskLedgerContractError,
        match="does not bind the lease fencing token",
    ):
        TaskLedgerContractConfig.from_mapping(mapping)

    # (e) active lease pointing at attempt A, but attempt A points at lease B.
    # The attempt->lease side of the bidirectional binding rejects this first
    # (the named lease must be bound back to this attempt); together with (d)
    # and the active-lease->attempt checks this closes both directions.
    mapping = _contract_mapping()
    attempt3 = mapping["ledger_contracts"]["attempts"][2]
    attempt3["task_lease_id"] = TASK_LEASE_ID  # points at LEASE_1, not LEASE_2
    attempt3["task_fencing_token"] = 9  # match LEASE_1's token to isolate the pointer drift
    with pytest.raises(
        TaskLedgerContractError,
        match="references a task lease bound to a different attempt",
    ):
        TaskLedgerContractConfig.from_mapping(mapping)

    # (f) one attempt with two active leases (set-level scan)
    mapping = _contract_mapping()
    second_active = _lease_2_mapping()
    second_active["task_lease_id"] = LEASE_3_ID
    second_active["attempt_id"] = ATTEMPT_3_ID
    mapping["ledger_contracts"]["task_leases"].append(second_active)
    with pytest.raises(TaskLedgerContractError, match="must have at most one active task lease"):
        TaskLedgerContractConfig.from_mapping(mapping)


def test_attempt_sequence_must_start_at_one_negative() -> None:
    # A single attempt whose attempt_number is not 1 must be rejected even
    # though there is no neighbor to compare against.
    mapping = _contract_mapping()
    attempts = mapping["ledger_contracts"]["attempts"]
    # STEP_2_ID currently has a lone attempt_number=1; bump it to 2.
    for attempt in attempts:
        if attempt["attempt_id"] == ATTEMPT_3_ID:
            attempt["attempt_number"] = 2
    with pytest.raises(
        TaskLedgerContractError, match="must form a contiguous sequence starting at 1"
    ):
        TaskLedgerContractConfig.from_mapping(mapping)


def test_attempt_sequence_must_be_contiguous_negative() -> None:
    # A 1 -> 3 jump (gap) within one Step must be rejected; sorting must not
    # tidy the gap into validity.
    mapping = _contract_mapping()
    # Make STEP_2_ID jump 1 -> 3 (skipping 2): add a third attempt with
    # attempt_number 3 to STEP_2_ID alongside its existing attempt_number 1.
    jumped = _attempt_3_mapping()
    jumped["attempt_id"] = ATTEMPT_4_ID
    jumped["attempt_number"] = 3
    jumped["task_lease_id"] = TASK_LEASE_4_ID
    jumped["task_fencing_token"] = 11
    jumped["created_at"] = "2026-08-03T00:10:30Z"
    mapping["ledger_contracts"]["attempts"].append(jumped)
    jumped_lease = _lease_2_mapping()
    jumped_lease["task_lease_id"] = TASK_LEASE_4_ID
    jumped_lease["attempt_id"] = ATTEMPT_4_ID
    jumped_lease["task_fencing_token"] = 11
    jumped_lease["created_at"] = "2026-08-03T00:10:30Z"
    mapping["ledger_contracts"]["task_leases"].append(jumped_lease)
    with pytest.raises(
        TaskLedgerContractError,
        match=r"must form a contiguous sequence starting at 1; found 3 where 2 was expected",
    ):
        TaskLedgerContractConfig.from_mapping(mapping)


def test_attempt_sequence_is_independent_per_step_positive() -> None:
    # Two Steps may each independently start their attempt_number sequence at 1.
    config = TaskLedgerContractConfig.from_mapping(_contract_mapping())
    step1 = sorted(
        a.attempt_number for a in config.ledger_contracts.attempts if a.step_id == STEP_ID
    )
    step2 = sorted(
        a.attempt_number for a in config.ledger_contracts.attempts if a.step_id == STEP_2_ID
    )
    assert step1 == [1, 2]
    assert step2 == [1]


def test_missing_evidence_reference_is_not_verified(tmp_path: Path) -> None:
    # A passed evidence reference whose sealed path does not exist must NOT be
    # reported as verified; the gate must fail closed (veto) rather than claim
    # evidence_references_verified=true.
    evidence = [
        {
            "id": "phase5_task_ledger_production_evidence",
            "status": "passed",
            "path": "docs/evidence/p5-2a/does-not-exist.json",
            "sha256": "0" * 64,
            "assertions": {"phase": "P5.2A"},
            "required_for_activation": True,
        }
    ]
    config, repo = _synthetic_config_with_evidence(tmp_path, evidence=evidence)
    report = TaskLedgerContractGate(repo).verify(config, source=_source())
    scope = report.to_dict()["verification_evidence"]["gate_execution"]
    assert scope["evidence_references_verified"] is False
    assert scope["evidence_path_verified"] is False
    assert scope["evidence_digest_verified"] is False
    assert scope["evidence_assertions_verified"] is False
    assert report.state is AdmissionState.INVALID
    assert any("phase5_task_ledger_production_evidence" in v for v in report.vetoes)


def test_evidence_digest_drift_is_not_verified(tmp_path: Path) -> None:
    # The file exists but its raw-byte SHA-256 disagrees with the sealed digest;
    # the gate must veto (fail closed) and must not report verified=true.
    evidence_rel = "docs/evidence/p5-2a/task-ledger-evidence.json"
    evidence = [
        {
            "id": "phase5_task_ledger_production_evidence",
            "status": "passed",
            "path": evidence_rel,
            "sha256": "f" * 64,  # wrong digest on purpose
            "assertions": {"phase": "P5.2A"},
            "required_for_activation": True,
        }
    ]
    config, repo = _synthetic_config_with_evidence(tmp_path, evidence=evidence)
    # Create the evidence file with a digest that disagrees with the seal above.
    _write_file(repo, evidence_rel, json.dumps({"phase": "P5.2A"}) + "\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "evidence fixture"],
        check=True,
        capture_output=True,
    )
    report = TaskLedgerContractGate(repo).verify(config, source=_source())
    scope = report.to_dict()["verification_evidence"]["gate_execution"]
    assert scope["evidence_references_verified"] is False
    assert scope["evidence_digest_verified"] is False
    assert report.state is AdmissionState.INVALID
    assert any("SHA-256 drifted" in v for v in report.vetoes)


def test_unexecuted_evidence_assertions_are_not_overclaimed(tmp_path: Path) -> None:
    # A passed evidence reference whose assertion does not match the parsed
    # payload must fail closed; the gate may never write verified=true for an
    # assertion it did not actually execute and pass.
    evidence_rel = "docs/evidence/p5-2a/task-ledger-evidence.json"
    content = json.dumps({"phase": "P5.2A"}) + "\n"
    evidence = [
        {
            "id": "phase5_task_ledger_production_evidence",
            "status": "passed",
            "path": evidence_rel,
            "sha256": _digest(content),  # digest passes
            "assertions": {"phase": "P5.2B"},  # assertion does NOT match
            "required_for_activation": True,
        }
    ]
    config, repo = _synthetic_config_with_evidence(tmp_path, evidence=evidence)
    _write_file(repo, evidence_rel, content)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "evidence fixture"],
        check=True,
        capture_output=True,
    )
    report = TaskLedgerContractGate(repo).verify(config, source=_source())
    scope = report.to_dict()["verification_evidence"]["gate_execution"]
    assert scope["evidence_path_verified"] is False
    assert scope["evidence_digest_verified"] is False
    assert scope["evidence_assertions_verified"] is False
    assert scope["evidence_references_verified"] is False
    assert report.state is AdmissionState.INVALID
    assert any("assertion failed" in v for v in report.vetoes)


def test_passed_evidence_reference_is_verified_when_sealed(tmp_path: Path) -> None:
    # Positive control: a passed evidence reference whose path exists, digest
    # matches and assertions resolve is genuinely verified and reports true.
    evidence_rel = "docs/evidence/p5-2a/task-ledger-evidence.json"
    content = json.dumps({"phase": "P5.2A"}) + "\n"
    evidence = [
        {
            "id": "phase5_task_ledger_production_evidence",
            "status": "passed",
            "path": evidence_rel,
            "sha256": _digest(content),
            "assertions": {"phase": "P5.2A"},
            "required_for_activation": True,
        }
    ]
    config, repo = _synthetic_config_with_evidence(tmp_path, evidence=evidence)
    _write_file(repo, evidence_rel, content)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "evidence fixture"],
        check=True,
        capture_output=True,
    )
    report = TaskLedgerContractGate(repo).verify(config, source=_source())
    scope = report.to_dict()["verification_evidence"]["gate_execution"]
    assert scope["evidence_path_verified"] is True
    assert scope["evidence_digest_verified"] is True
    assert scope["evidence_assertions_verified"] is True
    assert scope["evidence_references_verified"] is True
    # It must still be blocked (not ready): gates off, P34.7/P5.0/P5.1 not ready.
    assert report.state is AdmissionState.BLOCKED
    assert report.activation_allowed is False
