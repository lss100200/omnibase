"""P34.7A/B fail-closed production admission tests."""

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
    EvidenceReference,
    EvidenceStatus,
    GitSourceProvenance,
    ProductionCompositionConfig,
    ProductionCompositionGate,
    SourceScope,
    build_git_source_provenance,
    load_production_composition_config,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "deployment" / "production" / "composition.example.json"


def _mapping() -> dict[str, object]:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {
        "schema_version": 1,
        "phase": "P34.7A/B",
        "activation_requested": False,
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
        "components": {
            "core": {
                "service_identity": "spiffe://omnibase/production/core",
                "process_boundary": "independent",
                "accepts_browser_traffic": True,
                "executes_workspace_code": False,
                "credential_classes": ["database", "capability_signing"],
            },
            "runner": {
                "service_identity": "spiffe://omnibase/production/runner",
                "process_boundary": "independent",
                "accepts_browser_traffic": False,
                "executes_workspace_code": True,
                "credential_classes": ["runner_identity"],
            },
            "broker": {
                "service_identity": "spiffe://omnibase/production/network-broker",
                "process_boundary": "independent",
                "accepts_browser_traffic": False,
                "executes_workspace_code": False,
                "credential_classes": ["daemon_identity"],
            },
            "gateway": {
                "service_identity": "spiffe://omnibase/production/capability-gateway",
                "process_boundary": "independent",
                "accepts_browser_traffic": False,
                "executes_workspace_code": False,
                "credential_classes": ["capability_signing", "database_read_adapter"],
            },
        },
        "channels": [
            {
                "name": name,
                "source": source,
                "target": target,
                "transport": transport,
                "mutual_authentication": True,
                "server_owned_peer_identity": True,
                "logical_identifiers_only": True,
                "carries_browser_credentials": False,
            }
            for name, source, target, transport in (
                ("core_runner_mtls", "core", "runner", "mtls"),
                (
                    "runner_broker_private",
                    "runner",
                    "broker",
                    "private_unix_socket",
                ),
                ("runner_gateway_mtls", "runner", "gateway", "mtls"),
                ("broker_gateway_mtls", "broker", "gateway", "mtls"),
            )
        ],
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
    }


def _source(*, clean: bool = True) -> GitSourceProvenance:
    return GitSourceProvenance(
        git_commit="a" * 40,
        git_tree="b" * 40,
        remote_origin="https://github.com/lss100200/omnibase.git",
        clean=clean,
        dirty_paths=() if clean else (" M backend/src/omnibase/production/composition.py",),
        file_count=1,
        files=(("AGENTS.md", 1, "c" * 64),),
        manifest_sha256="d" * 64,
    )


def _single_passed_config(
    tmp_path: Path, *, activation_requested: bool = True
) -> ProductionCompositionConfig:
    mapping = _mapping()
    mapping["activation_requested"] = activation_requested
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
    digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    mapping["evidence"] = [
        {
            "id": "synthetic_complete_production_gate",
            "status": "passed",
            "path": "docs/evidence.json",
            "sha256": digest,
            "assertions": {
                "passed": True,
                "root_env_accessed": False,
                "business_database_migrated": False,
            },
            "required_for_activation": True,
        }
    ]
    return ProductionCompositionConfig.from_mapping(mapping)


def test_checked_in_contract_is_valid_but_explicitly_blocked() -> None:
    config = (
        load_production_composition_config(CONFIG_PATH)
        if CONFIG_PATH.exists()
        else ProductionCompositionConfig.from_mapping(_mapping())
    )

    report = ProductionCompositionGate(REPO_ROOT).validate_only(config)

    assert report.state is AdmissionState.BLOCKED
    assert report.activation_allowed is False
    assert "production activation remains explicitly disabled" in report.blockers
    assert any("current_source_linux_runner_12_of_12" in item for item in report.blockers)


