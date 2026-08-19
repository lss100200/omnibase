from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


def _builder(repo: Path):
    path = repo / "scripts/release/build_p6_5_desktop_payload.py"
    spec = importlib.util.spec_from_file_location("build_p6_5_desktop_payload", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def _fixture(tmp_path: Path) -> dict[str, Path]:
    frontend = tmp_path / "frontend-standalone"
    _write(frontend / "server.js", b"require('./.next/server/app.js')\n")
    _write(frontend / ".next/server/app.js", b"frontend server")
    frontend_static = tmp_path / "frontend-static"
    _write(frontend_static / "chunks/app.js", b"frontend static")
    frontend_public = tmp_path / "frontend-public"
    _write(frontend_public / "brand/omnibase-mark.svg", b"<svg></svg>")

    desktop = tmp_path / "desktop"
    _write(desktop / "dist/main.js", b"compiled desktop main")
    _write(desktop / "dist/preload.js", b"compiled desktop preload")
    _write(desktop / "package.json", b'{"name":"@omnibase/desktop"}\n')
    _write(desktop / "pnpm-lock.yaml", b"lockfileVersion: '9.0'\n")
    _write(desktop / "tsconfig.json", b'{"compilerOptions":{}}\n')
    _write(desktop / "tsconfig.build.json", b'{"extends":"./tsconfig.json"}\n')
    _write(desktop / "src/main.ts", b'import "./runtime/trusted-manifest.ts";\n')
    _write(
        desktop / "src/runtime/trusted-manifest.ts",
        b'export const PINNED_RUNTIME_MANIFEST_SHA256 = "'
        b"__OMNIBASE_RUNTIME_MANIFEST_SHA256__"
        b'";\n',
    )

    host = tmp_path / "runtime-host"
    _write(host / "OmniBase.RuntimeHost.exe", b"runtime host executable")
    _write(host / "OmniBase.RuntimeHost.dll", b"runtime host assembly")
    _write(host / "OmniBase.RuntimeHost.runtimeconfig.json", b"{}\n")

    backend = tmp_path / "backend-publish"
    _write(backend / "OmniBase.Desktop.Backend.exe", b"backend executable")
    _write(backend / "backend-package.bin", b"backend package")

    node = tmp_path / "node.exe"
    _write(node, b"node executable")
    return {
        "frontend_standalone_dir": frontend,
        "frontend_static_dir": frontend_static,
        "frontend_public_dir": frontend_public,
        "desktop_dist_dir": desktop / "dist",
        "runtime_host_publish_dir": host,
        "backend_publish_dir": backend,
        "node_executable": node,
        "desktop_project_dir": desktop,
    }


def _build(builder, fixture: dict[str, Path], output: Path, **overrides):
    values = {**fixture, "output_dir": output, **overrides}
    return builder.build_desktop_payload(**values)


def _file_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_build_generates_exact_runtime_schemas_and_pins_only_staged_source(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    fixture = _fixture(tmp_path)
    trusted_source = fixture["desktop_project_dir"] / "src/runtime/trusted-manifest.ts"
    original = trusted_source.read_bytes()
    output = tmp_path / "payload"

    result = _build(builder, fixture, output)

    manifest_raw = (output / "runtime/runtime-manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    assert set(manifest) == {"schemaVersion", "entrypoint", "files"}
    assert manifest["schemaVersion"] == 1
    assert manifest["entrypoint"] == {
        "path": "OmniBase.RuntimeHost.exe",
        "args": [],
    }
    assert [item["path"] for item in manifest["files"]] == sorted(
        (item["path"] for item in manifest["files"]),
        key=lambda value: (value.casefold(), value),
    )
    assert "runtime-manifest.json" not in {item["path"] for item in manifest["files"]}
    assert "runtime-host.json" in {item["path"] for item in manifest["files"]}
    assert result.runtime_manifest_sha256 == hashlib.sha256(manifest_raw).hexdigest()
    assert result.runtime_file_count == len(manifest["files"])

    host_config = json.loads((output / "runtime/runtime-host.json").read_bytes())
    assert set(host_config) == {
        "schema_version",
        "backend",
        "node",
        "frontend",
        "application_version",
        "backend_port",
        "frontend_port",
        "startup_timeout_seconds",
        "shutdown_timeout_seconds",
        "per_stream_output_limit_bytes",
        "total_output_limit_bytes",
    }
    assert host_config["schema_version"] == 1
    assert host_config["application_version"] == "1.0.0"
    assert host_config["backend_port"] == 8765
    assert host_config["frontend_port"] == 3000
    assert host_config["startup_timeout_seconds"] == 60
    assert host_config["shutdown_timeout_seconds"] == 10
    assert host_config["per_stream_output_limit_bytes"] == 128 * 1024
    assert host_config["total_output_limit_bytes"] == 256 * 1024
    assert host_config["backend"]["path"] == "backend/OmniBase.Desktop.Backend.exe"
    assert host_config["node"]["path"] == "node/node.exe"
    assert host_config["frontend"]["path"] == "frontend/server.js"
    for name in ("backend", "node", "frontend"):
        assert set(host_config[name]) == {"path", "sha256"}
        target = output / "runtime" / Path(host_config[name]["path"])
        assert (
            hashlib.sha256(target.read_bytes()).hexdigest()
            == host_config[name]["sha256"]
        )

    assert trusted_source.read_bytes() == original
    staged_trusted = (
        output / "desktop-build/project/src/runtime/trusted-manifest.ts"
    ).read_text(encoding="utf-8")
    assert builder.TRUST_TOKEN not in staged_trusted
    assert result.runtime_manifest_sha256 in staged_trusted
    assert (
        output / "desktop-build/prebuilt-dist/main.js"
    ).read_bytes() == b"compiled desktop main"
    assert (
        output / "runtime/frontend/.next/static/chunks/app.js"
    ).read_bytes() == b"frontend static"
    assert (
        output / "runtime/frontend/public/brand/omnibase-mark.svg"
    ).read_bytes() == b"<svg></svg>"


def test_build_is_byte_deterministic_for_the_same_inputs(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    fixture = _fixture(tmp_path)
    first = tmp_path / "payload-a"
    second = tmp_path / "payload-b"

    result_a = _build(builder, fixture, first)
    result_b = _build(builder, fixture, second)

    assert result_a.runtime_manifest_sha256 == result_b.runtime_manifest_sha256
    assert _file_bytes(first) == _file_bytes(second)


def test_runtime_manifest_accepts_and_verifies_empty_package_marker(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    fixture = _fixture(tmp_path)
    marker = fixture["frontend_standalone_dir"] / "node_modules/client-only/index.js"
    _write(marker, b"")

    output = tmp_path / "payload"
    _build(builder, fixture, output)

    manifest = json.loads((output / "runtime/runtime-manifest.json").read_bytes())
    entry = next(
        item
        for item in manifest["files"]
        if item["path"] == "frontend/node_modules/client-only/index.js"
    )
    assert entry == {
        "path": "frontend/node_modules/client-only/index.js",
        "size": 0,
        "sha256": hashlib.sha256(b"").hexdigest(),
    }
    assert (
        output / "runtime/frontend/node_modules/client-only/index.js"
    ).read_bytes() == b""


def test_runtime_manifest_is_a_closed_digest_and_size_inventory(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    fixture = _fixture(tmp_path)
    output = tmp_path / "payload"
    _build(builder, fixture, output)

    manifest = json.loads((output / "runtime/runtime-manifest.json").read_bytes())
    declared = {item["path"]: item for item in manifest["files"]}
    actual = {
        path.relative_to(output / "runtime").as_posix(): path
        for path in (output / "runtime").rglob("*")
        if path.is_file() and path.name != "runtime-manifest.json"
    }
    assert set(declared) == set(actual)
    for relative, path in actual.items():
        raw = path.read_bytes()
        assert declared[relative]["size"] == len(raw)
        assert declared[relative]["sha256"] == hashlib.sha256(raw).hexdigest()


def test_existing_output_is_rejected_without_modification(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    fixture = _fixture(tmp_path)
    output = tmp_path / "payload"
    _write(output / "owner-data.txt", b"must survive")

    with pytest.raises(builder.DesktopPayloadError, match="output_exists"):
        _build(builder, fixture, output)

    assert (output / "owner-data.txt").read_bytes() == b"must survive"
    assert list(output.iterdir()) == [output / "owner-data.txt"]


def test_failed_build_does_not_publish_or_modify_the_source(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    fixture = _fixture(tmp_path)
    trusted = fixture["desktop_project_dir"] / "src/runtime/trusted-manifest.ts"
    trusted.write_text(
        'export const PINNED_RUNTIME_MANIFEST_SHA256 = "already-pinned";\n',
        encoding="utf-8",
    )
    before = trusted.read_bytes()
    output = tmp_path / "payload"

    with pytest.raises(
        builder.DesktopPayloadError, match="trusted_manifest_placeholder_invalid"
    ):
        _build(builder, fixture, output)

    assert not output.exists()
    assert trusted.read_bytes() == before
    # No recursive cleanup is performed.  The isolated staging directory is
    # retained so a maintainer can inspect exactly what failed.
    assert any(path.name.startswith(".payload.staging-") for path in tmp_path.iterdir())


def test_final_redigest_rejects_staging_tampering_before_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    fixture = _fixture(tmp_path)
    original_copy_project = builder._copy_desktop_project

    def copy_then_tamper(desktop_project_dir, staging, *, manifest_sha256):
        result = original_copy_project(
            desktop_project_dir, staging, manifest_sha256=manifest_sha256
        )
        (staging / "runtime/backend/OmniBase.Desktop.Backend.exe").write_bytes(
            b"tampered after manifest"
        )
        return result

    monkeypatch.setattr(builder, "_copy_desktop_project", copy_then_tamper)
    output = tmp_path / "payload"
    with pytest.raises(builder.DesktopPayloadError, match="runtime_integrity_invalid"):
        _build(builder, fixture, output)
    assert not output.exists()


@pytest.mark.parametrize(
    ("namespace", "relative"),
    [
        ("frontend_standalone_dir", ".env"),
        ("frontend_static_dir", "secret.key"),
        ("frontend_public_dir", "state.sqlite"),
        ("frontend_standalone_dir", "secrets.env.local"),
        ("backend_publish_dir", "signing-key.pem"),
        ("runtime_host_publish_dir", "state.sqlite3"),
        ("desktop_dist_dir", "disk.vhdx"),
    ],
)
def test_sensitive_input_paths_fail_before_publication(
    tmp_path: Path, namespace: str, relative: str
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    fixture = _fixture(tmp_path)
    _write(fixture[namespace] / relative, b"must never enter payload")
    output = tmp_path / "payload"

    with pytest.raises(builder.DesktopPayloadError, match="sensitive_path_forbidden"):
        _build(builder, fixture, output)

    assert not output.exists()


def test_explicit_sensitive_node_source_is_rejected(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    fixture = _fixture(tmp_path)
    sensitive = tmp_path / ".env"
    sensitive.write_bytes(b"secret")
    fixture["node_executable"] = sensitive

    with pytest.raises(builder.DesktopPayloadError, match="sensitive_path_forbidden"):
        _build(builder, fixture, tmp_path / "payload")


def test_missing_required_entries_fail_closed(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    fixture = _fixture(tmp_path)

    with pytest.raises(builder.DesktopPayloadError, match="backend_entrypoint_missing"):
        _build(
            builder,
            fixture,
            tmp_path / "payload-a",
            backend_executable="missing.exe",
        )
    with pytest.raises(
        builder.DesktopPayloadError, match="frontend_entrypoint_missing"
    ):
        _build(
            builder,
            fixture,
            tmp_path / "payload-b",
            frontend_entry="missing.js",
        )
    with pytest.raises(builder.DesktopPayloadError, match="desktop_entrypoint_missing"):
        _build(
            builder,
            fixture,
            tmp_path / "payload-c",
            desktop_entry="missing.js",
        )


def test_runtime_namespace_collision_is_rejected_case_insensitively(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    fixture = _fixture(tmp_path)
    _write(
        fixture["runtime_host_publish_dir"] / "BACKEND/backend-package.bin",
        b"collision",
    )

    with pytest.raises(builder.DesktopPayloadError, match="duplicate_target"):
        _build(builder, fixture, tmp_path / "payload")


def test_invalid_ports_and_budgets_fail_without_publishing(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    fixture = _fixture(tmp_path)

    with pytest.raises(builder.DesktopPayloadError, match="runtime_port_invalid"):
        _build(
            builder,
            fixture,
            tmp_path / "payload-a",
            backend_port=3000,
            frontend_port=3000,
        )
    with pytest.raises(builder.DesktopPayloadError, match="output_budget_invalid"):
        _build(
            builder,
            fixture,
            tmp_path / "payload-b",
            max_captured_output_bytes=1024,
        )
    assert not (tmp_path / "payload-a").exists()
    assert not (tmp_path / "payload-b").exists()


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"application_version": "1.0.0 private"}, "application_version_invalid"),
        ({"startup_timeout_ms": 1_500}, "startup_timeout_invalid"),
        ({"shutdown_timeout_ms": 31_000}, "shutdown_timeout_invalid"),
    ],
)
def test_runtime_host_contract_values_fail_closed(
    tmp_path: Path, overrides: dict[str, object], code: str
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    fixture = _fixture(tmp_path)

    with pytest.raises(builder.DesktopPayloadError, match=code):
        _build(builder, fixture, tmp_path / "payload", **overrides)

    assert not (tmp_path / "payload").exists()


def test_symlink_or_reparse_source_is_rejected(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    fixture = _fixture(tmp_path)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    link = fixture["frontend_standalone_dir"] / "linked.bin"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")

    with pytest.raises(builder.DesktopPayloadError, match="link_or_reparse_forbidden"):
        _build(builder, fixture, tmp_path / "payload")


def test_runtime_manifest_file_ceiling_is_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    fixture = _fixture(tmp_path)
    # Use a small test-only ceiling so the check does not create thousands of
    # files merely to prove the production 4096-file budget is enforced.
    monkeypatch.setattr(builder, "MAX_RUNTIME_FILES", 8)
    for index in range(builder.MAX_RUNTIME_FILES):
        _write(
            fixture["frontend_standalone_dir"] / f"assets/{index:04d}.js",
            b"x",
        )

    with pytest.raises(builder.DesktopPayloadError, match="file_count_invalid"):
        _build(builder, fixture, tmp_path / "payload")


def test_output_may_not_be_nested_in_any_source(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    fixture = _fixture(tmp_path)
    output = fixture["frontend_standalone_dir"] / "generated-payload"

    with pytest.raises(builder.DesktopPayloadError, match="output_inside_source"):
        _build(builder, fixture, output)

    assert not output.exists()


def test_cli_reports_a_redacted_deterministic_error(tmp_path: Path, capsys) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    fixture = _fixture(tmp_path)
    output = tmp_path / "payload"
    output.mkdir()
    argv = [
        "build_p6_5_desktop_payload.py",
        "--frontend-standalone-dir",
        str(fixture["frontend_standalone_dir"]),
        "--frontend-static-dir",
        str(fixture["frontend_static_dir"]),
        "--frontend-public-dir",
        str(fixture["frontend_public_dir"]),
        "--desktop-dist-dir",
        str(fixture["desktop_dist_dir"]),
        "--runtime-host-publish-dir",
        str(fixture["runtime_host_publish_dir"]),
        "--backend-publish-dir",
        str(fixture["backend_publish_dir"]),
        "--node-executable",
        str(fixture["node_executable"]),
        "--desktop-project-dir",
        str(fixture["desktop_project_dir"]),
        "--output-dir",
        str(output),
    ]
    previous = os.sys.argv
    os.sys.argv = argv
    try:
        assert builder.main() == 2
    finally:
        os.sys.argv = previous
    reported = json.loads(capsys.readouterr().out)
    assert reported == {"error": "desktop_payload_output_exists"}
    assert str(tmp_path) not in json.dumps(reported)
