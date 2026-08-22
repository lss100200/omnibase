from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from omnibase.desktop_local.app import (
    DESKTOP_NATIVE_CONTROL_HEADER,
    DesktopLocalAppConfig,
    create_desktop_local_app,
)
from omnibase.desktop_local.config import DesktopLocalConfig
from omnibase.desktop_local.database import initialized_database
from omnibase.desktop_local.personal_team import (
    FORBIDDEN_ROLE_CONFIG_COLUMNS,
    SPECIALIST_ROLE_IDS,
    validate_collaboration_request,
    validate_employee_team_report,
    validate_parent_replan_decision,
    validate_parent_team_decision,
    validate_team_run_budget,
)
from omnibase.desktop_local.schema import DESKTOP_SCHEMA_VERSION

_CONTROL = "e" * 64
_TOKEN = "a" * 64
_PROOF = "c" * 64
_SECRET = "isolation-stream-secret"
_BLOB = "c3RyZWFtLWVuY3J5cHRlZC1ibG9i"
_FINGERPRINT = "a" * 64
_OTHER_WORKSPACE = "workspace_" + "f" * 32

_BUDGET = {
    "maximumProviderCalls": 16,
    "maximumWallTimeMs": 600_000,
    "maximumConcurrentCalls": 2,
    "maximumInputCharacters": 16_384,
    "maximumOutputCharacters": 32_768,
}


def _config(tmp_path: Path) -> DesktopLocalAppConfig:
    return DesktopLocalAppConfig(
        storage=DesktopLocalConfig(
            data_root=tmp_path / "personal-team-data",
            application_version="1.0.0",
        ),
        instance_token=_TOKEN,
        native_proof_key=_PROOF,
        native_control_token=_CONTROL,
        port=47_431,
    )


def _native() -> dict[str, str]:
    return {DESKTOP_NATIVE_CONTROL_HEADER: _CONTROL}


def _assignment(
    assignment_id: str = "frontend-review",
    role: str = "frontend",
    depends: list[str] | None = None,
    objective: str = "检查桌面端状态投影",
) -> dict[str, object]:
    return {
        "assignmentId": assignment_id,
        "employeeRoleId": role,
        "objective": objective,
        "dependsOnAssignmentIds": depends or [],
        "expectedOutput": "风险和建议",
        "contextRequirements": [],
    }


def _delegate(assignments: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "decision": "delegate",
        "objective": "审查并完善桌面端多 Agent 设计",
        "waves": [
            {
                "waveId": "wave-1",
                "execution": "parallel",
                "assignments": assignments
                or [
                    _assignment(),
                    _assignment("backend-review", "backend", objective="检查 SQLite 和 IPC"),
                ],
            }
        ],
        "finalSynthesisRequired": True,
    }


class _StreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(
            b'data: {"model":"loopback-chat","choices":[{"delta":{"content":"ok"}}]}\n\n'
            b"data: [DONE]\n\n"
        )
        self.wfile.flush()


def _serve() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StreamHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _bootstrap_workspace(
    client: TestClient, *, with_provider: bool = False, base_url: str = ""
) -> tuple[str, str]:
    assert (
        client.post(
            "/desktop/v1/owner/bootstrap",
            headers=_native(),
            json={"display_name": "Local Owner"},
        ).status_code
        == 200
    )
    workspace = client.post(
        "/desktop/v1/workspaces", headers=_native(), json={"name": "Team Space"}
    )
    workspace_id = workspace.json()["workspace"]["id"]
    if with_provider:
        created = client.post(
            "/desktop/v1/providers",
            headers=_native(),
            json={
                "display_name": "本地模型",
                "base_url": base_url,
                "model_name": "loopback-chat",
                "gear": "standard",
                "thinking_depth": "low",
                "timeout_seconds": 15,
                "allow_loopback_http": True,
                "is_default": True,
                "is_enabled": True,
                "credential_reference": "electron-safe-storage:v1",
                "encrypted_secret_blob": _BLOB,
                "secret_fingerprint": _FINGERPRINT,
            },
        )
        assert created.status_code == 200
    conversation = client.post(
        f"/desktop/v1/workspaces/{workspace_id}/conversations",
        headers=_native(),
        json={"title": "团队任务"},
    )
    return workspace_id, conversation.json()["conversation"]["id"]


def _start_run(
    client: TestClient,
    workspace_id: str,
    conversation_id: str,
    *,
    allowed_specialist_role_ids: list[str] | None = None,
    **budget: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "conversation_id": conversation_id,
        "task": "审查桌面端多 Agent 设计",
        "team_mode": True,
        "maximum_provider_calls": budget.get("maximum_provider_calls", 16),
        "maximum_wall_time_ms": budget.get("maximum_wall_time_ms", 600_000),
        "maximum_concurrent_calls": budget.get("maximum_concurrent_calls", 2),
        "maximum_input_characters": budget.get("maximum_input_characters", 16_384),
        "maximum_output_characters": budget.get("maximum_output_characters", 32_768),
    }
    if allowed_specialist_role_ids is not None:
        payload["allowed_specialist_role_ids"] = allowed_specialist_role_ids
    response = client.post(
        f"/desktop/v1/workspaces/{workspace_id}/team-runs",
        headers=_native(),
        json=payload,
    )
    return {"status": response.status_code, "body": response.json()}


def test_schema_v3_has_team_tables_without_secret_columns(tmp_path: Path) -> None:
    with initialized_database(_config(tmp_path).storage) as connection:
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0] == DESKTOP_SCHEMA_VERSION == 8
        )
        tables = {
            row["name"]: row["sql"]
            for row in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND sql IS NOT NULL"
            )
        }
        for name in (
            "workspace_agent_role_config",
            "team_run",
            "team_plan_revision",
            "team_assignment",
            "team_node",
            "team_collaboration_request",
            "team_employee_report",
        ):
            assert name in tables
            assert tables[name].rstrip().endswith("STRICT")
        role_sql = tables["workspace_agent_role_config"].lower()
        for forbidden in FORBIDDEN_ROLE_CONFIG_COLUMNS:
            assert forbidden not in role_sql
        history = [
            tuple(row)
            for row in connection.execute(
                "SELECT version, migration_id FROM desktop_migration_history ORDER BY version"
            )
        ]
        assert history[-6:] == [
            (3, "desktop_0003_personal_agent_team"),
            (4, "desktop_0004_personal_team_runtime"),
            (5, "desktop_0005_team_node_identity_epochs"),
            (6, "desktop_0006_report_collaboration_digest"),
            (7, "desktop_0007_recovery_success_downgrade"),
            (8, "desktop_0008_collaboration_report_binding"),
        ]
        indexes = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }
        assert "team_node_node_epoch_unique" in indexes
        assert "team_node_send_epoch_unique" in indexes


def test_unknown_role_is_rejected() -> None:
    result = validate_parent_team_decision(
        _delegate([_assignment(role="network-pentest")]),
        budget=_BUDGET,
        allowed_roles=SPECIALIST_ROLE_IDS,
        workspace_id=None,
    )
    assert result.ok is False
    assert result.code == "desktop_team_unknown_role"


def test_parent_cannot_be_specialist() -> None:
    result = validate_parent_team_decision(
        _delegate([_assignment(role="parent")]),
        budget=_BUDGET,
        allowed_roles=SPECIALIST_ROLE_IDS,
        workspace_id=None,
    )
    assert result.ok is False
    assert result.code == "desktop_team_parent_not_specialist"


def test_duplicate_assignment_id_is_rejected() -> None:
    result = validate_parent_team_decision(
        _delegate([_assignment(), _assignment()]),
        budget=_BUDGET,
        allowed_roles=SPECIALIST_ROLE_IDS,
        workspace_id=None,
    )
    assert result.ok is False
    assert result.code == "desktop_team_duplicate_assignment_id"


def test_missing_dependency_is_rejected() -> None:
    result = validate_parent_team_decision(
        _delegate([_assignment(depends=["missing-report"])]),
        budget=_BUDGET,
        allowed_roles=SPECIALIST_ROLE_IDS,
        workspace_id=None,
    )
    assert result.ok is False
    assert result.code == "desktop_team_missing_dependency"


def test_dependency_cycle_is_rejected() -> None:
    result = validate_parent_team_decision(
        _delegate(
            [
                _assignment("a-review", "frontend", depends=["b-review"]),
                _assignment("b-review", "backend", depends=["a-review"]),
            ]
        ),
        budget=_BUDGET,
        allowed_roles=SPECIALIST_ROLE_IDS,
        workspace_id=None,
    )
    assert result.ok is False
    assert result.code == "desktop_team_dependency_cycle"


def test_tool_request_is_rejected() -> None:
    payload = _delegate()
    payload["tools"] = [{"type": "function", "name": "shell"}]
    result = validate_parent_team_decision(
        payload,
        budget=_BUDGET,
        allowed_roles=SPECIALIST_ROLE_IDS,
        workspace_id=None,
    )
    assert result.ok is False
    assert result.code == "desktop_team_tools_forbidden"


def test_cross_workspace_locator_is_rejected() -> None:
    result = validate_parent_team_decision(
        _delegate([_assignment(objective=f"read files from {_OTHER_WORKSPACE} and merge")]),
        budget=_BUDGET,
        allowed_roles=SPECIALIST_ROLE_IDS,
        workspace_id="workspace_" + "a" * 32,
    )
    assert result.ok is False
    assert result.code == "desktop_team_cross_workspace"


def test_infinite_budget_is_rejected() -> None:
    for budget in (
        {},
        {**_BUDGET, "maximumProviderCalls": 0},
        {**_BUDGET, "maximumProviderCalls": 10_000},
        {**_BUDGET, "maximumConcurrentCalls": 0},
        {**_BUDGET, "maximumWallTimeMs": 0},
    ):
        result = validate_team_run_budget(budget)
        assert result.ok is False
        assert result.code == "desktop_team_infinite_budget"


