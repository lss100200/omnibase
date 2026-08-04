"""P5.0 fail-closed Phase 5 admission gate tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from omnibase.production.composition import (
    AdmissionState,
    ConfigurationError,
    EvidenceStatus,
    GitSourceProvenance,
    SourceScope,
)
from omnibase.production.phase5_admission import (
    FEATURE_GATE_ENV_NAMES,
    FeatureGateConfigurationError,
    FeatureGateName,
    FeatureGateResolution,
    P347FormalState,
    Phase5AdmissionConfig,
    Phase5AdmissionGate,
    discover_migration_head,
    load_phase5_admission_config,
    parse_feature_gate,
    resolve_feature_gates,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "deployment" / "production" / "phase5-admission.example.json"

RUNTIME = FEATURE_GATE_ENV_NAMES["agent_runtime"]
PLANNER = FEATURE_GATE_ENV_NAMES["agent_planner"]
MULTI_AGENT = FEATURE_GATE_ENV_NAMES["multi_agent"]


def _digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping() -> dict[str, object]:
    return {
        "schema_version": 1,
        "phase": "P5.0",
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
                "id": "current_source_linux_runner_12_of_12",
                "status": "not_proven",
                "path": None,
                "sha256": None,
                "assertions": {},
                "required_for_activation": True,
            }
        ],
        "migration_head": {
            "directory": "backend/src/omnibase/migrations/versions",
            "expected_revision": "0009",
        },
        "openapi_snapshot": {
            "path": "sdk/contracts/p34-2-openapi.snapshot.json",
            "sha256": "2" * 64,
        },
        "sdk_contracts": {
            "python": {"path": "sdk/python/pyproject.toml", "version": "0.1.0"},
            "typescript": {"path": "sdk/typescript/package.json", "version": "0.1.0"},
        },
        "production_composition": {
            "path": "deployment/production/composition.example.json",
            "sha256": "3" * 64,
        },
        "runbook": {
            "path": "docs/runbooks/p34-7-overlay-sla.md",
            "version": "1",
            "sha256": "4" * 64,
        },
        "critical_veto": {"expected": 0},
    }


def _source(*, clean: bool = True) -> GitSourceProvenance:
    return GitSourceProvenance(
        git_commit="a" * 40,
        git_tree="b" * 40,
        remote_origin="https://github.com/lss100200/omnibase.git",
        clean=clean,
        dirty_paths=() if clean else (" M backend/src/omnibase/production/phase5_admission.py",),
        file_count=1,
        files=(("AGENTS.md", 1, "c" * 64),),
        manifest_sha256="d" * 64,
    )


def _write_synthetic_artifacts(tmp_path: Path) -> None:
    """Create the full P5.0 manifest artifact tree under ``tmp_path``."""
    (tmp_path / "docs" / "evidence" / "p34-7").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "evidence" / "p34-7" / "production-readiness-decision.md").write_text(
        "# P34.7 decision\nP34.7 production total Gate: BLOCKED / NOT_PROVEN\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "runbooks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "runbooks" / "p34-7-overlay-sla.md").write_text(
        "# P34.7 Overlay SLA\n", encoding="utf-8"
    )
    (tmp_path / "sdk" / "contracts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sdk" / "contracts" / "p34-2-openapi.snapshot.json").write_text(
        json.dumps({"openapi": "3.1.0", "info": {"version": "0.1.0"}}), encoding="utf-8"
    )
    (tmp_path / "sdk" / "python").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sdk" / "python" / "pyproject.toml").write_text(
        '[project]\nname = "omnibase-sdk"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (tmp_path / "sdk" / "typescript").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sdk" / "typescript" / "package.json").write_text(
        json.dumps({"name": "@omnibase/sdk", "version": "0.1.0"}), encoding="utf-8"
    )
    (tmp_path / "deployment" / "production").mkdir(parents=True, exist_ok=True)
    (tmp_path / "deployment" / "production" / "composition.example.json").write_text(
        json.dumps({"activation_requested": False}), encoding="utf-8"
    )
    versions = tmp_path / "backend" / "src" / "omnibase" / "migrations" / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    (versions / "0001_create_tenants_table.py").write_text(
        'revision: str = "0001"\ndown_revision: str | None = None\n', encoding="utf-8"
    )
    (versions / "0008_p34_5_sandbox_dispatch.py").write_text(
        'revision: str = "0008"\ndown_revision: str | None = "0001"\n', encoding="utf-8"
    )
    (versions / "0009_p34_6_workspace_data.py").write_text(
        'revision: str = "0009"\ndown_revision: str | None = "0008"\n', encoding="utf-8"
    )


def _synthetic_config(
    tmp_path: Path,
    *,
    activation_requested: bool = True,
    p34_7_state: str = "blocked/not_proven",
    include_passed_evidence: bool = True,
) -> Phase5AdmissionConfig:
    _write_synthetic_artifacts(tmp_path)
    mapping = _mapping()
    mapping["activation_requested"] = activation_requested
    mapping["p34_7"]["formal_state"] = p34_7_state
    mapping["p34_7"]["decision"]["sha256"] = _digest_file(
        tmp_path / "docs" / "evidence" / "p34-7" / "production-readiness-decision.md"
    )
    mapping["openapi_snapshot"]["sha256"] = _digest_file(
        tmp_path / "sdk" / "contracts" / "p34-2-openapi.snapshot.json"
    )
    mapping["production_composition"]["sha256"] = _digest_file(
        tmp_path / "deployment" / "production" / "composition.example.json"
    )
    mapping["runbook"]["sha256"] = _digest_file(
        tmp_path / "docs" / "runbooks" / "p34-7-overlay-sla.md"
    )
    if include_passed_evidence:
        evidence_path = tmp_path / "docs" / "evidence.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps(
                {
                    "passed": True,
                    "root_env_accessed": False,
                    "business_database_migrated": False,
                }
            ),
            encoding="utf-8",
        )
        mapping["evidence"] = [
            {
                "id": "synthetic_complete_phase5_gate",
                "status": "passed",
                "path": "docs/evidence.json",
                "sha256": _digest_file(evidence_path),
                "assertions": {
                    "passed": True,
                    "root_env_accessed": False,
                    "business_database_migrated": False,
                },
                "required_for_activation": True,
            }
        ]
    return Phase5AdmissionConfig.from_mapping(mapping)


# ---------------------------------------------------------------------------
# Feature gate parsing (independent, server-owned, fail-closed)
# ---------------------------------------------------------------------------


def test_missing_gate_values_resolve_to_false() -> None:
    gates = resolve_feature_gates({})
    assert gates == FeatureGateResolution(False, False, False)
    assert gates.any_enabled is False


def test_empty_gate_values_resolve_to_false() -> None:
    gates = resolve_feature_gates({RUNTIME: "", PLANNER: "", MULTI_AGENT: ""})
    assert gates == FeatureGateResolution(False, False, False)


@pytest.mark.parametrize("token", ["true", "false"])
def test_exact_lowercase_tokens_are_accepted(token: str) -> None:
    assert parse_feature_gate(token, gate=FeatureGateName.AGENT_RUNTIME) == (token == "true")


@pytest.mark.parametrize(
    "token",
    ["True", "TRUE", " true", "true ", "1", "yes", "on", "enabled", "0", "null", "True "],
)
def test_truthy_looking_tokens_fail_closed(token: str) -> None:
    with pytest.raises(FeatureGateConfigurationError):
        parse_feature_gate(token, gate=FeatureGateName.AGENT_RUNTIME)


def test_non_string_gate_values_fail_closed() -> None:
    with pytest.raises(FeatureGateConfigurationError, match="string or absent"):
        parse_feature_gate(True, gate=FeatureGateName.AGENT_RUNTIME)
    with pytest.raises(FeatureGateConfigurationError):
        resolve_feature_gates({RUNTIME: True})


def test_gates_resolve_independently_without_a_master_switch() -> None:
    gates = resolve_feature_gates({RUNTIME: "true"})
    assert gates == FeatureGateResolution(True, False, False)
    # No master switch exists: a foreign flag cannot open any gate.
    gates = resolve_feature_gates({RUNTIME: "true", "SOME_MASTER_SWITCH": "true"})
    assert gates == FeatureGateResolution(True, False, False)


def test_unknown_mapping_keys_are_ignored() -> None:
    gates = resolve_feature_gates({"UNRELATED_FLAG": "true", "SOME_MASTER_SWITCH": "true"})
    assert gates == FeatureGateResolution(False, False, False)


def test_planner_true_requires_runtime_true() -> None:
    with pytest.raises(FeatureGateConfigurationError, match="requires AGENT_RUNTIME_ENABLED"):
        resolve_feature_gates({PLANNER: "true"})


def test_multi_agent_true_requires_planner_and_runtime() -> None:
    with pytest.raises(FeatureGateConfigurationError, match="requires both"):
        resolve_feature_gates({RUNTIME: "true", MULTI_AGENT: "true"})
    with pytest.raises(FeatureGateConfigurationError, match="requires AGENT_RUNTIME_ENABLED"):
        resolve_feature_gates({PLANNER: "true", MULTI_AGENT: "true"})
    with pytest.raises(FeatureGateConfigurationError, match="requires both"):
        resolve_feature_gates({MULTI_AGENT: "true"})


def test_all_three_gates_can_resolve_true() -> None:
    gates = resolve_feature_gates({RUNTIME: "true", PLANNER: "true", MULTI_AGENT: "true"})
    assert gates == FeatureGateResolution(True, True, True)


# ---------------------------------------------------------------------------
# Admission contract parsing
# ---------------------------------------------------------------------------


def test_checked_in_contract_is_valid_but_explicitly_blocked() -> None:
    config = (
        load_phase5_admission_config(CONFIG_PATH)
        if CONFIG_PATH.exists()
        else Phase5AdmissionConfig.from_mapping(_mapping())
    )

    report = Phase5AdmissionGate(REPO_ROOT).validate_only(config)

    assert report.state is AdmissionState.BLOCKED
    assert report.activation_allowed is False
    assert report.feature_gates == FeatureGateResolution(False, False, False)
    assert any("P34.7 formal state is not ready" in item for item in report.blockers)
    assert any("current_source_linux_runner_12_of_12" in item for item in report.blockers)
    assert "Phase 5 admission remains explicitly disabled" in report.blockers


def test_contract_requires_every_gate_disabled() -> None:
    mapping = _mapping()
    mapping["feature_gates"]["agent_runtime_enabled"] = True

    with pytest.raises(ConfigurationError, match="every Phase 5 feature gate to be disabled"):
        Phase5AdmissionConfig.from_mapping(mapping)


def test_critical_veto_requirement_must_be_zero() -> None:
    mapping = _mapping()
    mapping["critical_veto"]["expected"] = 1

    with pytest.raises(ConfigurationError, match="exactly 0"):
        Phase5AdmissionConfig.from_mapping(mapping)


def test_p34_7_formal_state_is_a_closed_set() -> None:
    mapping = _mapping()
    mapping["p34_7"]["formal_state"] = "passed"

    with pytest.raises(ConfigurationError, match="invalid state"):
        Phase5AdmissionConfig.from_mapping(mapping)


def test_sdk_contracts_require_exactly_python_and_typescript() -> None:
    mapping = _mapping()
    del mapping["sdk_contracts"]["typescript"]

    with pytest.raises(ConfigurationError, match="exactly python and typescript"):
        Phase5AdmissionConfig.from_mapping(mapping)


def test_sealed_sha256_must_be_valid_hex() -> None:
    mapping = _mapping()
    mapping["openapi_snapshot"]["sha256"] = "not-a-digest"

    with pytest.raises(ConfigurationError, match="64-character hex"):
        Phase5AdmissionConfig.from_mapping(mapping)


def test_passed_evidence_cannot_omit_hash_or_assertions() -> None:
    mapping = _mapping()
    mapping["evidence"][0]["status"] = "passed"
    mapping["evidence"][0]["sha256"] = None
    mapping["evidence"][0]["assertions"] = {}

    with pytest.raises(ConfigurationError, match="requires a path, SHA-256 and assertions"):
        Phase5AdmissionConfig.from_mapping(mapping)


def test_root_env_cannot_enter_source_scope_or_evidence() -> None:
    mapping = _mapping()
    mapping["source"]["tracked_pathspecs"].append(".env")
    with pytest.raises(ConfigurationError, match=r"root \.env"):
        Phase5AdmissionConfig.from_mapping(mapping)

    mapping = _mapping()
    mapping["evidence"][0]["path"] = ".env"
    with pytest.raises(ConfigurationError, match=r"root \.env"):
        Phase5AdmissionConfig.from_mapping(mapping)


# ---------------------------------------------------------------------------
# Formal verification (synthetic manifests)
# ---------------------------------------------------------------------------


def test_complete_manifest_and_explicit_activation_can_be_admitted(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path, activation_requested=True, p34_7_state="ready")

    report = Phase5AdmissionGate(tmp_path).verify(config, source=_source())

    assert report.state is AdmissionState.READY
    assert report.activation_allowed is True
    assert report.blockers == ()
    assert report.vetoes == ()
    assert report.migration_head == "0009"


def test_dirty_checkout_is_a_veto_even_when_manifest_is_complete(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path, activation_requested=True, p34_7_state="ready")

    report = Phase5AdmissionGate(tmp_path).verify(config, source=_source(clean=False))

    assert report.state is AdmissionState.INVALID
    assert report.activation_allowed is False
    assert report.vetoes == ("Phase 5 admission requires a clean checkout",)


def test_evidence_digest_drift_is_a_veto(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path, p34_7_state="ready")
    reference = replace(config.evidence[0], sha256="0" * 64)
    config = replace(config, evidence=(reference,))

    report = Phase5AdmissionGate(tmp_path).verify(config, source=_source())

    assert report.state is AdmissionState.INVALID
    assert report.vetoes == ("synthetic_complete_phase5_gate: sealed evidence SHA-256 drifted",)


def test_evidence_assertion_drift_is_a_veto(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path, p34_7_state="ready")
    reference = replace(config.evidence[0], assertions=(("passed", False),))
    config = replace(config, evidence=(reference,))

    report = Phase5AdmissionGate(tmp_path).verify(config, source=_source())

    assert report.state is AdmissionState.INVALID
    assert report.vetoes == ("synthetic_complete_phase5_gate: evidence assertion failed: passed",)


def test_migration_head_drift_is_a_veto(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path, p34_7_state="ready")
    config = replace(
        config,
        migration_head=replace(config.migration_head, expected_revision="0008"),
    )

    report = Phase5AdmissionGate(tmp_path).verify(config, source=_source())

    assert report.state is AdmissionState.INVALID
    assert report.vetoes == ("evidence manifest: migration head is 0009, expected 0008",)


def test_multi_head_migration_chain_is_rejected(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path, p34_7_state="ready")
    versions = tmp_path / "backend" / "src" / "omnibase" / "migrations" / "versions"
    (versions / "0010_p5_0_admission.py").write_text(
        'revision: str = "0010"\ndown_revision: str | None = None\n', encoding="utf-8"
    )

    report = Phase5AdmissionGate(tmp_path).verify(config, source=_source())

    assert report.state is AdmissionState.INVALID
    assert any("migration chain has 2 heads" in veto for veto in report.vetoes)


def test_disconnected_migration_cycle_is_rejected(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path, p34_7_state="ready")
    versions = tmp_path / "backend" / "src" / "omnibase" / "migrations" / "versions"
    (versions / "cycle_a.py").write_text(
        'revision: str = "cycle_a"\ndown_revision: str | None = "cycle_b"\n', encoding="utf-8"
    )
    (versions / "cycle_b.py").write_text(
        'revision: str = "cycle_b"\ndown_revision: str | None = "cycle_a"\n', encoding="utf-8"
    )

    report = Phase5AdmissionGate(tmp_path).verify(config, source=_source())

    assert report.state is AdmissionState.INVALID
    assert any("disconnected or cyclic revisions" in veto for veto in report.vetoes)


def test_passed_evidence_symlink_to_root_env_is_rejected(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path, p34_7_state="ready")
    evidence_path = tmp_path / "docs" / "evidence.json"
    evidence_path.unlink()
    (tmp_path / ".env").write_text(
        json.dumps(
            {
                "passed": True,
                "root_env_accessed": False,
                "business_database_migrated": False,
            }
        ),
        encoding="utf-8",
    )
    try:
        evidence_path.symlink_to("../.env")
    except OSError:
        pytest.skip("symbolic links are unavailable on this host")

    report = Phase5AdmissionGate(tmp_path).verify(config, source=_source())

    assert report.state is AdmissionState.INVALID
    assert any("link or reparse point" in veto for veto in report.vetoes)


def test_openapi_snapshot_digest_drift_is_a_veto(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path, p34_7_state="ready")
    config = replace(
        config,
        openapi_snapshot=replace(config.openapi_snapshot, sha256="0" * 64),
    )

    report = Phase5AdmissionGate(tmp_path).verify(config, source=_source())

    assert report.state is AdmissionState.INVALID
    assert any("p34-2-openapi.snapshot.json" in veto for veto in report.vetoes)


def test_production_composition_digest_drift_is_a_veto(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path, p34_7_state="ready")
    config = replace(
        config,
        production_composition=replace(config.production_composition, sha256="0" * 64),
    )

    report = Phase5AdmissionGate(tmp_path).verify(config, source=_source())

    assert report.state is AdmissionState.INVALID
    assert any("composition.example.json" in veto for veto in report.vetoes)


def test_runbook_digest_drift_is_a_veto(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path, p34_7_state="ready")
    config = replace(config, runbook=replace(config.runbook, sha256="0" * 64))

    report = Phase5AdmissionGate(tmp_path).verify(config, source=_source())

    assert report.state is AdmissionState.INVALID
    assert any("runbook" in veto for veto in report.vetoes)


def test_p34_7_decision_digest_drift_is_a_veto(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path, p34_7_state="ready")
    config = replace(
        config,
        p34_7=replace(config.p34_7, decision=replace(config.p34_7.decision, sha256="0" * 64)),
    )

    report = Phase5AdmissionGate(tmp_path).verify(config, source=_source())

    assert report.state is AdmissionState.INVALID
    assert any("p34-7" in veto for veto in report.vetoes)


def test_sdk_version_drift_is_a_veto(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path, p34_7_state="ready")
    config = replace(
        config,
        sdk_contracts=(
            ("python", replace(config.sdk_contracts[0][1], version="9.9.9")),
            config.sdk_contracts[1],
        ),
    )

    report = Phase5AdmissionGate(tmp_path).verify(config, source=_source())

    assert report.state is AdmissionState.INVALID
    assert any(
        "python SDK contract version is 0.1.0, expected 9.9.9" in veto for veto in report.vetoes
    )


def test_all_gates_true_are_still_blocked_while_p34_7_is_not_ready(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path, activation_requested=True)

    report = Phase5AdmissionGate(tmp_path).verify(
        config,
        source=_source(),
        gate_values={RUNTIME: "true", PLANNER: "true", MULTI_AGENT: "true"},
    )

    assert report.state is AdmissionState.BLOCKED
    assert report.activation_allowed is False
    assert report.feature_gates == FeatureGateResolution(True, True, True)
    assert any("P34.7 formal state is not ready" in item for item in report.blockers)
    assert any("feature gates must remain disabled" in item for item in report.blockers)


def test_gate_dependency_conflict_is_a_veto(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path, p34_7_state="ready")

    report = Phase5AdmissionGate(tmp_path).verify(
        config, source=_source(), gate_values={PLANNER: "true"}
    )

    assert report.state is AdmissionState.INVALID
    assert any(veto.startswith("feature gates:") for veto in report.vetoes)


def test_not_proven_evidence_is_never_counted_as_passed(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path, p34_7_state="ready")
    pending = replace(
        config.evidence[0],
        status=EvidenceStatus.NOT_PROVEN,
        path=None,
        sha256=None,
        assertions=(),
    )
    config = replace(config, evidence=(pending,))

    report = Phase5AdmissionGate(tmp_path).verify(config, source=_source())

    assert report.state is AdmissionState.BLOCKED
    assert report.passed_evidence == ()
    assert report.blockers == ("synthetic_complete_phase5_gate: not_proven",)


def test_report_exposes_explicit_safety_negatives() -> None:
    config = (
        load_phase5_admission_config(CONFIG_PATH)
        if CONFIG_PATH.exists()
        else Phase5AdmissionConfig.from_mapping(_mapping())
    )
    report = Phase5AdmissionGate(REPO_ROOT).validate_only(config)

    payload = report.to_dict()

    assert payload["root_env_accessed"] is False
    assert payload["business_database_accessed"] is False
    assert payload["business_database_migrated"] is False
    assert payload["hostile_code_executed"] is False
    assert payload["phase5_runtime_activated"] is False


def test_migration_head_discovery_on_synthetic_chain(tmp_path: Path) -> None:
    _write_synthetic_artifacts(tmp_path)

    head = discover_migration_head(tmp_path, "backend/src/omnibase/migrations/versions")

    assert head == "0009"


# ---------------------------------------------------------------------------
# Repo-dependent checks (full public checkout mounted)
# ---------------------------------------------------------------------------


def test_formal_gate_keeps_missing_proofs_blocked() -> None:
    if not CONFIG_PATH.exists():
        pytest.skip("full public checkout is not mounted in the backend test image")
    config = load_phase5_admission_config(CONFIG_PATH)

    report = Phase5AdmissionGate(REPO_ROOT).verify(config, source=_source())

    assert report.state is AdmissionState.BLOCKED
    assert report.activation_allowed is False
    assert report.vetoes == ()
    assert report.passed_evidence == ()
    assert report.migration_head == "0010"
    assert any("P34.7 formal state is not ready" in item for item in report.blockers)
    assert any("core_runner_mtls_production_roundtrip" in item for item in report.blockers)
    assert any("two_real_member_overlay_derp_node_compromise" in item for item in report.blockers)


def test_no_agent_runtime_planner_or_executor_packages_exist() -> None:
    if not CONFIG_PATH.exists():
        pytest.skip("full public checkout is not mounted in the backend test image")
    for forbidden in ("agent_runtime", "planner", "executor", "multi_agent"):
        assert not (
            REPO_ROOT / "backend" / "src" / "omnibase" / forbidden
        ).exists(), f"P5.0 must not introduce a runtime package: {forbidden}"


def test_git_provenance_hashes_only_tracked_scope_and_ignores_root_env(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "P5.0 test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "core.autocrlf", "false"],
        check=True,
        capture_output=True,
    )
    (repo / ".gitignore").write_text(".env\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "entry.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / ".env").write_text("MUST_NOT_BE_READ=synthetic\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", ".gitignore", "src/entry.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "test source"],
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
    scope = SourceScope(
        expected_repository="https://github.com/lss100200/omnibase.git",
        tracked_pathspecs=("src",),
        require_clean_checkout=True,
    )

    config = _synthetic_config(tmp_path, p34_7_state="ready", include_passed_evidence=False)
    config = replace(
        config,
        source=scope,
        activation_requested=True,
        p34_7=replace(config.p34_7, formal_state=P347FormalState.READY),
    )

    report = Phase5AdmissionGate(repo).verify(config)

    assert report.source is not None
    assert report.source.clean is True
    assert all(path != ".env" for path, _, _ in report.source.files)
