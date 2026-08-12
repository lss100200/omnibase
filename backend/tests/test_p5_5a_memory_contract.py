"""P5.5A compile-only Memory / ContextCapsule contract tests."""

from __future__ import annotations

import ast
import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from omnibase.production.composition import (
    AdmissionState,
    ConfigurationError,
    GitSourceProvenance,
)
from omnibase.production.phase5_memory_contract import (
    MemoryContractConfig,
    MemoryContractError,
    MemoryContractGate,
    MemoryPolicy,
    MemoryReviewEvidence,
    load_memory_contract_config,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "deployment" / "production" / "phase5-memory-contract.example.json"
VALIDATOR_PATH = REPO_ROOT / "scripts" / "production" / "validate_p5_5a_memory_contract.py"
MODULE_PATH = (
    REPO_ROOT / "backend" / "src" / "omnibase" / "production" / "phase5_memory_contract.py"
)


def _mapping() -> dict[str, object]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _object_list(mapping: dict[str, object], name: str) -> list[dict[str, object]]:
    value = mapping[name]
    assert isinstance(value, list)
    assert all(isinstance(item, dict) for item in value)
    return value  # type: ignore[return-value]


def _policy(mapping: dict[str, object]) -> dict[str, object]:
    return _object_list(mapping, "memory_policies")[0]


def _capsule(mapping: dict[str, object]) -> dict[str, object]:
    return _object_list(mapping, "context_capsule_examples")[0]


def _candidate(mapping: dict[str, object]) -> dict[str, object]:
    return _object_list(mapping, "memory_candidate_examples")[0]


def _selections(mapping: dict[str, object]) -> list[dict[str, object]]:
    selections = _capsule(mapping)["selected_memories"]
    assert isinstance(selections, list)
    assert all(isinstance(item, dict) for item in selections)
    return selections  # type: ignore[return-value]


def _selection(mapping: dict[str, object], scope: str) -> dict[str, object]:
    return next(item for item in _selections(mapping) if item["scope"] == scope)


def _reviews(mapping: dict[str, object]) -> list[dict[str, object]]:
    return _object_list(mapping, "memory_review_evidence_examples")


def _refresh_policy_digest(mapping: dict[str, object]) -> None:
    _capsule(mapping)["compiler_policy_sha256"] = MemoryPolicy.from_mapping(
        _policy(mapping)
    ).canonical_digest()


def _refresh_review_digest(mapping: dict[str, object]) -> None:
    _selection(mapping, "controlled_shared")["review_evidence_sha256"] = (
        MemoryReviewEvidence.from_mapping(_reviews(mapping)[0]).canonical_digest()
    )


def _provenance(*, clean: bool = True) -> GitSourceProvenance:
    return GitSourceProvenance(
        git_commit="1" * 40,
        git_tree="2" * 40,
        remote_origin="https://github.com/lss100200/omnibase.git",
        clean=clean,
        dirty_paths=() if clean else (" M docs/roadmap.md",),
        file_count=1,
        files=(("AGENTS.md", 1, "3" * 64),),
        manifest_sha256="4" * 64,
    )


def test_example_contract_is_valid_but_compile_only() -> None:
    config = load_memory_contract_config(CONFIG_PATH)
    report = MemoryContractGate(REPO_ROOT).validate_only(config)

    assert report.state is AdmissionState.BLOCKED
    assert report.contract_valid is True
    assert report.activation_allowed is False
    assert config.migration_baseline == "0015"
    assert config.memory_persistence_authorized is False
    assert config.memory_runtime_authorized is False
    assert config.memory_browser_api_exposed is False
    assert config.capsules[0].delegable is False
    assert config.capsules[0].trusted_instructions is False
    assert len(config.capsules[0].content_sha256()) == 64


def test_canonical_digest_is_input_order_independent() -> None:
    first = _mapping()
    second = copy.deepcopy(first)
    _selections(second).reverse()
    _selection(second, "agent_private")["evidence_reference_ids"] = [
        "99999999-9999-9999-9999-999999999912",
        "99999999-9999-9999-9999-999999999911",
    ]
    _selection(first, "agent_private")["evidence_reference_ids"] = [
        "99999999-9999-9999-9999-999999999911",
        "99999999-9999-9999-9999-999999999912",
    ]

    assert MemoryContractConfig.from_mapping(first).canonical_digest() == (
        MemoryContractConfig.from_mapping(second).canonical_digest()
    )


def test_unknown_top_level_field_is_rejected() -> None:
    mapping = _mapping()
    mapping["memory_database_url"] = "postgresql://forbidden"
    with pytest.raises(ConfigurationError, match="unexpected fields"):
        MemoryContractConfig.from_mapping(mapping)


@pytest.mark.parametrize(
    "gate", ["agent_runtime_enabled", "agent_planner_enabled", "multi_agent_enabled"]
)
def test_every_feature_gate_must_remain_false(gate: str) -> None:
    mapping = _mapping()
    gates = mapping["feature_gates"]
    assert isinstance(gates, dict)
    gates[gate] = True
    with pytest.raises(MemoryContractError, match="feature gates"):
        MemoryContractConfig.from_mapping(mapping)


@pytest.mark.parametrize(
    "field",
    [
        "memory_persistence_authorized",
        "memory_runtime_authorized",
        "memory_browser_api_exposed",
    ],
)
def test_p5_5a_cannot_authorize_runtime_surfaces(field: str) -> None:
    mapping = _mapping()
    mapping[field] = True
    with pytest.raises(MemoryContractError, match="cannot authorize"):
        MemoryContractConfig.from_mapping(mapping)


def test_source_and_migration_baseline_are_closed() -> None:
    mapping = _mapping()
    source = mapping["source"]
    assert isinstance(source, dict)
    source["require_clean_checkout"] = False
    with pytest.raises(MemoryContractError, match="clean checkout"):
        MemoryContractConfig.from_mapping(mapping)

    mapping = _mapping()
    mapping["migration_baseline"] = "0013"
    with pytest.raises(MemoryContractError, match="exactly 0015"):
        MemoryContractConfig.from_mapping(mapping)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("auto_activate_candidates", True),
        ("high_sensitivity_requires_confirmation", False),
        ("secret_storage_allowed", True),
        ("inferred_sensitive_attributes_allowed", True),
        ("treat_memory_as_untrusted_data", False),
        ("security_kernel_precedence", False),
        ("source_evidence_required", False),
    ],
)
def test_memory_policy_cannot_weaken_safety(field: str, value: bool) -> None:
    mapping = _mapping()
    _policy(mapping)[field] = value
    with pytest.raises(MemoryContractError, match="weakens"):
        MemoryPolicy.from_mapping(_policy(mapping))