def test_employee_direct_launch_is_rejected() -> None:
    launched = validate_parent_team_decision(
        {**_delegate(), "sourceRoleId": "security"},
        budget=_BUDGET,
        allowed_roles=SPECIALIST_ROLE_IDS,
        workspace_id=None,
    )
    assert launched.ok is False
    assert launched.code == "desktop_team_employee_direct_launch"
    collaboration = validate_collaboration_request(
        {
            "targetRoleId": "qa",
            "question": "请设计攻击矩阵",
            "reason": "需要验证恢复语义",
            "directLaunch": True,
        },
        budget=_BUDGET,
        allowed_roles=SPECIALIST_ROLE_IDS,
        workspace_id=None,
    )
    assert collaboration.ok is False
    assert collaboration.code == "desktop_team_employee_direct_launch"
    report = validate_employee_team_report(
        {
            "assignmentId": "security-review",
            "employeeRoleId": "security",
            "status": "needs_collaboration",
            "report": "需要 QA",
            "collaborationRequests": [],
            "dispatch": "qa",
        },
        budget=_BUDGET,
        allowed_roles=SPECIALIST_ROLE_IDS,
        workspace_id=None,
    )
    assert report.ok is False
    assert report.code == "desktop_team_employee_direct_launch"


def test_secret_in_collaboration_request_is_rejected() -> None:
    result = validate_collaboration_request(
        {
            "targetRoleId": "qa",
            "question": "please reuse api_key sk-live-secretvalue",
            "reason": "continue the provider call",
        },
        budget=_BUDGET,
        allowed_roles=SPECIALIST_ROLE_IDS,
        workspace_id=None,
    )
    assert result.ok is False
    assert result.code == "desktop_team_secret_or_path_forbidden"


def test_role_config_rejects_secret_columns_and_inherits_fingerprint_only(tmp_path: Path) -> None:
    server = _serve()
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
        with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
            workspace_id, _conversation_id = _bootstrap_workspace(
                client, with_provider=True, base_url=base_url
            )
            listed = client.get(
                f"/desktop/v1/workspaces/{workspace_id}/agent-roles",
                headers=_native(),
            )
            assert listed.status_code == 200
            roles = listed.json()["items"]
            assert len(roles) == 10
            frontend = next(item for item in roles if item["id"] == "frontend")
            assert frontend["inherited_provider"] is True
            assert frontend["secret_fingerprint"] == _FINGERPRINT
            assert "encrypted_secret_blob" not in frontend
            assert "api_key" not in frontend
            forbidden = client.post(
                f"/desktop/v1/workspaces/{workspace_id}/agent-roles/frontend",
                headers=_native(),
                json={
                    "provider_id": None,
                    "model_name_override": "loopback-chat",
                    "gear": "standard",
                    "thinking_depth": "low",
                    "api_key": "sk-must-not-store",
                },
            )
            assert forbidden.status_code == 422
            updated = client.post(
                f"/desktop/v1/workspaces/{workspace_id}/agent-roles/frontend",
                headers=_native(),
                json={
                    "provider_id": None,
                    "model_name_override": "loopback-specialist",
                    "gear": "standard",
                    "thinking_depth": "low",
                    "expected_row_version": 1,
                },
            )
            assert updated.status_code == 200
            assert updated.json()["role"]["model_name_override"] == "loopback-specialist"
            assert updated.json()["role"]["inherited_provider"] is True
            tested = client.post(
                f"/desktop/v1/workspaces/{workspace_id}/agent-roles/frontend/test",
                headers=_native(),
            )
            assert tested.status_code == 200
            assert tested.json()["secret_fingerprint"] == _FINGERPRINT
            assert tested.json()["inherited_provider"] is True
            assert tested.json()["requested_model"] == "loopback-specialist"
            db = sqlite3.connect(_config(tmp_path).storage.database_path)
            db.row_factory = sqlite3.Row
            try:
                columns = {
                    row["name"]
                    for row in db.execute("PRAGMA table_info(workspace_agent_role_config)")
                }
                for forbidden_name in FORBIDDEN_ROLE_CONFIG_COLUMNS:
                    assert forbidden_name not in columns
                row = db.execute(
                    "SELECT * FROM workspace_agent_role_config WHERE employee_role_id = 'frontend'"
                ).fetchone()
                dumped = json.dumps(dict(row), ensure_ascii=False)
                assert "sk-must-not-store" not in dumped
                assert _BLOB not in dumped
            finally:
                db.close()
    finally:
        server.shutdown()
        server.server_close()


def test_valid_parent_proposal_persists_and_illegal_proposals_do_not_create_assignments(
    tmp_path: Path,
) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        workspace_id, conversation_id = _bootstrap_workspace(client)
        started = _start_run(client, workspace_id, conversation_id)
        assert started["status"] == 200
        team_run_id = started["body"]["team_run"]["id"]
        rejected = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/proposals",
            headers=_native(),
            json={"proposal": _delegate([_assignment(role="super-agent")])},
        )
        assert rejected.status_code == 200
        assert rejected.json()["accepted"] is False
        assert rejected.json()["validation_error_code"] == "desktop_team_unknown_role"
        accepted = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/proposals",
            headers=_native(),
            json={"proposal": _delegate()},
        )
        assert accepted.status_code == 200
        assert accepted.json()["accepted"] is True
        assert accepted.json()["plan_revision"]["validated"] is True
        board = client.get(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/blackboard",
            headers=_native(),
        )
        assert board.status_code == 200
        assert len(board.json()["blackboard"]["assignments"]) == 2
        other = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/conversations",
            headers=_native(),
            json={"title": "第二会话"},
        )
        infinite = _start_run(
            client,
            workspace_id,
            other.json()["conversation"]["id"],
            maximum_provider_calls=0,
        )
        assert infinite["status"] == 400
        assert infinite["body"]["error"]["code"] == "desktop_team_infinite_budget"


def test_single_agent_send_path_still_works_with_team_schema(tmp_path: Path) -> None:
    server = _serve()
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
        with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
            workspace_id, conversation_id = _bootstrap_workspace(
                client, with_provider=True, base_url=base_url
            )
            with client.stream(
                "POST",
                f"/desktop/v1/workspaces/{workspace_id}/conversations/{conversation_id}/messages",
                headers=_native(),
                json={"secret": _SECRET, "content": "hello parent"},
            ) as stream:
                body = b"".join(stream.iter_bytes()).decode("utf-8")
            detail = client.get(
                f"/desktop/v1/workspaces/{workspace_id}/conversations/{conversation_id}",
                headers=_native(),
            )
    finally:
        server.shutdown()
        server.server_close()
        time.sleep(0)
    assert "event: done" in body
    messages = detail.json()["messages"]
    assert messages[0]["content"] == "hello parent"
    assert messages[1]["status"] == "completed"
    assert messages[1]["invocation"]["status"] == "succeeded"


def test_infinite_replan_cap_is_rejected() -> None:
    result = validate_parent_replan_decision(
        {"decision": "finish", "reason": "done", "replanCap": None},
        budget=_BUDGET,
        allowed_roles=SPECIALIST_ROLE_IDS,
        workspace_id=None,
        known_assignment_ids=frozenset({"frontend-review"}),
        revision_ordinal=1,
    )
    assert result.ok is False
    assert result.code == "desktop_team_infinite_replan"
    too_many = validate_parent_replan_decision(
        {"decision": "finish", "reason": "done"},
        budget=_BUDGET,
        allowed_roles=SPECIALIST_ROLE_IDS,
        workspace_id=None,
        known_assignment_ids=frozenset({"frontend-review"}),
        revision_ordinal=99,
    )
    assert too_many.ok is False
    assert too_many.code == "desktop_team_infinite_replan"


def test_continue_proposal_persists_new_assignments_without_overwriting(tmp_path: Path) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        workspace_id, conversation_id = _bootstrap_workspace(client)
        started = _start_run(client, workspace_id, conversation_id)
        team_run_id = started["body"]["team_run"]["id"]
        first = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/proposals",
            headers=_native(),
            json={"proposal": _delegate()},
        )
        assert first.status_code == 200
        follow = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/proposals",
            headers=_native(),
            json={
                "proposal": {
                    "decision": "request_followup",
                    "assignments": [
                        _assignment("frontend-followup", "frontend", ["frontend-review"])
                    ],
                }
            },
        )
        assert follow.status_code == 200
        assert follow.json()["accepted"] is True
        board = client.get(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/blackboard",
            headers=_native(),
        )
        ids = [item["assignment_id"] for item in board.json()["blackboard"]["assignments"]]
        assert "frontend-review" in ids
        assert "frontend-followup" in ids


def test_restart_marks_live_team_run_unknown_without_replay(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, conversation_id = _bootstrap_workspace(client)
        started = _start_run(client, workspace_id, conversation_id)
        assert started["status"] == 200
        team_run_id = started["body"]["team_run"]["id"]
        assert started["body"]["team_run"]["state"] == "preparing"
    with TestClient(create_desktop_local_app(config)) as client:
        recovered = client.get(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}",
            headers=_native(),
        )
        assert recovered.status_code == 200
        assert recovered.json()["team_run"]["state"] == "unknown"
        again = _start_run(client, workspace_id, conversation_id)
        assert again["status"] == 200
        assert again["body"]["team_run"]["id"] != team_run_id


def test_append_budget_rejects_infinite_and_keeps_consumed(tmp_path: Path) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        workspace_id, conversation_id = _bootstrap_workspace(client)
        started = _start_run(client, workspace_id, conversation_id)
        team_run_id = started["body"]["team_run"]["id"]
        bad = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/budget",
            headers=_native(),
            json={
                "maximum_provider_calls": 0,
                "maximum_wall_time_ms": 600000,
                "maximum_concurrent_calls": 2,
                "maximum_input_characters": 16384,
                "maximum_output_characters": 32768,
            },
        )
        assert bad.status_code == 400
        ok = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/budget",
            headers=_native(),
            json={
                "maximum_provider_calls": 24,
                "maximum_wall_time_ms": 600000,
                "maximum_concurrent_calls": 3,
                "maximum_input_characters": 16384,
                "maximum_output_characters": 32768,
            },
        )
        assert ok.status_code == 200
        assert ok.json()["team_run"]["maximum_provider_calls"] == 24


