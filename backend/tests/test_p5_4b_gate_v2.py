"""Fail-closed unit tests for the P5.4B run-scoped evidence Gate."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

_RUNNER_RELATIVE = Path("scripts/production/run_p5_4b_engineering_composition_disposable_gate.py")
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
    pytest.skip("P5.4B Gate tests require a full-repository mount", allow_module_level=True)


def _load_runner() -> ModuleType:
    assert RUNNER is not None
    spec = importlib.util.spec_from_file_location("p5_4b_gate_v2", RUNNER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_runner()


def _stdout(key: str) -> str:
    if key == "measured-alembic-head":
        return "0012\n"
    if key == "measured-alembic-graph":
        return 'P54B_GRAPH={"heads":["0012"],"revisions":["0012","0011","0010"]}\n'
    if key == "measured-runtime-gates":
        return json.dumps(gate.EXPECTED_RUNTIME_GATES, sort_keys=True) + "\n"
    if key == "measured-network":
        return "true\n"
    if key in {"measured-backend-image", "measured-postgres-image"}:
        return f'"sha256:{"a" * 64}"|[]\n'
    if key == "measured-venv-volume":
        return json.dumps(gate.BACKEND_VENV_VOLUME) + "\n"
    if key == "measured-python-environment":
        return '[["pytest","8.3.5"],["sqlalchemy","2.0.41"]]\n'
    return "ok\n"


def _synthetic_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict]:
    evidence_root = tmp_path / "gate-v2"
    legacy_root = tmp_path / "legacy"
    monkeypatch.setattr(gate, "EVIDENCE_ROOT", evidence_root)
    monkeypatch.setattr(gate, "LEGACY_ROOT", legacy_root)
    run_id = "20260807T010203000000Z-abcdef123456"
    run_dir = evidence_root / run_id
    run_dir.mkdir(parents=True)
    override = run_dir / "compose-internal-network.yml"
    override.write_bytes(
        b"services:\n  postgres-test:\n    pull_policy: never\nnetworks:\n  default:\n    internal: true\n"
    )
    manifest = gate._manifest()
    manifest_sha = gate._write_json(run_dir / "source-manifest.json", manifest)
    gate._write_bytes(run_dir / "source-manifest.sha256", f"{manifest_sha}\n".encode())
    project = "omnibase-test-p54b-abcdef123456"
    commands = []
    for key, command in gate._expected_commands(project, override).items():
        result = subprocess.CompletedProcess(command, 0, _stdout(key))
        commands.append(gate._record_command(run_dir, key, command, result))
    measurements = {
        "measured_alembic_head": "0012",
        "alembic_graph": {
            "heads": ["0012"],
            "revisions": ["0012", "0011", "0010"],
            "migration_0013_or_higher_present": False,
        },
        "runtime_gates": dict(gate.EXPECTED_RUNTIME_GATES),
        "docker_network_internal": True,
        "backend_image": {"id": f"sha256:{'a' * 64}", "repo_digests": []},
        "postgres_image": {"id": f"sha256:{'a' * 64}", "repo_digests": []},
        "backend_venv_volume": gate.BACKEND_VENV_VOLUME,
        "python_environment": [["pytest", "8.3.5"], ["sqlalchemy", "2.0.41"]],
        "ambient_runtime_dependent": True,
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
        cleanup={"containers": 0, "networks": 0, "volumes": 0},
        error=None,
        sentinel_database="omnibase_test_p54b_abcdef123456",
        compose_project=project,
        legacy_evidence_preserved=True,
    )
    return run_dir / "evidence.json", report


def _rewrite_evidence(path: Path, report: dict) -> None:
    digest = gate._write_json(path, report)
    gate._write_bytes(path.with_name("evidence.sha256"), f"{digest}\n".encode())


def test_source_closure_and_command_construction_are_fail_closed(tmp_path: Path) -> None:
    paths = set(gate._source_paths())
    assert ".env" not in paths
    assert "backend/tests/test_p5_4b_gate_v2.py" in paths
    assert "backend/src/omnibase/agent_executor/engineering.py" in paths
    override = tmp_path / "override.yml"
    project = "omnibase-test-p54b-abcdef123456"
    expected = gate._expected_commands(project, override)
    assert tuple(expected) == gate.REQUIRED_COMMAND_KEYS
    assert expected["preflight-images"] == [
        "docker",
        "image",
        "inspect",
        gate.BACKEND_IMAGE,
        gate.POSTGRES_IMAGE,
    ]
    container = expected["integration"]
    assert container[:4] == ["docker", "run", "--pull", "never"]
    assert "TEST_DATABASE_URL=<sentinel-redacted>" in container
    assert all("/.env" not in item.replace("\\", "/") for item in container)
    compose = expected["compose-up"]
    assert compose[2:4] == ["--env-file", str(gate.ENV_FILE)]
    assert str(override) in compose


@pytest.mark.parametrize(
    "stdout",
    [
        "missing marker",
        'P54B_GRAPH={"heads":["0011","0012"],"revisions":["0012"]}',
        'P54B_GRAPH={"heads":["0012"],"revisions":["0013","0012"]}',
        'P54B_GRAPH={"heads":["0012"],"revisions":["head","0012"]}',
    ],
)
def test_graph_parser_rejects_unsealed_revision_shapes(stdout: str) -> None:
    with pytest.raises(RuntimeError):
        gate._parse_graph(stdout)


def test_measurement_parsers_require_canonical_strict_values() -> None:
    image = gate._parse_image_measurement(f'"sha256:{"b" * 64}"|[]')
    assert image["id"] == f"sha256:{'b' * 64}"
    with pytest.raises(RuntimeError):
        gate._parse_image_measurement('"latest"|[]')
    assert gate._parse_python_environment('[["a","1"],["b","2"]]') == [
        ["a", "1"],
        ["b", "2"],
    ]
    with pytest.raises(RuntimeError):
        gate._parse_python_environment('[["b","2"],["a","1"]]')


def test_synthetic_sealed_run_verifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence, _ = _synthetic_run(tmp_path, monkeypatch)
    gate._verify(evidence)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("passed", False),
        ("migration_head", "0011"),
        ("workload_container_external_network_denied", False),
        ("legacy_evidence_preserved", False),
        ("ambient_runtime_dependent", False),
        ("cleanup", {"containers": 1, "networks": 0, "volumes": 0}),
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


def test_command_semantics_and_sidecar_tamper_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence, report = _synthetic_run(tmp_path, monkeypatch)
    report["commands"][0]["command"] = ["python", "-c", "pass"]
    _rewrite_evidence(evidence, report)
    with pytest.raises(RuntimeError, match="command semantics mismatch"):
        gate._verify(evidence)

    evidence, _ = _synthetic_run(tmp_path / "second", monkeypatch)
    stdout = evidence.parent / "commands" / "measured-network.stdout"
    stdout.write_text("false\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
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


def test_cleanup_requires_success_and_zero_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = tmp_path / "override.yml"
    override.write_text("networks: {}\n", encoding="utf-8")
    results = iter(
        [
            subprocess.CompletedProcess([], 0, ""),
            subprocess.CompletedProcess([], 0, ""),
            subprocess.CompletedProcess([], 0, ""),
            subprocess.CompletedProcess([], 0, ""),
        ]
    )
    monkeypatch.setattr(gate, "_run", lambda *args, **kwargs: next(results))
    counts, records = gate._cleanup(tmp_path, "omnibase-test-p54b-abcdef123456", {}, override)
    assert counts == {"containers": 0, "networks": 0, "volumes": 0}
    assert tuple(item["key"] for item in records) == gate.REQUIRED_COMMAND_KEYS[-4:]

    results = iter(
        [
            subprocess.CompletedProcess([], 1, "down failed"),
            subprocess.CompletedProcess([], 0, "container-id\n"),
            subprocess.CompletedProcess([], 0, ""),
            subprocess.CompletedProcess([], 0, ""),
        ]
    )
    monkeypatch.setattr(gate, "_run", lambda *args, **kwargs: next(results))
    with pytest.raises(RuntimeError, match="cleanup failed"):
        gate._cleanup(tmp_path / "failed", "omnibase-test-p54b-abcdef123456", {}, override)
