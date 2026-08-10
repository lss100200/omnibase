"""Filesystem-only CLI tests for the personal Runtime canary controller."""

from __future__ import annotations

import json
from pathlib import Path

import manage_p5_personal_runtime as manager

CONFIG = manager.REPO_ROOT / "deployment" / "production" / "personal-runtime-canary.example.json"
CANARY_ID = "00000000-0000-0000-0000-000000000005"


def _invoke(capsys, *arguments: str) -> tuple[int, dict[str, object]]:
    exit_code = manager.main(list(arguments))
    output = capsys.readouterr().out.strip()
    return exit_code, json.loads(output)


def _config_args() -> tuple[str, ...]:
    return ("--config", str(CONFIG), "--repo-root", str(manager.REPO_ROOT))


def test_validate_and_plan_are_filesystem_only(capsys) -> None:
    validate_code, validate = _invoke(capsys, "validate", *_config_args())
    plan_code, plan = _invoke(capsys, "plan", *_config_args())

    assert validate_code == 0
    assert plan_code == 0
    assert validate["contract_valid"] is True
    assert validate["production_runtime_started"] is False
    assert plan["plan_sha256"] == validate["plan_sha256"]
    assert plan["plan"]["required_feature_gates"] == {
        "AGENT_PLANNER_ENABLED": False,
        "AGENT_RUNTIME_ENABLED": True,
        "MULTI_AGENT_ENABLED": False,
    }


def test_activate_status_and_rollback_preserve_one_terminal_chain(
    tmp_path: Path,
    capsys,
) -> None:
    state_dir = (tmp_path / "activation").resolve()
    _, plan = _invoke(capsys, "plan", *_config_args())
    plan_sha256 = str(plan["plan_sha256"])

    activate_code, activated = _invoke(
        capsys,
        "activate-canary",
        *_config_args(),
        "--state-dir",
        str(state_dir),
        "--confirm-plan-sha256",
        plan_sha256,
    )
    status_code, active = _invoke(
        capsys,
        "status",
        *_config_args(),
        "--state-dir",
        str(state_dir),
    )
    rollback_code, rolled_back = _invoke(
        capsys,
        "rollback",
        *_config_args(),
        "--state-dir",
        str(state_dir),
        "--reason-code",
        "operator_requested",
    )
    final_status_code, final_status = _invoke(
        capsys,
        "status",
        *_config_args(),
        "--state-dir",
        str(state_dir),
    )

    assert activate_code == 0
    assert activated["state"] == "active"
    assert status_code == 0
    assert active["binding_valid"] is True
    assert rollback_code == 0
    assert rolled_back["state"] == "rolled_back"
    assert final_status_code == 2
    assert final_status["state"] == "rolled_back"
    assert final_status["events"] == 2


def test_wrong_plan_digest_and_event_tamper_fail_closed(tmp_path: Path, capsys) -> None:
    state_dir = (tmp_path / "activation").resolve()
    wrong_code, wrong = _invoke(
        capsys,
        "activate-canary",
        *_config_args(),
        "--state-dir",
        str(state_dir),
        "--confirm-plan-sha256",
        "0" * 64,
    )
    assert wrong_code == 1
    assert wrong["state"] == "invalid/veto"
    assert not (state_dir / "000001-activate.json").exists()

    _, plan = _invoke(capsys, "plan", *_config_args())
    active_code, _ = _invoke(
        capsys,
        "activate-canary",
        *_config_args(),
        "--state-dir",
        str(state_dir),
        "--confirm-plan-sha256",
        str(plan["plan_sha256"]),
    )
    assert active_code == 0
    event_path = state_dir / "000001-activate.json"
    event_path.write_bytes(event_path.read_bytes().replace(b'"sequence":1', b'"sequence":2'))

    status_code, status = _invoke(
        capsys,
        "status",
        *_config_args(),
        "--state-dir",
        str(state_dir),
    )
    assert status_code == 1
    assert status["state"] == "invalid/veto"
    assert status["binding_valid"] is False


def test_kill_does_not_trust_config_or_corrupt_ledger(tmp_path: Path, capsys) -> None:
    state_dir = (tmp_path / "activation").resolve()
    state_dir.mkdir()
    (state_dir / "000001-activate.json").write_text("not-json", encoding="utf-8")

    kill_code, killed = _invoke(
        capsys,
        "kill",
        "--state-dir",
        str(state_dir),
        "--canary-id",
        CANARY_ID,
        "--reason-code",
        "emergency_operator_kill",
    )

    assert kill_code == 0
    assert killed["state"] == "killed"
    assert killed["active"] is False
    assert (state_dir / "KILL_SWITCH.json").is_file()


def test_state_directory_must_be_absolute(capsys) -> None:
    code, payload = _invoke(
        capsys,
        "status",
        *_config_args(),
        "--state-dir",
        "relative/state",
    )

    assert code == 1
    assert payload["state"] == "invalid/veto"