def test_append_budget_requires_live_run(tmp_path: Path) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        workspace_id, conversation_id = _bootstrap_workspace(client)
        started = _start_run(client, workspace_id, conversation_id)
        team_run_id = started["body"]["team_run"]["id"]
        cancelled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/cancel",
            headers=_native(),
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["team_run"]["state"] == "cancelled"
        appended = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/budget",
            headers=_native(),
            json={
                "maximum_provider_calls": 24,
                "maximum_wall_time_ms": 600000,
                "maximum_concurrent_calls": 3,
                "maximum_input_characters": 16384,
                "maximum_output_characters": 32768,
            },
        )
        assert appended.status_code == 409
        assert appended.json()["error"]["code"] == "desktop_team_run_terminal"


def _prepare_assigned_run(client: TestClient) -> tuple[str, str, str, str]:
    workspace_id, conversation_id = _bootstrap_workspace(
        client, with_provider=True, base_url="http://127.0.0.1:9/v1"
    )
    listed = client.get("/desktop/v1/providers", headers=_native())
    assert listed.status_code == 200
    provider_id = listed.json()["items"][0]["id"]
    started = _start_run(client, workspace_id, conversation_id)
    assert started["status"] == 200
    team_run_id = started["body"]["team_run"]["id"]
    accepted = client.post(
        f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/proposals",
        headers=_native(),
        json={"proposal": _delegate([_assignment()])},
    )
    assert accepted.status_code == 200
    assert accepted.json()["accepted"] is True
    return workspace_id, conversation_id, team_run_id, provider_id


def _create_running_node(
    client: TestClient,
    workspace_id: str,
    team_run_id: str,
    provider_id: str,
    *,
    invocation_id: str = "invocation_" + "a" * 32,
    node_epoch: int = 1,
    send_epoch: int = 1,
) -> dict[str, str]:
    created = client.post(
        f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes",
        headers=_native(),
        json={
            "assignment_id": "frontend-review",
            "employee_role_id": "frontend",
            "invocation_id": invocation_id,
            "wave_id": "wave-1",
            "node_epoch": node_epoch,
            "send_epoch": send_epoch,
            "provider_id": provider_id,
            "requested_model": "loopback-chat",
        },
    )
    assert created.status_code == 200
    node_id = created.json()["node"]["id"]
    return {"node_id": node_id, "invocation_id": invocation_id}


def _settle_payload(
    invocation_id: str, report: str = "前端已完成桌面状态检查"
) -> dict[str, object]:
    digest = hashlib.sha256(report.encode("utf-8")).hexdigest()
    return {
        "state": "succeeded",
        "actual_model": "loopback-chat",
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
        "answer_sha256": digest,
        "error_code": None,
        "duration_ms": 12,
        "invocation_id": invocation_id,
        "assignment_id": "frontend-review",
        "employee_role_id": "frontend",
        "status": "completed",
        "report": report,
        "collaboration_requests": [],
        "wave_id": "wave-1",
        "node_epoch": 1,
        "send_epoch": 1,
    }


def _finish_plan(client: TestClient, workspace_id: str, team_run_id: str) -> None:
    finished = client.post(
        f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/proposals",
        headers=_native(),
        json={"proposal": {"decision": "finish", "reason": "团队工作已完成"}},
    )
    assert finished.status_code == 200, finished.text
    assert finished.json()["accepted"] is True


def test_empty_allow_list_fails_closed_without_defaulting_all_nine(tmp_path: Path) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        workspace_id, conversation_id = _bootstrap_workspace(client)
        started = _start_run(
            client,
            workspace_id,
            conversation_id,
            allowed_specialist_role_ids=[],
        )
        assert started["status"] == 400
        assert started["body"]["error"]["code"] == "desktop_team_allow_list_empty"
        db = sqlite3.connect(_config(tmp_path).storage.database_path)
        try:
            assert db.execute("SELECT COUNT(*) FROM team_run").fetchone()[0] == 0
        finally:
            db.close()


def test_unset_allow_list_still_defaults_to_all_nine(tmp_path: Path) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        workspace_id, conversation_id = _bootstrap_workspace(client)
        started = _start_run(client, workspace_id, conversation_id)
        assert started["status"] == 200
        allowed = started["body"]["team_run"]["allowed_specialist_role_ids"]
        assert sorted(allowed) == sorted(SPECIALIST_ROLE_IDS)


def test_role_config_cas_conflict_does_not_lost_update(tmp_path: Path) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        workspace_id, _conversation_id = _bootstrap_workspace(client)
        first = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/agent-roles/frontend",
            headers=_native(),
            json={
                "provider_id": None,
                "model_name_override": "loopback-first",
                "gear": "standard",
                "thinking_depth": "low",
                "expected_row_version": 1,
            },
        )
        assert first.status_code == 200
        assert first.json()["role"]["row_version"] == 1
        second = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/agent-roles/frontend",
            headers=_native(),
            json={
                "provider_id": None,
                "model_name_override": "loopback-second",
                "gear": "standard",
                "thinking_depth": "low",
                "expected_row_version": 1,
            },
        )
        assert second.status_code == 200
        assert second.json()["role"]["row_version"] == 2
        stale = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/agent-roles/frontend",
            headers=_native(),
            json={
                "provider_id": None,
                "model_name_override": "loopback-stale",
                "gear": "standard",
                "thinking_depth": "low",
                "expected_row_version": 1,
            },
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "desktop_role_config_cas_conflict"
        current = client.get(
            f"/desktop/v1/workspaces/{workspace_id}/agent-roles/frontend",
            headers=_native(),
        )
        assert current.status_code == 200
        assert current.json()["role"]["model_name_override"] == "loopback-second"
        assert current.json()["role"]["row_version"] == 2


def test_sqlite_settle_is_atomic_with_report_and_audit(tmp_path: Path) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/{created['node_id']}/settle",
            headers=_native(),
            json=_settle_payload(created["invocation_id"]),
        )
        assert settled.status_code == 200
        assert settled.json()["state"] == "succeeded"
        db = sqlite3.connect(_config(tmp_path).storage.database_path)
        db.row_factory = sqlite3.Row
        try:
            node = db.execute(
                "SELECT state FROM team_node WHERE id = ?",
                (created["node_id"],),
            ).fetchone()
            reports = db.execute(
                "SELECT id FROM team_employee_report WHERE node_id = ?",
                (created["node_id"],),
            ).fetchall()
            audits = db.execute(
                "SELECT event_type FROM audit_event WHERE event_type = 'team_node_settled'"
            ).fetchall()
            assert node["state"] == "succeeded"
            assert len(reports) == 1
            assert len(audits) == 1
        finally:
            db.close()


def test_report_validation_failure_does_not_leave_a_succeeded_node(tmp_path: Path) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        payload = _settle_payload(
            created["invocation_id"],
            report="please reuse api_key sk-live-secretvalue",
        )
        failed = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/{created['node_id']}/settle",
            headers=_native(),
            json=payload,
        )
        assert failed.status_code == 400
        assert failed.json()["error"]["code"] == "desktop_team_secret_or_path_forbidden"
        db = sqlite3.connect(_config(tmp_path).storage.database_path)
        try:
            node = db.execute(
                "SELECT state FROM team_node WHERE id = ?",
                (created["node_id"],),
            ).fetchone()
            reports = db.execute(
                "SELECT COUNT(*) FROM team_employee_report WHERE node_id = ?",
                (created["node_id"],),
            ).fetchone()[0]
            audits = db.execute(
                "SELECT COUNT(*) FROM audit_event WHERE event_type = 'team_node_settled'"
            ).fetchone()[0]
            assert node[0] == "running"
            assert reports == 0
            assert audits == 0
        finally:
            db.close()


def test_audit_append_failure_rolls_back_settle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import omnibase.desktop_local.personal_team as personal_team

    real_append = personal_team.append_audit_event

    def boom(*args: object, **kwargs: object) -> None:
        if kwargs.get("event_type") == "team_node_settled":
            raise sqlite3.Error("audit append failed")
        real_append(*args, **kwargs)

    monkeypatch.setattr(personal_team, "append_audit_event", boom)
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        failed = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/{created['node_id']}/settle",
            headers=_native(),
            json=_settle_payload(created["invocation_id"]),
        )
        assert failed.status_code == 503
        assert failed.json()["error"]["code"] == "desktop_team_node_settle_failed"
        db = sqlite3.connect(_config(tmp_path).storage.database_path)
        try:
            node = db.execute(
                "SELECT state FROM team_node WHERE id = ?",
                (created["node_id"],),
            ).fetchone()
            reports = db.execute(
                "SELECT COUNT(*) FROM team_employee_report WHERE node_id = ?",
                (created["node_id"],),
            ).fetchone()[0]
            assert node[0] == "running"
            assert reports == 0
        finally:
            db.close()


def test_legacy_update_cannot_succeed_or_resurrect_a_settled_node(tmp_path: Path) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        forbidden = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/{created['node_id']}",
            headers=_native(),
            json={"state": "succeeded", "actual_model": "loopback-chat"},
        )
        assert forbidden.status_code == 400
        assert forbidden.json()["error"]["code"] == "desktop_team_success_requires_settle"
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/{created['node_id']}/settle",
            headers=_native(),
            json=_settle_payload(created["invocation_id"]),
        )
        assert settled.status_code == 200
        resurrect = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/{created['node_id']}",
            headers=_native(),
            json={"state": "failed", "error_code": "desktop_invocation_failed"},
        )
        assert resurrect.status_code == 409
        assert resurrect.json()["error"]["code"] == "desktop_team_node_terminal"
        again = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/{created['node_id']}/settle",
            headers=_native(),
            json=_settle_payload(created["invocation_id"]),
        )
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "desktop_team_node_terminal"