def test_policy_requires_sensitive_inference_bans() -> None:
    mapping = _mapping()
    categories = _policy(mapping)["forbidden_inference_categories"]
    assert isinstance(categories, list)
    categories.remove("health")
    with pytest.raises(MemoryContractError, match="inference bans"):
        MemoryPolicy.from_mapping(_policy(mapping))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("initial_budget_tokens", 4097),
        ("retrieval_budget_tokens", 8193),
        ("max_memory_calls", 9),
        ("max_memory_items", 65),
        ("memory_deadline_ms", 5001),
        ("max_capsule_ttl_seconds", 86401),
        ("initial_budget_tokens", True),
    ],
)
def test_server_owned_memory_budget_ceilings_are_enforced(field: str, value: object) -> None:
    mapping = _mapping()
    budget = _policy(mapping)["budget"]
    assert isinstance(budget, dict)
    budget[field] = value
    with pytest.raises(MemoryContractError):
        MemoryPolicy.from_mapping(_policy(mapping))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("tenant_id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "cross-Tenant"),
        ("owner_user_id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "cross-user"),
    ],
)
def test_capsule_rejects_cross_principal_memory(field: str, value: str, message: str) -> None:
    mapping = _mapping()
    _selections(mapping)[0][field] = value
    with pytest.raises(MemoryContractError, match=message):
        MemoryContractConfig.from_mapping(mapping)


def test_capsule_rejects_cross_workspace_memory() -> None:
    mapping = _mapping()
    _selections(mapping)[1]["workspace_id"] = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    with pytest.raises(MemoryContractError, match="cross-Workspace"):
        MemoryContractConfig.from_mapping(mapping)


