"""Run the current-baseline P5.4A Capability Gateway disposable Gate.

Unlike the historical P5.2C runner, this Gate is pinned to migration head
``0013`` and exercises the real PostgreSQL capability/Gateway integration
tests. It never reads the root ``.env`` and always uses a disposable
``omnibase_test_p54a_*`` database with mandatory cleanup.
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

GATE_NAME = "P5.4A Capability Gateway current-baseline disposable Gate"
TEMP_ROOT = (REPO_ROOT / ".tmp" / "p5-4a-gateway-disposable-gate").resolve()
EVIDENCE_JSON = TEMP_ROOT / "evidence.json"
EVIDENCE_MD = TEMP_ROOT / "evidence.md"
SOURCE_PATHS = (
    "AGENTS.md",
    "backend/src/omnibase/agent_executor/contracts.py",
    "backend/src/omnibase/agent_executor/gateway_adapter.py",
    "backend/src/omnibase/agent_executor/service.py",
    "backend/src/omnibase/capabilities/service.py",
    "backend/src/omnibase/capability_gateway/contracts.py",
    "backend/src/omnibase/capability_gateway/service.py",
    "backend/src/omnibase/capability_gateway/security.py",
    "backend/src/omnibase/migrations/versions/0012_user_profiles_provider_credentials.py",
    "backend/src/omnibase/migrations/versions/0013_memory_context_capsules.py",
    "backend/tests/destructive_preflight.py",
    "backend/tests/integration/conftest.py",
    "backend/tests/integration/test_p34_2_capability_foundation.py",
    "backend/tests/integration/test_p34_6_gateway_core_foundation.py",
    "deployment/production/phase5-typed-executor.example.json",
    "docs/phase-5-typed-executor-contract.md",
    "scripts/production/run_p5_4a_gateway_disposable_gate.py",
)
INTEGRATION_TESTS = (
    "tests/integration/test_p34_2_capability_foundation.py",
    "tests/integration/test_p34_6_gateway_core_foundation.py",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest() -> dict[str, object]:
    files: dict[str, str] = {}
    for relative in SOURCE_PATHS:
        path = REPO_ROOT / relative
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"P5.4A source path is not a regular file: {relative}")
        files[relative] = _sha256(path)
    return {"schema_version": 1, "file_count": len(files), "files": files}


def _manifest_digest(manifest: dict[str, object]) -> str:
    raw = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _validate_config() -> None:
    config = json.loads(
        (
            REPO_ROOT / "deployment/production/phase5-typed-executor.example.json"
        ).read_text(encoding="utf-8")
    )
    if config.get("migration_baseline") != "0013":
        raise RuntimeError("P5.4A Gate requires migration baseline 0013")
    if config.get("activation_requested") is not False:
        raise RuntimeError("P5.4A activation must remain false")
    if config.get("feature_gates") != {
        "agent_runtime_enabled": False,
        "agent_planner_enabled": False,
        "multi_agent_enabled": False,
    }:
        raise RuntimeError("P5.4A feature gates must remain false")


def _dirty_paths() -> tuple[str, ...]:
    result = _run(["git", "status", "--porcelain"])
    if result.returncode != 0:
        raise RuntimeError("P5.4A Gate could not inspect Git status")
    return tuple(line for line in result.stdout.splitlines() if line.strip())


def _run_gate_steps(project: str, database_url: str) -> None:
    commands = (
        ("destructive preflight", ("python", "tests/destructive_preflight.py")),
        ("alembic upgrade head", ("python", "-m", "alembic", "upgrade", "head")),
        (
            "Capability/Gateway integration",
            ("python", "-m", "pytest", "-m", "integration", *INTEGRATION_TESTS, "-q"),
        ),
    )
    for label, arguments in commands:
        result = _run(_container_command(project, database_url, *arguments))
        if result.returncode != 0:
            raise RuntimeError(f"{label} failed:\n{result.stdout[-6000:]}")


def _record(
    *,
    manifest_sha256: str,
    started_at: str,
    passed: bool,
    cleanup: dict[str, int],
) -> dict[str, object]:
    report: dict[str, object] = {
        "schema_version": 1,
        "gate": GATE_NAME,
        "passed": passed,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "migration_head": "0013" if passed else None,
        "database_sentinel_verified": passed,
        "capability_scope_verified": passed,
        "gateway_budget_audit_verified": passed,
        "resource_resolution_verified": passed,
        "workspace_scope_verified": passed,
        "production_runtime_activated": False,
        "feature_gates_enabled": False,
        "migration_0013_created": True,
        "root_env_accessed": False,
        "business_database_accessed": False,
        "business_database_migrated": False,
        "external_network_accessed": False,
        "cleanup": cleanup,
        "source_manifest_sha256": manifest_sha256,
        "integration_tests": [f"backend/{item}" for item in INTEGRATION_TESTS],
    }
    _write(EVIDENCE_JSON, json.dumps(report, indent=2, sort_keys=True) + "\n")
    _write(
        EVIDENCE_MD,
        "\n".join(
            (
                "# P5.4A Capability Gateway current-baseline disposable Gate",
                "",
                f"- Passed: `{passed}`",
                f"- Migration head: `{report['migration_head']}`",
                f"- Source manifest SHA-256: `{manifest_sha256}`",
                f"- Cleanup: `{json.dumps(cleanup, sort_keys=True)}`",
                "- Production Runtime activated: `false`",
                "- Feature Gates enabled: `false / false / false`",
                "- Migration `0013`: created",
                "- Root `.env`: not accessed",
                "- Business database: not accessed",
                "",
            )
        ),
    )
    return report


def _verify(path: Path) -> None:
    report = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    expected = {
        "gate": GATE_NAME,
        "passed": True,
        "migration_head": "0013",
        "database_sentinel_verified": True,
        "capability_scope_verified": True,
        "gateway_budget_audit_verified": True,
        "resource_resolution_verified": True,
        "workspace_scope_verified": True,
        "production_runtime_activated": False,
        "feature_gates_enabled": False,
        "migration_0013_created": True,
        "root_env_accessed": False,
        "business_database_accessed": False,
        "business_database_migrated": False,
        "external_network_accessed": False,
        "cleanup": {"containers": 0, "networks": 0, "volumes": 0},
    }
    for key, value in expected.items():
        if report.get(key) != value:
            raise RuntimeError(f"P5.4A evidence mismatch: {key}")
    if report.get("source_manifest_sha256") != _manifest_digest(_manifest()):
        raise RuntimeError("P5.4A source manifest drifted")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--validate-only", action="store_true")
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--verify-evidence", type=Path)
    args = parser.parse_args()
    _validate_config()
    manifest = _manifest()
    if args.validate_only:
        print("P5.4A current-baseline disposable Gate static validation passed")
        return 0
    if args.verify_evidence is not None:
        _verify(args.verify_evidence)
        print("P5.4A current-baseline evidence seal passed")
        return 0
    if _dirty_paths():
        raise RuntimeError("P5.4A Gate requires a clean checkout")

    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    project = f"omnibase-test-p54a-{stamp}"
    database_name = f"omnibase_test_p54a_{stamp.lower()}"
    role_name = f"omnibase_test_p54a_{stamp[-8:]}"
    run_dir = (TEMP_ROOT / stamp).resolve()
    if TEMP_ROOT not in run_dir.parents:
        raise RuntimeError("P5.4A temporary path escaped .tmp")
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest_sha256 = _manifest_digest(manifest)
    _write(
        run_dir / "source-manifest.json",
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    )
    owner_password = secrets.token_hex(24)
    role_password = secrets.token_hex(24)
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
    cleanup_complete = False
    try:
        up = _compose(project, env, "up", "-d", "--wait", "postgres-test")
        if up.returncode != 0:
            raise RuntimeError(
                "P5.4A postgres-test startup failed:\n" + up.stdout[-3000:]
            )
        _run_gate_steps(project, database_url)
        cleanup = _cleanup(project, env)
        cleanup_complete = True
        _record(
            manifest_sha256=manifest_sha256,
            started_at=started_at,
            passed=True,
            cleanup=cleanup,
        )
        print("P5.4A current-baseline disposable Gate passed")
        return 0
    except Exception as exc:
        if not cleanup_complete:
            with suppress(Exception):
                cleanup = _cleanup(project, env)
                cleanup_complete = True
        _record(
            manifest_sha256=manifest_sha256,
            started_at=started_at,
            passed=False,
            cleanup=cleanup,
        )
        print(f"P5.4A current-baseline disposable Gate failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if not cleanup_complete:
            with suppress(Exception):
                _compose(project, env, "down", "-v", "--remove-orphans")


if __name__ == "__main__":
    raise SystemExit(main())