def test_create_node_binds_live_run_identity_and_forbids_epoch_reuse(tmp_path: Path) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        missing_provider = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes",
            headers=_native(),
            json={
                "assignment_id": "frontend-review",
                "employee_role_id": "frontend",
                "invocation_id": "invocation_" + "b" * 32,
                "wave_id": "wave-1",
                "node_epoch": 1,
                "send_epoch": 1,
                "requested_model": "loopback-chat",
            },
        )
        assert missing_provider.status_code == 422
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        reused = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes",
            headers=_native(),
            json={
                "assignment_id": "frontend-review",
                "employee_role_id": "frontend",
                "invocation_id": "invocation_" + "c" * 32,
                "wave_id": "wave-1",
                "node_epoch": 1,
                "send_epoch": 2,
                "provider_id": provider_id,
                "requested_model": "loopback-chat",
            },
        )
        assert reused.status_code == 409
        assert reused.json()["error"]["code"] == "desktop_team_epoch_reused"
        mismatched = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/{created['node_id']}/settle",
            headers=_native(),
            json={
                **_settle_payload(created["invocation_id"]),
                "wave_id": "wave-9",
                "node_epoch": 9,
                "send_epoch": 9,
            },
        )
        assert mismatched.status_code == 409
        assert mismatched.json()["error"]["code"] == "desktop_team_identity_mismatch"
        omitted_identity = dict(_settle_payload(created["invocation_id"]))
        omitted_identity.pop("wave_id")
        omitted_identity.pop("node_epoch")
        omitted_identity.pop("send_epoch")
        omitted = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/{created['node_id']}/settle",
            headers=_native(),
            json=omitted_identity,
        )
        assert omitted.status_code == 409
        assert omitted.json()["error"]["code"] == "desktop_team_identity_mismatch"
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{created['node_id']}/settle",
            headers=_native(),
            json=_settle_payload(created["invocation_id"]),
        )
        assert settled.status_code == 200
        marked = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "failed"},
        )
        assert marked.status_code == 200, marked.text
        assert marked.json()["team_run"]["state"] == "failed"
        terminal_create = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes",
            headers=_native(),
            json={
                "assignment_id": "frontend-review",
                "employee_role_id": "frontend",
                "invocation_id": "invocation_" + "d" * 32,
                "wave_id": "wave-1",
                "node_epoch": 9,
                "send_epoch": 9,
                "provider_id": provider_id,
                "requested_model": "loopback-chat",
            },
        )
        assert terminal_create.status_code == 409
        assert terminal_create.json()["error"]["code"] == "desktop_team_run_terminal"


def _report_body(
    created: dict[str, str], report: str = "前端已完成桌面状态检查"
) -> dict[str, object]:
    return {
        "assignment_id": "frontend-review",
        "employee_role_id": "frontend",
        "status": "completed",
        "report": report,
        "node_id": created["node_id"],
        "invocation_id": created["invocation_id"],
        "collaboration_requests": [],
    }


def test_cancel_team_run_cas_running_nodes_and_restart_keeps_cancelled(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        cancelled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/cancel",
            headers=_native(),
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["team_run"]["state"] == "cancelled"
        db = sqlite3.connect(config.storage.database_path)
        try:
            run_state = db.execute(
                "SELECT state FROM team_run WHERE id = ?",
                (team_run_id,),
            ).fetchone()[0]
            node_states = [
                row[0]
                for row in db.execute(
                    "SELECT state FROM team_node WHERE team_run_id = ?",
                    (team_run_id,),
                ).fetchall()
            ]
            running = db.execute(
                "SELECT COUNT(*) FROM team_node WHERE team_run_id = ? AND state = 'running'",
                (team_run_id,),
            ).fetchone()[0]
            assert run_state == "cancelled"
            assert running == 0
            assert node_states == ["cancelled"]
            assert created["node_id"]
        finally:
            db.close()
        again = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/{created['node_id']}",
            headers=_native(),
            json={"state": "cancelled", "error_code": "desktop_invocation_cancelled"},
        )
        assert again.status_code == 200
        assert again.json()["state"] == "cancelled"
        failed = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/{created['node_id']}",
            headers=_native(),
            json={"state": "failed", "error_code": "desktop_invocation_failed"},
        )
        assert failed.status_code == 409
        assert failed.json()["error"]["code"] == "desktop_team_node_terminal"
    with TestClient(create_desktop_local_app(config)) as client:
        recovered = client.get(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}",
            headers=_native(),
        )
        assert recovered.status_code == 200
        assert recovered.json()["team_run"]["state"] == "cancelled"
        db = sqlite3.connect(config.storage.database_path)
        try:
            node_state = db.execute(
                "SELECT state FROM team_node WHERE id = ?",
                (created["node_id"],),
            ).fetchone()[0]
            assert node_state == "cancelled"
        finally:
            db.close()


def test_report_on_running_node_is_rejected_without_settled_audit(tmp_path: Path) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        rejected = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/reports",
            headers=_native(),
            json=_report_body(created),
        )
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "desktop_team_report_requires_settle"
        db = sqlite3.connect(_config(tmp_path).storage.database_path)
        try:
            node_state = db.execute(
                "SELECT state FROM team_node WHERE id = ?",
                (created["node_id"],),
            ).fetchone()[0]
            reports = db.execute(
                "SELECT COUNT(*) FROM team_employee_report WHERE node_id = ?",
                (created["node_id"],),
            ).fetchone()[0]
            audits = db.execute(
                "SELECT COUNT(*) FROM audit_event WHERE event_type = 'team_node_settled'"
            ).fetchone()[0]
            assert node_state == "running"
            assert reports == 0
            assert audits == 0
        finally:
            db.close()
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/{created['node_id']}/settle",
            headers=_native(),
            json=_settle_payload(created["invocation_id"]),
        )
        assert settled.status_code == 200
        replay = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/reports",
            headers=_native(),
            json=_report_body(created),
        )
        assert replay.status_code == 200
        db = sqlite3.connect(_config(tmp_path).storage.database_path)
        try:
            reports = db.execute(
                "SELECT COUNT(*) FROM team_employee_report WHERE node_id = ?",
                (created["node_id"],),
            ).fetchone()[0]
            audits = db.execute(
                "SELECT COUNT(*) FROM audit_event WHERE event_type = 'team_node_settled'"
            ).fetchone()[0]
            node_state = db.execute(
                "SELECT state FROM team_node WHERE id = ?",
                (created["node_id"],),
            ).fetchone()[0]
            assert node_state == "succeeded"
            assert reports == 1
            assert audits == 1
        finally:
            db.close()


def test_pin_endpoint_uses_python_is_global_unicast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "omnibase.desktop_local.endpoint.socket.getaddrinfo",
        lambda *args, **kwargs: [(0, 0, 0, "", ("8.8.8.8", 443))],
    )
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        pinned = client.post(
            "/desktop/v1/provider-endpoints/pin",
            headers=_native(),
            json={
                "base_url": "https://api.example.test/v1",
                "allow_loopback_http": False,
            },
        )
        assert pinned.status_code == 200
        body = pinned.json()
        assert body["hostname"] == "api.example.test"
        assert body["connect_addrs"] == ["8.8.8.8"]
        assert body["scheme"] == "https"
    monkeypatch.setattr(
        "omnibase.desktop_local.endpoint.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (0, 0, 0, "", ("8.8.8.8", 443)),
            (0, 0, 0, "", ("198.18.0.1", 443)),
        ],
    )
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        mixed = client.post(
            "/desktop/v1/provider-endpoints/pin",
            headers=_native(),
            json={
                "base_url": "https://api.example.test/v1",
                "allow_loopback_http": False,
            },
        )
        assert mixed.status_code == 400
        assert mixed.json()["error"]["code"] == "desktop_provider_endpoint_invalid"


def _live_residue_counts(database_path: Path, team_run_id: str) -> tuple[int, int]:
    db = sqlite3.connect(database_path)
    try:
        nodes = db.execute(
            "SELECT COUNT(*) FROM team_node WHERE team_run_id = ? "
            "AND state IN ('pending', 'running')",
            (team_run_id,),
        ).fetchone()[0]
        assignments = db.execute(
            "SELECT COUNT(*) FROM team_assignment WHERE team_run_id = ? "
            "AND state IN ('pending', 'ready', 'running')",
            (team_run_id,),
        ).fetchone()[0]
        return int(nodes), int(assignments)
    finally:
        db.close()


def _inject_live_residue(
    database_path: Path,
    *,
    team_run_id: str,
    assignment_id: str = "frontend-review",
    employee_role_id: str = "frontend",
    ordinal: int = 2,
    node_epoch: int = 99,
    send_epoch: int = 99,
    invocation_suffix: str = "b",
) -> str:
    node_id = "teamnode_" + invocation_suffix * 32
    invocation_id = "invocation_" + invocation_suffix * 32
    now = "2026-08-21T00:00:00+00:00"
    db = sqlite3.connect(database_path)
    try:
        existing = db.execute(
            "SELECT wave_id, provider_id, requested_model FROM team_node "
            "WHERE team_run_id = ? ORDER BY ordinal LIMIT 1",
            (team_run_id,),
        ).fetchone()
        wave_id = existing[0] if existing is not None else "wave-1"
        provider_id = existing[1] if existing is not None else None
        requested_model = existing[2] if existing is not None else "loopback-chat"
        db.execute(
            "UPDATE team_assignment SET state = 'running', updated_at = ? "
            "WHERE team_run_id = ? AND assignment_id = ?",
            (now, team_run_id, assignment_id),
        )
        db.execute(
            "INSERT INTO team_node ("
            "id, team_run_id, assignment_id, ordinal, employee_role_id, invocation_id, "
            "state, provider_id, requested_model, wave_id, node_epoch, send_epoch, "
            "created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?)",
            (
                node_id,
                team_run_id,
                assignment_id,
                ordinal,
                employee_role_id,
                invocation_id,
                provider_id,
                requested_model,
                wave_id,
                node_epoch,
                send_epoch,
                now,
                now,
            ),
        )
        db.commit()
    finally:
        db.close()
    return node_id


def test_second_stop_on_cancelled_run_cas_residual_live_nodes(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        first = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/cancel",
            headers=_native(),
        )
        assert first.status_code == 200
        assert first.json()["team_run"]["state"] == "cancelled"
        second = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/cancel",
            headers=_native(),
        )
        assert second.status_code == 200
        assert "error" not in second.json()
        assert second.json()["team_run"]["state"] == "cancelled"
        assert second.json()["accepted"] is True
        live_nodes, live_assignments = _live_residue_counts(
            config.storage.database_path, team_run_id
        )
        assert live_nodes == 0
        assert live_assignments == 0
        injected = _inject_live_residue(config.storage.database_path, team_run_id=team_run_id)
        stuck_nodes, stuck_assignments = _live_residue_counts(
            config.storage.database_path, team_run_id
        )
        assert stuck_nodes == 1
        assert stuck_assignments == 1
        third = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/cancel",
            headers=_native(),
        )
        assert third.status_code == 200
        assert third.json()["team_run"]["state"] == "cancelled"
        cleaned_nodes, cleaned_assignments = _live_residue_counts(
            config.storage.database_path, team_run_id
        )
        assert cleaned_nodes == 0
        assert cleaned_assignments == 0
        db = sqlite3.connect(config.storage.database_path)
        try:
            original = db.execute(
                "SELECT state FROM team_node WHERE id = ?",
                (created["node_id"],),
            ).fetchone()[0]
            residual = db.execute(
                "SELECT state FROM team_node WHERE id = ?",
                (injected,),
            ).fetchone()[0]
            assert original == "cancelled"
            assert residual == "cancelled"
        finally:
            db.close()


