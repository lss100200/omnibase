"""P5.1A offline Agent Registry contract preflight tests."""

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
from omnibase.production.phase5_registry_contract import (
    AgentDefinition,
    AgentVersionManifest,
    BudgetCeilings,
    RegistryContractConfig,
    RegistryContractError,
    RegistryContractGate,
    RiskLevel,
    VersionState,
    WorkspaceAgentBinding,
    discover_migration_revisions,
    load_registry_contract_config,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "deployment" / "production" / "phase5-registry-contract.example.json"
VALIDATOR_PATH = REPO_ROOT / "scripts" / "production" / "validate_p5_1_registry_contract.py"

DEFINITION_ID = "00000000-0000-0000-0000-000000000001"
TENANT_ID = "00000000-0000-0000-0000-00000000000a"
VERSION_ID = "11111111-1111-1111-1111-111111111111"
MODEL_POLICY_ID = "22222222-2222-2222-2222-222222222222"
MEMORY_POLICY_ID = "44444444-4444-4444-4444-444444444444"
BINDING_ID = "55555555-5555-5555-5555-555555555555"
WORKSPACE_ID = "66666666-6666-6666-6666-666666666666"
ACTOR_ID = "00000000-0000-0000-0000-0000000000aa"
INSTRUCTIONS_DIGEST = "3333333333333333333333333333333333333333333333333333333333333333"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _version_canonical_digest(mapping: dict[str, object]) -> str:
    payload = {key: value for key, value in mapping.items() if key != "manifest_digest"}
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _definition_mapping() -> dict[str, object]:
    return {
        "schema_version": 1,
        "agent_definition_id": DEFINITION_ID,
        "tenant_id": TENANT_ID,
        "stable_logical_key": "repository-inspector",
        "display_name": "Repository Inspector",
        "description": "Read-only repository inspection agent",
        "risk_level": "low",
        "allowed_installation_scopes": ["workspace"],
        "definition_state": "active",
        "created_by": ACTOR_ID,
        "created_at": "2026-08-03T00:00:00Z",
        "metadata_version": 1,
    }


def _version_mapping() -> dict[str, object]:
    mapping: dict[str, object] = {
        "schema_version": 1,
        "agent_version_id": VERSION_ID,
        "agent_definition_id": DEFINITION_ID,
        "tenant_id": TENANT_ID,
        "version": "1.0.0",
        "manifest_digest": "0" * 64,
        "model_policy_id": MODEL_POLICY_ID,
        "instructions_digest": INSTRUCTIONS_DIGEST,
        "max_context_tokens": 200000,
        "allowed_tool_ids": ["rag_search", "artifact_read"],
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 1}},
            "required": ["query"],
        },
        "output_schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
        "risk_level": "low",
        "memory_policy_id": MEMORY_POLICY_ID,
        "max_concurrency": 2,
        "default_budget": {
            "max_tokens": 100000,
            "max_cost_units": 1000,
            "max_wall_clock_seconds": 300,
            "max_tool_calls": 50,
        },
        "version_state": "sealed",
        "created_by": ACTOR_ID,
        "created_at": "2026-08-03T00:00:00Z",
    }
    mapping["manifest_digest"] = _version_canonical_digest(mapping)
    return mapping


def _binding_mapping() -> dict[str, object]:
    return {
        "schema_version": 1,
        "workspace_agent_binding_id": BINDING_ID,
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "workspace_generation": 1,
        "agent_definition_id": DEFINITION_ID,
        "agent_version_id": VERSION_ID,
        "agent_version_digest": _version_canonical_digest(_version_mapping()),
        "installation_state": "installed",
        "resource_scopes": ["workspace_private_read"],
        "default_budget_policy": {
            "max_tokens": 50000,
            "max_cost_units": 500,
            "max_wall_clock_seconds": 300,
            "max_tool_calls": 50,
        },
        "installed_by": ACTOR_ID,
        "approval_id": None,
        "created_at": "2026-08-03T00:00:00Z",
        "disabled_at": None,
        "superseded_by": None,
    }


def _ceilings() -> dict[str, int]:
    return BudgetCeilings.from_mapping(
        {
            "max_tokens": 10000000,
            "max_cost_units": 100000,
            "max_wall_clock_seconds": 3600,
            "max_tool_calls": 1000,
            "max_concurrency": 64,
            "max_context_tokens": 2000000,
        }
    ).as_mapping()


