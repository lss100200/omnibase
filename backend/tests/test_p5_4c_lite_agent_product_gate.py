"""Fail-closed unit tests for the P5.4C Lite Agent product disposable Gate.

The Gate must derive every claimed result from executed receipts or report it
as ``not_proven``: the parser/resolver/posture claims come from the sealed
probe stdout, the migration head from a file measurement, and the
root-env/business-database negatives from the recorded command vectors.  The
run directory must be preserved (``evidence_preserved``) so the sealed
evidence can be re-verified after the process exits, and the Gate must never
claim integration of the formal P5.4B composition.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

_RUNNER_RELATIVE = Path("scripts/production/run_p5_4c_lite_agent_product_disposable_gate.py")
RUNNER = next(
    (
        candidate
        for candidate in (
            Path(__file__).resolve().parents[2] / _RUNNER_RELATIVE,
            Path("/workspace") / _RUNNER_RELATIVE,
        )
        if candidate.is_file()
    ),
    None,
)
if RUNNER is None:
    pytest.skip("P5.4C Gate tests require a full-repository mount", allow_module_level=True)


def _load_runner() -> ModuleType:
    assert RUNNER is not None
    spec = importlib.util.spec_from_file_location("p5_4c_gate", RUNNER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_runner()

_PROBE_JSON = (
    '{"absent_off": true, "false_off": true, "true_on": true, '
    '"invalid_fail_closed": true, "live_posture_reflects_env": true, '
    '"modes": ["no_tool"], "formal_builder": "build_engineering_single_agent_executor", '
    '"formal_builder_integration": "not_integrated"}'
)


def _synthetic_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe_json: str = _PROBE_JSON,
) -> tuple[Path, dict]:
    evidence_root = tmp_path / "gate-p5-4c"
    monkeypatch.setattr(gate, "EVIDENCE_ROOT", evidence_root)
    run_id = "20260807T010203000000Z-abcdef123456"
    run_dir = evidence_root / run_id
    run_dir.mkdir(parents=True)
    manifest = gate._manifest()
    manifest_sha = gate._write_json(run_dir / "source-manifest.json", manifest)
    gate._write_bytes(run_dir / "source-manifest.sha256", f"{manifest_sha}\n".encode())
    command = gate._container_command("python", "-m", "pytest", gate.LITE_UNIT_TEST, "-q")
    unit_result = subprocess.CompletedProcess(command, 0, "20 passed in 1.23s\n")
    unit_record = gate._record_command(run_dir, "lite-unit-suite", command, unit_result)
    probe_command = gate._container_command("python", "-c", gate._PROBE_SOURCE)
    probe_result = subprocess.CompletedProcess(probe_command, 0, probe_json + "\n")
    probe_record = gate._record_command(run_dir, "lite-gate-probes", probe_command, probe_result)
    commands = [unit_record, probe_record]
    probe = gate._parse_probe(probe_result.stdout)
    claims = gate._derive_claims(probe, commands)
    measurements = {
        "lite_unit_summary": {"passed": 20, "skipped": 0},
        "probe_measurements": probe,
        "migration_head": gate.EXPECTED_MIGRATION_HEAD,
    }
    report = gate._write_report(
        run_dir,
        run_id=run_id,
        started_at="2026-08-07T01:02:03+00:00",
        passed=True,
        manifest=manifest,
        manifest_raw_sha=manifest_sha,
        commands=commands,
        measurements=measurements,
        claims=claims,
        cleanup={"files_removed": 0, "evidence_preserved": True},
        error=None,
    )
    return run_dir / "evidence.json", report


def _rewrite_evidence(path: Path, report: dict) -> None:
    digest = gate._write_json(path, report)
    gate._write_bytes(path.with_name("evidence.sha256"), f"{digest}\n".encode())


def test_source_closure_excludes_secrets_and_env() -> None:
    paths = set(gate.SOURCE_FILES)
    assert ".env" not in paths
    assert "backend/src/omnibase/agent_alpha/lite.py" in paths
    assert "backend/src/omnibase/agent_executor/engineering.py" in paths
    assert "backend/tests/test_p5_4c_lite_agent_product_gate.py" in paths
    assert "scripts/production/run_p5_4c_lite_agent_product_disposable_gate.py" in paths


def test_source_closure_seals_compose_and_frontend_gate_files() -> None:
    """Round-4 closure: every file that decides Compose Lite-flag wiring,
    frontend ``canInvoke`` and Gate admission must be sealed in the source
    manifest."""
    paths = set(gate.SOURCE_FILES)
    for required in (
        "docker-compose.yml",
        ".env.example",
        "frontend/lib/lite-gate.ts",
        "frontend/lib/lite-gate.test.ts",
        "frontend/app/(dashboard)/agents/page.tsx",
        "frontend/lib/api.ts",
        "docs/phase-5-lite-agent-product-loop.md",
        "docs/maintainers/maintenance-map.json",
        "docs/maintainers/security-invariants.md",
        "deployment/production/phase5-typed-executor.example.json",
    ):
        assert required in paths, f"source closure is missing {required}"


def test_source_closure_covers_maintenance_map_authoritative_sources() -> None:
    """Authoritative sources declared by the maintenance map must not be
    missing from the Gate closure: the lite-agent-product-loop module
    source_paths and the INV-051 invariant source_paths must be a subset of
    ``SOURCE_FILES``."""
    map_path = gate.REPO_ROOT / "docs/maintainers/maintenance-map.json"
    assert map_path.is_file()
    maintenance_map = json.loads(map_path.read_text(encoding="utf-8"))
    module = next(
        item for item in maintenance_map["modules"] if item["id"] == "lite-agent-product-loop"
    )
    invariant = next(item for item in maintenance_map["invariants"] if item["id"] == "INV-051")
    declared = set(module["source_paths"]) | set(invariant["source_paths"])
    missing = sorted(declared - set(gate.SOURCE_FILES))
    assert not missing, f"maintenance-map authoritative sources missing from closure: {missing}"


def test_source_closure_files_all_exist() -> None:
    for relative in gate.SOURCE_FILES:
        path = gate.REPO_ROOT / relative
        assert path.is_file(), f"sealed source path does not exist: {relative}"
        assert not path.is_symlink(), f"sealed source path is a symlink: {relative}"


def test_container_command_uses_env_file_and_closed_gates() -> None:
    command = gate._container_command("python", "-m", "pytest")
    assert command[:4] == ["docker", "compose", "--env-file", str(gate.ENV_FILE)]
    assert "run" in command
    assert "--rm" in command
    assert "--no-deps" in command
    assert "AGENT_LITE_ENGINEERING_ENABLED=false" in command
    assert "P5_4B_ENGINEERING_ENABLED=false" in command
    assert "AGENT_RUNTIME_ENABLED=false" in command
    assert "AGENT_PLANNER_ENABLED=false" in command
    assert "MULTI_AGENT_ENABLED=false" in command
    # No root .env leak through the workload command.
    assert all(item != ".env" for item in command)


def test_manifest_seals_only_declared_regular_files() -> None:
    manifest = gate._manifest()
    assert manifest["schema_version"] == 1
    assert manifest["file_count"] == len(gate.SOURCE_FILES)
    for relative, metadata in manifest["files"].items():
        assert relative in gate.SOURCE_FILES
        assert metadata["size"] > 0
        assert len(metadata["sha256"]) == 64


def test_test_summary_parser_requires_passed_count() -> None:
    assert gate._parse_test_summary("14 passed in 6.77s\n") == {"passed": 14, "skipped": 0}
    assert gate._parse_test_summary("14 passed, 2 skipped in 6.77s\n") == {
        "passed": 14,
        "skipped": 2,
    }
    with pytest.raises(RuntimeError):
        gate._parse_test_summary("no summary here")


def test_probe_parser_requires_complete_bool_receipt() -> None:
    probe = gate._parse_probe(_PROBE_JSON)
    assert probe["absent_off"] is True
    assert probe["true_on"] is True
    assert probe["modes"] == ["no_tool"]
    with pytest.raises(RuntimeError, match="JSON not found"):
        gate._parse_probe("no json here")
    with pytest.raises(RuntimeError, match="field"):
        gate._parse_probe(_PROBE_JSON.replace('"true_on": true', '"true_on": "yes"'))


def test_receipt_derivations_are_computed_not_hardcoded() -> None:
    probe = gate._parse_probe(_PROBE_JSON)
    commands = [
        {"command": ["docker", "compose", "--env-file", "x.env.example", "run"]},
        {"command": ["python", "-m", "pytest", "tests/test_p5_4c_lite_gate.py", "-q"]},
    ]
    claims = gate._derive_claims(probe, commands)
    assert claims["lite_gate_default_off"] is True
    assert claims["runtime_env_resolver_true_on"] is True
    assert claims["knowledge_search_read_only_not_supported"] is True
    assert claims["formal_builder_named"] is True
    assert claims["formal_builder_integration"] == "not_proven"
    assert claims["formal_builder_posture_not_integrated"] is True
    assert claims["root_env_accessed"] is False
    assert claims["business_database_accessed"] is False
    assert claims["business_database_migrated"] is False

    # A command that carries the root .env or a database tool flips the
    # receipt-derived negative claims.
    dirty = [
        {"command": ["docker", "run", ".env"]},
        {"command": ["psql", "-c", "select 1"]},
    ]
    claims = gate._derive_claims(probe, dirty)
    assert claims["root_env_accessed"] is True
    assert claims["business_database_accessed"] is True


def test_synthetic_sealed_run_verifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence, _ = _synthetic_run(tmp_path, monkeypatch)
    gate._verify(evidence)


def _probe_variant(**changes: object) -> str:
    """Build a probe receipt JSON with one or more fields changed."""
    probe = json.loads(_PROBE_JSON)
    probe.update(changes)
    return json.dumps(probe, sort_keys=True)


@pytest.mark.parametrize(
    "variant",
    [
        {"absent_off": False},
        {"false_off": False},
        {"true_on": False},
        {"invalid_fail_closed": False},
        {"live_posture_reflects_env": False},
        {"modes": ["knowledge_search_read_only"]},
        {"modes": ["no_tool", "knowledge_search_read_only"]},
        {"formal_builder": "some_other_builder"},
    ],
)
def test_verify_reexecutes_admission_decision_rejects_drifted_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variant: dict[str, object],
) -> None:
    """Fix-3: --verify-evidence must re-execute the admission closed-set
    decision.  A probe that honestly reports true_on=false,
    invalid_fail_closed=false, live_posture=false, mode drift or builder-name
    drift still exits 0; the evidence must be REJECTED, never verified."""
    evidence, _ = _synthetic_run(tmp_path, monkeypatch, probe_json=_probe_variant(**variant))
    with pytest.raises(RuntimeError, match="admission expectation mismatch"):
        gate._verify(evidence)


def test_verify_rejects_mode_drift_in_claim_even_if_report_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A report that self-consistently records no_tool-only=false must fail the
    admission decision, not merely the report-vs-receipt equality check."""
    evidence, _ = _synthetic_run(
        tmp_path,
        monkeypatch,
        probe_json=_probe_variant(modes=["no_tool", "knowledge_search_read_only"]),
    )
    report = json.loads(evidence.read_bytes())
    assert report["knowledge_search_read_only_not_supported"] is False
    with pytest.raises(RuntimeError, match="admission expectation mismatch"):
        gate._verify(evidence)


