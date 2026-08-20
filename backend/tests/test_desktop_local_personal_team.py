from __future__ import annotations

import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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
    client: TestClient, workspace_id: str, conversation_id: str, **budget: object
) -> dict[str, object]:
    payload = {
        "conversation_id": conversation_id,
        "task": "审查桌面端多 Agent 设计",
        "team_mode": True,
        "maximum_provider_calls": budget.get("maximum_provider_calls", 16),
        "maximum_wall_time_ms": budget.get("maximum_wall_time_ms", 600_000),
        "maximum_concurrent_calls": budget.get("maximum_concurrent_calls", 2),
        "maximum_input_characters": budget.get("maximum_input_characters", 16_384),
        "maximum_output_characters": budget.get("maximum_output_characters", 32_768),
    }
    response = client.post(
        f"/desktop/v1/workspaces/{workspace_id}/team-runs",
        headers=_native(),
        json=payload,
    )
    return {"status": response.status_code, "body": response.json()}


def test_schema_v3_has_team_tables_without_secret_columns(tmp_path: Path) -> None:
    with initialized_database(_config(tmp_path).storage) as connection:
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0] == DESKTOP_SCHEMA_VERSION == 4
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
        assert history[-2:] == [
            (3, "desktop_0003_personal_agent_team"),
            (4, "desktop_0004_personal_team_runtime"),
        ]


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
