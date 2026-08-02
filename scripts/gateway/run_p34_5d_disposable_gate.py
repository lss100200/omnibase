"""Run and clean the guarded P34.5D disposable mTLS Gateway Gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "deployment" / "gateway" / "compose.disposable.yml"
GATE_DOCKERFILE = REPO_ROOT / "deployment" / "gateway" / "Dockerfile.gate"
CLIENT_DOCKERFILE = REPO_ROOT / "deployment" / "gateway" / "Dockerfile.client"
EVIDENCE_JSON = REPO_ROOT / "docs" / "evidence" / "p34-5" / "gateway-mtls-disposable-gate.json"
EVIDENCE_MD = REPO_ROOT / "docs" / "evidence" / "p34-5" / "gateway-mtls-disposable-gate.md"
TEMP_ROOT = (REPO_ROOT / ".tmp" / "p34-5d-gateway").resolve()
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)authorization\s*:\s*(?:bearer|capability)\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)postgresql(?:\+psycopg)?://[^\s:@/${}]+:[^\s@/${}]{12,}@"),
)
_SOURCE_MANIFEST_PATHS = (
    "backend/pyproject.toml",
    "backend/uv.lock",
    "backend/alembic.ini",
    "backend/tests/conftest.py",
    "backend/tests/integration/conftest.py",
    "backend/tests/postgres-init-destructive-tests.sh",
    "backend/tests/test_p34_2_gateway_api.py",
    "backend/tests/test_p34_2_gateway_query.py",
    "backend/tests/test_p34_5_gateway_mtls_ingress.py",
    "backend/tests/test_p34_5_gateway_workload.py",
    "backend/tests/integration/test_p34_5_gateway_mtls_disposable.py",
    "backend/tests/integration/test_p34_5_gateway_mtls_split_disposable.py",
    "deployment/gateway/compose.disposable.yml",
    "deployment/gateway/gate.env.example",
    "deployment/gateway/README.md",
    "deployment/gateway/Dockerfile.gate",
    "deployment/gateway/Dockerfile.gate.dockerignore",
    "deployment/gateway/Dockerfile.client",
    "deployment/gateway/Dockerfile.client.dockerignore",
    "scripts/gateway/run_p34_5d_disposable_gate.py",
    "scripts/gateway/p34_5d_broker_client.py",
    "scripts/gateway/test_run_p34_5d_disposable_gate.py",
)
_SOURCE_MANIFEST_GLOBS = (
    "backend/src/**/*",
    "backend/tests/**/*",
)


def _run(arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=REPO_ROOT,
        check=check,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _run_to_utf8_file(arguments: list[str], path: Path) -> subprocess.CompletedProcess[bytes]:
    with path.open("wb") as output:
        return subprocess.run(
            arguments,
            cwd=REPO_ROOT,
            check=False,
            stdout=output,
            stderr=subprocess.STDOUT,
        )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _critical_source_paths() -> list[Path]:
    paths = {REPO_ROOT / relative for relative in _SOURCE_MANIFEST_PATHS}
    symlinks: list[Path] = []
    for pattern in _SOURCE_MANIFEST_GLOBS:
        for path in REPO_ROOT.glob(pattern):
            if path.is_symlink():
                symlinks.append(path)
            elif (
                path.is_file()
                and not {"__pycache__", ".pytest_cache"}.intersection(path.parts)
                and path.suffix not in {".pyc", ".pyo"}
            ):
                paths.add(path)
    symlinks.extend(path for path in paths if path.is_symlink())
    if symlinks:
        names = ", ".join(path.relative_to(REPO_ROOT).as_posix() for path in sorted(set(symlinks)))
        raise RuntimeError(f"P34.5D build input contains a symlink: {names}")
    missing = [path for path in paths if not path.is_file()]
    if missing:
        names = ", ".join(path.relative_to(REPO_ROOT).as_posix() for path in sorted(missing))
        raise RuntimeError(f"P34.5D source manifest input is missing: {names}")
    return sorted(paths)


def _source_manifest() -> dict[str, object]:
    status = _run(
        [
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            ".",
            ":(exclude).env",
        ]
    ).stdout
    dirty_entries = sorted(line for line in status.splitlines() if line.strip())
    dirty_paths = sorted({entry[3:] for entry in dirty_entries if len(entry) > 3})
    if any(path == ".env" or path.startswith(".env/") for path in dirty_paths):
        raise RuntimeError("root .env unexpectedly entered the P34.5D dirty scope")
    files = [
        {
            "path": path.relative_to(REPO_ROOT).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in _critical_source_paths()
    ]
    return {
        "schema_version": 1,
        "gate": "P34.5D clean-checkout source manifest",
        "git_commit": _run(["git", "rev-parse", "HEAD"]).stdout.strip(),
        "git_tree": _run(["git", "rev-parse", "HEAD^{tree}"]).stdout.strip(),
        "dirty": bool(dirty_entries),
        "dirty_scope_definition": "git status --porcelain=v1; filenames/status only; root .env excluded",
        "dirty_scope_sha256": hashlib.sha256(status.encode("utf-8")).hexdigest(),
        "dirty_paths": dirty_paths,
        "symlink_count": 0,
        "files": files,
    }


def _canonical_json_bytes(document: dict[str, object]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _write_source_manifest(path: Path, manifest: dict[str, object]) -> str:
    payload = _canonical_json_bytes(manifest)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _missing_markers(content: str, markers: tuple[str, ...], context: str) -> list[str]:
    return [f"{context} missing {marker}" for marker in markers if marker not in content]


def _present_markers(content: str, markers: tuple[str, ...], context: str) -> list[str]:
    return [
        f"{context} exposes forbidden marker {marker}" for marker in markers if marker in content
    ]


def _validate_static_contract() -> None:
    compose = COMPOSE_FILE.read_text(encoding="utf-8")
    gateway_dockerfile = GATE_DOCKERFILE.read_text(encoding="utf-8")
    client_dockerfile = CLIENT_DOCKERFILE.read_text(encoding="utf-8")
    gateway_service = compose.split("  gateway-server:", 1)[1].split("\n  broker-client:", 1)[0]
    client_service = compose.split("  broker-client:", 1)[1].split("\nnetworks:", 1)[0]
    violations: list[str] = []
    violations.extend(
        _missing_markers(
            compose,
            (
                "GATE_GATEWAY_IMAGE",
                "GATE_CLIENT_IMAGE",
                "GATE_POSTGRES_IMAGE",
                "P34_5D_SPLIT_GATE",
                "internal: true",
            ),
            "compose",
        )
    )
    violations.extend(
        _present_markers(
            compose,
            ("omnibase-backend:latest", "backend-venv", "omnibase_backend_venv"),
            "compose ambient dependency",
        )
    )
    violations.extend(
        _present_markers(
            gateway_service,
            ("../../backend/src", "../../backend/tests", "/app/.venv"),
            "Gateway service source/dependency mount",
        )
    )
    violations.extend(
        _present_markers(
            client_service,
            ("../../", "DATABASE_URL", "REDIS_URL", "MINIO_", "JWT_SECRET", "/app/src"),
            "broker client service",
        )
    )
    violations.extend(
        _missing_markers(
            gateway_dockerfile,
            ("backend/pyproject.toml", "backend/uv.lock", "uv sync --frozen"),
            "Gateway Gate Dockerfile",
        )
    )
    violations.extend(
        _missing_markers(
            gateway_dockerfile,
            ("backend/src", "backend/tests"),
            "Gateway Gate Dockerfile clean-checkout input",
        )
    )
    if "backend/" in client_dockerfile or "COPY ." in client_dockerfile:
        violations.append(
            "minimal client image can include backend or unrestricted checkout content"
        )
    if "scripts/gateway/p34_5d_broker_client.py" not in client_dockerfile:
        violations.append("minimal client image does not copy the sealed client")
    if violations:
        raise RuntimeError("P34.5D static contract validation failed: " + "; ".join(violations))


def _write_env_file(
    path: Path,
    *,
    database_name: str,
    role_name: str,
    images: dict[str, str],
) -> None:
    path.write_text(
        "\n".join(
            [
                f"TEST_DATABASE_NAME={database_name}",
                f"TEST_DATABASE_ROLE={role_name}",
                f"TEST_DATABASE_OWNER_PASSWORD={secrets.token_hex(24)}",
                f"TEST_DATABASE_PASSWORD={secrets.token_hex(24)}",
                *(f"{key}={value}" for key, value in sorted(images.items())),
                "",
            ]
        ),
        encoding="utf-8",
    )


def _compose_command(env_file: Path, project: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-p",
        project,
        "-f",
        str(COMPOSE_FILE),
    ]


def _validate_compose(env_file: Path, project: str) -> None:
    _run([*_compose_command(env_file, project), "config", "--quiet"])


def _immutable_image_reference(reference: str) -> str:
    inspected = _run(
        ["docker", "image", "inspect", reference, "--format", "{{json .RepoDigests}}"],
        check=False,
    )
    if inspected.returncode != 0:
        _run(["docker", "pull", reference])
        inspected = _run(
            ["docker", "image", "inspect", reference, "--format", "{{json .RepoDigests}}"]
        )
    digests = json.loads(inspected.stdout.strip())
    if not isinstance(digests, list) or not digests:
        raise RuntimeError(f"image {reference} has no immutable repository digest")
    candidates = sorted(str(value) for value in digests if "@sha256:" in str(value))
    if not candidates:
        raise RuntimeError(f"image {reference} has no SHA-256 repository digest")
    return candidates[0]


def _image_id(reference: str) -> str:
    image_id = _run(["docker", "image", "inspect", reference, "--format", "{{.Id}}"]).stdout.strip()
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        raise RuntimeError(f"image {reference} does not have an immutable local image ID")
    return image_id


def _build_gate_image(
    *,
    dockerfile: Path,
    tag: str,
    python_base: str,
    source_manifest_sha256: str,
    uv_image: str | None = None,
) -> str:
    command = [
        "docker",
        "build",
        "--file",
        str(dockerfile),
        "--build-arg",
        f"PYTHON_BASE_IMAGE={python_base}",
        "--build-arg",
        f"SOURCE_MANIFEST_SHA256={source_manifest_sha256}",
    ]
    if uv_image is not None:
        command.extend(["--build-arg", f"UV_IMAGE={uv_image}"])
    command.extend(["--tag", tag, str(REPO_ROOT)])
    _run(command)
    image_id = _image_id(tag)
    label = _run(
        [
            "docker",
            "image",
            "inspect",
            tag,
            "--format",
            '{{index .Config.Labels "org.omnibase.source-manifest-sha256"}}',
        ]
    ).stdout.strip()
    if label != source_manifest_sha256:
        raise RuntimeError(f"built image {tag} is not bound to the source manifest")
    return image_id


def _resource_count(kind: str, project: str) -> int:
    command = {
        "containers": [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.ID}}",
        ],
        "networks": [
            "docker",
            "network",
            "ls",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.ID}}",
        ],
        "volumes": [
            "docker",
            "volume",
            "ls",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.Name}}",
        ],
    }[kind]
    output = _run(command).stdout.strip()
    return len([line for line in output.splitlines() if line.strip()])


def _secret_scan() -> list[str]:
    paths = [
        Path(__file__).resolve(),
        REPO_ROOT / "backend" / "src" / "omnibase" / "capability_gateway" / "mtls_ingress.py",
        REPO_ROOT / "backend" / "src" / "omnibase" / "capability_gateway" / "server.py",
        REPO_ROOT / "backend" / "src" / "omnibase" / "capability_gateway" / "thumbprints.py",
        REPO_ROOT / "backend" / "tests" / "test_p34_5_gateway_mtls_ingress.py",
        REPO_ROOT / "backend" / "tests" / "integration" / "test_p34_5_gateway_mtls_disposable.py",
        REPO_ROOT
        / "backend"
        / "tests"
        / "integration"
        / "test_p34_5_gateway_mtls_split_disposable.py",
        REPO_ROOT / "scripts" / "gateway" / "p34_5d_broker_client.py",
        COMPOSE_FILE,
        REPO_ROOT / "deployment" / "gateway" / "gate.env.example",
        REPO_ROOT / "deployment" / "gateway" / "README.md",
        EVIDENCE_JSON,
    ]
    findings: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        for pattern in _SECRET_PATTERNS:
            if pattern.search(content):
                findings.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{pattern.pattern}")
    return findings


def _write_markdown(report: dict[str, object], digest: str) -> None:
    statuses = report.get("read_and_rejection_matrix", report.get("read_actions", {}))
    assert isinstance(statuses, dict)
    lines = [
        "# P34.5D disposable mTLS Gateway Gate",
        "",
        f"- Result: **{'PASS' if report.get('passed') else 'FAIL'}**",
        f"- Database: `{report.get('database_name')}` (guarded `omnibase_test_*`, tmpfs)",
        "- TLS: CA verified, client certificate required, minimum TLS 1.2",
        "- Identity source: client certificate DER from the Uvicorn/asyncio TLS transport",
        "- Browser headers/cookies cannot create trusted peer evidence",
        "- Business database migration: not performed",
        "- Root `.env`: not read",
        f"- Source manifest SHA-256: `{report.get('source_manifest_sha256')}`",
        "- Gateway image: built from `backend/pyproject.toml` + `backend/uv.lock` + checkout source",
        "- Broker client image: contains only the stdlib client; no host/source bind mount",
        "",
        "## Read and rejection matrix",
        "",
    ]
    for name, status in sorted(statuses.items()):
        lines.append(f"- `{name}`: `{status}`")
    lines.extend(
        [
            "",
            "## Containment",
            "",
            f"- Physical locator exposed: `{report.get('physical_locator_exposed')}`",
            f"- Signing private key exposed: `{report.get('private_key_exposed')}`",
            f"- Direct database route present: `{report.get('direct_database_route_present')}`",
            f"- Secret scan findings: `{report.get('secret_scan_findings')}`",
            f"- Cleanup: `{json.dumps(report.get('cleanup'), sort_keys=True)}`",
            "",
            f"JSON SHA-256: `{digest}`",
            "",
        ]
    )
    EVIDENCE_MD.write_text("\n".join(lines), encoding="utf-8")


def _verify_recorded_gate_result(report: dict[str, object]) -> None:
    if report.get("passed") is not True or report.get("root_env_accessed") is not False:
        raise RuntimeError("P34.5D evidence is not a passed root-env-free Gate")
    if report.get("business_database_migrated") is not False:
        raise RuntimeError("P34.5D evidence does not preserve the business database boundary")
    cleanup = report.get("cleanup")
    if not isinstance(cleanup, dict) or cleanup != {
        "containers": 0,
        "networks": 0,
        "temporary_env_removed": True,
        "volumes": 0,
    }:
        raise RuntimeError("P34.5D evidence cleanup is incomplete")
    if report.get("secret_scan_findings") != []:
        raise RuntimeError("P34.5D evidence contains secret-scan findings")


def _verify_recorded_source_manifest(report: dict[str, object]) -> None:
    recorded = report.get("source_manifest")
    if not isinstance(recorded, dict):
        raise RuntimeError("P34.5D evidence has no embedded source manifest")
    if recorded.get("dirty") is not False or recorded.get("dirty_paths") != []:
        raise RuntimeError("P34.5D scored evidence was not produced from a clean checkout")
    if not isinstance(recorded.get("git_commit"), str) or len(recorded["git_commit"]) != 40:
        raise RuntimeError("P34.5D evidence commit identity is invalid")
    if not isinstance(recorded.get("git_tree"), str) or len(recorded["git_tree"]) != 40:
        raise RuntimeError("P34.5D evidence tree identity is invalid")
    expected_manifest_sha256 = hashlib.sha256(_canonical_json_bytes(recorded)).hexdigest()
    if report.get("source_manifest_sha256") != expected_manifest_sha256:
        raise RuntimeError("P34.5D embedded source manifest digest drifted")

    current = _source_manifest()
    stable_fields = ("schema_version", "gate", "symlink_count", "files")
    if any(recorded.get(field) != current.get(field) for field in stable_fields):
        raise RuntimeError("P34.5D sealed Gate source bytes changed after the scored run")


def _verify_recorded_build_containment(report: dict[str, object]) -> None:
    build = report.get("clean_checkout_build")
    if not isinstance(build, dict):
        raise RuntimeError("P34.5D clean-checkout build evidence is missing")
    required_negatives = {
        "ambient_backend_image_used": False,
        "ambient_virtualenv_used": False,
        "broker_client_host_mount_present": False,
    }
    if any(build.get(key) is not value for key, value in required_negatives.items()):
        raise RuntimeError("P34.5D clean-checkout build containment drifted")


def _verify_recorded_evidence(report: dict[str, object]) -> None:
    _verify_recorded_gate_result(report)
    _verify_recorded_source_manifest(report)
    _verify_recorded_build_containment(report)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--validate-only",
        action="store_true",
        help="validate clean-checkout inputs and Compose without building images or touching a database",
    )
    modes.add_argument(
        "--verify-evidence",
        type=Path,
        help="verify a sealed scored report against the current public source bytes",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _parse_args()
    if arguments.verify_evidence is not None:
        _validate_static_contract()
        evidence_path = arguments.verify_evidence.resolve(strict=True)
        try:
            evidence_path.relative_to(REPO_ROOT)
        except ValueError as exc:
            raise RuntimeError("P34.5D evidence path escaped the repository") from exc
        report = json.loads(evidence_path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise RuntimeError("P34.5D evidence root is not an object")
        _verify_recorded_evidence(report)
        print("P34.5D scored evidence source seal passed")
        return 0
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    project = f"omnibase-p34-p345d-{stamp}"
    database_name = f"omnibase_test_p345d_{stamp.lower()}"
    role_name = f"omnibase_test_p345d_{stamp[-8:]}"
    run_dir = (TEMP_ROOT / stamp).resolve()
    if TEMP_ROOT not in run_dir.parents:
        raise RuntimeError("temporary Gate path escaped the repository .tmp boundary")
    run_dir.mkdir(parents=True, exist_ok=False)
    _validate_static_contract()
    source_manifest = _source_manifest()
    source_manifest_path = run_dir / "source-manifest.json"
    source_manifest_sha256 = _write_source_manifest(source_manifest_path, source_manifest)
    env_file = run_dir / "gate.env"
    if arguments.validate_only:
        placeholder = "sha256:" + ("0" * 64)
        _write_env_file(
            env_file,
            database_name=database_name,
            role_name=role_name,
            images={
                "GATE_GATEWAY_IMAGE": placeholder,
                "GATE_POSTGRES_IMAGE": placeholder,
                "GATE_CLIENT_IMAGE": placeholder,
            },
        )
        try:
            _validate_compose(env_file, f"{project}-validate")
        finally:
            env_file.unlink(missing_ok=True)
        validation = {
            "schema_version": 1,
            "gate": "P34.5D clean-checkout validate-only",
            "passed": True,
            "formal_disposable_database_gate_started": False,
            "root_env_accessed": False,
            "source_manifest_path": source_manifest_path.relative_to(REPO_ROOT).as_posix(),
            "source_manifest_sha256": source_manifest_sha256,
            "source_manifest": source_manifest,
        }
        validation_path = run_dir / "validate-only.json"
        validation_path.write_text(
            json.dumps(validation, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(
            "P34.5D validate-only PASS; "
            f"source_manifest_sha256={source_manifest_sha256}; "
            f"report={validation_path}"
        )
        return 0

    python_base = _immutable_image_reference("python:3.11-slim-bookworm")
    uv_image = _immutable_image_reference("ghcr.io/astral-sh/uv:0.4.27")
    postgres_image = _immutable_image_reference("pgvector/pgvector:0.8.5-pg15-bookworm")
    gateway_tag = f"omnibase-p345d-gateway-gate:{source_manifest_sha256[:16]}"
    client_tag = f"omnibase-p345d-client-gate:{source_manifest_sha256[:16]}"
    gateway_image_id = _build_gate_image(
        dockerfile=GATE_DOCKERFILE,
        tag=gateway_tag,
        python_base=python_base,
        uv_image=uv_image,
        source_manifest_sha256=source_manifest_sha256,
    )
    client_image_id = _build_gate_image(
        dockerfile=CLIENT_DOCKERFILE,
        tag=client_tag,
        python_base=python_base,
        source_manifest_sha256=source_manifest_sha256,
    )
    postgres_image_id = _image_id(postgres_image)
    images = {
        "GATE_GATEWAY_IMAGE": gateway_image_id,
        "GATE_POSTGRES_IMAGE": postgres_image_id,
        "GATE_CLIENT_IMAGE": client_image_id,
    }
    _write_env_file(
        env_file,
        database_name=database_name,
        role_name=role_name,
        images=images,
    )
    compose = _compose_command(env_file, project)
    _validate_compose(env_file, project)
    result: subprocess.CompletedProcess[bytes] | None = None
    gate_log = run_dir / "docker-gate.log"
    cleanup = {"containers": -1, "networks": -1, "volumes": -1, "temporary_env_removed": False}
    try:
        result = _run_to_utf8_file(
            [
                *compose,
                "up",
                "--abort-on-container-exit",
                "--exit-code-from",
                "gateway-server",
            ],
            gate_log,
        )
    finally:
        _run([*compose, "down", "-v", "--remove-orphans"], check=False)
        cleanup.update(
            {
                "containers": _resource_count("containers", project),
                "networks": _resource_count("networks", project),
                "volumes": _resource_count("volumes", project),
            }
        )
        env_file.unlink(missing_ok=True)
        cleanup["temporary_env_removed"] = not env_file.exists()

    if result is None:
        raise RuntimeError("disposable Gateway Gate did not start")
    if result.returncode != 0:
        print(f"P34.5D disposable Gate failed; UTF-8 log preserved at {gate_log}")
        return result.returncode
    if not EVIDENCE_JSON.exists():
        raise RuntimeError("Gateway Gate passed without producing evidence")
    report = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8"))
    report["source_manifest"] = source_manifest
    report["source_manifest_sha256"] = source_manifest_sha256
    report["clean_checkout_build"] = {
        "gateway_dockerfile": GATE_DOCKERFILE.relative_to(REPO_ROOT).as_posix(),
        "client_dockerfile": CLIENT_DOCKERFILE.relative_to(REPO_ROOT).as_posix(),
        "python_base": python_base,
        "uv_image": uv_image,
        "postgres_image": postgres_image,
        "gateway_image_id": gateway_image_id,
        "client_image_id": client_image_id,
        "postgres_image_id": postgres_image_id,
        "ambient_backend_image_used": False,
        "ambient_virtualenv_used": False,
        "broker_client_host_mount_present": False,
    }
    findings = _secret_scan()
    report["cleanup"] = cleanup
    report["secret_scan_findings"] = findings
    report["passed"] = (
        bool(report.get("passed"))
        and not findings
        and all(cleanup[key] == 0 for key in ("containers", "networks", "volumes"))
        and cleanup["temporary_env_removed"] is True
    )
    EVIDENCE_JSON.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    digest = hashlib.sha256(EVIDENCE_JSON.read_bytes()).hexdigest()
    _write_markdown(report, digest)
    if not report["passed"]:
        print(json.dumps({"cleanup": cleanup, "secret_scan_findings": findings}, indent=2))
        return 1
    shutil.rmtree(run_dir)
    print(f"P34.5D disposable Gate PASS; evidence_sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
