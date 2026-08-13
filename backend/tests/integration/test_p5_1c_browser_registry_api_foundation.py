"""Guarded PostgreSQL acceptance tests for the P5.1C Browser Agent Registry control API.

Runs against a disposable sentinel database through the real Browser router
with the DB-backed control plane injected: API-backed install/upgrade/disable/
rollback, exact replay, digest drift, cross-tenant rejection, live membership,
approval single consumption, concurrency single-winner, audit append-only and
rollback atomicity.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from omnibase.agent_registry.control import AgentRegistryControlService
from omnibase.agent_registry.router import (
    builder_router,
    get_registry_control_plane,
    installation_router,
    router,
)
from omnibase.production.phase5_registry_contract import BindingState
from omnibase.tenants.dependencies import get_current_tenant, get_tenant_db

if os.environ.get("OMNIBASE_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "P5.1C integration tests require OMNIBASE_INTEGRATION_TESTS=1",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration
_BACKEND_ROOT = Path(__file__).resolve().parents[2]

ACTOR_ID = "00000000-0000-0000-0000-0000000000aa"
MEMBER_ID = "00000000-0000-0000-0000-0000000000bb"
MODEL_POLICY_ID = "00000000-0000-0000-0000-0000000000cc"
MEMORY_POLICY_ID = "00000000-0000-0000-0000-0000000000dd"
INSTRUCTIONS_DIGEST = "3333333333333333333333333333333333333333333333333333333333333333"


def _run_alembic(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=_BACKEND_ROOT,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )


def _upgrade_head() -> None:
    result = _run_alembic("upgrade", "head")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(scope="module", autouse=True)
def p51c_schema(db_engine) -> None:
    _upgrade_head()


def _tenant(connection, run_owned_resources, label: str) -> str:
    tenant_id = str(uuid.uuid4())
    suffix = uuid.uuid4().hex[:8]
    schema_name = f"tenant_{suffix}"
    connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    connection.execute(
        text(
            "INSERT INTO omnibase_meta.tenants "
            "(id, name, slug, schema_name, is_default, is_active) "
            "VALUES (:id, :name, :slug, :schema, FALSE, TRUE)"
        ),
        {
            "id": tenant_id,
            "name": f"P5.1C {label}",
            "slug": f"p51c-{label}-{suffix}",
            "schema": schema_name,
        },
    )
    run_owned_resources.add(tenant_id, schema_name)
    return tenant_id


def _tenant_schema(connection, tenant_id: str) -> str:
    return str(
        connection.execute(
            text("SELECT schema_name FROM omnibase_meta.tenants WHERE id = :tenant"),
            {"tenant": tenant_id},
        ).scalar_one()
    )


def _seed_actor_user(connection, schema_name: str, label: str, user_id: str) -> None:
    connection.execute(
        text(
            f'INSERT INTO "{schema_name}".users '  # noqa: S608
            "(id, email, password_hash, is_tenant_admin, is_active) "
            "VALUES (:id, :email, :hash, TRUE, TRUE)"
        ),
        {
            "id": user_id,
            "email": f"{label}-{uuid.uuid4().hex[:8]}@example.invalid",
            "hash": uuid.uuid4().hex,
        },
    )


def _tenant_with_schema(db_engine, run_owned_resources, label: str) -> str:
    with db_engine.begin() as connection:
        tenant_id = _tenant(connection, run_owned_resources, label)
        schema_name = _tenant_schema(connection, tenant_id)
    _upgrade_head()
    with db_engine.begin() as connection:
        _seed_actor_user(connection, schema_name, label, ACTOR_ID)
        _seed_actor_user(connection, schema_name, label, MEMBER_ID)
    return tenant_id


def _template(connection, tenant_id: str) -> str:
    return str(
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_templates "
                "(tenant_id, template_key, version, display_name, digest, "
                "template_spec, created_by_user_id) "
                "VALUES (:tenant, :key, 1, 'Synthetic', :digest, "
                '\'{"profile":"metadata-only"}\'::jsonb, :actor) RETURNING id'
            ),
            {
                "tenant": tenant_id,
                "key": f"synthetic-{uuid.uuid4().hex[:8]}",
                "digest": uuid.uuid4().hex + uuid.uuid4().hex,
                "actor": ACTOR_ID,
            },
        ).scalar_one()
    )


def _workspace(connection, tenant_id: str, template_id: str, label: str) -> str:
    workspace_id = str(
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.resource_registry "
                "(tenant_id, kind, owner_type, display_name, state, policy_class) "
                "VALUES (:tenant, 'workspace', 'system', :label, 'stopped', "
                "'workspace_private') RETURNING id"
            ),
            {"tenant": tenant_id, "label": label},
        ).scalar_one()
    )
    connection.execute(
        text(
            "INSERT INTO omnibase_meta.workspaces "
            "(id, tenant_id, template_id, owner_user_id, display_name, "
            "desired_state, observed_state, quota, generation) "
            "VALUES (:id, :tenant, :template, :owner, :label, 'stopped', "
            "'stopped', CAST(:quota AS jsonb), 1)"
        ),
        {
            "id": workspace_id,
            "tenant": tenant_id,
            "template": template_id,
            "owner": ACTOR_ID,
            "label": label,
            "quota": '{"max_active_runs":1}',
        },
    )
    return workspace_id


def _membership(connection, tenant_id: str, workspace_id: str, user_id: str, role: str) -> None:
    connection.execute(
        text(
            "INSERT INTO omnibase_meta.workspace_memberships "
            "(tenant_id, workspace_id, user_id, role, state, created_by_user_id) "
            "VALUES (:tenant, :workspace, :user, :role, 'active', :user)"
        ),
        {"tenant": tenant_id, "workspace": workspace_id, "user": user_id, "role": role},
    )


def _canonical_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _definition_mapping(tenant_id: str, *, risk_level: str = "low") -> dict[str, object]:
    return {
        "schema_version": 1,
        "agent_definition_id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "stable_logical_key": f"agent-{uuid.uuid4().hex[:10]}",
        "display_name": "Gate Agent",
        "description": "Disposable P5.1C gate agent",
        "risk_level": risk_level,
        "allowed_installation_scopes": ["workspace"],
        "definition_state": "active",
        "created_by": ACTOR_ID,
        "created_at": "2026-08-03T00:00:00Z",
        "metadata_version": 1,
    }


def _version_mapping(
    tenant_id: str, definition_id: str, *, risk_level: str = "low"
) -> dict[str, object]:
    mapping: dict[str, object] = {
        "schema_version": 1,
        "agent_version_id": str(uuid.uuid4()),
        "agent_definition_id": definition_id,
        "tenant_id": tenant_id,
        "version": f"1.0.{uuid.uuid4().int % 10 ** 6}",
        "manifest_digest": "0" * 64,
        "model_policy_id": MODEL_POLICY_ID,
        "instructions_digest": INSTRUCTIONS_DIGEST,
        "max_context_tokens": 200000,
        "allowed_tool_ids": ["rag_search", "artifact_read"],
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 1}},
            "required": ["query"],
        },
        "output_schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
        "risk_level": risk_level,
        "memory_policy_id": MEMORY_POLICY_ID,
        "max_concurrency": 2,
        "default_budget": {
            "max_tokens": 100000,
            "max_cost_units": 1000,
            "max_wall_clock_seconds": 300,
            "max_tool_calls": 50,
        },
        "version_state": "sealed",
        "created_by": ACTOR_ID,
        "created_at": "2026-08-03T00:00:00Z",
    }
    mapping["manifest_digest"] = _canonical_hash(
        {k: v for k, v in mapping.items() if k != "manifest_digest"}
    )
    return mapping


def _session(db_engine, tenant_id: str) -> Session:
    """Session whose transaction resolves unqualified names against the tenant schema."""
    session = Session(db_engine, expire_on_commit=False)
    with db_engine.connect() as connection:
        schema_name = str(
            connection.execute(
                text("SELECT schema_name FROM omnibase_meta.tenants WHERE id = :tenant"),
                {"tenant": tenant_id},
            ).scalar_one()
        )
    session.execute(text(f'SET LOCAL search_path TO "{schema_name}", omnibase_meta, public'))
    return session


def _register_and_seal(
    db_engine,
    tenant_id: str,
    *,
    risk_level: str = "low",
    definition_id: str | None = None,
) -> tuple[str, dict[str, object]]:
    """Register a definition (unless one is given) and seal a new version under it."""
    from omnibase.agent_registry.service import RegistryPersistenceService
    from omnibase.production.phase5_registry_contract import (
        AgentDefinition,
        AgentVersionManifest,
        BudgetCeilings,
    )

    if definition_id is None:
        definition_mapping = _definition_mapping(tenant_id, risk_level=risk_level)
        with _session(db_engine, tenant_id) as session:
            RegistryPersistenceService(session).register_definition(
                tenant_id=tenant_id,
                actor_user_id=ACTOR_ID,
                request_id=str(uuid.uuid4()),
                definition=AgentDefinition.from_mapping(definition_mapping),
                idempotency_key=uuid.uuid4().hex,
            )
            session.commit()
        definition_id = str(definition_mapping["agent_definition_id"])
    version_mapping = _version_mapping(tenant_id, definition_id, risk_level=risk_level)
    ceilings = BudgetCeilings.from_mapping(
        {
            "max_tokens": 10_000_000,
            "max_cost_units": 100_000,
            "max_wall_clock_seconds": 3_600,
            "max_tool_calls": 1_000,
            "max_concurrency": 64,
            "max_context_tokens": 2_000_000,
        }
    ).as_mapping()
    with _session(db_engine, tenant_id) as session:
        RegistryPersistenceService(session).seal_version(
            tenant_id=tenant_id,
            actor_user_id=ACTOR_ID,
            request_id=str(uuid.uuid4()),
            version=AgentVersionManifest.from_mapping(version_mapping, ceilings=ceilings),
            idempotency_key=uuid.uuid4().hex,
        )
        session.commit()
    return definition_id, version_mapping


def _seed(
    db_engine,
    run_owned_resources,
    label: str,
    *,
    risk_level: str = "low",
) -> tuple[str, str, str, dict[str, object]]:
    with db_engine.begin() as connection:
        tenant_id = _tenant(connection, run_owned_resources, label)
        schema_name = _tenant_schema(connection, tenant_id)
        template_id = _template(connection, tenant_id)
        workspace_id = _workspace(connection, tenant_id, template_id, label)
    _upgrade_head()
    with db_engine.begin() as connection:
        _seed_actor_user(connection, schema_name, label, ACTOR_ID)
        _seed_actor_user(connection, schema_name, label, MEMBER_ID)
        _membership(connection, tenant_id, workspace_id, ACTOR_ID, "maintainer")
    definition_id, version_mapping = _register_and_seal(db_engine, tenant_id, risk_level=risk_level)
    return tenant_id, workspace_id, definition_id, version_mapping


def _approval(
    connection,
    tenant_id: str,
    workspace_id: str,
    request_hash: str,
    *,
    approval_id: str | None = None,
    risk_level: str = "R3",
    action: str = "agent.install",
) -> str:
    approval_id = approval_id or str(uuid.uuid4())
    connection.execute(
        text(
            "INSERT INTO omnibase_meta.approval_requests "
            "(id, tenant_id, requester_type, requester_id, workspace_id, "
            "action, risk_level, required_approver_role, state, request_hash, "
            "grant_id, operation_id, decided_by_actor_type, decided_by_actor_id, "
            "expires_at) "
            "VALUES (:id, :tenant, 'user', :actor, :workspace, :action, "
            ":risk, :role, 'approved', :hash, :grant, :operation, 'user', :actor, :expires)"
        ),
        {
            "id": approval_id,
            "tenant": tenant_id,
            "actor": ACTOR_ID,
            "workspace": workspace_id,
            "action": action,
            "risk": risk_level,
            "role": "platform_admin" if risk_level == "R4" else "tenant_admin",
            "hash": request_hash,
            "grant": str(uuid.uuid4()),
            "operation": str(uuid.uuid4()),
            "expires": datetime.now(UTC) + timedelta(hours=1),
        },
    )
    return approval_id


def _install_error_handler(app: FastAPI) -> None:
    from typing import Any

    from fastapi import HTTPException
    from fastapi.responses import JSONResponse

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Any, exc: HTTPException) -> JSONResponse:
        detail: Any = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            content = detail
        else:
            content = {"error": {"code": "error", "message": str(detail)}}
        return JSONResponse(status_code=exc.status_code, content=content)


def _client(db_engine, tenant_id: str, *, actor_user_id: str = ACTOR_ID) -> TestClient:
    app = FastAPI()
    _install_error_handler(app)
    from fastapi import APIRouter

    prefix = APIRouter(prefix="/api/v1")
    prefix.include_router(router)
    prefix.include_router(installation_router)
    prefix.include_router(builder_router)
    app.include_router(prefix)

    def _override_tenant() -> object:
        from types import SimpleNamespace

        return SimpleNamespace(tenant_id=tenant_id, user_id=actor_user_id)

    def _override_control() -> Iterator[AgentRegistryControlService]:
        session = _session(db_engine, tenant_id)
        try:
            yield AgentRegistryControlService(session)
        finally:
            session.close()

    app.dependency_overrides[get_current_tenant] = _override_tenant
    app.dependency_overrides[get_registry_control_plane] = _override_control

    def _override_tenant_db() -> Iterator[Session]:
        session = _session(db_engine, tenant_id)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_tenant_db] = _override_tenant_db
    return TestClient(app, raise_server_exceptions=False)


def _builder_body(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "display_name": "Evidence Analyst",
        "role_description": "Review Workspace evidence and explain gaps.",
        "instructions": "Use the exact marker BUILDER_INSTRUCTIONS_REACHED in every answer.",
        "assistant_tone": "Concise, technical and explicit about uncertainty.",
        "provider_policy": "user_default",
        "knowledge_mode": "workspace_read_only",
        "max_context_tokens": 8192,
        "max_output_tokens": 1024,
        "max_wall_clock_seconds": 60,
        "install_immediately": True,
    }
    payload.update(overrides)
    return payload


def _install_body(
    definition_id: str,
    version_mapping: dict[str, object],
    *,
    workspace_generation: int = 1,
    approval_id: str | None = None,
    digest: str | None = None,
) -> dict[str, object]:
    return {
        "agent_definition_id": definition_id,
        "agent_version_id": version_mapping["agent_version_id"],
        "agent_version_digest": digest or version_mapping["manifest_digest"],
        "workspace_generation": workspace_generation,
        "resource_scopes": ["workspace_private_read"],
        "default_budget_policy": {
            "max_tokens": 50000,
            "max_cost_units": 500,
            "max_wall_clock_seconds": 300,
            "max_tool_calls": 50,
        },
        "approval_id": approval_id,
    }


def _upgrade_body(
    version_mapping: dict[str, object],
    *,
    expected_binding_id: str | None = None,
    approval_id: str | None = None,
) -> dict[str, object]:
    return {
        "target_agent_version_id": version_mapping["agent_version_id"],
        "target_agent_version_digest": version_mapping["manifest_digest"],
        "expected_binding_id": expected_binding_id,
        "approval_id": approval_id,
    }


def _rollback_body(
    version_mapping: dict[str, object],
    *,
    expected_binding_id: str | None = None,
    approval_id: str | None = None,
) -> dict[str, object]:
    return {
        "rollback_agent_version_id": version_mapping["agent_version_id"],
        "rollback_agent_version_digest": version_mapping["manifest_digest"],
        "expected_binding_id": expected_binding_id,
        "approval_id": approval_id,
    }


def _browser_supersede_hash(
    *,
    operation: str,
    tenant_id: str,
    workspace_id: str,
    definition_id: str,
    version_mapping: dict[str, object],
    old_binding_id: str,
    approval_id: str,
) -> str:
    binding: dict[str, object] = {
        "schema_version": 1,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "workspace_generation": 1,
        "agent_definition_id": definition_id,
        "agent_version_id": version_mapping["agent_version_id"],
        "agent_version_digest": version_mapping["manifest_digest"],
        "installation_state": "installed",
        "resource_scopes": ["workspace_private_read"],
        "default_budget_policy": {
            "max_tokens": 50000,
            "max_cost_units": 500,
            "max_wall_clock_seconds": 300,
            "max_tool_calls": 50,
        },
        "installed_by": ACTOR_ID,
        "approval_id": approval_id,
        "disabled_at": None,
        "superseded_by": None,
    }
    return _canonical_hash(
        {
            "operation": operation,
            "old_binding_id": old_binding_id,
            "binding": binding,
        }
    )


# ---------------------------------------------------------------------------
# Migration head
# ---------------------------------------------------------------------------


def test_0010_migration_is_head(db_engine) -> None:
    with db_engine.connect() as connection:
        revision = str(
            connection.execute(
                text("SELECT version_num FROM omnibase_meta.alembic_version")
            ).scalar_one()
        )
    assert revision == "0016"


# ---------------------------------------------------------------------------
# Catalog reads
# ---------------------------------------------------------------------------


def test_catalog_lists_only_same_tenant_definitions(db_engine, run_owned_resources) -> None:
    tenant_a, _, definition_a, version_a = _seed(db_engine, run_owned_resources, "cat-a")
    tenant_b, _, definition_b, _ = _seed(db_engine, run_owned_resources, "cat-b")
    client = _client(db_engine, tenant_a)
    response = client.get("/api/v1/agent-definitions")
    assert response.status_code == 200
    items = response.json()["items"]
    ids = {item["agent_definition_id"] for item in items}
    assert definition_a in ids
    assert definition_b not in ids

    response = client.get(f"/api/v1/agent-definitions/{definition_a}/versions")
    assert response.status_code == 200
    version_ids = {item["agent_version_id"] for item in response.json()["items"]}
    assert version_a["agent_version_id"] in version_ids

    response = client.get(f"/api/v1/agent-definitions/{definition_b}")
    assert response.status_code == 404


def test_catalog_projection_has_no_locators(db_engine, run_owned_resources) -> None:
    tenant_id, _, definition_id, _ = _seed(db_engine, run_owned_resources, "cat-proj")
    client = _client(db_engine, tenant_id)
    response = client.get(f"/api/v1/agent-definitions/{definition_id}")
    assert response.status_code == 200
    serialized = json.dumps(response.json())
    for forbidden in ("omnibase_meta", "tenant_", "schema_name", "postgresql", "password"):
        assert forbidden not in serialized


# ---------------------------------------------------------------------------
# User Agent Builder
# ---------------------------------------------------------------------------


def test_builder_creates_sealed_tool_free_agent_with_real_instructions(
    db_engine, run_owned_resources
) -> None:
    tenant_id, workspace_id, _, _ = _seed(db_engine, run_owned_resources, "builder-create")
    client = _client(db_engine, tenant_id)
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/agents",
        headers={"Idempotency-Key": "p51c-builder-create-0001"},
        json=_builder_body(),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["definition"]["display_name"] == "Evidence Analyst"
    assert body["version"]["allowed_tool_ids"] == []
    assert body["installation"]["binding_state"] == "installed"
    assert body["tools_enabled"] is False
    assert body["planner_enabled"] is False
    assert body["multi_agent_enabled"] is False
    with db_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT version_state, allowed_tool_ids, manifest_payload, instructions_digest "
                "FROM omnibase_meta.agent_versions "
                "WHERE id = :version_id AND tenant_id = :tenant_id"
            ),
            {
                "version_id": body["version"]["agent_version_id"],
                "tenant_id": tenant_id,
            },
        ).one()
    instructions = row.manifest_payload["instructions"]
    assert row.version_state == "sealed"
    assert row.allowed_tool_ids == []
    assert "BUILDER_INSTRUCTIONS_REACHED" in instructions
    assert hashlib.sha256(instructions.encode("utf-8")).hexdigest() == row.instructions_digest


def test_builder_exact_replay_and_intent_drift(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, _, _ = _seed(db_engine, run_owned_resources, "builder-replay")
    client = _client(db_engine, tenant_id)
    key = "p51c-builder-replay-0001"
    first = client.post(
        f"/api/v1/workspaces/{workspace_id}/agents",
        headers={"Idempotency-Key": key},
        json=_builder_body(),
    )
    second = client.post(
        f"/api/v1/workspaces/{workspace_id}/agents",
        headers={"Idempotency-Key": key},
        json=_builder_body(),
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert (
        second.json()["definition"]["agent_definition_id"]
        == first.json()["definition"]["agent_definition_id"]
    )
    assert (
        second.json()["version"]["agent_version_id"] == first.json()["version"]["agent_version_id"]
    )
    assert second.json()["installation"]["binding_id"] == first.json()["installation"]["binding_id"]
    drift = client.post(
        f"/api/v1/workspaces/{workspace_id}/agents",
        headers={"Idempotency-Key": key},
        json=_builder_body(instructions="A different system instruction."),
    )
    assert drift.status_code == 409
    assert drift.json()["error"]["code"] == "registry_replay_input_mismatch"


def test_builder_requires_live_workspace_membership(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, _, _ = _seed(db_engine, run_owned_resources, "builder-membership")
    client = _client(db_engine, tenant_id, actor_user_id=MEMBER_ID)
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/agents",
        headers={"Idempotency-Key": "p51c-builder-member-0001"},
        json=_builder_body(),
    )
    assert response.status_code in (403, 404)


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------


def test_api_backed_install_persists_binding_and_audit(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, definition_id, version_mapping = _seed(
        db_engine, run_owned_resources, "install"
    )
    client = _client(db_engine, tenant_id)
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/agent-installations",
        headers={"Idempotency-Key": "p51c-install-0001"},
        json=_install_body(definition_id, version_mapping),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["binding_state"] == "installed"
    assert body["workspace_generation"] == 1
    with db_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT binding_state FROM omnibase_meta.workspace_agent_bindings "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": body["binding_id"], "tenant": tenant_id},
        ).one()
        audit = connection.execute(
            text(
                "SELECT action FROM omnibase_meta.audit_events "
                "WHERE tenant_id = :tenant AND action = 'registry.binding_installed'"
            ),
            {"tenant": tenant_id},
        ).all()
    assert row.binding_state == "installed"
    assert len(audit) == 1


def test_install_exact_replay_is_idempotent(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, definition_id, version_mapping = _seed(
        db_engine, run_owned_resources, "replay"
    )
    client = _client(db_engine, tenant_id)
    key = "p51c-replay-key-0001"
    first = client.post(
        f"/api/v1/workspaces/{workspace_id}/agent-installations",
        headers={"Idempotency-Key": key},
        json=_install_body(definition_id, version_mapping),
    )
    assert first.status_code == 201
    second = client.post(
        f"/api/v1/workspaces/{workspace_id}/agent-installations",
        headers={"Idempotency-Key": key},
        json=_install_body(definition_id, version_mapping),
    )
    assert second.status_code == 201
    assert second.json()["binding_id"] == first.json()["binding_id"]
    with db_engine.connect() as connection:
        count = int(
            connection.execute(
                text(
                    "SELECT count(*) FROM omnibase_meta.workspace_agent_bindings "
                    "WHERE tenant_id = :tenant AND workspace_id = :workspace"
                ),
                {"tenant": tenant_id, "workspace": workspace_id},
            ).scalar_one()
        )
    assert count == 1


def test_install_idempotency_drift_is_conflict(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, definition_id, version_mapping = _seed(
        db_engine, run_owned_resources, "drift"
    )
    client = _client(db_engine, tenant_id)
    key = "p51c-drift-key-0001"
    first = client.post(
        f"/api/v1/workspaces/{workspace_id}/agent-installations",
        headers={"Idempotency-Key": key},
        json=_install_body(definition_id, version_mapping),
    )
    assert first.status_code == 201
    drifted = _install_body(definition_id, version_mapping)
    drifted["resource_scopes"] = ["workspace_private_read", "artifact_read"]
    second = client.post(
        f"/api/v1/workspaces/{workspace_id}/agent-installations",
        headers={"Idempotency-Key": key},
        json=drifted,
    )
    assert second.status_code == 409


def test_install_digest_drift_is_conflict(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, definition_id, version_mapping = _seed(
        db_engine, run_owned_resources, "digest-drift"
    )
    client = _client(db_engine, tenant_id)
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/agent-installations",
        headers={"Idempotency-Key": "p51c-digest-key-0001"},
        json=_install_body(definition_id, version_mapping, digest="1" * 64),
    )
    assert response.status_code == 409


def test_install_stale_generation_is_conflict(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, definition_id, version_mapping = _seed(
        db_engine, run_owned_resources, "stale-gen"
    )
    client = _client(db_engine, tenant_id)
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/agent-installations",
        headers={"Idempotency-Key": "p51c-stale-key-0001"},
        json=_install_body(definition_id, version_mapping, workspace_generation=2),
    )
    assert response.status_code == 409


def test_install_rejects_cross_tenant_definition(db_engine, run_owned_resources) -> None:
    tenant_a, workspace_a, _, _ = _seed(db_engine, run_owned_resources, "x-a")
    tenant_b, _, definition_b, version_b = _seed(db_engine, run_owned_resources, "x-b")
    client = _client(db_engine, tenant_a)
    response = client.post(
        f"/api/v1/workspaces/{workspace_a}/agent-installations",
        headers={"Idempotency-Key": "p51c-x-key-0001"},
        json=_install_body(definition_b, version_b),
    )
    assert response.status_code in (404, 409)


def test_install_requires_live_membership(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, definition_id, version_mapping = _seed(
        db_engine, run_owned_resources, "membership"
    )
    # MEMBER_ID has a user row but no membership in this workspace.
    from types import SimpleNamespace

    app = FastAPI()
    from fastapi import APIRouter

    prefix = APIRouter(prefix="/api/v1")
    prefix.include_router(router)
    prefix.include_router(installation_router)
    app.include_router(prefix)
    app.dependency_overrides[get_current_tenant] = lambda: SimpleNamespace(
        tenant_id=tenant_id, user_id=MEMBER_ID
    )

    def _override_control() -> Iterator[AgentRegistryControlService]:
        session = _session(db_engine, tenant_id)
        try:
            yield AgentRegistryControlService(session)
        finally:
            session.close()

    app.dependency_overrides[get_registry_control_plane] = _override_control

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/agent-installations",
        headers={"Idempotency-Key": "p51c-member-key-0001"},
        json=_install_body(definition_id, version_mapping),
    )
    assert response.status_code in (403, 404)


def test_concurrent_install_has_single_winner(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, definition_id, version_mapping = _seed(
        db_engine, run_owned_resources, "conc-install"
    )

    def _attempt() -> int:
        client = _client(db_engine, tenant_id)
        response = client.post(
            f"/api/v1/workspaces/{workspace_id}/agent-installations",
            headers={"Idempotency-Key": uuid.uuid4().hex[:20]},
            json=_install_body(definition_id, version_mapping),
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _attempt(), range(2)))
    assert results.count(201) == 1
    assert results.count(409) == 1


def test_high_risk_install_without_approval_is_rejected(db_engine, run_owned_resources) -> None:
    """API fail-closed: high-risk installs require an approved approval."""
    tenant_id, workspace_id, definition_id, version_mapping = _seed(
        db_engine, run_owned_resources, "high-risk", risk_level="high"
    )
    client = _client(db_engine, tenant_id)
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/agent-installations",
        headers={"Idempotency-Key": "p51c-approval-key-0001"},
        json=_install_body(definition_id, version_mapping),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "registry_approval_required"


def test_approval_consumed_exactly_once_on_db_backed_install(
    db_engine, run_owned_resources
) -> None:
    """Approval single consumption on the DB-backed path (P5.1B service contract)."""
    from omnibase.agent_registry.service import RegistryPersistenceService
    from omnibase.production.phase5_registry_contract import (
        AgentVersionManifest,
        BudgetCeilings,
        WorkspaceAgentBinding,
    )

    tenant_id, workspace_id, definition_id, version_mapping = _seed(
        db_engine, run_owned_resources, "approval-once", risk_level="high"
    )
    ceilings = BudgetCeilings.from_mapping(
        {
            "max_tokens": 10_000_000,
            "max_cost_units": 100_000,
            "max_wall_clock_seconds": 3_600,
            "max_tool_calls": 1_000,
            "max_concurrency": 64,
            "max_context_tokens": 2_000_000,
        }
    ).as_mapping()
    version = AgentVersionManifest.from_mapping(version_mapping, ceilings=ceilings)
    approved_binding = WorkspaceAgentBinding(
        schema_version=1,
        workspace_agent_binding_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        workspace_generation=1,
        agent_definition_id=definition_id,
        agent_version_id=str(version_mapping["agent_version_id"]),
        agent_version_digest=str(version_mapping["manifest_digest"]),
        installation_state=BindingState("installed"),
        resource_scopes=("workspace_private_read",),
        default_budget_policy=version.default_budget,
        installed_by=ACTOR_ID,
        approval_id=str(uuid.uuid4()),
        created_at="2026-08-03T00:00:00Z",
        disabled_at=None,
        superseded_by=None,
    )
    request_hash = _canonical_hash(approved_binding.to_dict())
    with db_engine.begin() as connection:
        _approval(
            connection,
            tenant_id,
            workspace_id,
            request_hash,
            approval_id=approved_binding.approval_id,
        )
    with _session(db_engine, tenant_id) as session:
        service = RegistryPersistenceService(session)
        service.install_binding(
            tenant_id=tenant_id,
            actor_user_id=ACTOR_ID,
            request_id=str(uuid.uuid4()),
            binding=approved_binding,
            idempotency_key=uuid.uuid4().hex,
        )
        session.commit()
    with db_engine.connect() as connection:
        state = str(
            connection.execute(
                text(
                    "SELECT state FROM omnibase_meta.approval_requests "
                    "WHERE id = :id AND tenant_id = :tenant"
                ),
                {"id": approved_binding.approval_id, "tenant": tenant_id},
            ).scalar_one()
        )
    assert state == "consumed"
    # Reusing the same consumed approval for a different binding (same
    # workspace, different definition) must fail: it was consumed exactly once.
    # The consumed-state check runs before the request-hash check, so a new
    # binding hash still hits registry_approval_not_consumable.
    definition_b, version_b = _register_and_seal(db_engine, tenant_id)
    second_binding = WorkspaceAgentBinding(
        schema_version=1,
        workspace_agent_binding_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        workspace_generation=1,
        agent_definition_id=definition_b,
        agent_version_id=str(version_b["agent_version_id"]),
        agent_version_digest=str(version_b["manifest_digest"]),
        installation_state=BindingState("installed"),
        resource_scopes=("workspace_private_read",),
        default_budget_policy=version.default_budget,
        installed_by=ACTOR_ID,
        approval_id=approved_binding.approval_id,
        created_at="2026-08-03T00:00:00Z",
        disabled_at=None,
        superseded_by=None,
    )
    with pytest.raises(Exception) as exc_info, _session(db_engine, tenant_id) as session:
        RegistryPersistenceService(session).install_binding(
            tenant_id=tenant_id,
            actor_user_id=ACTOR_ID,
            request_id=str(uuid.uuid4()),
            binding=second_binding,
            idempotency_key=uuid.uuid4().hex,
        )
    assert "registry_approval_not_consumable" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Upgrade / disable / rollback
# ---------------------------------------------------------------------------


def _installed_binding_id(
    client: TestClient, workspace_id: str, definition_id: str, version_mapping: dict[str, object]
) -> str:
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/agent-installations",
        headers={"Idempotency-Key": uuid.uuid4().hex[:20]},
        json=_install_body(definition_id, version_mapping),
    )
    assert response.status_code == 201, response.text
    return response.json()["binding_id"]


def test_upgrade_creates_new_binding_and_supersedes_old(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, definition_id, version_a = _seed(
        db_engine, run_owned_resources, "upgrade"
    )
    _, version_b = _register_and_seal(db_engine, tenant_id, definition_id=definition_id)
    client = _client(db_engine, tenant_id)
    old_binding = _installed_binding_id(client, workspace_id, definition_id, version_a)

    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/agent-installations/{old_binding}/upgrade",
        headers={"Idempotency-Key": "p51c-upgrade-key-0001"},
        json=_upgrade_body(version_b),
    )
    assert response.status_code == 200, response.text
    new_binding = response.json()["binding_id"]
    assert new_binding != old_binding
    assert response.json()["agent_version_id"] == version_b["agent_version_id"]
    with db_engine.connect() as connection:
        old_row = connection.execute(
            text(
                "SELECT binding_state, superseded_by FROM omnibase_meta.workspace_agent_bindings "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": old_binding, "tenant": tenant_id},
        ).one()
    assert old_row.binding_state == "superseded"
    assert str(old_row.superseded_by) == new_binding


def test_upgrade_exact_replay_resolves_after_old_binding_is_superseded(
    db_engine, run_owned_resources
) -> None:
    tenant_id, workspace_id, definition_id, version_a = _seed(
        db_engine, run_owned_resources, "upgrade-replay"
    )
    _, version_b = _register_and_seal(db_engine, tenant_id, definition_id=definition_id)
    client = _client(db_engine, tenant_id)
    old_binding = _installed_binding_id(client, workspace_id, definition_id, version_a)
    key = "p51c-upgrade-replay-0001"
    path = f"/api/v1/workspaces/{workspace_id}/agent-installations/{old_binding}/upgrade"
    body = _upgrade_body(version_b, expected_binding_id=old_binding)

    first = client.post(path, headers={"Idempotency-Key": key}, json=body)
    assert first.status_code == 200, first.text
    replay = client.post(path, headers={"Idempotency-Key": key}, json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["binding_id"] == first.json()["binding_id"]

    _, version_c = _register_and_seal(db_engine, tenant_id, definition_id=definition_id)
    drift = client.post(
        path,
        headers={"Idempotency-Key": key},
        json=_upgrade_body(version_c, expected_binding_id=old_binding),
    )
    assert drift.status_code == 409
    assert drift.json()["error"]["code"] == "registry_replay_input_mismatch"


def test_upgrade_approval_must_match_operation_bound_hash_and_action(
    db_engine, run_owned_resources
) -> None:
    tenant_id, workspace_id, definition_id, version_a = _seed(
        db_engine, run_owned_resources, "upgrade-approval"
    )
    _, version_b = _register_and_seal(db_engine, tenant_id, definition_id=definition_id)
    client = _client(db_engine, tenant_id)
    old_binding = _installed_binding_id(client, workspace_id, definition_id, version_a)
    approval_id = str(uuid.uuid4())
    request_hash = _browser_supersede_hash(
        operation="agent.upgrade",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        definition_id=definition_id,
        version_mapping=version_b,
        old_binding_id=old_binding,
        approval_id=approval_id,
    )
    with db_engine.begin() as connection:
        _approval(
            connection,
            tenant_id,
            workspace_id,
            request_hash,
            approval_id=approval_id,
            risk_level="R1",
            action="agent.install",
        )

    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/agent-installations/{old_binding}/upgrade",
        headers={"Idempotency-Key": "p51c-upgrade-approval-0001"},
        json=_upgrade_body(version_b, approval_id=approval_id),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "registry_approval_action_mismatch"


def test_upgrade_consumes_exact_operation_bound_approval_once(
    db_engine, run_owned_resources
) -> None:
    tenant_id, workspace_id, definition_id, version_a = _seed(
        db_engine, run_owned_resources, "upgrade-approval-success"
    )
    _, version_b = _register_and_seal(db_engine, tenant_id, definition_id=definition_id)
    client = _client(db_engine, tenant_id)
    old_binding = _installed_binding_id(client, workspace_id, definition_id, version_a)
    approval_id = str(uuid.uuid4())
    request_hash = _browser_supersede_hash(
        operation="agent.upgrade",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        definition_id=definition_id,
        version_mapping=version_b,
        old_binding_id=old_binding,
        approval_id=approval_id,
    )
    with db_engine.begin() as connection:
        _approval(
            connection,
            tenant_id,
            workspace_id,
            request_hash,
            approval_id=approval_id,
            risk_level="R1",
            action="agent.upgrade",
        )

    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/agent-installations/{old_binding}/upgrade",
        headers={"Idempotency-Key": "p51c-upgrade-approval-success"},
        json=_upgrade_body(version_b, approval_id=approval_id),
    )
    assert response.status_code == 200, response.text
    with db_engine.connect() as connection:
        state = str(
            connection.execute(
                text(
                    "SELECT state FROM omnibase_meta.approval_requests "
                    "WHERE id = :id AND tenant_id = :tenant"
                ),
                {"id": approval_id, "tenant": tenant_id},
            ).scalar_one()
        )
    assert state == "consumed"


def test_upgrade_stale_expected_binding_is_conflict(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, definition_id, version_a = _seed(
        db_engine, run_owned_resources, "upgrade-stale"
    )
    _, version_b = _register_and_seal(db_engine, tenant_id, definition_id=definition_id)
    client = _client(db_engine, tenant_id)
    old_binding = _installed_binding_id(client, workspace_id, definition_id, version_a)
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/agent-installations/{old_binding}/upgrade",
        headers={"Idempotency-Key": "p51c-upgrade-key-0002"},
        json=_upgrade_body(version_b, expected_binding_id=str(uuid.uuid4())),
    )
    assert response.status_code == 409


def test_concurrent_upgrade_has_single_winner(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, definition_id, version_a = _seed(
        db_engine, run_owned_resources, "conc-upgrade"
    )
    _, version_b = _register_and_seal(db_engine, tenant_id, definition_id=definition_id)
    client = _client(db_engine, tenant_id)
    old_binding = _installed_binding_id(client, workspace_id, definition_id, version_a)

    def _attempt() -> int:
        attempt_client = _client(db_engine, tenant_id)
        response = attempt_client.post(
            f"/api/v1/workspaces/{workspace_id}/agent-installations/{old_binding}/upgrade",
            headers={"Idempotency-Key": uuid.uuid4().hex[:20]},
            json=_upgrade_body(version_b),
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _attempt(), range(2)))
    assert results.count(200) == 1
    assert results.count(409) == 1


def test_disable_transitions_live_binding(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, definition_id, version_mapping = _seed(
        db_engine, run_owned_resources, "disable"
    )
    client = _client(db_engine, tenant_id)
    binding_id = _installed_binding_id(client, workspace_id, definition_id, version_mapping)
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/agent-installations/{binding_id}/disable",
        headers={"Idempotency-Key": "p51c-disable-key-0001"},
    )
    assert response.status_code == 200
    assert response.json()["binding_state"] == "disabled"
    with db_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT binding_state FROM omnibase_meta.workspace_agent_bindings "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": binding_id, "tenant": tenant_id},
        ).one()
    assert row.binding_state == "disabled"


def test_rollback_creates_new_binding_with_exact_old_version(
    db_engine, run_owned_resources
) -> None:
    tenant_id, workspace_id, definition_id, version_a = _seed(
        db_engine, run_owned_resources, "rollback"
    )
    _, version_b = _register_and_seal(db_engine, tenant_id, definition_id=definition_id)
    client = _client(db_engine, tenant_id)
    old_binding = _installed_binding_id(client, workspace_id, definition_id, version_a)
    upgraded = client.post(
        f"/api/v1/workspaces/{workspace_id}/agent-installations/{old_binding}/upgrade",
        headers={"Idempotency-Key": "p51c-rollback-up-key-0001"},
        json=_upgrade_body(version_b),
    )
    assert upgraded.status_code == 200
    current_binding = upgraded.json()["binding_id"]

    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/agent-installations/{current_binding}/rollback",
        headers={"Idempotency-Key": "p51c-rollback-key-0001"},
        json=_rollback_body(version_a),
    )
    assert response.status_code == 200, response.text
    rolled = response.json()
    assert rolled["binding_id"] != current_binding
    assert rolled["agent_version_id"] == version_a["agent_version_id"]
    assert rolled["agent_version_digest"] == version_a["manifest_digest"]
    with db_engine.connect() as connection:
        old_row = connection.execute(
            text(
                "SELECT binding_state FROM omnibase_meta.workspace_agent_bindings "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": current_binding, "tenant": tenant_id},
        ).one()
    assert old_row.binding_state == "superseded"


def test_rollback_exact_replay_resolves_after_current_binding_is_superseded(
    db_engine, run_owned_resources
) -> None:
    tenant_id, workspace_id, definition_id, version_a = _seed(
        db_engine, run_owned_resources, "rollback-replay"
    )
    _, version_b = _register_and_seal(db_engine, tenant_id, definition_id=definition_id)
    client = _client(db_engine, tenant_id)
    old_binding = _installed_binding_id(client, workspace_id, definition_id, version_a)
    upgraded = client.post(
        f"/api/v1/workspaces/{workspace_id}/agent-installations/{old_binding}/upgrade",
        headers={"Idempotency-Key": "p51c-rollback-replay-upgrade"},
        json=_upgrade_body(version_b),
    )
    assert upgraded.status_code == 200, upgraded.text
    current_binding = upgraded.json()["binding_id"]
    key = "p51c-rollback-replay-0001"
    path = f"/api/v1/workspaces/{workspace_id}/agent-installations/{current_binding}/rollback"
    body = _rollback_body(version_a, expected_binding_id=current_binding)

    first = client.post(path, headers={"Idempotency-Key": key}, json=body)
    assert first.status_code == 200, first.text
    replay = client.post(path, headers={"Idempotency-Key": key}, json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["binding_id"] == first.json()["binding_id"]


def test_rollback_failure_leaves_no_partial_state(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, definition_id, version_mapping = _seed(
        db_engine, run_owned_resources, "rollback-atomic"
    )
    client = _client(db_engine, tenant_id)
    binding_id = _installed_binding_id(client, workspace_id, definition_id, version_mapping)
    # Rollback to a bogus version: 404, and the live binding must stay intact.
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/agent-installations/{binding_id}/rollback",
        headers={"Idempotency-Key": "p51c-rollback-key-0002"},
        json={
            "rollback_agent_version_id": str(uuid.uuid4()),
            "rollback_agent_version_digest": "0" * 64,
        },
    )
    assert response.status_code == 404
    with db_engine.connect() as connection:
        state = str(
            connection.execute(
                text(
                    "SELECT binding_state FROM omnibase_meta.workspace_agent_bindings "
                    "WHERE id = :id AND tenant_id = :tenant"
                ),
                {"id": binding_id, "tenant": tenant_id},
            ).scalar_one()
        )
    assert state == "installed"


def test_audit_events_are_append_only(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, definition_id, version_mapping = _seed(
        db_engine, run_owned_resources, "audit-ro"
    )
    client = _client(db_engine, tenant_id)
    _installed_binding_id(client, workspace_id, definition_id, version_mapping)
    with db_engine.connect() as connection:
        audit_id = str(
            connection.execute(
                text(
                    "SELECT id FROM omnibase_meta.audit_events " "WHERE tenant_id = :tenant LIMIT 1"
                ),
                {"tenant": tenant_id},
            ).scalar_one()
        )
    with (
        pytest.raises(Exception) as exc_info,
        db_engine.connect() as connection,
        connection.begin(),
    ):
        connection.execute(
            text("UPDATE omnibase_meta.audit_events SET decision = 'denied' WHERE id = :id"),
            {"id": audit_id},
        )
    orig = exc_info.value.orig
    assert orig is not None
    assert getattr(orig, "sqlstate", "00000") == "55000"