def test_recovery_maps_residual_nodes_from_parent_run_state(tmp_path: Path) -> None:
    cancelled_config = _config(tmp_path / "cancelled")
    with TestClient(create_desktop_local_app(cancelled_config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        cancelled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/cancel",
            headers=_native(),
        )
        assert cancelled.status_code == 200
        injected = _inject_live_residue(
            cancelled_config.storage.database_path, team_run_id=team_run_id
        )
    with TestClient(create_desktop_local_app(cancelled_config)) as client:
        recovered = client.get(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}",
            headers=_native(),
        )
        assert recovered.status_code == 200
        assert recovered.json()["team_run"]["state"] == "cancelled"
        db = sqlite3.connect(cancelled_config.storage.database_path)
        try:
            original = db.execute(
                "SELECT state FROM team_node WHERE id = ?",
                (created["node_id"],),
            ).fetchone()[0]
            residual = db.execute(
                "SELECT state FROM team_node WHERE id = ?",
                (injected,),
            ).fetchone()[0]
            unknown_or_live = db.execute(
                "SELECT COUNT(*) FROM team_node WHERE team_run_id = ? "
                "AND state IN ('pending', 'running', 'unknown')",
                (team_run_id,),
            ).fetchone()[0]
            assert original == "cancelled"
            assert residual == "cancelled"
            assert unknown_or_live == 0
        finally:
            db.close()

    crash_config = _config(tmp_path / "crash")
    with TestClient(create_desktop_local_app(crash_config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        running = _create_running_node(client, workspace_id, team_run_id, provider_id)
        already_cancelled = _create_running_node(
            client,
            workspace_id,
            team_run_id,
            provider_id,
            invocation_id="invocation_" + "c" * 32,
            node_epoch=2,
            send_epoch=2,
        )
        stop_one = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{already_cancelled['node_id']}",
            headers=_native(),
            json={"state": "cancelled", "error_code": "desktop_invocation_cancelled"},
        )
        assert stop_one.status_code == 200
        assert stop_one.json()["state"] == "cancelled"
    with TestClient(create_desktop_local_app(crash_config)) as client:
        recovered = client.get(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}",
            headers=_native(),
        )
        assert recovered.status_code == 200
        assert recovered.json()["team_run"]["state"] == "unknown"
        db = sqlite3.connect(crash_config.storage.database_path)
        try:
            live_state = db.execute(
                "SELECT state FROM team_node WHERE id = ?",
                (running["node_id"],),
            ).fetchone()[0]
            kept = db.execute(
                "SELECT state FROM team_node WHERE id = ?",
                (already_cancelled["node_id"],),
            ).fetchone()[0]
            assert live_state == "unknown"
            assert kept == "cancelled"
        finally:
            db.close()


def test_state_succeeded_requires_settled_children_in_one_transaction(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        promoted = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "running"},
        )
        assert promoted.status_code == 200
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        marked = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "succeeded", "parent_final_answer": "提前宣布成功"},
        )
        assert marked.status_code == 409
        assert marked.json()["error"]["code"] == "desktop_team_run_children_live"
        db = sqlite3.connect(config.storage.database_path)
        try:
            run_state = db.execute(
                "SELECT state FROM team_run WHERE id = ?", (team_run_id,)
            ).fetchone()[0]
            node_state = db.execute(
                "SELECT state FROM team_node WHERE id = ?", (created["node_id"],)
            ).fetchone()[0]
            assignment_state = db.execute(
                "SELECT state FROM team_assignment WHERE team_run_id = ? "
                "AND assignment_id = 'frontend-review'",
                (team_run_id,),
            ).fetchone()[0]
            assert run_state == "running"
            assert node_state == "running"
            assert assignment_state == "running"
        finally:
            db.close()
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{created['node_id']}/settle",
            headers=_native(),
            json=_settle_payload(created["invocation_id"]),
        )
        assert settled.status_code == 200
        _finish_plan(client, workspace_id, team_run_id)
        succeeded = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "succeeded", "parent_final_answer": "终稿"},
        )
        assert succeeded.status_code == 200
        assert succeeded.json()["team_run"]["state"] == "succeeded"


def test_state_quiet_terminals_refuse_live_children(tmp_path: Path) -> None:
    for state in ("failed", "unknown", "budget_exhausted", "cannot_complete"):
        config = _config(tmp_path / state)
        with TestClient(create_desktop_local_app(config)) as client:
            workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
            created = _create_running_node(client, workspace_id, team_run_id, provider_id)
            marked = client.post(
                f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
                headers=_native(),
                json={"state": state},
            )
            assert marked.status_code == 409, state
            assert marked.json()["error"]["code"] == "desktop_team_run_children_live", state
            db = sqlite3.connect(config.storage.database_path)
            try:
                run_state = db.execute(
                    "SELECT state FROM team_run WHERE id = ?", (team_run_id,)
                ).fetchone()[0]
                node_state = db.execute(
                    "SELECT state FROM team_node WHERE id = ?", (created["node_id"],)
                ).fetchone()[0]
                assert run_state == "preparing", state
                assert node_state == "running", state
            finally:
                db.close()


def test_state_cancelled_converges_residual_live_children(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        cancelled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "cancelled"},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["team_run"]["state"] == "cancelled"
        live_nodes, live_assignments = _live_residue_counts(
            config.storage.database_path, team_run_id
        )
        assert live_nodes == 0
        assert live_assignments == 0
        db = sqlite3.connect(config.storage.database_path)
        try:
            node_state = db.execute(
                "SELECT state FROM team_node WHERE id = ?", (created["node_id"],)
            ).fetchone()[0]
            assert node_state == "cancelled"
        finally:
            db.close()


def test_recovery_downgrades_succeeded_without_success_proof(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        promoted = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "running"},
        )
        assert promoted.status_code == 200
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        db = sqlite3.connect(config.storage.database_path)
        try:
            db.execute("UPDATE team_run SET state = 'succeeded' WHERE id = ?", (team_run_id,))
            db.commit()
        finally:
            db.close()
    with TestClient(create_desktop_local_app(config)) as client:
        recovered = client.get(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}",
            headers=_native(),
        )
        assert recovered.status_code == 200
        assert recovered.json()["team_run"]["state"] == "unknown"
        db = sqlite3.connect(config.storage.database_path)
        try:
            node_state = db.execute(
                "SELECT state FROM team_node WHERE id = ?", (created["node_id"],)
            ).fetchone()[0]
            assignment_state = db.execute(
                "SELECT state FROM team_assignment WHERE team_run_id = ? "
                "AND assignment_id = 'frontend-review'",
                (team_run_id,),
            ).fetchone()[0]
            assert node_state == "unknown"
            assert assignment_state == "blocked"
        finally:
            db.close()


def test_recovery_keeps_proven_success_intact(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        promoted = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "running"},
        )
        assert promoted.status_code == 200
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{created['node_id']}/settle",
            headers=_native(),
            json=_settle_payload(created["invocation_id"]),
        )
        assert settled.status_code == 200
        _finish_plan(client, workspace_id, team_run_id)
        succeeded = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "succeeded", "parent_final_answer": "终稿"},
        )
        assert succeeded.status_code == 200
    with TestClient(create_desktop_local_app(config)) as client:
        recovered = client.get(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}",
            headers=_native(),
        )
        assert recovered.status_code == 200
        assert recovered.json()["team_run"]["state"] == "succeeded"
        db = sqlite3.connect(config.storage.database_path)
        try:
            node_state = db.execute(
                "SELECT state FROM team_node WHERE id = ?", (created["node_id"],)
            ).fetchone()[0]
            assignment_state = db.execute(
                "SELECT state FROM team_assignment WHERE team_run_id = ? "
                "AND assignment_id = 'frontend-review'",
                (team_run_id,),
            ).fetchone()[0]
            assert node_state == "succeeded"
            assert assignment_state == "completed"
        finally:
            db.close()


