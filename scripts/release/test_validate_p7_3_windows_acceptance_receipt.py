from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import sqlite3
from pathlib import Path

import pytest


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _repo() -> Path:
    return Path(__file__).resolve().parents[2]


def _validator():
    return _load(
        _repo() / "scripts/release/validate_p7_3_windows_acceptance_receipt.py",
        "p73_acceptance_validator_test",
    )


def _exporter():
    return _load(
        _repo() / "scripts/release/export_p7_3_component_bundles.py",
        "p73_acceptance_bundle_exporter_test",
    )


def _sha(value: str | bytes) -> str:
    raw = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


class _Ids:
    def __init__(self) -> None:
        self._value = 1

    def next(self) -> str:
        value = f"00000000-0000-4000-8000-{self._value:012x}"
        self._value += 1
        return value


def _ebook_source(tmp_path: Path) -> Path:
    root = tmp_path / "ebook"
    data = root / "data"
    data.mkdir(parents=True)
    connection = sqlite3.connect(data / "ebook.db")
    connection.executescript(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            source_path TEXT, doc_type TEXT DEFAULT 'markdown', content TEXT,
            plain_summary TEXT, imported_at TEXT, file_hash TEXT
        );
        CREATE TABLE sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER, level INTEGER,
            heading TEXT, content TEXT, plain_explanation TEXT, theme_tag TEXT,
            position INTEGER, FOREIGN KEY (doc_id) REFERENCES documents(id)
        );
        CREATE TABLE invariants (
            id INTEGER PRIMARY KEY AUTOINCREMENT, inv_id TEXT UNIQUE, title TEXT,
            content TEXT, plain_explanation TEXT, severity TEXT DEFAULT 'high',
            related_modules TEXT, related_source TEXT, phase TEXT
        );
        CREATE TABLE modules (
            id INTEGER PRIMARY KEY AUTOINCREMENT, module_key TEXT UNIQUE,
            name TEXT, description TEXT, source_paths TEXT, dependencies TEXT,
            invariants TEXT, verification TEXT, plain_summary TEXT
        );
        CREATE TABLE glossary (
            id INTEGER PRIMARY KEY AUTOINCREMENT, term TEXT UNIQUE,
            plain_explanation TEXT, technical_def TEXT, category TEXT
        );
        INSERT INTO documents
            (title, source_path, doc_type, content, plain_summary, imported_at, file_hash)
        VALUES
            ('Architecture', 'logical/handover.md', 'markdown', 'Bounded content',
             'Summary', '2026-08-30',
             'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
        INSERT INTO sections
            (doc_id, level, heading, content, plain_explanation, theme_tag, position)
        VALUES (1, 2, 'Boundary', 'No ambient authority', 'Owner review', 'security', 1);
        INSERT INTO invariants
            (inv_id, title, content, plain_explanation, severity, related_modules, phase)
        VALUES ('INV-TEST', 'Test', 'Bounded', 'Bounded', 'high', '["desktop"]', 'P7');
        INSERT INTO modules
            (module_key, name, description, source_paths, dependencies, invariants,
             verification, plain_summary)
        VALUES
            ('desktop', 'Desktop', 'Host', '["logical/source"]', '[]',
             '["INV-TEST"]', '["pytest"]', 'Summary');
        INSERT INTO glossary (term, plain_explanation, technical_def, category)
        VALUES ('fencing', 'Reject stale work', 'monotonic token', 'security');
        """
    )
    connection.commit()
    connection.close()
    return root


def _artifact(path: Path, *, name: str, kind: str) -> dict[str, object]:
    raw = path.read_bytes()
    return {"kind": kind, "name": name, "sha256": _sha(raw), "size": len(raw)}


def _component_runtime_files(component_root: Path) -> list[dict[str, object]]:
    files = []
    for path in sorted(
        (item for item in component_root.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(component_root).as_posix(),
    ):
        raw = path.read_bytes()
        files.append(
            {
                "path": f"components/{path.relative_to(component_root).as_posix()}",
                "sha256": _sha(raw),
                "size": len(raw),
            }
        )
    return files


def _build_fixture(tmp_path: Path, validator):  # noqa: C901, PLR0915
    ids = _Ids()
    source_commit = "a" * 40
    source_tree = "b" * 64
    owner_id = ids.next()
    workspace_id = ids.next()
    other_workspace_id = ids.next()
    run_id = ids.next()
    sandbox_id = ids.next()
    artifact_root = tmp_path / "artifacts"
    release = artifact_root / "release"
    runtime = artifact_root / "payload/runtime"
    component_root = runtime / "components"
    release.mkdir(parents=True)
    runtime.mkdir(parents=True)
    bundle_report = _exporter().export_component_bundles(
        repo_root=_repo(),
        ebook_root=_ebook_source(tmp_path),
        output_dir=component_root,
    )
    index = json.loads((component_root / "index.json").read_bytes())
    version_identities = {
        (item["component_id"], item["version"]): (
            item["manifest_sha256"],
            item["package_sha256"],
        )
        for item in index["packages"]
    }
    setup_path = release / "OmniBase-1.0.0-windows-x64-setup.exe"
    msi_path = release / "OmniBase-1.0.0-windows-x64.msi"
    manifest_path = runtime / "runtime-manifest.json"
    setup_path.write_bytes(b"p73 setup fixture\n")
    msi_path.write_bytes(b"p73 msi fixture\n")
    manifest = {
        "entrypoint": "OmniBase.RuntimeHost.exe",
        "files": _component_runtime_files(component_root),
        "schemaVersion": 1,
    }
    manifest_path.write_bytes(validator.canonical_json(manifest))
    screenshot_path = artifact_root / "screenshots/workbench.png"
    screenshot_path.parent.mkdir()
    screenshot_raw = (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + (1440).to_bytes(4, "big")
        + (900).to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00fixture"
    )
    screenshot_path.write_bytes(screenshot_raw)
    setup = _artifact(setup_path, name=setup_path.name, kind="burn_exe")
    msi = _artifact(msi_path, name=msi_path.name, kind="msi")
    runtime_manifest = _artifact(
        manifest_path, name=manifest_path.name, kind="runtime_manifest"
    )
    report = {
        "artifacts": [
            {
                "kind": "burn_exe",
                "path": f"release/{setup_path.name}",
                "sha256": setup["sha256"],
                "size": setup["size"],
            },
            {
                "kind": "msi",
                "path": f"release/{msi_path.name}",
                "sha256": msi["sha256"],
                "size": msi["size"],
            },
        ],
        "authenticode_verified": False,
        "clean_windows_lifecycle_verified": False,
        "component_bundle": bundle_report,
        "platform": "windows-x64",
        "product": "OmniBase",
        "production_ready": False,
        "required_product_journeys_verified": False,
        "runtime_manifest_pin": {
            "manifest_sha256": runtime_manifest["sha256"],
            "packaged_asar_verified": True,
            "placeholder_absent": True,
            "staged_dist_verified": True,
        },
        "schema_version": 1,
        "source_clean": True,
        "source_commit": source_commit,
        "source_mode": "clean-release",
        "source_tree_sha256": source_tree,
        "version": "1.0.0",
    }
    report_path = artifact_root / "desktop-build-report.json"
    report_path.write_bytes(validator.canonical_json(report))
    build_report = _artifact(report_path, name=report_path.name, kind="build_report")
    families: list[dict[str, object]] = []
    for family, component_id in validator._FAMILIES.items():
        versions = [
            {
                "manifest_sha256": version_identities[(component_id, version)][0],
                "package_sha256": version_identities[(component_id, version)][1],
                "version": version,
            }
            for version in ("1.0.0", "1.1.0")
        ]
        version_map = {item["version"]: item for item in versions}
        health_by_generation: dict[int, tuple[str, str]] = {}
        lifecycle = []
        for index_value, (
            operation,
            version,
            generation,
            expected_revision,
            result_revision,
        ) in enumerate(validator._LIFECYCLE):
            operation_id = ids.next()
            request_sha256 = _sha(
                f"{family}:{operation}:{index_value}:{expected_revision}:request"
            )
            owner_review = None
            if operation in validator._MUTATIONS:
                owner_review = {
                    "decision": "approved",
                    "expected_revision": expected_revision,
                    "explicit_owner_action": True,
                    "owner_id": owner_id,
                    "proposal_id": ids.next(),
                    "request_sha256": request_sha256,
                    "review_id": ids.next(),
                }
            health = None
            if operation == "activate":
                runtime_identity = ids.next()
                workload_identity = _sha(
                    f"{family}:{version}:{generation}:workload-identity"
                )
                health = {
                    "binding_generation": generation,
                    "evidence_sha256": _sha(f"{family}:{version}:{generation}:health"),
                    "runtime_identity": runtime_identity,
                    "state": "healthy",
                    "workload_identity_sha256": workload_identity,
                }
                health_by_generation[generation] = (
                    runtime_identity,
                    workload_identity,
                )
            quiesce = None
            if operation in validator._QUIESCE_ACTIONS:
                quiesce = {
                    "automatic_replay": False,
                    "evidence_sha256": _sha(
                        f"{family}:{version}:{generation}:{operation}:quiesce"
                    ),
                    "new_calls_closed": True,
                    "owned_processes_zero": True,
                    "pending_effects": 0,
                    "prior_generation_fenced": True,
                    "unknown_effects": 0,
                }
            invocation = None
            if operation == "invoke":
                runtime_identity, workload_identity = health_by_generation[generation]
                network_required = family in {"mcp_connector", "sandbox_workload"}
                invocation = {
                    "budget_reserved": True,
                    "capability_action": "component.invoke",
                    "grant_id": ids.next(),
                    "network_fencing_token": generation if network_required else None,
                    "network_lease_id": ids.next() if network_required else None,
                    "network_lease_required": network_required,
                    "operation_generation": generation,
                    "remaining_budget": {
                        "bytes": 4096,
                        "calls": 4,
                        "concurrency": 1,
                        "cost_micros": 0,
                        "retries": 0,
                        "tokens": 1024,
                        "wall_ms": 30000,
                    },
                    "resource_version": 1,
                    "result_sha256": _sha(f"{family}:{version}:{generation}:result"),
                    "revocation_clear": True,
                    "runtime_identity": runtime_identity,
                    "scope_revalidated": True,
                    "settled": True,
                    "ticket_operation_id": operation_id,
                    "workload_fencing_token": generation,
                    "workload_identity_sha256": workload_identity,
                    "workload_lease_id": ids.next(),
                }
            lifecycle.append(
                {
                    "acceptance_run_id": run_id,
                    "automatic_replay": False,
                    "binding_generation": generation,
                    "dispatch_count": 1,
                    "effect_state": "committed",
                    "expected_revision": expected_revision,
                    "health": health,
                    "installation_present": operation != "uninstall",
                    "installation_revision": result_revision,
                    "invocation": invocation,
                    "manifest_sha256": version_map[version]["manifest_sha256"],
                    "operation": operation,
                    "operation_id": operation_id,
                    "owner_review": owner_review,
                    "package_sha256": version_map[version]["package_sha256"],
                    "quiesce": quiesce,
                    "request_sha256": request_sha256,
                    "sandbox_instance_id": sandbox_id,
                    "status": "succeeded",
                    "version": version,
                    "workspace_id": workspace_id,
                }
            )
        families.append(
            {
                "acceptance_run_id": run_id,
                "component_id": component_id,
                "family": family,
                "lifecycle": lifecycle,
                "sandbox_instance_id": sandbox_id,
                "versions": versions,
                "workspace_id": workspace_id,
            }
        )
    receipt = {
        "artifacts": {
            "build_report": build_report,
            "msi": msi,
            "runtime_manifest": runtime_manifest,
            "setup_exe": setup,
        },
        "claims": {
            "authenticode_verified": False,
            "human_visual_review_verified": False,
            "production_ready": False,
            "publisher_signature_verified": False,
            "release_authorized": False,
        },
        "effect_closure": {
            "automatic_replay": False,
            "pending_count": 0,
            "reconciliation_required": False,
            "unknown_count": 0,
        },
        "emergency_stop": {
            "acceptance_run_id": run_id,
            "audit_available": True,
            "automatic_replay": False,
            "non_core_fenced": True,
            "operation_id": ids.next(),
            "pending_effects": 0,
            "sandbox_instance_id": sandbox_id,
            "settings_available": True,
            "standard_workbench_available": True,
            "status": "succeeded",
            "unknown_effects": 0,
            "workspace_id": workspace_id,
        },
        "evidence_class": "engineering_only",
        "families": families,
        "p7_1_regression": {
            "acceptance_run_id": run_id,
            "automatic_replay": False,
            "file_list_read": True,
            "logical_paths_only": True,
            "mcp_read_only": True,
            "mcp_tools": list(validator._MCP_TOOLS),
            "operation_id": ids.next(),
            "owner_folder_gesture": True,
            "sandbox_instance_id": sandbox_id,
            "status": "succeeded",
            "utf8_file_read": True,
            "workspace_id": workspace_id,
            "write_attempt_rejected": True,
        },
        "restart_recovery": {
            "acceptance_run_id": run_id,
            "active_generations_revalidated": True,
            "automatic_replay": False,
            "operation_id": ids.next(),
            "pending_effects": 0,
            "recovered_to_committed_state": True,
            "sandbox_instance_id": sandbox_id,
            "stale_runtimes_fenced": True,
            "status": "succeeded",
            "unknown_effects": 0,
            "workspace_id": workspace_id,
        },
        "run_window": {
            "completed_at": "2026-08-30T03:00:00Z",
            "started_at": "2026-08-30T01:00:00Z",
        },
        "schema": validator.RECEIPT_SCHEMA,
        "scope": {
            "acceptance_run_id": run_id,
            "owner_id": owner_id,
            "sandbox_instance_id": sandbox_id,
            "workspace_id": workspace_id,
        },
        "screenshots": {
            "acceptance_run_id": run_id,
            "items": [
                {
                    "height": 900,
                    "path": "screenshots/workbench.png",
                    "sha256": _sha(screenshot_raw),
                    "width": 1440,
                }
            ],
            "review": "pending_visual_review",
            "sandbox_instance_id": sandbox_id,
            "status": "captured",
            "workspace_id": workspace_id,
        },
        "source": {
            "clean": True,
            "commit": source_commit,
            "mode": "clean-release",
            "tree_sha256": source_tree,
        },
        "target": {
            "disposable": True,
            "host_configuration_modified": False,
            "instance_count": 1,
            "instance_ids": [sandbox_id],
            "kind": "windows_sandbox",
            "os_version": "Windows 11 Enterprise 10.0.22621",
            "preexisting_install": False,
            "user": "WDAGUtilityAccount",
        },
        "uninstall": {
            "acceptance_run_id": run_id,
            "application_files_removed": True,
            "exit_code": 0,
            "listening_ports_after_uninstall": [],
            "registration_removed": True,
            "remaining_processes": [],
            "sandbox_instance_id": sandbox_id,
            "shortcuts_removed": True,
            "status": "succeeded",
            "user_data_retained": True,
            "workspace_id": workspace_id,
        },
        "workspace_isolation": {
            "acceptance_run_id": run_id,
            "automatic_replay": False,
            "cross_workspace_action_rejected": True,
            "first_frame_projection_empty": True,
            "operation_id": ids.next(),
            "other_workspace_id": other_workspace_id,
            "sandbox_instance_id": sandbox_id,
            "source_workspace_id": workspace_id,
            "standard_workbench_available": True,
            "status": "succeeded",
        },
    }
    return receipt, artifact_root, source_commit


def _write_receipt(path: Path, validator, receipt: dict[str, object]) -> None:
    path.write_bytes(validator.canonical_json(receipt))


def _reject(validator, receipt: dict[str, object], code: str) -> None:
    with pytest.raises(validator.P73AcceptanceReceiptError, match=code):
        validator.validate_receipt(receipt)


def _load_external(validator, path: Path, artifact_root: Path, commit: str):
    return validator.load_and_validate_receipt(
        path,
        artifact_root=artifact_root,
        evidence_root=artifact_root,
        expected_source_commit=commit,
    )


def _refresh_build_report_claim(
    validator, receipt: dict[str, object], artifact_root: Path
) -> None:
    report_path = artifact_root / "desktop-build-report.json"
    receipt["artifacts"]["build_report"] = _artifact(
        report_path, name=report_path.name, kind="build_report"
    )


def test_schema_is_closed_and_tracks_the_validator_contract() -> None:
    schema = json.loads(
        (
            _repo() / "scripts/release/p7_3_windows_acceptance_receipt.schema.json"
        ).read_bytes()
    )
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert set(schema["required"]) == set(schema["properties"])

    def visit(value: object) -> None:
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value.get("additionalProperties") is False
                assert set(value.get("required", [])) == set(
                    value.get("properties", {})
                )
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)


def test_real_lifecycle_fixture_matches_backend_actions_and_revisions(
    tmp_path: Path,
) -> None:
    validator = _validator()
    receipt, _, _ = _build_fixture(tmp_path, validator)
    assert validator.validate_receipt(receipt)["valid"] is True
    for family in receipt["families"]:
        lifecycle = family["lifecycle"]
        assert [step["operation"] for step in lifecycle] == [
            item[0] for item in validator._LIFECYCLE
        ]
        assert [step["expected_revision"] for step in lifecycle] == [
            0,
            1,
            2,
            3,
            3,
            4,
            5,
            6,
            6,
            7,
            8,
            8,
            9,
        ]
        assert [step["installation_revision"] for step in lifecycle] == [
            1,
            2,
            3,
            3,
            4,
            5,
            6,
            6,
            7,
            8,
            8,
            9,
            10,
        ]
        assert lifecycle[-1]["installation_present"] is False


def test_actual_artifacts_bundle_and_runtime_manifest_complete_evidence(
    tmp_path: Path,
) -> None:
    validator = _validator()
    receipt, artifact_root, commit = _build_fixture(tmp_path, validator)
    path = tmp_path / "receipt.json"
    _write_receipt(path, validator, receipt)
    pure = validator.validate_receipt(receipt)
    assert pure["engineering_evidence_complete"] is False
    result = _load_external(validator, path, artifact_root, commit)
    assert result["engineering_evidence_complete"] is True
    assert result["external_evidence_verified"] is True
    assert result["production_ready"] is False
    assert result["human_visual_review_verified"] is False


@pytest.mark.parametrize(
    ("index_value", "field", "value", "code"),
    [
        (5, "operation", "bind", "p73_acceptance_lifecycle_identity_invalid"),
        (6, "operation", "bind", "p73_acceptance_lifecycle_identity_invalid"),
        (8, "operation", "bind", "p73_acceptance_lifecycle_identity_invalid"),
        (4, "expected_revision", 2, "p73_acceptance_expected_revision_invalid"),
        (7, "installation_revision", 7, "p73_acceptance_installation_revision_invalid"),
        (8, "binding_generation", 2, "p73_acceptance_binding_generation_invalid"),
        (
            12,
            "installation_present",
            True,
            "p73_acceptance_installation_presence_invalid",
        ),
    ],
)
def test_illegal_bind_revision_generation_or_uninstall_presence_is_rejected(
    tmp_path: Path, index_value: int, field: str, value: object, code: str
) -> None:
    validator = _validator()
    receipt, _, _ = _build_fixture(tmp_path, validator)
    receipt["families"][0]["lifecycle"][index_value][field] = value
    _reject(validator, receipt, code)


@pytest.mark.parametrize(
    ("index_value", "field", "value", "code"),
    [
        (2, "health", None, "p73_acceptance_health_fields_invalid"),
        (0, "health", {}, "p73_acceptance_unexpected_health_evidence"),
        (4, "quiesce", None, "p73_acceptance_quiesce_fields_invalid"),
        (2, "quiesce", {}, "p73_acceptance_unexpected_quiesce_evidence"),
    ],
)
def test_health_and_quiesce_are_bound_evidence_not_fake_actions(
    tmp_path: Path, index_value: int, field: str, value: object, code: str
) -> None:
    validator = _validator()
    receipt, _, _ = _build_fixture(tmp_path, validator)
    receipt["families"][0]["lifecycle"][index_value][field] = value
    _reject(validator, receipt, code)


def test_invocation_must_match_preceding_activation_identity(tmp_path: Path) -> None:
    validator = _validator()
    receipt, _, _ = _build_fixture(tmp_path, validator)
    invocation = receipt["families"][0]["lifecycle"][3]["invocation"]
    invocation["runtime_identity"] = _Ids().next()
    _reject(validator, receipt, "p73_acceptance_invocation_identity_invalid")

    receipt, _, _ = _build_fixture(tmp_path / "workload", validator)
    invocation = receipt["families"][0]["lifecycle"][3]["invocation"]
    invocation["workload_identity_sha256"] = "f" * 64
    _reject(validator, receipt, "p73_acceptance_invocation_identity_invalid")

    receipt, _, _ = _build_fixture(tmp_path / "ticket", validator)
    invocation = receipt["families"][0]["lifecycle"][3]["invocation"]
    invocation["ticket_operation_id"] = _Ids().next()
    _reject(validator, receipt, "p73_acceptance_invocation_identity_invalid")


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("dispatch_count", 2, "p73_acceptance_lifecycle_dispatch_count_invalid"),
        ("automatic_replay", True, "p73_acceptance_automatic_replay_forbidden"),
        ("effect_state", "pending", "p73_acceptance_lifecycle_identity_invalid"),
    ],
)
def test_every_dispatch_is_exactly_once_committed_and_never_auto_replayed(
    tmp_path: Path, field: str, value: object, code: str
) -> None:
    validator = _validator()
    receipt, _, _ = _build_fixture(tmp_path, validator)
    receipt["families"][0]["lifecycle"][3][field] = value
    _reject(validator, receipt, code)


def test_mutations_require_independent_exact_owner_reviews(tmp_path: Path) -> None:
    validator = _validator()
    receipt, _, _ = _build_fixture(tmp_path, validator)
    mutations = [
        step
        for family in receipt["families"]
        for step in family["lifecycle"]
        if step["operation"] in validator._MUTATIONS
    ]
    assert len({step["request_sha256"] for step in mutations}) == len(mutations)
    assert len({step["owner_review"]["proposal_id"] for step in mutations}) == len(
        mutations
    )
    assert len({step["owner_review"]["review_id"] for step in mutations}) == len(
        mutations
    )

    first, second = mutations[:2]
    second["owner_review"]["proposal_id"] = first["owner_review"]["proposal_id"]
    _reject(validator, receipt, "p73_acceptance_owner_review_reused")


def test_owner_review_request_and_expected_revision_cannot_drift(
    tmp_path: Path,
) -> None:
    validator = _validator()
    receipt, _, _ = _build_fixture(tmp_path, validator)
    step = receipt["families"][0]["lifecycle"][0]
    step["owner_review"]["request_sha256"] = "f" * 64
    _reject(validator, receipt, "p73_acceptance_owner_review_identity_invalid")

    receipt, _, _ = _build_fixture(tmp_path / "revision", validator)
    step = receipt["families"][0]["lifecycle"][1]
    step["owner_review"]["expected_revision"] = 0
    _reject(validator, receipt, "p73_acceptance_owner_review_identity_invalid")

    receipt, _, _ = _build_fixture(tmp_path / "invoke", validator)
    receipt["families"][0]["lifecycle"][3]["owner_review"] = copy.deepcopy(
        receipt["families"][0]["lifecycle"][2]["owner_review"]
    )
    _reject(validator, receipt, "p73_acceptance_unexpected_owner_review")


def test_request_sha_cannot_be_reused_across_mutations_or_invocations(
    tmp_path: Path,
) -> None:
    validator = _validator()
    receipt, _, _ = _build_fixture(tmp_path, validator)
    first, second = receipt["families"][0]["lifecycle"][:2]
    second["request_sha256"] = first["request_sha256"]
    second["owner_review"]["request_sha256"] = first["request_sha256"]
    _reject(validator, receipt, "p73_acceptance_request_digest_reused")


def test_component_versions_are_bound_to_actual_bundle_index(tmp_path: Path) -> None:
    validator = _validator()
    receipt, artifact_root, commit = _build_fixture(tmp_path, validator)
    family = receipt["families"][0]
    family["versions"][0]["manifest_sha256"] = "f" * 64
    for step in family["lifecycle"]:
        if step["version"] == "1.0.0":
            step["manifest_sha256"] = "f" * 64
    assert validator.validate_receipt(receipt)["valid"] is True
    path = tmp_path / "receipt.json"
    _write_receipt(path, validator, receipt)
    with pytest.raises(
        validator.P73AcceptanceReceiptError,
        match="p73_acceptance_component_version_binding_invalid",
    ):
        _load_external(validator, path, artifact_root, commit)


def test_build_report_uses_real_five_field_bundle_report_not_runtime_projection(
    tmp_path: Path,
) -> None:
    validator = _validator()
    receipt, artifact_root, commit = _build_fixture(tmp_path, validator)
    report_path = artifact_root / "desktop-build-report.json"
    report = json.loads(report_path.read_bytes())
    bundle = report["component_bundle"]
    report["component_bundle"] = {
        "file_count": bundle["file_count"],
        "total_bytes": bundle["output_bytes"],
        "tree_sha256": bundle["tree_sha256"],
    }
    report_path.write_bytes(validator.canonical_json(report))
    _refresh_build_report_claim(validator, receipt, artifact_root)
    path = tmp_path / "receipt.json"
    _write_receipt(path, validator, receipt)
    with pytest.raises(
        validator.P73AcceptanceReceiptError,
        match="p73_acceptance_build_report_component_bundle_invalid",
    ):
        _load_external(validator, path, artifact_root, commit)


def test_runtime_manifest_component_projection_is_closed_and_digest_bound(
    tmp_path: Path,
) -> None:
    validator = _validator()
    receipt, artifact_root, commit = _build_fixture(tmp_path, validator)
    manifest_path = artifact_root / "payload/runtime/runtime-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"].pop()
    manifest_path.write_bytes(validator.canonical_json(manifest))
    receipt["artifacts"]["runtime_manifest"] = _artifact(
        manifest_path, name=manifest_path.name, kind="runtime_manifest"
    )
    report_path = artifact_root / "desktop-build-report.json"
    report = json.loads(report_path.read_bytes())
    report["runtime_manifest_pin"]["manifest_sha256"] = receipt["artifacts"][
        "runtime_manifest"
    ]["sha256"]
    report_path.write_bytes(validator.canonical_json(report))
    _refresh_build_report_claim(validator, receipt, artifact_root)
    path = tmp_path / "receipt.json"
    _write_receipt(path, validator, receipt)
    with pytest.raises(
        validator.P73AcceptanceReceiptError,
        match="p73_acceptance_runtime_component_set_invalid",
    ):
        _load_external(validator, path, artifact_root, commit)


@pytest.mark.parametrize(
    ("section", "field", "value", "code"),
    [
        (
            "effect_closure",
            "pending_count",
            1,
            "p73_acceptance_pending_effect_forbidden",
        ),
        (
            "effect_closure",
            "unknown_count",
            1,
            "p73_acceptance_unknown_effect_forbidden",
        ),
        (
            "effect_closure",
            "automatic_replay",
            True,
            "p73_acceptance_automatic_replay_forbidden",
        ),
        (
            "emergency_stop",
            "standard_workbench_available",
            False,
            "p73_acceptance_emergency_stop_incomplete",
        ),
        (
            "restart_recovery",
            "stale_runtimes_fenced",
            False,
            "p73_acceptance_restart_recovery_incomplete",
        ),
        (
            "workspace_isolation",
            "first_frame_projection_empty",
            False,
            "p73_acceptance_workspace_isolation_incomplete",
        ),
    ],
)
def test_effect_recovery_and_workspace_isolation_remain_fail_closed(
    tmp_path: Path, section: str, field: str, value: object, code: str
) -> None:
    validator = _validator()
    receipt, _, _ = _build_fixture(tmp_path, validator)
    receipt[section][field] = value
    _reject(validator, receipt, code)


@pytest.mark.parametrize(
    ("section", "field", "value", "code"),
    [
        (
            "emergency_stop",
            "settings_available",
            False,
            "p73_acceptance_emergency_stop_incomplete",
        ),
        (
            "emergency_stop",
            "audit_available",
            False,
            "p73_acceptance_emergency_stop_incomplete",
        ),
        (
            "emergency_stop",
            "pending_effects",
            1,
            "p73_acceptance_pending_effect_forbidden",
        ),
        (
            "restart_recovery",
            "active_generations_revalidated",
            False,
            "p73_acceptance_restart_recovery_incomplete",
        ),
        (
            "restart_recovery",
            "recovered_to_committed_state",
            False,
            "p73_acceptance_restart_recovery_incomplete",
        ),
        (
            "restart_recovery",
            "automatic_replay",
            True,
            "p73_acceptance_automatic_replay_forbidden",
        ),
    ],
)
def test_emergency_and_restart_receipts_require_complete_core_recovery(
    tmp_path: Path, section: str, field: str, value: object, code: str
) -> None:
    validator = _validator()
    receipt, _, _ = _build_fixture(tmp_path, validator)
    receipt[section][field] = value
    _reject(validator, receipt, code)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        (
            "mcp_tools",
            [
                "omnibase_files_list",
                "omnibase_files_read",
                "omnibase_files_hash",
                "omnibase_text_search",
                "omnibase_files_write",
            ],
            "p73_acceptance_mcp_tool_set_invalid",
        ),
        (
            "write_attempt_rejected",
            False,
            "p73_acceptance_p71_regression_incomplete",
        ),
        (
            "logical_paths_only",
            False,
            "p73_acceptance_p71_regression_incomplete",
        ),
    ],
)
def test_p71_regression_never_admits_write_or_physical_path_authority(
    tmp_path: Path, field: str, value: object, code: str
) -> None:
    validator = _validator()
    receipt, _, _ = _build_fixture(tmp_path, validator)
    receipt["p7_1_regression"][field] = value
    _reject(validator, receipt, code)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        (
            "remaining_processes",
            ["OmniBase.exe"],
            "p73_acceptance_process_cleanup_incomplete",
        ),
        (
            "listening_ports_after_uninstall",
            [8765],
            "p73_acceptance_port_cleanup_incomplete",
        ),
        ("exit_code", 1, "p73_acceptance_uninstall_failed"),
    ],
)
def test_application_uninstall_requires_zero_processes_and_ports(
    tmp_path: Path, field: str, value: object, code: str
) -> None:
    validator = _validator()
    receipt, _, _ = _build_fixture(tmp_path, validator)
    receipt["uninstall"][field] = value
    _reject(validator, receipt, code)


def test_acceptance_is_bound_to_exactly_one_sandbox_instance(tmp_path: Path) -> None:
    validator = _validator()
    receipt, _, _ = _build_fixture(tmp_path, validator)
    receipt["target"]["instance_count"] = 2
    receipt["target"]["instance_ids"].append(_Ids().next())
    _reject(validator, receipt, "p73_acceptance_single_instance_required")


def test_operation_grant_and_lease_identities_cannot_be_reused(tmp_path: Path) -> None:
    validator = _validator()
    receipt, _, _ = _build_fixture(tmp_path, validator)
    first = receipt["families"][0]["lifecycle"][0]
    receipt["families"][1]["lifecycle"][0]["operation_id"] = first["operation_id"]
    _reject(validator, receipt, "p73_acceptance_operation_id_duplicate")

    receipt, _, _ = _build_fixture(tmp_path / "grant", validator)
    first_invocation = receipt["families"][0]["lifecycle"][3]["invocation"]
    second_invocation = receipt["families"][1]["lifecycle"][3]["invocation"]
    second_invocation["grant_id"] = first_invocation["grant_id"]
    _reject(validator, receipt, "p73_acceptance_invocation_identity_reused")

    receipt, _, _ = _build_fixture(tmp_path / "lease", validator)
    first_invocation = receipt["families"][0]["lifecycle"][3]["invocation"]
    second_invocation = receipt["families"][1]["lifecycle"][3]["invocation"]
    second_invocation["workload_lease_id"] = first_invocation["workload_lease_id"]
    _reject(validator, receipt, "p73_acceptance_invocation_identity_reused")


@pytest.mark.parametrize(
    ("family_index", "field", "value", "code"),
    [
        (
            0,
            "network_lease_required",
            True,
            "p73_acceptance_network_lease_invalid",
        ),
        (
            2,
            "network_lease_required",
            False,
            "p73_acceptance_network_lease_invalid",
        ),
        (
            0,
            "revocation_clear",
            False,
            "p73_acceptance_invocation_revalidation_incomplete",
        ),
    ],
)
def test_invocation_network_and_revocation_evidence_is_coherent(
    tmp_path: Path, family_index: int, field: str, value: object, code: str
) -> None:
    validator = _validator()
    receipt, _, _ = _build_fixture(tmp_path, validator)
    receipt["families"][family_index]["lifecycle"][3]["invocation"][field] = value
    _reject(validator, receipt, code)


def test_invocation_budget_values_are_nonnegative_strict_integers(
    tmp_path: Path,
) -> None:
    validator = _validator()
    receipt, _, _ = _build_fixture(tmp_path, validator)
    invocation = receipt["families"][0]["lifecycle"][3]["invocation"]
    invocation["remaining_budget"]["calls"] = -1
    _reject(validator, receipt, "p73_acceptance_budget_value_invalid")

    receipt, _, _ = _build_fixture(tmp_path / "bool", validator)
    invocation = receipt["families"][0]["lifecycle"][3]["invocation"]
    invocation["remaining_budget"]["calls"] = False
    _reject(validator, receipt, "p73_acceptance_budget_value_invalid")


@pytest.mark.parametrize(
    ("section", "field", "value", "code"),
    [
        (
            "screenshots",
            "review",
            "passed",
            "p73_acceptance_screenshot_posture_invalid",
        ),
        (
            "claims",
            "human_visual_review_verified",
            True,
            "p73_acceptance_claim_forbidden",
        ),
        ("claims", "production_ready", True, "p73_acceptance_claim_forbidden"),
        ("claims", "authenticode_verified", True, "p73_acceptance_claim_forbidden"),
        (
            "claims",
            "publisher_signature_verified",
            True,
            "p73_acceptance_claim_forbidden",
        ),
        ("claims", "release_authorized", True, "p73_acceptance_claim_forbidden"),
    ],
)
def test_visual_signing_production_and_release_claims_stay_false(
    tmp_path: Path, section: str, field: str, value: object, code: str
) -> None:
    validator = _validator()
    receipt, _, _ = _build_fixture(tmp_path, validator)
    receipt[section][field] = value
    _reject(validator, receipt, code)


def test_external_expected_head_and_artifact_digest_drift_are_rejected(
    tmp_path: Path,
) -> None:
    validator = _validator()
    receipt, artifact_root, commit = _build_fixture(tmp_path, validator)
    path = tmp_path / "receipt.json"
    _write_receipt(path, validator, receipt)
    with pytest.raises(
        validator.P73AcceptanceReceiptError,
        match="p73_acceptance_build_report_binding_invalid",
    ):
        _load_external(validator, path, artifact_root, "f" * 40)
    (artifact_root / "release/OmniBase-1.0.0-windows-x64-setup.exe").write_bytes(
        b"drift\n"
    )
    with pytest.raises(
        validator.P73AcceptanceReceiptError,
        match="p73_acceptance_artifact_digest_mismatch",
    ):
        _load_external(validator, path, artifact_root, commit)


def test_noncanonical_surrogate_and_linked_receipts_fail_closed(
    tmp_path: Path,
) -> None:
    validator = _validator()
    receipt, artifact_root, commit = _build_fixture(tmp_path, validator)
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    with pytest.raises(
        validator.P73AcceptanceReceiptError,
        match="p73_acceptance_receipt_not_canonical",
    ):
        _load_external(validator, path, artifact_root, commit)
    path.write_bytes(b'{"bad":"\\ud800"}\n')
    with pytest.raises(
        validator.P73AcceptanceReceiptError,
        match="p73_acceptance_receipt_json_invalid",
    ):
        _load_external(validator, path, artifact_root, commit)
    _write_receipt(path, validator, receipt)
    hardlink = tmp_path / "receipt-hardlink.json"
    os.link(path, hardlink)
    with pytest.raises(
        validator.P73AcceptanceReceiptError,
        match="p73_acceptance_receipt_identity_invalid",
    ):
        _load_external(validator, hardlink, artifact_root, commit)


def test_linked_artifacts_and_screenshot_byte_drift_fail_closed(tmp_path: Path) -> None:
    validator = _validator()
    receipt, artifact_root, commit = _build_fixture(tmp_path, validator)
    path = tmp_path / "receipt.json"
    _write_receipt(path, validator, receipt)
    setup = artifact_root / "release/OmniBase-1.0.0-windows-x64-setup.exe"
    os.link(setup, artifact_root / "release/setup-hardlink.exe")
    with pytest.raises(
        validator.P73AcceptanceReceiptError,
        match="p73_acceptance_artifact_identity_invalid",
    ):
        _load_external(validator, path, artifact_root, commit)

    receipt, artifact_root, commit = _build_fixture(tmp_path / "screenshot", validator)
    path = tmp_path / "screenshot-receipt.json"
    _write_receipt(path, validator, receipt)
    (artifact_root / "screenshots/workbench.png").write_bytes(b"not-a-png\n")
    with pytest.raises(
        validator.P73AcceptanceReceiptError,
        match="p73_acceptance_screenshot_digest_mismatch",
    ):
        _load_external(validator, path, artifact_root, commit)
