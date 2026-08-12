"""Run and verify the run-scoped P5.4C Lite Agent product disposable Gate.

The Gate is disposable and engineering-only.  It exercises the Lite product
surface inside the backend container:

- ``lite-unit-suite``: the focused Lite posture suite plus the formal P5.4B
  engineering-composition suite. The latter executes the formal builder,
  live authority validator and workload-digest drift negatives before the
  Gate may claim ``proven_engineering_only``;
- ``lite-gate-probes``: an executed probe that patches the process environment
  and measures the runtime resolver (absent -> off, false -> off, true -> on,
  invalid -> fail closed), the live posture env read, and the single supported
  invocation mode ``no_tool`` with the formal P5.4B builder disclosure.

Every claim in the report is derived from an executed command receipt or a
sealed file measurement; nothing is hardcoded as a measurement:

- parser/resolver/posture claims are parsed from the sealed probe stdout;
- ``migration_head`` is discovered from the migration directory and the typed
  executor example config on every run and every verification;
- ``root_env_accessed`` / ``business_database_accessed`` /
  ``business_database_migrated`` are re-derived from the recorded command
  vectors (the Gate only ever runs the recorded commands);
- the probe's ``formal_builder_integration`` token is recorded **honestly**:
  the posture reports ``proven_engineering_only`` (the formal P5.4B builder
  is formally connected to this product loop through a proven integration
  fixture).  The ``not_proven`` rewrite for ``not_integrated`` probe tokens
  is retained as defence-in-depth: a tampered probe that reports
  ``not_integrated`` is rewritten to ``not_proven`` and fails the admission
  expectation; any other non-matching token is recorded verbatim and fails
  the closed-set admission decision.  ``formal_builder_posture_not_integrated``
  independently records whether the probe really returned ``not_integrated``
  (``False`` when the posture genuinely reports ``proven_engineering_only``).

The run directory is **preserved** on success and on failure and can be
independently re-verified later with ``--verify-evidence``; the Gate never
deletes its own evidence.  It never activates production Runtime, never reads
  the root ``.env``, never touches a business database, and never opens any
  Phase 5 production Feature Gate. Migration ``0015`` is the current repository
  baseline and is only measured as a source fact.

**Integrity scope.**  The sealed evidence is a **self-contained integrity
receipt**: it proves run-scoped byte integrity of the recorded source
manifest (the closed set of files that decide Compose Lite-flag wiring,
frontend ``canInvoke`` and Gate admission — including ``docker-compose.yml``,
``frontend/lib/lite-gate.ts`` and ``frontend/lib/lite-gate.test.ts``),
command receipts (including the strictly parsed single-decimal
``commands/*.exitcode`` sidecars) and measurements.  Without an independent
trust anchor it proves **no external authenticity**: it cannot authenticate
who produced the bytes or that they came from any particular host, and it is
never production admission.  ``--verify-evidence`` re-executes the same
admission decision that ``--run`` computed, and rejects any evidence whose
receipts or vectors fail it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env.example"
EVIDENCE_ROOT = (REPO_ROOT / ".tmp" / "p5-4c-lite-agent-product-loop-gate").resolve()
GATE_NAME = "P5.4C Lite Agent product disposable Gate"
LITE_UNIT_TESTS = (
    "tests/test_p5_4c_lite_gate.py",
    "tests/test_p5_4b_engineering_composition.py",
)
BACKEND_IMAGE = "omnibase-backend:latest"
EXPECTED_MIGRATION_HEAD = "0015"
EXPECTED_RUNTIME_GATES = {
    "P5_4B_ENGINEERING_ENABLED": "false",
    "AGENT_RUNTIME_ENABLED": "false",
    "AGENT_PLANNER_ENABLED": "false",
    "MULTI_AGENT_ENABLED": "false",
}
FORMAL_BUILDER_NAME = "build_engineering_single_agent_executor"

# Executed inside the backend container by ``--run``: patches the process
# environment and measures the runtime resolver and the live posture.  The
# probe prints exactly one JSON object, which becomes the sealed receipt.
_PROBE_SOURCE = (
    "import json, os\n"
    "from omnibase.agent_alpha.lite import (\n"
    "    LiteAgentConfigurationError, lite_agent_posture, runtime_lite_agent_enabled)\n"
    "FLAG = 'AGENT_LITE_ENGINEERING_ENABLED'\n"
    "def absent() -> bool:\n"
    "    os.environ.pop(FLAG, None)\n"
    "    return bool(runtime_lite_agent_enabled())\n"
    "def set_flag(value: str) -> None:\n"
    "    os.environ[FLAG] = value\n"
    "def false_off() -> bool:\n"
    "    set_flag('false')\n"
    "    return bool(runtime_lite_agent_enabled())\n"
    "def true_on() -> bool:\n"
    "    set_flag('true')\n"
    "    return bool(runtime_lite_agent_enabled())\n"
    "def invalid_fail_closed() -> bool:\n"
    "    set_flag('1')\n"
    "    try:\n"
    "        runtime_lite_agent_enabled()\n"
    "    except LiteAgentConfigurationError:\n"
    "        return True\n"
    "    return False\n"
    "def live_posture_reflects_env() -> bool:\n"
    "    set_flag('true')\n"
    "    return bool(lite_agent_posture()['lite_gate_enabled'])\n"
    "posture = lite_agent_posture(env={})\n"
    "print(json.dumps({\n"
    "    'absent_off': not absent(),\n"
    "    'false_off': not false_off(),\n"
    "    'true_on': true_on(),\n"
    "    'invalid_fail_closed': invalid_fail_closed(),\n"
    "    'live_posture_reflects_env': live_posture_reflects_env(),\n"
    "    'modes': list(posture['supported_invocation_modes']),\n"
    "    'formal_builder': posture['formal_builder'],\n"
    "    'formal_builder_integration': posture['formal_builder_integration'],\n"
    "    'engineering_composition_ready': posture['engineering_composition_ready'],\n"
    "    'activation_allowed': posture['activation_allowed'],\n"
    "}, sort_keys=True))\n"
)

# The sealed source closure must cover every file that decides the Gate
# admission surface: the Compose Lite-flag wiring (docker-compose.yml,
# .env.example), the backend gate/parser/posture and router wiring
# (agent_alpha/**), the executor migration-head bound (agent_executor/**), the
# frontend canInvoke decision (frontend/lib/lite-gate.ts and its tests, the
# workbench page and API client), the focused test targets, the offline typed
# executor contract the admission re-reads, the maintainer docs that declare
# the INV-051 invariant, the handover report and this runner itself.  The
# maintenance map's lite-agent-product-loop module source_paths must stay a
# subset of this closure (enforced by the gate tests).
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
    "backend/tests/test_p5_4c_lite_agent_product_gate.py",
    "backend/tests/test_agent_alpha_engineering.py",
    "backend/tests/test_p5_4b_engineering_composition.py",
    "deployment/production/phase5-typed-executor.example.json",
    "docker-compose.yml",
    "docs/handover-report.md",
    "docs/maintainers/ai-maintainer-map.md",
    "docs/maintainers/maintenance-map.json",
    "docs/maintainers/security-invariants.md",
    "docs/phase-5-lite-agent-product-loop.md",
    "frontend/app/(dashboard)/agents/page.tsx",
    "frontend/lib/api.ts",
    "frontend/lib/lite-gate.ts",
    "frontend/lib/lite-gate.test.ts",
    "scripts/production/run_p5_4c_lite_agent_product_disposable_gate.py",
)

_DB_ACCESS_MARKERS = (
    "postgresql+psycopg://",
    "postgres://",
    "psql",
    "alembic",
    "omnibase_test_",
    "OMNIBASE_INTEGRATION_TESTS",
)
_MIGRATION_MARKERS = ("alembic", "migrate", "upgrade head", "upgrade_head")


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
    return _write_bytes(
        path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    )


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
    return _sha256_bytes(
        json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode()
    )


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


def _discover_migration_head() -> str:
    """Measure the migration boundary from the actual repository files."""
    versions = REPO_ROOT / "backend/src/omnibase/migrations/versions"
    numeric = {
        int(path.name[:4])
        for path in versions.glob("[0-9][0-9][0-9][0-9]_*.py")
        if path.is_file() and not path.is_symlink()
    }
    if 15 not in numeric or any(value >= 16 for value in numeric):
        raise RuntimeError("P5.4C migration filename boundary is not exactly 0015")
    return "0015"


def _validate_config() -> None:
    config = json.loads(
        (
            REPO_ROOT / "deployment/production/phase5-typed-executor.example.json"
        ).read_text(encoding="utf-8")
    )
    if config.get("migration_baseline") != EXPECTED_MIGRATION_HEAD:
        raise RuntimeError("P5.4C Gate requires migration baseline 0015")
    if config.get("activation_requested") is not False:
        raise RuntimeError("P5.4C activation must remain false")
    if config.get("feature_gates") != {
        "agent_runtime_enabled": False,
        "agent_planner_enabled": False,
        "multi_agent_enabled": False,
    }:
        raise RuntimeError("P5.4C feature gates must remain false")
    if _discover_migration_head() != EXPECTED_MIGRATION_HEAD:
        raise RuntimeError("P5.4C migration head measurement failed")
    _manifest()


def _dirty_paths() -> tuple[str, ...]:
    result = _run(["git", "status", "--porcelain"])
    if result.returncode != 0:
        raise RuntimeError("P5.4C Gate could not inspect Git status")
    return tuple(line for line in result.stdout.splitlines() if line.strip())


def _redact_command(command: list[str]) -> list[str]:
    return list(command)


def _record_command(
    run_dir: Path,
    key: str,
    command: list[str],
    result: subprocess.CompletedProcess[str],
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


_EXITCODE_SIDECAR_RE = re.compile(r"[0-9]+\n")

# The only two command keys the Gate may record, in the only order it may
# record them.  The verifier requires the receipt set to be exactly this
# closed set: no missing key, no duplicate, no extra/unknown key, no re-order.
COMMAND_KEYS: tuple[str, ...] = ("lite-unit-suite", "lite-gate-probes")


# The exact POSIX path literal every command key must bind its stdout and
# exitcode sidecar to.  The verifier compares the receipt's path LITERAL against
# this string BEFORE resolving (``_is_exact_sidecar_literal``): absolute paths,
# backslash alternatives, ``.``/``..`` segments, repeated separators, case
# aliases, URL/drive paths and any lexical alias (``commands/../commands/...``,
# ``commands/./...``) are rejected outright.  Only after the literal matches
# does the verifier resolve and check run-dir containment, regular-file,
# non-symlink and digest.  This binds each command key to its OWN unique
# sidecar, so two commands cannot share or swap stdout/exitcode artefacts.
def _expected_sidecar_literal(key: str, suffix: str) -> str:
    return f"commands/{key}.{suffix}"


def _is_exact_sidecar_literal(raw: object, *, key: str, suffix: str) -> bool:
    """Return ``True`` only if ``raw`` is exactly ``commands/{key}.{suffix}``.

    Rejects absolute paths, backslash alternatives, ``.``/``..`` segments,
    repeated separators, case aliases, URL/drive paths and any lexical alias
    BEFORE any filesystem resolution occurs.  ``isinstance(value, int)`` is
    deliberately avoided: this is a literal string comparison only.
    """
    if not isinstance(raw, str):
        return False
    expected = _expected_sidecar_literal(key, suffix)
    if raw != expected:
        return False
    # Defence in depth: the literal equality above already rejects every alias,
    # but also assert the normalised POSIX form is unchanged and there are no
    # absolute/drive/URL/backslash markers or traversal segments.
    if "\\" in raw or ":" in raw or raw.startswith("/"):
        return False
    parts = raw.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    return Path(raw).as_posix() == expected


def _parse_exitcode_sidecar(raw: str) -> int:
    """Strictly parse a ``commands/*.exitcode`` sidecar.

    The Gate writes exactly one decimal exit code followed by a newline
    (``_record_command``).  The verifier requires that exact single-line shape:
    non-integer content, empty content, multi-line content and missing files
    are all rejected rather than guessed at.
    """
    if _EXITCODE_SIDECAR_RE.fullmatch(raw) is None:
        raise RuntimeError(
            "command exitcode sidecar must contain exactly one decimal exit code"
        )
    return int(raw.rstrip("\n"))


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


# The exact argv templates of the only two commands the Gate may execute.  The
# verifier requires each recorded receipt to match its template byte-for-byte:
# the explicit ``.env.example`` path, the closed production engineering flags,
# the image service name and the exact Lite/formal-composition test targets /
# probe source are all part
# of the closed set and cannot drift while still verifying.
_EXPECTED_COMMAND_TEMPLATES: dict[str, list[str]] = {
    "lite-unit-suite": _container_command(
        "python", "-m", "pytest", *LITE_UNIT_TESTS, "-q"
    ),
    "lite-gate-probes": _container_command("python", "-c", _PROBE_SOURCE),
}


# ---------------------------------------------------------------------------
# Receipt-derived claim derivations: every negative claim below is computed
# from the actual recorded command vectors, never hardcoded.
# ---------------------------------------------------------------------------


def _command_arguments(
    commands: list[dict[str, object]],
) -> tuple[tuple[str, ...], ...]:
    vectors: list[tuple[str, ...]] = []
    for item in commands:
        command = item.get("command")
        if not isinstance(command, list) or not all(
            isinstance(part, str) for part in command
        ):
            raise RuntimeError("command receipt has an invalid command vector")
        vectors.append(tuple(command))
    return tuple(vectors)


def _receipt_root_env_accessed(commands: list[dict[str, object]]) -> bool:
    """The Gate never passes the root ``.env`` as an exact command argument."""
    return any(
        any(part == ".env" for part in vector)
        for vector in _command_arguments(commands)
    )


def _receipt_business_database_accessed(commands: list[dict[str, object]]) -> bool:
    """No recorded command connects to a database or runs a DB tool."""
    for vector in _command_arguments(commands):
        joined = " ".join(vector).lower()
        if any(marker in joined for marker in _DB_ACCESS_MARKERS):
            return True
    return False


def _receipt_business_database_migrated(commands: list[dict[str, object]]) -> bool:
    """No recorded command performs a migration."""
    for vector in _command_arguments(commands):
        joined = " ".join(vector).lower()
        if any(marker in joined for marker in _MIGRATION_MARKERS):
            return True
    return False


def _parse_probe(stdout: str) -> dict[str, object]:
    match = re.search(r"\{.*\}", stdout, flags=re.DOTALL)
    if match is None:
        raise RuntimeError("P5.4C gate probe JSON not found in receipt")
    probe = json.loads(match.group(0))
    if not isinstance(probe, dict):
        raise RuntimeError("P5.4C gate probe receipt is not a JSON object")
    for key in (
        "absent_off",
        "false_off",
        "true_on",
        "invalid_fail_closed",
        "live_posture_reflects_env",
    ):
        if not isinstance(probe.get(key), bool):
            raise RuntimeError(f"P5.4C gate probe receipt field {key} is invalid")
    if not isinstance(probe.get("modes"), list) or not isinstance(
        probe.get("formal_builder"), str
    ):
        raise RuntimeError("P5.4C gate probe receipt disclosure fields are invalid")
    if not isinstance(probe.get("formal_builder_integration"), str):
        raise RuntimeError(
            "P5.4C gate probe receipt formal_builder_integration is invalid"
        )
    if not isinstance(probe.get("engineering_composition_ready"), bool):
        raise RuntimeError(
            "P5.4C gate probe receipt engineering_composition_ready is invalid"
        )
    if not isinstance(probe.get("activation_allowed"), bool):
        raise RuntimeError("P5.4C gate probe receipt activation_allowed is invalid")
    return probe


def _derive_claims(
    probe: dict[str, object], commands: list[dict[str, object]]
) -> dict[str, object]:
    """Derive every claim from the executed probe receipt and command vectors.

    The formal-builder integration is recorded **honestly**: the probe reports
    the live posture's ``formal_builder_integration`` token and that token is
    what the Gate records.  The posture now reports
    ``proven_engineering_only`` (the formal P5.4B builder is formally connected
    to this product loop through a proven integration fixture).  The
    ``not_proven`` rewrite for ``not_integrated`` probe tokens is retained as
    defence-in-depth: a tampered probe that reports ``not_integrated`` is
    rewritten to ``not_proven`` and then fails the admission expectation
    (``proven_engineering_only``); any other non-matching token is recorded
    verbatim and fails the same expectation.  ``formal_builder_posture_not_integrated``
    is the independent posture-level claim: ``True`` only when the probe really
    returned ``not_integrated``.
    """
    raw_modes = probe.get("modes")
    if not isinstance(raw_modes, list):
        raise RuntimeError("P5.4C gate probe receipt modes field is invalid")
    modes = tuple(str(item) for item in raw_modes)
    probe_integration = probe["formal_builder_integration"]
    if not isinstance(probe_integration, str):
        raise RuntimeError(
            "P5.4C gate probe receipt formal_builder_integration is invalid"
        )
    posture_not_integrated = probe_integration == "not_integrated"
    return {
        "lite_gate_default_off": probe["absent_off"] is True,
        "runtime_env_resolver_absent_off": probe["absent_off"] is True,
        "runtime_env_resolver_false_off": probe["false_off"] is True,
        "runtime_env_resolver_true_on": probe["true_on"] is True,
        "runtime_env_resolver_invalid_fail_closed": probe["invalid_fail_closed"]
        is True,
        "live_posture_reflects_env": probe["live_posture_reflects_env"] is True,
        "knowledge_search_read_only_not_supported": (
            modes == ("no_tool",) and "knowledge_search_read_only" not in modes
        ),
        "formal_builder_named": probe["formal_builder"] == FORMAL_BUILDER_NAME,
        # Honest recording: proven_engineering_only is recorded verbatim; a
        # tampered not_integrated probe is rewritten to not_proven (defence-in-
        # depth) and fails the admission expectation; any other token is
        # recorded verbatim and fails.
        "formal_builder_integration": (
            "not_proven" if posture_not_integrated else probe_integration
        ),
        # Independent posture-level claim: False when the posture genuinely
        # reports proven_engineering_only (not not_integrated).
        "formal_builder_posture_not_integrated": posture_not_integrated,
        # New engineering-only proof claims: the composition is ready but
        # production activation is never allowed.
        "engineering_composition_ready": probe.get("engineering_composition_ready")
        is True,
        "activation_allowed": probe.get("activation_allowed"),
        "root_env_accessed": _receipt_root_env_accessed(commands),
        "business_database_accessed": _receipt_business_database_accessed(commands),
        "business_database_migrated": _receipt_business_database_migrated(commands),
    }


# The admission closed-set: every claim must meet its expectation or the run
# (and any evidence re-verification of it) is rejected.  The Gate may only
# PASS when all of the following hold; a single mismatch makes ``passed``
# false, and ``--verify-evidence`` re-executes the same decision.
# ``formal_builder_integration`` must be ``proven_engineering_only`` (the
# formal P5.4B builder is formally connected to this product loop through a
# proven integration fixture) and ``formal_builder_posture_not_integrated``
# must be ``False`` (the posture genuinely reports ``proven_engineering_only``,
# not ``not_integrated``).  A probe that reports
# integrated/enabled/available/selectable/empty/unknown/not_integrated is
# recorded honestly in the report but fails the expectations and is rejected.
ADMISSION_EXPECTATIONS: dict[str, object] = {
    "lite_gate_default_off": True,
    "runtime_env_resolver_absent_off": True,
    "runtime_env_resolver_false_off": True,
    "runtime_env_resolver_true_on": True,
    "runtime_env_resolver_invalid_fail_closed": True,
    "live_posture_reflects_env": True,
    "knowledge_search_read_only_not_supported": True,
    "formal_builder_named": True,
    "formal_builder_integration": "proven_engineering_only",
    "formal_builder_posture_not_integrated": False,
    "engineering_composition_ready": True,
    "activation_allowed": False,
    "root_env_accessed": False,
    "business_database_accessed": False,
    "business_database_migrated": False,
    "production_runtime_activated": False,
}


def _admission_mismatch(
    claims: Mapping[str, object],
    *,
    production_runtime_activated: object,
) -> str | None:
    """Re-execute the closed-set admission decision; return the first mismatch.

    ``claims`` carries the receipt-derived claim values; the report-level
    ``production_runtime_activated`` is passed separately because it is not
    part of ``_derive_claims``.  ``None`` means every expectation is met.
    """
    for key, expected in ADMISSION_EXPECTATIONS.items():
        actual = (
            claims.get(key)
            if key != "production_runtime_activated"
            else production_runtime_activated
        )
        if actual != expected:
            return f"{key}={actual!r} (expected {expected!r})"
    return None


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
    if _manifest_digest(source_manifest) != report.get(
        "source_manifest_canonical_sha256"
    ):
        raise RuntimeError("source manifest canonical digest mismatch")
    if _manifest() != source_manifest:
        raise RuntimeError("current source bytes differ from sealed source manifest")
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
    if report.get("evidence_preserved") is not True:
        raise RuntimeError("evidence-preservation claim mismatch")
    commands = report.get("commands")
    if not isinstance(commands, list) or not commands:
        raise RuntimeError("command evidence is missing")
    # Fix-2 (command closed set): the verifier requires the command keys to be
    # EXACTLY the Gate's closed set — no missing key, no duplicate, no
    # extra/unknown key, no re-order.  ``isinstance(value, int)`` is not used
    # for the returncode check below because ``bool`` is an ``int`` subclass.
    keys = [item.get("key") for item in commands]
    if not all(isinstance(key, str) for key in keys):
        raise RuntimeError("command receipt key is invalid")
    if len(keys) != len(set(keys)):
        raise RuntimeError("command receipt keys must not be duplicated")
    if len(keys) != len(COMMAND_KEYS) or set(keys) != set(COMMAND_KEYS):
        raise RuntimeError("command receipt set is not the closed P5.4C step set")
    if tuple(keys) != COMMAND_KEYS:
        raise RuntimeError(
            "command receipt set does not match the closed P5.4C step order"
        )
    stdout_literals: list[str] = []
    exitcode_literals: list[str] = []
    resolved_sidecars: list[Path] = []
    for item in commands:
        if not isinstance(item, dict):
            raise RuntimeError("command receipt is invalid")
        # Fix-1 (strict exit-code type): ``returncode`` must be a strict
        # ``int`` that equals ``0``.  ``type(value) is int`` rejects JSON
        # ``false``/``true`` (Python ``bool``, which ``isinstance(value, int)``
        # would wrongly accept because ``False == 0``), floats like ``0.0``,
        # strings like ``"0"``, ``null`` and any non-zero integer.
        returncode = item.get("returncode")
        if type(returncode) is not int:
            raise RuntimeError("command receipt returncode is not a strict integer")
        if returncode != 0:
            raise RuntimeError("command did not prove success")
        # Fix-3/Fix-4: the verifier validates the EXACT argv template of every
        # recorded command — the explicit .env.example path, the closed
        # production engineering flags and the exact test targets / probe source
        # are part of the closed set.  A command that merely "ran and
        # exited 0" with drifted arguments must be rejected.
        key = item["key"]
        template = _EXPECTED_COMMAND_TEMPLATES.get(key)
        if template is None:
            raise RuntimeError(
                f"command receipt key is not in the closed step set: {key!r}"
            )
        command_vector = item.get("command")
        if not isinstance(command_vector, list) or not all(
            isinstance(part, str) for part in command_vector
        ):
            raise RuntimeError("command vector is invalid")
        if tuple(command_vector) != tuple(template):
            raise RuntimeError(
                f"command vector for {key} does not match the exact closed template"
            )
        if any(Path(part).name == ".env" for part in command_vector):
            raise RuntimeError("recorded command must not reference the root .env")
        # Fix-3 (sidecar precise binding): the receipt's stdout path LITERAL
        # must be exactly ``commands/{key}.stdout`` BEFORE any resolve.  This
        # rejects absolute paths, backslash alternatives, ``.``/``..`` segments,
        # repeated separators, case aliases, URL/drive paths and every lexical
        # alias (``commands/../commands/{key}.stdout``, ``commands/./{key}.stdout``)
        # so that two commands cannot share or swap stdout artefacts and a unit
        # receipt cannot point at the probe's stdout (or vice versa).
        stdout_relative = item.get("stdout")
        if not _is_exact_sidecar_literal(stdout_relative, key=key, suffix="stdout"):
            raise RuntimeError(
                f"command stdout sidecar literal is not exactly commands/{key}.stdout"
            )
        stdout_literals.append(str(stdout_relative))
        stdout_unresolved = run_dir / str(stdout_relative)
        if stdout_unresolved.is_symlink():
            raise RuntimeError("command stdout sidecar must not be a symlink")
        stdout_path = stdout_unresolved.resolve(strict=True)
        if run_dir.resolve() not in stdout_path.parents:
            raise RuntimeError("command sidecar escaped run directory")
        if stdout_path.is_symlink() or not stdout_path.is_file():
            raise RuntimeError("command stdout sidecar is not a regular file")
        resolved_sidecars.append(stdout_path)
        if _sha256(stdout_path) != item.get("stdout_sha256"):
            raise RuntimeError("command stdout digest mismatch")
        # Fix-7: the verifier must read the commands/*.exitcode sidecar,
        # strictly parse exactly one decimal exit code and require it to equal
        # the receipt returncode.  A missing sidecar, a non-integer or
        # multi-line sidecar and any 0/1 drift between the sidecar and the
        # receipt are all rejected.
        exitcode_relative = item.get("exitcode")
        if not _is_exact_sidecar_literal(exitcode_relative, key=key, suffix="exitcode"):
            raise RuntimeError(
                f"command exitcode sidecar literal is not exactly commands/{key}.exitcode"
            )
        exitcode_literals.append(str(exitcode_relative))
        exitcode_unresolved = run_dir / str(exitcode_relative)
        if exitcode_unresolved.is_symlink():
            raise RuntimeError("command exitcode sidecar must not be a symlink")
        # Resolve BEFORE the containment check so a lexical ".." escape cannot
        # slip past a parents() comparison on the unresolved path.
        exitcode_path = exitcode_unresolved.resolve()
        if run_dir.resolve() not in exitcode_path.parents:
            raise RuntimeError("command exitcode sidecar escaped run directory")
        if exitcode_path.is_symlink() or not exitcode_path.is_file():
            raise RuntimeError("command exitcode sidecar is missing")
        resolved_sidecars.append(exitcode_path)
        try:
            sidecar_exit = _parse_exitcode_sidecar(
                exitcode_path.read_text(encoding="utf-8", errors="strict")
            )
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                "command exitcode sidecar is missing or undecodable"
            ) from exc
        if sidecar_exit != item["returncode"]:
            raise RuntimeError(
                "command exitcode sidecar drift: sidecar does not equal returncode"
            )
    # Fix-4 (cross-binding rejection): no two commands may share the same
    # stdout or exitcode sidecar literal, and the resolved artefacts must be
    # distinct.  The exact-literal binding above already forces each key to its
    # own sidecar, but this explicit distinct-set check rejects any attempt to
    # bind two commands to the same file (e.g. both pointing at the unit
    # stdout) and any same-inode sharing where the platform exposes inodes.
    if len(set(stdout_literals)) != len(stdout_literals):
        raise RuntimeError(
            "command stdout sidecars must not be shared between commands"
        )
    if len(set(exitcode_literals)) != len(exitcode_literals):
        raise RuntimeError(
            "command exitcode sidecars must not be shared between commands"
        )
    seen_inodes: set[tuple[object, object]] = set()
    for resolved in resolved_sidecars:
        stat = resolved.stat()
        identity = (stat.st_dev, stat.st_ino)
        # Platforms that do not expose a stable inode (e.g. some Windows
        # filesystems report st_ino == 0 for every file) collapse to a single
        # identity; in that case the literal+digest binding above is the
        # authoritative cross-binding defence, and this inode check is a
        # best-effort no-op rather than a false positive.
        if identity in seen_inodes and identity != (0, 0):
            raise RuntimeError("command sidecars must not share the same inode")
        seen_inodes.add(identity)
    # The artifact byte check runs after the per-command semantic checks so
    # that a fabricated-but-self-consistent evidence tree (sidecar bytes,
    # manifests and hashes all rewritten together) is still rejected by the
    # strict exitcode sidecar parsing and the re-executed admission decision,
    # while any real byte tamper is rejected here.
    if not isinstance(artifact_manifest, dict):
        raise RuntimeError("artifact manifest is invalid")
    for relative, metadata in artifact_manifest.items():
        if _artifact(run_dir / relative, root=run_dir) != metadata:
            raise RuntimeError(f"artifact digest mismatch: {relative}")
    if report.get("artifacts") != artifact_manifest:
        raise RuntimeError("evidence artifact index mismatch")
    # Fix-5 (re-derive unit summary from the precisely-bound unit stdout):
    # read the EXACT ``commands/lite-unit-suite.stdout`` bytes (not the
    # receipt's recorded stdout string, which could be tampered), call the
    # formal ``_parse_test_summary()`` and compare the re-derived summary with
    # BOTH the top-level ``lite_unit_summary`` and
    # ``measurements["lite_unit_summary"]`` field-by-field with strict type +
    # value equality.  A missing/extra field, a boolean-as-int, a
    # passed/failed/skipped/deselected count that disagrees with the sealed
    # stdout, or a top-level-vs-measurement drift is rejected.
    unit_stdout_path = run_dir / _expected_sidecar_literal("lite-unit-suite", "stdout")
    if not unit_stdout_path.is_file() or unit_stdout_path.is_symlink():
        raise RuntimeError("lite-unit-suite stdout sidecar is missing")
    derived_unit_summary = _parse_test_summary(
        unit_stdout_path.read_text(encoding="utf-8", errors="strict")
    )
    _assert_unit_summary_matches(
        derived_unit_summary, report.get("lite_unit_summary"), where="lite_unit_summary"
    )
    measurements = report.get("measurements")
    if not isinstance(measurements, dict):
        raise RuntimeError("measurements are missing")
    _assert_unit_summary_matches(
        derived_unit_summary,
        measurements.get("lite_unit_summary"),
        where="measurements.lite_unit_summary",
    )
    # Re-derive every claim from the sealed receipts; a tampered or fabricated
    # report cannot match the executed probe bytes and command vectors.
    # Fix-3/Fix-6: the probe is re-parsed from the precisely-bound
    # ``commands/lite-gate-probes.stdout``; ``formal_builder_integration`` and
    # ``formal_builder_posture_not_integrated`` stay two independent claims
    # (proven_engineering_only recorded verbatim; not_integrated rewritten to
    # not_proven as defence-in-depth and rejected).
    probe_stdout_path = run_dir / _expected_sidecar_literal(
        "lite-gate-probes", "stdout"
    )
    if not probe_stdout_path.is_file() or probe_stdout_path.is_symlink():
        raise RuntimeError("probe command receipt is missing")
    probe = _parse_probe(probe_stdout_path.read_text(encoding="utf-8"))
    expected_claims = _derive_claims(probe, commands)
    for key, expected in expected_claims.items():
        if report.get(key) != expected:
            raise RuntimeError(f"claim {key} does not match the sealed receipts")
    # Fix-3: --verify-evidence re-executes the SAME closed-set admission
    # decision as --run.  It is not enough that the report equals the derived
    # values: derived values that miss an admission expectation (e.g.
    # true_on=false, invalid_fail_closed=false, live_posture=false, mode
    # drift, command-vector drift, a touched database marker) must reject the
    # evidence instead of verifying it.
    admission_mismatch = _admission_mismatch(
        expected_claims,
        production_runtime_activated=report.get("production_runtime_activated"),
    )
    if admission_mismatch is not None:
        raise RuntimeError(f"admission expectation mismatch: {admission_mismatch}")
    # The sealed evidence is a self-contained integrity receipt only; it never
    # claims external authenticity and never claims production admission.
    integrity = report.get("integrity_receipt")
    if not isinstance(integrity, dict):
        raise RuntimeError("integrity receipt is missing")
    if integrity.get("scope") != "run-scoped byte integrity only":
        raise RuntimeError("integrity receipt scope wording mismatch")
    if integrity.get("external_authenticity") is not False:
        raise RuntimeError("integrity receipt must not claim external authenticity")
    if integrity.get("trust_anchor") is not None:
        raise RuntimeError(
            "integrity receipt must not name an independent trust anchor"
        )
    # The migration head is re-measured from the repository files.
    if _discover_migration_head() != EXPECTED_MIGRATION_HEAD:
        raise RuntimeError("migration head re-measurement mismatch")


def _parse_test_summary(stdout: str) -> dict[str, object]:
    """Parse the focused Lite/formal suite pytest summary into a closed dict.

    Captures ``passed``, ``failed``, ``skipped`` and ``deselected`` counts from
    the pytest summary line, each as a strict ``int`` (so a boolean-as-int
    cannot sneak through), defaulting absent counters to ``0``.  The Gate
    writes the same dict into both ``lite_unit_summary`` and
    ``measurements["lite_unit_summary"]`` during ``--run``; ``--verify-evidence``
    re-derives it from the precisely-bound ``commands/lite-unit-suite.stdout``
    bytes and compares every field with strict type+value equality, so any
    drift (a missing/extra field, a boolean-as-int, or a
    passed/failed/skipped/deselected count that disagrees with the actual
    sealed stdout) rejects the evidence.
    """
    summary: dict[str, object] = {}
    for field in ("passed", "failed", "skipped", "deselected"):
        match = re.search(rf"\b(\d+)\s+{field}\b", stdout)
        if match is not None:
            summary[field] = int(match.group(1))
    if "passed" not in summary:
        raise RuntimeError("P5.4C unit suite summary not found")
    for field in ("failed", "skipped", "deselected"):
        summary.setdefault(field, 0)
    return summary


def _assert_unit_summary_matches(
    derived: dict[str, object], stored: object, *, where: str
) -> None:
    """Field-by-field strict comparison of a re-derived unit summary.

    ``derived`` is re-parsed from the precisely-bound
    ``commands/lite-unit-suite.stdout`` bytes; ``stored`` is the value found in
    the report (either the top-level ``lite_unit_summary`` or
    ``measurements["lite_unit_summary"]``).  The comparison rejects a missing
    summary, a non-dict summary, any missing/extra field, any non-integer
    value (using ``type(value) is int`` so a JSON ``false``/``true`` boolean
    cannot masquerade as ``0``/``1``), and any passed/failed/skipped/deselected
    count that disagrees with the sealed stdout.
    """
    if not isinstance(stored, dict):
        raise RuntimeError(f"{where} is not a JSON object")
    if set(derived.keys()) != set(stored.keys()):
        raise RuntimeError(
            f"{where} fields differ from the re-derived summary: "
            f"missing={sorted(set(derived.keys()) - set(stored.keys()))} "
            f"extra={sorted(set(stored.keys()) - set(derived.keys()))}"
        )
    for field in ("passed", "failed", "skipped", "deselected"):
        stored_value = stored.get(field)
        derived_value = derived.get(field)
        if type(stored_value) is not int:
            raise RuntimeError(f"{where}.{field} is not a strict integer")
        if type(derived_value) is not int:
            raise RuntimeError(f"re-derived {where}.{field} is not a strict integer")
        if stored_value != derived_value:
            raise RuntimeError(
                f"{where}.{field} drift: stored={stored_value} re-derived={derived_value}"
            )


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
    claims: dict[str, object],
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
        "evidence_preserved": True,
        "lite_unit_summary": measurements.get("lite_unit_summary"),
        "probe_measurements": measurements.get("probe_measurements"),
        "root_env_accessed": claims["root_env_accessed"],
        "business_database_accessed": claims["business_database_accessed"],
        "business_database_migrated": claims["business_database_migrated"],
        "lite_gate_default_off": claims["lite_gate_default_off"],
        "runtime_env_resolver_absent_off": claims["runtime_env_resolver_absent_off"],
        "runtime_env_resolver_false_off": claims["runtime_env_resolver_false_off"],
        "runtime_env_resolver_true_on": claims["runtime_env_resolver_true_on"],
        "runtime_env_resolver_invalid_fail_closed": claims[
            "runtime_env_resolver_invalid_fail_closed"
        ],
        "live_posture_reflects_env": claims["live_posture_reflects_env"],
        "knowledge_search_read_only_not_supported": claims[
            "knowledge_search_read_only_not_supported"
        ],
        "formal_builder_named": claims["formal_builder_named"],
        "formal_builder_integration": claims["formal_builder_integration"],
        "formal_builder_posture_not_integrated": claims[
            "formal_builder_posture_not_integrated"
        ],
        "engineering_composition_ready": claims["engineering_composition_ready"],
        "activation_allowed": claims["activation_allowed"],
        "integrity_receipt": {
            "scope": "run-scoped byte integrity only",
            "external_authenticity": False,
            "trust_anchor": None,
            "wording": (
                "This sealed evidence proves run-scoped byte integrity of the "
                "recorded source manifest, command receipts and measurements. "
                "Without an independent trust anchor it proves no external "
                "authenticity: it cannot authenticate who produced the bytes, "
                "and it is never production admission."
            ),
        },
        "admission_expectations_checked": True,
        "claim_sources": {
            "lite_gate_default_off": "probe receipt (absent -> off)",
            "runtime_env_resolver_absent_off": "probe receipt",
            "runtime_env_resolver_false_off": "probe receipt",
            "runtime_env_resolver_true_on": "probe receipt",
            "runtime_env_resolver_invalid_fail_closed": "probe receipt",
            "live_posture_reflects_env": "probe receipt",
            "knowledge_search_read_only_not_supported": "probe receipt (modes == no_tool)",
            "formal_builder_named": "probe receipt (disclosure only)",
            "formal_builder_integration": "probe receipt, recorded honestly (proven_engineering_only when the formal builder is integrated; not_proven only as defence-in-depth when a tampered probe reports not_integrated)",
            "formal_builder_posture_not_integrated": "probe receipt (False when the posture genuinely reports proven_engineering_only)",
            "engineering_composition_ready": "probe receipt (the formal P5.4B composition is proven ready, engineering-only)",
            "activation_allowed": "probe receipt (production activation is never allowed by the engineering proof)",
            "root_env_accessed": "derived from recorded command vectors",
            "business_database_accessed": "derived from recorded command vectors",
            "business_database_migrated": "derived from recorded command vectors",
            "migration_head": "discovered from migration directory + typed executor config",
            "lite_unit_summary": "parsed from the precisely-bound commands/lite-unit-suite.stdout; re-derived and field-compared against lite_unit_summary and measurements.lite_unit_summary by --verify-evidence",
            "production_runtime_activated": "derived from recorded command vectors",
            "evidence_preserved": "run directory is retained for --verify-evidence",
            "integrity_receipt": "self-contained run-scoped byte-integrity receipt; no external authenticity and no trust anchor",
            "admission_expectations_checked": "closed-set admission decision re-executed by --verify-evidence",
        },
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
            "- Evidence preserved: `true` (reverify with `--verify-evidence`)",
            f"- Lite gate default-off: `{report['lite_gate_default_off']}` (probe receipt)",
            (
                "- Knowledge-search read-only mode not supported: "
                f"`{report['knowledge_search_read_only_not_supported']}` (probe receipt)"
            ),
            (
                "- Formal builder disclosed: "
                f"`{report['formal_builder_named']}`; integration claim: "
                f"`{report['formal_builder_integration']}` (proven_engineering_only "
                "when the formal builder is formally connected); posture "
                f"not-integrated: `{report['formal_builder_posture_not_integrated']}`; "
                f"engineering_composition_ready: `{report['engineering_composition_ready']}`; "
                f"activation_allowed: `{report['activation_allowed']}`"
            ),
            (
                "- Root .env / business database access (receipt-derived): "
                f"`{report['root_env_accessed']}` / `{report['business_database_accessed']}` "
                f"`{report['business_database_migrated']}`"
            ),
            f"- Lite unit summary: `{json.dumps(report.get('lite_unit_summary'), sort_keys=True)}`",
            f"- Cleanup: `{json.dumps(cleanup, sort_keys=True)}`",
            (
                "- Integrity scope: self-contained run-scoped byte-integrity "
                "receipt only; no external authenticity, no independent trust "
                "anchor, never production admission."
            ),
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
    artifact_raw_sha = _write_json(
        run_dir / "artifact-manifest.json", artifact_manifest
    )
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
            _container_command("python", "-m", "pytest", *LITE_UNIT_TESTS, "-q"),
        )
        commands.append(lite_test)
        stdout = (run_dir / str(lite_test["stdout"])).read_text(encoding="utf-8")
        measurements["lite_unit_summary"] = _parse_test_summary(stdout)

        probe_step = _run_step(
            run_dir,
            "lite-gate-probes",
            _container_command("python", "-c", _PROBE_SOURCE),
        )
        commands.append(probe_step)
        probe_stdout = (run_dir / str(probe_step["stdout"])).read_text(encoding="utf-8")
        probe = _parse_probe(probe_stdout)
        measurements["probe_measurements"] = probe
        measurements["migration_head"] = _discover_migration_head()
        steps_passed = True
    except Exception as exc:
        errors.append(str(exc))
    cleanup = {"files_removed": 0, "evidence_preserved": True}
    measured_probe: dict[str, object] | None = None
    if steps_passed:
        probe_value = measurements.get("probe_measurements")
        if isinstance(probe_value, dict):
            measured_probe = probe_value
        else:
            errors.append(
                "probe measurements are missing after a successful probe step"
            )
            steps_passed = False
    claims: dict[str, object] = {}
    if steps_passed:
        assert isinstance(measured_probe, dict)
        claims = _derive_claims(measured_probe, commands)
        # Fix-2: the Gate only PASSES when every admission boolean meets its
        # expectation (default_off/absent_off/false_off/true_on/
        # invalid_fail_closed/live_posture_reflects_env/no_tool-only/
        # formal_builder_named all true; root_env/business-database/
        # production_runtime negatives all false; formal_builder_integration
        # stays not_proven).  A single mismatch -> passed=false.
        admission_mismatch = _admission_mismatch(
            claims, production_runtime_activated=False
        )
        if admission_mismatch is not None:
            errors.append(f"admission expectation mismatch: {admission_mismatch}")
            steps_passed = False
    else:
        claims = {
            "lite_gate_default_off": False,
            "runtime_env_resolver_absent_off": False,
            "runtime_env_resolver_false_off": False,
            "runtime_env_resolver_true_on": False,
            "runtime_env_resolver_invalid_fail_closed": False,
            "live_posture_reflects_env": False,
            "knowledge_search_read_only_not_supported": False,
            "formal_builder_named": False,
            "formal_builder_integration": "not_proven",
            "formal_builder_posture_not_integrated": False,
            "engineering_composition_ready": False,
            "activation_allowed": False,
            "root_env_accessed": _receipt_root_env_accessed(commands),
            "business_database_accessed": _receipt_business_database_accessed(commands),
            "business_database_migrated": _receipt_business_database_migrated(commands),
        }
    passed = (
        steps_passed
        and not errors
        and tuple(item.get("key") for item in commands)
        == ("lite-unit-suite", "lite-gate-probes")
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
        claims=claims,
        cleanup=cleanup,
        error=" | ".join(errors) if errors else None,
    )
    # The run directory is preserved on success AND on failure so the sealed
    # evidence can be independently re-verified after the process exits.
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["passed"]:
        print(f"P5.4C disposable Gate passed; evidence preserved at: {run_dir}")
        return 0
    print(
        f"P5.4C disposable Gate failed; evidence preserved at: {run_dir}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
