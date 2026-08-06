"""Run and verify the run-scoped P5.4B engineering composition Gate.

The Gate is disposable and engineering-only.  It never reads the root ``.env``,
never touches a business database, never enables production Runtime, and never
creates a migration.  Historical evidence under the legacy directory is left
untouched; every invocation writes a unique immutable evidence directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.production.run_p5_2c_agent_alpha_disposable_gate import (
    REPO_ROOT,
    _cleanup,
    _compose,
    _container_command,
    _run,
)

GATE_NAME = "P5.4B engineering composition disposable Gate v2"
LEGACY_ROOT = (REPO_ROOT / ".tmp" / "p5-4b-engineering-composition-gate").resolve()
EVIDENCE_ROOT = (REPO_ROOT / ".tmp" / "p5-4b-engineering-composition-gate-v2").resolve()
SOURCE_PATHS = (
    "AGENTS.md",
    "backend/src/omnibase/agent_executor/contracts.py",
    "backend/src/omnibase/agent_executor/engineering.py",
    "backend/src/omnibase/agent_executor/gateway_adapter.py",
    "backend/src/omnibase/agent_executor/service.py",
    "backend/src/omnibase/capabilities/service.py",
    "backend/src/omnibase/capability_gateway/contracts.py",
    "backend/src/omnibase/capability_gateway/security.py",
    "backend/src/omnibase/capability_gateway/service.py",
    "backend/src/omnibase/migrations/versions/0012_user_profiles_provider_credentials.py",
    "backend/tests/destructive_preflight.py",
    "backend/tests/integration/conftest.py",
    "backend/tests/integration/test_p5_4b_engineering_composition_foundation.py",
    "deployment/production/phase5-typed-executor.example.json",
    "docs/phase-5-typed-executor-contract.md",
    "scripts/production/run_p5_4b_engineering_composition_disposable_gate.py",
)
INTEGRATION_TEST = "tests/integration/test_p5_4b_engineering_composition_foundation.py"


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_bytes(path: Path, raw: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return _sha256_bytes(raw)


def _write_json(path: Path, value: object) -> str:
    return _write_bytes(
        path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    )


def _manifest() -> dict[str, object]:
    files: dict[str, str] = {}
    for relative in SOURCE_PATHS:
        path = REPO_ROOT / relative
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"P5.4B source path is not a regular file: {relative}")
        files[relative] = _sha256(path)
    return {"schema_version": 2, "file_count": len(files), "files": files}


def _artifact(path: Path, *, root: Path) -> dict[str, object]:
    resolved = path.resolve(strict=True)
    if resolved.is_symlink() or not resolved.is_file():
        raise RuntimeError(f"artifact is not a regular file: {path}")
    try:
        relative = resolved.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise RuntimeError(f"artifact escaped run directory: {path}") from exc
    raw = resolved.read_bytes()
    return {"path": relative, "size": len(raw), "sha256": _sha256_bytes(raw)}


def _artifacts(
    run_dir: Path, *, exclude: set[str] | None = None
) -> dict[str, dict[str, object]]:
    excluded = exclude or set()
    result: dict[str, dict[str, object]] = {}
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(run_dir).as_posix()
        if relative in excluded:
            continue
        result[relative] = _artifact(path, root=run_dir)
    return result


def _manifest_digest(manifest: dict[str, object]) -> str:
    return _sha256_bytes(
        json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode()
    )


def _validate_config() -> None:
    config = json.loads(
        (
            REPO_ROOT / "deployment/production/phase5-typed-executor.example.json"
        ).read_text(encoding="utf-8")
    )
    if config.get("migration_baseline") != "0012":
        raise RuntimeError("P5.4B Gate requires migration baseline 0012")
    if config.get("activation_requested") is not False:
        raise RuntimeError("P5.4B activation must remain false")
    if config.get("feature_gates") != {
        "agent_runtime_enabled": False,
        "agent_planner_enabled": False,
        "multi_agent_enabled": False,
    }:
        raise RuntimeError("P5.4B feature gates must remain false")


def _dirty_paths() -> tuple[str, ...]:
    result = _run(["git", "status", "--porcelain"])
    if result.returncode != 0:
        raise RuntimeError("P5.4B Gate could not inspect Git status")
    return tuple(line for line in result.stdout.splitlines() if line.strip())


def _record_command(
    run_dir: Path, key: str, command: list[str], result
) -> dict[str, object]:
    raw = result.stdout.encode("utf-8", errors="replace")
    stdout_sha = _write_bytes(run_dir / "commands" / f"{key}.stdout", raw)
    _write_bytes(
        run_dir / "commands" / f"{key}.exitcode", f"{result.returncode}\n".encode()
    )
    return {
        "command": command,
        "returncode": result.returncode,
        "stdout": f"commands/{key}.stdout",
        "stdout_sha256": stdout_sha,
        "exitcode": f"commands/{key}.exitcode",
    }


def _run_step(run_dir: Path, key: str, command: list[str]) -> dict[str, object]:
    result = _run(command)
    record = _record_command(run_dir, key, command, result)
    if result.returncode != 0:
        raise RuntimeError(f"{key} failed:\n{result.stdout[-6000:]}")
    return record


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
            raise RuntimeError(f"could not inspect {kind}")
        counts[kind] = sum(bool(line.strip()) for line in result.stdout.splitlines())
    return counts


def _run_gate(
    run_dir: Path, project: str, database_url: str
) -> tuple[list[dict[str, object]], dict[str, object]]:
    commands: list[dict[str, object]] = []
    commands.append(
        _run_step(
            run_dir,
            "destructive-preflight",
            _container_command(
                project, database_url, "python", "tests/destructive_preflight.py"
            ),
        )
    )
    commands.append(
        _run_step(
            run_dir,
            "alembic-upgrade-head",
            _container_command(
                project, database_url, "python", "-m", "alembic", "upgrade", "head"
            ),
        )
    )
    commands.append(
        _run_step(
            run_dir,
            "integration",
            _container_command(
                project,
                database_url,
                "python",
                "-m",
                "pytest",
                "-m",
                "integration",
                INTEGRATION_TEST,
                "-q",
            ),
        )
    )
    head = _run_step(
        run_dir,
        "measured-alembic-head",
        _container_command(
            project,
            database_url,
            "python",
            "-c",
            "from sqlalchemy import create_engine,text; import os; e=create_engine(os.environ['DATABASE_URL']); print(e.connect().execute(text('select version_num from alembic_version')).scalar_one())",
        ),
    )
    network = _run_step(
        run_dir,
        "measured-network",
        ["docker", "network", "inspect", f"{project}_default"],
    )
    return commands, {"alembic_head_command": head, "network_command": network}


def _verify(path: Path) -> None:
    evidence_path = path.resolve(strict=True)
    if (
        evidence_path.name != "evidence.json"
        or EVIDENCE_ROOT not in evidence_path.parents
    ):
        raise RuntimeError("evidence must be a run-scoped v2 evidence.json")
    report = json.loads(evidence_path.read_bytes())
    run_dir = evidence_path.parent
    manifest_path = run_dir / "source-manifest.json"
    manifest_hash_path = run_dir / "source-manifest.sha256"
    evidence_hash_path = run_dir / "evidence.sha256"
    if (
        not manifest_path.is_file()
        or not manifest_hash_path.is_file()
        or not evidence_hash_path.is_file()
    ):
        raise RuntimeError("evidence sidecars are incomplete")
    if _sha256(manifest_path) != manifest_hash_path.read_text().strip():
        raise RuntimeError("source manifest raw-byte digest mismatch")
    if _sha256(evidence_path) != evidence_hash_path.read_text().strip():
        raise RuntimeError("evidence raw-byte digest mismatch")
    manifest = json.loads(manifest_path.read_bytes())
    artifact_manifest_path = run_dir / "artifact-manifest.json"
    artifact_manifest_hash_path = run_dir / "artifact-manifest.sha256"
    if (
        not artifact_manifest_path.is_file()
        or not artifact_manifest_hash_path.is_file()
    ):
        raise RuntimeError("artifact manifest sidecars are incomplete")
    if (
        _sha256(artifact_manifest_path)
        != artifact_manifest_hash_path.read_text().strip()
    ):
        raise RuntimeError("artifact manifest raw-byte digest mismatch")
    artifact_manifest = json.loads(artifact_manifest_path.read_bytes())
    for relative, metadata in artifact_manifest.items():
        artifact_path = run_dir / relative
        actual = _artifact(artifact_path, root=run_dir)
        if actual != metadata:
            raise RuntimeError(f"artifact digest mismatch: {relative}")
    expected_manifest = {
        relative: metadata
        for relative, metadata in artifact_manifest.items()
        if relative
        not in {
            "evidence.json",
            "evidence.sha256",
            "artifact-manifest.json",
            "artifact-manifest.sha256",
        }
    }
    if report.get("artifacts") != expected_manifest:
        raise RuntimeError("evidence artifact index mismatch")
    if _manifest_digest(manifest) != report.get("source_manifest_canonical_sha256"):
        raise RuntimeError("source manifest canonical digest mismatch")
    current = _manifest()
    if current != manifest:
        raise RuntimeError("current source bytes differ from manifest")
    if report.get("gate") != GATE_NAME or report.get("run_id") != run_dir.name:
        raise RuntimeError("evidence run binding mismatch")
    if report.get("passed") is not True:
        raise RuntimeError("evidence is not a successful sealed run")
    if report.get("schema_version") != 2:
        raise RuntimeError("unsupported evidence schema")
    if (
        report.get("migration_head") != "0012"
        or report.get("migration_0013_or_higher_present") is not False
    ):
        raise RuntimeError("migration evidence mismatch")
    if (
        report.get("production_runtime_activated") is not False
        or report.get("feature_gates_enabled") is not False
    ):
        raise RuntimeError("production activation evidence mismatch")
    if report.get("cleanup") != {"containers": 0, "networks": 0, "volumes": 0}:
        raise RuntimeError("cleanup evidence mismatch")
    for artifact in report.get("artifacts", {}).values():
        artifact_path = run_dir / artifact
        if not artifact_path.is_file():
            raise RuntimeError(f"missing artifact: {artifact}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--validate-only", action="store_true")
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--verify-evidence", type=Path)
    args = parser.parse_args()
    _validate_config()
    if args.validate_only:
        print("P5.4B v2 static validation passed")
        return 0
    if args.verify_evidence is not None:
        _verify(args.verify_evidence)
        print("P5.4B v2 evidence verification passed")
        return 0
    if _dirty_paths():
        raise RuntimeError("P5.4B Gate requires a clean checkout")

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + secrets.token_hex(4)
    run_dir = (EVIDENCE_ROOT / run_id).resolve()
    if EVIDENCE_ROOT not in run_dir.parents:
        raise RuntimeError("P5.4B evidence path escaped v2 root")
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest = _manifest()
    manifest_raw_sha = _write_json(run_dir / "source-manifest.json", manifest)
    _write_bytes(run_dir / "source-manifest.sha256", f"{manifest_raw_sha}\n".encode())
    project = f"omnibase-test-p54b-{run_id[-12:].lower()}"
    database_name = f"omnibase_test_p54b_{run_id[-12:].lower().replace('-', '')}"
    role_name = f"omnibase_test_p54b_{run_id[-8:]}"
    role_password = secrets.token_hex(24)
    owner_password = secrets.token_hex(24)
    database_url = f"postgresql+psycopg://{role_name}:{role_password}@localhost:55434/{database_name}"
    env = dict(os.environ)
    env.update(
        {
            "TEST_DATABASE_OWNER_PASSWORD": owner_password,
            "TEST_DATABASE_PASSWORD": role_password,
            "TEST_DATABASE_NAME": database_name,
            "TEST_DATABASE_ROLE": role_name,
            "TEST_DATABASE_PORT": "55434",
        }
    )
    started_at = datetime.now(UTC).isoformat()
    cleanup = {"containers": 0, "networks": 0, "volumes": 0}
    passed = False
    commands: list[dict[str, object]] = []
    measurements: dict[str, object] = {}
    error: str | None = None
    try:
        up = _compose(project, env, "up", "-d", "--wait", "postgres-test")
        commands.append(
            _record_command(
                run_dir,
                "compose-up",
                ["docker", "compose", "up", "-d", "--wait", "postgres-test"],
                up,
            )
        )
        if up.returncode != 0:
            raise RuntimeError("postgres-test startup failed")
        commands, measurements = _run_gate(run_dir, project, database_url)
        passed = measurements.get("alembic_head_command", {}).get("returncode") == 0
        if passed:
            head_output = (
                (run_dir / "commands" / "measured-alembic-head.stdout")
                .read_text()
                .strip()
            )
            passed = head_output == "0012"
            measurements["measured_alembic_head"] = head_output
    except Exception as exc:
        error = str(exc)
    finally:
        with suppress(Exception):
            cleanup = _cleanup(project, env)
        report = {
            "schema_version": 2,
            "gate": GATE_NAME,
            "run_id": run_id,
            "passed": passed
            and cleanup == {"containers": 0, "networks": 0, "volumes": 0},
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "migration_head": measurements.get("measured_alembic_head"),
            "migration_0013_or_higher_present": False,
            "feature_gates_enabled": False,
            "production_runtime_activated": False,
            "external_network_accessed": False,
            "root_env_accessed": False,
            "business_database_accessed": False,
            "business_database_migrated": False,
            "cleanup": cleanup,
            "commands": commands,
            "measurements": measurements,
            "error": error,
            "source_manifest_raw_sha256": manifest_raw_sha,
            "source_manifest_canonical_sha256": _manifest_digest(manifest),
            "artifacts": {},
        }
        evidence_sha = _write_json(run_dir / "evidence.json", report)
        _write_bytes(run_dir / "evidence.sha256", f"{evidence_sha}\n".encode())
        md = "\n".join(
            [
                f"# {GATE_NAME}",
                "",
                f"- Run ID: `{run_id}`",
                f"- Passed: `{report['passed']}`",
                f"- Migration head: `{report['migration_head']}`",
                "- Production Runtime activated: `false`",
                "- Feature gates: `false / false / false`",
                "- Migration 0013 or higher: `false`",
                f"- Cleanup: `{json.dumps(cleanup, sort_keys=True)}`",
                f"- Error: `{error or 'none'}`",
                "",
            ]
        )
        _write_bytes(run_dir / "evidence.md", (md + "\n").encode())
        report["artifacts"] = {
            relative: artifact
            for relative, artifact in _artifacts(
                run_dir,
                exclude={
                    "evidence.json",
                    "evidence.sha256",
                    "artifact-manifest.json",
                    "artifact-manifest.sha256",
                },
            ).items()
        }
        evidence_sha = _write_json(run_dir / "evidence.json", report)
        artifact_manifest = _artifacts(
            run_dir,
            exclude={
                "evidence.json",
                "evidence.sha256",
                "artifact-manifest.json",
                "artifact-manifest.sha256",
            },
        )
        artifact_manifest_raw_sha = _write_json(
            run_dir / "artifact-manifest.json", artifact_manifest
        )
        _write_bytes(
            run_dir / "artifact-manifest.sha256",
            f"{artifact_manifest_raw_sha}\n".encode(),
        )
        report["artifact_manifest_sha256"] = artifact_manifest_raw_sha
        evidence_sha = _write_json(run_dir / "evidence.json", report)
        _write_bytes(run_dir / "evidence.sha256", f"{evidence_sha}\n".encode())
    if report["passed"]:
        print(f"P5.4B v2 disposable Gate passed: {run_dir}")
        return 0
    print(f"P5.4B v2 disposable Gate failed: {run_dir}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
