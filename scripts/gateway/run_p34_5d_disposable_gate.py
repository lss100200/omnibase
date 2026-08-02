"""Run and clean the guarded P34.5D disposable mTLS Gateway Gate."""

from __future__ import annotations

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
EVIDENCE_JSON = REPO_ROOT / "docs" / "evidence" / "p34-5" / "gateway-mtls-disposable-gate.json"
EVIDENCE_MD = REPO_ROOT / "docs" / "evidence" / "p34-5" / "gateway-mtls-disposable-gate.md"
TEMP_ROOT = (REPO_ROOT / ".tmp" / "p34-5d-gateway").resolve()
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)authorization\s*:\s*(?:bearer|capability)\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)postgresql(?:\+psycopg)?://[^\s:@/${}]+:[^\s@/${}]{12,}@"),
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


def main() -> int:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    project = f"omnibase-p34-p345d-{stamp}"
    database_name = f"omnibase_test_p345d_{stamp.lower()}"
    role_name = f"omnibase_test_p345d_{stamp[-8:]}"
    run_dir = (TEMP_ROOT / stamp).resolve()
    if TEMP_ROOT not in run_dir.parents:
        raise RuntimeError("temporary Gate path escaped the repository .tmp boundary")
    run_dir.mkdir(parents=True, exist_ok=False)
    image_refs = {
        "GATE_BACKEND_IMAGE": "omnibase-backend:latest",
        "GATE_POSTGRES_IMAGE": "pgvector/pgvector:0.8.5-pg15-bookworm",
        "GATE_CLIENT_IMAGE": "python:3.11-slim-bookworm",
    }
    images = {
        key: _run(["docker", "image", "inspect", reference, "--format", "{{.Id}}"]).stdout.strip()
        for key, reference in image_refs.items()
    }
    if any(re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None for value in images.values()):
        raise RuntimeError("a disposable Gate image does not have an immutable SHA-256 ID")
    env_file = run_dir / "gate.env"
    env_file.write_text(
        "\n".join(
            [
                f"TEST_DATABASE_NAME={database_name}",
                f"TEST_DATABASE_ROLE={role_name}",
                f"TEST_DATABASE_OWNER_PASSWORD={secrets.token_hex(24)}",
                f"TEST_DATABASE_PASSWORD={secrets.token_hex(24)}",
                *(f"{key}={value}" for key, value in images.items()),
                "",
            ]
        ),
        encoding="utf-8",
    )
    compose = [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-p",
        project,
        "-f",
        str(COMPOSE_FILE),
    ]
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