def _contract_mapping() -> dict[str, object]:
    return {
        "schema_version": 1,
        "phase": "P5.1A",
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
                "id": "phase5_registry_production_evidence",
                "status": "not_proven",
                "path": None,
                "sha256": None,
                "assertions": {},
                "required_for_activation": True,
            }
        ],
        "budget_ceilings": {
            "max_tokens": 10000000,
            "max_cost_units": 100000,
            "max_wall_clock_seconds": 3600,
            "max_tool_calls": 1000,
            "max_concurrency": 64,
            "max_context_tokens": 2000000,
        },
        "approval_policy": {
            "low": "optional",
            "medium": "optional",
            "high": "required",
            "critical": "required",
        },
        "forbidden_source_paths": [
            "backend/src/omnibase/agent_runtime",
            "backend/src/omnibase/agent_registry",
            "backend/src/omnibase/agents",
            "backend/src/omnibase/planner",
            "backend/src/omnibase/executor",
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
            "0011",
            "0012",
            "0013",
            "0014",
            "0015",
            "0016",
        ],
        "sealed_contracts": [
            {
                "name": "agent_registry_contract_doc",
                "path": "docs/phase-5-agent-registry-contract.md",
                "sha256": "3" * 64,
            },
            {
                "name": "threat_model",
                "path": "docs/phase-5-threat-model.md",
                "sha256": "4" * 64,
            },
            {
                "name": "maintainer_map",
                "path": "docs/maintainers/maintenance-map.json",
                "sha256": "5" * 64,
            },
            {
                "name": "security_invariants",
                "path": "docs/maintainers/security-invariants.md",
                "sha256": "6" * 64,
            },
            {
                "name": "registry_tests",
                "path": "backend/tests/test_p5_1_registry_contract.py",
                "sha256": "7" * 64,
            },
        ],
        "openapi_snapshot": {
            "path": "sdk/contracts/p34-2-openapi.snapshot.json",
            "sha256": "8" * 64,
        },
        "registry_contracts": {
            "agent_definitions": [_definition_mapping()],
            "agent_versions": [_version_mapping()],
            "workspace_agent_bindings": [_binding_mapping()],
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
        else (" M backend/src/omnibase/production/phase5_registry_contract.py",),
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
        # Deterministic LF bytes: universal-newline translation on Windows
        # would make seal digests differ from the bytes read back by the
        # formal gate, so synthetic fixtures are written byte-for-byte.
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
        ["git", "-C", str(repo), "config", "user.name", "P5.1A test"],
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
        ("0011", "0010"),
        ("0012", "0011"),
        ("0013", "0012"),
        ("0014", "0013"),
        ("0015", "0014"),
        ("0016", "0015"),
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
    _write_file(repo, "docs/phase-5-agent-registry-contract.md", "# contract\n")
    _write_file(repo, "docs/phase-5-threat-model.md", "# threat model\n")
    _write_file(repo, "docs/maintainers/maintenance-map.json", "{}\n")
    _write_file(repo, "docs/maintainers/security-invariants.md", "# invariants\n")
    _write_file(repo, "backend/tests/test_p5_1_registry_contract.py", "# tests\n")
    _write_file(repo, "deployment/production/phase5-admission.example.json", "{}\n")
    _write_file(repo, "docs/evidence/p34-7/production-readiness-decision.md", "# decision\n")
    _write_file(repo, "backend/src/omnibase/production/source.py", "VALUE = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "synthetic base"], check=True, capture_output=True
    )
    return repo


def _synthetic_config(tmp_path: Path, repo: Path | None = None) -> RegistryContractConfig:
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
    mapping["openapi_snapshot"]["sha256"] = _digest(
        (repo / "sdk/contracts/p34-2-openapi.snapshot.json").read_text(encoding="utf-8")
    )
    sealed = [
        ("agent_registry_contract_doc", "docs/phase-5-agent-registry-contract.md", "# contract\n"),
        ("threat_model", "docs/phase-5-threat-model.md", "# threat model\n"),
        ("maintainer_map", "docs/maintainers/maintenance-map.json", "{}\n"),
        ("security_invariants", "docs/maintainers/security-invariants.md", "# invariants\n"),
        ("registry_tests", "backend/tests/test_p5_1_registry_contract.py", "# tests\n"),
    ]
    mapping["sealed_contracts"] = [
        {"name": name, "path": path, "sha256": seal(path, content)}
        for name, path, content in sealed
    ]
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "sealed fixtures"], check=True, capture_output=True
    )
    return RegistryContractConfig.from_mapping(mapping)


# ---------------------------------------------------------------------------
# Positive fixtures
# ---------------------------------------------------------------------------


def test_checked_in_contract_is_valid_but_explicitly_blocked() -> None:
    config = (
        load_registry_contract_config(CONFIG_PATH)
        if CONFIG_PATH.exists()
        else RegistryContractConfig.from_mapping(_contract_mapping())
    )

    report = RegistryContractGate(REPO_ROOT).validate_only(config)

    assert report.state is AdmissionState.BLOCKED
    assert report.activation_allowed is False
    assert report.contract_valid is True
    assert any("P34.7 formal state is not ready" in item for item in report.blockers)
    assert any("P5.0 admission formal state is not ready" in item for item in report.blockers)
    assert any(
        "production database schema is not applied/proven" in item for item in report.blockers
    )
    assert any(
        "Agent Invocation/Runtime API is not implemented" in item for item in report.blockers
    )
    assert any(
        "Workspace installation public/runtime surface is not implemented" in item
        for item in report.blockers
    )


def test_definition_version_binding_parse_positive() -> None:
    definition = AgentDefinition.from_mapping(_definition_mapping())
    assert definition.stable_logical_key == "repository-inspector"
    assert definition.risk_level is RiskLevel.LOW

    version = AgentVersionManifest.from_mapping(_version_mapping(), ceilings=_ceilings())
    assert version.version_state is VersionState.SEALED
    assert version.manifest_digest == version.canonical_digest()

    binding = WorkspaceAgentBinding.from_mapping(_binding_mapping(), ceilings=_ceilings())
    assert binding.workspace_generation == 1
    assert binding.installation_state.value == "installed"