def test_state_succeeded_requires_success_closure(tmp_path: Path) -> None:
    empty_config = _config(tmp_path / "empty")
    with TestClient(create_desktop_local_app(empty_config)) as client:
        workspace_id, conversation_id = _bootstrap_workspace(client)
        started = _start_run(client, workspace_id, conversation_id)
        assert started["status"] == 200
        team_run_id = started["body"]["team_run"]["id"]
        promoted = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "running"},
        )
        assert promoted.status_code == 200
        no_proof = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "succeeded", "parent_final_answer": "无凭据成功"},
        )
        assert no_proof.status_code == 409
        assert no_proof.json()["error"]["code"] == "desktop_team_success_closure_open"
        db = sqlite3.connect(empty_config.storage.database_path)
        try:
            run_state = db.execute(
                "SELECT state FROM team_run WHERE id = ?", (team_run_id,)
            ).fetchone()[0]
            assert run_state == "running"
        finally:
            db.close()

    failed_config = _config(tmp_path / "failed-child")
    with TestClient(create_desktop_local_app(failed_config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        promoted = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "running"},
        )
        assert promoted.status_code == 200
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        failed = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{created['node_id']}",
            headers=_native(),
            json={"state": "failed", "error_code": "desktop_provider_failed"},
        )
        assert failed.status_code == 200
        over_failed = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "succeeded", "parent_final_answer": "失败之上宣布成功"},
        )
        assert over_failed.status_code == 409
        assert over_failed.json()["error"]["code"] == "desktop_team_success_closure_open"

    collab_config = _config(tmp_path / "collab")
    with TestClient(create_desktop_local_app(collab_config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        promoted = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "running"},
        )
        assert promoted.status_code == 200
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{created['node_id']}/settle",
            headers=_native(),
            json={
                **_settle_payload(created["invocation_id"]),
                "status": "needs_collaboration",
                "collaboration_requests": [
                    {
                        "targetRoleId": "qa",
                        "question": "请补回归清单",
                        "reason": "需要覆盖取消路径",
                    }
                ],
            },
        )
        assert settled.status_code == 200
        no_answer = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "succeeded"},
        )
        assert no_answer.status_code == 409
        assert no_answer.json()["error"]["code"] == "desktop_team_success_closure_open"
        frozen = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "succeeded", "parent_final_answer": "带着待决协作成功"},
        )
        assert frozen.status_code == 409
        assert frozen.json()["error"]["code"] == "desktop_team_success_closure_open"
        db = sqlite3.connect(collab_config.storage.database_path)
        try:
            request_id = db.execute(
                "SELECT id FROM team_collaboration_request WHERE team_run_id = ?",
                (team_run_id,),
            ).fetchone()[0]
        finally:
            db.close()
        resolved = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/"
            f"collaboration-requests/{request_id}/resolve",
            headers=_native(),
            json={"parent_decision": "handle_self", "resolved_assignment_id": None},
        )
        assert resolved.status_code == 200, resolved.text
        _finish_plan(client, workspace_id, team_run_id)
        succeeded = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "succeeded", "parent_final_answer": "终稿"},
        )
        assert succeeded.status_code == 200, succeeded.text
        assert succeeded.json()["team_run"]["state"] == "succeeded"


def test_state_terminal_transition_from_terminal_is_conflict(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        promoted = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "running"},
        )
        assert promoted.status_code == 200
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{created['node_id']}/settle",
            headers=_native(),
            json=_settle_payload(created["invocation_id"]),
        )
        assert settled.status_code == 200
        _finish_plan(client, workspace_id, team_run_id)
        succeeded = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "succeeded", "parent_final_answer": "终稿"},
        )
        assert succeeded.status_code == 200
        downgrade = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "unknown"},
        )
        assert downgrade.status_code == 409
        assert downgrade.json()["error"]["code"] == "desktop_team_run_state_conflict"
        replay = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "succeeded", "parent_final_answer": "终稿"},
        )
        assert replay.status_code == 200
        assert replay.json()["team_run"]["state"] == "succeeded"


def test_state_succeeded_requires_terminal_plan_decision(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        promoted = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "running"},
        )
        assert promoted.status_code == 200
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{created['node_id']}/settle",
            headers=_native(),
            json=_settle_payload(created["invocation_id"]),
        )
        assert settled.status_code == 200
        premature = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "succeeded", "parent_final_answer": "父 Agent 尚未终结"},
        )
        assert premature.status_code == 409
        assert premature.json()["error"]["code"] == "desktop_team_success_closure_open"
        db = sqlite3.connect(config.storage.database_path)
        try:
            decision = db.execute(
                "SELECT decision FROM team_plan_revision WHERE id = ("
                "SELECT current_plan_revision_id FROM team_run WHERE id = ?)",
                (team_run_id,),
            ).fetchone()[0]
            assert decision == "delegate"
        finally:
            db.close()


def test_state_succeeded_cannot_launder_failed_child_via_finish(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        promoted = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "running"},
        )
        assert promoted.status_code == 200
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        failed = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{created['node_id']}",
            headers=_native(),
            json={"state": "failed", "error_code": "desktop_provider_failed"},
        )
        assert failed.status_code == 200
        _finish_plan(client, workspace_id, team_run_id)
        laundered = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "succeeded", "parent_final_answer": "空 finish 之后的伪成功"},
        )
        assert laundered.status_code == 409
        assert laundered.json()["error"]["code"] == "desktop_team_success_closure_open"
        db = sqlite3.connect(config.storage.database_path)
        try:
            db.execute("UPDATE team_run SET state = 'succeeded' WHERE id = ?", (team_run_id,))
            db.commit()
        finally:
            db.close()
    with TestClient(create_desktop_local_app(config)) as client:
        recovered = client.get(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}",
            headers=_native(),
        )
        assert recovered.status_code == 200
        assert recovered.json()["team_run"]["state"] == "unknown"


def test_state_succeeded_answer_directly_binds_validated_answer(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, conversation_id = _bootstrap_workspace(client)
        started = _start_run(client, workspace_id, conversation_id)
        assert started["status"] == 200
        team_run_id = started["body"]["team_run"]["id"]
        accepted = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/proposals",
            headers=_native(),
            json={
                "proposal": {
                    "decision": "answer_directly",
                    "answer": "validated answer A",
                    "reason": "direct",
                }
            },
        )
        assert accepted.status_code == 200
        assert accepted.json()["accepted"] is True
        promoted = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "running"},
        )
        assert promoted.status_code == 200
        diverged = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "succeeded", "parent_final_answer": "different answer B"},
        )
        assert diverged.status_code == 409
        assert diverged.json()["error"]["code"] == "desktop_team_success_closure_open"
        bound = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "succeeded", "parent_final_answer": "validated answer A"},
        )
        assert bound.status_code == 200, bound.text
        assert bound.json()["team_run"]["state"] == "succeeded"


def test_state_succeeded_replay_requires_identical_answer(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        promoted = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "running"},
        )
        assert promoted.status_code == 200
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{created['node_id']}/settle",
            headers=_native(),
            json=_settle_payload(created["invocation_id"]),
        )
        assert settled.status_code == 200
        _finish_plan(client, workspace_id, team_run_id)
        first = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "succeeded", "parent_final_answer": "original final"},
        )
        assert first.status_code == 200
        rewritten = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "succeeded", "parent_final_answer": "rewritten terminal final"},
        )
        assert rewritten.status_code == 409
        assert rewritten.json()["error"]["code"] == "desktop_team_success_answer_conflict"
        exact = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "succeeded", "parent_final_answer": "original final"},
        )
        assert exact.status_code == 200
        without_answer = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "succeeded"},
        )
        assert without_answer.status_code == 200
        db = sqlite3.connect(config.storage.database_path)
        try:
            stored = db.execute(
                "SELECT parent_final_answer FROM team_run WHERE id = ?", (team_run_id,)
            ).fetchone()[0]
            assert stored == "original final"
        finally:
            db.close()


def test_collaboration_write_requires_live_run_and_node_report_identity(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{created['node_id']}/settle",
            headers=_native(),
            json=_settle_payload(created["invocation_id"]),
        )
        assert settled.status_code == 200
        report_id = settled.json()["report"]["id"]
        payload = {
            "from_assignment_id": "frontend-review",
            "from_employee_role_id": "frontend",
            "target_role_id": "qa",
            "question": "请补充测试范围",
            "reason": "需要覆盖取消路径",
            "node_id": created["node_id"],
            "report_id": report_id,
        }
        ok = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/collaboration-requests",
            headers=_native(),
            json=payload,
        )
        assert ok.status_code == 200, ok.text
        wrong = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/collaboration-requests",
            headers=_native(),
            json={**payload, "node_id": "teamnode_" + "f" * 32},
        )
        assert wrong.status_code == 409
        assert wrong.json()["error"]["code"] == "desktop_team_collaboration_identity_mismatch"
        wrong_report = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/collaboration-requests",
            headers=_native(),
            json={**payload, "report_id": "teamrpt_" + "f" * 32},
        )
        assert wrong_report.status_code == 409
        assert (
            wrong_report.json()["error"]["code"] == "desktop_team_collaboration_identity_mismatch"
        )
        cancelled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/cancel",
            headers=_native(),
        )
        assert cancelled.status_code == 200
        terminal = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/collaboration-requests",
            headers=_native(),
            json=payload,
        )
        assert terminal.status_code == 409
        assert terminal.json()["error"]["code"] == "desktop_team_run_terminal"
        db = sqlite3.connect(config.storage.database_path)
        try:
            rows = db.execute(
                "SELECT COUNT(*) FROM team_collaboration_request WHERE team_run_id = ?",
                (team_run_id,),
            ).fetchone()[0]
            assert rows == 1
        finally:
            db.close()


def test_collaboration_resolve_requires_live_run_and_pending_cas(tmp_path: Path) -> None:
    config = _config(tmp_path / "live")
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{created['node_id']}/settle",
            headers=_native(),
            json=_settle_payload(created["invocation_id"]),
        )
        assert settled.status_code == 200
        report_id = settled.json()["report"]["id"]
        created_request = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/collaboration-requests",
            headers=_native(),
            json={
                "from_assignment_id": "frontend-review",
                "from_employee_role_id": "frontend",
                "target_role_id": "qa",
                "question": "请补充测试范围",
                "reason": "需要覆盖取消路径",
                "node_id": created["node_id"],
                "report_id": report_id,
            },
        )
        assert created_request.status_code == 200, created_request.text
        db = sqlite3.connect(config.storage.database_path)
        try:
            request_id = db.execute(
                "SELECT id FROM team_collaboration_request WHERE team_run_id = ?",
                (team_run_id,),
            ).fetchone()[0]
        finally:
            db.close()
        resolve_url = (
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/"
            f"collaboration-requests/{request_id}/resolve"
        )
        missing_assignment = client.post(
            resolve_url,
            headers=_native(),
            json={"parent_decision": "accept_start", "resolved_assignment_id": "no-such"},
        )
        assert missing_assignment.status_code == 404
        assert missing_assignment.json()["error"]["code"] == "desktop_team_assignment_not_found"
        resolved = client.post(
            resolve_url,
            headers=_native(),
            json={"parent_decision": "decline", "resolved_assignment_id": None},
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["collaboration_request"]["parent_decision"] == "decline"
        replay = client.post(
            resolve_url,
            headers=_native(),
            json={"parent_decision": "decline", "resolved_assignment_id": None},
        )
        assert replay.status_code == 200
        assert replay.json()["collaboration_request"]["parent_decision"] == "decline"
        conflict = client.post(
            resolve_url,
            headers=_native(),
            json={"parent_decision": "accept_start", "resolved_assignment_id": None},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "desktop_team_collaboration_resolve_conflict"

    terminal_config = _config(tmp_path / "terminal")
    with TestClient(create_desktop_local_app(terminal_config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{created['node_id']}/settle",
            headers=_native(),
            json=_settle_payload(created["invocation_id"]),
        )
        assert settled.status_code == 200
        report_id = settled.json()["report"]["id"]
        created_request = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/collaboration-requests",
            headers=_native(),
            json={
                "from_assignment_id": "frontend-review",
                "from_employee_role_id": "frontend",
                "target_role_id": "qa",
                "question": "请补充测试范围",
                "reason": "需要覆盖取消路径",
                "node_id": created["node_id"],
                "report_id": report_id,
            },
        )
        assert created_request.status_code == 200
        stopped = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/cancel",
            headers=_native(),
        )
        assert stopped.status_code == 200
        assert stopped.json()["team_run"]["state"] == "cancelled"
        db = sqlite3.connect(terminal_config.storage.database_path)
        try:
            request_id = db.execute(
                "SELECT id FROM team_collaboration_request WHERE team_run_id = ?",
                (team_run_id,),
            ).fetchone()[0]
        finally:
            db.close()
        late = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/"
            f"collaboration-requests/{request_id}/resolve",
            headers=_native(),
            json={"parent_decision": "decline", "resolved_assignment_id": None},
        )
        assert late.status_code == 409
        assert late.json()["error"]["code"] == "desktop_team_run_terminal"
        db = sqlite3.connect(terminal_config.storage.database_path)
        try:
            decision = db.execute(
                "SELECT parent_decision FROM team_collaboration_request WHERE id = ?",
                (request_id,),
            ).fetchone()[0]
            assert decision == "pending"
        finally:
            db.close()


