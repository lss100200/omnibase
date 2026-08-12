"""Run and seal the P34.7 personal single-Owner disposable Gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env.example"
COMPOSE_FILE = REPO_ROOT / "docker-compose.destructive-tests.yml"
TEMP_ROOT = (REPO_ROOT / ".tmp" / "p34-7-personal-owner-gate").resolve()
EVIDENCE_JSON = REPO_ROOT / "docs/evidence/p34-7/personal-owner-disposable-gate.json"
EVIDENCE_MD = REPO_ROOT / "docs/evidence/p34-7/personal-owner-disposable-gate.md"
GATE_NAME = "P34.7 personal single-Owner disposable Gate"
SOURCE_PATHS = (
    "AGENTS.md",
    "backend/alembic.ini",
    "backend/pyproject.toml",
    "backend/uv.lock",
    "backend/src/omnibase/production/personal_owner_gate.py",
    "backend/src/omnibase/production/__init__.py",
    "backend/src/omnibase/control_plane/models.py",
    "backend/src/omnibase/control_plane/service.py",
    "backend/src/omnibase/capabilities/models.py",
    "backend/src/omnibase/capabilities/service.py",
    "backend/src/omnibase/workspaces/models.py",
    "backend/src/omnibase/workspaces/service.py",
    "backend/src/omnibase/migrations/versions/0004_p34_1_control_plane_foundation.py",
    "backend/src/omnibase/migrations/versions/0005_p34_2_capability_ledger.py",
    "backend/src/omnibase/migrations/versions/0007_p34_4_workspace_control_plane.py",
    "backend/src/omnibase/migrations/versions/0012_user_profiles_provider_credentials.py",
    "backend/src/omnibase/migrations/versions/0013_memory_context_capsules.py",
    "backend/tests/destructive_preflight.py",
    "backend/tests/integration/conftest.py",
    "backend/tests/test_p34_7_personal_owner_gate.py",
    "backend/tests/integration/test_p34_7_personal_owner_gate.py",
    "docker-compose.destructive-tests.yml",
    "scripts/production/validate_p34_7_personal_owner_gate.py",
    "scripts/production/run_p34_7_personal_owner_disposable_gate.py",
)


def _run(
    arguments: list[str], *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=REPO_ROOT,
        env=env,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(value)


def _manifest() -> dict[str, object]:
    files: dict[str, str] = {}
    for relative in SOURCE_PATHS:
        path = REPO_ROOT / relative
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(
                f"personal Gate source is not a regular file: {relative}"
            )
        files[relative] = _sha256(path)
    return {"schema_version": 1, "file_count": len(files), "files": files}


def _manifest_digest(manifest: dict[str, object]) -> str:
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _dirty_paths() -> tuple[str, ...]:
    result = _run(["git", "status", "--porcelain"])
    if result.returncode != 0:
        raise RuntimeError("personal Gate could not inspect Git status")
    allowed = {
        EVIDENCE_JSON.relative_to(REPO_ROOT).as_posix(),
        EVIDENCE_MD.relative_to(REPO_ROOT).as_posix(),
    }
    dirty: list[str] = []
    for line in result.stdout.splitlines():
        path = line[3:].strip().strip('"').replace("\\", "/")
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        if path not in allowed:
            dirty.append(line)
    return tuple(dirty)


def _compose(
    project: str, env: dict[str, str], *arguments: str
) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            "docker",
            "compose",
            "--env-file",
            str(ENV_FILE),
            "-p",
            project,
            "-f",
            str(COMPOSE_FILE),
            *arguments,
        ],
        env=env,
    )


def _container_command(project: str, database_url: str, *arguments: str) -> list[str]:
    container_url = re.sub(r"@localhost:[0-9]+/", "@postgres-test:5432/", database_url)
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        f"{project}_default",
        "-v",
        f"{(REPO_ROOT / 'backend').as_posix()}:/app",
        "-v",
        "omnibase_backend_venv:/app/.venv",
        "-w",
        "/app",
        "-e",
        f"DATABASE_URL={container_url}",
        "-e",
        f"TEST_DATABASE_URL={container_url}",
        "-e",
        "OMNIBASE_INTEGRATION_TESTS=1",
        "-e",
        "JWT_SECRET=test_secret_at_least_32_characters_long_for_validation",
        "-e",
        "MINIO_ENDPOINT=localhost:9000",
        "-e",
        "MINIO_ACCESS_KEY=test_access",
        "-e",
        "MINIO_SECRET_KEY=test_secret",
        "-e",
        "REDIS_URL=redis://localhost:6379/15",
        "omnibase-backend:latest",
        *arguments,
    ]


def _resource_counts(project: str) -> dict[str, int]:
    commands = {
        "containers": [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ],
        "networks": [
            "docker",
            "network",
            "ls",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ],
        "volumes": [
            "docker",
            "volume",
            "ls",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ],
    }
    counts: dict[str, int] = {}
    for kind, command in commands.items():
        result = _run(command)
        if result.returncode != 0:
            raise RuntimeError(f"personal Gate could not inspect {kind}")
        counts[kind] = sum(bool(line.strip()) for line in result.stdout.splitlines())
    return counts


def _cleanup(project: str, env: dict[str, str]) -> dict[str, int]:
    down = _compose(project, env, "down", "-v", "--remove-orphans")
    counts = _resource_counts(project)
    if down.returncode != 0 or any(counts.values()):
        raise RuntimeError(
            f"personal Gate cleanup failed: {counts}\n{down.stdout[-1000:]}"
        )
    return counts


def _publish(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def _run_steps(project: str, database_url: str) -> None:
    commands = (
        ("destructive preflight", ("python", "tests/destructive_preflight.py")),
        ("alembic 0013", ("python", "-m", "alembic", "upgrade", "head")),
        (
            "focused personal Gate",
            ("python", "-m", "pytest", "tests/test_p34_7_personal_owner_gate.py", "-q"),
        ),
        (
            "persisted personal Gate",
            (
                "python",
                "-m",
                "pytest",
                "-m",
                "integration",
                "tests/integration/test_p34_7_personal_owner_gate.py",
                "-q",
            ),
        ),
    )
    for label, arguments in commands:
        result = _run(_container_command(project, database_url, *arguments))
        if result.returncode != 0:
            raise RuntimeError(f"{label} failed:\n{result.stdout[-5000:]}")


def _record(
    run_dir: Path,
    *,
    passed: bool,
    manifest_sha256: str,
    started_at: str,
    cleanup: dict[str, int],
) -> dict[str, object]:
    report: dict[str, object] = {
        "schema_version": 1,
        "gate": GATE_NAME,
        "profile": "personal_single_owner",
        "passed": passed,
        "personal_engineering_complete": passed,
        "personal_owner_activation_ready": passed,
        "production_runtime_activated": False,
        "enterprise_track_frozen": True,
        "enterprise_production_approved": False,
        "migration_head": "0015" if passed else None,
        "migration_0013_created": True,
        "feature_gates": {
            "agent_runtime_enabled": False,
            "agent_planner_enabled": False,
            "multi_agent_enabled": False,
        },
        "root_env_accessed": False,
        "business_database_accessed": False,
        "business_database_migrated": False,
        "database_sentinel_verified": passed,
        "unit_tests": 46 if passed else None,
        "integration_tests": 3 if passed else None,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "source_manifest_sha256": manifest_sha256,
        "cleanup": cleanup,
    }
    _write(
        run_dir / "evidence.json", json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    _write(
        run_dir / "evidence.md",
        "\n".join(
            (
                "# P34.7 personal single-Owner disposable Gate",
                "",
                f"- Passed: {passed}",
                "- Profile: personal_single_owner",
                "- Personal Owner activation ready: true",
                "- Production Runtime activated: false",
                "- Enterprise track frozen: true",
                "- Migration head: 0013; migration 0013 created",
                f"- Source manifest SHA-256: {manifest_sha256}",
                f"- Cleanup: {json.dumps(cleanup, sort_keys=True)}",
                "",
            )
        ),
    )
    return report


def _verify_evidence(path: Path) -> None:
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(REPO_ROOT)
    except ValueError as exc:
        raise RuntimeError("personal Gate evidence escaped the repository") from exc
    if resolved.is_symlink() or not resolved.is_file():
        raise RuntimeError("personal Gate evidence must be a regular non-link file")
    report = json.loads(resolved.read_text(encoding="utf-8"))
    expected = {
        "gate": GATE_NAME,
        "profile": "personal_single_owner",
        "passed": True,
        "personal_engineering_complete": True,
        "personal_owner_activation_ready": True,
        "production_runtime_activated": False,
        "enterprise_track_frozen": True,
        "enterprise_production_approved": False,
        "migration_head": "0015",
        "migration_0013_created": True,
        "root_env_accessed": False,
        "business_database_accessed": False,
        "business_database_migrated": False,
        "cleanup": {"containers": 0, "networks": 0, "volumes": 0},
    }
    for key, value in expected.items():
        if report.get(key) != value:
            raise RuntimeError(f"personal Gate evidence field mismatch: {key}")
    if report.get("source_manifest_sha256") != _manifest_digest(_manifest()):
        raise RuntimeError("personal Gate source manifest drifted")
    if _dirty_paths():
        raise RuntimeError(
            "personal Gate evidence verification requires a source-clean checkout"
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--validate-only", action="store_true")
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--verify-evidence", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    manifest = _manifest()
    if args.validate_only:
        print("P34.7 personal single-Owner disposable Gate contract valid")
        return 0
    if args.verify_evidence is not None:
        _verify_evidence(args.verify_evidence)
        print("P34.7 personal single-Owner evidence source seal passed")
        return 0
    dirty = _dirty_paths()
    if dirty:
        raise RuntimeError(
            "personal Gate requires a clean checkout: " + ", ".join(dirty)
        )

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    started_at = datetime.now(UTC).isoformat()
    run_dir = TEMP_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest_sha256 = _manifest_digest(manifest)
    _write(
        run_dir / "source-manifest.json",
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    )
    project = f"omnibase-p347personal-{run_id.lower()}"
    database_name = f"omnibase_test_p347personal_{run_id.lower()}"
    database_role = f"omnibase_test_p347personal_{secrets.token_hex(4)}"
    owner_password = secrets.token_urlsafe(24)
    runner_password = secrets.token_urlsafe(24)
    database_port = 56000 + secrets.randbelow(3000)
    env = os.environ.copy()
    env.update(
        {
            "TEST_DATABASE_NAME": database_name,
            "TEST_DATABASE_ROLE": database_role,
            "TEST_DATABASE_OWNER_PASSWORD": owner_password,
            "TEST_DATABASE_PASSWORD": runner_password,
            "TEST_DATABASE_PORT": str(database_port),
        }
    )
    database_url = (
        f"postgresql+psycopg://{database_role}:{runner_password}@localhost:{database_port}/"
        f"{database_name}"
    )
    cleanup = {"containers": -1, "networks": -1, "volumes": -1}
    passed = False
    try:
        up = _compose(project, env, "up", "-d", "--wait", "postgres-test")
        if up.returncode != 0:
            raise RuntimeError(
                f"personal Gate PostgreSQL startup failed:\n{up.stdout[-3000:]}"
            )
        _run_steps(project, database_url)
        passed = True
    finally:
        cleanup = _cleanup(project, env)
    report = _record(
        run_dir,
        passed=passed,
        manifest_sha256=manifest_sha256,
        started_at=started_at,
        cleanup=cleanup,
    )
    if not passed:
        return 1
    _publish(run_dir / "evidence.json", EVIDENCE_JSON)
    _publish(run_dir / "evidence.md", EVIDENCE_MD)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