def test_version_can_seal_real_instructions_and_binds_their_digest() -> None:
    mapping = _version_mapping()
    instructions = "You are a tool-free Workspace research employee."
    mapping["instructions"] = instructions
    mapping["instructions_digest"] = _digest(instructions)
    mapping["manifest_digest"] = _version_canonical_digest(mapping)
    version = AgentVersionManifest.from_mapping(mapping, ceilings=_ceilings())
    assert version.instructions == instructions
    assert version.to_dict()["instructions"] == instructions

    drifted = dict(mapping)
    drifted["instructions"] = "A different instruction."
    drifted["manifest_digest"] = _version_canonical_digest(drifted)
    with pytest.raises(RegistryContractError, match="instructions_digest does not match"):
        AgentVersionManifest.from_mapping(drifted, ceilings=_ceilings())


def test_version_canonical_digest_ignores_line_endings() -> None:
    mapping = _version_mapping()
    lf = json.dumps(mapping, indent=2).encode("utf-8")
    crlf = lf.replace(b"\n", b"\r\n")
    parsed = json.loads(crlf.decode("utf-8"))
    version = AgentVersionManifest.from_mapping(parsed, ceilings=_ceilings())
    assert version.manifest_digest == version.canonical_digest()
    raw_text_digest = hashlib.sha256(crlf).hexdigest()
    assert raw_text_digest != version.canonical_digest()  # raw text is never the digest


# ---------------------------------------------------------------------------
# Contract-level negative fixtures (items 1-4, 39, 42, 51, 57)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data.update({"schema_version": 2}), "schema_version must be 1"),
        (lambda data: data.update({"phase": "P5.1"}), "phase must be P5.1A"),
        (lambda data: data.update({"unexpected_top_level": True}), "unexpected fields"),
        (
            lambda data: data["registry_contracts"]["agent_definitions"][0].update({"bogus": 1}),
            "unexpected fields",
        ),
        (
            lambda data: data["feature_gates"].update({"agent_runtime_enabled": True}),
            "requires the Agent Runtime feature gate to be disabled",
        ),
        (
            lambda data: data["feature_gates"].update({"agent_planner_enabled": True}),
            "every Phase 5 feature gate to be disabled",
        ),
        (
            lambda data: data["approval_policy"].update({"high": "optional"}),
            "must be required",
        ),
        (lambda data: data["critical_veto"].update({"expected": 1}), "exactly 0"),
        (
            lambda data: data["forbidden_source_paths"].append(".env"),
            "root .env",
        ),
    ],
)
def test_unsafe_contracts_fail_closed(mutation, message: str) -> None:
    mapping = _contract_mapping()
    mutation(mapping)
    with pytest.raises(ConfigurationError, match=message):
        RegistryContractConfig.from_mapping(mapping)


def test_nan_and_infinity_are_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "contract.json"
    config_path.write_text(
        json.dumps(_contract_mapping()).replace('"max_tokens": 100000,', '"max_tokens": NaN,'),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="non-finite"):
        load_registry_contract_config(config_path)

    config_path.write_text(
        json.dumps(_contract_mapping()).replace('"max_tokens": 100000,', '"max_tokens": Infinity,'),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="non-finite"):
        load_registry_contract_config(config_path)


def test_symlink_configuration_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "real-contract.json"
    target.write_text(json.dumps(_contract_mapping()), encoding="utf-8")
    link = tmp_path / "link-contract.json"
    link.symlink_to(target)
    with pytest.raises(ConfigurationError, match="regular non-link"):
        load_registry_contract_config(link)