def test_replan_requires_collaboration_decisions_coverage(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        promoted = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "running"},
        )
        assert promoted.status_code == 200
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{created['node_id']}/settle",
            headers=_native(),
            json={
                **_settle_payload(created["invocation_id"]),
                "status": "needs_collaboration",
                "collaboration_requests": [
                    {
                        "targetRoleId": "qa",
                        "question": "请补回归清单",
                        "reason": "需要覆盖取消路径",
                    }
                ],
            },
        )
        assert settled.status_code == 200
        db = sqlite3.connect(config.storage.database_path)
        try:
            request_id = db.execute(
                "SELECT id FROM team_collaboration_request WHERE team_run_id = ?",
                (team_run_id,),
            ).fetchone()[0]
        finally:
            db.close()
        proposals_url = f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/proposals"
        qa_wave: dict[str, object] = {
            "waveId": "wave-qa",
            "execution": "serial",
            "assignments": [_assignment("qa-review", "qa")],
        }
        undecided = client.post(
            proposals_url,
            headers=_native(),
            json={"proposal": {"decision": "continue", "nextWave": qa_wave}},
        )
        assert undecided.status_code == 200
        assert undecided.json()["accepted"] is False
        assert undecided.json()["validation_error_code"] == "desktop_team_collaboration_undecided"
        wrong_role = client.post(
            proposals_url,
            headers=_native(),
            json={
                "proposal": {
                    "decision": "continue",
                    "nextWave": {
                        "waveId": "wave-sec",
                        "execution": "serial",
                        "assignments": [_assignment("sec-audit", "security")],
                    },
                    "collaborationDecisions": [
                        {
                            "requestId": request_id,
                            "decision": "accept_start",
                            "resolvedAssignmentId": "sec-audit",
                        }
                    ],
                }
            },
        )
        assert wrong_role.status_code == 200
        assert wrong_role.json()["accepted"] is False
        assert (
            wrong_role.json()["validation_error_code"]
            == "desktop_team_collaboration_identity_mismatch"
        )
        accepted = client.post(
            proposals_url,
            headers=_native(),
            json={
                "proposal": {
                    "decision": "continue",
                    "nextWave": qa_wave,
                    "collaborationDecisions": [
                        {
                            "requestId": request_id,
                            "decision": "accept_start",
                            "resolvedAssignmentId": "qa-review",
                        }
                    ],
                }
            },
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["accepted"] is True
        resolved = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/"
            f"collaboration-requests/{request_id}/resolve",
            headers=_native(),
            json={"parent_decision": "accept_start", "resolved_assignment_id": "qa-review"},
        )
        assert resolved.status_code == 200, resolved.text
        finish_undecided = client.post(
            proposals_url,
            headers=_native(),
            json={"proposal": {"decision": "finish", "reason": "已完成"}},
        )
        assert finish_undecided.status_code == 200
        assert finish_undecided.json()["accepted"] is True


def test_collaboration_resolve_binds_decision_shape_role_and_plan(tmp_path: Path) -> None:
    config = _config(tmp_path / "shape")
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, conversation_id = _bootstrap_workspace(
            client, with_provider=True, base_url="http://127.0.0.1:9/v1"
        )
        listed = client.get("/desktop/v1/providers", headers=_native())
        provider_id = listed.json()["items"][0]["id"]
        started = _start_run(client, workspace_id, conversation_id)
        assert started["status"] == 200
        team_run_id = started["body"]["team_run"]["id"]
        accepted = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/proposals",
            headers=_native(),
            json={
                "proposal": _delegate(
                    [
                        _assignment(),
                        _assignment("qa-review", "qa", objective="补齐回归矩阵"),
                    ]
                )
            },
        )
        assert accepted.status_code == 200
        assert accepted.json()["accepted"] is True
        promoted = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "running"},
        )
        assert promoted.status_code == 200
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{created['node_id']}/settle",
            headers=_native(),
            json={
                **_settle_payload(created["invocation_id"]),
                "status": "needs_collaboration",
                "collaboration_requests": [
                    {
                        "targetRoleId": "qa",
                        "question": "请补回归清单",
                        "reason": "需要覆盖取消路径",
                    }
                ],
            },
        )
        assert settled.status_code == 200
        db = sqlite3.connect(config.storage.database_path)
        try:
            request_id = db.execute(
                "SELECT id FROM team_collaboration_request WHERE team_run_id = ?",
                (team_run_id,),
            ).fetchone()[0]
        finally:
            db.close()
        resolve_url = (
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/"
            f"collaboration-requests/{request_id}/resolve"
        )
        wrong_role = client.post(
            resolve_url,
            headers=_native(),
            json={"parent_decision": "merge_existing", "resolved_assignment_id": "frontend-review"},
        )
        assert wrong_role.status_code == 409
        assert wrong_role.json()["error"]["code"] == "desktop_team_collaboration_identity_mismatch"
        accept_without_assignment = client.post(
            resolve_url,
            headers=_native(),
            json={"parent_decision": "accept_start", "resolved_assignment_id": None},
        )
        assert accept_without_assignment.status_code == 400
        assert accept_without_assignment.json()["error"]["code"] == "desktop_native_input_invalid"
        decline_with_assignment = client.post(
            resolve_url,
            headers=_native(),
            json={"parent_decision": "decline", "resolved_assignment_id": "qa-review"},
        )
        assert decline_with_assignment.status_code == 400
        handle_self_with_assignment = client.post(
            resolve_url,
            headers=_native(),
            json={"parent_decision": "handle_self", "resolved_assignment_id": "qa-review"},
        )
        assert handle_self_with_assignment.status_code == 400
        accepted_start = client.post(
            resolve_url,
            headers=_native(),
            json={"parent_decision": "accept_start", "resolved_assignment_id": "qa-review"},
        )
        assert accepted_start.status_code == 200, accepted_start.text
        assert accepted_start.json()["collaboration_request"]["parent_decision"] == "accept_start"

    stale_config = _config(tmp_path / "stale-plan")
    with TestClient(create_desktop_local_app(stale_config)) as client:
        workspace_id, conversation_id = _bootstrap_workspace(
            client, with_provider=True, base_url="http://127.0.0.1:9/v1"
        )
        listed = client.get("/desktop/v1/providers", headers=_native())
        provider_id = listed.json()["items"][0]["id"]
        started = _start_run(client, workspace_id, conversation_id)
        assert started["status"] == 200
        team_run_id = started["body"]["team_run"]["id"]
        accepted = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/proposals",
            headers=_native(),
            json={"proposal": _delegate([_assignment("qa-review", "qa")])},
        )
        assert accepted.status_code == 200
        promoted = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
            headers=_native(),
            json={"state": "running"},
        )
        assert promoted.status_code == 200
        created = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes",
            headers=_native(),
            json={
                "assignment_id": "qa-review",
                "employee_role_id": "qa",
                "invocation_id": "invocation_" + "a" * 32,
                "wave_id": "wave-1",
                "node_epoch": 1,
                "send_epoch": 1,
                "provider_id": provider_id,
                "requested_model": "loopback-chat",
            },
        )
        assert created.status_code == 200, created.text
        node_id = created.json()["node"]["id"]
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{node_id}/settle",
            headers=_native(),
            json={
                **_settle_payload("invocation_" + "a" * 32),
                "assignment_id": "qa-review",
                "employee_role_id": "qa",
            },
        )
        assert settled.status_code == 200, settled.text
        report_id = settled.json()["report"]["id"]
        recorded = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/collaboration-requests",
            headers=_native(),
            json={
                "from_assignment_id": "qa-review",
                "from_employee_role_id": "qa",
                "target_role_id": "qa",
                "question": "请补充测试范围",
                "reason": "需要覆盖取消路径",
                "node_id": node_id,
                "report_id": report_id,
            },
        )
        assert recorded.status_code == 200, recorded.text
        db = sqlite3.connect(stale_config.storage.database_path)
        try:
            request_id = db.execute(
                "SELECT id FROM team_collaboration_request WHERE team_run_id = ?",
                (team_run_id,),
            ).fetchone()[0]
        finally:
            db.close()
        not_pending = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/"
            f"collaboration-requests/{request_id}/resolve",
            headers=_native(),
            json={"parent_decision": "accept_start", "resolved_assignment_id": "qa-review"},
        )
        assert not_pending.status_code == 409
        assert not_pending.json()["error"]["code"] == "desktop_team_collaboration_identity_mismatch"