def test_user_private_memory_must_not_bind_workspace() -> None:
    mapping = _mapping()
    _selections(mapping)[0]["workspace_id"] = _capsule(mapping)["workspace_id"]
    with pytest.raises(MemoryContractError, match="user-private"):
        MemoryContractConfig.from_mapping(mapping)


def test_agent_private_binding_and_non_agent_scope_are_exact() -> None:
    mapping = _mapping()
    _selections(mapping)[1]["agent_version_id"] = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    with pytest.raises(MemoryContractError, match="AgentVersion"):
        MemoryContractConfig.from_mapping(mapping)

    mapping = _mapping()
    _selection(mapping, "controlled_shared")["agent_version_id"] = _capsule(mapping)[
        "agent_version_id"
    ]
    with pytest.raises(MemoryContractError, match="non-agent-private"):
        MemoryContractConfig.from_mapping(mapping)


def test_controlled_shared_memory_requires_review_evidence() -> None:
    mapping = _mapping()
    selection = _selection(mapping, "controlled_shared")
    selection["review_evidence_id"] = None
    selection["review_evidence_sha256"] = None
    with pytest.raises(MemoryContractError, match="requires sealed review evidence"):
        MemoryContractConfig.from_mapping(mapping)


def test_controlled_shared_memory_accepts_bound_review_evidence() -> None:
    mapping = _mapping()

    config = MemoryContractConfig.from_mapping(mapping)

    shared = next(
        item for item in config.capsules[0].selected_memories if item.scope == "controlled_shared"
    )
    assert shared.review_evidence_id == config.review_evidence[0].review_evidence_id
    assert shared.review_evidence_sha256 == config.review_evidence[0].canonical_digest()


def test_controlled_shared_review_must_be_an_evidence_reference() -> None:
    mapping = _mapping()
    selection = _selection(mapping, "controlled_shared")
    selection["evidence_reference_ids"] = ["99999999-9999-9999-9999-999999999904"]
    with pytest.raises(MemoryContractError, match="include its review"):
        MemoryContractConfig.from_mapping(mapping)


def test_controlled_shared_review_digest_is_exact() -> None:
    mapping = _mapping()
    _selection(mapping, "controlled_shared")["review_evidence_sha256"] = "f" * 64
    with pytest.raises(MemoryContractError, match="digest drifted"):
        MemoryContractConfig.from_mapping(mapping)


def test_controlled_shared_unknown_review_id_is_rejected() -> None:
    mapping = _mapping()
    selection = _selection(mapping, "controlled_shared")
    selection["review_evidence_id"] = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    selection["review_evidence_sha256"] = "f" * 64
    references = selection["evidence_reference_ids"]
    assert isinstance(references, list)
    references[0] = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    with pytest.raises(MemoryContractError, match="review evidence is unknown"):
        MemoryContractConfig.from_mapping(mapping)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tenant_id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        ("reviewer_user_id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        ("workspace_id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        ("memory_id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        ("memory_version", 2),
        ("content_sha256", "f" * 64),
    ],
)
def test_controlled_shared_resealed_cross_scope_review_is_rejected(
    field: str, value: object
) -> None:
    mapping = _mapping()
    _reviews(mapping)[0][field] = value
    _refresh_review_digest(mapping)
    with pytest.raises(MemoryContractError, match="binding drifted"):
        MemoryContractConfig.from_mapping(mapping)


def test_controlled_shared_review_must_predate_capsule() -> None:
    mapping = _mapping()
    _reviews(mapping)[0]["reviewed_at"] = "2026-08-11T06:00:01Z"
    _refresh_review_digest(mapping)
    with pytest.raises(MemoryContractError, match="predate Capsule"):
        MemoryContractConfig.from_mapping(mapping)


def test_private_memory_must_not_carry_shared_review_evidence() -> None:
    mapping = _mapping()
    _selections(mapping)[0]["review_evidence_id"] = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    with pytest.raises(MemoryContractError, match="private memory"):
        MemoryContractConfig.from_mapping(mapping)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("tenant_id", "Tenant"),
        ("owner_user_id", "Owner"),
        ("workspace_id", "Workspace"),
        ("agent_version_id", "AgentVersion"),
    ],
)
def test_candidate_binding_to_capsule_is_exact(field: str, message: str) -> None:
    mapping = _mapping()
    _candidate(mapping)[field] = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    with pytest.raises(MemoryContractError, match=message):
        MemoryContractConfig.from_mapping(mapping)