def test_report_output_inside_repository_is_rejected(tmp_path: Path) -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "production"
        / "validate_p5_1_registry_contract.py"
    )
    if not script.exists():
        pytest.skip("full public checkout is not mounted in the backend test image")
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--validate-only",
            "--output",
            str(REPO_ROOT / ".p51a-inside-report.json"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "outside the repository" in result.stdout
    assert not (REPO_ROOT / ".p51a-inside-report.json").exists()


# ---------------------------------------------------------------------------
# DTO negative fixtures (items 5-38, 40-41)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda d: d.update({"agent_definition_id": ""}), "non-empty string"),
        (lambda d: d.update({"agent_definition_id": "not-a-uuid"}), "strict lowercase UUID"),
        (
            lambda d: d.update({"agent_definition_id": "ABCDEFAB-CDEF-ABCD-EFAB-CDEFABCDEFAB"}),
            "strict lowercase UUID",
        ),
        (lambda d: d.update({"definition_state": "ACTIVE"}), "unknown or malformed state"),
        (lambda d: d.update({"definition_state": " active"}), "unknown or malformed state"),
        (lambda d: d.update({"definition_state": "published"}), "unknown or malformed state"),
        (lambda d: d.update({"risk_level": "moderate"}), "unknown or malformed state"),
        (
            lambda d: d.update({"allowed_installation_scopes": ["cluster"]}),
            "unknown or malformed state",
        ),
        (
            lambda d: d.update({"allowed_installation_scopes": ["workspace", "workspace"]}),
            "non-empty and unique",
        ),
        (lambda d: d.update({"allowed_installation_scopes": []}), "non-empty and unique"),
        (lambda d: d.update({"stable_logical_key": "*"}), "logical identifier without wildcards"),
        (lambda d: d.update({"stable_logical_key": ""}), "non-empty string"),
        (lambda d: d.update({"metadata_version": 0}), "positive integer"),
        (lambda d: d.update({"api_key": "sk-live"}), "unexpected fields"),
        (lambda d: d.update({"api_base_url": "https://provider.example"}), "unexpected fields"),
        (lambda d: d.update({"authorization": "Bearer x"}), "unexpected fields"),
        (lambda d: d.update({"cookie": "session=1"}), "unexpected fields"),
        (lambda d: d.update({"bearer_token": "abc"}), "unexpected fields"),
        (lambda d: d.update({"database_schema": "tenant_42"}), "unexpected fields"),
        (lambda d: d.update({"database_table": "users"}), "unexpected fields"),
        (lambda d: d.update({"database_column": "email"}), "unexpected fields"),
        (lambda d: d.update({"connection_string": "postgresql://u:p@h/db"}), "unexpected fields"),
        (lambda d: d.update({"redis_key": "omnibase:agent"}), "unexpected fields"),
        (lambda d: d.update({"minio_bucket": "omnibase-files"}), "unexpected fields"),
        (lambda d: d.update({"host_path": "/var/run"}), "unexpected fields"),
        (lambda d: d.update({"docker_socket": "/var/run/docker.sock"}), "unexpected fields"),
        (lambda d: d.update({"shell_command": "rm -rf /"}), "unexpected fields"),
        (lambda d: d.update({"environment": {"TOKEN": "x"}}), "unexpected fields"),
    ],
)
def test_definition_negative_fixtures(mutation, message: str) -> None:
    mapping = _definition_mapping()
    mutation(mapping)
    with pytest.raises(ConfigurationError, match=message):
        AgentDefinition.from_mapping(mapping)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda v: v.update({"allowed_tool_ids": ["*"]}), "without wildcards"),
        (lambda v: v.update({"allowed_tool_ids": ["all"]}), "without wildcards"),
        (
            lambda v: v.update({"allowed_tool_ids": ["rag_search", "rag_search"]}),
            "must not contain duplicates",
        ),
        (lambda v: v.update({"allowed_tool_ids": [""]}), "non-empty string"),
        (lambda v: v.update({"allowed_tool_ids": ["rag_search", ""]}), "non-empty string"),
        (lambda v: v.update({"version": "1.0"}), "strict version string"),
        (lambda v: v.update({"version": "latest"}), "strict version string"),
        (lambda v: v.update({"manifest_digest": "abc"}), "lowercase 64-character SHA-256"),
        (lambda v: v.update({"manifest_digest": "A" * 64}), "lowercase 64-character SHA-256"),
        (lambda v: v.update({"manifest_digest": "0" * 63}), "lowercase 64-character SHA-256"),
        (
            lambda v: v.update({"instructions_digest": "sk-live-credential-here"}),
            "lowercase 64-character SHA-256",
        ),
        (
            lambda v: v.update({"instructions_digest": "0" * 64}),
            "does not match the canonical manifest bytes",
        ),
        (lambda v: v.update({"model_policy_id": "openai"}), "strict lowercase UUID"),
        (lambda v: v.update({"max_context_tokens": 0}), "positive integer"),
        (lambda v: v.update({"max_concurrency": -1}), "positive integer"),
        (
            lambda v: v.update(
                {
                    "default_budget": {
                        "max_tokens": 0,
                        "max_cost_units": 1,
                        "max_wall_clock_seconds": 1,
                        "max_tool_calls": 1,
                    }
                }
            ),
            "positive integer",
        ),
        (
            lambda v: v.update(
                {
                    "default_budget": {
                        "max_tokens": -5,
                        "max_cost_units": 1,
                        "max_wall_clock_seconds": 1,
                        "max_tool_calls": 1,
                    }
                }
            ),
            "positive integer",
        ),
        (
            lambda v: v.update(
                {
                    "default_budget": {
                        "max_tokens": 99999999,
                        "max_cost_units": 1,
                        "max_wall_clock_seconds": 1,
                        "max_tool_calls": 1,
                    }
                }
            ),
            "exceeds the server-owned ceiling",
        ),
        (lambda v: v.update({"version_state": "Sealed"}), "unknown or malformed state"),
        (lambda v: v.update({"version_state": "active"}), "unknown or malformed state"),
        (lambda v: v.update({"provider_name": "openai"}), "unexpected fields"),
        (lambda v: v.update({"provider_base_url": "https://api.openai.com"}), "unexpected fields"),
        (lambda v: v.update({"api_key": "sk-live"}), "unexpected fields"),
        (
            lambda v: v.update({"input_schema": {"$ref": "https://evil.example/schema.json"}}),
            "local JSON pointer",
        ),
        (
            lambda v: v.update({"input_schema": {"$ref": "file:///etc/passwd"}}),
            "local JSON pointer",
        ),
        (
            lambda v: v.update({"input_schema": {"type": "object", "command": "rm -rf /"}}),
            "non-controlled JSON Schema keywords",
        ),
        (
            lambda v: v.update({"input_schema": {"type": "object", "env": {"SECRET": "x"}}}),
            "non-controlled JSON Schema keywords",
        ),
        (
            lambda v: v.update({"input_schema": {"type": "string", "minimum": float("nan")}}),
            "finite number",
        ),
    ],
)
def test_version_negative_fixtures(mutation, message: str) -> None:
    mapping = _version_mapping()
    mutation(mapping)
    with pytest.raises(ConfigurationError, match=message):
        AgentVersionManifest.from_mapping(mapping, ceilings=_ceilings())


def test_sealed_version_cannot_be_modified_in_place() -> None:
    mapping = _version_mapping()
    sealed = AgentVersionManifest.from_mapping(mapping, ceilings=_ceilings())
    modified = dict(mapping)
    modified["max_context_tokens"] = 99999
    modified["manifest_digest"] = _version_canonical_digest(modified)
    resealed = AgentVersionManifest.from_mapping(modified, ceilings=_ceilings())
    assert resealed.canonical_digest() != sealed.canonical_digest()
    assert resealed.manifest_digest == resealed.canonical_digest()


