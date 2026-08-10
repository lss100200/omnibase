"""Run the guarded engineering-only P5.2B Task ledger disposable Gate.

The Gate uses an isolated ``omnibase_test_p52b_*`` PostgreSQL database,
executes the destructive preflight before Alembic, verifies migration head
``0011`` and the P5.2B integration suite, proves Docker cleanup, and seals the
tested source bytes. It never reads the root ``.env`` or touches a business
database. Passing this Gate does not activate the production Agent Runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env.example"
COMPOSE_FILE = REPO_ROOT / "docker-compose.destructive-tests.yml"
TEMP_ROOT = (REPO_ROOT / ".tmp" / "p5-2b-task-ledger-gate").resolve()
EVIDENCE_JSON = REPO_ROOT / "docs/evidence/p5-2/phase5-task-ledger-disposable-gate.json"
EVIDENCE_MD = REPO_ROOT / "docs/evidence/p5-2/phase5-task-ledger-disposable-gate.md"
GATE_NAME = "P5.2B Task ledger persistence disposable Gate"
INTEGRATION_TESTS = (
    "tests/integration/test_p5_2b_task_ledger_foundation.py",
    "tests/integration/test_p5_2b_task_ledger_lease_gate.py",
)
INTEGRATION_TEST = INTEGRATION_TESTS[0]  # canonical foundation suite name
_SOURCE_PATHS = (
    "AGENTS.md",
    "backend/alembic.ini",
    "backend/pyproject.toml",
    "backend/uv.lock",
    "backend/src/omnibase/task_ledger/__init__.py",
    "backend/src/omnibase/task_ledger/models.py",
    "backend/src/omnibase/task_ledger/service.py",
    "backend/src/omnibase/workspaces/models.py",
    "backend/src/omnibase/migrations/versions/0011_p5_2b_task_ledger.py",
    "backend/tests/destructive_preflight.py",
    "backend/tests/integration/conftest.py",
    "backend/src/omnibase/agent_alpha/adapters.py",
    "backend/src/omnibase/workspaces/service.py",
    "backend/tests/integration/test_p5_1b_agent_registry_foundation.py",
    "backend/tests/integration/test_p5_2b_task_ledger_foundation.py",
    "backend/tests/integration/test_p5_2b_task_ledger_lease_gate.py",
    "backend/tests/test_p5_2b_task_ledger.py",
    "docker-compose.destructive-tests.yml",
    "scripts/production/run_p5_2b_task_ledger_disposable_gate.py",
)


def _run(
    arguments: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path = REPO_ROOT,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=cwd,
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
    for relative in _SOURCE_PATHS:
        path = REPO_ROOT / relative
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"P5.2B source path is not a regular file: {relative}")
        files[relative] = _sha256(path)
    return {"schema_version": 1, "file_count": len(files), "files": files}


def _manifest_digest(manifest: dict[str, object]) -> str:
    canonical = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(canonical).hexdigest()


def _dirty_paths() -> tuple[str, ...]:
    result = _run(["git", "status", "--porcelain"])
    if result.returncode != 0:
        raise RuntimeError("P5.2B Gate could not inspect Git status")
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
    project: str,
    env: dict[str, str],
    *arguments: str,
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
            raise RuntimeError(f"P5.2B Gate could not inspect {kind}")
        counts[kind] = sum(bool(line.strip()) for line in result.stdout.splitlines())
    return counts


def _cleanup(project: str, env: dict[str, str]) -> dict[str, int]:
    down = _compose(project, env, "down", "-v", "--remove-orphans")
    counts = _resource_counts(project)
    if down.returncode != 0 or any(counts.values()):
        raise RuntimeError(f"P5.2B cleanup failed: {counts}\n{down.stdout[-1000:]}")
    return counts


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


def _run_gate_steps(project: str, database_url: str) -> None:
    commands = (
        ("destructive preflight", ("python", "tests/destructive_preflight.py")),
        ("alembic upgrade", ("python", "-m", "alembic", "upgrade", "head")),
        (
            "P5.2B integration suite",
            (
                "python",
                "-m",
                "pytest",
                "-m",
                "integration",
                *INTEGRATION_TESTS,
                "-q",
            ),
        ),
    )
    for label, arguments in commands:
        result = _run(_container_command(project, database_url, *arguments))
        if result.returncode != 0:
            raise RuntimeError(f"{label} failed:\n{result.stdout[-5000:]}")


def _publish(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise RuntimeError("P5.2B evidence source is not a regular file")
    if destination.parent.is_symlink():
        raise RuntimeError("P5.2B evidence parent is a symlink")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


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
        "passed": passed,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "migration_head": "0011" if passed else None,
        "database_sentinel_verified": passed,
        "root_env_accessed": False,
        "business_database_accessed": False,
        "business_database_migrated": False,
        "production_runtime_activated": False,
        "feature_gates_enabled": False,
        "cleanup": cleanup,
        "source_manifest_sha256": manifest_sha256,
        "integration_tests": [f"backend/{path}" for path in INTEGRATION_TESTS],
    }
    _write(run_dir / "evidence.json", json.dumps(report, indent=2, sort_keys=True) + "\n")
    _write(
        run_dir / "evidence.md",
        "\n".join(
            (
                "# P5.2B Task ledger persistence disposable Gate",
                "",
                f"- Passed: {passed}",
                f"- Migration head: {report['migration_head']}",
                f"- Source manifest SHA-256: {manifest_sha256}",
                f"- Cleanup: {json.dumps(cleanup, sort_keys=True)}",
                "- Production Runtime activated: false",
                "- Phase 5 Feature Gates enabled: false",
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
        raise RuntimeError("P5.2B evidence escaped the repository") from exc
    report = json.loads(resolved.read_text(encoding="utf-8"))
    expected = {
        "gate": GATE_NAME,
        "passed": True,
        "migration_head": "0011",
        "database_sentinel_verified": True,
        "root_env_accessed": False,
        "business_database_accessed": False,
        "business_database_migrated": False,
        "production_runtime_activated": False,
        "feature_gates_enabled": False,
        "cleanup": {"containers": 0, "networks": 0, "volumes": 0},
    }
    for key, value in expected.items():
        if report.get(key) != value:
            raise RuntimeError(f"P5.2B evidence field mismatch: {key}")
    current = _manifest_digest(_manifest())
    if report.get("source_manifest_sha256") != current:
        raise RuntimeError("P5.2B source manifest drifted")
    if _dirty_paths():
        raise RuntimeError("P5.2B evidence verification requires a source-clean checkout")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--validate-only", action="store_true")
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--verify-evidence", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not ENV_FILE.is_file() or not COMPOSE_FILE.is_file():
        raise RuntimeError("P5.2B disposable Gate prerequisites are missing")
    manifest = _manifest()
    if args.validate_only:
        print("P5.2B disposable Gate static contract valid")
        return 0
    if args.verify_evidence is not None:
        _verify_evidence(args.verify_evidence)
        print("P5.2B recorded evidence source seal passed")
        return 0
    dirty = _dirty_paths()
    if dirty:
        raise RuntimeError("P5.2B Gate requires a clean checkout: " + ", ".join(dirty))

    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    project = f"omnibase-p52b-{stamp}"
    database_name = f"omnibase_test_p52b_{stamp.lower()}"
    role_name = f"omnibase_test_p52b_{stamp[-8:]}"
    run_dir = (TEMP_ROOT / stamp).resolve()
    if TEMP_ROOT not in run_dir.parents:
        raise RuntimeError("P5.2B temporary path escaped .tmp")
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest_sha256 = _manifest_digest(manifest)
    _write(run_dir / "source-manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    owner_password = secrets.token_hex(24)
    role_password = secrets.token_hex(24)
    database_url = (
        f"postgresql+psycopg://{role_name}:{role_password}" f"@localhost:55432/{database_name}"
    )
    env = dict(os.environ)
    env.update(
        {
            "TEST_DATABASE_OWNER_PASSWORD": owner_password,
            "TEST_DATABASE_PASSWORD": role_password,
            "TEST_DATABASE_NAME": database_name,
            "TEST_DATABASE_ROLE": role_name,
            "TEST_DATABASE_PORT": "55432",
        }
    )
    started_at = datetime.now(UTC).isoformat()
    cleanup = {"containers": 0, "networks": 0, "volumes": 0}
    cleanup_complete = False
    try:
        up = _compose(project, env, "up", "-d", "--wait", "postgres-test")
        if up.returncode != 0:
            raise RuntimeError("P5.2B PostgreSQL startup failed:\n" + up.stdout[-3000:])
        _run_gate_steps(project, database_url)
        cleanup = _cleanup(project, env)
        cleanup_complete = True
        _record(
            run_dir,
            passed=True,
            manifest_sha256=manifest_sha256,
            started_at=started_at,
            cleanup=cleanup,
        )
        _publish(run_dir / "evidence.json", EVIDENCE_JSON)
        _publish(run_dir / "evidence.md", EVIDENCE_MD)
        print("P5.2B disposable Gate passed")
        return 0
    except Exception as exc:
        if not cleanup_complete:
            with suppress(Exception):
                cleanup = _cleanup(project, env)
                cleanup_complete = True
        with suppress(OSError):
            _record(
                run_dir,
                passed=False,
                manifest_sha256=manifest_sha256,
                started_at=started_at,
                cleanup=cleanup,
            )
        print(f"P5.2B disposable Gate failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if not cleanup_complete:
            _compose(project, env, "down", "-v", "--remove-orphans")


if __name__ == "__main__":
    raise SystemExit(main())
