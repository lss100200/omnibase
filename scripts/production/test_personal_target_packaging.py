from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile.production"
COMPOSE = REPO_ROOT / "deployment" / "personal-production" / "compose.yml"
OPERATOR_ENV = REPO_ROOT / "deployment" / "personal-production" / "operator.env.example"

SERVICES = (
    "postgres",
    "redis-init",
    "redis",
    "minio",
    "minio-init",
    "migrate",
    "backend",
    "frontend",
)


def _service_block(compose: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(?P<body>.*?)(?=^  [a-z][a-z0-9-]*:\n|^networks:\n)",
        compose,
    )
    assert match is not None, f"missing service: {name}"
    return match.group("body")


def _env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in OPERATOR_ENV.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        assert separator, f"invalid operator env line: {line!r}"
        values[key] = value
    return values


def test_backend_production_image_is_non_root_and_runtime_minimal() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    final_stage = dockerfile.rsplit("\nFROM ", maxsplit=1)[1].lower()

    assert " as production" in final_stage.splitlines()[0]
    assert "user omnibase:omnibase" in final_stage
    assert 'cmd ["uvicorn", "omnibase.main:app"' in final_stage
    assert '"--workers", "1"' in final_stage
    assert "--reload" not in final_stage

    forbidden_runtime_tools = ("build-essential", "gcc", "g++", "git", "wget")
    for tool in forbidden_runtime_tools:
        assert tool not in final_stage


def test_compose_uses_production_images_without_source_mounts() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    backend = _service_block(compose, "backend")
    frontend = _service_block(compose, "frontend")

    assert "dockerfile: Dockerfile.production" in backend
    assert "target: production" in backend
    assert "target: production" in frontend
    assert "type: bind" not in compose
    assert re.search(r"(?m)^\s*-\s+\./", compose) is None


def test_frontend_is_the_only_loopback_host_entrypoint() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    services_with_ports = [
        name for name in SERVICES if "\n    ports:\n" in _service_block(compose, name)
    ]

    assert services_with_ports == ["frontend"]
    frontend = _service_block(compose, "frontend")
    assert "127.0.0.1:${OMNIBASE_FRONTEND_PORT:?" in frontend
    assert ":3000" in frontend
    for name in ("postgres", "redis", "minio", "backend"):
        assert "\n    ports:\n" not in _service_block(compose, name)


def test_migration_is_one_shot_and_gates_backend_start() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    migrate = _service_block(compose, "migrate")
    backend = _service_block(compose, "backend")

    assert 'restart: "no"' in migrate
    assert 'command: ["alembic", "upgrade", "head"]' in migrate
    assert "migrate:" in backend
    assert "condition: service_completed_successfully" in backend


def test_redis_volume_ownership_is_initialized_before_non_root_runtime() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    init = _service_block(compose, "redis-init")
    redis = _service_block(compose, "redis")

    assert 'restart: "no"' in init
    assert 'user: "0:0"' in init
    assert 'command: ["sh", "-ec", "chown redis:redis /data"]' in init
    assert "- CHOWN" in init
    assert "user: redis" in redis
    assert "redis-init:" in redis
    assert "condition: service_completed_successfully" in redis
    assert "cap_add:" not in redis


def test_runtime_and_orchestration_are_fail_closed_by_default() -> None:
    backend = _service_block(COMPOSE.read_text(encoding="utf-8"), "backend")
    for name in (
        "AGENT_RUNTIME_ENABLED",
        "AGENT_PLANNER_ENABLED",
        "MULTI_AGENT_ENABLED",
        "AGENT_ALPHA_ENGINEERING_ENABLED",
    ):
        assert f'{name}: "false"' in backend
    assert (
        "OMNIBASE_DEPLOYMENT_INSTANCE_ID: ${OMNIBASE_DEPLOYMENT_INSTANCE_ID:?"
        in backend
    )
    for name in (
        "PERSONAL_RUNTIME_PROFILE",
        "PERSONAL_RUNTIME_CANARY_CONFIG",
        "PERSONAL_RUNTIME_STATE_DIR",
        "PERSONAL_RUNTIME_READINESS_ROOT",
    ):
        assert f'{name}: ""' in backend


def test_every_service_has_explicit_container_hardening() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    for name in SERVICES:
        block = _service_block(compose, name)
        assert "read_only: true" in block, name
        assert "tmpfs:" in block, name
        assert "cap_drop:" in block and "- ALL" in block, name
        assert "no-new-privileges:true" in block, name


def test_required_operator_values_fail_closed_and_example_contains_no_secret() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    assert "${" in compose
    assert ":-" not in compose
    for expression in re.findall(r"\$\{([^}]+)\}", compose):
        assert ":?" in expression, expression

    values = _env_values()
    secret_values = {
        key: value
        for key, value in values.items()
        if key
        in {
            "POSTGRES_PASSWORD",
            "DATABASE_URL",
            "MINIO_ROOT_PASSWORD",
            "REDIS_PASSWORD",
            "REDIS_URL",
            "JWT_SECRET",
            "PROVIDER_CREDENTIAL_ENCRYPTION_KEY",
        }
    }
    assert secret_values
    for key, value in secret_values.items():
        assert "REPLACE_WITH_" in value, key