def test_nan_infinity_budget_values_are_rejected() -> None:
    mapping = _version_mapping()
    budget = mapping["default_budget"]
    assert isinstance(budget, dict)
    budget["max_tokens"] = float("inf")
    with pytest.raises(ConfigurationError, match="positive integer"):
        AgentVersionManifest.from_mapping(mapping, ceilings=_ceilings())


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda b: b.update({"workspace_generation": 0}), "positive integer"),
        (lambda b: b.update({"workspace_generation": -3}), "positive integer"),
        (lambda b: b.update({"installation_state": "Installed"}), "unknown or malformed state"),
        (lambda b: b.update({"installation_state": "installed "}), "unknown or malformed state"),
        (lambda b: b.update({"installation_state": "running"}), "unknown or malformed state"),
        (
            lambda b: b.update(
                {"resource_scopes": ["workspace_private_read", "workspace_private_read"]}
            ),
            "non-empty and unique",
        ),
        (lambda b: b.update({"resource_scopes": []}), "non-empty and unique"),
        (lambda b: b.update({"resource_scopes": ["../../etc"]}), "logical identifier"),
        (lambda b: b.update({"agent_version_id": "unknown"}), "strict lowercase UUID"),
        (lambda b: b.update({"agent_version_digest": "0" * 63}), "lowercase 64-character SHA-256"),
        (lambda b: b.update({"installation_state": "disabled"}), "disabled_at is required"),
        (
            lambda b: b.update(
                {"installation_state": "installed", "disabled_at": "2026-08-03T00:00:00Z"}
            ),
            "only allowed when installation_state is disabled",
        ),
        (lambda b: b.update({"installation_state": "superseded"}), "superseded_by is required"),
        (
            lambda b: b.update({"installation_state": "superseded", "superseded_by": BINDING_ID}),
            "must not reference itself",
        ),
        (
            lambda b: b.update(
                {
                    "default_budget_policy": {
                        "max_tokens": 1,
                        "max_cost_units": 1,
                        "max_wall_clock_seconds": 1,
                        "max_tool_calls": 1,
                        "max_retries": 5,
                    }
                }
            ),
            "unexpected fields",
        ),
        (lambda b: b.update({"workload_token": "abc"}), "unexpected fields"),
        (lambda b: b.update({"certificate_private_key": "-----BEGIN"}), "unexpected fields"),
        (lambda b: b.update({"host_command": "sh -c x"}), "unexpected fields"),
    ],
)
def test_binding_negative_fixtures(mutation, message: str) -> None:
    mapping = _binding_mapping()
    mutation(mapping)
    with pytest.raises(ConfigurationError, match=message):
        WorkspaceAgentBinding.from_mapping(mapping, ceilings=_ceilings())


@pytest.mark.parametrize(
    "risk_level",
    ["high", "critical"],
)
def test_high_risk_binding_requires_approval(risk_level: str) -> None:
    mapping = _contract_mapping()
    version = _version_mapping()
    version["risk_level"] = risk_level
    version["manifest_digest"] = _version_canonical_digest(version)
    binding = _binding_mapping()
    binding["agent_version_digest"] = _version_canonical_digest(version)
    binding["approval_id"] = None
    mapping["registry_contracts"]["agent_versions"] = [version]
    mapping["registry_contracts"]["workspace_agent_bindings"] = [binding]

    with pytest.raises(ConfigurationError, match="requires an approval_id"):
        RegistryContractConfig.from_mapping(mapping)


def test_exact_version_digest_binding_drift_is_rejected() -> None:
    mapping = _contract_mapping()
    binding = _binding_mapping()
    binding["agent_version_digest"] = "0" * 64
    mapping["registry_contracts"]["workspace_agent_bindings"] = [binding]

    with pytest.raises(ConfigurationError, match="drifted digest"):
        RegistryContractConfig.from_mapping(mapping)


@pytest.mark.parametrize(
    ("collection", "message"),
    [
        ("agent_definitions", "agent_definition IDs must be unique"),
        ("agent_versions", "agent_version IDs must be unique"),
        ("workspace_agent_bindings", "workspace_agent_binding IDs must be unique"),
    ],
)
def test_registry_logical_ids_must_be_unique(collection: str, message: str) -> None:
    mapping = _contract_mapping()
    items = mapping["registry_contracts"][collection]
    assert isinstance(items, list)
    items.append(dict(items[0]))

    with pytest.raises(ConfigurationError, match=message):
        RegistryContractConfig.from_mapping(mapping)


def test_definition_logical_key_and_version_label_are_tenant_unique() -> None:
    mapping = _contract_mapping()
    second_definition = _definition_mapping()
    second_definition["agent_definition_id"] = "00000000-0000-0000-0000-000000000002"
    definitions = mapping["registry_contracts"]["agent_definitions"]
    assert isinstance(definitions, list)
    definitions.append(second_definition)
    with pytest.raises(ConfigurationError, match="stable_logical_key values must be unique"):
        RegistryContractConfig.from_mapping(mapping)

    mapping = _contract_mapping()
    second_version = _version_mapping()
    second_version["agent_version_id"] = "11111111-1111-1111-1111-111111111112"
    second_version["manifest_digest"] = _version_canonical_digest(second_version)
    versions = mapping["registry_contracts"]["agent_versions"]
    assert isinstance(versions, list)
    versions.append(second_version)
    with pytest.raises(ConfigurationError, match="version values must be unique"):
        RegistryContractConfig.from_mapping(mapping)