def test_candidate_requires_an_existing_capsule_invocation() -> None:
    mapping = _mapping()
    _candidate(mapping)["invocation_id"] = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    with pytest.raises(MemoryContractError, match="existing ContextCapsule"):
        MemoryContractConfig.from_mapping(mapping)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("total_tokens", "token accounting"),
        ("sensitivity_summary", "sensitivity summary"),
        ("compiler_policy_sha256", "policy digest"),
    ],
)
def test_capsule_accounting_and_policy_digest_drift_are_rejected(
    mutation: str, message: str
) -> None:
    mapping = _mapping()
    if mutation == "total_tokens":
        _capsule(mapping)[mutation] = 401
    elif mutation == "sensitivity_summary":
        summary = _capsule(mapping)[mutation]
        assert isinstance(summary, dict)
        summary["standard"] = 3
    else:
        _capsule(mapping)[mutation] = "f" * 64
    with pytest.raises(MemoryContractError, match=message):
        MemoryContractConfig.from_mapping(mapping)


def test_capsule_positions_and_memory_identity_are_closed() -> None:
    mapping = _mapping()
    _selections(mapping)[1]["position"] = 3
    with pytest.raises(MemoryContractError, match="positions"):
        MemoryContractConfig.from_mapping(mapping)

    mapping = _mapping()
    _selections(mapping)[1]["memory_id"] = _selections(mapping)[0]["memory_id"]
    _selections(mapping)[1]["memory_version"] = _selections(mapping)[0]["memory_version"]
    with pytest.raises(MemoryContractError, match="repeats"):
        MemoryContractConfig.from_mapping(mapping)


def test_capsule_ttl_and_timestamp_are_canonical_and_bounded() -> None:
    mapping = _mapping()
    _capsule(mapping)["expires_at"] = "2026-08-11T07:00:01Z"
    with pytest.raises(MemoryContractError, match="TTL"):
        MemoryContractConfig.from_mapping(mapping)

    mapping = _mapping()
    _capsule(mapping)["issued_at"] = "2026-08-11T14:00:00+08:00"
    with pytest.raises(MemoryContractError, match="canonical UTC"):
        MemoryContractConfig.from_mapping(mapping)

    mapping = _mapping()
    _capsule(mapping)["expires_at"] = _capsule(mapping)["issued_at"]
    with pytest.raises(MemoryContractError, match="after issued_at"):
        MemoryContractConfig.from_mapping(mapping)


def test_capsule_sensitive_item_ceiling_is_enforced() -> None:
    mapping = _mapping()
    budget = _policy(mapping)["budget"]
    assert isinstance(budget, dict)
    budget["max_sensitive_items"] = 0
    _selections(mapping)[1]["sensitivity"] = "sensitive"
    summary = _capsule(mapping)["sensitivity_summary"]
    assert isinstance(summary, dict)
    summary["personal"] = 0
    summary["sensitive"] = 1
    _refresh_policy_digest(mapping)
    with pytest.raises(MemoryContractError, match="sensitive item"):
        MemoryContractConfig.from_mapping(mapping)


def test_candidate_cannot_store_secret_or_inferred_sensitive_trait() -> None:
    mapping = _mapping()
    _candidate(mapping)["contains_secret"] = True
    with pytest.raises(MemoryContractError, match="secrets"):
        MemoryContractConfig.from_mapping(mapping)

    mapping = _mapping()
    _candidate(mapping)["inferred_sensitive_categories"] = ["health"]
    with pytest.raises(MemoryContractError, match="sensitive traits"):
        MemoryContractConfig.from_mapping(mapping)


def test_candidate_cannot_activate_itself() -> None:
    mapping = _mapping()
    _candidate(mapping)["active_memory_id"] = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    with pytest.raises(MemoryContractError, match="active memory"):
        MemoryContractConfig.from_mapping(mapping)


