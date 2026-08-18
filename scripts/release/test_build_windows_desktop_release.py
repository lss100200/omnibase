from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _builder(repo: Path):
    path = repo / "scripts/release/build_windows_desktop_release.py"
    spec = importlib.util.spec_from_file_location("build_windows_desktop_release", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _template(
    tmp_path: Path, *, production_ready: bool = False, files: list[str] | None = None
) -> Path:
    value = {
        "schema_version": 2,
        "product": "OmniBase",
        "version": "1.0.0",
        "channel": "stable",
        "platform": "windows-x64",
        "publisher": "lss100200/omnibase",
        "runtime_profile": "personal-desktop-core",
        "migration_compatibility": {
            "minimum": "0016",
            "current": "0016",
            "maximum": "0016",
        },
        "feature_gates": {
            "agent_runtime_enabled": False,
            "agent_planner_enabled": False,
            "multi_agent_enabled": False,
            "mcp_runtime_enabled": False,
        },
        "optional_components": [
            {"id": "bge-m3", "bundled": False, "required": False},
            {"id": "hardened-sandbox", "bundled": False, "required": False},
            {"id": "postgresql-pgvector", "bundled": False, "required": False},
        ],
        "production_ready": production_ready,
        "limits": {"max_files": 10, "max_file_bytes": 1024, "max_total_bytes": 2048},
        "expected_files": files or ["OmniBase.exe", "resources/app.asar"],
    }
    path = tmp_path / "template.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _payload(tmp_path: Path) -> Path:
    payload = tmp_path / "payload"
    (payload / "resources").mkdir(parents=True)
    (payload / "OmniBase.exe").write_bytes(b"desktop executable")
    (payload / "resources/app.asar").write_bytes(b"renderer payload")
    return payload


def test_build_is_deterministic_closed_set_and_unsigned_by_default(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    payload = _payload(tmp_path)
    template = _template(tmp_path)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    manifest_a = builder.build_desktop_release(
        payload, first, source_commit="a" * 40, template_path=template
    )
    manifest_b = builder.build_desktop_release(
        payload, second, source_commit="a" * 40, template_path=template
    )
    assert manifest_a == manifest_b
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes().endswith(b"\n")
    assert manifest_a["version"] == "1.0.0"
    assert manifest_a["runtime_profile"] == "personal-desktop-core"
    assert manifest_a["production_ready"] is False
    assert manifest_a["publisher_signature_verified"] is False
    assert manifest_a["authenticode_verified"] is False
    assert [item["path"] for item in manifest_a["files"]] == [
        "OmniBase.exe",
        "resources/app.asar",
    ]
    assert manifest_a["total_size"] == sum(item["size"] for item in manifest_a["files"])
    assert all(len(item["sha256"]) == 64 for item in manifest_a["files"])
    assert builder.verify_desktop_release(payload, first) == manifest_a


def test_verify_rejects_payload_tampering(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    payload = _payload(tmp_path)
    manifest = tmp_path / "release.json"
    builder.build_desktop_release(
        payload, manifest, source_commit="b" * 40, template_path=_template(tmp_path)
    )
    (payload / "OmniBase.exe").write_bytes(b"tampered")
    with pytest.raises(builder.DesktopReleaseError, match="integrity_invalid"):
        builder.verify_desktop_release(payload, manifest)


@pytest.mark.parametrize("extra", ["unknown.txt", "nested/unknown.dll"])
def test_build_rejects_unknown_files(tmp_path: Path, extra: str) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    payload = _payload(tmp_path)
    target = payload / extra
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"unknown")
    with pytest.raises(builder.DesktopReleaseError, match="closed_set_drifted"):
        builder.build_desktop_release(
            payload,
            tmp_path / "release.json",
            source_commit="c" * 40,
            template_path=_template(tmp_path),
        )


def test_build_rejects_missing_expected_file_and_duplicate_allowlist(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    payload = _payload(tmp_path)
    (payload / "resources/app.asar").unlink()
    with pytest.raises(builder.DesktopReleaseError, match="closed_set_drifted"):
        builder.build_desktop_release(
            payload,
            tmp_path / "release.json",
            source_commit="d" * 40,
            template_path=_template(tmp_path),
        )
    duplicate = _template(tmp_path, files=["OmniBase.exe", "omnibase.EXE"])
    with pytest.raises(builder.DesktopReleaseError, match="duplicate_or_over_budget"):
        builder.load_template(duplicate)


@pytest.mark.parametrize(
    "path",
    [
        "../escape.exe",
        "/absolute.exe",
        "dir\\escape.exe",
        ".env",
        "secrets.env.local",
        "state.db",
        "disk.VHDX",
        "key.pem",
    ],
)
def test_template_rejects_escape_and_sensitive_paths(tmp_path: Path, path: str) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    with pytest.raises(
        builder.DesktopReleaseError, match="path_invalid|sensitive_path_forbidden"
    ):
        builder.load_template(_template(tmp_path, files=[path]))


def test_build_rejects_symlink_or_reparse_input(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    payload = _payload(tmp_path)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    link = payload / "linked.bin"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip(
            "symlink creation unavailable; Windows reparse guard shares this path"
        )
    template = _template(
        tmp_path, files=["OmniBase.exe", "resources/app.asar", "linked.bin"]
    )
    with pytest.raises(builder.DesktopReleaseError, match="link_or_reparse_forbidden"):
        builder.build_desktop_release(
            payload,
            tmp_path / "release.json",
            source_commit="e" * 40,
            template_path=template,
        )


def test_limits_are_hard_capped_and_enforced_before_manifest_write(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    payload = _payload(tmp_path)
    template = _template(tmp_path)
    value = json.loads(template.read_text(encoding="utf-8"))
    value["limits"]["max_file_bytes"] = 20
    value["limits"]["max_total_bytes"] = 25
    template.write_text(json.dumps(value), encoding="utf-8")
    output = tmp_path / "release.json"
    with pytest.raises(builder.DesktopReleaseError, match="total_size_over_budget"):
        builder.build_desktop_release(
            payload, output, source_commit="f" * 40, template_path=template
        )
    assert not output.exists()
    value["limits"]["max_total_bytes"] = builder.HARD_MAX_TOTAL_BYTES + 1
    template.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(builder.DesktopReleaseError, match="max_total_bytes_invalid"):
        builder.load_template(template)


def test_unsigned_release_cannot_claim_production_ready(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    payload = _payload(tmp_path)
    template = _template(tmp_path, production_ready=True)
    with pytest.raises(
        builder.DesktopReleaseError, match="unsigned_release_cannot_be_production_ready"
    ):
        builder.build_desktop_release(
            payload,
            tmp_path / "release.json",
            source_commit="1" * 40,
            template_path=template,
        )
    status = builder.SignatureStatus(
        publisher_signature_verified=True, authenticode_verified=True
    )
    manifest = builder.build_desktop_release(
        payload,
        tmp_path / "signed.json",
        source_commit="1" * 40,
        template_path=template,
        signature_status=status,
    )
    assert manifest["production_ready"] is True
    assert manifest["publisher_signature_verified"] is True
    assert manifest["authenticode_verified"] is True


def test_manifest_tampered_signing_state_fails_closed(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    payload = _payload(tmp_path)
    manifest_path = tmp_path / "release.json"
    manifest = builder.build_desktop_release(
        payload,
        manifest_path,
        source_commit="2" * 40,
        template_path=_template(tmp_path),
    )
    manifest["production_ready"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(
        builder.DesktopReleaseError, match="unsigned_release_cannot_be_production_ready"
    ):
        builder.verify_desktop_release(payload, manifest_path)


def test_manifest_metadata_tampering_and_noncanonical_order_fail_closed(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    payload = _payload(tmp_path)
    manifest_path = tmp_path / "release.json"
    manifest = builder.build_desktop_release(
        payload,
        manifest_path,
        source_commit="4" * 40,
        template_path=_template(tmp_path),
    )
    manifest["product"] = "AttackerProduct"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(builder.DesktopReleaseError, match="product_invalid"):
        builder.verify_desktop_release(payload, manifest_path)

    manifest["product"] = "OmniBase"
    manifest["files"].reverse()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(builder.DesktopReleaseError, match="files_not_canonical"):
        builder.verify_desktop_release(payload, manifest_path)


def test_repository_template_is_fail_closed_and_schema_is_exact(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    template, _limits, expected = builder.load_template(
        repo / "deployment/release/windows/desktop-release.template.json"
    )
    schema = json.loads(
        (repo / "deployment/release/windows/desktop-release.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert template["version"] == "1.0.0"
    assert template["production_ready"] is False
    assert expected == ("OmniBase.exe",)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["files"]["items"]["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    signature_rule = schema["allOf"][0]
    assert (
        signature_rule["then"]["properties"]["publisher_signature_verified"]["const"]
        is True
    )
    assert (
        signature_rule["then"]["properties"]["authenticode_verified"]["const"] is True
    )


def test_output_manifest_cannot_be_inside_payload(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    payload = _payload(tmp_path)
    with pytest.raises(builder.DesktopReleaseError, match="outside_payload"):
        builder.build_desktop_release(
            payload,
            payload / "release.json",
            source_commit="3" * 40,
            template_path=_template(tmp_path),
        )
