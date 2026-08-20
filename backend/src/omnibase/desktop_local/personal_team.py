"""Host validation and persistence for personal parent-directed team runs."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any

from omnibase.desktop_local.database import utc_now_text
from omnibase.desktop_local.providers import DesktopApiError
from omnibase.desktop_local.repository import append_audit_event

PARENT_ROLE_ID = "parent"
SPECIALIST_ROLE_IDS: frozenset[str] = frozenset(
    {
        "product",
        "ux",
        "frontend",
        "backend",
        "data",
        "security",
        "qa",
        "operations",
        "docs",
    }
)
EMPLOYEE_ROLE_IDS: frozenset[str] = SPECIALIST_ROLE_IDS | {PARENT_ROLE_ID}

ROLE_CATALOG: tuple[dict[str, str], ...] = (
    {
        "id": "parent",
        "display_name": "父 Agent",
        "responsibility": "项目负责人",
        "default_state": "active",
        "may_join_team": "false",
    },
    {
        "id": "product",
        "display_name": "产品经理",
        "responsibility": "产品目标与范围",
        "default_state": "dormant",
        "may_join_team": "true",
    },
    {
        "id": "ux",
        "display_name": "UI/UX 设计师",
        "responsibility": "交互与视觉",
        "default_state": "dormant",
        "may_join_team": "true",
    },
    {
        "id": "frontend",
        "display_name": "前端工程师",
        "responsibility": "桌面与前端实现",
        "default_state": "dormant",
        "may_join_team": "true",
    },
    {
        "id": "backend",
        "display_name": "后端工程师",
        "responsibility": "SQLite、IPC 与数据模型",
        "default_state": "dormant",
        "may_join_team": "true",
    },
    {
        "id": "data",
        "display_name": "数据工程师",
        "responsibility": "数据与检索",
        "default_state": "dormant",
        "may_join_team": "true",
    },
    {
        "id": "security",
        "display_name": "安全架构师",
        "responsibility": "身份、取消与权限边界",
        "default_state": "dormant",
        "may_join_team": "true",
    },
    {
        "id": "qa",
        "display_name": "测试工程师",
        "responsibility": "攻击矩阵与回归",
        "default_state": "dormant",
        "may_join_team": "true",
    },
    {
        "id": "operations",
        "display_name": "运维/发布工程师",
        "responsibility": "发布与运行稳定性",
        "default_state": "dormant",
        "may_join_team": "true",
    },
    {
        "id": "docs",
        "display_name": "文档工程师",
        "responsibility": "产品与维护者文档",
        "default_state": "dormant",
        "may_join_team": "true",
    },
)

BUDGET_BOUNDS: dict[str, tuple[int, int]] = {
    "maximumProviderCalls": (1, 128),
    "maximumWallTimeMs": (1_000, 3_600_000),
    "maximumConcurrentCalls": (1, 9),
    "maximumInputCharacters": (1, 131_072),
    "maximumOutputCharacters": (1, 131_072),
}

FORBIDDEN_STRUCTURAL_KEYS: frozenset[str] = frozenset(
    {
        "tools",
        "tool",
        "tool_choice",
        "toolChoice",
        "mcp",
        "shell",
        "sandbox",
        "side_effects",
        "sideEffects",
        "functions",
        "function_call",
        "functionCall",
        "plugins",
        "skills",
        "dispatch",
        "direct_launch",
        "directLaunch",
        "launch_employee",
        "launchEmployee",
        "api_key",
        "apiKey",
        "ciphertext",
        "nonce",
        "dpapi",
        "vault",
        "vault_handle",
        "vaultHandle",
        "encrypted_secret_blob",
        "encryptedSecretBlob",
        "credential_reference",
        "credentialReference",
        "secret",
        "password",
    }
)
FORBIDDEN_ROLE_CONFIG_COLUMNS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "secret",
        "ciphertext",
        "nonce",
        "dpapi",
        "vault",
        "encrypted_secret_blob",
        "credential_reference",
        "password",
    }
)

_WORKSPACE_ID_PATTERN = re.compile(r"workspace_[0-9a-f]{32}\Z")
_CONVERSATION_ID_PATTERN = re.compile(r"conversation_[0-9a-f]{32}\Z")
_PROVIDER_ID_PATTERN = re.compile(r"provider_[0-9a-f]{32}\Z")
_TEAM_RUN_ID_PATTERN = re.compile(r"teamrun_[0-9a-f]{32}\Z")
_ASSIGNMENT_ID_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,127}\Z")
_WAVE_ID_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,127}\Z")
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_WORKSPACE_LOCATOR_PATTERN = re.compile(r"workspace_[0-9a-f]{32}")
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"api[_-]?key", re.IGNORECASE),
    re.compile(r"\bsecret\b", re.IGNORECASE),
    re.compile(r"\bbearer\s+\S+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9]{8,}"),
    re.compile(r"ciphertext", re.IGNORECASE),
    re.compile(r"\bnonce\b", re.IGNORECASE),
    re.compile(r"dpapi", re.IGNORECASE),
    re.compile(r"vault[_-]?handle", re.IGNORECASE),
    re.compile(r"encrypted_secret_blob", re.IGNORECASE),
    re.compile(r"native[_-]?control[_-]?token", re.IGNORECASE),
)
_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"\\\\[^\\\s]+\\"),
    re.compile(r"/(?:etc|home|root|usr|var|tmp)/"),
    re.compile(r"file://", re.IGNORECASE),
)
_LOCATOR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bpostgres(?:ql)?://", re.IGNORECASE),
    re.compile(r"\bmongodb://", re.IGNORECASE),
)
_ALLOWED_GEARS = frozenset({"economy", "standard", "deep", "audit"})
_ALLOWED_DEPTHS = frozenset({"disabled", "low", "medium", "high"})
_MAX_TEAM_RUNS = 64
_MAX_WAVES = 16
_MAX_ASSIGNMENTS = 128


@dataclass(frozen=True, slots=True)
class TeamValidationResult:
    ok: bool
    error_code: str | None
    normalized: dict[str, Any] | None = None

    @property
    def code(self) -> str:
        if self.ok:
            return "ok"
        return self.error_code or "desktop_team_proposal_invalid"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_owner(connection: sqlite3.Connection) -> sqlite3.Row:
    owner = connection.execute("SELECT id FROM owner WHERE singleton_key = 1").fetchone()
    if owner is None:
        raise DesktopApiError(409, "desktop_owner_not_initialized")
    return owner


def _require_workspace(
    connection: sqlite3.Connection,
    owner_id: str,
    workspace_id: str,
    *,
    active: bool,
) -> sqlite3.Row:
    if not _WORKSPACE_ID_PATTERN.fullmatch(workspace_id):
        raise DesktopApiError(404, "desktop_workspace_not_found")
    row = connection.execute(
        "SELECT id, owner_id, name, state FROM workspace WHERE id = ? AND owner_id = ?",
        (workspace_id, owner_id),
    ).fetchone()
    if row is None:
        raise DesktopApiError(404, "desktop_workspace_not_found")
    if active and str(row["state"]) != "active":
        raise DesktopApiError(409, "desktop_workspace_archived")
    return row


def _bounded_text(value: object, maximum: int) -> str | None:
    if not isinstance(value, str) or _CONTROL_CHARACTER_PATTERN.search(value):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        return None
    return normalized


def _walk_forbidden_keys(value: object) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and key in FORBIDDEN_STRUCTURAL_KEYS:
                if key in {"dispatch", "direct_launch", "directLaunch", "launch_employee", "launchEmployee"}:
                    return "desktop_team_employee_direct_launch"
                if key in {
                    "tools",
                    "tool",
                    "tool_choice",
                    "toolChoice",
                    "mcp",
                    "shell",
                    "sandbox",
                    "side_effects",
                    "sideEffects",
                    "functions",
                    "function_call",
                    "functionCall",
                    "plugins",
                    "skills",
                }:
                    return "desktop_team_tools_forbidden"
                return "desktop_team_secret_or_path_forbidden"
            nested = _walk_forbidden_keys(child)
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for child in value:
            nested = _walk_forbidden_keys(child)
            if nested is not None:
                return nested
    return None


def _scan_sensitive_text(value: str, *, workspace_id: str | None) -> str | None:
    if any(pattern.search(value) for pattern in _SECRET_PATTERNS):
        return "desktop_team_secret_or_path_forbidden"
    if any(pattern.search(value) for pattern in _PATH_PATTERNS):
        return "desktop_team_secret_or_path_forbidden"
    if any(pattern.search(value) for pattern in _LOCATOR_PATTERNS):
        return "desktop_team_secret_or_path_forbidden"
    if workspace_id is not None:
        for match in _WORKSPACE_LOCATOR_PATTERN.findall(value):
            if match != workspace_id:
                return "desktop_team_cross_workspace"
    return None


def _walk_sensitive_text(value: object, *, workspace_id: str | None) -> str | None:
    if isinstance(value, str):
        return _scan_sensitive_text(value, workspace_id=workspace_id)
    if isinstance(value, dict):
        for child in value.values():
            found = _walk_sensitive_text(child, workspace_id=workspace_id)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _walk_sensitive_text(child, workspace_id=workspace_id)
            if found is not None:
                return found
    return None


def _has_assignment_cycle(graph: dict[str, tuple[str, ...]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visited:
            return False
        if node in visiting:
            return True
        visiting.add(node)
        for dependency in graph.get(node, ()):
            if visit(dependency):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


def validate_team_run_budget(budget: object) -> TeamValidationResult:
    if not isinstance(budget, dict):
        return TeamValidationResult(False, "desktop_team_infinite_budget")
    expected = tuple(BUDGET_BOUNDS)
    actual = tuple(sorted(budget))
    if actual != tuple(sorted(expected)):
        return TeamValidationResult(False, "desktop_team_infinite_budget")
    normalized: dict[str, int] = {}
    for key, (minimum, maximum) in BUDGET_BOUNDS.items():
        value = budget[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
            return TeamValidationResult(False, "desktop_team_infinite_budget")
        normalized[key] = value
    return TeamValidationResult(True, None, normalized)


def _validate_assignment(
    assignment: object,
    *,
    budget: dict[str, int],
    allowed_roles: frozenset[str],
    workspace_id: str | None,
) -> TeamValidationResult:
    if not isinstance(assignment, dict):
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    expected = {
        "assignmentId",
        "employeeRoleId",
        "objective",
        "dependsOnAssignmentIds",
        "expectedOutput",
        "contextRequirements",
    }
    if set(assignment) != expected:
        extra = set(assignment) - expected
        if extra & {"dispatch", "directLaunch", "direct_launch", "launchEmployee"}:
            return TeamValidationResult(False, "desktop_team_employee_direct_launch")
        if extra & FORBIDDEN_STRUCTURAL_KEYS:
            forbidden = extra & FORBIDDEN_STRUCTURAL_KEYS
            if forbidden & {"tools", "tool", "mcp", "shell", "sandbox", "skills"}:
                return TeamValidationResult(False, "desktop_team_tools_forbidden")
            return TeamValidationResult(False, "desktop_team_secret_or_path_forbidden")
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    assignment_id = assignment["assignmentId"]
    role_id = assignment["employeeRoleId"]
    if not isinstance(assignment_id, str) or not _ASSIGNMENT_ID_PATTERN.fullmatch(assignment_id):
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    if role_id == PARENT_ROLE_ID:
        return TeamValidationResult(False, "desktop_team_parent_not_specialist")
    if not isinstance(role_id, str) or role_id not in SPECIALIST_ROLE_IDS:
        return TeamValidationResult(False, "desktop_team_unknown_role")
    if role_id not in allowed_roles:
        return TeamValidationResult(False, "desktop_team_unknown_role")
    objective = _bounded_text(assignment["objective"], budget["maximumInputCharacters"])
    expected_output = _bounded_text(
        assignment["expectedOutput"], budget["maximumOutputCharacters"]
    )
    if objective is None:
        return TeamValidationResult(False, "desktop_team_input_budget_exceeded")
    if expected_output is None:
        return TeamValidationResult(False, "desktop_team_output_budget_exceeded")
    depends = assignment["dependsOnAssignmentIds"]
    context = assignment["contextRequirements"]
    if not isinstance(depends, list) or not isinstance(context, list):
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    if len(depends) > _MAX_ASSIGNMENTS or len(context) > _MAX_ASSIGNMENTS:
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    if not all(isinstance(item, str) and _ASSIGNMENT_ID_PATTERN.fullmatch(item) for item in depends):
        return TeamValidationResult(False, "desktop_team_missing_dependency")
    if not all(isinstance(item, str) and len(item) <= 256 for item in context):
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    payload = {
        "assignmentId": assignment_id,
        "employeeRoleId": role_id,
        "objective": objective,
        "dependsOnAssignmentIds": tuple(depends),
        "expectedOutput": expected_output,
        "contextRequirements": tuple(context),
    }
    sensitive = _walk_sensitive_text(payload, workspace_id=workspace_id)
    if sensitive is not None:
        return TeamValidationResult(False, sensitive)
    return TeamValidationResult(True, None, payload)


def _validate_wave(
    wave: object,
    *,
    budget: dict[str, int],
    allowed_roles: frozenset[str],
    workspace_id: str | None,
) -> TeamValidationResult:
    if not isinstance(wave, dict):
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    if set(wave) != {"waveId", "execution", "assignments"}:
        extra = set(wave) - {"waveId", "execution", "assignments"}
        if extra & FORBIDDEN_STRUCTURAL_KEYS:
            return TeamValidationResult(False, "desktop_team_tools_forbidden")
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    wave_id = wave["waveId"]
    execution = wave["execution"]
    assignments = wave["assignments"]
    if not isinstance(wave_id, str) or not _WAVE_ID_PATTERN.fullmatch(wave_id):
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    if execution not in {"serial", "parallel"}:
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    if not isinstance(assignments, list) or not assignments or len(assignments) > _MAX_ASSIGNMENTS:
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    if execution == "parallel" and len(assignments) > budget["maximumConcurrentCalls"]:
        # Host may serialize later; oversized parallel is not a hard reject.
        pass
    normalized_assignments: list[dict[str, Any]] = []
    for assignment in assignments:
        result = _validate_assignment(
            assignment,
            budget=budget,
            allowed_roles=allowed_roles,
            workspace_id=workspace_id,
        )
        if not result.ok or result.normalized is None:
            return result
        normalized_assignments.append(result.normalized)
    return TeamValidationResult(
        True,
        None,
        {
            "waveId": wave_id,
            "execution": execution,
            "assignments": normalized_assignments,
        },
    )


def _validate_delegate_waves(
    waves: object,
    *,
    budget: dict[str, int],
    allowed_roles: frozenset[str],
    workspace_id: str | None,
) -> TeamValidationResult:
    if not isinstance(waves, list) or not waves or len(waves) > _MAX_WAVES:
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    normalized_waves: list[dict[str, Any]] = []
    assignment_ids: list[str] = []
    graph: dict[str, tuple[str, ...]] = {}
    known: set[str] = set()
    for wave in waves:
        result = _validate_wave(
            wave,
            budget=budget,
            allowed_roles=allowed_roles,
            workspace_id=workspace_id,
        )
        if not result.ok or result.normalized is None:
            return result
        wave_ids = [item["assignmentId"] for item in result.normalized["assignments"]]
        if len(set(wave_ids)) != len(wave_ids) or any(item in known for item in wave_ids):
            return TeamValidationResult(False, "desktop_team_duplicate_assignment_id")
        for assignment in result.normalized["assignments"]:
            assignment_id = str(assignment["assignmentId"])
            depends = tuple(assignment["dependsOnAssignmentIds"])
            missing = [item for item in depends if item not in known and item not in wave_ids]
            if missing:
                return TeamValidationResult(False, "desktop_team_missing_dependency")
            graph[assignment_id] = depends
            assignment_ids.append(assignment_id)
            known.add(assignment_id)
        normalized_waves.append(result.normalized)
    if _has_assignment_cycle(graph):
        return TeamValidationResult(False, "desktop_team_dependency_cycle")
    if len(assignment_ids) > budget["maximumProviderCalls"]:
        return TeamValidationResult(False, "desktop_team_call_budget_exceeded")
    return TeamValidationResult(True, None, {"waves": normalized_waves, "assignmentIds": assignment_ids})


def validate_parent_team_decision(
    proposal: object,
    *,
    budget: dict[str, int],
    allowed_roles: frozenset[str],
    workspace_id: str | None,
) -> TeamValidationResult:
    forbidden = _walk_forbidden_keys(proposal)
    if forbidden is not None:
        return TeamValidationResult(False, forbidden)
    sensitive = _walk_sensitive_text(proposal, workspace_id=workspace_id)
    if sensitive is not None:
        return TeamValidationResult(False, sensitive)
    if not isinstance(proposal, dict) or "decision" not in proposal:
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    source_role = proposal.get("sourceRoleId")
    if isinstance(source_role, str) and source_role in SPECIALIST_ROLE_IDS:
        return TeamValidationResult(False, "desktop_team_employee_direct_launch")
    decision = proposal["decision"]
    if decision == "answer_directly":
        if set(proposal) != {"decision", "answer", "reason"}:
            return TeamValidationResult(False, "desktop_team_proposal_invalid")
        answer = _bounded_text(proposal["answer"], budget["maximumOutputCharacters"])
        reason = _bounded_text(proposal["reason"], budget["maximumInputCharacters"])
        if answer is None:
            return TeamValidationResult(False, "desktop_team_output_budget_exceeded")
        if reason is None:
            return TeamValidationResult(False, "desktop_team_input_budget_exceeded")
        return TeamValidationResult(
            True,
            None,
            {"decision": "answer_directly", "answer": answer, "reason": reason},
        )
    if decision != "delegate":
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    if set(proposal) != {"decision", "objective", "waves", "finalSynthesisRequired"}:
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    if proposal["finalSynthesisRequired"] is not True:
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    objective = _bounded_text(proposal["objective"], budget["maximumInputCharacters"])
    if objective is None:
        return TeamValidationResult(False, "desktop_team_input_budget_exceeded")
    waves = _validate_delegate_waves(
        proposal["waves"],
        budget=budget,
        allowed_roles=allowed_roles,
        workspace_id=workspace_id,
    )
    if not waves.ok or waves.normalized is None:
        return waves
    return TeamValidationResult(
        True,
        None,
        {
            "decision": "delegate",
            "objective": objective,
            "waves": waves.normalized["waves"],
            "finalSynthesisRequired": True,
        },
    )


def validate_parent_replan_decision(
    proposal: object,
    *,
    budget: dict[str, int],
    allowed_roles: frozenset[str],
    workspace_id: str | None,
    known_assignment_ids: frozenset[str],
) -> TeamValidationResult:
    forbidden = _walk_forbidden_keys(proposal)
    if forbidden is not None:
        return TeamValidationResult(False, forbidden)
    sensitive = _walk_sensitive_text(proposal, workspace_id=workspace_id)
    if sensitive is not None:
        return TeamValidationResult(False, sensitive)
    if not isinstance(proposal, dict) or "decision" not in proposal:
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    decision = proposal["decision"]
    if decision in {"finish", "cannot_complete"}:
        if set(proposal) != {"decision", "reason"}:
            return TeamValidationResult(False, "desktop_team_proposal_invalid")
        reason = _bounded_text(proposal["reason"], budget["maximumInputCharacters"])
        if reason is None:
            return TeamValidationResult(False, "desktop_team_input_budget_exceeded")
        return TeamValidationResult(True, None, {"decision": decision, "reason": reason})
    if decision == "continue":
        if set(proposal) != {"decision", "nextWave"}:
            return TeamValidationResult(False, "desktop_team_proposal_invalid")
        wave = _validate_wave(
            proposal["nextWave"],
            budget=budget,
            allowed_roles=allowed_roles,
            workspace_id=workspace_id,
        )
        if not wave.ok or wave.normalized is None:
            return wave
        assignment_ids = [item["assignmentId"] for item in wave.normalized["assignments"]]
        if any(item in known_assignment_ids for item in assignment_ids) or len(set(assignment_ids)) != len(
            assignment_ids
        ):
            return TeamValidationResult(False, "desktop_team_duplicate_assignment_id")
        graph = {
            item["assignmentId"]: tuple(item["dependsOnAssignmentIds"])
            for item in wave.normalized["assignments"]
        }
        known = set(known_assignment_ids) | set(assignment_ids)
        for assignment_id, depends in graph.items():
            if any(item not in known for item in depends):
                return TeamValidationResult(False, "desktop_team_missing_dependency")
            if assignment_id in depends:
                return TeamValidationResult(False, "desktop_team_dependency_cycle")
        if _has_assignment_cycle({**{key: () for key in known_assignment_ids}, **graph}):
            return TeamValidationResult(False, "desktop_team_dependency_cycle")
        return TeamValidationResult(True, None, {"decision": "continue", "nextWave": wave.normalized})
    if decision != "request_followup":
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    if set(proposal) != {"decision", "assignments"}:
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    assignments = proposal["assignments"]
    if not isinstance(assignments, list) or not assignments:
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for assignment in assignments:
        result = _validate_assignment(
            assignment,
            budget=budget,
            allowed_roles=allowed_roles,
            workspace_id=workspace_id,
        )
        if not result.ok or result.normalized is None:
            return result
        assignment_id = str(result.normalized["assignmentId"])
        if assignment_id in seen or assignment_id in known_assignment_ids:
            return TeamValidationResult(False, "desktop_team_duplicate_assignment_id")
        depends = tuple(result.normalized["dependsOnAssignmentIds"])
        if any(item not in known_assignment_ids and item not in seen for item in depends):
            return TeamValidationResult(False, "desktop_team_missing_dependency")
        seen.add(assignment_id)
        normalized.append(result.normalized)
    return TeamValidationResult(True, None, {"decision": "request_followup", "assignments": normalized})


def validate_employee_team_report(
    report: object,
    *,
    budget: dict[str, int],
    allowed_roles: frozenset[str],
    workspace_id: str | None,
) -> TeamValidationResult:
    forbidden = _walk_forbidden_keys(report)
    if forbidden is not None:
        return TeamValidationResult(False, forbidden)
    if not isinstance(report, dict):
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    expected = {"assignmentId", "employeeRoleId", "status", "report", "collaborationRequests"}
    if set(report) != expected:
        extra = set(report) - expected
        if extra & {"dispatch", "directLaunch", "direct_launch", "launchEmployee"}:
            return TeamValidationResult(False, "desktop_team_employee_direct_launch")
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    if report["status"] not in {"completed", "needs_collaboration", "blocked"}:
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    if report["employeeRoleId"] not in allowed_roles:
        return TeamValidationResult(False, "desktop_team_unknown_role")
    if report["employeeRoleId"] == PARENT_ROLE_ID:
        return TeamValidationResult(False, "desktop_team_parent_not_specialist")
    body = _bounded_text(report["report"], budget["maximumOutputCharacters"])
    if body is None:
        return TeamValidationResult(False, "desktop_team_output_budget_exceeded")
    requests = report["collaborationRequests"]
    if not isinstance(requests, list) or len(requests) > 9:
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    normalized_requests: list[dict[str, str]] = []
    for item in requests:
        result = validate_collaboration_request(
            item,
            budget=budget,
            allowed_roles=allowed_roles,
            workspace_id=workspace_id,
        )
        if not result.ok or result.normalized is None:
            return result
        normalized_requests.append(
            {
                "targetRoleId": str(result.normalized["targetRoleId"]),
                "question": str(result.normalized["question"]),
                "reason": str(result.normalized["reason"]),
            }
        )
    payload = {
        "assignmentId": report["assignmentId"],
        "employeeRoleId": report["employeeRoleId"],
        "status": report["status"],
        "report": body,
        "collaborationRequests": normalized_requests,
    }
    sensitive = _walk_sensitive_text(payload, workspace_id=workspace_id)
    if sensitive is not None:
        return TeamValidationResult(False, sensitive)
    return TeamValidationResult(True, None, payload)


def validate_collaboration_request(
    payload: object,
    *,
    budget: dict[str, int],
    allowed_roles: frozenset[str],
    workspace_id: str | None,
) -> TeamValidationResult:
    forbidden = _walk_forbidden_keys(payload)
    if forbidden is not None:
        return TeamValidationResult(False, forbidden)
    if not isinstance(payload, dict):
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    allowed_keys = {"targetRoleId", "question", "reason"}
    optional = {"fromAssignmentId", "fromEmployeeRoleId"}
    extra = set(payload) - allowed_keys - optional
    if extra:
        if extra & {"dispatch", "directLaunch", "direct_launch", "launchEmployee"}:
            return TeamValidationResult(False, "desktop_team_employee_direct_launch")
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    if not allowed_keys.issubset(payload):
        return TeamValidationResult(False, "desktop_team_proposal_invalid")
    target = payload["targetRoleId"]
    if target == PARENT_ROLE_ID:
        return TeamValidationResult(False, "desktop_team_parent_not_specialist")
    if target not in SPECIALIST_ROLE_IDS or target not in allowed_roles:
        return TeamValidationResult(False, "desktop_team_unknown_role")
    question = _bounded_text(payload["question"], budget["maximumInputCharacters"])
    reason = _bounded_text(payload["reason"], budget["maximumInputCharacters"])
    if question is None or reason is None:
        return TeamValidationResult(False, "desktop_team_input_budget_exceeded")
    sensitive = _walk_sensitive_text(
        {"question": question, "reason": reason}, workspace_id=workspace_id
    )
    if sensitive is not None:
        return TeamValidationResult(False, sensitive)
    normalized = {
        "targetRoleId": target,
        "question": question,
        "reason": reason,
        "fromAssignmentId": payload.get("fromAssignmentId"),
        "fromEmployeeRoleId": payload.get("fromEmployeeRoleId"),
    }
    return TeamValidationResult(True, None, normalized)


def _budget_from_run(row: sqlite3.Row) -> dict[str, int]:
    return {
        "maximumProviderCalls": int(row["maximum_provider_calls"]),
        "maximumWallTimeMs": int(row["maximum_wall_time_ms"]),
        "maximumConcurrentCalls": int(row["maximum_concurrent_calls"]),
        "maximumInputCharacters": int(row["maximum_input_characters"]),
        "maximumOutputCharacters": int(row["maximum_output_characters"]),
    }


def _allowed_roles_from_run(row: sqlite3.Row) -> frozenset[str]:
    raw = json.loads(str(row["allowed_specialist_role_ids"]))
    return frozenset(raw)


def _role_config_payload(role: dict[str, str], config: sqlite3.Row | None, resolved: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": role["id"],
        "display_name": role["display_name"],
        "responsibility": role["responsibility"],
        "default_state": role["default_state"],
        "may_join_team": role["may_join_team"] == "true",
        "provider_id": None if config is None else config["provider_id"],
        "model_name_override": None if config is None else config["model_name_override"],
        "gear": resolved["gear"],
        "thinking_depth": resolved["thinking_depth"],
        "row_version": 1 if config is None else int(config["row_version"]),
        "verification_state": "unverified" if config is None else str(config["verification_state"]),
        "verified_actual_model": None if config is None else config["verified_actual_model"],
        "inherited_provider": bool(resolved["inherited_provider"]),
        "resolved_provider_id": resolved["provider_id"],
        "resolved_model_name": resolved["model_name"],
        "secret_fingerprint": resolved["secret_fingerprint"],
        "has_secret": resolved["secret_fingerprint"] is not None,
    }


def _load_provider(
    connection: sqlite3.Connection, owner_id: str, provider_id: str | None
) -> sqlite3.Row | None:
    if provider_id is None:
        return connection.execute(
            "SELECT id, model_name, gear, thinking_depth, secret_fingerprint, base_url "
            "FROM provider WHERE owner_id = ? AND is_enabled = 1 AND is_default = 1",
            (owner_id,),
        ).fetchone()
    if not _PROVIDER_ID_PATTERN.fullmatch(provider_id):
        return None
    return connection.execute(
        "SELECT id, model_name, gear, thinking_depth, secret_fingerprint, base_url "
        "FROM provider WHERE id = ? AND owner_id = ?",
        (provider_id, owner_id),
    ).fetchone()


def resolve_role_provider(
    connection: sqlite3.Connection,
    owner_id: str,
    config: sqlite3.Row | None,
) -> dict[str, Any]:
    explicit_id = None if config is None else config["provider_id"]
    inherited = explicit_id is None
    provider = _load_provider(connection, owner_id, None if inherited else str(explicit_id))
    if provider is None:
        return {
            "provider_id": None,
            "model_name": None if config is None else config["model_name_override"],
            "gear": "standard" if config is None else str(config["gear"]),
            "thinking_depth": "disabled" if config is None else str(config["thinking_depth"]),
            "secret_fingerprint": None,
            "base_url": None,
            "inherited_provider": inherited,
        }
    override = None if config is None else config["model_name_override"]
    return {
        "provider_id": str(provider["id"]),
        "model_name": str(override or provider["model_name"]),
        "gear": str(provider["gear"] if config is None else config["gear"]),
        "thinking_depth": str(
            provider["thinking_depth"] if config is None else config["thinking_depth"]
        ),
        "secret_fingerprint": str(provider["secret_fingerprint"]),
        "base_url": str(provider["base_url"]),
        "inherited_provider": inherited,
    }


def list_agent_roles(connection: sqlite3.Connection, workspace_id: str) -> dict[str, object]:
    owner = _require_owner(connection)
    _require_workspace(connection, str(owner["id"]), workspace_id, active=False)
    configs = {
        str(row["employee_role_id"]): row
        for row in connection.execute(
            "SELECT * FROM workspace_agent_role_config "
            "WHERE owner_id = ? AND workspace_id = ?",
            (owner["id"], workspace_id),
        ).fetchall()
    }
    items = []
    for role in ROLE_CATALOG:
        config = configs.get(role["id"])
        resolved = resolve_role_provider(connection, str(owner["id"]), config)
        items.append(_role_config_payload(role, config, resolved))
    return {"items": items}


def get_agent_role(
    connection: sqlite3.Connection, workspace_id: str, role_id: str
) -> dict[str, object]:
    if role_id not in EMPLOYEE_ROLE_IDS:
        raise DesktopApiError(404, "desktop_agent_role_not_found")
    listed = list_agent_roles(connection, workspace_id)
    for item in listed["items"]:  # type: ignore[union-attr]
        if item["id"] == role_id:
            return {"role": item}
    raise DesktopApiError(404, "desktop_agent_role_not_found")


def update_agent_role(
    connection: sqlite3.Connection,
    workspace_id: str,
    role_id: str,
    payload: dict[str, Any],
) -> dict[str, object]:
    if role_id not in EMPLOYEE_ROLE_IDS:
        raise DesktopApiError(404, "desktop_agent_role_not_found")
    lowered = {str(key).lower() for key in payload}
    if lowered & FORBIDDEN_ROLE_CONFIG_COLUMNS:
        raise DesktopApiError(400, "desktop_role_config_secret_forbidden")
    provider_id = payload.get("provider_id")
    model_name_override = payload.get("model_name_override")
    gear = payload.get("gear")
    thinking_depth = payload.get("thinking_depth")
    if gear not in _ALLOWED_GEARS or thinking_depth not in _ALLOWED_DEPTHS:
        raise DesktopApiError(400, "desktop_native_input_invalid")
    if provider_id is not None and (
        not isinstance(provider_id, str) or not _PROVIDER_ID_PATTERN.fullmatch(provider_id)
    ):
        raise DesktopApiError(400, "desktop_native_input_invalid")
    if model_name_override is not None:
        normalized_model = _bounded_text(model_name_override, 256)
        if normalized_model is None:
            raise DesktopApiError(400, "desktop_native_input_invalid")
        model_name_override = normalized_model
    owner = _require_owner(connection)
    _require_workspace(connection, str(owner["id"]), workspace_id, active=True)
    if provider_id is not None and _load_provider(connection, str(owner["id"]), provider_id) is None:
        raise DesktopApiError(404, "desktop_provider_not_found")
    now = utc_now_text()
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT id, row_version FROM workspace_agent_role_config "
            "WHERE workspace_id = ? AND employee_role_id = ?",
            (workspace_id, role_id),
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO workspace_agent_role_config ("
                "id, owner_id, workspace_id, employee_role_id, provider_id, "
                "model_name_override, gear, thinking_depth, row_version, "
                "verification_state, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'unverified', ?, ?)",
                (
                    _new_id("rolecfg"),
                    owner["id"],
                    workspace_id,
                    role_id,
                    provider_id,
                    model_name_override,
                    gear,
                    thinking_depth,
                    now,
                    now,
                ),
            )
        else:
            connection.execute(
                "UPDATE workspace_agent_role_config SET provider_id = ?, "
                "model_name_override = ?, gear = ?, thinking_depth = ?, "
                "row_version = row_version + 1, verification_state = 'unverified', "
                "verified_actual_model = NULL, verification_digest = NULL, updated_at = ? "
                "WHERE id = ?",
                (
                    provider_id,
                    model_name_override,
                    gear,
                    thinking_depth,
                    now,
                    existing["id"],
                ),
            )
        append_audit_event(
            connection,
            event_id=_new_id("event"),
            owner_id=str(owner["id"]),
            workspace_id=workspace_id,
            event_type="agent_role_config_updated",
            payload={"role_id": role_id},
        )
        connection.execute("COMMIT")
    except DesktopApiError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopApiError(503, "desktop_role_config_update_failed") from None
    return get_agent_role(connection, workspace_id, role_id)


def test_agent_role(
    connection: sqlite3.Connection, workspace_id: str, role_id: str
) -> dict[str, object]:
    if role_id not in EMPLOYEE_ROLE_IDS:
        raise DesktopApiError(404, "desktop_agent_role_not_found")
    owner = _require_owner(connection)
    _require_workspace(connection, str(owner["id"]), workspace_id, active=True)
    config = connection.execute(
        "SELECT * FROM workspace_agent_role_config "
        "WHERE workspace_id = ? AND employee_role_id = ?",
        (workspace_id, role_id),
    ).fetchone()
    resolved = resolve_role_provider(connection, str(owner["id"]), config)
    if resolved["provider_id"] is None or resolved["secret_fingerprint"] is None:
        raise DesktopApiError(409, "desktop_role_provider_unresolved")
    row_version = 1 if config is None else int(config["row_version"])
    digest = _sha256_text(
        "|".join(
            [
                workspace_id,
                role_id,
                str(row_version),
                str(resolved["provider_id"]),
                str(resolved["secret_fingerprint"]),
                str(resolved["base_url"]),
                str(resolved["model_name"]),
            ]
        )
    )
    now = utc_now_text()
    try:
        connection.execute("BEGIN IMMEDIATE")
        if config is None:
            connection.execute(
                "INSERT INTO workspace_agent_role_config ("
                "id, owner_id, workspace_id, employee_role_id, provider_id, "
                "model_name_override, gear, thinking_depth, row_version, "
                "verification_state, verification_digest, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, 1, 'binding_recorded', ?, ?, ?)",
                (
                    _new_id("rolecfg"),
                    owner["id"],
                    workspace_id,
                    role_id,
                    resolved["gear"],
                    resolved["thinking_depth"],
                    digest,
                    now,
                    now,
                ),
            )
        else:
            connection.execute(
                "UPDATE workspace_agent_role_config SET verification_state = 'binding_recorded', "
                "verification_digest = ?, verified_actual_model = NULL, updated_at = ? "
                "WHERE id = ?",
                (digest, now, config["id"]),
            )
        connection.execute("COMMIT")
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopApiError(503, "desktop_role_config_test_failed") from None
    return {
        "ok": True,
        "role_id": role_id,
        "workspace_id": workspace_id,
        "provider_id": resolved["provider_id"],
        "inherited_provider": resolved["inherited_provider"],
        "requested_model": resolved["model_name"],
        "secret_fingerprint": resolved["secret_fingerprint"],
        "verification_digest": digest,
        "identity_proven": False,
    }


def _team_run_payload(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "workspace_id": str(row["workspace_id"]),
        "conversation_id": str(row["conversation_id"]),
        "mode": str(row["mode"]),
        "state": str(row["state"]),
        "staffing_authority": str(row["staffing_authority"]),
        "current_plan_revision_id": row["current_plan_revision_id"],
        "current_wave_id": row["current_wave_id"],
        "dispatched_participant_count": row["dispatched_participant_count"],
        "maximum_provider_calls": int(row["maximum_provider_calls"]),
        "maximum_wall_time_ms": int(row["maximum_wall_time_ms"]),
        "maximum_concurrent_calls": int(row["maximum_concurrent_calls"]),
        "maximum_input_characters": int(row["maximum_input_characters"]),
        "maximum_output_characters": int(row["maximum_output_characters"]),
        "consumed_provider_calls": int(row["consumed_provider_calls"]),
        "task": str(row["task_text"]),
        "allowed_specialist_role_ids": json.loads(str(row["allowed_specialist_role_ids"])),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def start_team_run(
    connection: sqlite3.Connection,
    workspace_id: str,
    payload: dict[str, Any],
) -> dict[str, object]:
    conversation_id = payload.get("conversation_id")
    task = payload.get("task")
    team_mode = payload.get("team_mode")
    if team_mode is not True:
        raise DesktopApiError(400, "desktop_native_input_invalid")
    if not isinstance(conversation_id, str) or not _CONVERSATION_ID_PATTERN.fullmatch(conversation_id):
        raise DesktopApiError(404, "desktop_conversation_not_found")
    normalized_task = _bounded_text(task, 16_384)
    if normalized_task is None:
        raise DesktopApiError(400, "desktop_native_input_invalid")
    budget_result = validate_team_run_budget(
        {
            "maximumProviderCalls": payload.get("maximum_provider_calls"),
            "maximumWallTimeMs": payload.get("maximum_wall_time_ms"),
            "maximumConcurrentCalls": payload.get("maximum_concurrent_calls"),
            "maximumInputCharacters": payload.get("maximum_input_characters"),
            "maximumOutputCharacters": payload.get("maximum_output_characters"),
        }
    )
    if not budget_result.ok or budget_result.normalized is None:
        raise DesktopApiError(400, budget_result.code)
    allowed = payload.get("allowed_specialist_role_ids")
    if allowed is None:
        allowed_roles = tuple(sorted(SPECIALIST_ROLE_IDS))
    else:
        if not isinstance(allowed, list) or not allowed:
            raise DesktopApiError(400, "desktop_native_input_invalid")
        if any(role not in SPECIALIST_ROLE_IDS for role in allowed):
            raise DesktopApiError(400, "desktop_team_unknown_role")
        allowed_roles = tuple(dict.fromkeys(allowed))
    owner = _require_owner(connection)
    _require_workspace(connection, str(owner["id"]), workspace_id, active=True)
    conversation = connection.execute(
        "SELECT id, state FROM conversation WHERE id = ? AND owner_id = ? AND workspace_id = ?",
        (conversation_id, owner["id"], workspace_id),
    ).fetchone()
    if conversation is None:
        raise DesktopApiError(404, "desktop_conversation_not_found")
    if str(conversation["state"]) != "active":
        raise DesktopApiError(409, "desktop_conversation_archived")
    now = utc_now_text()
    team_run_id = _new_id("teamrun")
    budget = budget_result.normalized
    try:
        connection.execute("BEGIN IMMEDIATE")
        live = connection.execute(
            "SELECT id FROM team_run WHERE conversation_id = ? "
            "AND state IN ('preparing', 'running', 'cancelling')",
            (conversation_id,),
        ).fetchone()
        if live is not None:
            raise DesktopApiError(409, "desktop_team_run_already_active")
        count = connection.execute(
            "SELECT COUNT(*) FROM team_run WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()[0]
        if int(count) >= _MAX_TEAM_RUNS:
            raise DesktopApiError(409, "desktop_team_run_capacity_reached")
        connection.execute(
            "INSERT INTO team_run ("
            "id, owner_id, workspace_id, conversation_id, mode, state, staffing_authority, "
            "maximum_provider_calls, maximum_wall_time_ms, maximum_concurrent_calls, "
            "maximum_input_characters, maximum_output_characters, consumed_provider_calls, "
            "task_text, allowed_specialist_role_ids, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, 'team', 'preparing', 'parent_proposal', ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
            (
                team_run_id,
                owner["id"],
                workspace_id,
                conversation_id,
                budget["maximumProviderCalls"],
                budget["maximumWallTimeMs"],
                budget["maximumConcurrentCalls"],
                budget["maximumInputCharacters"],
                budget["maximumOutputCharacters"],
                normalized_task,
                _canonical_json(list(allowed_roles)),
                now,
                now,
            ),
        )
        append_audit_event(
            connection,
            event_id=_new_id("event"),
            owner_id=str(owner["id"]),
            workspace_id=workspace_id,
            event_type="team_run_started",
            payload={"team_run_id": team_run_id},
        )
        row = connection.execute("SELECT * FROM team_run WHERE id = ?", (team_run_id,)).fetchone()
        connection.execute("COMMIT")
    except DesktopApiError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopApiError(503, "desktop_team_run_start_failed") from None
    return {"team_run": _team_run_payload(row)}


def _load_team_run(
    connection: sqlite3.Connection, owner_id: str, workspace_id: str, team_run_id: str
) -> sqlite3.Row:
    if not _TEAM_RUN_ID_PATTERN.fullmatch(team_run_id):
        raise DesktopApiError(404, "desktop_team_run_not_found")
    row = connection.execute(
        "SELECT * FROM team_run WHERE id = ? AND owner_id = ? AND workspace_id = ?",
        (team_run_id, owner_id, workspace_id),
    ).fetchone()
    if row is None:
        raise DesktopApiError(404, "desktop_team_run_not_found")
    return row


def get_team_run(
    connection: sqlite3.Connection, workspace_id: str, team_run_id: str
) -> dict[str, object]:
    owner = _require_owner(connection)
    _require_workspace(connection, str(owner["id"]), workspace_id, active=False)
    return {"team_run": _team_run_payload(_load_team_run(connection, str(owner["id"]), workspace_id, team_run_id))}


def list_team_runs(connection: sqlite3.Connection, workspace_id: str) -> dict[str, object]:
    owner = _require_owner(connection)
    _require_workspace(connection, str(owner["id"]), workspace_id, active=False)
    rows = connection.execute(
        "SELECT * FROM team_run WHERE owner_id = ? AND workspace_id = ? "
        "ORDER BY created_at DESC, id LIMIT ?",
        (owner["id"], workspace_id, _MAX_TEAM_RUNS),
    ).fetchall()
    return {"items": [_team_run_payload(row) for row in rows]}


def cancel_team_run(
    connection: sqlite3.Connection, workspace_id: str, team_run_id: str
) -> dict[str, object]:
    owner = _require_owner(connection)
    _require_workspace(connection, str(owner["id"]), workspace_id, active=True)
    now = utc_now_text()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = _load_team_run(connection, str(owner["id"]), workspace_id, team_run_id)
        if str(row["state"]) in {"succeeded", "failed", "cancelled", "budget_exhausted", "cannot_complete"}:
            connection.execute("COMMIT")
            return {"cancelled": False, "accepted": False, "team_run": _team_run_payload(row)}
        target = "cancelled" if str(row["state"]) == "preparing" else "cancelling"
        updated = connection.execute(
            "UPDATE team_run SET state = ?, updated_at = ? WHERE id = ? AND state = ? RETURNING *",
            (target, now, team_run_id, row["state"]),
        ).fetchone()
        if updated is None:
            raise DesktopApiError(409, "desktop_team_run_state_conflict")
        if target == "cancelling":
            updated = connection.execute(
                "UPDATE team_run SET state = 'cancelled', updated_at = ? "
                "WHERE id = ? AND state = 'cancelling' RETURNING *",
                (now, team_run_id),
            ).fetchone()
        append_audit_event(
            connection,
            event_id=_new_id("event"),
            owner_id=str(owner["id"]),
            workspace_id=workspace_id,
            event_type="team_run_cancelled",
            payload={"team_run_id": team_run_id},
        )
        connection.execute("COMMIT")
    except DesktopApiError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopApiError(503, "desktop_team_run_cancel_failed") from None
    return {"cancelled": True, "accepted": True, "team_run": _team_run_payload(updated)}


def _persist_assignments(
    connection: sqlite3.Connection,
    *,
    team_run_id: str,
    revision_id: str,
    waves: list[dict[str, Any]],
    now: str,
) -> None:
    for wave in waves:
        declared = str(wave["execution"])
        effective = "serial" if declared == "parallel" else declared
        for assignment in wave["assignments"]:
            connection.execute(
                "INSERT INTO team_assignment ("
                "id, team_run_id, plan_revision_id, wave_id, assignment_id, employee_role_id, "
                "objective, depends_on_assignment_ids, expected_output, context_requirements, "
                "declared_execution, effective_execution, state, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (
                    _new_id("teamasg"),
                    team_run_id,
                    revision_id,
                    wave["waveId"],
                    assignment["assignmentId"],
                    assignment["employeeRoleId"],
                    assignment["objective"],
                    _canonical_json(list(assignment["dependsOnAssignmentIds"])),
                    assignment["expectedOutput"],
                    _canonical_json(list(assignment["contextRequirements"])),
                    declared,
                    effective,
                    now,
                    now,
                ),
            )


def submit_parent_proposal(
    connection: sqlite3.Connection,
    workspace_id: str,
    team_run_id: str,
    proposal: object,
) -> dict[str, object]:
    owner = _require_owner(connection)
    _require_workspace(connection, str(owner["id"]), workspace_id, active=True)
    now = utc_now_text()
    try:
        connection.execute("BEGIN IMMEDIATE")
        run = _load_team_run(connection, str(owner["id"]), workspace_id, team_run_id)
        if str(run["state"]) not in {"preparing", "running"}:
            raise DesktopApiError(409, "desktop_team_run_not_accepting_proposals")
        budget = _budget_from_run(run)
        allowed = _allowed_roles_from_run(run)
        known = {
            str(row["assignment_id"])
            for row in connection.execute(
                "SELECT assignment_id FROM team_assignment WHERE team_run_id = ?",
                (team_run_id,),
            ).fetchall()
        }
        if not known:
            result = validate_parent_team_decision(
                proposal,
                budget=budget,
                allowed_roles=allowed,
                workspace_id=workspace_id,
            )
        else:
            result = validate_parent_replan_decision(
                proposal,
                budget=budget,
                allowed_roles=allowed,
                workspace_id=workspace_id,
                known_assignment_ids=frozenset(known),
            )
        encoded = _canonical_json(proposal)
        digest = _sha256_text(encoded)
        ordinal = connection.execute(
            "SELECT COALESCE(MAX(revision_ordinal), 0) + 1 FROM team_plan_revision WHERE team_run_id = ?",
            (team_run_id,),
        ).fetchone()[0]
        decision = "cannot_complete"
        if isinstance(proposal, dict) and isinstance(proposal.get("decision"), str):
            decision = str(proposal["decision"])
        if decision not in {
            "answer_directly",
            "delegate",
            "continue",
            "request_followup",
            "finish",
            "cannot_complete",
        }:
            decision = "cannot_complete"
        revision_id = _new_id("teamrev")
        connection.execute(
            "INSERT INTO team_plan_revision ("
            "id, team_run_id, revision_ordinal, decision, proposal_json, "
            "proposal_json_sha256, validated, validation_error_code, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                revision_id,
                team_run_id,
                int(ordinal),
                decision if result.ok else decision,
                encoded,
                digest,
                1 if result.ok else 0,
                None if result.ok else result.code,
                now,
            ),
        )
        if result.ok and result.normalized is not None and result.normalized.get("decision") == "delegate":
            _persist_assignments(
                connection,
                team_run_id=team_run_id,
                revision_id=revision_id,
                waves=list(result.normalized["waves"]),
                now=now,
            )
            connection.execute(
                "UPDATE team_run SET current_plan_revision_id = ?, current_wave_id = ?, "
                "dispatched_participant_count = ?, updated_at = ? WHERE id = ?",
                (
                    revision_id,
                    result.normalized["waves"][0]["waveId"],
                    len(result.normalized["waves"][0]["assignments"]) + 1,
                    now,
                    team_run_id,
                ),
            )
        elif result.ok:
            connection.execute(
                "UPDATE team_run SET current_plan_revision_id = ?, updated_at = ? WHERE id = ?",
                (revision_id, now, team_run_id),
            )
        run = connection.execute("SELECT * FROM team_run WHERE id = ?", (team_run_id,)).fetchone()
        revision = connection.execute(
            "SELECT * FROM team_plan_revision WHERE id = ?", (revision_id,)
        ).fetchone()
        connection.execute("COMMIT")
    except DesktopApiError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopApiError(503, "desktop_team_proposal_persist_failed") from None
    return {
        "accepted": bool(result.ok),
        "validation_error_code": None if result.ok else result.code,
        "team_run": _team_run_payload(run),
        "plan_revision": {
            "id": str(revision["id"]),
            "revision_ordinal": int(revision["revision_ordinal"]),
            "decision": str(revision["decision"]),
            "proposal_json_sha256": str(revision["proposal_json_sha256"]),
            "validated": bool(revision["validated"]),
            "validation_error_code": revision["validation_error_code"],
            "created_at": str(revision["created_at"]),
        },
    }


def record_collaboration_request(
    connection: sqlite3.Connection,
    workspace_id: str,
    team_run_id: str,
    payload: dict[str, Any],
) -> dict[str, object]:
    owner = _require_owner(connection)
    _require_workspace(connection, str(owner["id"]), workspace_id, active=True)
    try:
        connection.execute("BEGIN IMMEDIATE")
        run = _load_team_run(connection, str(owner["id"]), workspace_id, team_run_id)
        result = validate_collaboration_request(
            payload,
            budget=_budget_from_run(run),
            allowed_roles=_allowed_roles_from_run(run),
            workspace_id=workspace_id,
        )
        if not result.ok or result.normalized is None:
            raise DesktopApiError(400, result.code)
        from_assignment = result.normalized.get("fromAssignmentId")
        from_role = result.normalized.get("fromEmployeeRoleId")
        if not isinstance(from_assignment, str) or not isinstance(from_role, str):
            raise DesktopApiError(400, "desktop_native_input_invalid")
        if from_role not in SPECIALIST_ROLE_IDS:
            raise DesktopApiError(400, "desktop_team_unknown_role")
        assignment = connection.execute(
            "SELECT assignment_id FROM team_assignment "
            "WHERE team_run_id = ? AND assignment_id = ? AND employee_role_id = ?",
            (team_run_id, from_assignment, from_role),
        ).fetchone()
        if assignment is None:
            raise DesktopApiError(404, "desktop_team_assignment_not_found")
        now = utc_now_text()
        request_id = _new_id("teamcollab")
        connection.execute(
            "INSERT INTO team_collaboration_request ("
            "id, team_run_id, from_assignment_id, from_employee_role_id, target_role_id, "
            "question, reason, parent_decision, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (
                request_id,
                team_run_id,
                from_assignment,
                from_role,
                result.normalized["targetRoleId"],
                result.normalized["question"],
                result.normalized["reason"],
                now,
                now,
            ),
        )
        connection.execute("COMMIT")
    except DesktopApiError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopApiError(503, "desktop_team_collaboration_persist_failed") from None
    return {
        "collaboration_request": {
            "id": request_id,
            "team_run_id": team_run_id,
            "from_assignment_id": from_assignment,
            "from_employee_role_id": from_role,
            "target_role_id": result.normalized["targetRoleId"],
            "question": result.normalized["question"],
            "reason": result.normalized["reason"],
            "parent_decision": "pending",
            "resolved_assignment_id": None,
        }
    }


def get_team_blackboard(
    connection: sqlite3.Connection, workspace_id: str, team_run_id: str
) -> dict[str, object]:
    owner = _require_owner(connection)
    _require_workspace(connection, str(owner["id"]), workspace_id, active=False)
    run = _load_team_run(connection, str(owner["id"]), workspace_id, team_run_id)
    assignments = connection.execute(
        "SELECT assignment_id, employee_role_id, objective, state, wave_id, "
        "depends_on_assignment_ids, expected_output "
        "FROM team_assignment WHERE team_run_id = ? ORDER BY created_at, id",
        (team_run_id,),
    ).fetchall()
    requests = connection.execute(
        "SELECT from_assignment_id, from_employee_role_id, target_role_id, question, "
        "reason, parent_decision, resolved_assignment_id "
        "FROM team_collaboration_request WHERE team_run_id = ? ORDER BY created_at, id",
        (team_run_id,),
    ).fetchall()
    return {
        "blackboard": {
            "team_run_id": str(run["id"]),
            "workspace_id": str(run["workspace_id"]),
            "owner_objective": str(run["task_text"]),
            "current_plan_revision_id": run["current_plan_revision_id"],
            "assignments": [
                {
                    "assignment_id": str(row["assignment_id"]),
                    "employee_role_id": str(row["employee_role_id"]),
                    "objective": str(row["objective"]),
                    "state": str(row["state"]),
                    "wave_id": str(row["wave_id"]),
                    "depends_on_assignment_ids": json.loads(str(row["depends_on_assignment_ids"])),
                    "expected_output": str(row["expected_output"]),
                }
                for row in assignments
            ],
            "reports": [],
            "collaboration_requests": [
                {
                    "from_assignment_id": str(row["from_assignment_id"]),
                    "from_employee_role_id": str(row["from_employee_role_id"]),
                    "target_role_id": str(row["target_role_id"]),
                    "question": str(row["question"]),
                    "reason": str(row["reason"]),
                    "parent_decision": str(row["parent_decision"]),
                    "resolved_assignment_id": row["resolved_assignment_id"],
                }
                for row in requests
            ],
        }
    }
