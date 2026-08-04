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
RUN_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
WORKSPACE_RUN_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
NODE_ID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
RUN_LEASE_ID = "ffffffff-ffff-ffff-ffff-ffffffffffff"
TASK_LEASE_ID = "77777777-7777-7777-7777-777777777777"
ATTEMPT_1_ID = "88888888-8888-8888-8888-888888888881"
ATTEMPT_2_ID = "88888888-8888-8888-8888-888888888882"
ATTEMPT_3_ID = "88888888-8888-8888-8888-888888888883"
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
        "created_at": "2026-08-03T00:10:00Z",
    }


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
            "steps": [_step_mapping()],
            "attempts": [_attempt_1_mapping(), _attempt_mapping()],
            "task_leases": [_lease_mapping()],
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
            "run.node_fencing_token is required with run.node_id",
        ),
        # 20. missing Run fencing
        (
            lambda r: r.update({"run_fencing_token": None}),
            "run.run_fencing_token is required with run.run_lease_id",
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
    with pytest.raises(
        TaskLedgerContractError, match="must not retain runtime or workload identity"
    ):
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
            lambda a: a.update({"state": "dispatching", "task_lease_id": None}),
            "must be provided together",
        ),
        (
            lambda a: a.update(
                {"state": "leased", "task_lease_id": None, "task_fencing_token": None}
            ),
            "leased attempt requires",
        ),
        (
            lambda a: a.update(
                {"state": "committed", "task_lease_id": TASK_LEASE_ID, "task_fencing_token": 9}
            ),
            "must not retain a task lease",
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
    with pytest.raises(TaskLedgerContractError, match="exceeds the server-owned ceiling"):
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

    # 22. Task fencing regression
    mapping = _contract_mapping()
    attempts = mapping["ledger_contracts"]["attempts"]
    third = _attempt_mapping(ATTEMPT_3_ID, 3)
    third["task_fencing_token"] = 5
    attempts.append(third)
    with pytest.raises(TaskLedgerContractError, match="retry task fencing must increase"):
        TaskLedgerContractConfig.from_mapping(mapping)

    # 23. Attempt number regression
    mapping = _contract_mapping()
    attempts = mapping["ledger_contracts"]["attempts"]
    second = _attempt_mapping()
    second["attempt_number"] = 1
    attempts[1] = second
    with pytest.raises(TaskLedgerContractError, match="retry attempt number must increase"):
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

    # lease expiry disagrees with the bounds contract
    mapping = _contract_mapping()
    mapping["ledger_contracts"]["task_leases"][0]["expires_at"] = "2026-08-03T00:14:00Z"
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


def test_attempted_migration_0011_is_a_veto(tmp_path: Path) -> None:
    repo = _build_synthetic_repo(tmp_path)
    _write_file(
        repo,
        "backend/src/omnibase/migrations/versions/0011_p5_2_task_ledger.py",
        'revision: str = "0011"\ndown_revision: str | None = "0010"\n',
    )
    config = _synthetic_config(tmp_path, repo=repo)

    report = TaskLedgerContractGate(repo).verify(config, source=_source())

    assert report.state is AdmissionState.INVALID
    assert any(
        "migration head is 0011" in veto or "migration revision set drifted" in veto
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
