"""Run the disposable P5.9P personal production-like acceptance journey.

The runner creates two isolated Compose projects and never reads the repository
root ``.env``.  Project A proves the exact personal Agent/Skill/Memory/SSE/
cancel/restart/no-replay/retry/kill lifecycle.  A cold PostgreSQL dump is then
restored into a distinct ``omnibase_restore_*`` database in project B, where an
authenticated Runtime-off product smoke proves restore-new usability.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

_SENSITIVE_RECEIPT_KEY_PARTS = (
    "access_token",
    "api_key",
    "authorization",
    "database_url",
    "jwt_secret",
    "message",
    "password",
    "prompt",
    "secret",
)


class AcceptanceError(RuntimeError):
    """The production-like acceptance journey failed closed."""


@dataclass(frozen=True, slots=True)
class Target:
    project: str
    env_file: Path
    port: int
    database: str
    database_user: str


@dataclass(frozen=True, slots=True)
class ProductCoordinates:
    access_token: str
    email: str
    password: str
    tenant_id: str
    owner_user_id: str
    workspace_id: str
    agent_version_id: str


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: float = 900,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise AcceptanceError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout[-4000:]}\nstderr:\n{result.stderr[-4000:]}"
        )
    return result


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _secret_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


def _assert_receipt_safe(value: object, *, path: str = "receipt") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in _SENSITIVE_RECEIPT_KEY_PARTS):
                raise AcceptanceError(
                    f"{path} contains forbidden sensitive key {key!r}"
                )
            _assert_receipt_safe(item, path=f"{path}.{key}")
        return
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _assert_receipt_safe(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and any(
        marker in value.lower()
        for marker in ("bearer ", "postgresql+psycopg://", "redis://")
    ):
        raise AcceptanceError(f"{path} contains a forbidden secret locator")


def _write_operator_env(
    path: Path,
    *,
    port: int,
    database: str,
    database_user: str,
    deployment_id: str,
    password: str,
    redis_password: str,
    minio_password: str,
    jwt_secret: str,
    provider_key: str,
    memory_key: str,
) -> None:
    values = {
        "OMNIBASE_FRONTEND_PORT": str(port),
        "POSTGRES_USER": database_user,
        "POSTGRES_PASSWORD": password,
        "POSTGRES_DB": database,
        "DATABASE_URL": (
            f"postgresql+psycopg://{database_user}:{password}@postgres:5432/{database}"
        ),
        "MINIO_ROOT_USER": "omnibase",
        "MINIO_ROOT_PASSWORD": minio_password,
        "MINIO_BUCKET": "omnibase-files",
        "REDIS_PASSWORD": redis_password,
        "REDIS_URL": f"redis://:{redis_password}@redis:6379/0",
        "JWT_SECRET": jwt_secret,
        "PROVIDER_CREDENTIAL_ENCRYPTION_KEY": provider_key,
        "MEMORY_CONTENT_ENCRYPTION_KEY": memory_key,
        "PROVIDER_ENDPOINT_ALLOWLIST": '["api.openai.com"]',
        "OMNIBASE_DEPLOYMENT_INSTANCE_ID": deployment_id,
        "CORS_ORIGINS": f'["http://127.0.0.1:{port}"]',
    }
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8"
    )
    if os.name != "nt":
        path.chmod(0o600)


class Journey:
    def __init__(self, *, repo: Path, work_root: Path, lease_wait_seconds: int) -> None:
        self.repo = repo
        self.work_root = work_root
        self.lease_wait_seconds = lease_wait_seconds
        self.base_compose = repo / "deployment/personal-production/compose.yml"
        self.support_compose = (
            repo / "deployment/personal-production/acceptance.compose.yml"
        )
        self.runtime_compose = (
            repo / "deployment/production/personal-runtime-canary.compose.example.yml"
        )
        self.fixture = repo / "scripts/production/p5_9p_acceptance_fixture.py"
        self.fake_provider = repo / "scripts/production/p5_9p_fake_provider.py"
        self.canary_config = work_root / "canary.json"
        self.state_dir = work_root / "canary-state"
        self.readiness_root = work_root / "readiness-root"
        self.backup_root = work_root / "cold-backup"
        self.receipt_path = work_root / "p5-9p-acceptance-receipt.json"
        self.compose_env = os.environ.copy()
        self.compose_env.update(
            {
                "P5_ACCEPTANCE_FIXTURE_PATH": str(self.fixture),
                "P5_ACCEPTANCE_FAKE_PROVIDER_PATH": str(self.fake_provider),
                "P5_ACCEPTANCE_CANARY_CONFIG": str(self.canary_config),
                "P5_ACCEPTANCE_STATE_DIR": str(self.state_dir),
                "P5_ACCEPTANCE_READINESS_ROOT": str(self.readiness_root),
                "PERSONAL_RUNTIME_CANARY_HOST_CONFIG": str(self.canary_config),
                "PERSONAL_RUNTIME_CANARY_HOST_STATE": str(self.state_dir),
                "PERSONAL_RUNTIME_READINESS_HOST_ROOT": str(self.readiness_root),
            }
        )
        self.targets: list[Target] = []
        self.redaction_values: set[str] = set()
        self.receipt: dict[str, Any] = {
            "schema": "omnibase.p5-9p-personal-acceptance.v1",
            "schema_version": 1,
            "root_env_accessed": False,
            "business_database_accessed": False,
            "business_database_migrated": False,
            "real_provider_credential_used": False,
            "planner_enabled": False,
            "multi_agent_enabled": False,
        }

    def redact_error(self, value: str) -> str:
        redacted = value
        for secret_value in sorted(self.redaction_values, key=len, reverse=True):
            if secret_value:
                redacted = redacted.replace(secret_value, "[REDACTED]")
        return redacted

    def _compose_files(self, *, runtime: bool) -> list[str]:
        files = ["-f", str(self.base_compose), "-f", str(self.support_compose)]
        if runtime:
            files.extend(["-f", str(self.runtime_compose)])
        return files

    def compose(
        self,
        target: Target,
        arguments: list[str],
        *,
        runtime: bool = False,
        timeout: float = 900,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return _run(
            [
                "docker",
                "compose",
                "--env-file",
                str(target.env_file),
                "-p",
                target.project,
                *self._compose_files(runtime=runtime),
                *arguments,
            ],
            cwd=self.repo,
            env=self.compose_env,
            timeout=timeout,
            check=check,
        )

    def base_compose_only(
        self,
        target: Target,
        arguments: list[str],
        *,
        timeout: float = 900,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return _run(
            [
                "docker",
                "compose",
                "--env-file",
                str(target.env_file),
                "-p",
                target.project,
                "-f",
                str(self.base_compose),
                *arguments,
            ],
            cwd=self.repo,
            env=self.compose_env,
            timeout=timeout,
            check=check,
        )

    def fixture_command(self, target: Target, arguments: list[str]) -> dict[str, Any]:
        result = self.compose(
            target,
            [
                "run",
                "--rm",
                "--no-deps",
                "acceptance-fixture",
                "python",
                "/acceptance/p5_9p_acceptance_fixture.py",
                *arguments,
            ],
            timeout=120,
        )
        lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
        if not lines:
            raise AcceptanceError(f"fixture produced no JSON: {result.stdout[-2000:]}")
        payload = json.loads(lines[-1])
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise AcceptanceError(f"fixture failed: {payload}")
        return payload

    @staticmethod
    def _url(target: Target, path: str) -> str:
        return f"http://127.0.0.1:{target.port}{path}"

    def request(
        self,
        target: Target,
        method: str,
        path: str,
        *,
        token: str | None = None,
        payload: object | None = None,
        idempotency_key: str | None = None,
        expected: tuple[int, ...] = (200,),
        timeout: float = 20,
    ) -> tuple[int, Any]:
        raw = (
            None
            if payload is None
            else json.dumps(payload, separators=(",", ":")).encode()
        )
        headers = {"Accept": "application/json"}
        if raw is not None:
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(
            self._url(target, path), data=raw, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.status
                body = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            body = exc.read()
        if status not in expected:
            raise AcceptanceError(
                f"{method} {path} returned {status}, expected {expected}: {body[:2000]!r}"
            )
        if not body:
            return status, None
        try:
            return status, json.loads(body)
        except json.JSONDecodeError:
            return status, body.decode(errors="replace")

    def wait_http(
        self, target: Target, path: str = "/healthz", timeout: float = 180
    ) -> None:
        deadline = time.monotonic() + timeout
        last_error = "unavailable"
        while time.monotonic() < deadline:
            try:
                status, _ = self.request(
                    target, "GET", path, expected=(200,), timeout=3
                )
                if status == 200:
                    return
            except (AcceptanceError, OSError) as exc:
                last_error = str(exc)
            time.sleep(2)
        raise AcceptanceError(f"target did not become healthy: {last_error}")

    def _prepare_readiness_root(self) -> str:
        relative_files = (
            Path("deployment/production/personal-single-owner.example.json"),
            Path("docs/evidence/p34-7/personal-owner-disposable-gate.json"),
        )
        for relative in relative_files:
            source = self.repo / relative
            target = self.readiness_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        readiness = self.readiness_root / relative_files[0]
        return hashlib.sha256(readiness.read_bytes()).hexdigest()

    def _create_target(self, *, suffix: str, restore: bool = False) -> Target:
        database = (
            f"omnibase_restore_p59_{suffix}"
            if restore
            else f"omnibase_test_p59_{suffix}"
        )
        user = f"omnibase_p59_{suffix}"
        target = Target(
            project=f"omnibase-p59p-{'b' if restore else 'a'}-{suffix}",
            env_file=self.work_root
            / ("operator-b.env" if restore else "operator-a.env"),
            port=_free_port(),
            database=database,
            database_user=user,
        )
        return target

    def _register_product(self, target: Target) -> ProductCoordinates:
        email = f"p59p-{uuid.uuid4().hex[:10]}@example.com"
        password = f"P5p!{secrets.token_hex(12)}"
        _, registration = self.request(
            target,
            "POST",
            "/api/v1/auth/register",
            payload={
                "email": email,
                "password": password,
                "tenant_name": "P5.9 Personal",
            },
            expected=(201,),
        )
        _, login = self.request(
            target,
            "POST",
            "/api/v1/auth/login",
            payload={"email": email, "password": password},
        )
        token = str(login["access_token"])
        tenant_id = str(registration["tenant"]["id"])
        owner_user_id = str(registration["user"]["id"])
        _, template = self.request(
            target,
            "POST",
            "/api/v1/workspace-templates",
            token=token,
            payload={
                "template_key": "personal.acceptance",
                "version": 1,
                "display_name": "Personal Acceptance",
                "template_spec": {"profile": "personal_single_owner", "tools": []},
                "supersedes_template_id": None,
            },
            expected=(201,),
        )
        _, workspace = self.request(
            target,
            "POST",
            "/api/v1/workspaces",
            token=token,
            idempotency_key=f"p59-workspace-{uuid.uuid4().hex}",
            payload={
                "display_name": "P5.9 Personal Workspace",
                "template_id": template["id"],
                "parent_workspace_id": None,
                "quota": {"max_active_runs": 8},
            },
            expected=(201,),
        )
        workspace_id = str(workspace["id"])
        _, agent = self.request(
            target,
            "POST",
            f"/api/v1/workspaces/{workspace_id}/agents",
            token=token,
            idempotency_key=f"p59-agent-{uuid.uuid4().hex}",
            payload={
                "display_name": "Personal Acceptance Agent",
                "role_description": "A bounded personal assistant",
                "instructions": "Stay within the sealed no-tool personal boundary.",
                "assistant_tone": "concise",
                "provider_policy": "user_default",
                "knowledge_mode": "workspace_read_only",
                "max_context_tokens": 4096,
                "max_output_tokens": 1024,
                "max_wall_clock_seconds": 120,
                "install_immediately": True,
            },
            expected=(201,),
        )
        return ProductCoordinates(
            access_token=token,
            email=email,
            password=password,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            agent_version_id=str(agent["version"]["agent_version_id"]),
        )

    def _write_canary(
        self, coordinates: ProductCoordinates, readiness_sha256: str
    ) -> str:
        canary_id = str(uuid.uuid4())
        payload = {
            "agent_planner_enabled": False,
            "agent_version_id": coordinates.agent_version_id,
            "canary_id": canary_id,
            "enterprise_approved_digest_present": False,
            "environment": "production",
            "external_side_effects": False,
            "invocation_mode": "no_tool",
            "max_canary_seconds": 900,
            "max_concurrent_invocations": 1,
            "max_top_k": 5,
            "migration_0013_created": True,
            "migration_head": "0014",
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
        return canary_id

    def _stream(
        self,
        target: Target,
        coordinates: ProductCoordinates,
        *,
        message: str,
        idempotency_key: str,
        retry_of: str | None = None,
        callback: Callable[[dict[str, Any], float], None] | None = None,
        timeout: float = 160,
    ) -> list[tuple[dict[str, Any], float]]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", target.port, timeout=timeout
        )
        body = json.dumps(
            {
                "agent_version_id": coordinates.agent_version_id,
                "message": message,
                "top_k": 1,
                "retry_of": retry_of,
            },
            separators=(",", ":"),
        )
        connection.request(
            "POST",
            f"/api/v1/workspaces/{coordinates.workspace_id}/agent-alpha/invoke",
            body=body,
            headers={
                "Authorization": f"Bearer {coordinates.access_token}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "Idempotency-Key": idempotency_key,
            },
        )
        response = connection.getresponse()
        if response.status != 200:
            raw = response.read()
            connection.close()
            raise AcceptanceError(f"invoke returned {response.status}: {raw[:2000]!r}")
        content_type = (
            response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        )
        if content_type != "text/event-stream":
            connection.close()
            raise AcceptanceError(
                f"invoke returned unexpected Content-Type {content_type!r}"
            )
        events: list[tuple[dict[str, Any], float]] = []
        event_name: str | None = None
        data_lines: list[str] = []
        terminal_seen = False

        def flush() -> bool:
            nonlocal event_name, data_lines, terminal_seen
            if event_name is None and not data_lines:
                return False
            data = json.loads("\n".join(data_lines)) if data_lines else {}
            event = {"event": event_name or "message", "data": data}
            observed_at = time.monotonic()
            events.append((event, observed_at))
            if callback is not None:
                callback(event, observed_at)
            terminal = event["event"] in {"done", "cancelled", "error"}
            terminal_seen = terminal_seen or terminal
            event_name = None
            data_lines = []
            return terminal

        try:
            while True:
                raw_line = response.readline()
                if not raw_line:
                    flush()
                    break
                line = raw_line.decode("utf-8").rstrip("\r\n")
                if not line:
                    if flush():
                        break
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
        finally:
            connection.close()
        if not terminal_seen:
            raise AcceptanceError("SSE stream ended without a terminal event")
        return events

    @staticmethod
    def _event(events: list[tuple[dict[str, Any], float]], name: str) -> dict[str, Any]:
        for event, _ in events:
            if event["event"] == name:
                return event
        raise AcceptanceError(f"SSE event {name!r} was not observed")

    def _threaded_stream(
        self,
        target: Target,
        coordinates: ProductCoordinates,
        *,
        message: str,
        idempotency_key: str,
        retry_of: str | None = None,
    ) -> tuple[threading.Thread, threading.Event, dict[str, Any]]:
        first_chunk = threading.Event()
        shared: dict[str, Any] = {"events": [], "error": None, "task_id": None}

        def callback(event: dict[str, Any], _observed_at: float) -> None:
            if event["event"] == "meta":
                shared["task_id"] = event["data"]["task_id"]
            if event["event"] == "chunk":
                first_chunk.set()

        def run() -> None:
            try:
                shared["events"] = self._stream(
                    target,
                    coordinates,
                    message=message,
                    idempotency_key=idempotency_key,
                    retry_of=retry_of,
                    callback=callback,
                )
            except Exception as exc:  # crash test deliberately tears down the transport
                shared["error"] = exc

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread, first_chunk, shared

    @staticmethod
    def _assert_new_identities(old: dict[str, Any], new: dict[str, Any]) -> None:
        fields = (
            "task_id",
            "attempt_id",
            "task_lease_id",
            "effect_id",
            "operation_id",
            "agent_run_id",
            "workspace_run_id",
            "runtime_instance_id",
            "workload_identity_digest",
            "run_lease_id",
        )
        for field in fields:
            if not old.get(field) or not new.get(field) or old[field] == new[field]:
                raise AcceptanceError(f"explicit retry did not create a new {field}")

    def _provider_stats(self, target: Target) -> dict[str, Any]:
        return dict(self.fixture_command(target, ["provider-stats"])["provider_stats"])

    def _assert_service_stopped(self, target: Target, service: str) -> None:
        for _attempt in range(2):
            running = self.compose(
                target,
                ["ps", "--status", "running", "-q", service],
                runtime=True,
                timeout=30,
            ).stdout.strip()
            if running:
                raise AcceptanceError(
                    f"{service} restarted after the forced interruption"
                )
            time.sleep(2)

    def _start_a(self, target: Target) -> None:
        self.compose(target, ["config", "--quiet"], timeout=60)
        self.compose(target, ["build", "backend", "frontend"], timeout=1800)
        self.compose(
            target,
            ["up", "-d", "--wait", "frontend", "fake-provider"],
            timeout=600,
        )
        self.wait_http(target)

    def _enable_runtime(self, target: Target) -> None:
        self.compose(
            target,
            ["up", "-d", "--no-deps", "--force-recreate", "--wait", "backend"],
            runtime=True,
            timeout=300,
        )
        self.compose(
            target,
            ["up", "-d", "--no-deps", "--force-recreate", "--wait", "frontend"],
            runtime=True,
            timeout=300,
        )
        self.wait_http(target)

    def _disable_runtime(self, target: Target) -> None:
        self.compose(
            target,
            ["up", "-d", "--no-deps", "--force-recreate", "--wait", "backend"],
            runtime=False,
            timeout=300,
        )
        self.compose(
            target,
            ["up", "-d", "--no-deps", "--force-recreate", "--wait", "frontend"],
            runtime=False,
            timeout=300,
        )
        self.wait_http(target)

    def _cold_dump(self, target: Target) -> tuple[Path, str]:
        self.compose(target, ["stop", "frontend", "backend"], timeout=120)
        self.compose(
            target,
            [
                "exec",
                "-T",
                "postgres",
                "pg_dump",
                "-U",
                target.database_user,
                "-d",
                target.database,
                "--format=custom",
                "--file=/tmp/p5-9p.dump",
            ],
            timeout=300,
        )
        self.compose(
            target,
            ["exec", "-T", "postgres", "pg_restore", "--list", "/tmp/p5-9p.dump"],
            timeout=120,
        )
        container = self.compose(
            target, ["ps", "-q", "postgres"], timeout=30
        ).stdout.strip()
        if not container:
            raise AcceptanceError("source PostgreSQL container is unavailable")
        self.backup_root.mkdir(parents=True, exist_ok=False)
        dump_path = self.backup_root / "database.dump"
        _run(
            ["docker", "cp", f"{container}:/tmp/p5-9p.dump", str(dump_path)],
            cwd=self.repo,
            timeout=120,
        )
        self.compose(
            target,
            ["exec", "-T", "postgres", "rm", "-f", "/tmp/p5-9p.dump"],
            timeout=30,
        )
        digest = hashlib.sha256(dump_path.read_bytes()).hexdigest()
        (self.backup_root / "manifest.json").write_bytes(
            _canonical(
                {
                    "database": target.database,
                    "dump_sha256": digest,
                    "migration_head": "0014",
                    "redis_authoritative": False,
                    "runtime_enabled_at_backup": False,
                    "schema": "omnibase.p5-9p-cold-backup.v1",
                }
            )
        )
        return dump_path, digest

    def _database_fingerprint(self, target: Target) -> str:
        table_names = (
            "tenants",
            "workspaces",
            "agent_definitions",
            "agent_versions",
            "agent_tasks",
            "agent_attempts",
            "agent_task_effects",
            "agent_reconciliation_cases",
            "agent_runs",
            "workspace_runs",
            "run_leases",
        )
        expressions = [
            "'migration',(SELECT version_num FROM omnibase_meta.alembic_version)"
        ]
        for table in table_names:
            expressions.append(
                f"'{table}',(SELECT COALESCE(jsonb_agg(to_jsonb(item) ORDER BY item.id),"
                f"'[]'::jsonb) FROM omnibase_meta.{table} item)"
            )
        sql = f"SELECT md5(jsonb_build_object({','.join(expressions)})::text)"
        result = self.compose(
            target,
            [
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                target.database_user,
                "-d",
                target.database,
                "-Atc",
                sql,
            ],
            timeout=120,
        ).stdout.strip()
        if len(result) != 32 or any(
            character not in "0123456789abcdef" for character in result
        ):
            raise AcceptanceError("source database fingerprint is invalid")
        return result

    def _restore_b(
        self,
        source: Target,
        restored: Target,
        dump_path: Path,
        coordinates: ProductCoordinates,
        source_fingerprint_before: str,
        memory_id: str,
        skill_version_id: str,
    ) -> None:
        self.base_compose_only(restored, ["config", "--quiet"], timeout=60)
        self.base_compose_only(
            restored, ["up", "-d", "--wait", "postgres"], timeout=300
        )
        container = self.base_compose_only(
            restored, ["ps", "-q", "postgres"], timeout=30
        ).stdout.strip()
        if not container:
            raise AcceptanceError("restore PostgreSQL container is unavailable")
        _run(
            ["docker", "cp", str(dump_path), f"{container}:/tmp/p5-9p-restore.dump"],
            cwd=self.repo,
            timeout=120,
        )
        self.base_compose_only(
            restored,
            [
                "exec",
                "-T",
                "postgres",
                "pg_restore",
                "-U",
                restored.database_user,
                "-d",
                restored.database,
                "--no-owner",
                "--no-privileges",
                "--exit-on-error",
                "/tmp/p5-9p-restore.dump",
            ],
            timeout=300,
        )
        self.base_compose_only(
            restored,
            ["exec", "-T", "postgres", "rm", "-f", "/tmp/p5-9p-restore.dump"],
            timeout=30,
        )
        self.base_compose_only(
            restored,
            ["up", "-d", "--wait", "frontend"],
            timeout=600,
        )
        self.wait_http(restored)
        _, login = self.request(
            restored,
            "POST",
            "/api/v1/auth/login",
            payload={"email": coordinates.email, "password": coordinates.password},
        )
        token = str(login["access_token"])
        _, workspaces = self.request(restored, "GET", "/api/v1/workspaces", token=token)
        ids = {str(item["id"]) for item in workspaces["items"]}
        if coordinates.workspace_id not in ids:
            raise AcceptanceError(
                "restored target did not preserve the personal Workspace"
            )
        restored_product = self.fixture_command(
            restored,
            [
                "inspect-restored",
                "--tenant-id",
                coordinates.tenant_id,
                "--owner-user-id",
                coordinates.owner_user_id,
                "--workspace-id",
                coordinates.workspace_id,
                "--agent-version-id",
                coordinates.agent_version_id,
                "--memory-id",
                memory_id,
                "--skill-version-id",
                skill_version_id,
            ],
        )
        if (
            not restored_product["memory_present"]
            or not restored_product["skill_present"]
        ):
            raise AcceptanceError(
                f"restore-new lost personal Memory or Skill: {restored_product}"
            )
        _, status = self.request(
            restored,
            "GET",
            f"/api/v1/workspaces/{coordinates.workspace_id}/agent-alpha/status",
            token=token,
        )
        if status["personal_runtime_active"] is not False:
            raise AcceptanceError("restored target activated Runtime")
        self.request(
            restored,
            "POST",
            f"/api/v1/workspaces/{coordinates.workspace_id}/agent-alpha/invoke",
            token=token,
            idempotency_key=f"p59-restored-runtime-off-{uuid.uuid4().hex}",
            payload={
                "agent_version_id": coordinates.agent_version_id,
                "message": "Runtime must remain disabled after restore-new",
                "top_k": 1,
                "retry_of": None,
            },
            expected=(503,),
        )
        head = self.base_compose_only(
            restored,
            [
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                restored.database_user,
                "-d",
                restored.database,
                "-Atc",
                "SELECT version_num FROM omnibase_meta.alembic_version",
            ],
            timeout=60,
        ).stdout.strip()
        if head != "0014":
            raise AcceptanceError(
                f"restored migration head is {head!r}, expected '0014'"
            )
        source_fingerprint_after = self._database_fingerprint(source)
        source_unchanged = (
            source.project != restored.project
            and source.database != restored.database
            and source_fingerprint_after == source_fingerprint_before
        )
        if not source_unchanged:
            raise AcceptanceError(
                "restore-new changed the source database or reused its identity"
            )
        self.receipt["restore_new"] = {
            "authenticated_smoke": True,
            "database": restored.database,
            "migration_head": head,
            "runtime_enabled": False,
            "source_database_fingerprint": source_fingerprint_after,
            "source_database_unchanged": source_unchanged,
            "memory_preserved": True,
            "skill_preserved": True,
            "workspace_preserved": True,
        }

    def execute(self) -> dict[str, Any]:
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            self.work_root.chmod(0o755)
            self.state_dir.chmod(0o777)
        self.backup_root.parent.mkdir(parents=True, exist_ok=True)
        self.canary_config.write_bytes(b"{}\n")
        if os.name != "nt":
            self.canary_config.chmod(0o644)
        readiness_sha256 = self._prepare_readiness_root()
        suffix = uuid.uuid4().hex[:8]
        source = self._create_target(suffix=suffix)
        restored = self._create_target(suffix=suffix, restore=True)
        self.targets.extend((source, restored))
        database_password = secrets.token_hex(20)
        redis_password = secrets.token_hex(20)
        minio_password = secrets.token_hex(20)
        jwt_secret = secrets.token_hex(32)
        provider_key = _secret_key()
        memory_key = _secret_key()
        restored_database_password = secrets.token_hex(20)
        restored_redis_password = secrets.token_hex(20)
        restored_minio_password = secrets.token_hex(20)
        restored_jwt_secret = secrets.token_hex(32)
        self.redaction_values.update(
            {
                database_password,
                redis_password,
                minio_password,
                jwt_secret,
                provider_key,
                memory_key,
                restored_database_password,
                restored_redis_password,
                restored_minio_password,
                restored_jwt_secret,
            }
        )
        _write_operator_env(
            source.env_file,
            port=source.port,
            database=source.database,
            database_user=source.database_user,
            deployment_id=str(uuid.uuid4()),
            password=database_password,
            redis_password=redis_password,
            minio_password=minio_password,
            jwt_secret=jwt_secret,
            provider_key=provider_key,
            memory_key=memory_key,
        )
        _write_operator_env(
            restored.env_file,
            port=restored.port,
            database=restored.database,
            database_user=restored.database_user,
            deployment_id=str(uuid.uuid4()),
            password=restored_database_password,
            redis_password=restored_redis_password,
            minio_password=restored_minio_password,
            jwt_secret=restored_jwt_secret,
            provider_key=provider_key,
            memory_key=memory_key,
        )

        self._start_a(source)
        coordinates = self._register_product(source)
        self.redaction_values.update({coordinates.access_token, coordinates.password})
        skill = self.fixture_command(
            source,
            [
                "install-skill",
                "--tenant-id",
                coordinates.tenant_id,
                "--owner-user-id",
                coordinates.owner_user_id,
                "--workspace-id",
                coordinates.workspace_id,
                "--agent-version-id",
                coordinates.agent_version_id,
            ],
        )
        canary_id = self._write_canary(coordinates, readiness_sha256)
        activation = self.fixture_command(source, ["activate"])
        if (
            activation.get("canary_id") != canary_id
            or activation.get("state") != "active"
        ):
            raise AcceptanceError(
                f"personal Runtime activation did not converge: {activation}"
            )
        self._enable_runtime(source)
        _, posture = self.request(
            source,
            "GET",
            f"/api/v1/workspaces/{coordinates.workspace_id}/agent-alpha/status",
            token=coordinates.access_token,
        )
        if (
            not posture["personal_runtime_active"]
            or not posture["production_activation_allowed"]
        ):
            raise AcceptanceError(f"personal Runtime did not activate: {posture}")

        first_events = self._stream(
            source,
            coordinates,
            message="P5_FIRST_STREAM verify incremental SSE and sealed Skill",
            idempotency_key=f"p59-first-{uuid.uuid4().hex}",
        )
        first_meta = self._event(first_events, "meta")["data"]
        first_chunks = [
            (event, observed)
            for event, observed in first_events
            if event["event"] == "chunk"
        ]
        if len(first_chunks) < 3:
            raise AcceptanceError("incremental SSE produced fewer than three chunks")
        gaps = [
            first_chunks[index + 1][1] - first_chunks[index][1]
            for index in range(len(first_chunks) - 1)
        ]
        if min(gaps) < 0.15:
            raise AcceptanceError(f"SSE chunks were not observably separated: {gaps}")
        if first_meta.get("skill_count") != 1:
            raise AcceptanceError(
                "sealed Skill was not projected into the first invocation"
            )
        first_task_id = str(first_meta["task_id"])

        memory = self.fixture_command(
            source,
            [
                "publish-memory",
                "--tenant-id",
                coordinates.tenant_id,
                "--owner-user-id",
                coordinates.owner_user_id,
                "--workspace-id",
                coordinates.workspace_id,
                "--agent-version-id",
                coordinates.agent_version_id,
                "--source-task-id",
                first_task_id,
            ],
        )
        memory_events = self._stream(
            source,
            coordinates,
            message="P5_MEMORY_AND_SKILL verify combined personal context",
            idempotency_key=f"p59-memory-{uuid.uuid4().hex}",
        )
        memory_meta = self._event(memory_events, "meta")["data"]
        if memory_meta.get("context_capsule_item_count") != 1:
            raise AcceptanceError("encrypted scoped Memory was not projected")
        if memory_meta.get("skill_count") != 1:
            raise AcceptanceError("Skill disappeared from the combined invocation")
        stats = self._provider_stats(source)
        if not stats.get("saw_memory_marker") or not stats.get("saw_skill_marker"):
            raise AcceptanceError(
                f"fake Provider did not observe Memory+Skill projection: {stats}"
            )

        cancel_key = f"p59-cancel-{uuid.uuid4().hex}"
        cancel_thread, cancel_chunk, cancel_shared = self._threaded_stream(
            source,
            coordinates,
            message="P5_CANCEL verify durable cancellation",
            idempotency_key=cancel_key,
        )
        if not cancel_chunk.wait(timeout=30):
            raise AcceptanceError("cancel invocation did not produce its first chunk")
        cancel_task_id = str(cancel_shared["task_id"])
        self.request(
            source,
            "POST",
            f"/api/v1/workspaces/{coordinates.workspace_id}/agent-alpha/invocations/{cancel_task_id}/cancel",
            token=coordinates.access_token,
        )
        cancel_thread.join(timeout=30)
        cancel_events = cancel_shared["events"]
        self._event(cancel_events, "cancelled")
        cancel_state = self.fixture_command(
            source,
            [
                "inspect-task",
                "--tenant-id",
                coordinates.tenant_id,
                "--task-id",
                cancel_task_id,
            ],
        )
        if cancel_state["task_state"] != "cancelled":
            raise AcceptanceError(f"cancelled Task did not converge: {cancel_state}")

        crash_key = f"p59-crash-{uuid.uuid4().hex}"
        crash_thread, crash_chunk, crash_shared = self._threaded_stream(
            source,
            coordinates,
            message="P5_CRASH_HOLD simulate Core interruption",
            idempotency_key=crash_key,
        )
        if not crash_chunk.wait(timeout=30):
            raise AcceptanceError("crash invocation did not produce its first chunk")
        crash_task_id = str(crash_shared["task_id"])
        old_identity = self.fixture_command(
            source,
            [
                "inspect-task",
                "--tenant-id",
                coordinates.tenant_id,
                "--task-id",
                crash_task_id,
            ],
        )
        calls_before_recovery = int(self._provider_stats(source)["call_count"])
        self.compose(
            source, ["kill", "-s", "SIGKILL", "backend"], runtime=True, timeout=30
        )
        crash_thread.join(timeout=15)
        self._assert_service_stopped(source, "backend")
        time.sleep(self.lease_wait_seconds)
        self.compose(
            source,
            ["up", "-d", "--no-deps", "--wait", "backend"],
            runtime=True,
            timeout=300,
        )
        self.wait_http(source)
        replay_events = self._stream(
            source,
            coordinates,
            message="P5_CRASH_HOLD simulate Core interruption",
            idempotency_key=crash_key,
        )
        replay_error = self._event(replay_events, "error")["data"]
        if replay_error.get("code") != "agent_alpha_exact_replay":
            raise AcceptanceError(
                f"expired exact replay returned the wrong result: {replay_error}"
            )
        calls_after_recovery = int(self._provider_stats(source)["call_count"])
        if calls_after_recovery != calls_before_recovery:
            raise AcceptanceError(
                "expired invocation automatically replayed the Provider"
            )
        recovered_old = self.fixture_command(
            source,
            [
                "inspect-task",
                "--tenant-id",
                coordinates.tenant_id,
                "--task-id",
                crash_task_id,
            ],
        )
        if (
            recovered_old["task_state"] != "blocked_unknown"
            or recovered_old["effect_state"] != "unknown"
            or recovered_old["open_reconciliation_count"] != 1
        ):
            raise AcceptanceError(
                f"expired invocation did not converge once: {recovered_old}"
            )

        retry_key = f"p59-retry-{uuid.uuid4().hex}"
        retry_thread, retry_chunk, retry_shared = self._threaded_stream(
            source,
            coordinates,
            message="P5_RETRY_HOLD explicit Owner retry",
            idempotency_key=retry_key,
            retry_of=crash_task_id,
        )
        if not retry_chunk.wait(timeout=30):
            raise AcceptanceError("explicit retry did not produce its first chunk")
        retry_task_id = str(retry_shared["task_id"])
        retry_identity = self.fixture_command(
            source,
            [
                "inspect-task",
                "--tenant-id",
                coordinates.tenant_id,
                "--task-id",
                retry_task_id,
            ],
        )
        self._assert_new_identities(old_identity, retry_identity)
        retry_thread.join(timeout=45)
        if retry_shared["error"] is not None:
            raise AcceptanceError(
                f"explicit retry stream failed: {retry_shared['error']}"
            )
        self._event(retry_shared["events"], "done")
        retry_terminal = self.fixture_command(
            source,
            [
                "inspect-task",
                "--tenant-id",
                coordinates.tenant_id,
                "--task-id",
                retry_task_id,
            ],
        )
        if retry_terminal["task_state"] != "succeeded":
            raise AcceptanceError(f"explicit retry did not succeed: {retry_terminal}")

        calls_before_kill = int(self._provider_stats(source)["call_count"])
        kill = self.fixture_command(source, ["kill"])
        self.request(
            source,
            "POST",
            f"/api/v1/workspaces/{coordinates.workspace_id}/agent-alpha/invoke",
            token=coordinates.access_token,
            idempotency_key=f"p59-after-kill-{uuid.uuid4().hex}",
            payload={
                "agent_version_id": coordinates.agent_version_id,
                "message": "Provider must not run after kill",
                "top_k": 1,
                "retry_of": None,
            },
            expected=(503,),
        )
        if int(self._provider_stats(source)["call_count"]) != calls_before_kill:
            raise AcceptanceError("kill switch admitted a new Provider call")
        self._disable_runtime(source)
        _, disabled_posture = self.request(
            source,
            "GET",
            f"/api/v1/workspaces/{coordinates.workspace_id}/agent-alpha/status",
            token=coordinates.access_token,
        )
        if disabled_posture["personal_runtime_active"] is not False:
            raise AcceptanceError("deployment-layer Runtime=false was not restored")

        dump_path, dump_sha256 = self._cold_dump(source)
        source_fingerprint = self._database_fingerprint(source)
        self._restore_b(
            source,
            restored,
            dump_path,
            coordinates,
            source_fingerprint,
            str(memory["memory_id"]),
            str(skill["skill_version_id"]),
        )
        self.receipt.update(
            {
                "acceptance": "P5_9P_PERSONAL_PRODUCTION_LIKE_ACCEPTANCE_PASSED",
                "canary_id": canary_id,
                "cold_backup": {
                    "dump_sha256": dump_sha256,
                    "redis_archived": False,
                    "runtime_enabled": False,
                    "writers_stopped": True,
                },
                "incremental_sse": {
                    "chunk_count": len(first_chunks),
                    "gaps_seconds": gaps,
                },
                "kill_switch": {"provider_call_blocked": True, **kill},
                "memory": {
                    "context_capsule_item_count": 1,
                    "memory_id": memory["memory_id"],
                },
                "personal_scope": {
                    "agent_version_id": coordinates.agent_version_id,
                    "owner_user_id": coordinates.owner_user_id,
                    "tenant_id": coordinates.tenant_id,
                    "workspace_id": coordinates.workspace_id,
                },
                "restart_recovery": {
                    "old_task_id": crash_task_id,
                    "old_task_state": "blocked_unknown",
                    "provider_replayed": False,
                    "reconciliation_count": 1,
                    "retry_task_id": retry_task_id,
                    "retry_task_state": "succeeded",
                },
                "runtime_enabled_after_acceptance": False,
                "skill": {
                    "skill_count": 1,
                    "skill_version_id": skill["skill_version_id"],
                },
            }
        )
        _assert_receipt_safe(self.receipt)
        self.receipt_path.write_bytes(_canonical(self.receipt))
        return self.receipt

    def _project_resources(self, target: Target) -> dict[str, list[str]]:
        label = f"label=com.docker.compose.project={target.project}"
        commands = {
            "containers": ["docker", "ps", "-aq", "--filter", label],
            "networks": ["docker", "network", "ls", "-q", "--filter", label],
            "volumes": ["docker", "volume", "ls", "-q", "--filter", label],
        }
        resources: dict[str, list[str]] = {}
        for kind, command in commands.items():
            result = _run(command, cwd=self.repo, timeout=60, check=False)
            if result.returncode != 0:
                resources[kind] = ["query_failed"]
            else:
                resources[kind] = [line for line in result.stdout.splitlines() if line]
        return resources

    def _remove_sensitive_artifacts(self) -> list[str]:
        errors: list[str] = []
        root = self.work_root.resolve()
        paths = [
            *(target.env_file for target in self.targets),
            self.canary_config,
            self.state_dir,
            self.readiness_root,
            self.backup_root,
        ]
        for path in paths:
            resolved = path.resolve()
            if resolved == root or root not in resolved.parents:
                errors.append(f"refused cleanup outside work root: {path.name}")
                continue
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                errors.append(f"failed to remove sensitive artifact: {path.name}")
        return errors

    def cleanup(self) -> tuple[str, ...]:
        errors: list[str] = []
        for target in reversed(self.targets):
            try:
                result = self.compose(
                    target,
                    ["down", "-v", "--remove-orphans"],
                    timeout=300,
                    check=False,
                )
                if result.returncode != 0:
                    errors.append(f"Compose cleanup failed for {target.project}")
                    continue
                remaining = self._project_resources(target)
                leaked = [kind for kind, values in remaining.items() if values]
                if leaked:
                    errors.append(
                        f"Compose cleanup left resources for {target.project}: {','.join(leaked)}"
                    )
            except (AcceptanceError, OSError, subprocess.SubprocessError):
                errors.append(f"Compose cleanup raised for {target.project}")
        errors.extend(self._remove_sensitive_artifacts())
        return tuple(errors)


def _args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--work-root")
    parser.add_argument(
        "--lease-wait-seconds",
        type=int,
        default=95,
        help="Real wait after SIGKILL; must exceed the 90 second personal TaskLease TTL.",
    )
    parser.add_argument("--keep-projects-on-failure", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _args(argv)
    repo = Path(args.repo_root).resolve(strict=True)
    if args.lease_wait_seconds < 91:
        raise SystemExit("--lease-wait-seconds must exceed the 90 second TaskLease TTL")
    work_root = (
        Path(args.work_root).resolve()
        if args.work_root
        else Path(tempfile.mkdtemp(prefix="omnibase-p5-9p-acceptance-"))
    )
    journey = Journey(
        repo=repo,
        work_root=work_root,
        lease_wait_seconds=args.lease_wait_seconds,
    )
    succeeded = False
    output: dict[str, object]
    exit_code = 1
    try:
        receipt = journey.execute()
        output = {"receipt": receipt, "work_root": str(work_root)}
        succeeded = True
        exit_code = 0
    except (AcceptanceError, OSError, subprocess.SubprocessError, ValueError) as exc:
        output = {
            "error": journey.redact_error(str(exc)),
            "receipt": journey.receipt,
            "state": "failed/veto",
            "work_root": str(work_root),
        }
    if succeeded or not args.keep_projects_on_failure:
        cleanup_errors = journey.cleanup()
        if cleanup_errors:
            output = {
                "cleanup_errors": list(cleanup_errors),
                "error": "disposable acceptance cleanup did not converge",
                "receipt": journey.receipt,
                "state": "failed/veto",
                "work_root": str(work_root),
            }
            exit_code = 1
    print(_canonical(output).decode(), end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