def test_registry_reference_graph_rejects_cross_tenant_or_mismatched_edges() -> None:
    mapping = _contract_mapping()
    version = mapping["registry_contracts"]["agent_versions"][0]
    assert isinstance(version, dict)
    version["tenant_id"] = "00000000-0000-0000-0000-00000000000b"
    version["manifest_digest"] = _version_canonical_digest(version)
    with pytest.raises(ConfigurationError, match="crosses the tenant boundary"):
        RegistryContractConfig.from_mapping(mapping)

    mapping = _contract_mapping()
    binding = mapping["registry_contracts"]["workspace_agent_bindings"][0]
    assert isinstance(binding, dict)
    binding["tenant_id"] = "00000000-0000-0000-0000-00000000000b"
    with pytest.raises(ConfigurationError, match="crosses a tenant boundary"):
        RegistryContractConfig.from_mapping(mapping)

    mapping = _contract_mapping()
    second_definition = _definition_mapping()
    second_definition["agent_definition_id"] = "00000000-0000-0000-0000-000000000002"
    second_definition["stable_logical_key"] = "repository-inspector-v2"
    definitions = mapping["registry_contracts"]["agent_definitions"]
    assert isinstance(definitions, list)
    definitions.append(second_definition)
    binding = mapping["registry_contracts"]["workspace_agent_bindings"][0]
    assert isinstance(binding, dict)
    binding["agent_definition_id"] = second_definition["agent_definition_id"]
    with pytest.raises(ConfigurationError, match="different agent_definition"):
        RegistryContractConfig.from_mapping(mapping)


def test_version_cannot_downgrade_definition_risk_or_bypass_workspace_scope() -> None:
    mapping = _contract_mapping()
    definition = mapping["registry_contracts"]["agent_definitions"][0]
    assert isinstance(definition, dict)
    definition["risk_level"] = "high"
    with pytest.raises(ConfigurationError, match="must not downgrade the risk level"):
        RegistryContractConfig.from_mapping(mapping)

    mapping = _contract_mapping()
    definition = mapping["registry_contracts"]["agent_definitions"][0]
    assert isinstance(definition, dict)
    definition["allowed_installation_scopes"] = ["tenant"]
    with pytest.raises(ConfigurationError, match="does not allow workspace installation"):
        RegistryContractConfig.from_mapping(mapping)


@pytest.mark.parametrize("keyword", ["exclusiveMinimum", "exclusiveMaximum"])
def test_controlled_json_schema_rejects_non_numeric_exclusive_bounds(keyword: str) -> None:
    mapping = _version_mapping()
    input_schema = mapping["input_schema"]
    assert isinstance(input_schema, dict)
    input_schema[keyword] = {"shell_command": "not-allowed"}
    mapping["manifest_digest"] = _version_canonical_digest(mapping)

    with pytest.raises(ConfigurationError, match=rf"input_schema\.{keyword} must be a finite"):
        AgentVersionManifest.from_mapping(mapping, ceilings=_ceilings())


def test_nested_feature_gate_and_critical_veto_fields_are_closed_sets() -> None:
    mapping = _contract_mapping()
    feature_gates = mapping["feature_gates"]
    assert isinstance(feature_gates, dict)
    feature_gates["runtime_alias"] = False
    with pytest.raises(ConfigurationError, match="feature_gates has unexpected fields"):
        RegistryContractConfig.from_mapping(mapping)

    mapping = _contract_mapping()
    critical_veto = mapping["critical_veto"]
    assert isinstance(critical_veto, dict)
    critical_veto["ignore"] = True
    with pytest.raises(ConfigurationError, match="critical_veto has unexpected fields"):
        RegistryContractConfig.from_mapping(mapping)


def test_binding_referencing_unknown_definition_or_version_is_rejected() -> None:
    mapping = _contract_mapping()
    binding = _binding_mapping()
    binding["agent_definition_id"] = "99999999-9999-9999-9999-999999999999"
    mapping["registry_contracts"]["workspace_agent_bindings"] = [binding]
    with pytest.raises(ConfigurationError, match="unknown agent_definition"):
        RegistryContractConfig.from_mapping(mapping)

    mapping = _contract_mapping()
    binding = _binding_mapping()
    binding["agent_version_id"] = "99999999-9999-9999-9999-999999999999"
    mapping["registry_contracts"]["workspace_agent_bindings"] = [binding]
    with pytest.raises(ConfigurationError, match="unknown agent_version"):
        RegistryContractConfig.from_mapping(mapping)


def test_revoked_definition_cannot_be_bound() -> None:
    mapping = _contract_mapping()
    definition = _definition_mapping()
    definition["definition_state"] = "revoked"
    mapping["registry_contracts"]["agent_definitions"] = [definition]

    with pytest.raises(ConfigurationError, match="references revoked agent_definition"):
        RegistryContractConfig.from_mapping(mapping)


# ---------------------------------------------------------------------------
# Formal verification negatives (items 43-56, 58-60)
# ---------------------------------------------------------------------------


def test_dirty_checkout_is_a_veto(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path)
    report = RegistryContractGate(tmp_path / "repo").verify(config, source=_source(clean=False))
    assert report.state is AdmissionState.INVALID
    assert "Phase 5 registry contract requires a clean checkout" in report.vetoes


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
        RegistryContractGate(repo).verify(config)


