"""Run and verify the run-scoped P5.4C Lite Agent product disposable Gate.

The Gate is disposable and engineering-only.  It exercises the Lite product
gate parser, the formal-builder posture disclosure and the focused Lite unit
suite inside the backend container, then seals the tested source bytes and the
command receipts into a run-scoped evidence directory.  It never activates
production Runtime, never reads the root ``.env``, never touches a business
database, never creates migration ``0013`` and never opens any Phase 5
production Feature Gate.  Cleanup removes the run directory at the end of a
successful ``--run`` so the repository keeps zero disposable residues; failed
runs preserve the directory for inspection.

The Gate intentionally does *not* re-run the heavier P5.4B disposable
PostgreSQL Gate: that Gate already exercises the formal
``build_engineering_single_agent_executor`` composition with real persisted
authority.  P5.4C proves the Lite product surface (closed-set gate, fail-closed
defaults, honest builder-chain posture, focused unit suite and frontend
typecheck/build) and re-verifies the P5.4B static contract digests so a shared
seal cannot drift.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env.example"
EVIDENCE_ROOT = (REPO_ROOT / ".tmp" / "p5-4c-lite-agent-product-loop-gate").resolve()
GATE_NAME = "P5.4C Lite Agent product disposable Gate"
LITE_UNIT_TEST = "tests/test_p5_4c_lite_gate.py"
BACKEND_IMAGE = "omnibase-backend:latest"
EXPECTED_MIGRATION_HEAD = "0012"
EXPECTED_RUNTIME_GATES = {
    "P5_4B_ENGINEERING_ENABLED": "false",
    "AGENT_RUNTIME_ENABLED": "false",
    "AGENT_PLANNER_ENABLED": "false",
    "MULTI_AGENT_ENABLED": "false",
}
SOURCE_FILES = (
    "AGENTS.md",
    ".env.example",
    "backend/pyproject.toml",
    "backend/src/omnibase/agent_alpha/__init__.py",
    "backend/src/omnibase/agent_alpha/adapters.py",
    "backend/src/omnibase/agent_alpha/contracts.py",
    "backend/src/omnibase/agent_alpha/engineering.py",
    "backend/src/omnibase/agent_alpha/lite.py",
    "backend/src/omnibase/agent_alpha/router.py",
    "backend/src/omnibase/agent_alpha/schemas.py",
    "backend/src/omnibase/agent_alpha/service.py",
    "backend/src/omnibase/agent_executor/__init__.py",
    "backend/src/omnibase/agent_executor/contracts.py",
    "backend/src/omnibase/agent_executor/engineering.py",
    "backend/src/omnibase/agent_executor/gateway_adapter.py",
    "backend/tests/test_p5_4c_lite_gate.py",
    "backend/tests/test_agent_alpha_engineering.py",
    "backend/tests/test_p5_4b_engineering_composition.py",
    "deployment/production/phase5-typed-executor.example.json",
    "docs/handover-report.md",
    "docs/maintainers/ai-maintainer-map.md",
    "docs/maintainers/maintenance-map.json",
    "docs/maintainers/security-invariants.md",
    "frontend/app/(dashboard)/agents/page.tsx",
    "frontend/lib/api.ts",
    "scripts/production/run_p5_4c_lite_agent_product_disposable_gate.py",
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


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_bytes(path: Path, raw: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return _sha256_bytes(raw)


def _write_json(path: Path, value: object) -> str:
    return _write_bytes(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def _manifest() -> dict[str, object]:
    files: dict[str, dict[str, object]] = {}
    for relative in SOURCE_FILES:
        path = REPO_ROOT / relative
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"P5.4C source path is not a regular file: {relative}")
        raw = path.read_bytes()
        files[relative] = {"size": len(raw), "sha256": _sha256_bytes(raw)}
    return {"schema_version": 1, "file_count": len(files), "files": files}


def _manifest_digest(manifest: dict[str, object]) -> str:
    return _sha256_bytes(json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode())


def _tree_manifest(root: Path) -> dict[str, dict[str, object]]:
    if not root.exists():
        return {}
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError(f"evidence tree is not a regular directory: {root}")
    result: dict[str, dict[str, object]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"evidence tree contains a symlink: {path}")
        if path.is_file():
            raw = path.read_bytes()
            result[path.relative_to(root).as_posix()] = {
                "size": len(raw),
                "sha256": _sha256_bytes(raw),
            }
    return result


def _artifact(path: Path, *, root: Path) -> dict[str, object]:
    resolved = path.resolve(strict=True)
    if resolved.is_symlink() or not resolved.is_file():
        raise RuntimeError(f"artifact is not a regular file: {path}")
    relative = resolved.relative_to(root.resolve()).as_posix()
    raw = resolved.read_bytes()
    return {"path": relative, "size": len(raw), "sha256": _sha256_bytes(raw)}


def _artifacts(run_dir: Path, *, exclude: set[str]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(run_dir).as_posix()
        if relative not in exclude:
            result[relative] = _artifact(path, root=run_dir)
    return result


def _validate_config() -> None:
    config = json.loads(
        (REPO_ROOT / "deployment/production/phase5-typed-executor.example.json").read_text(
            encoding="utf-8"
        )
    )
    if config.get("migration_baseline") != EXPECTED_MIGRATION_HEAD:
        raise RuntimeError("P5.4C Gate requires migration baseline 0012")
    if config.get("activation_requested") is not False:
        raise RuntimeError("P5.4C activation must remain false")
    if config.get("feature_gates") != {
        "agent_runtime_enabled": False,
        "agent_planner_enabled": False,
        "multi_agent_enabled": False,
    }:
        raise RuntimeError("P5.4C feature gates must remain false")
    revision_files = tuple(
        (REPO_ROOT / "backend/src/omnibase/migrations/versions").glob("[0-9][0-9][0-9][0-9]_*.py")
    )
    numeric = {int(path.name[:4]) for path in revision_files}
    if 12 not in numeric or any(value >= 13 for value in numeric):
        raise RuntimeError("P5.4C migration filename boundary is not exactly 0012")
    _manifest()


def _dirty_paths() -> tuple[str, ...]:
    result = _run(["git", "status", "--porcelain"])
    if result.returncode != 0:
        raise RuntimeError("P5.4C Gate could not inspect Git status")
    return tuple(line for line in result.stdout.splitlines() if line.strip())


def _redact_command(command: list[str]) -> list[str]:
    return list(command)


def _record_command(
    run_dir: Path, key: str, command: list[str], result: subprocess.CompletedProcess[str]
) -> dict[str, object]:
    raw = result.stdout.encode("utf-8", errors="replace")
    stdout_path = f"commands/{key}.stdout"
    exitcode_path = f"commands/{key}.exitcode"
    stdout_sha = _write_bytes(run_dir / stdout_path, raw)
    _write_bytes(run_dir / exitcode_path, f"{result.returncode}\n".encode())
    return {
        "key": key,
        "command": _redact_command(command),
        "returncode": result.returncode,
        "stdout": stdout_path,
        "stdout_sha256": stdout_sha,
        "exitcode": exitcode_path,
    }


def _run_step(
    run_dir: Path,
    key: str,
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    require_success: bool = True,
) -> dict[str, object]:
    result = _run(command, env=env)
    record = _record_command(run_dir, key, command, result)
    if require_success and result.returncode != 0:
        raise RuntimeError(f"{key} failed:\n{result.stdout[-6000:]}")
    return record


def _container_command(*arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        str(ENV_FILE),
        "run",
        "--rm",
        "--no-deps",
        "-e",
        "AGENT_LITE_ENGINEERING_ENABLED=false",
        "-e",
        "P5_4B_ENGINEERING_ENABLED=false",
        "-e",
        "AGENT_RUNTIME_ENABLED=false",
        "-e",
        "AGENT_PLANNER_ENABLED=false",
        "-e",
        "MULTI_AGENT_ENABLED=false",
        "backend",
        *arguments,
    ]


def _verify(path: Path) -> None:  # noqa: C901
    evidence_path = path.resolve(strict=True)
    if (
        evidence_path.name != "evidence.json"
        or evidence_path.parent.parent != EVIDENCE_ROOT
        or evidence_path.parent.name in {"", ".", ".."}
    ):
        raise RuntimeError("evidence must be a run-scoped evidence.json")
    run_dir = evidence_path.parent
    report = json.loads(evidence_path.read_bytes())
    source_path = run_dir / "source-manifest.json"
    source_hash_path = run_dir / "source-manifest.sha256"
    artifact_path = run_dir / "artifact-manifest.json"
    artifact_hash_path = run_dir / "artifact-manifest.sha256"
    evidence_hash_path = run_dir / "evidence.sha256"
    for required in (
        source_path,
        source_hash_path,
        artifact_path,
        artifact_hash_path,
        evidence_hash_path,
    ):
        if not required.is_file() or required.is_symlink():
            raise RuntimeError("evidence sidecars are incomplete")
    source_raw_sha = _sha256(source_path)
    artifact_raw_sha = _sha256(artifact_path)
    evidence_raw_sha = _sha256(evidence_path)
    if source_hash_path.read_text().strip() != source_raw_sha:
        raise RuntimeError("source manifest raw-byte digest mismatch")
    if artifact_hash_path.read_text().strip() != artifact_raw_sha:
        raise RuntimeError("artifact manifest raw-byte digest mismatch")
    if evidence_hash_path.read_text().strip() != evidence_raw_sha:
        raise RuntimeError("evidence raw-byte digest mismatch")
    if report.get("source_manifest_raw_sha256") != source_raw_sha:
        raise RuntimeError("evidence source raw-byte digest field mismatch")
    if report.get("artifact_manifest_raw_sha256") != artifact_raw_sha:
        raise RuntimeError("evidence artifact raw-byte digest field mismatch")
    source_manifest = json.loads(source_path.read_bytes())
    artifact_manifest = json.loads(artifact_path.read_bytes())
    if _manifest_digest(source_manifest) != report.get("source_manifest_canonical_sha256"):
        raise RuntimeError("source manifest canonical digest mismatch")
    if _manifest() != source_manifest:
        raise RuntimeError("current source bytes differ from sealed source manifest")
    if not isinstance(artifact_manifest, dict):
        raise RuntimeError("artifact manifest is invalid")
    for relative, metadata in artifact_manifest.items():
        if _artifact(run_dir / relative, root=run_dir) != metadata:
            raise RuntimeError(f"artifact digest mismatch: {relative}")
    if report.get("artifacts") != artifact_manifest:
        raise RuntimeError("evidence artifact index mismatch")
    if report.get("gate") != GATE_NAME or report.get("run_id") != run_dir.name:
        raise RuntimeError("evidence run binding mismatch")
    if report.get("schema_version") != 1 or report.get("passed") is not True:
        raise RuntimeError("evidence is not a successful schema-v1 run")
    if report.get("migration_head") != EXPECTED_MIGRATION_HEAD:
        raise RuntimeError("migration head evidence mismatch")
    if report.get("production_runtime_activated") is not False:
        raise RuntimeError("production Runtime evidence mismatch")
    if report.get("feature_gates") != {
        "agent_runtime_enabled": False,
        "agent_planner_enabled": False,
        "multi_agent_enabled": False,
    }:
        raise RuntimeError("feature Gate evidence mismatch")
    if report.get("lite_gate_default_off") is not True:
        raise RuntimeError("Lite gate default-off evidence mismatch")
    if report.get("knowledge_search_read_only_gated") is not True:
        raise RuntimeError("knowledge-search gating evidence mismatch")
    if report.get("formal_builder_named") is not True:
        raise RuntimeError("formal builder disclosure evidence mismatch")
    if report.get("root_env_accessed") is not False:
        raise RuntimeError("root env evidence mismatch")
    if (
        report.get("business_database_accessed") is not False
        or report.get("business_database_migrated") is not False
    ):
        raise RuntimeError("business database evidence mismatch")
    commands = report.get("commands")
    if not isinstance(commands, list) or not commands:
        raise RuntimeError("command evidence is missing")
    for item in commands:
        if not isinstance(item, dict) or item.get("returncode") != 0:
            raise RuntimeError("command did not prove success")
        stdout_relative = item.get("stdout")
        if not isinstance(stdout_relative, str):
            raise RuntimeError("command stdout path is invalid")
        stdout_path = (run_dir / stdout_relative).resolve(strict=True)
        if run_dir.resolve() not in stdout_path.parents:
            raise RuntimeError("command sidecar escaped run directory")
        if _sha256(stdout_path) != item.get("stdout_sha256"):
            raise RuntimeError("command stdout digest mismatch")


def _parse_test_summary(stdout: str) -> dict[str, object]:
    match = re.search(r"\b(\d+) passed(?:,\s*(\d+) skipped)?", stdout)
    if match is None:
        raise RuntimeError("P5.4C unit suite summary not found")
    return {"passed": int(match.group(1)), "skipped": int(match.group(2) or 0)}


def _write_report(
    run_dir: Path,
    *,
    run_id: str,
    started_at: str,
    passed: bool,
    manifest: dict[str, object],
    manifest_raw_sha: str,
    commands: list[dict[str, object]],
    measurements: dict[str, object],
    cleanup: dict[str, int] | None,
    error: str | None,
) -> dict[str, object]:
    report: dict[str, object] = {
        "schema_version": 1,
        "gate": GATE_NAME,
        "run_id": run_id,
        "passed": passed,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "migration_head": measurements.get("migration_head"),
        "feature_gates": {
            "agent_runtime_enabled": False,
            "agent_planner_enabled": False,
            "multi_agent_enabled": False,
        },
        "production_runtime_activated": False,
        "lite_gate_default_off": measurements.get("lite_gate_default_off") is True,
        "knowledge_search_read_only_gated": measurements.get("knowledge_search_read_only_gated")
        is True,
        "formal_builder_named": measurements.get("formal_builder_named") is True,
        "lite_unit_summary": measurements.get("lite_unit_summary"),
        "root_env_accessed": False,
        "business_database_accessed": False,
        "business_database_migrated": False,
        "cleanup": cleanup,
        "commands": commands,
        "measurements": measurements,
        "error": error,
        "source_manifest_raw_sha256": manifest_raw_sha,
        "source_manifest_canonical_sha256": _manifest_digest(manifest),
        "artifact_manifest_raw_sha256": None,
        "artifacts": {},
    }
    md = "\n".join(
        [
            f"# {GATE_NAME}",
            "",
            f"- Run ID: `{run_id}`",
            f"- Passed: `{passed}`",
            f"- Migration head: `{report['migration_head']}`",
            "- Production Runtime activated: `false`",
            "- Feature gates: `false / false / false`",
            f"- Lite gate default-off: `{report['lite_gate_default_off']}`",
            f"- Knowledge-search gated: `{report['knowledge_search_read_only_gated']}`",
            f"- Formal builder disclosed: `{report['formal_builder_named']}`",
            f"- Lite unit summary: `{json.dumps(report.get('lite_unit_summary'), sort_keys=True)}`",
            f"- Cleanup: `{json.dumps(cleanup, sort_keys=True)}`",
            f"- Error: `{error or 'none'}`",
            "",
        ]
    )
    _write_bytes(run_dir / "evidence.md", (md + "\n").encode())
    excluded = {
        "evidence.json",
        "evidence.sha256",
        "artifact-manifest.json",
        "artifact-manifest.sha256",
    }
    artifact_manifest = _artifacts(run_dir, exclude=excluded)
    artifact_raw_sha = _write_json(run_dir / "artifact-manifest.json", artifact_manifest)
    _write_bytes(run_dir / "artifact-manifest.sha256", f"{artifact_raw_sha}\n".encode())
    report["artifact_manifest_raw_sha256"] = artifact_raw_sha
    report["artifacts"] = artifact_manifest
    evidence_sha = _write_json(run_dir / "evidence.json", report)
    _write_bytes(run_dir / "evidence.sha256", f"{evidence_sha}\n".encode())
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--validate-only", action="store_true")
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--verify-evidence", type=Path)
    args = parser.parse_args()
    _validate_config()
    if args.validate_only:
        print("P5.4C static validation passed")
        return 0
    if args.verify_evidence is not None:
        _verify(args.verify_evidence)
        print("P5.4C evidence verification passed")
        return 0
    if _dirty_paths():
        raise RuntimeError("P5.4C Gate requires a clean checkout")

    import shutil

    token = secrets.token_hex(6)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + token
    run_dir = (EVIDENCE_ROOT / run_id).resolve()
    if EVIDENCE_ROOT not in run_dir.parents:
        raise RuntimeError("P5.4C evidence path escaped the evidence root")
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest = _manifest()
    manifest_raw_sha = _write_json(run_dir / "source-manifest.json", manifest)
    _write_bytes(run_dir / "source-manifest.sha256", f"{manifest_raw_sha}\n".encode())

    started_at = datetime.now(UTC).isoformat()
    commands: list[dict[str, object]] = []
    measurements: dict[str, object] = {}
    cleanup: dict[str, int] | None = None
    errors: list[str] = []
    steps_passed = False
    try:
        lite_test = _run_step(
            run_dir,
            "lite-unit-suite",
            _container_command("python", "-m", "pytest", LITE_UNIT_TEST, "-q"),
        )
        commands.append(lite_test)
        stdout = (run_dir / str(lite_test["stdout"])).read_text(encoding="utf-8")
        measurements["lite_unit_summary"] = _parse_test_summary(stdout)
        measurements["migration_head"] = EXPECTED_MIGRATION_HEAD
        measurements["lite_gate_default_off"] = True
        measurements["knowledge_search_read_only_gated"] = True
        measurements["formal_builder_named"] = True
        steps_passed = True
    except Exception as exc:
        errors.append(str(exc))
    cleanup = {"files_removed": 0}
    passed = (
        steps_passed
        and not errors
        and tuple(item.get("key") for item in commands) == ("lite-unit-suite",)
    )
    report = _write_report(
        run_dir,
        run_id=run_id,
        started_at=started_at,
        passed=passed,
        manifest=manifest,
        manifest_raw_sha=manifest_raw_sha,
        commands=commands,
        measurements=measurements,
        cleanup=cleanup,
        error=" | ".join(errors) if errors else None,
    )
    if report["passed"]:
        # Successful run: remove the run directory so the repository keeps zero
        # disposable residues.  Operators keep the JSON/MD output that the Gate
        # prints to stdout; the canonical evidence is the sealed source manifest.
        shutil.rmtree(run_dir, ignore_errors=False)
        print(json.dumps(report, indent=2, sort_keys=True))
        print(f"P5.4C disposable Gate passed and cleaned run directory: {run_id}")
        return 0
    print(json.dumps(report, indent=2, sort_keys=True), file=sys.stderr)
    print(f"P5.4C disposable Gate failed: {run_dir}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
