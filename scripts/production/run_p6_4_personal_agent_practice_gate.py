#!/usr/bin/env python3
"""Run and close the complete disposable P6.4 production acceptance target.

The DeepSeek credential is read only from ``OMNIBASE_P64_DEEPSEEK_API_KEY``.
This controller creates one new sentinel Compose project, proves the closed
before posture, activates one exact personal canary, delegates the six live
journeys to the matrix runner, closes the canary and all gates, destroys only
the named sentinel project, validates the redacted final receipt and writes it
inside the caller-declared run root.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_SRC = REPO_ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from run_p6_4_personal_agent_practice import (  # noqa: E402
    DEEPSEEK_KEY_ENV,
    LiveMatrixError,
    LiveMatrixRunner,
    TargetCoordinates,
    _canonical,
    _sha256,
)

from omnibase.agent_practice.receipt import (  # noqa: E402
    RECEIPT_SCHEMA,
    validate_personal_practice_receipt,
)

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_GATE_KEYS = (
    "P6_4_PERSONAL_PRACTICE_ENABLED",
    "AGENT_RUNTIME_ENABLED",
    "AGENT_PLANNER_ENABLED",
    "MULTI_AGENT_ENABLED",
    "MCP_RUNTIME_ENABLED",
)
_CLOSED = {
    "personal_practice_enabled": False,
    "agent_runtime_enabled": False,
    "agent_planner_enabled": False,
    "enterprise_multi_agent_enabled": False,
    "mcp_runtime_enabled": False,
}


class PracticeGateError(RuntimeError):
    """Stable fail-closed acceptance-controller error."""


@dataclass(frozen=True, slots=True)
class Target:
    project: str
    port: int
    env_file: Path


@dataclass(frozen=True, slots=True)
class ProductCoordinates:
    access_token: str
    tenant_id: str
    owner_user_id: str
    workspace_id: str
    decoy_workspace_id: str
    agent_version_id: str


@dataclass(frozen=True, slots=True)
class _CleanupInventory:
    files: tuple[Path, ...]
    links: tuple[Path, ...]
    directories: tuple[Path, ...]


def _run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: float = 900,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PracticeGateError("acceptance_command_unavailable") from exc
    if check and result.returncode != 0:
        raise PracticeGateError(f"acceptance_command_failed:{Path(argv[0]).name}")
    return result


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _clean_source_head(repo_root: Path) -> str:
    head = _run(["git", "rev-parse", "HEAD"], cwd=repo_root, timeout=30)
    value = head.stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise PracticeGateError("source_head_invalid")
    status = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repo_root,
        timeout=30,
    )
    if status.stdout:
        raise PracticeGateError("source_worktree_not_clean")
    return value


def _require_healthy_docker(repo_root: Path) -> None:
    """Read-only preflight; never starts or repairs Docker/WSL infrastructure."""

    version = _run(
        ["docker", "version", "--format", "{{.Server.Os}}"],
        cwd=repo_root,
        timeout=30,
        check=False,
    )
    if version.returncode != 0 or version.stdout.strip().lower() != "linux":
        raise PracticeGateError("docker_linux_engine_not_healthy")
    info = _run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        cwd=repo_root,
        timeout=30,
        check=False,
    )
    if info.returncode != 0 or not info.stdout.strip():
        raise PracticeGateError("docker_linux_engine_not_healthy")


def _fernet_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def _write_operator_env(path: Path, *, target: Target) -> None:
    database_user = f"omnibase_p64_{target.project.rsplit('-', 1)[-1]}"
    database = f"omnibase_test_p64_{target.project.rsplit('-', 1)[-1]}"
    database_password = secrets.token_hex(20)
    redis_password = secrets.token_hex(20)
    minio_password = secrets.token_hex(20)
    values = {
        "OMNIBASE_FRONTEND_PORT": str(target.port),
        "POSTGRES_USER": database_user,
        "POSTGRES_PASSWORD": database_password,
        "POSTGRES_DB": database,
        "DATABASE_URL": (
            f"postgresql+psycopg://{database_user}:{database_password}@postgres:5432/{database}"
        ),
        "MINIO_ROOT_USER": "omnibase",
        "MINIO_ROOT_PASSWORD": minio_password,
        "MINIO_BUCKET": "omnibase-files",
        "REDIS_PASSWORD": redis_password,
        "REDIS_URL": f"redis://:{redis_password}@redis:6379/0",
        "JWT_SECRET": secrets.token_hex(32),
        "PROVIDER_CREDENTIAL_ENCRYPTION_KEY": _fernet_key(),
        "MEMORY_CONTENT_ENCRYPTION_KEY": _fernet_key(),
        "PROVIDER_ENDPOINT_ALLOWLIST": '["api.deepseek.com"]',
        "OMNIBASE_DEPLOYMENT_INSTANCE_ID": str(uuid.uuid4()),
        "CORS_ORIGINS": f'["http://127.0.0.1:{target.port}"]',
    }
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="utf-8",
        newline="\n",
    )


def _request_json(
    *,
    base_url: str,
    method: str,
    path: str,
    payload: object | None = None,
    token: str | None = None,
    idempotency_key: str | None = None,
    expected: tuple[int, ...] = (200,),
    timeout: float = 60,
) -> dict[str, object]:
    data = None if payload is None else _canonical(payload)
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            body = response.read(1024 * 1024 + 1)
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        raise PracticeGateError(f"browser_request_rejected:http_{exc.code}") from None
    except (OSError, urllib.error.URLError, TimeoutError):
        raise PracticeGateError("browser_request_unavailable") from None
    if status not in expected or len(body) > 1024 * 1024:
        raise PracticeGateError("browser_response_invalid")
    if "application/json" not in content_type.lower():
        raise PracticeGateError("browser_response_content_type_invalid")
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PracticeGateError("browser_response_json_invalid") from exc
    if not isinstance(value, dict):
        raise PracticeGateError("browser_response_shape_invalid")
    return value


class PracticeGateController:
    def __init__(
        self,
        *,
        repo_root: Path,
        work_root: Path,
        output: Path,
        model_id: str,
        deepseek_key: str,
    ) -> None:
        self.repo_root = repo_root
        self.work_root = work_root
        self.output = output
        self.model_id = model_id
        self.deepseek_key = deepseek_key
        suffix = uuid.uuid4().hex[:8]
        self.target = Target(
            project=f"omnibase-p64-{suffix}",
            port=_free_port(),
            env_file=work_root / "operator.env",
        )
        self.base_compose = repo_root / "deployment/personal-production/compose.yml"
        self.practice_compose = (
            repo_root / "deployment/personal-production/p6-4-acceptance.compose.yml"
        )
        self.runtime_compose = (
            repo_root
            / "deployment/production/personal-runtime-canary.compose.example.yml"
        )
        self.fixture = repo_root / "scripts/production/p5_9p_acceptance_fixture.py"
        self.canary_config = work_root / "canary.json"
        self.state_dir = work_root / "canary-state"
        self.readiness_root = work_root / "readiness-root"
        self.model_cache = work_root / "model-cache"
        self.matrix_root = work_root / "matrix-run"
        self.compose_env = os.environ.copy()
        self.compose_env.update(
            {
                "P6_4_MODEL_CACHE_HOST_ROOT": str(self.model_cache),
                "P6_4_ACCEPTANCE_FIXTURE_PATH": str(self.fixture),
                "PERSONAL_RUNTIME_CANARY_HOST_CONFIG": str(self.canary_config),
                "PERSONAL_RUNTIME_CANARY_HOST_STATE": str(self.state_dir),
                "PERSONAL_RUNTIME_READINESS_HOST_ROOT": str(self.readiness_root),
            }
        )
        self.coordinates: ProductCoordinates | None = None
        self.matrix: dict[str, object] | None = None
        self.before: dict[str, object] | None = None
        self.after: dict[str, object] | None = None
        self.canary_closed = False
        self.target_removed = False
        self.source_head: str | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.target.port}"

    def _compose_files(self, *, during: bool) -> list[str]:
        files = ["-f", str(self.base_compose)]
        if during:
            files.extend(
                ["-f", str(self.practice_compose), "-f", str(self.runtime_compose)]
            )
        return files

    def compose(
        self,
        arguments: list[str],
        *,
        during: bool,
        timeout: float = 900,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return _run(
            [
                "docker",
                "compose",
                "--env-file",
                str(self.target.env_file),
                "-p",
                self.target.project,
                *self._compose_files(during=during),
                *arguments,
            ],
            cwd=self.repo_root,
            env=self.compose_env,
            timeout=timeout,
            check=check,
        )

    def _wait_http(self, *, timeout: float = 300) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                request = urllib.request.Request(
                    self.base_url + "/healthz", method="GET"
                )
                with urllib.request.urlopen(request, timeout=3) as response:
                    if int(response.status) == 200:
                        return
            except (OSError, urllib.error.URLError, TimeoutError):
                pass
            time.sleep(2)
        raise PracticeGateError("production_target_health_timeout")

    def _prepare_files(self) -> str:
        self.work_root.mkdir(parents=False, exist_ok=False)
        self.state_dir.mkdir()
        self.model_cache.mkdir()
        self.canary_config.write_bytes(b"{}\n")
        relative_files = (
            Path("deployment/production/personal-single-owner.example.json"),
            Path("docs/evidence/p34-7/personal-owner-disposable-gate.json"),
        )
        for relative in relative_files:
            target = self.readiness_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.repo_root / relative, target)
        readiness = self.readiness_root / relative_files[0]
        return hashlib.sha256(readiness.read_bytes()).hexdigest()

    def _start_closed(self) -> None:
        self.compose(["config", "--quiet"], during=False, timeout=60)
        self.compose(["build", "backend", "frontend"], during=False, timeout=1800)
        self.compose(["up", "-d", "--wait", "frontend"], during=False, timeout=600)
        self._wait_http()

    def _register_product(self) -> ProductCoordinates:
        email = f"p64-{uuid.uuid4().hex[:12]}@example.com"
        password = f"P6!{secrets.token_hex(14)}"
        registration = _request_json(
            base_url=self.base_url,
            method="POST",
            path="/api/v1/auth/register",
            payload={
                "email": email,
                "password": password,
                "tenant_name": "P6.4 Personal",
            },
            expected=(201,),
        )
        login = _request_json(
            base_url=self.base_url,
            method="POST",
            path="/api/v1/auth/login",
            payload={"email": email, "password": password},
        )
        token = str(login.get("access_token", ""))
        if not token:
            raise PracticeGateError("acceptance_login_token_missing")
        tenant = registration.get("tenant")
        user = registration.get("user")
        if not isinstance(tenant, dict) or not isinstance(user, dict):
            raise PracticeGateError("acceptance_registration_identity_missing")
        template = _request_json(
            base_url=self.base_url,
            method="POST",
            path="/api/v1/workspace-templates",
            token=token,
            payload={
                "template_key": "personal.p64.acceptance",
                "version": 1,
                "display_name": "P6.4 Personal Acceptance",
                "template_spec": {"profile": "personal_single_owner", "tools": []},
                "supersedes_template_id": None,
            },
            expected=(201,),
        )
        template_id = str(template.get("id", ""))
        workspace_ids: list[str] = []
        for label in ("main", "decoy"):
            workspace = _request_json(
                base_url=self.base_url,
                method="POST",
                path="/api/v1/workspaces",
                token=token,
                idempotency_key=f"p64-workspace-{label}-{uuid.uuid4().hex}",
                payload={
                    "display_name": f"P6.4 {label.title()} Workspace",
                    "template_id": template_id,
                    "parent_workspace_id": None,
                    "quota": {"max_active_runs": 8},
                },
                expected=(201,),
            )
            workspace_ids.append(str(workspace.get("id", "")))
        if len(set(workspace_ids)) != 2 or any(
            _UUID.fullmatch(item) is None for item in workspace_ids
        ):
            raise PracticeGateError("acceptance_workspace_identity_invalid")
        agent = _request_json(
            base_url=self.base_url,
            method="POST",
            path=f"/api/v1/workspaces/{workspace_ids[0]}/agents",
            token=token,
            idempotency_key=f"p64-agent-{uuid.uuid4().hex}",
            payload={
                "display_name": "P6.4 Practice Agent",
                "role_description": "A bounded no-tool personal practice parent",
                "instructions": "Use only Workspace evidence and return the exact requested JSON.",
                "assistant_tone": "concise",
                "provider_policy": "user_default",
                "knowledge_mode": "workspace_read_only",
                "max_context_tokens": 8192,
                "max_output_tokens": 2048,
                "max_wall_clock_seconds": 180,
                "install_immediately": True,
            },
            expected=(201,),
        )
        version = agent.get("version")
        if not isinstance(version, dict):
            raise PracticeGateError("acceptance_agent_version_missing")
        coordinates = ProductCoordinates(
            access_token=token,
            tenant_id=str(tenant.get("id", "")),
            owner_user_id=str(user.get("id", "")),
            workspace_id=workspace_ids[0],
            decoy_workspace_id=workspace_ids[1],
            agent_version_id=str(version.get("agent_version_id", "")),
        )
        for value in (
            coordinates.tenant_id,
            coordinates.owner_user_id,
            coordinates.agent_version_id,
        ):
            if _UUID.fullmatch(value) is None:
                raise PracticeGateError("acceptance_product_identity_invalid")
        self.coordinates = coordinates
        return coordinates

    def _write_canary(self, *, readiness_sha256: str) -> None:
        coordinates = self._coordinates()
        payload = {
            "agent_planner_enabled": False,
            "agent_version_id": coordinates.agent_version_id,
            "canary_id": str(uuid.uuid4()),
            "enterprise_approved_digest_present": False,
            "environment": "production",
            "external_side_effects": False,
            "invocation_mode": "no_tool",
            "max_canary_seconds": 3600,
            "max_concurrent_invocations": 1,
            "max_top_k": 5,
            "migration_0013_created": True,
            "migration_head": "0016",
            "multi_agent_enabled": False,
            "network": {"default_deny": True, "destinations": []},
            "owner_readiness": {
                "path": "deployment/production/personal-single-owner.example.json",
                "sha256": readiness_sha256,
            },
            "owner_user_id": coordinates.owner_user_id,
            "profile": "personal_single_owner",
            "schema_version": 1,
            "tenant_id": coordinates.tenant_id,
            "workspace_id": coordinates.workspace_id,
        }
        self.canary_config.write_bytes(_canonical(payload))

    def _coordinates(self) -> ProductCoordinates:
        if self.coordinates is None:
            raise PracticeGateError("acceptance_coordinates_unavailable")
        return self.coordinates

    def _fixture(self, command: str) -> dict[str, object]:
        result = self.compose(
            [
                "run",
                "--rm",
                "--no-deps",
                "acceptance-fixture",
                "python",
                "/acceptance/p5_9p_acceptance_fixture.py",
                command,
            ],
            during=True,
            timeout=180,
        )
        lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
        if not lines:
            raise PracticeGateError("acceptance_fixture_output_missing")
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise PracticeGateError("acceptance_fixture_output_invalid") from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise PracticeGateError("acceptance_fixture_failed")
        return payload

    def _gate_env(self, *, during: bool) -> dict[str, object]:
        expression = (
            "import json,os;print(json.dumps({k:os.environ.get(k) for k in "
            + repr(_GATE_KEYS)
            + "},sort_keys=True))"
        )
        result = self.compose(
            ["exec", "-T", "backend", "python", "-c", expression],
            during=during,
            timeout=60,
        )
        try:
            value = json.loads(result.stdout.strip())
        except json.JSONDecodeError as exc:
            raise PracticeGateError("gate_environment_probe_invalid") from exc
        if not isinstance(value, dict):
            raise PracticeGateError("gate_environment_probe_invalid")
        return value

    def _status(self) -> dict[str, object]:
        coordinates = self._coordinates()
        return _request_json(
            base_url=self.base_url,
            method="GET",
            path=f"/api/v1/workspaces/{coordinates.workspace_id}/agent-alpha/status",
            token=coordinates.access_token,
        )

    def _verify_closed(self) -> dict[str, object]:
        env = self._gate_env(during=False)
        if (
            env.get("AGENT_RUNTIME_ENABLED") != "false"
            or env.get("AGENT_PLANNER_ENABLED") != "false"
            or env.get("MULTI_AGENT_ENABLED") != "false"
            or env.get("P6_4_PERSONAL_PRACTICE_ENABLED") not in {None, "false"}
            or env.get("MCP_RUNTIME_ENABLED") not in {None, "false"}
        ):
            raise PracticeGateError("closed_gate_environment_drift")
        status = self._status()
        if (
            status.get("personal_runtime_active") is not False
            or status.get("personal_practice_active") is not False
            or status.get("tools_enabled") is not False
            or status.get("multi_agent_enabled") is not False
        ):
            raise PracticeGateError("closed_product_posture_drift")
        return dict(_CLOSED)

    def _start_during(self) -> None:
        self.compose(["config", "--quiet"], during=True, timeout=60)
        activation = self._fixture("activate")
        if activation.get("state") != "active":
            raise PracticeGateError("personal_canary_activation_failed")
        self.compose(
            [
                "up",
                "-d",
                "--force-recreate",
                "--wait",
                "backend",
                "celery-worker",
                "frontend",
            ],
            during=True,
            timeout=1200,
        )
        self._wait_http(timeout=600)
        env = self._gate_env(during=True)
        expected = {
            "P6_4_PERSONAL_PRACTICE_ENABLED": "true",
            "AGENT_RUNTIME_ENABLED": "true",
            "AGENT_PLANNER_ENABLED": "false",
            "MULTI_AGENT_ENABLED": "false",
            "MCP_RUNTIME_ENABLED": "false",
        }
        if env != expected:
            raise PracticeGateError("during_gate_environment_drift")
        status = self._status()
        if (
            status.get("personal_runtime_active") is not True
            or status.get("personal_practice_active") is not True
            or status.get("personal_practice_blockers") != []
        ):
            raise PracticeGateError("during_product_posture_drift")

    def _run_matrix(self) -> None:
        coordinates = self._coordinates()
        runner = LiveMatrixRunner(
            repo_root=self.repo_root,
            work_root=self.matrix_root,
            coordinates=TargetCoordinates(
                base_url=self.base_url,
                workspace_id=coordinates.workspace_id,
                decoy_workspace_id=coordinates.decoy_workspace_id,
                agent_version_id=coordinates.agent_version_id,
            ),
            model_id=self.model_id,
            access_token=coordinates.access_token,
            deepseek_key=self.deepseek_key,
        )
        matrix: dict[str, object] | None = None
        failure: BaseException | None = None
        try:
            matrix = runner.execute()
        except (
            LiveMatrixError,
            OSError,
            subprocess.SubprocessError,
            ValueError,
        ) as exc:
            failure = exc
        cleanup_errors = runner.cleanup_browser_state(matrix)
        if failure is not None:
            raise PracticeGateError(
                f"live_matrix_failed:{_error_code(failure)}"
            ) from failure
        if matrix is None or cleanup_errors:
            raise PracticeGateError("live_matrix_cleanup_failed")
        cleanup = matrix.get("cleanup")
        if not isinstance(cleanup, dict) or (
            cleanup.get("disposable_documents_removed") is not True
            or cleanup.get("provider_credential_revoked") is not True
        ):
            raise PracticeGateError("live_matrix_cleanup_unverified")
        self.matrix = matrix

    def _close_runtime(self) -> None:
        killed = self._fixture("kill")
        if killed.get("state") != "killed":
            raise PracticeGateError("personal_canary_kill_failed")
        self.compose(["stop", "celery-worker"], during=True, timeout=120, check=False)
        self.compose(
            ["up", "-d", "--no-deps", "--force-recreate", "--wait", "backend"],
            during=False,
            timeout=300,
        )
        self.compose(
            ["up", "-d", "--no-deps", "--force-recreate", "--wait", "frontend"],
            during=False,
            timeout=300,
        )
        self._wait_http()
        self.after = self._verify_closed()
        self.canary_closed = True

    def _project_resources(self) -> dict[str, list[str]]:
        label = f"label=com.docker.compose.project={self.target.project}"
        commands = {
            "containers": ["docker", "ps", "-aq", "--filter", label],
            "networks": ["docker", "network", "ls", "-q", "--filter", label],
            "volumes": ["docker", "volume", "ls", "-q", "--filter", label],
        }
        resources: dict[str, list[str]] = {}
        for kind, command in commands.items():
            result = _run(command, cwd=self.repo_root, timeout=60, check=False)
            resources[kind] = (
                ["query_failed"]
                if result.returncode != 0
                else [line for line in result.stdout.splitlines() if line]
            )
        return resources

    def _remove_target(self) -> None:
        result = self.compose(
            ["down", "-v", "--remove-orphans"],
            during=True,
            timeout=600,
            check=False,
        )
        if result.returncode != 0:
            raise PracticeGateError("disposable_target_down_failed")
        resources = self._project_resources()
        if any(resources.values()):
            raise PracticeGateError("disposable_target_resources_remain")
        self.target_removed = True

    def _purge_bounded_tree(self, root: Path) -> None:
        if not os.path.lexists(root):
            return
        try:
            metadata = os.lstat(root)
        except OSError as exc:
            raise PracticeGateError("local_cleanup_root_stat_failed") from exc
        if stat.S_ISLNK(metadata.st_mode) or bool(
            getattr(metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise PracticeGateError("local_cleanup_root_is_link")
        resolved_root = root.resolve(strict=True)
        resolved_work = self.work_root.resolve(strict=True)
        if resolved_root == resolved_work or resolved_work not in resolved_root.parents:
            raise PracticeGateError("local_cleanup_root_outside_run")
        inventory = self._inventory_bounded_tree(root)
        self._delete_bounded_inventory(inventory)

    @staticmethod
    def _inventory_bounded_tree(root: Path) -> _CleanupInventory:
        files: list[Path] = []
        links: list[Path] = []
        directories: list[Path] = [root]
        stack = [root]
        entries = 0
        total_bytes = 0
        while stack:
            current = stack.pop()
            try:
                children = list(os.scandir(current))
            except OSError as exc:
                raise PracticeGateError("local_cleanup_inventory_failed") from exc
            for child in children:
                entries += 1
                if entries > 250_000:
                    raise PracticeGateError("local_cleanup_entry_budget_exceeded")
                path = Path(child.path)
                try:
                    metadata = child.stat(follow_symlinks=False)
                except OSError as exc:
                    raise PracticeGateError("local_cleanup_stat_failed") from exc
                reparse = bool(
                    getattr(metadata, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                )
                if child.is_symlink() or reparse:
                    links.append(path)
                    continue
                if child.is_dir(follow_symlinks=False):
                    directories.append(path)
                    stack.append(path)
                    continue
                if not child.is_file(follow_symlinks=False):
                    raise PracticeGateError("local_cleanup_special_file_rejected")
                total_bytes += metadata.st_size
                if total_bytes > 8 * 1024**3:
                    raise PracticeGateError("local_cleanup_byte_budget_exceeded")
                files.append(path)
        return _CleanupInventory(
            files=tuple(files),
            links=tuple(links),
            directories=tuple(directories),
        )

    @staticmethod
    def _delete_bounded_inventory(inventory: _CleanupInventory) -> None:
        try:
            for path in inventory.files:
                path.unlink()
            for path in inventory.links:
                if getattr(path, "is_junction", lambda: False)():
                    path.rmdir()
                else:
                    path.unlink()
            for path in sorted(
                inventory.directories,
                key=lambda item: len(item.parts),
                reverse=True,
            ):
                path.rmdir()
        except OSError as exc:
            raise PracticeGateError("local_cleanup_delete_failed") from exc

    def _cleanup_local_disposable(self) -> None:
        for root in (
            self.matrix_root,
            self.model_cache,
            self.state_dir,
            self.readiness_root,
        ):
            self._purge_bounded_tree(root)
        try:
            self.canary_config.unlink(missing_ok=True)
            self.target.env_file.unlink(missing_ok=True)
        except OSError as exc:
            raise PracticeGateError("local_sensitive_file_cleanup_failed") from exc
        if self.canary_config.exists() or self.target.env_file.exists():
            raise PracticeGateError("local_sensitive_file_cleanup_unverified")
        if any(
            os.path.lexists(root)
            for root in (
                self.matrix_root,
                self.model_cache,
                self.state_dir,
                self.readiness_root,
            )
        ):
            raise PracticeGateError("local_disposable_cleanup_unverified")

    def _receipt(self) -> dict[str, object]:
        if self.matrix is None or self.before is None or self.after is None:
            raise PracticeGateError("final_receipt_inputs_incomplete")
        provider = self.matrix.get("provider")
        during = self.matrix.get("during_posture")
        journeys = self.matrix.get("journeys")
        if (
            not isinstance(provider, dict)
            or not isinstance(during, dict)
            or not isinstance(journeys, dict)
        ):
            raise PracticeGateError("final_receipt_matrix_shape_invalid")
        if (
            self.source_head is None
            or self.matrix.get("source_head") != self.source_head
        ):
            raise PracticeGateError("final_receipt_source_drift")
        if _clean_source_head(self.repo_root) != self.source_head:
            raise PracticeGateError("final_receipt_source_drift")
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source_head": self.matrix.get("source_head"),
            "provider": provider,
            "posture": {
                "before": self.before,
                "during": during,
                "after": self.after,
            },
            "journeys": journeys,
            "cleanup": {
                "disposable_documents_removed": True,
                "disposable_workspaces_removed": self.target_removed,
                "provider_credential_revoked": True,
                "runtime_canary_closed": self.canary_closed,
                "all_feature_gates_closed": self.after == _CLOSED,
            },
            "production_accepted": True,
        }
        validate_personal_practice_receipt(receipt)
        return receipt

    def execute(self) -> dict[str, object]:
        self.source_head = _clean_source_head(self.repo_root)
        _require_healthy_docker(self.repo_root)
        readiness_sha256 = self._prepare_files()
        _write_operator_env(self.target.env_file, target=self.target)
        self._start_closed()
        self._register_product()
        self.before = self._verify_closed()
        self._write_canary(readiness_sha256=readiness_sha256)
        self._start_during()
        self._run_matrix()
        self._close_runtime()
        self._remove_target()
        self._cleanup_local_disposable()
        receipt = self._receipt()
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_bytes(_canonical(receipt))
        return receipt

    def cleanup_after_failure(self) -> tuple[str, ...]:
        errors: list[str] = []
        if self.target.env_file.exists():
            result = self.compose(
                ["down", "-v", "--remove-orphans"],
                during=True,
                timeout=600,
                check=False,
            )
            if result.returncode != 0 or any(self._project_resources().values()):
                errors.append("disposable_target_cleanup_failed")
        try:
            self.target.env_file.unlink(missing_ok=True)
        except OSError:
            errors.append("operator_env_cleanup_failed")
        for root in (
            self.matrix_root,
            self.model_cache,
            self.state_dir,
            self.readiness_root,
        ):
            try:
                self._purge_bounded_tree(root)
            except PracticeGateError:
                errors.append(f"local_cleanup_failed:{root.name}")
        try:
            self.canary_config.unlink(missing_ok=True)
        except OSError:
            errors.append("canary_config_cleanup_failed")
        return tuple(errors)


def _absolute(value: str, *, label: str, must_exist: bool) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise PracticeGateError(f"{label}_must_be_absolute")
    try:
        return path.resolve(strict=must_exist)
    except OSError as exc:
        raise PracticeGateError(f"{label}_unavailable") from exc


def _validate_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    repo_root = _absolute(args.repo_root, label="repo_root", must_exist=True)
    if repo_root != REPO_ROOT.resolve(strict=True):
        raise PracticeGateError("repo_root_must_match_controller_source")
    work_root = _absolute(args.work_root, label="work_root", must_exist=False)
    if work_root.exists() or not work_root.name.startswith("omnibase-p64-"):
        raise PracticeGateError("work_root_invalid")
    if repo_root in work_root.parents or work_root in repo_root.parents:
        raise PracticeGateError("work_root_must_be_outside_repo")
    if not work_root.parent.is_dir():
        raise PracticeGateError("work_root_parent_missing")
    output = _absolute(args.output, label="output", must_exist=False)
    if (
        output.exists()
        or output.suffix.lower() != ".json"
        or output.parent != work_root
    ):
        raise PracticeGateError("output_must_be_new_json_inside_work_root")
    return repo_root, work_root, output


def _args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--model-id",
        choices=("deepseek-v4-flash", "deepseek-v4-pro"),
        default="deepseek-v4-flash",
    )
    return parser.parse_args(argv)


def _error_code(exc: BaseException) -> str:
    if not isinstance(exc, (PracticeGateError, LiveMatrixError)):
        return "p6_4_acceptance_failed"
    return re.sub(r"[^a-z0-9]+", "_", str(exc).lower()).strip("_")[:160]


def main(argv: list[str] | None = None) -> int:
    try:
        args = _args(argv)
        repo_root, work_root, output = _validate_paths(args)
        deepseek_key = os.environ.get(DEEPSEEK_KEY_ENV, "")
        if not deepseek_key:
            raise PracticeGateError(f"{DEEPSEEK_KEY_ENV}_missing")
    except (PracticeGateError, OSError, ValueError) as exc:
        print(
            _canonical(
                {"state": "failed/veto", "error_code": _error_code(exc)}
            ).decode(),
            end="",
        )
        return 2
    controller = PracticeGateController(
        repo_root=repo_root,
        work_root=work_root,
        output=output,
        model_id=args.model_id,
        deepseek_key=deepseek_key,
    )
    try:
        receipt = controller.execute()
    except (
        PracticeGateError,
        LiveMatrixError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as exc:
        cleanup_errors = controller.cleanup_after_failure()
        print(
            _canonical(
                {
                    "state": "failed/veto",
                    "error_code": _error_code(exc),
                    "cleanup_errors": list(cleanup_errors),
                    "production_accepted": False,
                }
            ).decode(),
            end="",
        )
        return 1
    print(
        _canonical(
            {
                "state": "accepted",
                "receipt_name": output.name,
                "receipt_sha256": _sha256(_canonical(receipt)),
                "production_accepted": True,
            }
        ).decode(),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