@pytest.mark.parametrize("scope", ["workspace_private", "controlled_shared"])
def test_sensitive_or_shared_candidate_requires_confirmation(scope: str) -> None:
    mapping = _mapping()
    candidate = _candidate(mapping)
    candidate["requested_scope"] = scope
    candidate["sensitivity"] = "sensitive" if scope == "workspace_private" else "standard"
    candidate["lifecycle_state"] = "candidate"
    candidate["requires_user_confirmation"] = False
    with pytest.raises(MemoryContractError, match="requires user confirmation"):
        MemoryContractConfig.from_mapping(mapping)


def test_candidate_policy_and_scope_references_are_exact() -> None:
    mapping = _mapping()
    _candidate(mapping)["memory_policy_id"] = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    with pytest.raises(MemoryContractError, match="unknown memory policy"):
        MemoryContractConfig.from_mapping(mapping)

    mapping = _mapping()
    scopes = _policy(mapping)["allowed_scopes"]
    assert isinstance(scopes, list)
    scopes.remove("workspace_private")
    _refresh_policy_digest(mapping)
    with pytest.raises(MemoryContractError, match="outside its policy"):
        MemoryContractConfig.from_mapping(mapping)


def test_verify_reports_dirty_source_gate_and_migration_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_memory_contract_config(CONFIG_PATH)
    gate = MemoryContractGate(REPO_ROOT)
    report = gate.verify(config, source=_provenance(clean=False))
    assert report.state is AdmissionState.INVALID
    assert any("clean checkout" in item for item in report.vetoes)

    monkeypatch.setattr(
        "omnibase.production.phase5_memory_contract.discover_migration_head",
        lambda *_args: "0016",
    )
    report = gate.verify(config, source=_provenance())
    assert report.state is AdmissionState.INVALID
    assert any("migration head drifted" in item for item in report.vetoes)


def test_verify_rejects_runtime_path_and_enabled_gate(tmp_path: Path) -> None:
    config = load_memory_contract_config(CONFIG_PATH)
    forbidden = tmp_path / "backend" / "src" / "omnibase" / "agent_memory" / "runtime.py"
    forbidden.parent.mkdir(parents=True)
    forbidden.write_text("runtime = True\n", encoding="utf-8")
    gate = MemoryContractGate(tmp_path)
    report = gate.verify(
        config,
        source=_provenance(),
        gate_values={"AGENT_RUNTIME_ENABLED": "true"},
    )
    assert report.state is AdmissionState.INVALID
    assert any("feature gates" in item for item in report.vetoes)
    assert any("forbidden Memory runtime" in item for item in report.vetoes)


def test_config_loader_rejects_symlink_when_supported(tmp_path: Path) -> None:
    copy_path = tmp_path / "memory.json"
    shutil.copyfile(CONFIG_PATH, copy_path)
    link = tmp_path / "linked.json"
    try:
        link.symlink_to(copy_path)
    except OSError:
        pytest.skip("symlink creation is not available")
    with pytest.raises(MemoryContractError, match="non-link"):
        load_memory_contract_config(link)


def test_contract_module_imports_no_database_network_or_runtime_stack() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    forbidden = (
        "sqlalchemy",
        "requests",
        "httpx",
        "omnibase.agent_alpha",
        "omnibase.task_ledger",
        "omnibase.rag",
        "omnibase.storage",
    )
    assert not any(name.startswith(forbidden) for name in imports)


def test_validator_validate_only_is_blocked_but_exits_zero() -> None:
    env = os.environ.copy()
    env.pop("AGENT_RUNTIME_ENABLED", None)
    env.pop("AGENT_PLANNER_ENABLED", None)
    env.pop("MULTI_AGENT_ENABLED", None)
    completed = subprocess.run(
        [sys.executable, str(VALIDATOR_PATH), "--validate-only"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["state"] == "blocked/not_proven"
    assert payload["contract_valid"] is True
    assert payload["memory_persistence_created"] is True
    assert payload["memory_runtime_created"] is False
    assert payload["root_env_accessed"] is False


def test_validator_report_output_must_remain_outside_repository() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR_PATH),
            "--validate-only",
            "--output",
            str(REPO_ROOT / ".tmp-memory-report.json"),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["state"] == "invalid/veto"
    assert "outside the repository" in payload["vetoes"][0]
    assert not (REPO_ROOT / ".tmp-memory-report.json").exists()