def test_gate_records_probe_builder_integration_honestly_and_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-4: the Gate must NOT unconditionally discard the probe's
    formal_builder_integration and rewrite it to not_proven.  The probe result
    is recorded honestly — when the probe reports 'integrated', the report
    claims 'integrated' — and the admission decision rejects the evidence
    instead of verifying it."""
    evidence, report = _synthetic_run(
        tmp_path,
        monkeypatch,
        probe_json=_probe_variant(formal_builder_integration="integrated"),
    )
    assert report["formal_builder_integration"] == "integrated"
    assert report["formal_builder_posture_not_integrated"] is False
    with pytest.raises(RuntimeError, match="admission expectation mismatch"):
        gate._verify(evidence)


@pytest.mark.parametrize(
    "token",
    [
        "integrated",
        "enabled",
        "available",
        "selectable",
        "",
        "not_proven",
        "unknown_token",
        "TRUE",
        "1",
    ],
)
def test_probe_builder_integration_token_matrix_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    """Round-4: any probe formal_builder_integration token other than the
    genuine ``not_integrated`` posture must make ``--run`` produce
    passed=false and ``--verify-evidence`` reject.  The token is still
    recorded honestly in the report (never rewritten)."""
    evidence, report = _synthetic_run(
        tmp_path,
        monkeypatch,
        probe_json=_probe_variant(formal_builder_integration=token),
    )
    assert report["formal_builder_integration"] == token
    assert report["formal_builder_posture_not_integrated"] is False
    # The admission decision is what rejects: the run's own claim derivation
    # would flag the mismatch (passed=false) and --verify-evidence re-executes
    # the same decision.
    probe = gate._parse_probe(_probe_variant(formal_builder_integration=token))
    claims = gate._derive_claims(probe, report["commands"])
    mismatch = gate._admission_mismatch(claims, production_runtime_activated=False)
    assert mismatch is not None
    assert "formal_builder" in mismatch
    with pytest.raises(RuntimeError, match="admission expectation mismatch"):
        gate._verify(evidence)


@pytest.mark.parametrize(
    "drift",
    [
        ("different test target", "tests/test_some_other_test.py"),
        ("dropped closed flag", None),
        ("different env file", "other.env.example"),
        ("extra argument", None),
    ],
)
def test_verify_rejects_command_vector_drift_even_with_exit_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: tuple[str, str | None],
) -> None:
    """Fix-4: the verifier validates the EXACT argv template of each command —
    explicit .env.example, closed production flags and exact test target — not
    just the command key and return code.  A drifted vector that still exited
    0 must be rejected."""
    label, replacement = drift
    evidence, report = _synthetic_run(tmp_path, monkeypatch)
    vector = list(report["commands"][0]["command"])
    if label == "different test target":
        vector[vector.index(gate.LITE_UNIT_TEST)] = replacement  # type: ignore[arg-type]
    elif label == "dropped closed flag":
        vector.remove("AGENT_RUNTIME_ENABLED=false")
    elif label == "different env file":
        vector[vector.index(str(gate.ENV_FILE))] = replacement  # type: ignore[arg-type]
    elif label == "extra argument":
        vector.append("--no-header")
    report["commands"][0]["command"] = vector
    _rewrite_evidence(evidence, report)
    with pytest.raises(RuntimeError, match="does not match the exact closed template"):
        gate._verify(evidence)


def test_verify_rejects_root_env_in_command_vector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence, report = _synthetic_run(tmp_path, monkeypatch)
    vector = list(report["commands"][0]["command"])
    vector.append(".env")
    report["commands"][0]["command"] = vector
    _rewrite_evidence(evidence, report)
    # The root .env part both breaks the exact template and flips the
    # receipt-derived root_env_accessed claim; either rejection is correct.
    with pytest.raises(RuntimeError, match="does not match the exact closed template|root \\.env"):
        gate._verify(evidence)


# ---------------------------------------------------------------------------
# Round-4: the verifier must strictly parse the commands/*.exitcode sidecars
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        "abc\n",
        "",
        "0\n1\n",
        "1\n0\n",
        "0\n\n",
        "1.0\n",
        "0x0\n",
        "0",
        " 0\n",
        "-1\n",
        "0 ",
    ],
)
def test_verify_rejects_malformed_exitcode_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    """A sidecar that is not exactly one decimal exit code must be rejected."""
    evidence, _ = _synthetic_run(tmp_path, monkeypatch)
    sidecar = evidence.parent / "commands" / "lite-unit-suite.exitcode"
    sidecar.write_text(content, encoding="utf-8")
    with pytest.raises(RuntimeError, match="exitcode sidecar"):
        gate._verify(evidence)


def test_verify_rejects_exitcode_sidecar_returncode_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0/1 drift: the sidecar must equal the receipt returncode exactly."""
    evidence, _ = _synthetic_run(tmp_path, monkeypatch)
    sidecar = evidence.parent / "commands" / "lite-unit-suite.exitcode"
    sidecar.write_text("1\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="exitcode sidecar drift"):
        gate._verify(evidence)


def test_verify_rejects_missing_exitcode_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence, _ = _synthetic_run(tmp_path, monkeypatch)
    sidecar = evidence.parent / "commands" / "lite-unit-suite.exitcode"
    sidecar.unlink()
    with pytest.raises(RuntimeError, match="exitcode sidecar is missing"):
        gate._verify(evidence)


def test_verify_rejects_receipt_returncode_not_an_integer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The receipt returncode must be a strict JSON integer."""
    evidence, report = _synthetic_run(tmp_path, monkeypatch)
    report["commands"][0]["returncode"] = "0"
    _rewrite_evidence(evidence, report)
    with pytest.raises(RuntimeError, match="returncode is invalid|did not prove success"):
        gate._verify(evidence)


def test_verify_rejects_exitcode_path_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence, report = _synthetic_run(tmp_path, monkeypatch)
    report["commands"][0]["exitcode"] = "../outside.exitcode"
    _rewrite_evidence(evidence, report)
    with pytest.raises(RuntimeError, match="escaped run directory"):
        gate._verify(evidence)


def test_parse_exitcode_sidecar_strict_grammar() -> None:
    assert gate._parse_exitcode_sidecar("0\n") == 0
    assert gate._parse_exitcode_sidecar("127\n") == 127
    for bad in ("", "abc\n", "0\n1\n", "0\n\n", "1.0\n", "-1\n", "0", "0\n1"):
        with pytest.raises(RuntimeError, match="exactly one decimal exit code"):
            gate._parse_exitcode_sidecar(bad)


def test_admission_decision_is_shared_between_run_and_verify() -> None:
    """The admission closed-set decision is a single function; the happy probe
    passes it and every drifted probe fails it."""
    happy = gate._parse_probe(_PROBE_JSON)
    commands = [
        {"command": ["docker", "compose", "--env-file", "x.env.example", "run"]},
        {"command": ["python", "-m", "pytest", "tests/test_p5_4c_lite_gate.py", "-q"]},
    ]
    happy_claims = gate._derive_claims(happy, commands)
    assert gate._admission_mismatch(happy_claims, production_runtime_activated=False) is None
    assert (
        gate._admission_mismatch(happy_claims, production_runtime_activated=True)
        == "production_runtime_activated=True (expected False)"
    )
    drifted = gate._parse_probe(_probe_variant(true_on=False))
    drifted_claims = gate._derive_claims(drifted, commands)
    mismatch = gate._admission_mismatch(drifted_claims, production_runtime_activated=False)
    assert mismatch is not None
    assert "runtime_env_resolver_true_on" in mismatch


def test_integrity_receipt_is_self_contained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix-5: the evidence is a self-contained run-scoped integrity receipt.
    It must never claim external authenticity and must not name an independent
    trust anchor; production stays not_proven."""
    evidence, report = _synthetic_run(tmp_path, monkeypatch)
    receipt = report["integrity_receipt"]
    assert receipt["scope"] == "run-scoped byte integrity only"
    assert receipt["external_authenticity"] is False
    assert receipt["trust_anchor"] is None
    assert "no external" in receipt["wording"]
    assert report["production_runtime_activated"] is False
    gate._verify(evidence)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("passed", False),
        ("migration_head", "0011"),
        ("production_runtime_activated", True),
        ("evidence_preserved", False),
        ("lite_gate_default_off", False),
        ("runtime_env_resolver_absent_off", False),
        ("runtime_env_resolver_false_off", False),
        ("runtime_env_resolver_true_on", False),
        ("runtime_env_resolver_invalid_fail_closed", False),
        ("live_posture_reflects_env", False),
        ("knowledge_search_read_only_not_supported", False),
        ("formal_builder_named", False),
        ("formal_builder_integration", "integrated"),
        ("formal_builder_posture_not_integrated", False),
        ("root_env_accessed", True),
        ("business_database_accessed", True),
        ("business_database_migrated", True),
        (
            "feature_gates",
            {
                "agent_runtime_enabled": True,
                "agent_planner_enabled": False,
                "multi_agent_enabled": False,
            },
        ),
    ],
)
def test_report_claim_tamper_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    evidence, report = _synthetic_run(tmp_path, monkeypatch)
    report[field] = value
    _rewrite_evidence(evidence, report)
    with pytest.raises(RuntimeError):
        gate._verify(evidence)


