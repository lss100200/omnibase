"""Offline P5.3A Planner Proposal contract preflight.

P5.3A freezes the offline contract for the Planner Proposal DAG **without**
any ORM, Alembic migration, database table, FastAPI invocation route, Browser
or workload SDK, Agent Runtime, Planner service, Executor, dispatcher,
scheduler, worker, Celery task, polling or heartbeat loop, model or tool call,
background coroutine or network request.  It is a pure offline contract gate:
strict DTOs, closed-set enums, canonical hashing over raw UTF-8 bytes,
deterministic DAG validation and fail-closed negative semantics.

The contract freezes:

- the PlanProposal identity bound to a frozen Task, Workspace, Tenant, Actor
  and root AgentVersion;
- the closed DAG structure: bounded nodes, no cycles, depth/fan-out/concurrency
  limits, deterministic topological order and canonical graph digest;
- typed input bindings that may only reference Task input, declared dependency
  output or allowed logical resource;
- AgentVersion snapshots verified against server-provided registry: same tenant,
  sealed, not disabled/revoked/superseded;
- Tool allowlist intersection of AgentVersion, Workspace binding and Planner
  policy; no wildcard, no hidden tools, no shell/SQL/arbitrary HTTP;
- Resource scope intersection of Task, Workspace binding and AgentVersion;
  no wildcard, no cross-tenant, no read-to-write escalation;
- twelve-dimensional budget matching P5.2A semantics; node aggregate must not
  exceed the Task frozen budget; worst-case retry/replan included;
- risk and approval closed set matching the Registry/Operation/Approval system;
  high/critical nodes require a compile-time approval requirement;
- retry policy closed set; unknown effect never auto-retry;
- portability: no Hyper-V/KVM/Docker/WSL/host path/physical provider fields.

The three Phase 5 feature gates stay disabled and P5.3A remains
``blocked/not_proven`` while P34.7, P5.0, P5.1 and P5.2 production are not
``ready``.  This module never reads the root ``.env``, never connects to a
database or network, never imports SQLAlchemy/FastAPI/Celery and never starts
anything.
"""

from __future__ import annotations

import json
import re
import stat
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
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
    _nested_value,
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
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
_JSON_POINTER_REF_RE = re.compile(r"^#/(?:definitions|\$defs)/[A-Za-z0-9_.-]+$")

# Server-owned integer ceiling.
_MAX_INT = (1 << 63) - 1

# ---------------------------------------------------------------------------
# Planner ceilings (server-owned; proposals may only tighten)
# ---------------------------------------------------------------------------

_DEFAULT_PLANNER_CEILINGS = {
    "max_nodes": 32,
    "max_depth": 8,
    "max_fan_out": 8,
    "max_concurrency": 4,
    "max_replan": 2,
    "max_attempts_per_node": 2,
}

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

# ---------------------------------------------------------------------------
# Closed sets
# ---------------------------------------------------------------------------

_ALLOWED_NODE_KINDS = frozenset({
    "model_reasoning",
    "knowledge_read",
    "artifact_read_proposal",
    "artifact_write_proposal",
    "sandbox_job_proposal",
    "human_approval",
    "aggregate",
    "review",
})

_ALLOWED_RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})
_RISK_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}

_ALLOWED_RETRY_POLICIES = frozenset({
    "no_retry",
    "retry_idempotent",
    "retry_with_backoff",
})

_ALLOWED_EFFECT_CLASSES = frozenset({
    "read_only",
    "idempotent_write",
    "non_idempotent_write",
    "external_effect",
    "sandbox_exec",
    "human_action",
    "unknown",
})

_ALLOWED_INPUT_BINDING_KINDS = frozenset({
    "task_input",
    "dependency_output",
    "logical_resource",
    "server_context",
})

_ALLOWED_RESOURCE_ACCESS_MODES = frozenset({"read", "write"})
_ALLOWED_RESOURCE_VISIBILITY = frozenset({
    "workspace_private",
    "tenant_shared",
    "canonical",
})

_ALLOWED_ISOLATION_CLASSES = frozenset({
    "none",
    "process",
    "container",
    "virtual_machine",
})

_ALLOWED_NETWORK_POLICIES = frozenset({
    "deny_all",
    "internal_only",
    "restricted_egress",
})

_ALLOWED_DATA_ACCESS_MODES = frozenset({
    "none",
    "read_only",
    "read_write",
})

_ALLOWED_BUDGET_DIMENSIONS = frozenset(_DEFAULT_BUDGET_CEILINGS)

_FORBIDDEN_LOGICAL_TOKENS = frozenset({"all", "any", "*"})

_FORBIDDEN_TOOL_PATTERNS = frozenset({
    "shell", "bash", "sh", "cmd", "powershell", "exec",
    "sql", "query", "database",
    "http", "fetch", "curl", "wget",
    "docker", "container",
    "filesystem", "file_write", "file_read",
    "process", "subprocess",
    "credential", "secret",
})

_FORBIDDEN_PROPOSAL_FIELDS = frozenset({
    "command", "shell", "script", "sql", "query_sql",
    "base_url", "url", "endpoint", "headers", "authorization",
    "api_key", "credential", "host_path", "file_path",
    "working_directory", "docker_socket", "database_schema",
    "database_table", "bucket", "object_key", "provider_handle",
    "raw_tool_arguments",
})

_FORBIDDEN_PORTABILITY_TOKENS = frozenset({
    "hyperv", "kvm", "virtualbox", "vmware",
    "virtualization.framework",
    "docker", "podman", "containerd",
    "wsl", "wsl2",
    "powershell", "pwsh",
    "vm_name", "virtual_switch", "network_interface",
    "vnic", "vswitch",
})

_JSON_SCHEMA_KEYWORDS = frozenset({
    "type", "title", "description", "properties", "items",
    "required", "enum", "const", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength",
    "pattern", "format", "oneOf", "anyOf", "allOf", "not",
    "$ref", "$defs", "definitions",
})
_JSON_SCHEMA_TYPES = frozenset(
    {"string", "number", "integer", "boolean", "object", "array", "null"}
)
_JSON_SCHEMA_MAX_DEPTH = 12

_APPROVAL_POLICY_VALUES = frozenset({"optional", "required"})

# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class PlannerContractError(ConfigurationError):
    """A P5.3A planner proposal contract is unsafe, malformed or drifted."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class NodeKind(StrEnum):
    MODEL_REASONING = "model_reasoning"
    KNOWLEDGE_READ = "knowledge_read"
    ARTIFACT_READ_PROPOSAL = "artifact_read_proposal"
    ARTIFACT_WRITE_PROPOSAL = "artifact_write_proposal"
    SANDBOX_JOB_PROPOSAL = "sandbox_job_proposal"
    HUMAN_APPROVAL = "human_approval"
    AGGREGATE = "aggregate"
    REVIEW = "review"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RetryPolicy(StrEnum):
    NO_RETRY = "no_retry"
    RETRY_IDEMPOTENT = "retry_idempotent"
    RETRY_WITH_BACKOFF = "retry_with_backoff"


class EffectClass(StrEnum):
    READ_ONLY = "read_only"
    IDEMPOTENT_WRITE = "idempotent_write"
    NON_IDEMPOTENT_WRITE = "non_idempotent_write"
    EXTERNAL_EFFECT = "external_effect"
    SANDBOX_EXEC = "sandbox_exec"
    HUMAN_ACTION = "human_action"
    UNKNOWN = "unknown"


class InputBindingKind(StrEnum):
    TASK_INPUT = "task_input"
    DEPENDENCY_OUTPUT = "dependency_output"
    LOGICAL_RESOURCE = "logical_resource"
    SERVER_CONTEXT = "server_context"


class IsolationClass(StrEnum):
    NONE = "none"
    PROCESS = "process"
    CONTAINER = "container"
    VIRTUAL_MACHINE = "virtual_machine"


class NetworkPolicy(StrEnum):
    DENY_ALL = "deny_all"
    INTERNAL_ONLY = "internal_only"
    RESTRICTED_EGRESS = "restricted_egress"


class DataAccessMode(StrEnum):
    NONE = "none"
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


# ---------------------------------------------------------------------------
# Strict helpers (module-local to avoid circular import issues)
# ---------------------------------------------------------------------------


def _strict_non_negative_int(value: object, *, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise PlannerContractError(f"{name} must be a non-negative integer")
    if value > _MAX_INT:
        raise PlannerContractError(f"{name} exceeds the maximum integer")
    return value


def _strict_logical_ref(value: object, *, name: str) -> str:
    text = _strict_string(value, name=name)
    if _LOGICAL_REF_RE.fullmatch(text) is None:
        raise PlannerContractError(
            f"{name} must be a plain logical reference without wildcards or path tricks"
        )
    lowered = text.lower()
    if any(token in lowered for token in ("..", "%", "\\", "?", "#")) or any(
        text.split(":", 1)[0] == token for token in _FORBIDDEN_LOGICAL_TOKENS
    ):
        raise PlannerContractError(
            f"{name} must be a plain logical reference without wildcards or path tricks"
        )
    return text


def _strict_logical_key(value: object, *, name: str) -> str:
    text = _strict_string(value, name=name)
    if _LOGICAL_KEY_RE.fullmatch(text) is None or text in _FORBIDDEN_LOGICAL_TOKENS:
        raise PlannerContractError(
            f"{name} must be a plain logical identifier without wildcards"
        )
    return text


def _unique_strings(values: object, *, name: str) -> tuple[str, ...]:
    items = tuple(
        _strict_string(item, name=f"{name}[]") for item in _strict_list(values, name=name)
    )
    if len(items) != len(set(items)):
        raise PlannerContractError(f"{name} must not contain duplicates")
    return items


# ---------------------------------------------------------------------------
# JSON Schema validation (bounded safe subset)
# ---------------------------------------------------------------------------


def _validate_controlled_json_schema(
    value: object, *, name: str, depth: int = 0
) -> dict[str, object]:
    if depth > _JSON_SCHEMA_MAX_DEPTH:
        raise PlannerContractError(f"{name} exceeds the maximum JSON Schema depth")
    if not isinstance(value, dict):
        raise PlannerContractError(f"{name} must be a JSON Schema object")
    unexpected = sorted(set(value) - _JSON_SCHEMA_KEYWORDS)
    if unexpected:
        raise PlannerContractError(
            f"{name} uses non-controlled JSON Schema keywords: {', '.join(unexpected)}"
        )
    schema_type = value.get("type")
    if schema_type is not None and (
        not isinstance(schema_type, str) or schema_type not in _JSON_SCHEMA_TYPES
    ):
        raise PlannerContractError(f"{name}.type must be a closed-set JSON Schema type")
    # Reject unbounded additionalProperties
    if value.get("type") == "object" and "properties" not in value:
        # Object without properties must have explicit constraints
        if "additionalProperties" not in value and "$ref" not in value:
            raise PlannerContractError(
                f"{name} object type requires properties, $ref or additionalProperties"
            )
    for key in ("title", "description", "pattern", "format"):
        if key in value and (not isinstance(value[key], str) or not value[key]):
            raise PlannerContractError(f"{name}.{key} must be a non-empty string")
    for key in (
        "minLength", "maxLength", "minimum", "maximum",
        "exclusiveMinimum", "exclusiveMaximum",
    ):
        if key in value and (
            not isinstance(value[key], (int, float))
            or isinstance(value[key], bool)
            or value[key] != value[key]  # NaN
            or value[key] in (float("inf"), float("-inf"))
        ):
            raise PlannerContractError(f"{name}.{key} must be a finite number")
    ref = value.get("$ref")
    if ref is not None and (
        not isinstance(ref, str) or _JSON_POINTER_REF_RE.fullmatch(ref) is None
    ):
        raise PlannerContractError(
            f"{name}.$ref must be a local JSON pointer, not a URL or file reference"
        )
    _validate_schema_children(value, name=name, depth=depth)
    return value


def _validate_schema_children(value: dict[str, object], *, name: str, depth: int) -> None:
    for key in ("properties", "$defs", "definitions"):
        if key not in value:
            continue
        subschemas = value[key]
        if not isinstance(subschemas, dict) or not subschemas:
            raise PlannerContractError(f"{name}.{key} must be a non-empty object")
        for sub_name, sub_schema in subschemas.items():
            if not isinstance(sub_name, str) or not sub_name:
                raise PlannerContractError(f"{name}.{key} keys must be non-empty strings")
            _validate_controlled_json_schema(
                sub_schema, name=f"{name}.{key}.{sub_name}", depth=depth + 1
            )
    _validate_schema_branches(value, name=name, depth=depth)
    _validate_schema_scalars(value, name=name)


def _validate_schema_branches(value: dict[str, object], *, name: str, depth: int) -> None:
    for key in ("items", "not"):
        if key in value:
            _validate_controlled_json_schema(value[key], name=f"{name}.{key}", depth=depth + 1)
    for key in ("oneOf", "anyOf", "allOf"):
        if key not in value:
            continue
        branches = _strict_list(value[key], name=f"{name}.{key}")
        if not branches:
            raise PlannerContractError(f"{name}.{key} must not be empty")
        for index, branch in enumerate(branches):
            _validate_controlled_json_schema(
                branch, name=f"{name}.{key}[{index}]", depth=depth + 1
            )


def _validate_schema_scalars(value: dict[str, object], *, name: str) -> None:
    if "required" in value:
        required = _unique_strings(value["required"], name=f"{name}.required")
        for item in required:
            if not _LOGICAL_KEY_RE.fullmatch(item):
                raise PlannerContractError(
                    f"{name}.required contains an invalid property name"
                )
    if "enum" in value:
        enum_values = _strict_list(value["enum"], name=f"{name}.enum")
        if not enum_values:
            raise PlannerContractError(f"{name}.enum must not be empty")
        seen: set[str] = set()
        for index, scalar in enumerate(enum_values):
            if not isinstance(scalar, (str, int, float)) or isinstance(scalar, bool):
                raise PlannerContractError(f"{name}.enum[{index}] must be a scalar value")
            canonical = repr(scalar)
            if canonical in seen:
                raise PlannerContractError(f"{name}.enum contains duplicates")
            seen.add(canonical)
    if "const" in value and not isinstance(value["const"], (str, int, float)):
        raise PlannerContractError(f"{name}.const must be a scalar value")


# ---------------------------------------------------------------------------
# Snapshot DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentVersionSnapshot:
    """Server-provided snapshot of a sealed AgentVersion for proposal validation."""
    agent_definition_id: str
    agent_version_id: str
    agent_version_digest: str
    tenant_id: str
    version_state: str  # sealed, deprecated, revoked, draft
    risk_level: str
    allowed_tool_ids: tuple[str, ...]
    resource_scopes: tuple[str, ...]
    instructions_digest: str

    @classmethod
    def from_mapping(cls, value: object) -> AgentVersionSnapshot:
        data = _strict_object(value, name="agent_version_snapshot")
        _only_keys(
            data,
            {
                "agent_definition_id", "agent_version_id", "agent_version_digest",
                "tenant_id", "version_state", "risk_level",
                "allowed_tool_ids", "resource_scopes", "instructions_digest",
            },
            name="agent_version_snapshot",
        )
        tool_ids = tuple(
            _strict_logical_key(item, name="agent_version_snapshot.allowed_tool_ids[]")
            for item in _strict_list(
                data.get("allowed_tool_ids"), name="agent_version_snapshot.allowed_tool_ids"
            )
        )
        if len(tool_ids) != len(set(tool_ids)):
            raise PlannerContractError(
                "agent_version_snapshot.allowed_tool_ids must not contain duplicates"
            )
        scopes = tuple(
            _strict_logical_ref(item, name="agent_version_snapshot.resource_scopes[]")
            for item in _strict_list(
                data.get("resource_scopes"), name="agent_version_snapshot.resource_scopes"
            )
        )
        if len(scopes) != len(set(scopes)):
            raise PlannerContractError(
                "agent_version_snapshot.resource_scopes must not contain duplicates"
            )
        version_state = _closed_state(
            data.get("version_state"),
            name="agent_version_snapshot.version_state",
            allowed=frozenset({"draft", "sealed", "deprecated", "revoked"}),
        )
        risk = _closed_state(
            data.get("risk_level"),
            name="agent_version_snapshot.risk_level",
            allowed=_ALLOWED_RISK_LEVELS,
        )
        return cls(
            agent_definition_id=_strict_uuid(
                data.get("agent_definition_id"),
                name="agent_version_snapshot.agent_definition_id",
            ),
            agent_version_id=_strict_uuid(
                data.get("agent_version_id"),
                name="agent_version_snapshot.agent_version_id",
            ),
            agent_version_digest=_strict_digest(
                data.get("agent_version_digest"),
                name="agent_version_snapshot.agent_version_digest",
            ),
            tenant_id=_strict_uuid(
                data.get("tenant_id"), name="agent_version_snapshot.tenant_id"
            ),
            version_state=version_state,
            risk_level=risk,
            allowed_tool_ids=tool_ids,
            resource_scopes=scopes,
            instructions_digest=_strict_digest(
                data.get("instructions_digest"),
                name="agent_version_snapshot.instructions_digest",
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_definition_id": self.agent_definition_id,
            "agent_version_id": self.agent_version_id,
            "agent_version_digest": self.agent_version_digest,
            "tenant_id": self.tenant_id,
            "version_state": self.version_state,
            "risk_level": self.risk_level,
            "allowed_tool_ids": list(self.allowed_tool_ids),
            "resource_scopes": list(self.resource_scopes),
            "instructions_digest": self.instructions_digest,
        }


@dataclass(frozen=True, slots=True)
class ToolVersionSnapshot:
    """Server-provided snapshot of a registered tool version."""
    tool_id: str
    tool_version: str
    tool_digest: str
    effect_class: str
    input_schema: dict[str, object]

    @classmethod
    def from_mapping(cls, value: object) -> ToolVersionSnapshot:
        data = _strict_object(value, name="tool_version_snapshot")
        _only_keys(
            data,
            {"tool_id", "tool_version", "tool_digest", "effect_class", "input_schema"},
            name="tool_version_snapshot",
        )
        effect = _closed_state(
            data.get("effect_class"),
            name="tool_version_snapshot.effect_class",
            allowed=_ALLOWED_EFFECT_CLASSES,
        )
        schema = _validate_controlled_json_schema(
            data.get("input_schema"), name="tool_version_snapshot.input_schema"
        )
        return cls(
            tool_id=_strict_logical_key(
                data.get("tool_id"), name="tool_version_snapshot.tool_id"
            ),
            tool_version=_strict_string(
                data.get("tool_version"), name="tool_version_snapshot.tool_version"
            ),
            tool_digest=_strict_digest(
                data.get("tool_digest"), name="tool_version_snapshot.tool_digest"
            ),
            effect_class=effect,
            input_schema=schema,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "tool_id": self.tool_id,
            "tool_version": self.tool_version,
            "tool_digest": self.tool_digest,
            "effect_class": self.effect_class,
            "input_schema": self.input_schema,
        }


@dataclass(frozen=True, slots=True)
class WorkspaceScopeSnapshot:
    """Server-provided frozen Workspace scope for a proposal."""
    workspace_id: str
    workspace_generation: int
    tenant_id: str
    resource_scopes: tuple[str, ...]
    tool_binding_ids: tuple[str, ...]
    scope_digest: str

    @classmethod
    def from_mapping(cls, value: object) -> WorkspaceScopeSnapshot:
        data = _strict_object(value, name="workspace_scope_snapshot")
        _only_keys(
            data,
            {
                "workspace_id", "workspace_generation", "tenant_id",
                "resource_scopes", "tool_binding_ids", "scope_digest",
            },
            name="workspace_scope_snapshot",
        )
        scopes = tuple(
            _strict_logical_ref(item, name="workspace_scope_snapshot.resource_scopes[]")
            for item in _strict_list(
                data.get("resource_scopes"), name="workspace_scope_snapshot.resource_scopes"
            )
        )
        if len(scopes) != len(set(scopes)):
            raise PlannerContractError(
                "workspace_scope_snapshot.resource_scopes must not contain duplicates"
            )
        tools = tuple(
            _strict_logical_key(item, name="workspace_scope_snapshot.tool_binding_ids[]")
            for item in _strict_list(
                data.get("tool_binding_ids"),
                name="workspace_scope_snapshot.tool_binding_ids",
            )
        )
        if len(tools) != len(set(tools)):
            raise PlannerContractError(
                "workspace_scope_snapshot.tool_binding_ids must not contain duplicates"
            )
        return cls(
            workspace_id=_strict_uuid(
                data.get("workspace_id"), name="workspace_scope_snapshot.workspace_id"
            ),
            workspace_generation=_strict_positive_int(
                data.get("workspace_generation"),
                name="workspace_scope_snapshot.workspace_generation",
            ),
            tenant_id=_strict_uuid(
                data.get("tenant_id"), name="workspace_scope_snapshot.tenant_id"
            ),
            resource_scopes=scopes,
            tool_binding_ids=tools,
            scope_digest=_strict_digest(
                data.get("scope_digest"), name="workspace_scope_snapshot.scope_digest"
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "workspace_generation": self.workspace_generation,
            "tenant_id": self.tenant_id,
            "resource_scopes": list(self.resource_scopes),
            "tool_binding_ids": list(self.tool_binding_ids),
            "scope_digest": self.scope_digest,
        }


@dataclass(frozen=True, slots=True)
class FrozenTaskSnapshot:
    """Server-provided frozen Task context for a proposal."""
    task_id: str
    task_generation: int
    tenant_id: str
    workspace_id: str
    workspace_generation: int
    actor_user_id: str
    agent_definition_id: str
    agent_version_id: str
    agent_version_digest: str
    resource_scope_digest: str
    budget_policy_digest: str
    deadline: str
    task_budget: dict[str, int]

    @classmethod
    def from_mapping(cls, value: object) -> FrozenTaskSnapshot:
        data = _strict_object(value, name="frozen_task_snapshot")
        _only_keys(
            data,
            {
                "task_id", "task_generation", "tenant_id", "workspace_id",
                "workspace_generation", "actor_user_id", "agent_definition_id",
                "agent_version_id", "agent_version_digest",
                "resource_scope_digest", "budget_policy_digest", "deadline",
                "task_budget",
            },
            name="frozen_task_snapshot",
        )
        budget_data = _strict_object(
            data.get("task_budget"), name="frozen_task_snapshot.task_budget"
        )
        _only_keys(budget_data, set(_DEFAULT_BUDGET_CEILINGS), name="frozen_task_snapshot.task_budget")
        task_budget: dict[str, int] = {}
        for dim in _DEFAULT_BUDGET_CEILINGS:
            task_budget[dim] = _strict_positive_int(
                budget_data.get(dim), name=f"frozen_task_snapshot.task_budget.{dim}"
            )
        return cls(
            task_id=_strict_uuid(
                data.get("task_id"), name="frozen_task_snapshot.task_id"
            ),
            task_generation=_strict_positive_int(
                data.get("task_generation"), name="frozen_task_snapshot.task_generation"
            ),
            tenant_id=_strict_uuid(
                data.get("tenant_id"), name="frozen_task_snapshot.tenant_id"
            ),
            workspace_id=_strict_uuid(
                data.get("workspace_id"), name="frozen_task_snapshot.workspace_id"
            ),
            workspace_generation=_strict_positive_int(
                data.get("workspace_generation"),
                name="frozen_task_snapshot.workspace_generation",
            ),
            actor_user_id=_strict_uuid(
                data.get("actor_user_id"), name="frozen_task_snapshot.actor_user_id"
            ),
            agent_definition_id=_strict_uuid(
                data.get("agent_definition_id"),
                name="frozen_task_snapshot.agent_definition_id",
            ),
            agent_version_id=_strict_uuid(
                data.get("agent_version_id"),
                name="frozen_task_snapshot.agent_version_id",
            ),
            agent_version_digest=_strict_digest(
                data.get("agent_version_digest"),
                name="frozen_task_snapshot.agent_version_digest",
            ),
            resource_scope_digest=_strict_digest(
                data.get("resource_scope_digest"),
                name="frozen_task_snapshot.resource_scope_digest",
            ),
            budget_policy_digest=_strict_digest(
                data.get("budget_policy_digest"),
                name="frozen_task_snapshot.budget_policy_digest",
            ),
            deadline=_strict_timestamp(
                data.get("deadline"), name="frozen_task_snapshot.deadline"
            ),
            task_budget=task_budget,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "task_generation": self.task_generation,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "workspace_generation": self.workspace_generation,
            "actor_user_id": self.actor_user_id,
            "agent_definition_id": self.agent_definition_id,
            "agent_version_id": self.agent_version_id,
            "agent_version_digest": self.agent_version_digest,
            "resource_scope_digest": self.resource_scope_digest,
            "budget_policy_digest": self.budget_policy_digest,
            "deadline": self.deadline,
            "task_budget": dict(self.task_budget),
        }


# ---------------------------------------------------------------------------
# Plan node sub-contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanInputBinding:
    node_id: str
    binding_kind: str
    source_ref: str
    source_field: str

    @classmethod
    def from_mapping(cls, value: object) -> PlanInputBinding:
        data = _strict_object(value, name="input_binding")
        _only_keys(
            data, {"node_id", "binding_kind", "source_ref", "source_field"},
            name="input_binding",
        )
        kind = _closed_state(
            data.get("binding_kind"),
            name="input_binding.binding_kind",
            allowed=_ALLOWED_INPUT_BINDING_KINDS,
        )
        return cls(
            node_id=_strict_logical_key(
                data.get("node_id"), name="input_binding.node_id"
            ),
            binding_kind=kind,
            source_ref=_strict_logical_ref(
                data.get("source_ref"), name="input_binding.source_ref"
            ),
            source_field=_strict_logical_key(
                data.get("source_field"), name="input_binding.source_field"
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "binding_kind": self.binding_kind,
            "source_ref": self.source_ref,
            "source_field": self.source_field,
        }


@dataclass(frozen=True, slots=True)
class PlanOutputContract:
    output_schema: dict[str, object]
    output_digest: str

    @classmethod
    def from_mapping(cls, value: object) -> PlanOutputContract:
        data = _strict_object(value, name="output_contract")
        _only_keys(data, {"output_schema", "output_digest"}, name="output_contract")
        schema = _validate_controlled_json_schema(
            data.get("output_schema"), name="output_contract.output_schema"
        )
        return cls(
            output_schema=schema,
            output_digest=_strict_digest(
                data.get("output_digest"), name="output_contract.output_digest"
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "output_schema": self.output_schema,
            "output_digest": self.output_digest,
        }


@dataclass(frozen=True, slots=True)
class PlanNodeBudget:
    values: tuple[tuple[str, int], ...]

    @classmethod
    def from_mapping(
        cls, value: object, *, ceilings: Mapping[str, int]
    ) -> PlanNodeBudget:
        data = _strict_object(value, name="node_budget")
        _only_keys(data, set(_DEFAULT_BUDGET_CEILINGS), name="node_budget")
        parsed: list[tuple[str, int]] = []
        for dim in sorted(_DEFAULT_BUDGET_CEILINGS):
            dim_value = _strict_positive_int(
                data.get(dim), name=f"node_budget.{dim}"
            )
            if dim_value > ceilings[dim]:
                raise PlannerContractError(
                    f"node_budget.{dim} exceeds the server-owned ceiling {ceilings[dim]}"
                )
            parsed.append((dim, dim_value))
        return cls(values=tuple(parsed))

    def as_mapping(self) -> dict[str, int]:
        return dict(self.values)

    def to_dict(self) -> dict[str, int]:
        return self.as_mapping()


@dataclass(frozen=True, slots=True)
class PlanRetryPolicy:
    policy: str
    max_retries: int
    backoff_base_ms: int

    @classmethod
    def from_mapping(cls, value: object) -> PlanRetryPolicy:
        data = _strict_object(value, name="retry_policy")
        _only_keys(
            data, {"policy", "max_retries", "backoff_base_ms"}, name="retry_policy"
        )
        policy = _closed_state(
            data.get("policy"),
            name="retry_policy.policy",
            allowed=_ALLOWED_RETRY_POLICIES,
        )
        max_retries = _strict_non_negative_int(
            data.get("max_retries"), name="retry_policy.max_retries"
        )
        if max_retries > _DEFAULT_PLANNER_CEILINGS["max_attempts_per_node"]:
            raise PlannerContractError(
                "retry_policy.max_retries exceeds the server ceiling of "
                f"{_DEFAULT_PLANNER_CEILINGS['max_attempts_per_node']}"
            )
        backoff = _strict_non_negative_int(
            data.get("backoff_base_ms"), name="retry_policy.backoff_base_ms"
        )
        if policy == "no_retry" and max_retries != 0:
            raise PlannerContractError(
                "retry_policy with no_retry must have max_retries=0"
            )
        return cls(policy=policy, max_retries=max_retries, backoff_base_ms=backoff)

    def to_dict(self) -> dict[str, object]:
        return {
            "policy": self.policy,
            "max_retries": self.max_retries,
            "backoff_base_ms": self.backoff_base_ms,
        }


@dataclass(frozen=True, slots=True)
class PlanApprovalRequirement:
    plan_digest: str
    node_digest: str
    task_id: str
    tenant_id: str
    workspace_id: str
    resource_ref: str
    resource_version: str
    tool_digest: str | None
    action: str
    request_hash: str
    risk_level: str
    required_approver_role: str

    @classmethod
    def from_mapping(cls, value: object) -> PlanApprovalRequirement:
        data = _strict_object(value, name="approval_requirement")
        _only_keys(
            data,
            {
                "plan_digest", "node_digest", "task_id", "tenant_id",
                "workspace_id", "resource_ref", "resource_version",
                "tool_digest", "action", "request_hash", "risk_level",
                "required_approver_role",
            },
            name="approval_requirement",
        )
        tool_digest = data.get("tool_digest")
        if tool_digest is not None:
            tool_digest = _strict_digest(tool_digest, name="approval_requirement.tool_digest")
        return cls(
            plan_digest=_strict_digest(
                data.get("plan_digest"), name="approval_requirement.plan_digest"
            ),
            node_digest=_strict_digest(
                data.get("node_digest"), name="approval_requirement.node_digest"
            ),
            task_id=_strict_uuid(
                data.get("task_id"), name="approval_requirement.task_id"
            ),
            tenant_id=_strict_uuid(
                data.get("tenant_id"), name="approval_requirement.tenant_id"
            ),
            workspace_id=_strict_uuid(
                data.get("workspace_id"), name="approval_requirement.workspace_id"
            ),
            resource_ref=_strict_logical_ref(
                data.get("resource_ref"), name="approval_requirement.resource_ref"
            ),
            resource_version=_strict_string(
                data.get("resource_version"),
                name="approval_requirement.resource_version",
            ),
            tool_digest=tool_digest,
            action=_strict_logical_ref(
                data.get("action"), name="approval_requirement.action"
            ),
            request_hash=_strict_digest(
                data.get("request_hash"), name="approval_requirement.request_hash"
            ),
            risk_level=_closed_state(
                data.get("risk_level"),
                name="approval_requirement.risk_level",
                allowed=_ALLOWED_RISK_LEVELS,
            ),
            required_approver_role=_strict_logical_key(
                data.get("required_approver_role"),
                name="approval_requirement.required_approver_role",
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_digest": self.plan_digest,
            "node_digest": self.node_digest,
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "resource_ref": self.resource_ref,
            "resource_version": self.resource_version,
            "tool_digest": self.tool_digest,
            "action": self.action,
            "request_hash": self.request_hash,
            "risk_level": self.risk_level,
            "required_approver_role": self.required_approver_role,
        }


@dataclass(frozen=True, slots=True)
class ExecutionRequirement:
    """Provider-neutral execution requirement for nodes that need execution."""
    isolation_class: str
    untrusted_code: bool
    os_architecture: str
    network_policy: str
    workspace_data_access_mode: str
    artifact_policy: str
    resource_ceilings: dict[str, int]
    required_logical_capabilities: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: object) -> ExecutionRequirement:
        data = _strict_object(value, name="execution_requirement")
        _only_keys(
            data,
            {
                "isolation_class", "untrusted_code", "os_architecture",
                "network_policy", "workspace_data_access_mode",
                "artifact_policy", "resource_ceilings",
                "required_logical_capabilities",
            },
            name="execution_requirement",
        )
        isolation = _closed_state(
            data.get("isolation_class"),
            name="execution_requirement.isolation_class",
            allowed=_ALLOWED_ISOLATION_CLASSES,
        )
        network = _closed_state(
            data.get("network_policy"),
            name="execution_requirement.network_policy",
            allowed=_ALLOWED_NETWORK_POLICIES,
        )
        data_mode = _closed_state(
            data.get("workspace_data_access_mode"),
            name="execution_requirement.workspace_data_access_mode",
            allowed=_ALLOWED_DATA_ACCESS_MODES,
        )
        os_arch = _strict_string(
            data.get("os_architecture"),
            name="execution_requirement.os_architecture",
        )
        if not re.fullmatch(r"^[a-z0-9][a-z0-9_/]{1,31}$", os_arch):
            raise PlannerContractError(
                "execution_requirement.os_architecture must be a plain identifier"
            )
        artifact = _strict_logical_key(
            data.get("artifact_policy"),
            name="execution_requirement.artifact_policy",
        )
        ceilings_obj = _strict_object(
            data.get("resource_ceilings"),
            name="execution_requirement.resource_ceilings",
        )
        resource_ceilings: dict[str, int] = {}
        for k, v in ceilings_obj.items():
            if not isinstance(k, str) or not _LOGICAL_KEY_RE.fullmatch(k):
                raise PlannerContractError(
                    "execution_requirement.resource_ceilings key must be a logical key"
                )
            resource_ceilings[k] = _strict_positive_int(
                v, name=f"execution_requirement.resource_ceilings.{k}"
            )
        caps = tuple(
            _strict_logical_key(
                item, name="execution_requirement.required_logical_capabilities[]"
            )
            for item in _strict_list(
                data.get("required_logical_capabilities"),
                name="execution_requirement.required_logical_capabilities",
            )
        )
        if len(caps) != len(set(caps)):
            raise PlannerContractError(
                "execution_requirement.required_logical_capabilities must not contain duplicates"
            )
        return cls(
            isolation_class=isolation,
            untrusted_code=data.get("untrusted_code") is True
            if isinstance(data.get("untrusted_code"), bool)
            else _raise_bool_error("execution_requirement.untrusted_code"),
            os_architecture=os_arch,
            network_policy=network,
            workspace_data_access_mode=data_mode,
            artifact_policy=artifact,
            resource_ceilings=resource_ceilings,
            required_logical_capabilities=caps,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "isolation_class": self.isolation_class,
            "untrusted_code": self.untrusted_code,
            "os_architecture": self.os_architecture,
            "network_policy": self.network_policy,
            "workspace_data_access_mode": self.workspace_data_access_mode,
            "artifact_policy": self.artifact_policy,
            "resource_ceilings": dict(self.resource_ceilings),
            "required_logical_capabilities": list(self.required_logical_capabilities),
        }


def _raise_bool_error(name: str) -> bool:
    raise PlannerContractError(f"{name} must be a boolean")


# ---------------------------------------------------------------------------
# PlanNodeProposal
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanNodeProposal:
    node_id: str
    node_kind: str
    agent_definition_id: str
    agent_version_id: str
    agent_version_digest: str
    depends_on: tuple[str, ...]
    input_bindings: tuple[PlanInputBinding, ...]
    output_contract: PlanOutputContract
    allowed_tool_ids: tuple[str, ...]
    resource_scopes: tuple[str, ...]
    risk_level: str
    budget: PlanNodeBudget
    timeout_ms: int
    retry_policy: PlanRetryPolicy
    approval_requirement: PlanApprovalRequirement | None
    effect_class: str
    execution_requirement: ExecutionRequirement | None
    node_digest: str

    @classmethod
    def from_mapping(
        cls, value: object, *, ceilings: Mapping[str, int]
    ) -> PlanNodeProposal:
        data = _strict_object(value, name="plan_node")
        _only_keys(
            data,
            {
                "node_id", "node_kind", "agent_definition_id", "agent_version_id",
                "agent_version_digest", "depends_on", "input_bindings",
                "output_contract", "allowed_tool_ids", "resource_scopes",
                "risk_level", "budget", "timeout_ms", "retry_policy",
                "approval_requirement", "effect_class",
                "execution_requirement", "node_digest",
            },
            name="plan_node",
        )
        node_kind = _closed_state(
            data.get("node_kind"),
            name="plan_node.node_kind",
            allowed=_ALLOWED_NODE_KINDS,
        )
        deps = _unique_strings(data.get("depends_on"), name="plan_node.depends_on")
        bindings = tuple(
            PlanInputBinding.from_mapping(item)
            for item in _strict_list(
                data.get("input_bindings"), name="plan_node.input_bindings"
            )
        )
        tools = tuple(
            _strict_logical_key(item, name="plan_node.allowed_tool_ids[]")
            for item in _strict_list(
                data.get("allowed_tool_ids"), name="plan_node.allowed_tool_ids"
            )
        )
        if len(tools) != len(set(tools)):
            raise PlannerContractError(
                "plan_node.allowed_tool_ids must not contain duplicates"
            )
        scopes = tuple(
            _strict_logical_ref(item, name="plan_node.resource_scopes[]")
            for item in _strict_list(
                data.get("resource_scopes"), name="plan_node.resource_scopes"
            )
        )
        if len(scopes) != len(set(scopes)):
            raise PlannerContractError(
                "plan_node.resource_scopes must not contain duplicates"
            )
        risk = _closed_state(
            data.get("risk_level"),
            name="plan_node.risk_level",
            allowed=_ALLOWED_RISK_LEVELS,
        )
        effect = _closed_state(
            data.get("effect_class"),
            name="plan_node.effect_class",
            allowed=_ALLOWED_EFFECT_CLASSES,
        )
        approval = data.get("approval_requirement")
        if approval is not None:
            approval = PlanApprovalRequirement.from_mapping(approval)
        execution = data.get("execution_requirement")
        if execution is not None:
            execution = ExecutionRequirement.from_mapping(execution)
        return cls(
            node_id=_strict_logical_key(
                data.get("node_id"), name="plan_node.node_id"
            ),
            node_kind=node_kind,
            agent_definition_id=_strict_uuid(
                data.get("agent_definition_id"), name="plan_node.agent_definition_id"
            ),
            agent_version_id=_strict_uuid(
                data.get("agent_version_id"), name="plan_node.agent_version_id"
            ),
            agent_version_digest=_strict_digest(
                data.get("agent_version_digest"),
                name="plan_node.agent_version_digest",
            ),
            depends_on=deps,
            input_bindings=bindings,
            output_contract=PlanOutputContract.from_mapping(
                data.get("output_contract")
            ),
            allowed_tool_ids=tools,
            resource_scopes=scopes,
            risk_level=risk,
            budget=PlanNodeBudget.from_mapping(data.get("budget"), ceilings=ceilings),
            timeout_ms=_strict_positive_int(
                data.get("timeout_ms"), name="plan_node.timeout_ms"
            ),
            retry_policy=PlanRetryPolicy.from_mapping(data.get("retry_policy")),
            approval_requirement=approval,
            effect_class=effect,
            execution_requirement=execution,
            node_digest=_strict_digest(
                data.get("node_digest"), name="plan_node.node_digest"
            ),
        )

    def canonical_payload(self) -> dict[str, object]:
        """The canonical payload used for the node digest.

        The ``approval_requirement`` is deliberately excluded: the approval
        binds *to* the node digest, so including it would create a circular
        dependency.  The approval is validated separately by the validator.
        """
        return {
            "node_id": self.node_id,
            "node_kind": self.node_kind,
            "agent_definition_id": self.agent_definition_id,
            "agent_version_id": self.agent_version_id,
            "agent_version_digest": self.agent_version_digest,
            "depends_on": sorted(self.depends_on),
            "input_bindings": [b.to_dict() for b in self.input_bindings],
            "output_contract": self.output_contract.to_dict(),
            "allowed_tool_ids": sorted(self.allowed_tool_ids),
            "resource_scopes": sorted(self.resource_scopes),
            "risk_level": self.risk_level,
            "budget": self.budget.to_dict(),
            "timeout_ms": self.timeout_ms,
            "retry_policy": self.retry_policy.to_dict(),
            "effect_class": self.effect_class,
            "execution_requirement": (
                self.execution_requirement.to_dict()
                if self.execution_requirement is not None
                else None
            ),
        }

    def compute_node_digest(self) -> str:
        return _sha256_bytes(_canonical_json(self.canonical_payload()))

    def to_dict(self) -> dict[str, object]:
        payload = self.canonical_payload()
        payload["approval_requirement"] = (
            self.approval_requirement.to_dict()
            if self.approval_requirement is not None
            else None
        )
        payload["node_digest"] = self.node_digest
        return payload


# ---------------------------------------------------------------------------
# PlannerPolicy / PlannerCeilings
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlannerCeilings:
    values: dict[str, int]

    @classmethod
    def from_mapping(cls, value: object) -> PlannerCeilings:
        data = _strict_object(value, name="planner_ceilings")
        _only_keys(data, set(_DEFAULT_PLANNER_CEILINGS), name="planner_ceilings")
        parsed: dict[str, int] = {}
        for key, ceiling in _DEFAULT_PLANNER_CEILINGS.items():
            parsed[key] = _strict_positive_int(
                data.get(key), name=f"planner_ceilings.{key}"
            )
            if parsed[key] > ceiling:
                raise PlannerContractError(
                    f"planner_ceilings.{key} may only tighten the server-owned "
                    f"ceiling {ceiling}"
                )
        return cls(values=parsed)

    def to_dict(self) -> dict[str, int]:
        return dict(self.values)


@dataclass(frozen=True, slots=True)
class PlannerPolicy:
    schema_version: int
    policy_digest: str
    allowed_node_kinds: tuple[str, ...]
    allowed_tool_ids: tuple[str, ...]
    max_replan: int
    approval_policy: dict[str, str]
    ceilings: PlannerCeilings

    @classmethod
    def from_mapping(cls, value: object) -> PlannerPolicy:
        data = _strict_object(value, name="planner_policy")
        _only_keys(
            data,
            {
                "schema_version", "policy_digest", "allowed_node_kinds",
                "allowed_tool_ids", "max_replan", "approval_policy", "ceilings",
            },
            name="planner_policy",
        )
        if data.get("schema_version") != 1:
            raise PlannerContractError("planner_policy.schema_version must be 1")
        kinds = tuple(
            _closed_state(
                item,
                name="planner_policy.allowed_node_kinds[]",
                allowed=_ALLOWED_NODE_KINDS,
            )
            for item in _strict_list(
                data.get("allowed_node_kinds"),
                name="planner_policy.allowed_node_kinds",
            )
        )
        if len(kinds) != len(set(kinds)):
            raise PlannerContractError(
                "planner_policy.allowed_node_kinds must not contain duplicates"
            )
        tools = tuple(
            _strict_logical_key(item, name="planner_policy.allowed_tool_ids[]")
            for item in _strict_list(
                data.get("allowed_tool_ids"), name="planner_policy.allowed_tool_ids"
            )
        )
        if len(tools) != len(set(tools)):
            raise PlannerContractError(
                "planner_policy.allowed_tool_ids must not contain duplicates"
            )
        max_replan = _strict_non_negative_int(
            data.get("max_replan"), name="planner_policy.max_replan"
        )
        if max_replan > _DEFAULT_PLANNER_CEILINGS["max_replan"]:
            raise PlannerContractError(
                "planner_policy.max_replan exceeds the server ceiling"
            )
        approval = _parse_approval_policy(data.get("approval_policy"))
        ceilings = PlannerCeilings.from_mapping(data.get("ceilings"))
        return cls(
            schema_version=1,
            policy_digest=_strict_digest(
                data.get("policy_digest"), name="planner_policy.policy_digest"
            ),
            allowed_node_kinds=kinds,
            allowed_tool_ids=tools,
            max_replan=max_replan,
            approval_policy=approval,
            ceilings=ceilings,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "policy_digest": self.policy_digest,
            "allowed_node_kinds": list(self.allowed_node_kinds),
            "allowed_tool_ids": list(self.allowed_tool_ids),
            "max_replan": self.max_replan,
            "approval_policy": dict(self.approval_policy),
            "ceilings": self.ceilings.to_dict(),
        }


def _parse_approval_policy(value: object) -> dict[str, str]:
    data = _strict_object(value, name="planner_policy.approval_policy")
    _only_keys(data, {"low", "medium", "high", "critical"}, name="approval_policy")
    policy: dict[str, str] = {}
    for level in ("low", "medium", "high", "critical"):
        raw = _strict_string(data.get(level), name=f"approval_policy.{level}")
        if raw not in _APPROVAL_POLICY_VALUES:
            raise PlannerContractError(
                f"approval_policy.{level} must be optional or required"
            )
        policy[level] = raw
    if policy["high"] != "required" or policy["critical"] != "required":
        raise PlannerContractError(
            "approval_policy.high and approval_policy.critical must be required"
        )
    return policy


# ---------------------------------------------------------------------------
# PlanProposal
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanProposal:
    schema_version: int
    proposal_id: str
    tenant_id: str
    workspace_id: str
    workspace_generation: int
    task_id: str
    task_generation: int
    actor_user_id: str
    root_agent_definition_id: str
    root_agent_version_id: str
    root_agent_version_digest: str
    request_hash: str
    goal_digest: str
    planner_policy_digest: str
    resource_scope_digest: str
    budget_policy_digest: str
    deadline: str
    proposal_version: int
    created_at: str
    nodes: tuple[PlanNodeProposal, ...]
    plan_budget: dict[str, int]
    plan_risk_summary: dict[str, int]
    proposal_digest: str
    parent_proposal_id: str | None
    parent_proposal_version: int | None

    @classmethod
    def from_mapping(
        cls, value: object, *, budget_ceilings: Mapping[str, int]
    ) -> PlanProposal:
        data = _strict_object(value, name="plan_proposal")
        _only_keys(
            data,
            {
                "schema_version", "proposal_id", "tenant_id", "workspace_id",
                "workspace_generation", "task_id", "task_generation",
                "actor_user_id", "root_agent_definition_id",
                "root_agent_version_id", "root_agent_version_digest",
                "request_hash", "goal_digest", "planner_policy_digest",
                "resource_scope_digest", "budget_policy_digest",
                "deadline", "proposal_version", "created_at", "nodes",
                "plan_budget", "plan_risk_summary", "proposal_digest",
                "parent_proposal_id", "parent_proposal_version",
            },
            name="plan_proposal",
        )
        if data.get("schema_version") != 1:
            raise PlannerContractError("plan_proposal.schema_version must be 1")
        nodes = tuple(
            PlanNodeProposal.from_mapping(item, ceilings=budget_ceilings)
            for item in _strict_list(data.get("nodes"), name="plan_proposal.nodes")
        )
        budget_data = _strict_object(
            data.get("plan_budget"), name="plan_proposal.plan_budget"
        )
        _only_keys(budget_data, set(_DEFAULT_BUDGET_CEILINGS), name="plan_proposal.plan_budget")
        plan_budget: dict[str, int] = {}
        for dim in _DEFAULT_BUDGET_CEILINGS:
            plan_budget[dim] = _strict_positive_int(
                budget_data.get(dim), name=f"plan_proposal.plan_budget.{dim}"
            )
        risk_data = _strict_object(
            data.get("plan_risk_summary"), name="plan_proposal.plan_risk_summary"
        )
        _only_keys(risk_data, set(_ALLOWED_RISK_LEVELS), name="plan_proposal.plan_risk_summary")
        risk_summary: dict[str, int] = {}
        for level in _ALLOWED_RISK_LEVELS:
            risk_summary[level] = _strict_non_negative_int(
                risk_data.get(level),
                name=f"plan_proposal.plan_risk_summary.{level}",
            )
        parent_id = data.get("parent_proposal_id")
        if parent_id is not None:
            parent_id = _strict_uuid(parent_id, name="plan_proposal.parent_proposal_id")
        parent_version = data.get("parent_proposal_version")
        if parent_version is not None:
            parent_version = _strict_positive_int(
                parent_version, name="plan_proposal.parent_proposal_version"
            )
        if (parent_id is None) != (parent_version is None):
            raise PlannerContractError(
                "parent_proposal_id and parent_proposal_version must be provided together"
            )
        return cls(
            schema_version=1,
            proposal_id=_strict_uuid(
                data.get("proposal_id"), name="plan_proposal.proposal_id"
            ),
            tenant_id=_strict_uuid(
                data.get("tenant_id"), name="plan_proposal.tenant_id"
            ),
            workspace_id=_strict_uuid(
                data.get("workspace_id"), name="plan_proposal.workspace_id"
            ),
            workspace_generation=_strict_positive_int(
                data.get("workspace_generation"),
                name="plan_proposal.workspace_generation",
            ),
            task_id=_strict_uuid(
                data.get("task_id"), name="plan_proposal.task_id"
            ),
            task_generation=_strict_positive_int(
                data.get("task_generation"), name="plan_proposal.task_generation"
            ),
            actor_user_id=_strict_uuid(
                data.get("actor_user_id"), name="plan_proposal.actor_user_id"
            ),
            root_agent_definition_id=_strict_uuid(
                data.get("root_agent_definition_id"),
                name="plan_proposal.root_agent_definition_id",
            ),
            root_agent_version_id=_strict_uuid(
                data.get("root_agent_version_id"),
                name="plan_proposal.root_agent_version_id",
            ),
            root_agent_version_digest=_strict_digest(
                data.get("root_agent_version_digest"),
                name="plan_proposal.root_agent_version_digest",
            ),
            request_hash=_strict_digest(
                data.get("request_hash"), name="plan_proposal.request_hash"
            ),
            goal_digest=_strict_digest(
                data.get("goal_digest"), name="plan_proposal.goal_digest"
            ),
            planner_policy_digest=_strict_digest(
                data.get("planner_policy_digest"),
                name="plan_proposal.planner_policy_digest",
            ),
            resource_scope_digest=_strict_digest(
                data.get("resource_scope_digest"),
                name="plan_proposal.resource_scope_digest",
            ),
            budget_policy_digest=_strict_digest(
                data.get("budget_policy_digest"),
                name="plan_proposal.budget_policy_digest",
            ),
            deadline=_strict_timestamp(
                data.get("deadline"), name="plan_proposal.deadline"
            ),
            proposal_version=_strict_positive_int(
                data.get("proposal_version"), name="plan_proposal.proposal_version"
            ),
            created_at=_strict_timestamp(
                data.get("created_at"), name="plan_proposal.created_at"
            ),
            nodes=nodes,
            plan_budget=plan_budget,
            plan_risk_summary=risk_summary,
            proposal_digest=_strict_digest(
                data.get("proposal_digest"), name="plan_proposal.proposal_digest"
            ),
            parent_proposal_id=parent_id,
            parent_proposal_version=parent_version,
        )

    def canonical_payload(self) -> dict[str, object]:
        # Nodes are sorted by node_id for canonical stability regardless of
        # input array order.  This is a DAG: the semantic identity of the plan
        # must not depend on the serialization order of its nodes.
        sorted_nodes = sorted(
            [n.canonical_payload() for n in self.nodes],
            key=lambda n: str(n["node_id"]),
        )
        return {
            "schema_version": self.schema_version,
            "proposal_id": self.proposal_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "workspace_generation": self.workspace_generation,
            "task_id": self.task_id,
            "task_generation": self.task_generation,
            "actor_user_id": self.actor_user_id,
            "root_agent_definition_id": self.root_agent_definition_id,
            "root_agent_version_id": self.root_agent_version_id,
            "root_agent_version_digest": self.root_agent_version_digest,
            "request_hash": self.request_hash,
            "goal_digest": self.goal_digest,
            "planner_policy_digest": self.planner_policy_digest,
            "resource_scope_digest": self.resource_scope_digest,
            "budget_policy_digest": self.budget_policy_digest,
            "deadline": self.deadline,
            "proposal_version": self.proposal_version,
            "created_at": self.created_at,
            "nodes": sorted_nodes,
            "plan_budget": dict(sorted(self.plan_budget.items())),
            "plan_risk_summary": dict(sorted(self.plan_risk_summary.items())),
            "parent_proposal_id": self.parent_proposal_id,
            "parent_proposal_version": self.parent_proposal_version,
        }

    def compute_proposal_digest(self) -> str:
        return _sha256_bytes(_canonical_json(self.canonical_payload()))

    def to_dict(self) -> dict[str, object]:
        payload = self.canonical_payload()
        payload["proposal_digest"] = self.proposal_digest
        return payload


# ---------------------------------------------------------------------------
# ValidatedPlan / Validation findings
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanValidationFinding:
    code: str
    severity: str  # error, warning
    message: str
    node_id: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "node_id": self.node_id,
        }


@dataclass(frozen=True, slots=True)
class PlanValidationReport:
    valid: bool
    proposal_digest: str | None
    topological_order: tuple[str, ...]
    findings: tuple[PlanValidationFinding, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "proposal_digest": self.proposal_digest,
            "topological_order": list(self.topological_order),
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass(frozen=True, slots=True)
class ValidatedPlan:
    """An immutable validated plan version. Never modified in place."""
    proposal: PlanProposal
    validation_report: PlanValidationReport
    validated_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "proposal": self.proposal.to_dict(),
            "validation_report": self.validation_report.to_dict(),
            "validated_at": self.validated_at,
        }


# ---------------------------------------------------------------------------
# Deterministic DAG Validator
# ---------------------------------------------------------------------------


class PlanProposalValidator:
    """Deterministic server-side validator for PlanProposal contracts.

    The validator never calls a model, never starts a runtime, never accesses
    a database or network.  All validation is pure deterministic computation
    over the proposal data and server-provided snapshots.
    """

    def __init__(
        self,
        *,
        agent_versions: tuple[AgentVersionSnapshot, ...],
        tool_versions: tuple[ToolVersionSnapshot, ...],
        workspace_scope: WorkspaceScopeSnapshot,
        frozen_task: FrozenTaskSnapshot,
        planner_policy: PlannerPolicy,
        budget_ceilings: Mapping[str, int],
    ) -> None:
        self._agent_versions = {av.agent_version_id: av for av in agent_versions}
        self._tool_versions = {tv.tool_id: tv for tv in tool_versions}
        self._workspace_scope = workspace_scope
        self._frozen_task = frozen_task
        self._planner_policy = planner_policy
        self._budget_ceilings = budget_ceilings
        self._findings: list[PlanValidationFinding] = []

    def _add_finding(
        self,
        *,
        code: str,
        severity: str = "error",
        message: str,
        node_id: str | None = None,
    ) -> None:
        self._findings.append(
            PlanValidationFinding(
                code=code, severity=severity, message=message, node_id=node_id
            )
        )

    def validate(self, proposal: PlanProposal) -> PlanValidationReport:
        self._findings = []
        self._validate_identity(proposal)
        topo = self._validate_dag_structure(proposal)
        self._validate_data_flow(proposal, topo)
        self._validate_agent_versions(proposal)
        self._validate_tool_allowlist(proposal)
        self._validate_resource_scope(proposal)
        self._validate_budget(proposal)
        self._validate_risk_and_approval(proposal)
        self._validate_retry_and_deadline(proposal)
        self._validate_portability(proposal)
        self._validate_no_forbidden_fields(proposal)
        self._validate_digests(proposal)
        valid = all(f.severity == "error" for f in self._findings) is False or not self._findings
        has_errors = any(f.severity == "error" for f in self._findings)
        proposal_digest = (
            proposal.compute_proposal_digest() if not has_errors else None
        )
        return PlanValidationReport(
            valid=not has_errors,
            proposal_digest=proposal_digest,
            topological_order=topo,
            findings=tuple(self._findings),
        )

    def _validate_identity(self, proposal: PlanProposal) -> None:
        if proposal.tenant_id != self._frozen_task.tenant_id:
            self._add_finding(
                code="identity_tenant_mismatch",
                message="proposal tenant_id does not match frozen task tenant_id",
            )
        if proposal.workspace_id != self._frozen_task.workspace_id:
            self._add_finding(
                code="identity_workspace_mismatch",
                message="proposal workspace_id does not match frozen task workspace_id",
            )
        if proposal.task_id != self._frozen_task.task_id:
            self._add_finding(
                code="identity_task_mismatch",
                message="proposal task_id does not match frozen task task_id",
            )
        if proposal.task_generation != self._frozen_task.task_generation:
            self._add_finding(
                code="identity_task_generation_mismatch",
                message="proposal task_generation does not match frozen task",
            )

    def _validate_dag_structure(self, proposal: PlanProposal) -> tuple[str, ...]:
        nodes = proposal.nodes
        if not nodes:
            self._add_finding(
                code="dag_empty",
                message="proposal must contain at least one node",
            )
            return ()
        node_ids = [n.node_id for n in nodes]
        if len(node_ids) != len(set(node_ids)):
            self._add_finding(
                code="dag_duplicate_node_id",
                message="node_id values must be unique",
            )
            return ()
        max_nodes = self._planner_policy.ceilings.values["max_nodes"]
        if len(nodes) > max_nodes:
            self._add_finding(
                code="dag_exceeds_max_nodes",
                message=f"node count {len(nodes)} exceeds ceiling {max_nodes}",
            )
        node_set = set(node_ids)
        # Check dependency references and self-dependency
        for node in nodes:
            if node.node_id in node.depends_on:
                self._add_finding(
                    code="dag_self_dependency",
                    message=f"node {node.node_id} depends on itself",
                    node_id=node.node_id,
                )
            for dep in node.depends_on:
                if dep not in node_set:
                    self._add_finding(
                        code="dag_missing_dependency",
                        message=f"node {node.node_id} depends on unknown node {dep}",
                        node_id=node.node_id,
                    )
            if len(node.depends_on) != len(set(node.depends_on)):
                self._add_finding(
                    code="dag_duplicate_dependency",
                    message=f"node {node.node_id} has duplicate dependencies",
                    node_id=node.node_id,
                )
            max_fan_out = self._planner_policy.ceilings.values["max_fan_out"]
            if len(node.depends_on) > max_fan_out:
                self._add_finding(
                    code="dag_exceeds_max_fan_out",
                    message=f"node {node.node_id} fan-out {len(node.depends_on)} "
                            f"exceeds ceiling {max_fan_out}",
                    node_id=node.node_id,
                )
        if any(f.severity == "error" for f in self._findings):
            return ()
        # Kahn's algorithm for cycle detection and deterministic topological order
        in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
        adjacency: dict[str, list[str]] = {nid: [] for nid in node_ids}
        for node in nodes:
            for dep in node.depends_on:
                adjacency[dep].append(node.node_id)
                in_degree[node.node_id] += 1
        # Use sorted queue for deterministic order
        queue: deque[str] = deque(sorted(nid for nid, deg in in_degree.items() if deg == 0))
        topo: list[str] = []
        while queue:
            current = queue.popleft()
            topo.append(current)
            # Sort successors for deterministic order
            for successor in sorted(adjacency[current]):
                in_degree[successor] -= 1
                if in_degree[successor] == 0:
                    queue.append(successor)
            # Re-sort to maintain deterministic order after adding new items
            remaining = sorted(queue)
            queue.clear()
            queue.extend(remaining)
        if len(topo) != len(node_ids):
            self._add_finding(
                code="dag_cycle_detected",
                message="proposal DAG contains a cycle",
            )
            return ()
        # Compute depth
        depth_map: dict[str, int] = {}
        for nid in topo:
            node = next(n for n in nodes if n.node_id == nid)
            if not node.depends_on:
                depth_map[nid] = 0
            else:
                depth_map[nid] = max(depth_map[dep] for dep in node.depends_on) + 1
        max_depth = self._planner_policy.ceilings.values["max_depth"]
        max_computed_depth = max(depth_map.values()) if depth_map else 0
        if max_computed_depth >= max_depth:
            self._add_finding(
                code="dag_exceeds_max_depth",
                message=f"DAG depth {max_computed_depth + 1} exceeds ceiling {max_depth}",
            )
        # Compute max concurrency per level
        levels: dict[int, list[str]] = {}
        for nid, depth in depth_map.items():
            levels.setdefault(depth, []).append(nid)
        max_concurrency = self._planner_policy.ceilings.values["max_concurrency"]
        for level_depth, level_nodes in levels.items():
            if len(level_nodes) > max_concurrency:
                self._add_finding(
                    code="dag_exceeds_max_concurrency",
                    message=f"DAG level {level_depth} has {len(level_nodes)} concurrent "
                            f"nodes, exceeding ceiling {max_concurrency}",
                )
        return tuple(topo)

    def _validate_data_flow(
        self, proposal: PlanProposal, topo: tuple[str, ...]
    ) -> None:
        if not topo:
            return
        node_map = {n.node_id: n for n in proposal.nodes}
        topo_set = set(topo)
        for node in proposal.nodes:
            for binding in node.input_bindings:
                if binding.binding_kind == "dependency_output":
                    if binding.source_ref not in set(node.depends_on):
                        self._add_finding(
                            code="dataflow_undeclared_dependency",
                            message=f"node {node.node_id} references output of "
                                    f"{binding.source_ref} which is not a declared dependency",
                            node_id=node.node_id,
                        )
                elif binding.binding_kind == "task_input":
                    pass  # Always valid
                elif binding.binding_kind == "logical_resource":
                    if binding.source_ref not in self._workspace_scope.resource_scopes:
                        self._add_finding(
                            code="dataflow_unknown_resource",
                            message=f"node {node.node_id} references unknown resource "
                                    f"{binding.source_ref}",
                            node_id=node.node_id,
                        )
                elif binding.binding_kind == "server_context":
                    pass  # Server-owned, always valid

    def _validate_agent_versions(self, proposal: PlanProposal) -> None:
        # Validate root agent
        root_av = self._agent_versions.get(proposal.root_agent_version_id)
        if root_av is None:
            self._add_finding(
                code="agent_version_missing",
                message=f"root agent version {proposal.root_agent_version_id} not in snapshot",
            )
        else:
            if root_av.tenant_id != proposal.tenant_id:
                self._add_finding(
                    code="agent_version_tenant_mismatch",
                    message="root agent version tenant does not match proposal tenant",
                )
            if root_av.version_state != "sealed":
                self._add_finding(
                    code="agent_version_not_sealed",
                    message=f"root agent version state is {root_av.version_state}, not sealed",
                )
            if root_av.agent_version_digest != proposal.root_agent_version_digest:
                self._add_finding(
                    code="agent_version_digest_mismatch",
                    message="root agent version digest does not match",
                )
        # Validate per-node agents
        for node in proposal.nodes:
            av = self._agent_versions.get(node.agent_version_id)
            if av is None:
                self._add_finding(
                    code="agent_version_missing",
                    message=f"node {node.node_id} references unknown agent version "
                            f"{node.agent_version_id}",
                    node_id=node.node_id,
                )
                continue
            if av.tenant_id != proposal.tenant_id:
                self._add_finding(
                    code="agent_version_tenant_mismatch",
                    message=f"node {node.node_id} agent version crosses tenant boundary",
                    node_id=node.node_id,
                )
            if av.version_state != "sealed":
                self._add_finding(
                    code="agent_version_not_sealed",
                    message=f"node {node.node_id} agent version is {av.version_state}",
                    node_id=node.node_id,
                )
            if av.agent_version_digest != node.agent_version_digest:
                self._add_finding(
                    code="agent_version_digest_mismatch",
                    message=f"node {node.node_id} agent version digest mismatch",
                    node_id=node.node_id,
                )
            if av.agent_definition_id != node.agent_definition_id:
                self._add_finding(
                    code="agent_version_definition_mismatch",
                    message=f"node {node.node_id} agent definition mismatch",
                    node_id=node.node_id,
                )

    def _validate_tool_allowlist(self, proposal: PlanProposal) -> None:
        policy_tools = set(self._planner_policy.allowed_tool_ids)
        workspace_tools = set(self._workspace_scope.tool_binding_ids)
        for node in proposal.nodes:
            av = self._agent_versions.get(node.agent_version_id)
            agent_tools = set(av.allowed_tool_ids) if av is not None else set()
            for tool_id in node.allowed_tool_ids:
                if tool_id in _FORBIDDEN_LOGICAL_TOKENS:
                    self._add_finding(
                        code="tool_wildcard",
                        message=f"node {node.node_id} uses wildcard tool {tool_id}",
                        node_id=node.node_id,
                    )
                if tool_id not in policy_tools:
                    self._add_finding(
                        code="tool_not_in_policy",
                        message=f"node {node.node_id} tool {tool_id} not in planner policy",
                        node_id=node.node_id,
                    )
                if tool_id not in workspace_tools:
                    self._add_finding(
                        code="tool_not_in_workspace",
                        message=f"node {node.node_id} tool {tool_id} not in workspace binding",
                        node_id=node.node_id,
                    )
                if av is not None and tool_id not in agent_tools:
                    self._add_finding(
                        code="tool_not_in_agent",
                        message=f"node {node.node_id} tool {tool_id} not in agent version "
                                "allowlist",
                        node_id=node.node_id,
                    )
                # Check forbidden tool patterns
                lowered = tool_id.lower()
                for pattern in _FORBIDDEN_TOOL_PATTERNS:
                    if pattern in lowered:
                        self._add_finding(
                            code="tool_forbidden_pattern",
                            message=f"node {node.node_id} tool {tool_id} matches "
                                    f"forbidden pattern {pattern}",
                            node_id=node.node_id,
                        )

    def _validate_resource_scope(self, proposal: PlanProposal) -> None:
        workspace_scopes = set(self._workspace_scope.resource_scopes)
        for node in proposal.nodes:
            for scope in node.resource_scopes:
                if scope in _FORBIDDEN_LOGICAL_TOKENS:
                    self._add_finding(
                        code="scope_wildcard",
                        message=f"node {node.node_id} uses wildcard scope",
                        node_id=node.node_id,
                    )
                if scope not in workspace_scopes:
                    self._add_finding(
                        code="scope_not_in_workspace",
                        message=f"node {node.node_id} scope {scope} not in workspace",
                        node_id=node.node_id,
                    )
            av = self._agent_versions.get(node.agent_version_id)
            if av is not None:
                agent_scopes = set(av.resource_scopes)
                for scope in node.resource_scopes:
                    if scope not in agent_scopes:
                        self._add_finding(
                            code="scope_not_in_agent",
                            message=f"node {node.node_id} scope {scope} not in agent version",
                            node_id=node.node_id,
                        )

    def _validate_budget(self, proposal: PlanProposal) -> None:
        # Aggregate node budgets
        aggregate: dict[str, int] = {dim: 0 for dim in _DEFAULT_BUDGET_CEILINGS}
        for node in proposal.nodes:
            node_budget = node.budget.as_mapping()
            # Worst-case: multiply by (1 + max_retries)
            worst_factor = 1 + node.retry_policy.max_retries
            for dim, limit in _DEFAULT_BUDGET_CEILINGS.items():
                node_value = node_budget.get(dim, 0)
                worst_value = node_value * worst_factor
                aggregate[dim] += worst_value
                if aggregate[dim] > _MAX_INT:
                    self._add_finding(
                        code="budget_overflow",
                        message=f"budget dimension {dim} aggregate overflow",
                        node_id=node.node_id,
                    )
        # Check against task frozen budget
        for dim in _DEFAULT_BUDGET_CEILINGS:
            plan_value = proposal.plan_budget.get(dim, 0)
            task_value = self._frozen_task.task_budget.get(dim, 0)
            if plan_value > task_value:
                self._add_finding(
                    code="budget_exceeds_task",
                    message=f"plan budget {dim} ({plan_value}) exceeds task budget "
                            f"({task_value})",
                )
            if aggregate[dim] > plan_value:
                self._add_finding(
                    code="budget_node_aggregate_exceeds_plan",
                    message=f"node aggregate for {dim} ({aggregate[dim]}) exceeds "
                            f"plan budget ({plan_value})",
                )
        # total_tokens consistency
        total = proposal.plan_budget.get("total_tokens", 0)
        input_t = proposal.plan_budget.get("input_tokens", 0)
        output_t = proposal.plan_budget.get("output_tokens", 0)
        reasoning_t = proposal.plan_budget.get("reasoning_tokens", 0)
        if input_t + output_t + reasoning_t > total:
            self._add_finding(
                code="budget_token_inconsistency",
                message="input + output + reasoning tokens exceed total_tokens",
            )

    def _validate_risk_and_approval(self, proposal: PlanProposal) -> None:
        for node in proposal.nodes:
            av = self._agent_versions.get(node.agent_version_id)
            if av is not None:
                if _RISK_RANK[node.risk_level] < _RISK_RANK[av.risk_level]:
                    self._add_finding(
                        code="risk_downgrade",
                        message=f"node {node.node_id} risk {node.risk_level} downgrades "
                                f"agent risk {av.risk_level}",
                        node_id=node.node_id,
                    )
            policy_req = self._planner_policy.approval_policy.get(node.risk_level)
            if policy_req == "required":
                if node.approval_requirement is None:
                    self._add_finding(
                        code="approval_missing",
                        message=f"node {node.node_id} risk {node.risk_level} requires "
                                "an approval requirement",
                        node_id=node.node_id,
                    )
                else:
                    self._validate_approval_binding(node, proposal)

    def _validate_approval_binding(
        self, node: PlanNodeProposal, proposal: PlanProposal
    ) -> None:
        if node.approval_requirement is None:
            return
        approval = node.approval_requirement
        if approval.plan_digest != proposal.proposal_digest:
            self._add_finding(
                code="approval_plan_digest_drift",
                message=f"node {node.node_id} approval plan_digest does not match proposal",
                node_id=node.node_id,
            )
        if approval.node_digest != node.node_digest:
            self._add_finding(
                code="approval_node_digest_drift",
                message=f"node {node.node_id} approval node_digest does not match",
                node_id=node.node_id,
            )
        if approval.task_id != proposal.task_id:
            self._add_finding(
                code="approval_task_drift",
                message=f"node {node.node_id} approval task_id does not match",
                node_id=node.node_id,
            )
        if approval.tenant_id != proposal.tenant_id:
            self._add_finding(
                code="approval_tenant_drift",
                message=f"node {node.node_id} approval tenant_id does not match",
                node_id=node.node_id,
            )
        if approval.workspace_id != proposal.workspace_id:
            self._add_finding(
                code="approval_workspace_drift",
                message=f"node {node.node_id} approval workspace_id does not match",
                node_id=node.node_id,
            )
        if approval.risk_level != node.risk_level:
            self._add_finding(
                code="approval_risk_drift",
                message=f"node {node.node_id} approval risk_level does not match node",
                node_id=node.node_id,
            )

    def _validate_retry_and_deadline(self, proposal: PlanProposal) -> None:
        for node in proposal.nodes:
            if node.effect_class == "unknown" and node.retry_policy.policy != "no_retry":
                self._add_finding(
                    code="retry_unknown_effect",
                    message=f"node {node.node_id} has unknown effect but allows retry",
                    node_id=node.node_id,
                )
            if node.effect_class in (
                "external_effect", "sandbox_exec", "human_action"
            ) and node.retry_policy.policy != "no_retry":
                self._add_finding(
                    code="retry_non_idempotent_effect",
                    message=f"node {node.node_id} has non-replayable effect class "
                            f"{node.effect_class} but allows retry",
                    node_id=node.node_id,
                )

    def _validate_portability(self, proposal: PlanProposal) -> None:
        """Reject any provider-specific fields in the proposal."""
        raw = json.dumps(proposal.to_dict())
        lowered = raw.lower()
        for token in _FORBIDDEN_PORTABILITY_TOKENS:
            if token in lowered:
                self._add_finding(
                    code="portability_forbidden_token",
                    message=f"proposal contains forbidden portability token: {token}",
                )

    def _validate_no_forbidden_fields(self, proposal: PlanProposal) -> None:
        """Reject proposals that embed executable fields."""
        self._check_forbidden_in_mapping(proposal.to_dict(), path="plan_proposal")

    def _check_forbidden_in_mapping(
        self, value: object, *, path: str, depth: int = 0
    ) -> None:
        if depth > 30:
            return
        if isinstance(value, dict):
            for key, sub in value.items():
                if key in _FORBIDDEN_PROPOSAL_FIELDS:
                    self._add_finding(
                        code="forbidden_field",
                        message=f"proposal contains forbidden field {key} at {path}",
                    )
                self._check_forbidden_in_mapping(sub, path=f"{path}.{key}", depth=depth + 1)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                self._check_forbidden_in_mapping(
                    item, path=f"{path}[{i}]", depth=depth + 1
                )

    def _validate_digests(self, proposal: PlanProposal) -> None:
        computed = proposal.compute_proposal_digest()
        if computed != proposal.proposal_digest:
            self._add_finding(
                code="proposal_digest_mismatch",
                message="proposal_digest does not match canonical payload",
            )
        for node in proposal.nodes:
            computed_node = node.compute_node_digest()
            if computed_node != node.node_digest:
                self._add_finding(
                    code="node_digest_mismatch",
                    message=f"node {node.node_id} node_digest does not match canonical payload",
                    node_id=node.node_id,
                )


def node_set_from_deps(node: PlanNodeProposal) -> set[str]:
    return set(node.depends_on)


# ---------------------------------------------------------------------------
# Gate configuration and report
# ---------------------------------------------------------------------------


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
        raise PlannerContractError(
            "P5.3A contract requires every Phase 5 feature gate to be disabled"
        )
    return gates


def _parse_formal_state(value: object, *, name: str) -> P347FormalState:
    text = _strict_string(value, name=name)
    try:
        return P347FormalState(text)
    except ValueError as exc:
        raise PlannerContractError(f"{name} has an invalid state") from exc


def _parse_sealed_contract(value: object) -> tuple[str, str, str]:
    data = _strict_object(value, name="sealed_contracts[]")
    _only_keys(data, {"name", "path", "sha256"}, name="sealed_contracts[]")
    name = _strict_string(data.get("name"), name="sealed_contracts[].name")
    path = _relative_repo_path(data.get("path"), name="sealed_contracts[].path")
    digest = _strict_digest(data.get("sha256"), name="sealed_contracts[].sha256")
    return name, path, digest


@dataclass(frozen=True, slots=True)
class PlannerContractConfig:
    schema_version: int
    phase: str
    activation_requested: bool
    feature_gates: dict[str, object]
    p34_7: tuple[P347FormalState, str, str]
    p5_0: tuple[P347FormalState, str, str]
    p5_1: tuple[P347FormalState, str, str]
    p5_2a: tuple[P347FormalState, str, str]
    source: SourceScope
    evidence: tuple[EvidenceReference, ...]
    budget_ceilings: dict[str, int]
    planner_policy: PlannerPolicy
    agent_version_snapshots: tuple[AgentVersionSnapshot, ...]
    tool_version_snapshots: tuple[ToolVersionSnapshot, ...]
    workspace_scope: WorkspaceScopeSnapshot
    frozen_task: FrozenTaskSnapshot
    plan_proposals: tuple[PlanProposal, ...]
    forbidden_source_paths: tuple[str, ...]
    baseline_migration_revisions: tuple[str, ...]
    sealed_contracts: tuple[tuple[str, str, str], ...]
    openapi_snapshot_path: str
    openapi_snapshot_sha256: str
    critical_veto: int

    @classmethod
    def from_mapping(cls, value: object) -> PlannerContractConfig:
        data = _strict_object(value, name="configuration")
        _only_keys(
            data,
            {
                "schema_version", "phase", "activation_requested",
                "feature_gates",
                "p34_7", "p5_0", "p5_1", "p5_2a",
                "source", "evidence", "budget_ceilings",
                "planner_policy", "agent_version_snapshots",
                "tool_version_snapshots", "workspace_scope",
                "frozen_task", "plan_proposals",
                "forbidden_source_paths", "baseline_migration_revisions",
                "sealed_contracts", "openapi_snapshot", "critical_veto",
            },
            name="configuration",
        )
        if data.get("schema_version") != 1:
            raise PlannerContractError("configuration.schema_version must be 1")
        if data.get("phase") != "P5.3A":
            raise PlannerContractError("configuration.phase must be P5.3A")
        if data.get("activation_requested") is not False:
            raise PlannerContractError(
                "P5.3A contract requires activation_requested to be false"
            )
        gates = _parse_gate_block(data.get("feature_gates"))
        p34_7 = _parse_upstream_block(data.get("p34_7"), sub_key="decision", name="p34_7")
        p5_0 = _parse_upstream_block(
            data.get("p5_0"), sub_key="admission_contract", name="p5_0"
        )
        p5_1 = _parse_upstream_block(
            data.get("p5_1"), sub_key="registry_contract", name="p5_1"
        )
        p5_2a = _parse_upstream_block(
            data.get("p5_2a"), sub_key="task_ledger_contract", name="p5_2a"
        )
        source = SourceScope.from_mapping(data.get("source"))
        evidence = tuple(
            EvidenceReference.from_mapping(item)
            for item in _strict_list(data.get("evidence"), name="evidence")
        )
        if not evidence or len({item.evidence_id for item in evidence}) != len(evidence):
            raise PlannerContractError("evidence IDs must be non-empty and unique")
        # Budget ceilings
        budget_data = _strict_object(data.get("budget_ceilings"), name="budget_ceilings")
        _only_keys(budget_data, set(_DEFAULT_BUDGET_CEILINGS), name="budget_ceilings")
        budget_ceilings: dict[str, int] = {}
        for dim, ceiling in _DEFAULT_BUDGET_CEILINGS.items():
            v = _strict_positive_int(
                budget_data.get(dim), name=f"budget_ceilings.{dim}"
            )
            if v > ceiling:
                raise PlannerContractError(
                    f"budget_ceilings.{dim} may only tighten the server ceiling {ceiling}"
                )
            budget_ceilings[dim] = v
        planner_policy = PlannerPolicy.from_mapping(data.get("planner_policy"))
        av_snapshots = tuple(
            AgentVersionSnapshot.from_mapping(item)
            for item in _strict_list(
                data.get("agent_version_snapshots"), name="agent_version_snapshots"
            )
        )
        tv_snapshots = tuple(
            ToolVersionSnapshot.from_mapping(item)
            for item in _strict_list(
                data.get("tool_version_snapshots"), name="tool_version_snapshots"
            )
        )
        ws_scope = WorkspaceScopeSnapshot.from_mapping(data.get("workspace_scope"))
        frozen_task = FrozenTaskSnapshot.from_mapping(data.get("frozen_task"))
        proposals = tuple(
            PlanProposal.from_mapping(item, budget_ceilings=budget_ceilings)
            for item in _strict_list(
                data.get("plan_proposals"), name="plan_proposals"
            )
        )
        if not proposals:
            raise PlannerContractError("plan_proposals must not be empty")
        forbidden = tuple(
            _relative_repo_path(item, name="forbidden_source_paths[]")
            for item in _strict_list(
                data.get("forbidden_source_paths"), name="forbidden_source_paths"
            )
        )
        if not forbidden or len(forbidden) != len(set(forbidden)):
            raise PlannerContractError(
                "forbidden_source_paths must be non-empty and unique"
            )
        baseline = tuple(
            _strict_string(item, name="baseline_migration_revisions[]")
            for item in _strict_list(
                data.get("baseline_migration_revisions"),
                name="baseline_migration_revisions",
            )
        )
        if not baseline or len(baseline) != len(set(baseline)):
            raise PlannerContractError(
                "baseline_migration_revisions must be non-empty and unique"
            )
        sealed = tuple(
            _parse_sealed_contract(item)
            for item in _strict_list(data.get("sealed_contracts"), name="sealed_contracts")
        )
        if not sealed or len({n for n, _, _ in sealed}) != len(sealed):
            raise PlannerContractError(
                "sealed_contracts must be non-empty with unique names"
            )
        openapi = _strict_object(data.get("openapi_snapshot"), name="openapi_snapshot")
        _only_keys(openapi, {"path", "sha256"}, name="openapi_snapshot")
        critical = _strict_object(data.get("critical_veto"), name="critical_veto")
        _only_keys(critical, {"expected"}, name="critical_veto")
        if critical.get("expected") != 0:
            raise PlannerContractError("critical_veto.expected must be exactly 0")
        return cls(
            schema_version=1,
            phase="P5.3A",
            activation_requested=False,
            feature_gates=gates,
            p34_7=p34_7,
            p5_0=p5_0,
            p5_1=p5_1,
            p5_2a=p5_2a,
            source=source,
            evidence=evidence,
            budget_ceilings=budget_ceilings,
            planner_policy=planner_policy,
            agent_version_snapshots=av_snapshots,
            tool_version_snapshots=tv_snapshots,
            workspace_scope=ws_scope,
            frozen_task=frozen_task,
            plan_proposals=proposals,
            forbidden_source_paths=forbidden,
            baseline_migration_revisions=baseline,
            sealed_contracts=sealed,
            openapi_snapshot_path=_relative_repo_path(
                openapi.get("path"), name="openapi_snapshot.path"
            ),
            openapi_snapshot_sha256=_strict_digest(
                openapi.get("sha256"), name="openapi_snapshot.sha256"
            ),
            critical_veto=0,
        )

    def canonical_digest(self) -> str:
        return _sha256_bytes(_canonical_json(self.to_dict()))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "phase": self.phase,
            "activation_requested": False,
            "feature_gates": self.feature_gates,
            "p34_7": {
                "formal_state": self.p34_7[0].value,
                "decision": {"path": self.p34_7[1], "sha256": self.p34_7[2]},
            },
            "p5_0": {
                "formal_state": self.p5_0[0].value,
                "admission_contract": {
                    "path": self.p5_0[1], "sha256": self.p5_0[2]
                },
            },
            "p5_1": {
                "formal_state": self.p5_1[0].value,
                "registry_contract": {
                    "path": self.p5_1[1], "sha256": self.p5_1[2]
                },
            },
            "p5_2a": {
                "formal_state": self.p5_2a[0].value,
                "task_ledger_contract": {
                    "path": self.p5_2a[1], "sha256": self.p5_2a[2]
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
            "budget_ceilings": self.budget_ceilings,
            "planner_policy": self.planner_policy.to_dict(),
            "agent_version_snapshots": [
                s.to_dict() for s in self.agent_version_snapshots
            ],
            "tool_version_snapshots": [
                s.to_dict() for s in self.tool_version_snapshots
            ],
            "workspace_scope": self.workspace_scope.to_dict(),
            "frozen_task": self.frozen_task.to_dict(),
            "plan_proposals": [p.to_dict() for p in self.plan_proposals],
            "forbidden_source_paths": list(self.forbidden_source_paths),
            "baseline_migration_revisions": list(self.baseline_migration_revisions),
            "sealed_contracts": [
                {"name": n, "path": p, "sha256": d}
                for n, p, d in self.sealed_contracts
            ],
            "openapi_snapshot": {
                "path": self.openapi_snapshot_path,
                "sha256": self.openapi_snapshot_sha256,
            },
            "critical_veto": {"expected": self.critical_veto},
        }


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


# ---------------------------------------------------------------------------
# PlannerContractReport
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlannerContractReport:
    state: AdmissionState
    activation_allowed: bool
    contract_valid: bool
    configuration_sha256: str
    feature_gates: dict[str, bool]
    p34_7_formal_state: str
    p5_0_formal_state: str
    p5_1_formal_state: str
    p5_2a_formal_state: str
    source: GitSourceProvenance | None
    passed_evidence: tuple[str, ...]
    blockers: tuple[str, ...]
    vetoes: tuple[str, ...]
    migration_head: str | None
    planner_validation_results: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "gate": "P5.3A Planner Proposal contract preflight",
            "state": self.state.value,
            "activation_allowed": self.activation_allowed,
            "contract_valid": self.contract_valid,
            "configuration_sha256": self.configuration_sha256,
            "feature_gates": self.feature_gates,
            "p34_7_formal_state": self.p34_7_formal_state,
            "p5_0_formal_state": self.p5_0_formal_state,
            "p5_1_formal_state": self.p5_1_formal_state,
            "p5_2a_formal_state": self.p5_2a_formal_state,
            "source": None if self.source is None else self.source.to_dict(),
            "passed_evidence": list(self.passed_evidence),
            "blockers": list(self.blockers),
            "vetoes": list(self.vetoes),
            "migration_head": self.migration_head,
            "planner_runtime_created": False,
            "planner_execution_activated": False,
            "dag_execution_allowed": False,
            "planner_validation_results": list(self.planner_validation_results),
            "root_env_accessed": False,
            "business_database_accessed": False,
            "business_database_migrated": False,
            "external_network_accessed": False,
            "model_or_tool_invoked": False,
            "agent_runtime_activated": False,
            "executor_activated": False,
            "worker_or_scheduler_started": False,
        }


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def _lexists(path: Path) -> bool:
    try:
        path.lstat()
    except OSError:
        return False
    return True


class PlannerContractGate:
    """Offline P5.3A preflight; never ready until upstream gates are ready."""

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root.resolve(strict=True)

    def validate_only(self, config: PlannerContractConfig) -> PlannerContractReport:
        blockers = [
            "formal Phase 5 planner contract verification was not executed",
            "Planner Runtime is not implemented",
            "Plan persistence is not implemented",
            "Plan execution API is not implemented",
        ]
        if config.p34_7[0] is not P347FormalState.READY:
            blockers.append(f"P34.7 formal state is not ready: {config.p34_7[0].value}")
        if config.p5_0[0] is not P347FormalState.READY:
            blockers.append(f"P5.0 formal state is not ready: {config.p5_0[0].value}")
        if config.p5_1[0] is not P347FormalState.READY:
            blockers.append(f"P5.1 formal state is not ready: {config.p5_1[0].value}")
        if config.p5_2a[0] is not P347FormalState.READY:
            blockers.append(f"P5.2A formal state is not ready: {config.p5_2a[0].value}")
        # Run proposal validation
        validation_results = self._run_proposal_validation(config)
        return PlannerContractReport(
            state=AdmissionState.BLOCKED,
            activation_allowed=False,
            contract_valid=True,
            configuration_sha256=config.canonical_digest(),
            feature_gates={
                "agent_runtime_enabled": False,
                "agent_planner_enabled": False,
                "multi_agent_enabled": False,
            },
            p34_7_formal_state=config.p34_7[0].value,
            p5_0_formal_state=config.p5_0[0].value,
            p5_1_formal_state=config.p5_1[0].value,
            p5_2a_formal_state=config.p5_2a[0].value,
            source=None,
            passed_evidence=(),
            blockers=tuple(blockers),
            vetoes=(),
            migration_head=None,
            planner_validation_results=validation_results,
        )

    def verify(
        self,
        config: PlannerContractConfig,
        *,
        source: GitSourceProvenance | None = None,
        gate_values: Mapping[str, object] | None = None,
    ) -> PlannerContractReport:
        provenance = source or build_git_source_provenance(self._repo_root, config.source)
        blockers: list[str] = []
        vetoes: list[str] = []
        if config.source.require_clean_checkout and not provenance.clean:
            vetoes.append("Phase 5 planner contract requires a clean checkout")
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
                vetoes.append("Phase 5 feature gates must remain disabled")
            else:
                blockers.append(
                    "Planner gate remains disabled: planner activation is not authorized"
                )
        if config.p34_7[0] is not P347FormalState.READY:
            blockers.append(f"P34.7 formal state is not ready: {config.p34_7[0].value}")
        if config.p5_0[0] is not P347FormalState.READY:
            blockers.append(f"P5.0 formal state is not ready: {config.p5_0[0].value}")
        if config.p5_1[0] is not P347FormalState.READY:
            blockers.append(f"P5.1 formal state is not ready: {config.p5_1[0].value}")
        if config.p5_2a[0] is not P347FormalState.READY:
            blockers.append(f"P5.2A formal state is not ready: {config.p5_2a[0].value}")
        blockers.extend(
            f"{item.evidence_id}: {item.status.value}"
            for item in config.evidence
            if item.required_for_activation and item.status is not EvidenceStatus.PASSED
        )
        blockers.extend([
            "Planner Runtime is not implemented",
            "Plan persistence is not implemented",
            "Plan execution API is not implemented",
        ])
        try:
            migration_head = self._verify_source_boundaries(config)
        except (
            PlannerContractError,
            ConfigurationError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            OSError,
        ) as exc:
            vetoes.append(f"source boundaries: {exc}")
            migration_head = None
        try:
            self._verify_sealed_files(config)
        except (
            PlannerContractError,
            ConfigurationError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            OSError,
        ) as exc:
            vetoes.append(f"sealed contracts: {exc}")
        validation_results = self._run_proposal_validation(config)
        state = AdmissionState.INVALID if vetoes else AdmissionState.BLOCKED
        return PlannerContractReport(
            state=state,
            activation_allowed=False,
            contract_valid=not vetoes,
            configuration_sha256=config.canonical_digest(),
            feature_gates=resolved,
            p34_7_formal_state=config.p34_7[0].value,
            p5_0_formal_state=config.p5_0[0].value,
            p5_1_formal_state=config.p5_1[0].value,
            p5_2a_formal_state=config.p5_2a[0].value,
            source=provenance,
            passed_evidence=(),
            blockers=tuple(blockers),
            vetoes=tuple(vetoes),
            migration_head=migration_head,
            planner_validation_results=validation_results,
        )

    def _run_proposal_validation(
        self, config: PlannerContractConfig
    ) -> tuple[dict[str, object], ...]:
        results: list[dict[str, object]] = []
        validator = PlanProposalValidator(
            agent_versions=config.agent_version_snapshots,
            tool_versions=config.tool_version_snapshots,
            workspace_scope=config.workspace_scope,
            frozen_task=config.frozen_task,
            planner_policy=config.planner_policy,
            budget_ceilings=config.budget_ceilings,
        )
        for proposal in config.plan_proposals:
            report = validator.validate(proposal)
            results.append(report.to_dict())
        return tuple(results)

    def _verify_source_boundaries(self, config: PlannerContractConfig) -> str:
        migration_head = discover_migration_head(
            self._repo_root, "backend/src/omnibase/migrations/versions"
        )
        for relative in config.forbidden_source_paths:
            candidate = self._repo_root / relative
            if _lexists(candidate):
                raise PlannerContractError(
                    f"forbidden source path exists: {relative}"
                )
        openapi_path = _safe_repo_file(self._repo_root, config.openapi_snapshot_path)
        content = openapi_path.read_bytes()
        if _sha256_bytes(content) != config.openapi_snapshot_sha256:
            raise PlannerContractError("openapi snapshot SHA-256 drifted")
        payload = json.loads(content.decode("utf-8"))
        paths = payload.get("paths")
        if isinstance(paths, dict):
            for path_name in paths:
                if "planner" in path_name.lower() or "plan" in path_name.lower():
                    if "execution" in path_name.lower() or "run" in path_name.lower():
                        raise PlannerContractError(
                            f"openapi snapshot exposes planner execution endpoint: {path_name}"
                        )
        return migration_head

    def _verify_sealed_files(self, config: PlannerContractConfig) -> None:
        for name, relative, digest in config.sealed_contracts:
            path = _safe_repo_file(self._repo_root, relative)
            if _sha256_bytes(path.read_bytes()) != digest:
                raise PlannerContractError(f"sealed contract drifted: {name}")
        for _, relative, digest in (
            config.p34_7,
            config.p5_0,
            config.p5_1,
            config.p5_2a,
        ):
            path = _safe_repo_file(self._repo_root, relative)
            if _sha256_bytes(path.read_bytes()) != digest:
                raise PlannerContractError(f"sealed reference drifted: {relative}")


def load_planner_contract_config(path: Path) -> PlannerContractConfig:
    metadata = path.lstat()
    is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
    if stat.S_ISLNK(metadata.st_mode) or is_reparse or not stat.S_ISREG(metadata.st_mode):
        raise PlannerContractError(
            "P5.3A contract configuration must be a regular non-link file"
        )

    def _reject_constant(value: str) -> None:
        raise PlannerContractError(f"contract contains a non-finite number: {value}")

    payload = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    return PlannerContractConfig.from_mapping(payload)


__all__ = [
    "AgentVersionSnapshot",
    "DataAccessMode",
    "EffectClass",
    "ExecutionRequirement",
    "FrozenTaskSnapshot",
    "InputBindingKind",
    "IsolationClass",
    "NetworkPolicy",
    "NodeKind",
    "PlanApprovalRequirement",
    "PlanInputBinding",
    "PlannerCeilings",
    "PlannerContractConfig",
    "PlannerContractError",
    "PlannerContractGate",
    "PlannerContractReport",
    "PlannerPolicy",
    "PlanNodeBudget",
    "PlanNodeProposal",
    "PlanOutputContract",
    "PlanProposal",
    "PlanProposalValidator",
    "PlanRetryPolicy",
    "PlanValidationFinding",
    "PlanValidationReport",
    "RetryPolicy",
    "RiskLevel",
    "ToolVersionSnapshot",
    "ValidatedPlan",
    "WorkspaceScopeSnapshot",
    "load_planner_contract_config",
]