def test_feature_gate_true_is_a_blocker(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path)
    report = RegistryContractGate(tmp_path / "repo").verify(
        config,
        source=_source(),
        gate_values={"AGENT_RUNTIME_ENABLED": "true"},
    )
    assert report.state is AdmissionState.BLOCKED
    assert report.activation_allowed is False
    assert any("feature gates must remain disabled" in item for item in report.blockers)


@pytest.mark.parametrize("token", ["TRUE", "yes", "on", "1", " true"])
def test_feature_gate_truthy_tokens_are_vetoes(tmp_path: Path, token: str) -> None:
    config = _synthetic_config(tmp_path)
    report = RegistryContractGate(tmp_path / "repo").verify(
        config,
        source=_source(),
        gate_values={"AGENT_RUNTIME_ENABLED": token},
    )
    assert report.state is AdmissionState.INVALID
    assert any(veto.startswith("feature gates:") for veto in report.vetoes)


def test_symlink_sealed_contract_to_synthetic_env_is_rejected(tmp_path: Path) -> None:
    repo = _build_synthetic_repo(tmp_path)
    synthetic_env = repo / "synthetic.env"
    synthetic_env.write_text("SECRET=synthetic\n", encoding="utf-8")
    target = repo / "docs" / "phase-5-agent-registry-contract.md"
    target.unlink()
    target.symlink_to(synthetic_env)
    config = _synthetic_config(tmp_path, repo=repo)

    report = RegistryContractGate(repo).verify(config, source=_source())

    assert report.state is AdmissionState.INVALID
    assert any("link or reparse point" in veto for veto in report.vetoes)


def test_parent_directory_symlink_escape_is_rejected(tmp_path: Path) -> None:
    repo = _build_synthetic_repo(tmp_path)
    docs = repo / "docs"
    real_docs = repo / "docs-real"
    docs.rename(real_docs)
    docs.symlink_to(real_docs, target_is_directory=True)
    config = _synthetic_config(tmp_path, repo=repo)

    report = RegistryContractGate(repo).verify(config, source=_source())

    assert report.state is AdmissionState.INVALID
    assert any("link or reparse point" in veto for veto in report.vetoes)


def test_attempted_runtime_orm_router_packages_are_vetoes(tmp_path: Path) -> None:
    for index, forbidden in enumerate(
        (
            "backend/src/omnibase/agent_runtime",
            "backend/src/omnibase/agent_registry",
            "backend/src/omnibase/planner",
            "backend/src/omnibase/executor",
            "backend/src/omnibase/multi_agent",
        )
    ):
        # One synthetic repo per forbidden path: Windows keeps git object
        # handles open long enough that reusing one directory would fail.
        repo_dir = tmp_path / f"repo-{index}"
        repo_dir.mkdir(parents=True, exist_ok=True)
        repo = _build_synthetic_repo(repo_dir)
        _write_file(repo, f"{forbidden}/__init__.py", "")
        config = _synthetic_config(repo_dir, repo=repo)
        report = RegistryContractGate(repo).verify(config, source=_source())
        assert report.state is AdmissionState.INVALID, forbidden
        assert any("forbidden source path exists" in veto for veto in report.vetoes), forbidden


def test_attempted_migration_0017_is_a_veto(tmp_path: Path) -> None:
    repo = _build_synthetic_repo(tmp_path)
    _write_file(
        repo,
        "backend/src/omnibase/migrations/versions/0017_unapproved_runtime.py",
        'revision: str = "0017"\ndown_revision: str | None = "0016"\n',
    )
    config = _synthetic_config(tmp_path, repo=repo)

    report = RegistryContractGate(repo).verify(config, source=_source())

    assert report.state is AdmissionState.INVALID
    assert any("migration revision set drifted" in veto for veto in report.vetoes)


def test_openapi_snapshot_with_agent_endpoint_is_a_veto(tmp_path: Path) -> None:
    repo = _build_synthetic_repo(tmp_path)
    _write_file(
        repo,
        "sdk/contracts/p34-2-openapi.snapshot.json",
        json.dumps({"openapi": "3.1.0", "paths": {"/api/v1/agent-definitions": {}}}),
    )
    config = _synthetic_config(tmp_path, repo=repo)
    config = replace(
        config,
        openapi_snapshot_sha256=_digest(
            (repo / "sdk/contracts/p34-2-openapi.snapshot.json").read_text(encoding="utf-8")
        ),
    )

    report = RegistryContractGate(repo).verify(config, source=_source())

    assert report.state is AdmissionState.INVALID
    assert any("exposes an agent endpoint" in veto for veto in report.vetoes)


def test_sealed_contract_digest_drift_is_a_veto(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path)
    config = replace(
        config,
        sealed_contracts=(("threat_model", "docs/phase-5-threat-model.md", "0" * 64),)
        + config.sealed_contracts[1:],
    )
    report = RegistryContractGate(tmp_path / "repo").verify(config, source=_source())
    assert report.state is AdmissionState.INVALID
    assert any("sealed contract drifted" in veto for veto in report.vetoes)


def test_not_proven_evidence_is_never_counted_as_passed(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path)
    report = RegistryContractGate(tmp_path / "repo").verify(config, source=_source())
    assert report.passed_evidence == ()
    assert any("not_proven" in item for item in report.blockers)