def test_command_failure_and_sidecar_tamper_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence, report = _synthetic_run(tmp_path, monkeypatch)
    report["commands"][0]["returncode"] = 1
    _rewrite_evidence(evidence, report)
    with pytest.raises(RuntimeError, match="did not prove success"):
        gate._verify(evidence)

    evidence, _ = _synthetic_run(tmp_path / "second", monkeypatch)
    stdout = evidence.parent / "commands" / "lite-unit-suite.stdout"
    stdout.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="stdout digest mismatch|artifact digest mismatch"):
        gate._verify(evidence)


def test_probe_receipt_tamper_fails_claim_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changing the executed probe bytes must change the derived claims."""
    evidence, _ = _synthetic_run(tmp_path, monkeypatch)
    probe_stdout = evidence.parent / "commands" / "lite-gate-probes.stdout"
    probe_stdout.write_text(
        _PROBE_JSON.replace('"true_on": true', '"true_on": false') + "\n", encoding="utf-8"
    )
    # The tampered bytes are caught either by the artifact digest check or by
    # the claim re-derivation from the sealed probe receipt.
    with pytest.raises(
        RuntimeError,
        match="claim .* does not match|stdout digest mismatch|artifact digest mismatch",
    ):
        gate._verify(evidence)


def test_command_set_is_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence, report = _synthetic_run(tmp_path, monkeypatch)
    report["commands"] = report["commands"][:1]
    _rewrite_evidence(evidence, report)
    with pytest.raises(RuntimeError, match="command receipt set"):
        gate._verify(evidence)


def test_source_and_artifact_seals_reject_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence, _ = _synthetic_run(tmp_path, monkeypatch)
    source_hash = evidence.parent / "source-manifest.sha256"
    source_hash.write_text("0" * 64 + "\n", encoding="ascii")
    with pytest.raises(RuntimeError, match="source manifest raw-byte digest mismatch"):
        gate._verify(evidence)

    evidence, _ = _synthetic_run(tmp_path / "second", monkeypatch)
    artifact = evidence.parent / "evidence.md"
    artifact.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="artifact digest mismatch"):
        gate._verify(evidence)


def test_static_validation_closes_migration_and_gates(tmp_path: Path) -> None:
    # The typed-executor example contract must keep migration baseline 0012,
    # activation false and all three Phase 5 Feature Gates false.
    gate._validate_config()


def test_successful_run_never_deletes_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Gate must not delete its own run directory (evidence_preserved)."""
    evidence_root = tmp_path / "gate-preserve"
    monkeypatch.setattr(gate, "EVIDENCE_ROOT", evidence_root)
    evidence, report = _synthetic_run(tmp_path / "elsewhere", monkeypatch)
    assert report["evidence_preserved"] is True
    assert report["cleanup"]["evidence_preserved"] is True
    assert evidence.is_file()