def test_formal_gate_verifies_passed_component_evidence_but_keeps_missing_proofs_blocked() -> None:
    if not CONFIG_PATH.exists():
        pytest.skip("full public checkout is not mounted in the backend test image")
    config = load_production_composition_config(CONFIG_PATH)

    report = ProductionCompositionGate(REPO_ROOT).verify(config, source=_source())

    assert report.state is AdmissionState.BLOCKED
    assert report.activation_allowed is False
    assert report.vetoes == ()
    assert set(report.passed_evidence) == {
        "p34_5b_network_broker_attack_gate",
        "p34_5d_disposable_gateway_gate",
    }
    assert any("core_runner_mtls_production_roundtrip" in item for item in report.blockers)


def test_complete_evidence_and_explicit_activation_can_be_admitted(tmp_path: Path) -> None:
    config = _single_passed_config(tmp_path)

    report = ProductionCompositionGate(tmp_path).verify(config, source=_source())

    assert report.state is AdmissionState.READY
    assert report.activation_allowed is True
    assert report.blockers == ()
    assert report.vetoes == ()


def test_dirty_checkout_is_a_veto_even_when_all_evidence_passes(tmp_path: Path) -> None:
    config = _single_passed_config(tmp_path)

    report = ProductionCompositionGate(tmp_path).verify(config, source=_source(clean=False))

    assert report.state is AdmissionState.INVALID
    assert report.activation_allowed is False
    assert report.vetoes == ("production Gate requires a clean checkout",)


def test_evidence_digest_drift_is_a_veto(tmp_path: Path) -> None:
    config = _single_passed_config(tmp_path)
    reference = replace(config.evidence[0], sha256="0" * 64)
    config = replace(config, evidence=(reference,))

    report = ProductionCompositionGate(tmp_path).verify(config, source=_source())

    assert report.state is AdmissionState.INVALID
    assert report.activation_allowed is False
    assert report.vetoes == ("synthetic_complete_production_gate: sealed evidence SHA-256 drifted",)


def test_evidence_assertion_drift_is_a_veto(tmp_path: Path) -> None:
    config = _single_passed_config(tmp_path)
    reference = replace(config.evidence[0], assertions=(("passed", False),))
    config = replace(config, evidence=(reference,))

    report = ProductionCompositionGate(tmp_path).verify(config, source=_source())

    assert report.state is AdmissionState.INVALID
    assert report.vetoes == (
        "synthetic_complete_production_gate: evidence assertion failed: passed",
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda data: data["components"]["runner"].update({"accepts_browser_traffic": True}),
            "only Core",
        ),
        (
            lambda data: data["components"]["runner"].update(
                {"credential_classes": ["runner_identity", "database"]}
            ),
            "forbidden or unknown credential classes",
        ),
        (
            lambda data: data["components"]["gateway"].update(
                {"credential_classes": ["browser_cookie"]}
            ),
            "forbidden or unknown credential classes",
        ),
        (
            lambda data: data["components"]["gateway"].update(
                {"service_identity": data["components"]["runner"]["service_identity"]}
            ),
            "service identities must be unique",
        ),
        (
            lambda data: data["channels"][0].update({"target": "postgresql"}),
            "sealed topology",
        ),
        (
            lambda data: data["channels"][0].update({"server_owned_peer_identity": False}),
            "server-owned peer identity",
        ),
        (
            lambda data: data["channels"][2].update({"carries_browser_credentials": True}),
            "forbid Browser credentials",
        ),
    ],
)
def test_unsafe_composition_contracts_fail_closed(mutation, message: str) -> None:
    mapping = _mapping()
    mutation(mapping)

    with pytest.raises(ConfigurationError, match=message):
        ProductionCompositionConfig.from_mapping(mapping)


def test_passed_evidence_cannot_omit_hash_or_assertions() -> None:
    mapping = _mapping()
    mapping["evidence"][0]["status"] = "passed"
    mapping["evidence"][0]["sha256"] = None
    mapping["evidence"][0]["assertions"] = {}

    with pytest.raises(ConfigurationError, match="requires a path, SHA-256 and assertions"):
        ProductionCompositionConfig.from_mapping(mapping)