def test_report_never_claims_runtime_activated(tmp_path: Path) -> None:
    config = _synthetic_config(tmp_path)
    report = RegistryContractGate(tmp_path / "repo").verify(config, source=_source())
    payload = report.to_dict()
    assert payload["registry_runtime_implemented"] is False
    assert payload["database_schema_applied"] is False
    assert payload["public_api_exposed"] is False
    assert payload["root_env_accessed"] is False
    assert payload["business_database_accessed"] is False
    assert payload["business_database_migrated"] is False
    assert payload["external_network_accessed"] is False
    assert payload["agent_registry_runtime_created"] is False
    assert payload["agent_api_exposed"] is False
    assert payload["agent_runtime_activated"] is False
    assert payload["planner_activated"] is False
    assert payload["executor_activated"] is False
    assert payload["worker_or_scheduler_started"] is False


def test_validate_only_never_returns_ready() -> None:
    config = (
        load_registry_contract_config(CONFIG_PATH)
        if CONFIG_PATH.exists()
        else RegistryContractConfig.from_mapping(_contract_mapping())
    )
    report = RegistryContractGate(REPO_ROOT).validate_only(config)
    assert report.state is not AdmissionState.READY


def test_validator_reads_server_owned_feature_gate_environment(monkeypatch) -> None:
    namespace = runpy.run_path(str(VALIDATOR_PATH), run_name="p5_1_validator_test")
    monkeypatch.setenv("AGENT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("AGENT_PLANNER_ENABLED", "false")
    monkeypatch.setenv("MULTI_AGENT_ENABLED", "false")

    values = namespace["_server_gate_values"]()

    assert values == {
        "AGENT_RUNTIME_ENABLED": "true",
        "AGENT_PLANNER_ENABLED": "false",
        "MULTI_AGENT_ENABLED": "false",
    }


def test_validator_rejects_parent_symlink_for_config_and_output_symlink(
    tmp_path: Path,
) -> None:
    namespace = runpy.run_path(str(VALIDATOR_PATH), run_name="p5_1_validator_path_test")
    repo = tmp_path / "repo"
    real_config_dir = repo / "real-config"
    real_config_dir.mkdir(parents=True)
    (real_config_dir / "contract.json").write_text("{}\n", encoding="utf-8")
    linked_config_dir = repo / "linked-config"
    linked_config_dir.symlink_to(real_config_dir, target_is_directory=True)
    safe_config_path = namespace["_safe_config_path"]
    safe_config_path.__globals__["REPO_ROOT"] = repo

    with pytest.raises(ConfigurationError, match="link or reparse point"):
        safe_config_path(linked_config_dir / "contract.json")

    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    victim = tmp_path / "victim.json"
    victim.write_text("unchanged\n", encoding="utf-8")
    linked_output = report_dir / "report.json"
    linked_output.symlink_to(victim)
    write_report = namespace["_write_report"]
    write_report.__globals__["REPO_ROOT"] = repo

    with pytest.raises(ConfigurationError, match="link or reparse point"):
        write_report(linked_output, {"state": "blocked/not_proven"})
    assert victim.read_text(encoding="utf-8") == "unchanged\n"


# ---------------------------------------------------------------------------
# Import and source-boundary constraints
# ---------------------------------------------------------------------------


def test_registry_contract_module_has_no_runtime_imports() -> None:
    module_file = (
        REPO_ROOT / "backend" / "src" / "omnibase" / "production" / "phase5_registry_contract.py"
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
        "agents",
        "planner",
        "executor",
        "multi_agent",
    ):
        assert not (REPO_ROOT / "backend" / "src" / "omnibase" / forbidden).exists(), forbidden
    # P5.1B legitimately added the internal persistence package; P5.1C added the
    # Browser control API. Neither is a runtime, and the API default composition
    # is fail-closed (rejecting authorizer, never a DB-backed control plane).
    registry = REPO_ROOT / "backend" / "src" / "omnibase" / "agent_registry"
    assert registry.is_dir()
    assert (registry / "router.py").is_file()
    assert not (registry / "runtime.py").exists()
    control_source = (registry / "control.py").read_text(encoding="utf-8")
    assert "UnavailableAgentRegistryControlPlane" in control_source
    assert "RegistryControlPlaneUnavailable" in control_source


def test_migration_revision_discovery_on_synthetic_chain(tmp_path: Path) -> None:
    repo = _build_synthetic_repo(tmp_path)
    revisions = discover_migration_revisions(repo, "backend/src/omnibase/migrations/versions")
    assert set(revisions) == {f"{i:04d}" for i in range(1, 17)}


def test_formal_gate_keeps_missing_proofs_blocked() -> None:
    if not CONFIG_PATH.exists():
        pytest.skip("full public checkout is not mounted in the backend test image")
    config = load_registry_contract_config(CONFIG_PATH)
    report = RegistryContractGate(REPO_ROOT).verify(config, source=_source())
    assert report.state is AdmissionState.BLOCKED
    assert report.activation_allowed is False
    assert report.vetoes == ()
    assert any("P34.7 formal state is not ready" in item for item in report.blockers)
    assert any("Agent Runtime gate remains disabled" in item for item in report.blockers)
    assert any(
        "production database schema is not applied/proven" in item for item in report.blockers
    )
    assert any(
        "Agent Invocation/Runtime API is not implemented" in item for item in report.blockers
    )
    assert any(
        "Workspace installation public/runtime surface is not implemented" in item
        for item in report.blockers
    )
    assert any("not_proven" in item for item in report.blockers)