def test_stop_missing_and_unknown_run_are_conflict_noops(tmp_path: Path) -> None:
    config = _config(tmp_path)
    missing_id = "teamrun_" + "0" * 32
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, conversation_id = _bootstrap_workspace(client)
        started = _start_run(client, workspace_id, conversation_id)
        assert started["status"] == 200
        unknown_id = started["body"]["team_run"]["id"]
    with TestClient(create_desktop_local_app(config)) as client:
        recovered = client.get(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{unknown_id}",
            headers=_native(),
        )
        assert recovered.json()["team_run"]["state"] == "unknown"
        live = _start_run(client, workspace_id, conversation_id)
        assert live["status"] == 200
        live_id = live["body"]["team_run"]["id"]
        missing = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{missing_id}/cancel",
            headers=_native(),
        )
        assert missing.status_code == 409
        assert missing.json()["error"]["code"] == "desktop_team_run_not_found"
        malformed = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/not-a-team-run/cancel",
            headers=_native(),
        )
        assert malformed.status_code == 409
        assert malformed.json()["error"]["code"] == "desktop_team_run_not_found"
        unknown = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{unknown_id}/cancel",
            headers=_native(),
        )
        assert unknown.status_code == 409
        assert unknown.json()["error"]["code"] == "desktop_team_run_unknown"
        still_unknown = client.get(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{unknown_id}",
            headers=_native(),
        )
        assert still_unknown.json()["team_run"]["state"] == "unknown"
        still_live = client.get(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{live_id}",
            headers=_native(),
        )
        assert still_live.json()["team_run"]["state"] == "preparing"


def test_report_replay_rejects_mutated_body_and_accepts_exact_match(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{created['node_id']}/settle",
            headers=_native(),
            json=_settle_payload(created["invocation_id"]),
        )
        assert settled.status_code == 200
        mutated = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/reports",
            headers=_native(),
            json=_report_body(created, report="被篡改的报告正文"),
        )
        assert mutated.status_code == 409
        assert mutated.json()["error"]["code"] == "desktop_team_report_replay_mismatch"
        status_mutated = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/reports",
            headers=_native(),
            json={**_report_body(created), "status": "blocked"},
        )
        assert status_mutated.status_code == 409
        assert status_mutated.json()["error"]["code"] == "desktop_team_report_replay_mismatch"
        exact = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/reports",
            headers=_native(),
            json=_report_body(created),
        )
        assert exact.status_code == 200
        mutated_requests = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/reports",
            headers=_native(),
            json={
                **_report_body(created),
                "collaboration_requests": [
                    {
                        "targetRoleId": "qa",
                        "question": "补一份回归清单",
                        "reason": "需要覆盖取消路径",
                    }
                ],
            },
        )
        assert mutated_requests.status_code == 409
        assert mutated_requests.json()["error"]["code"] == "desktop_team_report_replay_mismatch"
        db = sqlite3.connect(config.storage.database_path)
        try:
            reports = db.execute(
                "SELECT COUNT(*), MIN(report) FROM team_employee_report WHERE node_id = ?",
                (created["node_id"],),
            ).fetchone()
            assert reports[0] == 1
            assert reports[1] == "前端已完成桌面状态检查"
        finally:
            db.close()


def test_report_replay_compares_collaboration_digest_with_canonical_order(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        requests: list[dict[str, object]] = [
            {
                "targetRoleId": "qa",
                "question": "补一份回归清单",
                "reason": "需要覆盖取消路径",
            },
            {
                "targetRoleId": "backend",
                "question": "确认 SQLite 事务边界",
                "reason": "需要验证 CAS",
            },
        ]
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{created['node_id']}/settle",
            headers=_native(),
            json={**_settle_payload(created["invocation_id"]), "collaboration_requests": requests},
        )
        assert settled.status_code == 200, settled.text
        db = sqlite3.connect(config.storage.database_path)
        try:
            digest = db.execute(
                "SELECT collaboration_requests_sha256 FROM team_employee_report "
                "WHERE node_id = ?",
                (created["node_id"],),
            ).fetchone()[0]
        finally:
            db.close()
        assert digest is not None
        assert len(digest) == 64
        reports_url = f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/reports"
        exact = client.post(
            reports_url,
            headers=_native(),
            json={
                **_report_body(created),
                "collaboration_requests": requests,
            },
        )
        assert exact.status_code == 200, exact.text
        reordered = client.post(
            reports_url,
            headers=_native(),
            json={
                **_report_body(created),
                "collaboration_requests": [requests[1], requests[0]],
            },
        )
        assert reordered.status_code == 200, reordered.text
        mutated_question = client.post(
            reports_url,
            headers=_native(),
            json={
                **_report_body(created),
                "collaboration_requests": [
                    {**requests[0], "question": "换一个问题"},
                    requests[1],
                ],
            },
        )
        assert mutated_question.status_code == 409
        assert mutated_question.json()["error"]["code"] == "desktop_team_report_replay_mismatch"
        dropped = client.post(
            reports_url,
            headers=_native(),
            json={
                **_report_body(created),
                "collaboration_requests": [requests[0]],
            },
        )
        assert dropped.status_code == 409
        assert dropped.json()["error"]["code"] == "desktop_team_report_replay_mismatch"


def test_report_replay_legacy_null_digest_fails_closed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{created['node_id']}/settle",
            headers=_native(),
            json=_settle_payload(created["invocation_id"]),
        )
        assert settled.status_code == 200
        db = sqlite3.connect(config.storage.database_path)
        try:
            db.execute("DROP TRIGGER team_employee_report_immutable")
            db.execute(
                "UPDATE team_employee_report SET collaboration_requests_sha256 = NULL "
                "WHERE node_id = ?",
                (created["node_id"],),
            )
            db.execute(
                "CREATE TRIGGER team_employee_report_immutable "
                "BEFORE UPDATE ON team_employee_report "
                "BEGIN SELECT RAISE(ABORT, 'desktop_team_employee_report_immutable'); END"
            )
            db.commit()
        finally:
            db.close()
        replay = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/reports",
            headers=_native(),
            json=_report_body(created),
        )
        assert replay.status_code == 409
        assert replay.json()["error"]["code"] == "desktop_team_report_replay_legacy_unverifiable"


def test_report_replay_response_excludes_later_standalone_requests(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{created['node_id']}/settle",
            headers=_native(),
            json=_settle_payload(created["invocation_id"]),
        )
        assert settled.status_code == 200
        report_id = settled.json()["report"]["id"]
        later = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/collaboration-requests",
            headers=_native(),
            json={
                "from_assignment_id": "frontend-review",
                "from_employee_role_id": "frontend",
                "target_role_id": "qa",
                "question": "later-Q",
                "reason": "later-R",
                "node_id": created["node_id"],
                "report_id": report_id,
            },
        )
        assert later.status_code == 200, later.text
        replay = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/reports",
            headers=_native(),
            json=_report_body(created),
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["report"]["collaboration_requests"] == []


def test_standalone_collaboration_replay_is_idempotent(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{created['node_id']}/settle",
            headers=_native(),
            json=_settle_payload(created["invocation_id"]),
        )
        assert settled.status_code == 200
        report_id = settled.json()["report"]["id"]
        create_url = (
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/collaboration-requests"
        )
        payload = {
            "from_assignment_id": "frontend-review",
            "from_employee_role_id": "frontend",
            "target_role_id": "qa",
            "question": "请补充测试范围",
            "reason": "需要覆盖取消路径",
            "node_id": created["node_id"],
            "report_id": report_id,
        }
        first = client.post(create_url, headers=_native(), json=payload)
        assert first.status_code == 200, first.text
        second = client.post(create_url, headers=_native(), json=payload)
        assert second.status_code == 200, second.text
        assert (
            second.json()["collaboration_request"]["id"]
            == first.json()["collaboration_request"]["id"]
        )
        db = sqlite3.connect(config.storage.database_path)
        try:
            rows = db.execute(
                "SELECT COUNT(*) FROM team_collaboration_request WHERE team_run_id = ? "
                "AND question = ?",
                (team_run_id, "请补充测试范围"),
            ).fetchone()[0]
            assert rows == 1
        finally:
            db.close()


def test_report_replay_unbound_rows_fail_closed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{created['node_id']}/settle",
            headers=_native(),
            json={
                **_settle_payload(created["invocation_id"]),
                "collaboration_requests": [
                    {
                        "targetRoleId": "qa",
                        "question": "补一份回归清单",
                        "reason": "需要覆盖取消路径",
                    }
                ],
            },
        )
        assert settled.status_code == 200
        db = sqlite3.connect(config.storage.database_path)
        try:
            db.execute(
                "UPDATE team_collaboration_request SET report_id = NULL " "WHERE team_run_id = ?",
                (team_run_id,),
            )
            db.commit()
        finally:
            db.close()
        replay = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/reports",
            headers=_native(),
            json={
                **_report_body(created),
                "collaboration_requests": [
                    {
                        "targetRoleId": "qa",
                        "question": "补一份回归清单",
                        "reason": "需要覆盖取消路径",
                    }
                ],
            },
        )
        assert replay.status_code == 409
        assert replay.json()["error"]["code"] == "desktop_team_report_replay_legacy_unverifiable"


def test_report_settle_rejects_duplicate_collaboration_tuples(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        workspace_id, _conversation_id, team_run_id, provider_id = _prepare_assigned_run(client)
        created = _create_running_node(client, workspace_id, team_run_id, provider_id)
        duplicated: list[dict[str, object]] = [
            {
                "targetRoleId": "qa",
                "question": "补一份回归清单",
                "reason": "需要覆盖取消路径",
            },
            {
                "targetRoleId": "qa",
                "question": "补一份回归清单",
                "reason": "需要覆盖取消路径",
            },
        ]
        settled = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/"
            f"{created['node_id']}/settle",
            headers=_native(),
            json={
                **_settle_payload(created["invocation_id"]),
                "collaboration_requests": duplicated,
            },
        )
        assert settled.status_code == 400
        assert settled.json()["error"]["code"] == "desktop_team_collaboration_duplicate"
        db = sqlite3.connect(config.storage.database_path)
        try:
            rows = db.execute(
                "SELECT COUNT(*) FROM team_collaboration_request WHERE team_run_id = ?",
                (team_run_id,),
            ).fetchone()[0]
            assert rows == 0
        finally:
            db.close()