def test_root_env_cannot_enter_source_scope_or_evidence() -> None:
    mapping = _mapping()
    mapping["source"]["tracked_pathspecs"].append(".env")

    with pytest.raises(ConfigurationError, match=r"root \.env"):
        ProductionCompositionConfig.from_mapping(mapping)

    mapping = _mapping()
    mapping["evidence"][0]["path"] = ".env"
    with pytest.raises(ConfigurationError, match=r"root \.env"):
        ProductionCompositionConfig.from_mapping(mapping)


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_git_provenance_hashes_only_tracked_scope_and_ignores_root_env(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "test@example.invalid")
    _run_git(repo, "config", "user.name", "P34.7 test")
    _run_git(repo, "config", "core.autocrlf", "false")
    (repo / ".gitignore").write_text(".env\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "entry.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / ".env").write_text("MUST_NOT_BE_READ=synthetic\n", encoding="utf-8")
    _run_git(repo, "add", ".gitignore", "src/entry.py")
    _run_git(repo, "commit", "-m", "test source")
    _run_git(repo, "remote", "add", "origin", "https://github.com/lss100200/omnibase.git")
    scope = SourceScope(
        expected_repository="https://github.com/lss100200/omnibase.git",
        tracked_pathspecs=("src",),
        require_clean_checkout=True,
    )

    provenance = build_git_source_provenance(repo, scope)

    assert provenance.clean is True
    assert provenance.file_count == 1
    assert provenance.files[0][0] == "src/entry.py"
    assert all(path != ".env" for path, _, _ in provenance.files)


def test_git_provenance_reports_tracked_dirty_checkout(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "test@example.invalid")
    _run_git(repo, "config", "user.name", "P34.7 test")
    (repo / "source.txt").write_text("sealed\n", encoding="utf-8")
    _run_git(repo, "add", "source.txt")
    _run_git(repo, "commit", "-m", "test source")
    _run_git(repo, "remote", "add", "origin", "git@github.com:lss100200/omnibase.git")
    (repo / "source.txt").write_text("drifted\n", encoding="utf-8")
    scope = SourceScope(
        expected_repository="https://github.com/lss100200/omnibase.git",
        tracked_pathspecs=("source.txt",),
        require_clean_checkout=True,
    )

    provenance = build_git_source_provenance(repo, scope)

    assert provenance.clean is False
    assert any("source.txt" in path for path in provenance.dirty_paths)


def test_report_exposes_explicit_safety_negatives() -> None:
    config = (
        load_production_composition_config(CONFIG_PATH)
        if CONFIG_PATH.exists()
        else ProductionCompositionConfig.from_mapping(_mapping())
    )
    report = ProductionCompositionGate(REPO_ROOT).validate_only(config)

    payload = report.to_dict()

    assert payload["root_env_accessed"] is False
    assert payload["business_database_accessed"] is False
    assert payload["business_database_migrated"] is False
    assert payload["hostile_code_executed"] is False


def test_evidence_reference_requires_known_status() -> None:
    with pytest.raises(ConfigurationError, match="invalid status"):
        EvidenceReference.from_mapping(
            {
                "id": "bad",
                "status": "probably",
                "path": None,
                "sha256": None,
                "assertions": {},
                "required_for_activation": True,
            }
        )


def test_not_proven_evidence_is_never_counted_as_passed(tmp_path: Path) -> None:
    config = _single_passed_config(tmp_path)
    pending = replace(
        config.evidence[0],
        status=EvidenceStatus.NOT_PROVEN,
        path=None,
        sha256=None,
        assertions=(),
    )
    config = replace(config, evidence=(pending,))

    report = ProductionCompositionGate(tmp_path).verify(config, source=_source())

    assert report.state is AdmissionState.BLOCKED
    assert report.passed_evidence == ()
    assert report.blockers == ("synthetic_complete_production_gate: not_proven",)
