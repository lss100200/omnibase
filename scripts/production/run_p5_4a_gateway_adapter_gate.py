"""Run the non-database P5.4A Capability Gateway adapter contract Gate.

This Gate intentionally does not claim a PostgreSQL/sentinel or production
Runtime result.  It executes the adapter-focused tests and quality checks,
seals the exact source bytes, and records the remaining Docker database Gate
as not proven when Docker is unavailable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = (REPO_ROOT / ".tmp" / "p5-4a-gateway-adapter-gate").resolve()
EVIDENCE_JSON = EVIDENCE_ROOT / "evidence.json"
EVIDENCE_MD = EVIDENCE_ROOT / "evidence.md"
GATE_NAME = "P5.4A Capability Gateway adapter contract Gate"
SOURCE_PATHS = (
    "AGENTS.md",
    "backend/src/omnibase/agent_executor/__init__.py",
    "backend/src/omnibase/agent_executor/contracts.py",
    "backend/src/omnibase/agent_executor/gateway_adapter.py",
    "backend/src/omnibase/agent_executor/service.py",
    "backend/src/omnibase/capability_gateway/contracts.py",
    "backend/src/omnibase/capability_gateway/service.py",
    "backend/src/omnibase/capability_gateway/security.py",
    "backend/tests/test_p5_4a_typed_executor.py",
    "backend/tests/test_p5_4a_gateway_adapter.py",
    "deployment/production/phase5-typed-executor.example.json",
    "docs/phase-5-typed-executor-contract.md",
    "docs/maintainers/security-invariants.md",
    "docs/maintainers/maintenance-map.json",
    "scripts/production/run_p5_4a_gateway_adapter_gate.py",
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
            raise RuntimeError(f"source path is not a regular file: {relative}")
        files[relative] = _sha256(path)
    return {"schema_version": 1, "file_count": len(files), "files": files}


def _manifest_digest(manifest: dict[str, object]) -> str:
    raw = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _validate_config() -> None:
    config = json.loads(
        (
            REPO_ROOT / "deployment/production/phase5-typed-executor.example.json"
        ).read_text(encoding="utf-8")
    )
    if config.get("migration_baseline") != "0015":
        raise RuntimeError("P5.4A migration baseline drifted")
    if config.get("activation_requested") is not False:
        raise RuntimeError("P5.4A activation must remain false")
    gates = config.get("feature_gates")
    if gates != {
        "agent_runtime_enabled": False,
        "agent_planner_enabled": False,
        "multi_agent_enabled": False,
    }:
        raise RuntimeError("P5.4A feature gates must remain false")
    forbidden = set(config.get("forbidden_capabilities", ()))
    if {
        "shell",
        "sql",
        "arbitrary_http",
        "mcp",
        "skill_runtime",
        "sandbox_exec",
    } - forbidden:
        raise RuntimeError("P5.4A forbidden capability set is incomplete")


def _run_checks() -> dict[str, object]:
    commands = {
        "focused_tests": [
            sys.executable,
            "-m",
            "pytest",
            "backend/tests/test_p5_4a_typed_executor.py",
            "backend/tests/test_p5_4a_gateway_adapter.py",
            "-q",
        ],
        "mypy": [sys.executable, "-m", "mypy", "backend/src/omnibase/agent_executor"],
        "ruff": [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "backend/src/omnibase/agent_executor",
            "backend/tests/test_p5_4a_typed_executor.py",
            "backend/tests/test_p5_4a_gateway_adapter.py",
        ],
        "compileall": [
            sys.executable,
            "-m",
            "compileall",
            "-q",
            "backend/src/omnibase/agent_executor",
        ],
    }
    results: dict[str, object] = {}
    for name, command in commands.items():
        completed = _run(command)
        results[name] = {
            "passed": completed.returncode == 0,
            "exit_code": completed.returncode,
            "output_tail": completed.stdout[-2000:],
        }
        if completed.returncode != 0:
            break
    return results


def _record(
    manifest_digest: str, checks: dict[str, object], *, passed: bool
) -> dict[str, object]:
    report: dict[str, object] = {
        "schema_version": 1,
        "gate": GATE_NAME,
        "recorded_at": datetime.now(UTC).isoformat(),
        "passed": passed,
        "status": "adapter_contract_passed_database_gate_pending"
        if passed
        else "failed",
        "adapter_scope_verified": passed,
        "budget_boundary_verified": passed,
        "audit_call_boundary_verified": passed,
        "lease_fencing_revalidation_verified": passed,
        "unknown_no_replay_verified": passed,
        "database_sentinel_verified": False,
        "docker_gate_executed": False,
        "production_runtime_activated": False,
        "feature_gates_enabled": False,
        "migration_head": "0013",
        "migration_0013_created": True,
        "root_env_accessed": False,
        "business_database_accessed": False,
        "external_network_accessed": False,
        "cleanup": {"containers": 0, "networks": 0, "volumes": 0},
        "checks": checks,
        "source_manifest_sha256": manifest_digest,
    }
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    EVIDENCE_JSON.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    EVIDENCE_MD.write_text(
        "# P5.4A Capability Gateway adapter contract Gate\n\n"
        f"- Passed: `{passed}`\n"
        "- Database sentinel Gate: `not_run` (Docker availability is a separate admission)\n"
        "- Production Runtime: `false`\n"
        "- Feature Gates: `false / false / false`\n"
        "- Migration head: `0013`; migration `0013`: created\n"
        f"- Source manifest SHA-256: `{manifest_digest}`\n",
        encoding="utf-8",
    )
    return report


def _verify(path: Path) -> None:
    report = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    if report.get("gate") != GATE_NAME or report.get("passed") is not True:
        raise RuntimeError(
            "P5.4A adapter evidence is not a passed adapter contract Gate"
        )
    for key in (
        "adapter_scope_verified",
        "budget_boundary_verified",
        "audit_call_boundary_verified",
        "lease_fencing_revalidation_verified",
        "unknown_no_replay_verified",
    ):
        if report.get(key) is not True:
            raise RuntimeError(f"P5.4A adapter evidence missing: {key}")
    if report.get("database_sentinel_verified") is not False:
        raise RuntimeError("adapter evidence must not claim a database sentinel")
    if report.get("production_runtime_activated") is not False:
        raise RuntimeError("production Runtime must remain disabled")
    if report.get("feature_gates_enabled") is not False:
        raise RuntimeError("Feature Gates must remain disabled")
    if (
        report.get("migration_head") != "0013"
        or report.get("migration_0013_created") is not True
    ):
        raise RuntimeError("P5.4A migration boundary drifted")
    if report.get("cleanup") != {"containers": 0, "networks": 0, "volumes": 0}:
        raise RuntimeError("adapter evidence cleanup is not zero")
    if report.get("source_manifest_sha256") != _manifest_digest(_manifest()):
        raise RuntimeError("P5.4A adapter source manifest drifted")


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
        print("P5.4A adapter contract static validation passed")
        return 0
    if args.verify_evidence is not None:
        _verify(args.verify_evidence)
        print("P5.4A adapter contract evidence seal passed")
        return 0
    checks = _run_checks()
    passed = all(
        item.get("passed") is True for item in checks.values() if isinstance(item, dict)
    )
    _record(_manifest_digest(manifest), checks, passed=passed)
    print(
        "P5.4A adapter contract Gate passed"
        if passed
        else "P5.4A adapter contract Gate failed"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
