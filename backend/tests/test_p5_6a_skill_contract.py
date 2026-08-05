"""P5.6A compile-only first-party native Skill contract tests."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from omnibase.production.composition import AdmissionState, ConfigurationError, GitSourceProvenance
from omnibase.production.phase5_skill_contract import (
    SkillContractConfig,
    SkillContractError,
    SkillContractGate,
    load_skill_contract_config,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "deployment" / "production" / "phase5-skill-contract.example.json"
VALIDATOR_PATH = REPO_ROOT / "scripts" / "production" / "validate_p5_6a_skill_contract.py"
MODULE_PATH = REPO_ROOT / "backend" / "src" / "omnibase" / "production" / "phase5_skill_contract.py"


def _mapping() -> dict[str, object]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _version(mapping: dict[str, object], index: int = 0) -> dict[str, object]:
    versions = mapping["skill_versions"]
    assert isinstance(versions, list)
    version = versions[index]
    assert isinstance(version, dict)
    return version


def _definition(mapping: dict[str, object], index: int = 0) -> dict[str, object]:
    definitions = mapping["skill_definitions"]
    assert isinstance(definitions, list)
    definition = definitions[index]
    assert isinstance(definition, dict)
    return definition


def _source(mapping: dict[str, object]) -> dict[str, object]:
    source = mapping["source"]
    assert isinstance(source, dict)
    return source


def _gates(mapping: dict[str, object]) -> dict[str, object]:
    gates = mapping["feature_gates"]
    assert isinstance(gates, dict)
    return gates


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _command(command_id: str, *arguments: str) -> dict[str, object]:
    return {
        "command_id": command_id,
        "profile": "python-validator",
        "arguments": list(arguments),
        "network_allowed": False,
    }


def _provenance(*, clean: bool = True) -> GitSourceProvenance:
    return GitSourceProvenance(
        git_commit="1" * 40,
        git_tree="2" * 40,
        remote_origin="https://github.com/lss100200/omnibase.git",
        clean=clean,
        dirty_paths=() if clean else (" M backend/src/omnibase/production/example.py",),
        file_count=1,
        files=(("AGENTS.md", 1, "3" * 64),),
        manifest_sha256="4" * 64,
    )


def _with_second_version(mapping: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    versions = mapping["skill_versions"]
    assert isinstance(versions, list)
    current = _version(mapping)
    older = copy.deepcopy(current)
    older["skill_version_id"] = "56000000-0000-0000-0000-000000000100"
    older["version"] = "0.0.1"
    older["version_state"] = "deprecated"
    older["rollback_version_id"] = None
    current["version"] = "0.1.0"
    current["rollback_version_id"] = older["skill_version_id"]
    versions.append(older)
    return current, older


def test_example_contract_is_valid_but_compile_only() -> None:
    config = load_skill_contract_config(CONFIG_PATH)
    report = SkillContractGate(REPO_ROOT).validate_only(config)

    assert report.state is AdmissionState.BLOCKED
    assert report.contract_valid is True
    assert report.activation_allowed is False
    assert report.source is None
    assert config.migration_baseline == "0012"
    assert config.versions[0].kind.value == "instruction"
    assert config.versions[0].budget.max_tool_calls == 0


def test_canonical_digest_is_input_order_independent() -> None:
    first = _mapping()
    current, older = _with_second_version(first)
    current["supported_agent_version_digests"] = ["a" * 64, "b" * 64]
    current["verification_commands"] = [
        _command("validator-second", "scripts/second.py"),
        _command("validator-first", "scripts/first.py"),
    ]

    second = copy.deepcopy(first)
    second_versions = second["skill_versions"]
    assert isinstance(second_versions, list)
    second_versions.reverse()
    second_current = next(
        item
        for item in second_versions
        if isinstance(item, dict) and item["skill_version_id"] == current["skill_version_id"]
    )
    assert isinstance(second_current, dict)
    supported = second_current["supported_agent_version_digests"]
    commands = second_current["verification_commands"]
    assert isinstance(supported, list)
    assert isinstance(commands, list)
    supported.reverse()
    commands.reverse()

    assert (
        SkillContractConfig.from_mapping(first).canonical_digest()
        == SkillContractConfig.from_mapping(second).canonical_digest()
    )
    assert older["version_state"] == "deprecated"


def test_same_definition_strictly_older_rollback_is_valid() -> None:
    mapping = _mapping()
    current, older = _with_second_version(mapping)

    config = SkillContractConfig.from_mapping(mapping)

    assert current["rollback_version_id"] == older["skill_version_id"]
    assert len(config.versions) == 2


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("skill_runtime_authorized", True, "cannot authorize"),
        ("mcp_enabled", True, "cannot authorize"),
        ("third_party_marketplace_enabled", True, "cannot authorize"),
        ("migration_baseline", "0013", "exactly 0012"),
    ],
)
def test_top_level_activation_and_migration_drift_are_rejected(
    field: str, value: object, match: str
) -> None:
    mapping = _mapping()
    mapping[field] = value

    with pytest.raises(ConfigurationError, match=match):
        SkillContractConfig.from_mapping(mapping)


def test_unknown_top_level_field_is_rejected() -> None:
    mapping = _mapping()
    mapping["runtime"] = {"enabled": True}

    with pytest.raises(ConfigurationError, match="unexpected fields"):
        SkillContractConfig.from_mapping(mapping)


@pytest.mark.parametrize(
    "gate", ["agent_runtime_enabled", "agent_planner_enabled", "multi_agent_enabled"]
)
def test_every_feature_gate_must_remain_false(gate: str) -> None:
    mapping = _mapping()
    _gates(mapping)[gate] = True

    with pytest.raises(ConfigurationError):
        SkillContractConfig.from_mapping(mapping)


def test_source_must_require_clean_checkout() -> None:
    mapping = _mapping()
    _source(mapping)["require_clean_checkout"] = False

    with pytest.raises(SkillContractError, match="clean checkout"):
        SkillContractConfig.from_mapping(mapping)


@pytest.mark.parametrize("scope", ["tenant", "global"])
def test_only_workspace_installation_scope_is_accepted(scope: str) -> None:
    mapping = _mapping()
    _definition(mapping)["allowed_installation_scopes"] = [scope]

    with pytest.raises(SkillContractError, match="workspace"):
        SkillContractConfig.from_mapping(mapping)


def test_third_party_definition_is_rejected() -> None:
    mapping = _mapping()
    _definition(mapping)["first_party"] = False

    with pytest.raises(SkillContractError, match="first-party"):
        SkillContractConfig.from_mapping(mapping)


@pytest.mark.parametrize("identifier", ["*", "all", "any", "root", "host"])
def test_wildcard_or_privileged_tool_identifiers_are_rejected(identifier: str) -> None:
    mapping = _mapping()
    version = _version(mapping)
    version["kind"] = "workflow"
    version["required_tool_ids"] = [identifier]
    version["budget"]["max_tool_calls"] = 1

    with pytest.raises(SkillContractError, match="without wildcards"):
        SkillContractConfig.from_mapping(mapping)


def test_instruction_skill_cannot_request_tools_capabilities_or_tool_budget() -> None:
    mapping = _mapping()
    version = _version(mapping)
    version["required_tool_ids"] = ["workspace.read"]
    version["capability_requirements"] = [
        {
            "action_id": "workspace.read",
            "resource_kind": "knowledge",
            "resource_scope": "workspace",
            "required": True,
        }
    ]
    version["budget"]["max_tool_calls"] = 1

    with pytest.raises(SkillContractError, match="instruction Skills cannot request"):
        SkillContractConfig.from_mapping(mapping)


@pytest.mark.parametrize("kind", ["instruction", "workflow", "script"])
@pytest.mark.parametrize("state", ["approved", "published"])
def test_p5_6a_cannot_claim_reviewed_or_published_versions(kind: str, state: str) -> None:
    mapping = _mapping()
    version = _version(mapping)
    version["kind"] = kind
    version["version_state"] = state
    version["signature_status"] = "verified"

    with pytest.raises(SkillContractError, match="cannot claim approved or published|before P5.4"):
        SkillContractConfig.from_mapping(mapping)


def test_workflow_and_script_tested_manifests_remain_compile_only() -> None:
    for kind in ("workflow", "script"):
        mapping = _mapping()
        version = _version(mapping)
        version["kind"] = kind
        version["required_tool_ids"] = ["workspace.read"]
        version["budget"]["max_tool_calls"] = 1
        config = SkillContractConfig.from_mapping(mapping)
        assert config.versions[0].version_state.value == "tested"


def test_secret_and_network_expansion_are_rejected() -> None:
    secret = _mapping()
    _version(secret)["secrets_allowed"] = True
    with pytest.raises(SkillContractError, match="must not request or contain secrets"):
        SkillContractConfig.from_mapping(secret)

    network = _mapping()
    _version(network)["network_policy"] = "allow"
    with pytest.raises(SkillContractError, match="must be one of"):
        SkillContractConfig.from_mapping(network)


def test_instruction_digest_drift_is_rejected() -> None:
    mapping = _mapping()
    _version(mapping)["instructions"] = "drifted"

    with pytest.raises(SkillContractError, match="does not match UTF-8 bytes"):
        SkillContractConfig.from_mapping(mapping)


def test_duplicate_definition_version_and_semver_are_rejected() -> None:
    duplicate_definition = _mapping()
    definitions = duplicate_definition["skill_definitions"]
    assert isinstance(definitions, list)
    definitions.append(copy.deepcopy(definitions[0]))
    with pytest.raises(SkillContractError, match="Definition IDs"):
        SkillContractConfig.from_mapping(duplicate_definition)

    duplicate_version = _mapping()
    versions = duplicate_version["skill_versions"]
    assert isinstance(versions, list)
    versions.append(copy.deepcopy(versions[0]))
    with pytest.raises(SkillContractError, match="Version IDs"):
        SkillContractConfig.from_mapping(duplicate_version)

    duplicate_semver = _mapping()
    versions = duplicate_semver["skill_versions"]
    assert isinstance(versions, list)
    second = copy.deepcopy(versions[0])
    second["skill_version_id"] = "56000000-0000-0000-0000-000000000102"
    versions.append(second)
    with pytest.raises(SkillContractError, match="SemVer values"):
        SkillContractConfig.from_mapping(duplicate_semver)


def test_cross_definition_self_and_forward_rollback_are_rejected() -> None:
    self_reference = _mapping()
    current = _version(self_reference)
    current["rollback_version_id"] = current["skill_version_id"]
    with pytest.raises(SkillContractError, match="cannot reference itself"):
        SkillContractConfig.from_mapping(self_reference)

    forward = _mapping()
    current, older = _with_second_version(forward)
    current["version"] = "0.0.1"
    older["version"] = "0.1.0"
    with pytest.raises(SkillContractError, match="strictly older"):
        SkillContractConfig.from_mapping(forward)

    cross = _mapping()
    current, older = _with_second_version(cross)
    second_definition = copy.deepcopy(_definition(cross))
    second_definition["skill_definition_id"] = "56000000-0000-0000-0000-000000000002"
    second_definition["stable_logical_key"] = "omnibase.second-skill"
    definitions = cross["skill_definitions"]
    assert isinstance(definitions, list)
    definitions.append(second_definition)
    older["skill_definition_id"] = second_definition["skill_definition_id"]
    with pytest.raises(SkillContractError, match="same definition"):
        SkillContractConfig.from_mapping(cross)


def test_revoked_definition_cannot_retain_tested_version() -> None:
    mapping = _mapping()
    _definition(mapping)["definition_state"] = "revoked"

    with pytest.raises(SkillContractError, match="cannot retain"):
        SkillContractConfig.from_mapping(mapping)


def test_json_schema_is_closed_local_bounded_and_acyclic() -> None:
    unknown = _mapping()
    _version(unknown)["input_schema"]["unevaluatedProperties"] = False
    with pytest.raises(SkillContractError, match="non-controlled"):
        SkillContractConfig.from_mapping(unknown)

    open_object = _mapping()
    _version(open_object)["input_schema"]["additionalProperties"] = True
    with pytest.raises(SkillContractError, match="additionalProperties"):
        SkillContractConfig.from_mapping(open_object)

    external = _mapping()
    _version(external)["input_schema"] = {"$ref": "https://example.com/schema.json"}
    with pytest.raises(SkillContractError, match="inside the local"):
        SkillContractConfig.from_mapping(external)

    missing = _mapping()
    _version(missing)["input_schema"] = {"$ref": "#/$defs/missing"}
    with pytest.raises(SkillContractError, match="missing local definitions"):
        SkillContractConfig.from_mapping(missing)

    cycle = _mapping()
    _version(cycle)["input_schema"] = {
        "$defs": {"loop": {"$ref": "#/$defs/loop"}},
        "$ref": "#/$defs/loop",
    }
    with pytest.raises(SkillContractError, match="cyclic"):
        SkillContractConfig.from_mapping(cycle)

    invalid_pattern = _mapping()
    _version(invalid_pattern)["input_schema"] = {"type": "string", "pattern": "["}
    with pytest.raises(SkillContractError, match="regular expression"):
        SkillContractConfig.from_mapping(invalid_pattern)

    negative_bound = _mapping()
    _version(negative_bound)["input_schema"] = {"type": "string", "minLength": -1}
    with pytest.raises(SkillContractError, match="non-negative"):
        SkillContractConfig.from_mapping(negative_bound)


def test_json_schema_depth_overflow_is_rejected() -> None:
    mapping = _mapping()
    schema: dict[str, object] = {"type": "string"}
    for _ in range(18):
        schema = {"type": "array", "items": schema}
    _version(mapping)["input_schema"] = schema

    with pytest.raises(SkillContractError, match="maximum JSON Schema depth"):
        SkillContractConfig.from_mapping(mapping)


def test_verification_commands_are_unique_and_not_shell_programs() -> None:
    duplicate = _mapping()
    _version(duplicate)["verification_commands"] = [
        _command("validator", "scripts/one.py"),
        _command("validator", "scripts/two.py"),
    ]
    with pytest.raises(SkillContractError, match="unique command IDs"):
        SkillContractConfig.from_mapping(duplicate)

    shell = _mapping()
    _version(shell)["verification_commands"] = [
        _command("validator", "validator && curl https://example.com")
    ]
    with pytest.raises(SkillContractError, match="shell-free"):
        SkillContractConfig.from_mapping(shell)

    network = _mapping()
    command = _version(network)["verification_commands"][0]
    assert isinstance(command, dict)
    command["network_allowed"] = True
    with pytest.raises(SkillContractError, match="cannot use the network"):
        SkillContractConfig.from_mapping(network)


def test_budget_ceiling_and_strict_identity_formats_are_rejected() -> None:
    budget = _mapping()
    _version(budget)["budget"]["max_context_tokens"] = 131073
    with pytest.raises(SkillContractError, match="ceiling"):
        SkillContractConfig.from_mapping(budget)

    identifier = _mapping()
    _version(identifier)["skill_version_id"] = "NOT-A-UUID"
    with pytest.raises(SkillContractError, match="strict lowercase UUID"):
        SkillContractConfig.from_mapping(identifier)

    semver = _mapping()
    _version(semver)["version"] = "01.0.0"
    with pytest.raises(SkillContractError, match="strict SemVer"):
        SkillContractConfig.from_mapping(semver)


def test_verify_remains_blocked_and_reports_dirty_source_as_veto() -> None:
    config = SkillContractConfig.from_mapping(_mapping())
    clean = SkillContractGate(REPO_ROOT).verify(config, source=_provenance())
    dirty = SkillContractGate(REPO_ROOT).verify(config, source=_provenance(clean=False))

    assert clean.state is AdmissionState.BLOCKED
    assert clean.contract_valid is True
    assert clean.activation_allowed is False
    assert clean.migration_head == "0012"
    assert dirty.state is AdmissionState.INVALID
    assert dirty.contract_valid is False
    assert any("clean checkout" in veto for veto in dirty.vetoes)


def test_verify_rejects_migration_head_drift(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    versions_dir = repo / "backend" / "src" / "omnibase" / "migrations" / "versions"
    versions_dir.mkdir(parents=True)
    source = REPO_ROOT / "backend" / "src" / "omnibase" / "migrations" / "versions"
    for path in source.glob("*.py"):
        shutil.copy2(path, versions_dir / path.name)
    (versions_dir / "0013_unapproved.py").write_text(
        'revision = "0013"\ndown_revision = "0012"\n', encoding="utf-8"
    )

    report = SkillContractGate(repo).verify(
        SkillContractConfig.from_mapping(_mapping()), source=_provenance()
    )

    assert report.state is AdmissionState.INVALID
    assert any("migration head drifted" in veto for veto in report.vetoes)


def test_verify_converts_git_provenance_failure_to_veto(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    versions_dir = repo / "backend" / "src" / "omnibase" / "migrations" / "versions"
    versions_dir.mkdir(parents=True)
    source = REPO_ROOT / "backend" / "src" / "omnibase" / "migrations" / "versions"
    for path in source.glob("*.py"):
        shutil.copy2(path, versions_dir / path.name)

    report = SkillContractGate(repo).verify(SkillContractConfig.from_mapping(_mapping()))

    assert report.state is AdmissionState.INVALID
    assert report.source is None
    assert any("source provenance" in veto for veto in report.vetoes)


def test_verify_rejects_forbidden_skill_runtime_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    versions_dir = repo / "backend" / "src" / "omnibase" / "migrations" / "versions"
    versions_dir.mkdir(parents=True)
    source = REPO_ROOT / "backend" / "src" / "omnibase" / "migrations" / "versions"
    for path in source.glob("*.py"):
        shutil.copy2(path, versions_dir / path.name)
    forbidden = repo / "backend" / "src" / "omnibase" / "skills.py"
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text("# forbidden runtime seam\n", encoding="utf-8")

    report = SkillContractGate(repo).verify(
        SkillContractConfig.from_mapping(_mapping()), source=_provenance()
    )

    assert report.state is AdmissionState.INVALID
    assert any("backend/src/omnibase/skills.py" in veto for veto in report.vetoes)


def test_config_loader_rejects_symlink_when_supported(tmp_path: Path) -> None:
    target = tmp_path / "contract.json"
    target.write_text(CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    link = tmp_path / "contract-link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")

    with pytest.raises(SkillContractError, match="regular non-link"):
        load_skill_contract_config(link)


def test_instructions_digest_helper_matches_fixture() -> None:
    mapping = _mapping()
    version = _version(mapping)
    assert version["instructions_digest"] == _digest(str(version["instructions"]))


def test_contract_module_imports_no_runtime_or_external_io_stack() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])

    assert roots <= {
        "__future__",
        "collections",
        "dataclasses",
        "enum",
        "hashlib",
        "json",
        "omnibase",
        "os",
        "pathlib",
        "re",
        "stat",
    }


def test_validator_validate_only_is_blocked_but_exits_zero() -> None:
    completed = subprocess.run(
        [sys.executable, str(VALIDATOR_PATH), "--validate-only"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["state"] == AdmissionState.BLOCKED.value
    assert payload["activation_allowed"] is False
    assert payload["skill_runtime_created"] is False


def test_validator_report_output_must_remain_outside_repository(tmp_path: Path) -> None:
    outside = tmp_path / "p5-6a-report.json"
    accepted = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR_PATH),
            "--validate-only",
            "--output",
            str(outside),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert accepted.returncode == 0
    assert json.loads(outside.read_text(encoding="utf-8"))["state"] == AdmissionState.BLOCKED.value

    inside = REPO_ROOT / "p5-6a-report-forbidden.json"
    rejected = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR_PATH),
            "--validate-only",
            "--output",
            str(inside),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert rejected.returncode == 1
    assert not inside.exists()
    assert "outside the repository" in rejected.stdout
