"""Run and clean the guarded P5.1C Browser Agent Registry control API disposable Gate.

The Gate provisions a fresh disposable PostgreSQL database (isolated Compose
project, ``omnibase_test_p51c_*`` names, sentinel, restricted non-owner role),
migrates it to head, runs the guarded P5.1C integration suite through the real
Browser router with the DB-backed control plane injected, records fail-closed
evidence, and tears the disposable database down.

``--validate-only`` performs the static checks without provisioning anything.
``--verify-evidence PATH`` re-verifies a recorded evidence file against the
current source tree.  Exit codes are ``0`` for a passing Gate, ``1`` for a
failure or safety veto.

The Gate never reads the root ``.env``, never runs a production migration, and
never touches a business database.
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
COMPOSE_FILE = REPO_ROOT / "docker-compose.destructive-tests.yml"
ENV_FILE = REPO_ROOT / ".env.example"
EVIDENCE_JSON = (
    REPO_ROOT
    / "docs"
    / "evidence"
    / "p5-1"
    / "phase5-browser-registry-api-disposable-gate.json"
)
EVIDENCE_MD = (
    REPO_ROOT
    / "docs"
    / "evidence"
    / "p5-1"
    / "phase5-browser-registry-api-disposable-gate.md"
)
TEMP_ROOT = (REPO_ROOT / ".tmp" / "p5-1c-registry-gate").resolve()
INTEGRATION_TEST = (
    "backend/tests/integration/test_p5_1c_browser_registry_api_foundation.py"
)

_SOURCE_MANIFEST_PATHS = (
    "AGENTS.md",
    "backend/pyproject.toml",
    "backend/uv.lock",
    "backend/alembic.ini",
    "backend/tests/conftest.py",
    "backend/tests/integration/conftest.py",
    "backend/tests/cleanup.py",
    "backend/tests/postgres-init-destructive-tests.sh",
    "backend/tests/test_p5_1c_registry_api.py",
    "backend/tests/integration/test_p5_1c_browser_registry_api_foundation.py",
    "backend/src/omnibase/agent_registry/__init__.py",
    "backend/src/omnibase/agent_registry/models.py",
    "backend/src/omnibase/agent_registry/service.py",
    "backend/src/omnibase/agent_registry/control.py",
    "backend/src/omnibase/agent_registry/router.py",
    "backend/src/omnibase/agent_registry/schemas.py",
    "backend/src/omnibase/main.py",
    "backend/src/omnibase/migrations/versions/0010_p5_1b_agent_registry.py",
    "backend/src/omnibase/control_plane/service.py",
    "backend/src/omnibase/production/phase5_registry_contract.py",
    "sdk/python/src/omnibase_sdk/browser_registry.py",
    "sdk/typescript/src/registry-browser.ts",
    "docker-compose.destructive-tests.yml",
    "docs/evidence/p5-1/phase5-browser-registry-api-design.md",
    "scripts/production/run_p5_1c_browser_registry_disposable_gate.py",
)
_SOURCE_MANIFEST_GLOBS = ("backend/src/**/*", "backend/tests/**/*", "sdk/**/*")
_SECRET_PATTERNS = (
    re.compile(r"(?i)postgresql(?:\+psycopg)?://[^\s:@/${}]+:[^\s@/${}]{12,}@"),
    re.compile(
        r"(?i)authorization\s*:\s*(?:bearer|capability)\s+[A-Za-z0-9._~+/=-]{16,}"
    ),
)


def _run(
    arguments: list[str],
    *,
    check: bool = True,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=cwd or REPO_ROOT,
        check=check,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_text_lf(path: Path, content: str) -> None:
    """Write repository artifacts as UTF-8 with deterministic LF endings."""

    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def _git_clean() -> tuple[bool, tuple[str, ...]]:
    result = _run(["git", "status", "--porcelain"], check=False)
    dirty = tuple(line for line in result.stdout.splitlines() if line.strip())
    return not dirty, dirty


def _tracked_glob_paths() -> tuple[str, ...]:
    """Resolve manifest globs through Git so ignored caches never enter a seal."""

    result = _run(
        [
            "git",
            "ls-files",
            "--",
            *(f":(glob){pattern}" for pattern in _SOURCE_MANIFEST_GLOBS),
        ],
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("failed to enumerate tracked P5.1C source files")
    return tuple(
        sorted({line.strip() for line in result.stdout.splitlines() if line.strip()})
    )


def _dirty_path(status_line: str) -> str:
    """Extract one Git porcelain path for exact evidence allowlisting."""
    path = status_line[3:] if len(status_line) >= 4 else status_line
    if " -> " in path:
        path = path.rsplit(" -> ", 1)[1]
    return path.strip().strip('"').replace("\\", "/")


def _source_manifest(
    *, allowed_dirty_paths: frozenset[str] = frozenset()
) -> dict[str, object]:
    clean, dirty = _git_clean()
    if allowed_dirty_paths:
        dirty = tuple(
            line for line in dirty if _dirty_path(line) not in allowed_dirty_paths
        )
        clean = not dirty
    files: dict[str, object] = {}
    for relative in _SOURCE_MANIFEST_PATHS:
        path = REPO_ROOT / relative
        if not path.is_file():
            raise RuntimeError(f"source manifest path missing: {relative}")
        files[relative] = _sha256_file(path)
    for relative in _tracked_glob_paths():
        path = REPO_ROOT / relative
        if not path.is_file():
            raise RuntimeError(f"tracked source manifest path missing: {relative}")
        if relative not in files:
            files[relative] = _sha256_file(path)
    return {
        "schema_version": 1,
        "repository_clean": clean,
        "dirty_paths": dirty,
        "file_count": len(files),
        "files": files,
    }


def _manifest_sha256(manifest: dict[str, object]) -> str:
    canonical = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    return hashlib.sha256(canonical).hexdigest()


def _validate_static_contract() -> None:
    for relative in _SOURCE_MANIFEST_PATHS:
        if not (REPO_ROOT / relative).is_file():
            raise RuntimeError(f"P5.1C Gate source path missing: {relative}")
    if not COMPOSE_FILE.is_file():
        raise RuntimeError("destructive-test Compose file is missing")
    if not ENV_FILE.is_file():
        raise RuntimeError("explicit Compose env file is missing")


def _write_source_manifest(path: Path, manifest: dict[str, object]) -> str:
    _write_text_lf(path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return _manifest_sha256(manifest)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend-executor",
        choices=("host", "container"),
        default="host",
        help=(
            "run alembic/pytest with the ambient Python (host) or inside the "
            "omnibase-backend image (container; requires Docker and the "
            "omnibase_backend_venv volume)"
        ),
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--validate-only", action="store_true")
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--verify-evidence", type=Path)
    return parser.parse_args()


def _compose(
    project: str, *arguments: str, env: dict[str, str]
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
        check=False,
        env=env,
    )


def _project_resource_counts(project: str) -> dict[str, int]:
    """Return Docker resources still owned by one disposable Compose project."""

    counts: dict[str, int] = {}
    for kind, arguments in (
        (
            "containers",
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ],
        ),
        (
            "networks",
            [
                "docker",
                "network",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ],
        ),
        (
            "volumes",
            [
                "docker",
                "volume",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ],
        ),
    ):
        result = _run(arguments, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"failed to inspect disposable {kind}: {result.stdout[-1000:]}"
            )
        counts[kind] = sum(1 for line in result.stdout.splitlines() if line.strip())
    return counts


def _cleanup_project(project: str, *, env: dict[str, str]) -> dict[str, int]:
    """Tear down the disposable project and prove no labeled resources remain."""

    down = _compose(project, "down", "-v", "--remove-orphans", env=env)
    counts = _project_resource_counts(project)
    if down.returncode != 0:
        raise RuntimeError("P5.1C Gate cleanup failed:\n" + down.stdout[-1000:])
    if any(counts.values()):
        raise RuntimeError(f"P5.1C Gate cleanup left resources: {counts}")
    return counts


def _pytest_command(*, container: bool = False) -> list[str]:
    """Return the canonical backend test command for the disposable database."""
    target = INTEGRATION_TEST
    if container:
        target = INTEGRATION_TEST.removeprefix("backend/")
    return [
        "-m",
        "pytest",
        "-m",
        "integration",
        target,
        "-q",
    ]


def _container_backend_command(
    project: str,
    database_url: str,
    *arguments: str,
) -> list[str]:
    """Wrap one backend command inside the omnibase-backend image.

    The container resolves the disposable database through the Compose network
    service name instead of ``localhost``.
    """
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


def _run_backend_steps(
    database_url: str,
    *,
    project: str,
    backend_executor: str,
    env: dict[str, str],
) -> None:
    """Run Alembic head and the guarded P5.1C integration suite."""
    if backend_executor == "container":
        alembic = _run(
            _container_backend_command(
                project, database_url, "python", "-m", "alembic", "upgrade", "head"
            ),
            check=False,
        )
        if alembic.returncode != 0:
            raise RuntimeError(
                "alembic upgrade head failed:\n" + alembic.stdout[-2000:]
            )
        pytest = _run(
            _container_backend_command(
                project, database_url, "python", *_pytest_command(container=True)
            ),
            check=False,
        )
        if pytest.returncode != 0:
            raise RuntimeError(
                "P5.1C integration suite failed:\n" + pytest.stdout[-4000:]
            )
        return
    alembic = _run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=False,
        env=_backend_env(env, database_url=database_url),
        cwd=REPO_ROOT / "backend",
    )
    if alembic.returncode != 0:
        raise RuntimeError("alembic upgrade head failed:\n" + alembic.stdout[-2000:])
    pytest = _run(
        [sys.executable, *_pytest_command(container=True)],
        check=False,
        env=_backend_env(env, database_url=database_url),
        cwd=REPO_ROOT / "backend",
    )
    if pytest.returncode != 0:
        raise RuntimeError("P5.1C integration suite failed:\n" + pytest.stdout[-4000:])


def _backend_env(env: dict[str, str], *, database_url: str) -> dict[str, str]:
    """Host-executor environment with the backend sources on the import path."""
    backend_env = dict(env)
    backend_env["PYTHONPATH"] = str(REPO_ROOT / "backend" / "src")
    backend_env["DATABASE_URL"] = database_url
    backend_env["TEST_DATABASE_URL"] = database_url
    backend_env["OMNIBASE_INTEGRATION_TESTS"] = "1"
    # Same synthetic service endpoints the container executor passes via -e.
    backend_env.setdefault(
        "JWT_SECRET", "test_secret_at_least_32_characters_long_for_validation"
    )
    backend_env.setdefault("MINIO_ENDPOINT", "localhost:9000")
    backend_env.setdefault("MINIO_ACCESS_KEY", "test_access")
    backend_env.setdefault("MINIO_SECRET_KEY", "test_secret")
    backend_env.setdefault("REDIS_URL", "redis://localhost:6379/15")
    return backend_env


def _run_database_preflight(
    database_url: str,
    *,
    project: str,
    backend_executor: str,
    env: dict[str, str],
) -> None:
    """Prove the database name, sentinel, and restricted role before DDL."""
    if backend_executor == "container":
        command = _container_backend_command(
            project,
            database_url,
            "python",
            "tests/destructive_preflight.py",
        )
        result = _run(command, check=False)
    else:
        result = _run(
            [sys.executable, "tests/destructive_preflight.py"],
            check=False,
            env=_backend_env(env, database_url=database_url),
            cwd=REPO_ROOT / "backend",
        )
    if result.returncode != 0:
        raise RuntimeError(
            "disposable database preflight failed:\n" + result.stdout[-2000:]
        )


def _record_evidence(
    evidence: dict[str, object],
    *,
    passed: bool,
    manifest_sha256: str,
    run_dir: Path,
) -> tuple[Path, Path]:
    evidence["passed"] = passed
    evidence["manifest_sha256"] = manifest_sha256
    json_path = run_dir / "evidence.json"
    _write_text_lf(json_path, json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    md_lines = [
        "# P5.1C Browser Agent Registry control API disposable Gate",
        "",
        f"- Gate: {evidence['gate']}",
        f"- Passed: {passed}",
        f"- Manifest SHA-256: {manifest_sha256}",
        f"- Started: {evidence['started_at']}",
        f"- Finished: {evidence['finished_at']}",
        f"- Root env accessed: {evidence['root_env_accessed']}",
        f"- Business database accessed: {evidence['business_database_accessed']}",
        f"- Business database migrated: {evidence['business_database_migrated']}",
        f"- Cleanup: {json.dumps(evidence['cleanup'], sort_keys=True)}",
        "",
        "The machine-readable report is `phase5-browser-registry-api-disposable-gate.json`.",
        "",
    ]
    md_path = run_dir / "evidence.md"
    _write_text_lf(md_path, "\n".join(md_lines))
    return json_path, md_path


def _publish_evidence(run_dir: Path) -> None:
    EVIDENCE_JSON.parent.mkdir(parents=True, exist_ok=True)
    for name, source in (
        ("json", run_dir / "evidence.json"),
        ("md", run_dir / "evidence.md"),
    ):
        destination = EVIDENCE_JSON if name == "json" else EVIDENCE_MD
        if source.is_symlink() or not source.is_file():
            raise RuntimeError(f"evidence source is not a regular file: {source}")
        if destination.is_symlink() or (
            destination.exists() and not destination.is_file()
        ):
            raise RuntimeError(
                f"evidence destination is not a regular file: {destination}"
            )
        if destination.parent.is_symlink():
            raise RuntimeError(
                f"evidence destination parent is a symlink: {destination.parent}"
            )
        temporary = destination.with_name(
            f".{destination.name}.{secrets.token_hex(8)}.tmp"
        )
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
        finally:
            with suppress(FileNotFoundError):
                temporary.unlink()


def _verify_recorded_evidence(report: dict[str, object]) -> None:
    if report.get("schema_version") != 1:
        raise RuntimeError("P5.1C evidence schema version mismatch")
    if report.get("gate") != "P5.1C Browser Agent Registry control API disposable Gate":
        raise RuntimeError("P5.1C evidence gate identity mismatch")
    if report.get("passed") is not True:
        raise RuntimeError("P5.1C recorded Gate did not pass")
    if report.get("root_env_accessed") is not False:
        raise RuntimeError("P5.1C evidence claims root .env access")
    if report.get("business_database_accessed") is not False:
        raise RuntimeError("P5.1C evidence claims business database access")
    if report.get("business_database_migrated") is not False:
        raise RuntimeError("P5.1C evidence claims a business database migration")
    if report.get("database_sentinel_verified") is not True:
        raise RuntimeError("P5.1C disposable database sentinel was not verified")
    if report.get("physical_locator_exposed") is not False:
        raise RuntimeError("P5.1C evidence claims physical locator exposure")
    if report.get("cleanup") != {"containers": 0, "networks": 0, "volumes": 0}:
        raise RuntimeError("P5.1C evidence does not prove complete cleanup")
    manifest = _source_manifest(
        allowed_dirty_paths=frozenset(
            {
                EVIDENCE_JSON.relative_to(REPO_ROOT).as_posix(),
                EVIDENCE_MD.relative_to(REPO_ROOT).as_posix(),
            }
        )
    )
    recorded = report.get("manifest_sha256")
    if not isinstance(recorded, str) or recorded != _manifest_sha256(manifest):
        raise RuntimeError("P5.1C source manifest drifted since the recorded Gate")


def main() -> int:
    arguments = _parse_args()
    if arguments.verify_evidence is not None:
        _validate_static_contract()
        evidence_path = arguments.verify_evidence.resolve(strict=True)
        try:
            evidence_path.relative_to(REPO_ROOT)
        except ValueError as exc:
            raise RuntimeError("P5.1C evidence path escaped the repository") from exc
        report = json.loads(evidence_path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise RuntimeError("P5.1C evidence root is not an object")
        _verify_recorded_evidence(report)
        print("P5.1C recorded evidence source seal passed")
        return 0

    _validate_static_contract()
    if arguments.validate_only:
        print("P5.1C Gate static contract valid")
        return 0

    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    project = f"omnibase-p51c-{stamp}"
    database_name = f"omnibase_test_p51c_{stamp.lower()}"
    role_name = f"omnibase_test_p51c_{stamp[-8:]}"
    run_dir = (TEMP_ROOT / stamp).resolve()
    if TEMP_ROOT not in run_dir.parents:
        raise RuntimeError("temporary Gate path escaped the repository .tmp boundary")
    run_dir.mkdir(parents=True, exist_ok=False)

    source_manifest = _source_manifest()
    if not source_manifest["repository_clean"]:
        raise RuntimeError(
            "P5.1C Gate requires a clean checkout: "
            + ", ".join(source_manifest["dirty_paths"])
        )
    manifest_sha256 = _write_source_manifest(
        run_dir / "source-manifest.json", source_manifest
    )

    owner_password = secrets.token_hex(24)
    test_password = secrets.token_hex(24)
    database_url = f"postgresql+psycopg://{role_name}:{test_password}@localhost:55432/{database_name}"
    env = dict(os.environ)
    env.update(
        {
            "TEST_DATABASE_OWNER_PASSWORD": owner_password,
            "TEST_DATABASE_PASSWORD": test_password,
            "TEST_DATABASE_NAME": database_name,
            "TEST_DATABASE_ROLE": role_name,
            "TEST_DATABASE_PORT": "55432",
        }
    )

    started_at = datetime.now(UTC).isoformat()
    evidence: dict[str, object] = {
        "schema_version": 1,
        "gate": "P5.1C Browser Agent Registry control API disposable Gate",
        "started_at": started_at,
        "finished_at": None,
        "passed": False,
        "database_name": database_name,
        "database_sentinel_verified": False,
        "root_env_accessed": False,
        "business_database_accessed": False,
        "business_database_migrated": False,
        "physical_locator_exposed": False,
        "cleanup": {"containers": 0, "networks": 0, "volumes": 0},
        "integration_tests": [INTEGRATION_TEST],
    }

    cleanup_verified = False
    try:
        up = _compose(
            project,
            "up",
            "-d",
            "--wait",
            "postgres-test",
            env=env,
        )
        if up.returncode != 0:
            raise RuntimeError(
                "disposable PostgreSQL failed to start:\n" + up.stdout[-2000:]
            )
        _run_database_preflight(
            database_url,
            project=project,
            backend_executor=arguments.backend_executor,
            env=env,
        )
        evidence["database_sentinel_verified"] = True
        _run_backend_steps(
            database_url,
            project=project,
            backend_executor=arguments.backend_executor,
            env=env,
        )
        evidence["cleanup"] = _cleanup_project(project, env=env)
        cleanup_verified = True
        evidence["finished_at"] = datetime.now(UTC).isoformat()
        _record_evidence(
            evidence,
            passed=True,
            manifest_sha256=manifest_sha256,
            run_dir=run_dir,
        )
        _publish_evidence(run_dir)
        print("P5.1C disposable Gate passed")
        return 0
    except (RuntimeError, json.JSONDecodeError, OSError) as exc:
        if not cleanup_verified:
            with suppress(RuntimeError):
                evidence["cleanup"] = _cleanup_project(project, env=env)
                cleanup_verified = True
        evidence["finished_at"] = datetime.now(UTC).isoformat()
        with suppress(OSError):
            _record_evidence(
                evidence,
                passed=False,
                manifest_sha256=manifest_sha256,
                run_dir=run_dir,
            )
        print(f"P5.1C disposable Gate failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if not cleanup_verified:
            down = _compose(project, "down", "-v", "--remove-orphans", env=env)
            if down.returncode != 0:
                print(
                    "P5.1C Gate cleanup warning:\n" + down.stdout[-1000:],
                    file=sys.stderr,
                )


if __name__ == "__main__":
    raise SystemExit(main())
