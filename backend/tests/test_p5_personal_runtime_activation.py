"""P5 personal Runtime canary activation contract and ledger attacks."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from omnibase.production.personal_runtime_activation import (
    PersonalRuntimeCanaryConfig,
    PersonalRuntimeConfigurationError,
    PersonalRuntimeState,
    activate_personal_runtime_canary,
    kill_personal_runtime_canary,
    load_personal_runtime_canary_config,
    personal_runtime_status_binding_valid,
    read_personal_runtime_status,
    rollback_personal_runtime_canary,
)

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def _mapping() -> dict[str, object]:
    return {
        "agent_planner_enabled": False,
        "agent_version_id": "00000000-0000-0000-0000-000000000104",
        "canary_id": "00000000-0000-0000-0000-000000000100",
        "enterprise_approved_digest_present": False,
        "environment": "production",
        "external_side_effects": False,
        "invocation_mode": "no_tool",
        "max_canary_seconds": 3600,
        "max_concurrent_invocations": 1,
        "max_top_k": 5,
        "migration_0013_created": True,
        "migration_head": "0014",
        "multi_agent_enabled": False,
        "network": {"default_deny": True, "destinations": []},
        "owner_readiness": {
            "path": "deployment/production/personal-single-owner.example.json",
            "sha256": "a" * 64,
        },
        "owner_user_id": "00000000-0000-0000-0000-000000000103",
        "profile": "personal_single_owner",
        "schema_version": 1,
        "tenant_id": "00000000-0000-0000-0000-000000000101",
        "workspace_id": "00000000-0000-0000-0000-000000000102",
    }


def _config() -> PersonalRuntimeCanaryConfig:
    return PersonalRuntimeCanaryConfig.from_mapping(_mapping())


def _canonical_file(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _env_assignments(path: Path) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        assignments[name] = raw_value.split("#", 1)[0].strip()
    return assignments


def test_personal_canary_contract_and_plan_are_exact_and_deterministic() -> None:
    config = _config()
    plan = config.activation_plan()

    assert config.profile == "personal_single_owner"
    assert config.invocation_mode == "no_tool"
    assert config.network_default_deny is True
    assert config.network_destinations == ()
    assert plan.required_feature_gates == {
        "AGENT_RUNTIME_ENABLED": True,
        "AGENT_PLANNER_ENABLED": False,
        "MULTI_AGENT_ENABLED": False,
    }
    assert len(config.canonical_digest()) == 64
    assert plan.canonical_digest() == config.activation_plan().canonical_digest()


def test_repository_canary_keeps_historical_owner_evidence_separate_from_current_head() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    config = load_personal_runtime_canary_config(
        (repo_root / "deployment/production/personal-runtime-canary.example.json").resolve(),
        repo_root=repo_root,
    )
    readiness = json.loads(
        (repo_root / "deployment/production/personal-single-owner.example.json").read_text(
            encoding="utf-8"
        )
    )
    evidence = json.loads(
        (repo_root / "docs/evidence/p34-7/personal-owner-disposable-gate.json").read_text(
            encoding="utf-8"
        )
    )

    assert config.migration_head == "0014"
    assert readiness["migration_head"] == "0014"
    assert readiness["engineering_evidence"]["assertions"]["migration_head"] == "0013"
    assert evidence["migration_head"] == "0013"


def test_compose_personal_runtime_defaults_and_overlay_are_exact() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    base_compose = (repo_root / "docker-compose.yml").read_text(encoding="utf-8")
    overlay = (
        repo_root / "deployment/production/personal-runtime-canary.compose.example.yml"
    ).read_text(encoding="utf-8")
    env = _env_assignments(repo_root / ".env.example")

    assert env["ENV"] == "development"
    assert env["AGENT_RUNTIME_ENABLED"] == "false"
    assert env["AGENT_PLANNER_ENABLED"] == "false"
    assert env["MULTI_AGENT_ENABLED"] == "false"
    assert env["PERSONAL_RUNTIME_PROFILE"] == ""
    assert env["PERSONAL_RUNTIME_CANARY_CONFIG"] == ""
    assert env["PERSONAL_RUNTIME_STATE_DIR"] == ""
    assert env["PERSONAL_RUNTIME_READINESS_ROOT"] == ""

    for exact_default in (
        "AGENT_RUNTIME_ENABLED: ${AGENT_RUNTIME_ENABLED:-false}",
        "AGENT_PLANNER_ENABLED: ${AGENT_PLANNER_ENABLED:-false}",
        "MULTI_AGENT_ENABLED: ${MULTI_AGENT_ENABLED:-false}",
        "PERSONAL_RUNTIME_PROFILE: ${PERSONAL_RUNTIME_PROFILE:-}",
        "PERSONAL_RUNTIME_CANARY_CONFIG: ${PERSONAL_RUNTIME_CANARY_CONFIG:-}",
        "PERSONAL_RUNTIME_STATE_DIR: ${PERSONAL_RUNTIME_STATE_DIR:-}",
        "PERSONAL_RUNTIME_READINESS_ROOT: ${PERSONAL_RUNTIME_READINESS_ROOT:-}",
    ):
        assert exact_default in base_compose
    assert "/run/omnibase-personal/canary.json" not in base_compose
    assert "/run/omnibase-personal/state" not in base_compose

    for exact_overlay_value in (
        "ENV: production",
        'AGENT_RUNTIME_ENABLED: "true"',
        'AGENT_PLANNER_ENABLED: "false"',
        'MULTI_AGENT_ENABLED: "false"',
        "PERSONAL_RUNTIME_PROFILE: personal_single_owner",
        "PERSONAL_RUNTIME_CANARY_CONFIG: /run/omnibase-personal/canary.json",
        "PERSONAL_RUNTIME_STATE_DIR: /run/omnibase-personal/state",
        "PERSONAL_RUNTIME_READINESS_ROOT: /run/omnibase-personal/readiness-root",
        "target: /run/omnibase-personal/canary.json",
        "target: /run/omnibase-personal/state",
        "target: /run/omnibase-personal/readiness-root",
    ):
        assert exact_overlay_value in overlay
    assert overlay.count("read_only: true") == 3


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("profile",), "enterprise_governed", "personal_single_owner"),
        (("environment",), "development", "must be production"),
        (("invocation_mode",), "knowledge_search", "no_tool only"),
        (("external_side_effects",), True, "cannot carry side effects"),
        (("agent_planner_enabled",), True, "cannot carry side effects"),
        (("multi_agent_enabled",), True, "cannot carry side effects"),
        (("migration_0013_created",), False, "requires the current migration 0013"),
        (("enterprise_approved_digest_present",), True, "cannot carry side effects"),
        (("max_concurrent_invocations",), 2, "must be an integer"),
        (("network", "default_deny"), False, "default-deny"),
        (("network", "destinations"), ["gateway.read"], "no workload destinations"),
    ],
)
def test_unsafe_personal_canary_contracts_fail_closed(
    path: tuple[str, ...], value: object, message: str
) -> None:
    mapping = _mapping()
    target: dict[str, object] = mapping
    for segment in path[:-1]:
        nested = target[segment]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = value

    with pytest.raises(PersonalRuntimeConfigurationError, match=message):
        PersonalRuntimeCanaryConfig.from_mapping(mapping)


def test_unknown_config_field_is_rejected() -> None:
    mapping = _mapping()
    mapping["activation_allowed"] = True
    with pytest.raises(PersonalRuntimeConfigurationError, match="unknown"):
        PersonalRuntimeCanaryConfig.from_mapping(mapping)


def test_config_loader_requires_canonical_bytes(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical.json"
    _canonical_file(canonical, _mapping())
    loaded = load_personal_runtime_canary_config(
        canonical,
        verify_owner_readiness=False,
    )
    assert loaded.canonical_digest() == _config().canonical_digest()

    pretty = tmp_path / "pretty.json"
    pretty.write_text(json.dumps(_mapping(), indent=2) + "\n", encoding="utf-8")
    with pytest.raises(PersonalRuntimeConfigurationError, match="canonical JSON"):
        load_personal_runtime_canary_config(pretty, verify_owner_readiness=False)


def test_config_loader_rejects_symlink_before_resolution(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical.json"
    _canonical_file(canonical, _mapping())
    linked = tmp_path / "linked.json"
    linked.symlink_to(canonical)

    with pytest.raises(PersonalRuntimeConfigurationError, match="non-link"):
        load_personal_runtime_canary_config(linked, verify_owner_readiness=False)


def test_activate_status_and_expiry(tmp_path: Path) -> None:
    config = _config()
    state_dir = (tmp_path / "run-1").resolve()
    inactive = read_personal_runtime_status(state_dir, now=NOW)
    assert inactive.state is PersonalRuntimeState.INACTIVE
    assert state_dir.exists() is False

    active = activate_personal_runtime_canary(
        config,
        state_dir=state_dir,
        confirmed_plan_sha256=config.activation_plan().canonical_digest(),
        now=NOW,
    )
    assert active.state is PersonalRuntimeState.ACTIVE
    assert active.canary_id == config.canary_id
    assert active.config_sha256 == config.canonical_digest()
    assert active.events == 1

    expired = read_personal_runtime_status(
        state_dir,
        now=NOW + timedelta(seconds=config.max_canary_seconds),
    )
    assert expired.state is PersonalRuntimeState.EXPIRED
    assert expired.active is False


def test_future_activation_is_invalid_until_its_declared_start(tmp_path: Path) -> None:
    config = _config()
    state_dir = (tmp_path / "run-future").resolve()
    future = NOW + timedelta(hours=1)
    activate_personal_runtime_canary(
        config,
        state_dir=state_dir,
        confirmed_plan_sha256=config.activation_plan().canonical_digest(),
        now=future,
    )

    before = read_personal_runtime_status(state_dir, now=NOW)
    at_boundary = read_personal_runtime_status(state_dir, now=future)

    assert before.state is PersonalRuntimeState.INVALID
    assert before.active is False
    assert "future" in before.vetoes[0]
    assert at_boundary.state is PersonalRuntimeState.ACTIVE


def test_activation_requires_exact_plan_digest_and_empty_run_directory(tmp_path: Path) -> None:
    config = _config()
    state_dir = (tmp_path / "run-2").resolve()
    with pytest.raises(PersonalRuntimeConfigurationError, match="plan digest"):
        activate_personal_runtime_canary(
            config,
            state_dir=state_dir,
            confirmed_plan_sha256="0" * 64,
            now=NOW,
        )
    activate_personal_runtime_canary(
        config,
        state_dir=state_dir,
        confirmed_plan_sha256=config.activation_plan().canonical_digest(),
        now=NOW,
    )
    with pytest.raises(PersonalRuntimeConfigurationError, match="new empty"):
        activate_personal_runtime_canary(
            config,
            state_dir=state_dir,
            confirmed_plan_sha256=config.activation_plan().canonical_digest(),
            now=NOW,
        )


def test_rollback_is_terminal_and_hash_chained(tmp_path: Path) -> None:
    config = _config()
    state_dir = (tmp_path / "run-3").resolve()
    activate_personal_runtime_canary(
        config,
        state_dir=state_dir,
        confirmed_plan_sha256=config.activation_plan().canonical_digest(),
        now=NOW,
    )
    rolled_back = rollback_personal_runtime_canary(
        config,
        state_dir=state_dir,
        reason_code="operator_requested",
        now=NOW + timedelta(minutes=5),
    )
    assert rolled_back.state is PersonalRuntimeState.ROLLED_BACK
    assert rolled_back.terminal_reason == "operator_requested"
    assert rolled_back.events == 2
    with pytest.raises(PersonalRuntimeConfigurationError, match="requires an active"):
        rollback_personal_runtime_canary(
            config,
            state_dir=state_dir,
            reason_code="again",
            now=NOW + timedelta(minutes=6),
        )


def test_event_tamper_and_unknown_artifact_fail_closed(tmp_path: Path) -> None:
    config = _config()
    state_dir = (tmp_path / "run-4").resolve()
    activate_personal_runtime_canary(
        config,
        state_dir=state_dir,
        confirmed_plan_sha256=config.activation_plan().canonical_digest(),
        now=NOW,
    )
    event_path = state_dir / "000001-activate.json"
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    payload["workspace_id"] = config.workspace_id
    _canonical_file(event_path, payload)
    invalid = read_personal_runtime_status(state_dir, now=NOW)
    assert invalid.state is PersonalRuntimeState.INVALID
    assert invalid.vetoes

    clean_dir = (tmp_path / "run-5").resolve()
    clean_dir.mkdir()
    (clean_dir / "notes.txt").write_text("not an event", encoding="utf-8")
    unknown = read_personal_runtime_status(clean_dir, now=NOW)
    assert unknown.state is PersonalRuntimeState.INVALID


def test_noncanonical_event_bytes_fail_closed(tmp_path: Path) -> None:
    config = _config()
    state_dir = (tmp_path / "run-noncanonical").resolve()
    activate_personal_runtime_canary(
        config,
        state_dir=state_dir,
        confirmed_plan_sha256=config.activation_plan().canonical_digest(),
        now=NOW,
    )
    event_path = state_dir / "000001-activate.json"
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    event_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    status = read_personal_runtime_status(state_dir, now=NOW)

    assert status.state is PersonalRuntimeState.INVALID
    assert "canonical JSON" in status.vetoes[0]


def test_event_type_must_match_filename(tmp_path: Path) -> None:
    config = _config()
    state_dir = (tmp_path / "run-filename-binding").resolve()
    activate_personal_runtime_canary(
        config,
        state_dir=state_dir,
        confirmed_plan_sha256=config.activation_plan().canonical_digest(),
        now=NOW,
    )
    (state_dir / "000001-activate.json").rename(state_dir / "000001-rollback.json")

    status = read_personal_runtime_status(state_dir, now=NOW)

    assert status.state is PersonalRuntimeState.INVALID
    assert "filename" in status.vetoes[0]


def test_config_binding_rejects_extended_activation_window(tmp_path: Path) -> None:
    config = _config()
    state_dir = (tmp_path / "run-binding").resolve()
    activate_personal_runtime_canary(
        config,
        state_dir=state_dir,
        confirmed_plan_sha256=config.activation_plan().canonical_digest(),
        now=NOW,
    )
    event_path = state_dir / "000001-activate.json"
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    payload["expires_at"] = "2026-08-10T13:00:01Z"
    _canonical_file(event_path, payload)

    status = read_personal_runtime_status(state_dir, now=NOW)
    assert status.state is PersonalRuntimeState.ACTIVE
    assert personal_runtime_status_binding_valid(config, status) is False


def test_rollback_event_must_retain_activation_binding(tmp_path: Path) -> None:
    config = _config()
    state_dir = (tmp_path / "run-rollback-binding").resolve()
    activate_personal_runtime_canary(
        config,
        state_dir=state_dir,
        confirmed_plan_sha256=config.activation_plan().canonical_digest(),
        now=NOW,
    )
    rollback_personal_runtime_canary(
        config,
        state_dir=state_dir,
        reason_code="operator_requested",
        now=NOW + timedelta(minutes=1),
    )
    rollback_path = state_dir / "000002-rollback.json"
    payload = json.loads(rollback_path.read_text(encoding="utf-8"))
    payload["canary_id"] = "00000000-0000-0000-0000-000000000999"
    _canonical_file(rollback_path, payload)

    status = read_personal_runtime_status(state_dir, now=NOW)
    assert status.state is PersonalRuntimeState.INVALID
    assert "rollback binding drifted" in status.vetoes


def test_kill_switch_wins_even_when_event_ledger_is_corrupt(tmp_path: Path) -> None:
    config = _config()
    state_dir = (tmp_path / "run-6").resolve()
    activate_personal_runtime_canary(
        config,
        state_dir=state_dir,
        confirmed_plan_sha256=config.activation_plan().canonical_digest(),
        now=NOW,
    )
    (state_dir / "000001-activate.json").write_text("corrupt", encoding="utf-8")

    killed = kill_personal_runtime_canary(
        state_dir=state_dir,
        canary_id=config.canary_id,
        reason_code="emergency_stop",
        now=NOW + timedelta(seconds=1),
    )
    assert killed.state is PersonalRuntimeState.KILLED
    assert killed.terminal_reason == "emergency_stop"

    # Marker corruption must still fail closed as killed, never reopen Runtime.
    (state_dir / "KILL_SWITCH.json").write_text("corrupt", encoding="utf-8")
    still_killed = read_personal_runtime_status(state_dir, now=NOW)
    assert still_killed.state is PersonalRuntimeState.KILLED


def test_state_directory_must_be_absolute() -> None:
    status = read_personal_runtime_status(Path("relative-state"), now=NOW)
    assert status.state is PersonalRuntimeState.INVALID
    assert "absolute" in status.vetoes[0]
