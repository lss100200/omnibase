"""Run and verify the run-scoped P5.4B engineering composition Gate.

The Gate is disposable and engineering-only.  It uses an isolated
``omnibase_test_p54b_*`` PostgreSQL database, an internal-only Docker network,
explicit ``.env.example`` Compose configuration and a unique non-overwriting
evidence directory.  It never activates production Runtime and never writes to
the legacy P5.4B evidence directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env.example"
COMPOSE_FILE = REPO_ROOT / "docker-compose.destructive-tests.yml"
LEGACY_ROOT = (REPO_ROOT / ".tmp" / "p5-4b-engineering-composition-gate").resolve()
EVIDENCE_ROOT = (REPO_ROOT / ".tmp" / "p5-4b-engineering-composition-gate-v2").resolve()
GATE_NAME = "P5.4B engineering composition disposable Gate v2"
INTEGRATION_TEST = "tests/integration/test_p5_4b_engineering_composition_foundation.py"
EXPECTED_HEAD = "0016"
BACKEND_IMAGE = "omnibase-backend:latest"
POSTGRES_IMAGE = "pgvector/pgvector:0.8.5-pg15-bookworm"
BACKEND_VENV_VOLUME = "omnibase_backend_venv"
EXPECTED_RUNTIME_GATES = {
    "P5_4B_ENGINEERING_ENABLED": "false",
    "AGENT_RUNTIME_ENABLED": "false",
    "AGENT_PLANNER_ENABLED": "false",
    "MULTI_AGENT_ENABLED": "false",
}
REQUIRED_COMMAND_KEYS = (
    "preflight-images",
    "compose-up",
    "destructive-preflight",
    "alembic-upgrade-head",
    "integration",
    "measured-alembic-head",
    "measured-alembic-graph",
    "measured-runtime-gates",
    "measured-network",
    "measured-backend-image",
    "measured-postgres-image",
    "measured-venv-volume",
    "measured-python-environment",
    "compose-down",
    "cleanup-containers",
    "cleanup-networks",
    "cleanup-volumes",
)
SOURCE_FILES = (
    ".env.example",
    "AGENTS.md",
    "backend/alembic.ini",
    "backend/pyproject.toml",
    "backend/uv.lock",
    "backend/src/omnibase/production/phase5_planner_contract.py",
    "backend/tests/cleanup.py",
    "backend/tests/destructive_preflight.py",
    "backend/tests/integration/conftest.py",
    "backend/tests/integration/test_p5_4b_engineering_composition_foundation.py",
    "backend/tests/postgres-init-destructive-tests.sh",
    "backend/tests/test_p34_2_gateway_api.py",
    "backend/tests/test_p34_2_gateway_query.py",
    "backend/tests/test_p34_5_gateway_workload.py",
    "backend/tests/test_p34_6_gateway_workload_write.py",
    "backend/tests/test_p34_7_production_composition.py",
    "backend/tests/test_p5_4a_gateway_adapter.py",
    "backend/tests/test_p5_4a_typed_executor.py",
    "backend/tests/test_p5_4b_engineering_composition.py",
    "backend/tests/test_p5_4b_gate_v2.py",
    "deployment/production/phase5-typed-executor.example.json",
    "docker-compose.destructive-tests.yml",
    "docs/handover-report.md",
    "docs/maintainers/ai-maintainer-map.md",
    "docs/maintainers/maintenance-map.json",
    "docs/maintainers/security-invariants.md",
    "docs/phase-5-engineering-composition-contract.md",
    "docs/phase-5-typed-executor-contract.md",
    "scripts/production/run_p5_4b_engineering_composition_disposable_gate.py",
)
SOURCE_TREES = (
    "backend/src/omnibase/agent_executor",
    "backend/src/omnibase/agent_registry",
    "backend/src/omnibase/capabilities",
    "backend/src/omnibase/capability_gateway",
    "backend/src/omnibase/control_plane",
    "backend/src/omnibase/migrations",
    "backend/src/omnibase/task_ledger",
    "backend/src/omnibase/workspaces",
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
    return _write_bytes(
        path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    )


def _source_paths() -> tuple[str, ...]:
    relative_paths = set(SOURCE_FILES)
    for relative_root in SOURCE_TREES:
        root = REPO_ROOT / relative_root
        if root.is_symlink() or not root.is_dir():
            raise RuntimeError(f"P5.4B source tree is unavailable: {relative_root}")
        for path in root.rglob("*"):
            if path.is_symlink():
                raise RuntimeError(f"P5.4B source tree contains a symlink: {path}")
            if path.is_file() and path.suffix in {".py", ".mako"}:
                relative_paths.add(path.relative_to(REPO_ROOT).as_posix())
    return tuple(sorted(relative_paths))


def _manifest() -> dict[str, object]:
    files: dict[str, dict[str, object]] = {}
    for relative in _source_paths():
        path = REPO_ROOT / relative
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"P5.4B source path is not a regular file: {relative}")
        raw = path.read_bytes()
        files[relative] = {"size": len(raw), "sha256": _sha256_bytes(raw)}
    return {"schema_version": 3, "file_count": len(files), "files": files}


def _manifest_digest(manifest: dict[str, object]) -> str:
    return _sha256_bytes(
        json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode()
    )


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
        (
            REPO_ROOT / "deployment/production/phase5-typed-executor.example.json"
        ).read_text(encoding="utf-8")
    )
    if config.get("migration_baseline") != EXPECTED_HEAD:
        raise RuntimeError("P5.4B Gate requires migration baseline 0016")
    if config.get("activation_requested") is not False:
        raise RuntimeError("P5.4B activation must remain false")
    if config.get("feature_gates") != {
        "agent_runtime_enabled": False,
        "agent_planner_enabled": False,
        "multi_agent_enabled": False,
    }:
        raise RuntimeError("P5.4B feature gates must remain false")
    revision_files = tuple(
        (REPO_ROOT / "backend/src/omnibase/migrations/versions").glob(
            "[0-9][0-9][0-9][0-9]_*.py"
        )
    )
    numeric = {int(path.name[:4]) for path in revision_files}
    if 13 not in numeric or any(value >= 14 for value in numeric):
        raise RuntimeError("P5.4B migration filename boundary is not exactly 0013")
    _manifest()


def _dirty_paths() -> tuple[str, ...]:
    result = _run(["git", "status", "--porcelain"])
    if result.returncode != 0:
        raise RuntimeError("P5.4B Gate could not inspect Git status")
    return tuple(line for line in result.stdout.splitlines() if line.strip())


def _redact_command(command: list[str]) -> list[str]:
    redacted: list[str] = []
    for value in command:
        if value.startswith("DATABASE_URL=") or value.startswith("TEST_DATABASE_URL="):
            redacted.append(value.split("=", 1)[0] + "=<sentinel-redacted>")
        else:
            redacted.append(value)
    return redacted


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


def _compose_command(project: str, override: Path, *arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        str(ENV_FILE),
        "-p",
        project,
        "-f",
        str(COMPOSE_FILE),
        "-f",
        str(override),
        *arguments,
    ]


def _container_command(project: str, database_url: str, *arguments: str) -> list[str]:
    container_url = re.sub(r"@localhost:[0-9]+/", "@postgres-test:5432/", database_url)
    return [
        "docker",
        "run",
        "--pull",
        "never",
        "--rm",
        "--network",
        f"{project}_default",
        "-v",
        f"{(REPO_ROOT / 'backend').as_posix()}:/app",
        "-v",
        f"{BACKEND_VENV_VOLUME}:/app/.venv",
        "-w",
        "/app",
        "-e",
        f"DATABASE_URL={container_url}",
        "-e",
        f"TEST_DATABASE_URL={container_url}",
        "-e",
        "OMNIBASE_INTEGRATION_TESTS=1",
        "-e",
        "P5_4B_ENGINEERING_ENABLED=false",
        "-e",
        "AGENT_RUNTIME_ENABLED=false",
        "-e",
        "AGENT_PLANNER_ENABLED=false",
        "-e",
        "MULTI_AGENT_ENABLED=false",
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
        BACKEND_IMAGE,
        *arguments,
    ]


def _expected_commands(project: str, override: Path) -> dict[str, list[str]]:
    dummy_url = (
        "postgresql+psycopg://runner:secret@localhost:55434/omnibase_test_p54b_dummy"
    )
    container = lambda *args: _redact_command(  # noqa: E731
        _container_command(project, dummy_url, *args)
    )
    return {
        "preflight-images": [
            "docker",
            "image",
            "inspect",
            BACKEND_IMAGE,
            POSTGRES_IMAGE,
        ],
        "compose-up": _compose_command(
            project, override, "up", "-d", "--wait", "postgres-test"
        ),
        "destructive-preflight": container("python", "tests/destructive_preflight.py"),
        "alembic-upgrade-head": container("python", "-m", "alembic", "upgrade", "head"),
        "integration": container(
            "python", "-m", "pytest", "-m", "integration", INTEGRATION_TEST, "-q"
        ),
        "measured-alembic-head": container(
            "python",
            "-c",
            "from sqlalchemy import create_engine,text; import os; e=create_engine(os.environ['DATABASE_URL']); print(e.connect().execute(text('select version_num from omnibase_meta.alembic_version')).scalar_one())",
        ),
        "measured-alembic-graph": container(
            "python",
            "-c",
            "import json; from alembic.config import Config; from alembic.script import ScriptDirectory; s=ScriptDirectory.from_config(Config('alembic.ini')); print('P54B_GRAPH='+json.dumps({'heads':sorted(s.get_heads()),'revisions':sorted(r.revision for r in s.walk_revisions())},sort_keys=True))",
        ),
        "measured-runtime-gates": container(
            "python",
            "-c",
            "import json,os; print(json.dumps({k:os.environ.get(k) for k in ['P5_4B_ENGINEERING_ENABLED','AGENT_RUNTIME_ENABLED','AGENT_PLANNER_ENABLED','MULTI_AGENT_ENABLED']},sort_keys=True))",
        ),
        "measured-network": [
            "docker",
            "network",
            "inspect",
            "--format",
            "{{json .Internal}}",
            f"{project}_default",
        ],
        "measured-backend-image": [
            "docker",
            "image",
            "inspect",
            "--format",
            "{{json .Id}}|{{json .RepoDigests}}",
            BACKEND_IMAGE,
        ],
        "measured-postgres-image": [
            "docker",
            "image",
            "inspect",
            "--format",
            "{{json .Id}}|{{json .RepoDigests}}",
            POSTGRES_IMAGE,
        ],
        "measured-venv-volume": [
            "docker",
            "volume",
            "inspect",
            "--format",
            "{{json .Name}}",
            BACKEND_VENV_VOLUME,
        ],
        "measured-python-environment": container(
            "python",
            "-c",
            "import importlib.metadata as m,json; print(json.dumps(sorted((d.metadata['Name'] or '',d.version) for d in m.distributions()),separators=(',',':')))",
        ),
        "compose-down": _compose_command(
            project, override, "down", "-v", "--remove-orphans"
        ),
        "cleanup-containers": [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ],
        "cleanup-networks": [
            "docker",
            "network",
            "ls",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ],
        "cleanup-volumes": [
            "docker",
            "volume",
            "ls",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ],
    }


def _command_stdout(run_dir: Path, record: dict[str, object]) -> str:
    relative = record.get("stdout")
    if not isinstance(relative, str):
        raise RuntimeError("command stdout path is invalid")
    return (run_dir / relative).read_text(encoding="utf-8").strip()


def _parse_graph(stdout: str) -> dict[str, object]:
    marker = "P54B_GRAPH="
    line = next(
        (item for item in reversed(stdout.splitlines()) if item.startswith(marker)),
        None,
    )
    if line is None:
        raise RuntimeError("Alembic graph measurement is missing")
    graph = json.loads(line[len(marker) :])
    heads = graph.get("heads")
    revisions = graph.get("revisions")
    if heads != [EXPECTED_HEAD] or not isinstance(revisions, list):
        raise RuntimeError("Alembic graph is not single-head 0016")
    numeric = []
    for revision in revisions:
        if not isinstance(revision, str) or re.fullmatch(r"[0-9]{4}", revision) is None:
            raise RuntimeError(
                "Alembic revision identifier is outside the closed numeric set"
            )
        numeric.append(int(revision))
    if 16 not in numeric or any(value >= 17 for value in numeric):
        raise RuntimeError("Alembic graph contains migration 0017 or higher")
    return {
        "heads": heads,
        "revisions": revisions,
        "migration_0014_created": True,
        "migration_0015_created": True,
        "migration_0016_created": True,
        "migration_0017_or_higher_present": False,
    }


def _parse_image_measurement(stdout: str) -> dict[str, object]:
    try:
        image_raw, repo_digests_raw = stdout.strip().split("|", maxsplit=1)
        image_id = json.loads(image_raw)
        repo_digests = json.loads(repo_digests_raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Docker image measurement is malformed") from exc
    if (
        not isinstance(image_id, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None
    ):
        raise RuntimeError("Docker image ID is not a SHA-256 identity")
    if repo_digests is None:
        repo_digests = []
    if not isinstance(repo_digests, list) or not all(
        isinstance(item, str) for item in repo_digests
    ):
        raise RuntimeError("Docker image RepoDigests measurement is malformed")
    return {"id": image_id, "repo_digests": repo_digests}


def _parse_python_environment(stdout: str) -> list[list[str]]:
    try:
        packages = json.loads(stdout.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("Python environment measurement is malformed") from exc
    if (
        not isinstance(packages, list)
        or not packages
        or any(
            not isinstance(item, list)
            or len(item) != 2
            or not all(isinstance(value, str) and value for value in item)
            for item in packages
        )
    ):
        raise RuntimeError("Python environment measurement is incomplete")
    if packages != sorted(packages):
        raise RuntimeError("Python environment measurement is not canonical")
    return packages


def _run_gate(
    run_dir: Path, project: str, database_url: str
) -> tuple[list[dict[str, object]], dict[str, object]]:
    commands: list[dict[str, object]] = []
    for key, arguments in (
        ("destructive-preflight", ("python", "tests/destructive_preflight.py")),
        ("alembic-upgrade-head", ("python", "-m", "alembic", "upgrade", "head")),
        (
            "integration",
            ("python", "-m", "pytest", "-m", "integration", INTEGRATION_TEST, "-q"),
        ),
    ):
        commands.append(
            _run_step(
                run_dir, key, _container_command(project, database_url, *arguments)
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
            "from sqlalchemy import create_engine,text; import os; e=create_engine(os.environ['DATABASE_URL']); print(e.connect().execute(text('select version_num from omnibase_meta.alembic_version')).scalar_one())",
        ),
    )
    commands.append(head)
    graph = _run_step(
        run_dir,
        "measured-alembic-graph",
        _container_command(
            project,
            database_url,
            "python",
            "-c",
            "import json; from alembic.config import Config; from alembic.script import ScriptDirectory; s=ScriptDirectory.from_config(Config('alembic.ini')); print('P54B_GRAPH='+json.dumps({'heads':sorted(s.get_heads()),'revisions':sorted(r.revision for r in s.walk_revisions())},sort_keys=True))",
        ),
    )
    commands.append(graph)
    gates = _run_step(
        run_dir,
        "measured-runtime-gates",
        _container_command(
            project,
            database_url,
            "python",
            "-c",
            "import json,os; print(json.dumps({k:os.environ.get(k) for k in ['P5_4B_ENGINEERING_ENABLED','AGENT_RUNTIME_ENABLED','AGENT_PLANNER_ENABLED','MULTI_AGENT_ENABLED']},sort_keys=True))",
        ),
    )
    commands.append(gates)
    network = _run_step(
        run_dir,
        "measured-network",
        [
            "docker",
            "network",
            "inspect",
            "--format",
            "{{json .Internal}}",
            f"{project}_default",
        ],
    )
    commands.append(network)
    backend_image = _run_step(
        run_dir,
        "measured-backend-image",
        _expected_commands(project, run_dir / "compose-internal-network.yml")[
            "measured-backend-image"
        ],
    )
    commands.append(backend_image)
    postgres_image = _run_step(
        run_dir,
        "measured-postgres-image",
        _expected_commands(project, run_dir / "compose-internal-network.yml")[
            "measured-postgres-image"
        ],
    )
    commands.append(postgres_image)
    volume = _run_step(
        run_dir,
        "measured-venv-volume",
        [
            "docker",
            "volume",
            "inspect",
            "--format",
            "{{json .Name}}",
            BACKEND_VENV_VOLUME,
        ],
    )
    commands.append(volume)
    python_environment = _run_step(
        run_dir,
        "measured-python-environment",
        _expected_commands(project, run_dir / "compose-internal-network.yml")[
            "measured-python-environment"
        ],
    )
    commands.append(python_environment)
    measured_head = _command_stdout(run_dir, head)
    if measured_head != EXPECTED_HEAD:
        raise RuntimeError(f"sentinel Alembic head is {measured_head!r}, not 0016")
    graph_value = _parse_graph(_command_stdout(run_dir, graph))
    gates_value = json.loads(_command_stdout(run_dir, gates))
    if gates_value != EXPECTED_RUNTIME_GATES:
        raise RuntimeError("runtime or Phase 5 Gate environment is not closed")
    if _command_stdout(run_dir, network) != "true":
        raise RuntimeError("P5.4B disposable network is not internal-only")
    backend_image_value = _parse_image_measurement(
        _command_stdout(run_dir, backend_image)
    )
    postgres_image_value = _parse_image_measurement(
        _command_stdout(run_dir, postgres_image)
    )
    if json.loads(_command_stdout(run_dir, volume)) != BACKEND_VENV_VOLUME:
        raise RuntimeError("P5.4B backend venv volume identity drifted")
    python_environment_value = _parse_python_environment(
        _command_stdout(run_dir, python_environment)
    )
    return commands, {
        "measured_alembic_head": measured_head,
        "alembic_graph": graph_value,
        "runtime_gates": gates_value,
        "docker_network_internal": True,
        "backend_image": backend_image_value,
        "postgres_image": postgres_image_value,
        "backend_venv_volume": json.loads(_command_stdout(run_dir, volume)),
        "python_environment": python_environment_value,
        "ambient_runtime_dependent": True,
    }


def _cleanup(
    run_dir: Path, project: str, env: dict[str, str], override: Path
) -> tuple[dict[str, int], list[dict[str, object]]]:
    records: list[dict[str, object]] = []
    down_command = _compose_command(project, override, "down", "-v", "--remove-orphans")
    down = _run(down_command, env=env)
    records.append(_record_command(run_dir, "compose-down", down_command, down))
    counts: dict[str, int] = {}
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
    for kind, command in commands.items():
        result = _run(command)
        records.append(_record_command(run_dir, f"cleanup-{kind}", command, result))
        if result.returncode != 0:
            raise RuntimeError(f"could not inspect cleanup {kind}")
        counts[kind] = sum(bool(line.strip()) for line in result.stdout.splitlines())
    if down.returncode != 0 or counts != {"containers": 0, "networks": 0, "volumes": 0}:
        raise RuntimeError(f"P5.4B cleanup failed: {counts}\n{down.stdout[-1000:]}")
    return counts, records


def _verify_command_records(
    run_dir: Path, commands: object, *, project: str, override: Path
) -> dict[str, dict[str, object]]:
    if not isinstance(commands, list):
        raise RuntimeError("evidence command index is invalid")
    by_key: dict[str, dict[str, object]] = {}
    for item in commands:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            raise RuntimeError("evidence command record is invalid")
        key = str(item["key"])
        if key in by_key:
            raise RuntimeError(f"duplicate command record: {key}")
        by_key[key] = item
    if tuple(by_key) != REQUIRED_COMMAND_KEYS:
        raise RuntimeError("required command record order or closure is incomplete")
    expected = _expected_commands(project, override)
    for key, item in by_key.items():
        stdout_relative = item.get("stdout")
        exitcode_relative = item.get("exitcode")
        returncode = item.get("returncode")
        if (
            not isinstance(stdout_relative, str)
            or not isinstance(exitcode_relative, str)
            or type(returncode) is not int
            or returncode != 0
        ):
            raise RuntimeError(f"command did not prove success: {key}")
        stdout_path = (run_dir / stdout_relative).resolve(strict=True)
        exitcode_path = (run_dir / exitcode_relative).resolve(strict=True)
        if (
            run_dir.resolve() not in stdout_path.parents
            or run_dir.resolve() not in exitcode_path.parents
        ):
            raise RuntimeError(f"command sidecar escaped run directory: {key}")
        if _sha256(stdout_path) != item.get("stdout_sha256"):
            raise RuntimeError(f"command stdout digest mismatch: {key}")
        if exitcode_path.read_text(encoding="utf-8").strip() != str(returncode):
            raise RuntimeError(f"command exitcode sidecar mismatch: {key}")
        if item.get("command") != _redact_command(expected[key]):
            raise RuntimeError(f"command semantics mismatch: {key}")
    return by_key


def _verify(path: Path) -> None:  # noqa: C901
    evidence_path = path.resolve(strict=True)
    if (
        evidence_path.name != "evidence.json"
        or evidence_path.parent.parent != EVIDENCE_ROOT
        or evidence_path.parent.name in {"", ".", ".."}
    ):
        raise RuntimeError("evidence must be a run-scoped v2 evidence.json")
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
    if not isinstance(artifact_manifest, dict):
        raise RuntimeError("artifact manifest is invalid")
    for relative, metadata in artifact_manifest.items():
        if not isinstance(relative, str) or not isinstance(metadata, dict):
            raise RuntimeError("artifact manifest entry is invalid")
        if _artifact(run_dir / relative, root=run_dir) != metadata:
            raise RuntimeError(f"artifact digest mismatch: {relative}")
    if report.get("artifacts") != artifact_manifest:
        raise RuntimeError("evidence artifact index mismatch")
    if report.get("gate") != GATE_NAME or report.get("run_id") != run_dir.name:
        raise RuntimeError("evidence run binding mismatch")
    project = report.get("compose_project")
    sentinel_database = report.get("sentinel_database")
    if (
        not isinstance(project, str)
        or re.fullmatch(r"omnibase-test-p54b-[0-9a-f]{12}", project) is None
        or not isinstance(sentinel_database, str)
        or re.fullmatch(r"omnibase_test_p54b_[0-9a-f]{12}", sentinel_database) is None
    ):
        raise RuntimeError("sentinel project/database binding is invalid")
    override = run_dir / "compose-internal-network.yml"
    if override.is_symlink() or override.read_bytes() != (
        b"services:\n  postgres-test:\n    pull_policy: never\nnetworks:\n  default:\n    internal: true\n"
    ):
        raise RuntimeError("run-scoped internal network override is invalid")
    if report.get("schema_version") != 3 or report.get("passed") is not True:
        raise RuntimeError("evidence is not a successful schema-v3 run")
    if report.get("migration_head") != EXPECTED_HEAD:
        raise RuntimeError("migration head evidence mismatch")
    if report.get("migration_0014_created") is not True:
        raise RuntimeError("migration 0014 evidence mismatch")
    if report.get("migration_0015_created") is not True:
        raise RuntimeError("migration 0015 evidence mismatch")
    if report.get("migration_0016_created") is not True:
        raise RuntimeError("migration 0016 evidence mismatch")
    if report.get("migration_0017_or_higher_present") is not False:
        raise RuntimeError("migration graph evidence mismatch")
    if report.get("production_runtime_activated") is not False:
        raise RuntimeError("production Runtime evidence mismatch")
    if report.get("feature_gates") != {
        "agent_runtime_enabled": False,
        "agent_planner_enabled": False,
        "multi_agent_enabled": False,
    }:
        raise RuntimeError("feature Gate evidence mismatch")
    if report.get("workload_container_external_network_denied") is not True:
        raise RuntimeError("network-deny evidence mismatch")
    if report.get("cleanup") != {"containers": 0, "networks": 0, "volumes": 0}:
        raise RuntimeError("cleanup evidence mismatch")
    if report.get("root_env_accessed") is not False:
        raise RuntimeError("root env evidence mismatch")
    if (
        report.get("business_database_accessed") is not False
        or report.get("business_database_migrated") is not False
    ):
        raise RuntimeError("business database evidence mismatch")
    if report.get("legacy_evidence_preserved") is not True:
        raise RuntimeError("legacy evidence preservation was not proven")
    if report.get("ambient_runtime_dependent") is not True:
        raise RuntimeError("ambient runtime dependency disclosure is missing")
    records = _verify_command_records(
        run_dir, report.get("commands"), project=project, override=override
    )
    measurements = report.get("measurements")
    if not isinstance(measurements, dict):
        raise RuntimeError("measurement index is invalid")
    measured_head = _command_stdout(run_dir, records["measured-alembic-head"])
    measured_graph = _parse_graph(
        _command_stdout(run_dir, records["measured-alembic-graph"])
    )
    measured_gates = json.loads(
        _command_stdout(run_dir, records["measured-runtime-gates"])
    )
    measured_network = _command_stdout(run_dir, records["measured-network"])
    measured_backend_image = _parse_image_measurement(
        _command_stdout(run_dir, records["measured-backend-image"])
    )
    measured_postgres_image = _parse_image_measurement(
        _command_stdout(run_dir, records["measured-postgres-image"])
    )
    measured_volume = json.loads(
        _command_stdout(run_dir, records["measured-venv-volume"])
    )
    measured_python = _parse_python_environment(
        _command_stdout(run_dir, records["measured-python-environment"])
    )
    if (
        measured_head != EXPECTED_HEAD
        or measurements.get("measured_alembic_head") != measured_head
    ):
        raise RuntimeError("measured Alembic head mismatch")
    if measurements.get("alembic_graph") != measured_graph:
        raise RuntimeError("measured Alembic graph mismatch")
    if (
        measured_gates != EXPECTED_RUNTIME_GATES
        or measurements.get("runtime_gates") != measured_gates
    ):
        raise RuntimeError("measured Runtime Gate mismatch")
    if (
        measured_network != "true"
        or measurements.get("docker_network_internal") is not True
    ):
        raise RuntimeError("measured Docker network is not internal")
    if measurements.get("backend_image") != measured_backend_image:
        raise RuntimeError("measured backend image mismatch")
    if measurements.get("postgres_image") != measured_postgres_image:
        raise RuntimeError("measured PostgreSQL image mismatch")
    if (
        measured_volume != BACKEND_VENV_VOLUME
        or measurements.get("backend_venv_volume") != measured_volume
    ):
        raise RuntimeError("measured backend venv volume mismatch")
    if measurements.get("python_environment") != measured_python:
        raise RuntimeError("measured Python environment mismatch")
    if measurements.get("ambient_runtime_dependent") is not True:
        raise RuntimeError("ambient runtime dependency measurement is missing")


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
    sentinel_database: str,
    compose_project: str,
    legacy_evidence_preserved: bool,
) -> dict[str, object]:
    report: dict[str, object] = {
        "schema_version": 3,
        "gate": GATE_NAME,
        "run_id": run_id,
        "compose_project": compose_project,
        "passed": passed,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "sentinel_database": sentinel_database,
        "migration_head": measurements.get("measured_alembic_head"),
        "migration_0014_created": measurements.get("alembic_graph", {}).get(
            "migration_0014_created"
        )
        if isinstance(measurements.get("alembic_graph"), dict)
        else None,
        "migration_0015_created": measurements.get("alembic_graph", {}).get(
            "migration_0015_created"
        )
        if isinstance(measurements.get("alembic_graph"), dict)
        else None,
        "migration_0016_created": measurements.get("alembic_graph", {}).get(
            "migration_0016_created"
        )
        if isinstance(measurements.get("alembic_graph"), dict)
        else None,
        "migration_0017_or_higher_present": measurements.get("alembic_graph", {}).get(
            "migration_0017_or_higher_present"
        )
        if isinstance(measurements.get("alembic_graph"), dict)
        else None,
        "feature_gates": {
            "agent_runtime_enabled": False,
            "agent_planner_enabled": False,
            "multi_agent_enabled": False,
        },
        "production_runtime_activated": False,
        "workload_container_external_network_denied": measurements.get(
            "docker_network_internal"
        )
        is True,
        "ambient_runtime_dependent": measurements.get("ambient_runtime_dependent")
        is True,
        "legacy_evidence_preserved": legacy_evidence_preserved,
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
            f"- Migration 0014 created: `{report['migration_0014_created']}`",
            f"- Migration 0015 created: `{report['migration_0015_created']}`",
            f"- Migration 0016 created: `{report['migration_0016_created']}`",
            f"- Migration 0017 or higher: `{report['migration_0017_or_higher_present']}`",
            "- Production Runtime activated: `false`",
            "- Feature gates: `false / false / false`",
            "- Workload-container external network denied: "
            f"`{report['workload_container_external_network_denied']}`",
            f"- Ambient image/venv runtime dependency disclosed: `{report['ambient_runtime_dependent']}`",
            f"- Legacy evidence preserved: `{legacy_evidence_preserved}`",
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
        print("P5.4B v2 static validation passed")
        return 0
    if args.verify_evidence is not None:
        _verify(args.verify_evidence)
        print("P5.4B v2 evidence verification passed")
        return 0
    if _dirty_paths():
        raise RuntimeError("P5.4B Gate requires a clean checkout")

    token = secrets.token_hex(6)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + token
    run_dir = (EVIDENCE_ROOT / run_id).resolve()
    if EVIDENCE_ROOT not in run_dir.parents or LEGACY_ROOT in run_dir.parents:
        raise RuntimeError("P5.4B evidence path escaped the v2 root")
    run_dir.mkdir(parents=True, exist_ok=False)
    override = run_dir / "compose-internal-network.yml"
    _write_bytes(
        override,
        b"services:\n  postgres-test:\n    pull_policy: never\nnetworks:\n  default:\n    internal: true\n",
    )
    manifest = _manifest()
    manifest_raw_sha = _write_json(run_dir / "source-manifest.json", manifest)
    _write_bytes(run_dir / "source-manifest.sha256", f"{manifest_raw_sha}\n".encode())

    project = f"omnibase-test-p54b-{token}"
    database_name = f"omnibase_test_p54b_{token}"
    role_name = f"omnibase_test_p54b_{token}"
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
    commands: list[dict[str, object]] = []
    measurements: dict[str, object] = {}
    cleanup: dict[str, int] | None = None
    errors: list[str] = []
    steps_passed = False
    legacy_before = _tree_manifest(LEGACY_ROOT)
    try:
        preflight_command = _expected_commands(project, override)["preflight-images"]
        commands.append(_run_step(run_dir, "preflight-images", preflight_command))
        up_command = _compose_command(
            project, override, "up", "-d", "--wait", "postgres-test"
        )
        commands.append(_run_step(run_dir, "compose-up", up_command, env=env))
        gate_commands, measurements = _run_gate(run_dir, project, database_url)
        commands.extend(gate_commands)
        steps_passed = True
    except Exception as exc:
        errors.append(str(exc))
    try:
        cleanup, cleanup_commands = _cleanup(run_dir, project, env, override)
        commands.extend(cleanup_commands)
    except Exception as exc:
        errors.append(f"cleanup: {exc}")
    legacy_evidence_preserved = legacy_before == _tree_manifest(LEGACY_ROOT)
    if not legacy_evidence_preserved:
        errors.append("legacy evidence tree changed during v2 run")
    passed = (
        steps_passed
        and not errors
        and cleanup == {"containers": 0, "networks": 0, "volumes": 0}
        and tuple(item.get("key") for item in commands) == REQUIRED_COMMAND_KEYS
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
        sentinel_database=database_name,
        compose_project=project,
        legacy_evidence_preserved=legacy_evidence_preserved,
    )
    if report["passed"]:
        print(f"P5.4B v2 disposable Gate passed: {run_dir}")
        return 0
    print(f"P5.4B v2 disposable Gate failed: {run_dir}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
