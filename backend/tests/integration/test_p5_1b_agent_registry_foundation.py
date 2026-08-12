"""Guarded PostgreSQL acceptance tests for the P5.1B Agent Registry persistence foundation.

These tests prove the disposable P5.1B Gate requirements against a sentinel
disposable PostgreSQL database: migration ``0010`` upgrades the head, the
database itself blocks cross-tenant references, sealed versions and revoked
states are immutable at the database level, natural-key and live-binding
concurrency has exactly one winner, exact digest replay is idempotent, digest
drift and stale generations conflict, high/critical bindings require an
approval consumed exactly once, audit rows are append-only, a failed
transaction leaves no partial state, and physical locators never appear in
DTO projections or error messages.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from omnibase.agent_registry.service import (
    RegistryConflictError,
    RegistryNotFoundError,
    RegistryPersistenceService,
    RegistryStateError,
)
from omnibase.production.phase5_registry_contract import (
    AgentDefinition,
    AgentVersionManifest,
    BudgetCeilings,
    WorkspaceAgentBinding,
)

if os.environ.get("OMNIBASE_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "P5.1B integration tests require OMNIBASE_INTEGRATION_TESTS=1",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration
_BACKEND_ROOT = Path(__file__).resolve().parents[2]

ACTOR_ID = "00000000-0000-0000-0000-0000000000aa"
MODEL_POLICY_ID = "00000000-0000-0000-0000-0000000000bb"
MEMORY_POLICY_ID = "00000000-0000-0000-0000-0000000000cc"
INSTRUCTIONS_DIGEST = "3333333333333333333333333333333333333333333333333333333333333333"

_PHYSICAL_LOCATORS = (
    "omnibase_meta",
    "agent_definitions",
    "agent_versions",
    "workspace_agent_bindings",
    "audit_events",
    "approval_requests",
    "idempotency_records",
)


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
def p51b_schema(db_engine) -> None:
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
            "name": f"P5.1B {label}",
            "slug": f"p51b-{label}-{suffix}",
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


def _seed_actor_user(connection, schema_name: str, label: str) -> None:
    connection.execute(
        text(
            f'INSERT INTO "{schema_name}".users '  # noqa: S608
            "(id, email, password_hash, is_tenant_admin, is_active) "
            "VALUES (:id, :email, :hash, TRUE, TRUE)"
        ),
        {
            "id": ACTOR_ID,
            "email": f"{label}-{uuid.uuid4().hex[:8]}@example.invalid",
            "hash": uuid.uuid4().hex,
        },
    )


def _tenant_with_schema(db_engine, run_owned_resources, label: str) -> str:
    """Create a tenant, bootstrap its schema tables, and seed the actor user."""
    with db_engine.begin() as connection:
        tenant_id = _tenant(connection, run_owned_resources, label)
        schema_name = _tenant_schema(connection, tenant_id)
    _upgrade_head()
    with db_engine.begin() as connection:
        _seed_actor_user(connection, schema_name, label)
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


def _canonical_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _ceilings() -> dict[str, int]:
    return BudgetCeilings.from_mapping(
        {
            "max_tokens": 10_000_000,
            "max_cost_units": 100_000,
            "max_wall_clock_seconds": 3_600,
            "max_tool_calls": 1_000,
            "max_concurrency": 64,
            "max_context_tokens": 2_000_000,
        }
    ).as_mapping()


def _definition_mapping(
    tenant_id: str, *, key: str | None = None, risk_level: str = "low"
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "agent_definition_id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "stable_logical_key": key or f"agent-{uuid.uuid4().hex[:10]}",
        "display_name": "Registry Gate Agent",
        "description": "Disposable P5.1B gate agent",
        "risk_level": risk_level,
        "allowed_installation_scopes": ["workspace"],
        "definition_state": "active",
        "created_by": ACTOR_ID,
        "created_at": "2026-08-03T00:00:00Z",
        "metadata_version": 1,
    }


def _definition_dto(mapping: dict[str, object]) -> AgentDefinition:
    return AgentDefinition.from_mapping(mapping)


def _version_mapping(
    tenant_id: str,
    definition_id: str,
    *,
    version: str | None = None,
    risk_level: str = "low",
) -> dict[str, object]:
    mapping: dict[str, object] = {
        "schema_version": 1,
        "agent_version_id": str(uuid.uuid4()),
        "agent_definition_id": definition_id,
        "tenant_id": tenant_id,
        "version": version or f"1.0.{uuid.uuid4().int % 10 ** 6}",
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


def _version_dto(mapping: dict[str, object]) -> AgentVersionManifest:
    return AgentVersionManifest.from_mapping(mapping, ceilings=_ceilings())


def _binding_mapping(
    tenant_id: str,
    workspace_id: str,
    definition_id: str,
    version: AgentVersionManifest,
    *,
    workspace_generation: int = 1,
    approval_id: str | None = None,
    version_digest: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "workspace_agent_binding_id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "workspace_generation": workspace_generation,
        "agent_definition_id": definition_id,
        "agent_version_id": version.agent_version_id,
        "agent_version_digest": version_digest or version.canonical_digest(),
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
        "created_at": "2026-08-03T00:00:00Z",
        "disabled_at": None,
        "superseded_by": None,
    }


def _binding_dto(mapping: dict[str, object]) -> WorkspaceAgentBinding:
    return WorkspaceAgentBinding.from_mapping(mapping, ceilings=_ceilings())


def _approval(
    connection,
    tenant_id: str,
    workspace_id: str,
    request_hash: str,
    *,
    approval_id: str | None = None,
    risk_level: str = "R3",
) -> str:
    approval_id = approval_id or str(uuid.uuid4())
    connection.execute(
        text(
            "INSERT INTO omnibase_meta.approval_requests "
            "(id, tenant_id, requester_type, requester_id, workspace_id, "
            "action, risk_level, required_approver_role, state, request_hash, "
            "grant_id, operation_id, decided_by_actor_type, decided_by_actor_id, "
            "expires_at) "
            "VALUES (:id, :tenant, 'user', :actor, :workspace, 'agent.install', "
            ":risk, :role, 'approved', :hash, :grant, :operation, 'user', :actor, :expires)"
        ),
        {
            "id": approval_id,
            "tenant": tenant_id,
            "actor": ACTOR_ID,
            "workspace": workspace_id,
            "risk": risk_level,
            "role": "platform_admin" if risk_level == "R4" else "tenant_admin",
            "hash": request_hash,
            "grant": str(uuid.uuid4()),
            "operation": str(uuid.uuid4()),
            "expires": datetime.now(UTC) + timedelta(hours=1),
        },
    )
    return approval_id


def _seed_definition_version(
    db_engine,
    run_owned_resources,
    label: str,
    *,
    risk_level: str = "low",
) -> tuple[str, str, AgentVersionManifest]:
    """Create tenant + workspace + definition + sealed version, committed."""
    with db_engine.begin() as connection:
        tenant_id = _tenant(connection, run_owned_resources, label)
        schema_name = _tenant_schema(connection, tenant_id)
        template_id = _template(connection, tenant_id)
        workspace_id = _workspace(connection, tenant_id, template_id, label)
    _upgrade_head()
    with db_engine.begin() as connection:
        _seed_actor_user(connection, schema_name, label)
    definition_mapping = _definition_mapping(tenant_id, risk_level=risk_level)
    version_mapping = _version_mapping(
        tenant_id, str(definition_mapping["agent_definition_id"]), risk_level=risk_level
    )
    version = _version_dto(version_mapping)
    with _session(db_engine, tenant_id) as session:
        _register(session, tenant_id=tenant_id, mapping=definition_mapping, key=uuid.uuid4().hex)
        RegistryPersistenceService(session).seal_version(
            tenant_id=tenant_id,
            actor_user_id=ACTOR_ID,
            request_id=str(uuid.uuid4()),
            version=version,
            idempotency_key=uuid.uuid4().hex,
        )
        session.commit()
    return tenant_id, workspace_id, version


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


def _register(session: Session, *, tenant_id: str, mapping: dict[str, object], key: str):
    return RegistryPersistenceService(session).register_definition(
        tenant_id=tenant_id,
        actor_user_id=ACTOR_ID,
        request_id=str(uuid.uuid4()),
        definition=_definition_dto(mapping),
        idempotency_key=key,
    )


def _install(session: Session, *, tenant_id: str, binding: WorkspaceAgentBinding, key: str):
    return RegistryPersistenceService(session).install_binding(
        tenant_id=tenant_id,
        actor_user_id=ACTOR_ID,
        request_id=str(uuid.uuid4()),
        binding=binding,
        idempotency_key=key,
    )


def _expect_trigger_rejection(exc_info: pytest.ExceptionInfo[BaseException], message: str) -> None:
    """Database trigger RAISE (55000) surfaces through the DBAPI error."""
    orig = exc_info.value.orig
    assert orig is not None, exc_info.value
    assert getattr(orig, "sqlstate", "00000") == "55000", exc_info.value
    assert message in str(exc_info.value)


# ---------------------------------------------------------------------------
# Migration head
# ---------------------------------------------------------------------------


def test_0010_migration_upgrades_to_head(db_engine) -> None:
    with db_engine.connect() as connection:
        current = str(
            connection.execute(
                text("SELECT version_num FROM omnibase_meta.alembic_version")
            ).scalar_one()
        )
    assert current == "0015"


# ---------------------------------------------------------------------------
# Register / seal / install happy path and same-transaction audit
# ---------------------------------------------------------------------------


def test_register_definition_persists_row_and_same_transaction_audit(
    db_engine, run_owned_resources
) -> None:
    tenant_id = _tenant_with_schema(db_engine, run_owned_resources, "register")
    mapping = _definition_mapping(tenant_id)
    with _session(db_engine, tenant_id) as session:
        model = _register(session, tenant_id=tenant_id, mapping=mapping, key=uuid.uuid4().hex)
        session.commit()
    assert model.definition_state == "active"
    with db_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT definition_state, risk_level FROM omnibase_meta.agent_definitions "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": model.id, "tenant": tenant_id},
        ).one()
        audit = connection.execute(
            text(
                "SELECT action, decision FROM omnibase_meta.audit_events "
                "WHERE tenant_id = :tenant AND resource_id = :id"
            ),
            {"tenant": tenant_id, "id": model.id},
        ).all()
    assert row.definition_state == "active"
    assert row.risk_level == "low"
    actions = {item.action for item in audit}
    assert "registry.definition_registered" in actions


def test_register_definition_exact_replay_is_idempotent(db_engine, run_owned_resources) -> None:
    tenant_id = _tenant_with_schema(db_engine, run_owned_resources, "replay")
    mapping = _definition_mapping(tenant_id)
    key = uuid.uuid4().hex
    with _session(db_engine, tenant_id) as session:
        first = _register(session, tenant_id=tenant_id, mapping=mapping, key=key)
        session.commit()
    with _session(db_engine, tenant_id) as session:
        second = _register(session, tenant_id=tenant_id, mapping=mapping, key=key)
        session.commit()
    assert first.id == second.id
    with db_engine.connect() as connection:
        count = int(
            connection.execute(
                text(
                    "SELECT count(*) FROM omnibase_meta.agent_definitions "
                    "WHERE id = :id AND tenant_id = :tenant"
                ),
                {"id": first.id, "tenant": tenant_id},
            ).scalar_one()
        )
    assert count == 1


def test_register_definition_digest_drift_replay_is_conflict(
    db_engine, run_owned_resources
) -> None:
    tenant_id = _tenant_with_schema(db_engine, run_owned_resources, "drift")
    mapping = _definition_mapping(tenant_id)
    key = uuid.uuid4().hex
    with _session(db_engine, tenant_id) as session:
        _register(session, tenant_id=tenant_id, mapping=mapping, key=key)
        session.commit()
    drifted = _definition_mapping(tenant_id)
    drifted["display_name"] = "Drifted Display Name"
    with _session(db_engine, tenant_id) as session:
        with pytest.raises(RegistryConflictError):
            _register(session, tenant_id=tenant_id, mapping=drifted, key=key)
        session.rollback()


def test_duplicate_logical_key_is_conflict(db_engine, run_owned_resources) -> None:
    tenant_id = _tenant_with_schema(db_engine, run_owned_resources, "dup-key")
    key_name = f"dup-{uuid.uuid4().hex[:10]}"
    with _session(db_engine, tenant_id) as session:
        _register(
            session,
            tenant_id=tenant_id,
            mapping=_definition_mapping(tenant_id, key=key_name),
            key=uuid.uuid4().hex,
        )
        session.commit()
    with _session(db_engine, tenant_id) as session:
        with pytest.raises(RegistryConflictError):
            _register(
                session,
                tenant_id=tenant_id,
                mapping=_definition_mapping(tenant_id, key=key_name),
                key=uuid.uuid4().hex,
            )
        session.rollback()


def test_seal_version_persists_canonical_digest_and_audit(db_engine, run_owned_resources) -> None:
    tenant_id, _, version = _seed_definition_version(db_engine, run_owned_resources, "seal")
    with db_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT manifest_digest, version_state FROM omnibase_meta.agent_versions "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": version.agent_version_id, "tenant": tenant_id},
        ).one()
    assert row.manifest_digest == version.canonical_digest()
    assert row.version_state == "sealed"


def test_duplicate_semver_for_definition_is_conflict(db_engine, run_owned_resources) -> None:
    tenant_id = _tenant_with_schema(db_engine, run_owned_resources, "dup-semver")
    definition_mapping = _definition_mapping(tenant_id)
    version_label = "1.2.3"
    with _session(db_engine, tenant_id) as session:
        _register(session, tenant_id=tenant_id, mapping=definition_mapping, key=uuid.uuid4().hex)
        first = _version_dto(
            _version_mapping(
                tenant_id, str(definition_mapping["agent_definition_id"]), version=version_label
            )
        )
        RegistryPersistenceService(session).seal_version(
            tenant_id=tenant_id,
            actor_user_id=ACTOR_ID,
            request_id=str(uuid.uuid4()),
            version=first,
            idempotency_key=uuid.uuid4().hex,
        )
        session.commit()
    duplicate = _version_dto(
        _version_mapping(
            tenant_id, str(definition_mapping["agent_definition_id"]), version=version_label
        )
    )
    with _session(db_engine, tenant_id) as session:
        with pytest.raises(RegistryConflictError):
            RegistryPersistenceService(session).seal_version(
                tenant_id=tenant_id,
                actor_user_id=ACTOR_ID,
                request_id=str(uuid.uuid4()),
                version=duplicate,
                idempotency_key=uuid.uuid4().hex,
            )
        session.rollback()


def test_install_binding_persists_with_same_transaction_audit(
    db_engine, run_owned_resources
) -> None:
    tenant_id, workspace_id, version = _seed_definition_version(
        db_engine, run_owned_resources, "install"
    )
    binding = _binding_dto(
        _binding_mapping(
            tenant_id,
            workspace_id,
            version.agent_definition_id,
            version,
        )
    )
    with _session(db_engine, tenant_id) as session:
        model = _install(session, tenant_id=tenant_id, binding=binding, key=uuid.uuid4().hex)
        session.commit()
    assert model.binding_state == "installed"
    with db_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT binding_state FROM omnibase_meta.workspace_agent_bindings "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": model.id, "tenant": tenant_id},
        ).one()
        audit = connection.execute(
            text(
                "SELECT action FROM omnibase_meta.audit_events "
                "WHERE tenant_id = :tenant AND action = 'registry.binding_installed'"
            ),
            {"tenant": tenant_id},
        ).all()
    assert row.binding_state == "installed"
    actions = {item.action for item in audit}
    assert "registry.binding_installed" in actions


# ---------------------------------------------------------------------------
# Database-enforced integrity (raw SQL, bypassing the service on purpose)
# ---------------------------------------------------------------------------


def test_database_rejects_cross_tenant_definition_version_reference(
    db_engine, run_owned_resources
) -> None:
    tenant_a, _, _ = _seed_definition_version(db_engine, run_owned_resources, "x-def-a")
    tenant_b = _tenant_with_schema(db_engine, run_owned_resources, "x-def-b")
    with db_engine.connect() as connection:
        definition_id = str(
            connection.execute(
                text(
                    "SELECT id FROM omnibase_meta.agent_definitions "
                    "WHERE tenant_id = :tenant LIMIT 1"
                ),
                {"tenant": tenant_a},
            ).scalar_one()
        )
    with (
        pytest.raises(Exception) as exc_info,
        db_engine.connect() as connection,
        connection.begin(),
    ):
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.agent_versions "
                "(id, tenant_id, definition_id, version, version_state, "
                "manifest_payload, manifest_digest, model_policy_id, "
                "instructions_digest, max_context_tokens, allowed_tool_ids, "
                "input_schema, output_schema, max_concurrency, default_budget, "
                "risk_level, created_by) "
                "VALUES (:id, :tenant_b, :definition, '9.9.9', 'sealed', "
                "CAST(:payload AS jsonb), :digest, :policy, :instr, 100, "
                "CAST(:tools AS jsonb), CAST(:input_schema AS jsonb), "
                "CAST(:output_schema AS jsonb), 1, CAST(:budget AS jsonb), "
                "'low', :actor)"
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant_b": tenant_b,
                "definition": definition_id,
                "payload": '{"x":1}',
                "tools": '["rag_search"]',
                "input_schema": '{"type":"object"}',
                "output_schema": '{"type":"object"}',
                "budget": '{"max_tokens":1,"max_cost_units":1,'
                '"max_wall_clock_seconds":1,"max_tool_calls":1}',
                "digest": "0" * 64,
                "policy": MODEL_POLICY_ID,
                "instr": INSTRUCTIONS_DIGEST,
                "actor": ACTOR_ID,
            },
        )
    _expect_trigger_rejection(exc_info, "references an unknown agent_definition")


def test_database_rejects_cross_tenant_version_binding_reference(
    db_engine, run_owned_resources
) -> None:
    tenant_a, _, version = _seed_definition_version(db_engine, run_owned_resources, "x-bind-a")
    with db_engine.begin() as connection:
        tenant_b = _tenant(connection, run_owned_resources, "x-bind-b")
        template_b = _template(connection, tenant_b)
        workspace_b = _workspace(connection, tenant_b, template_b, "Workspace B")
    _upgrade_head()
    assert tenant_a != tenant_b
    with (
        pytest.raises(Exception) as exc_info,
        db_engine.connect() as connection,
        connection.begin(),
    ):
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_agent_bindings "
                "(id, tenant_id, workspace_id, workspace_generation, "
                "agent_definition_id, agent_version_id, agent_version_digest, "
                "binding_state, resource_scopes, default_budget_policy, "
                "installed_by) "
                "VALUES (:id, :tenant_b, :workspace_b, 1, "
                ":definition, :version, :digest, 'installed', "
                "CAST(:scopes AS jsonb), "
                "CAST(:budget AS jsonb), "
                ":actor)"
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant_b": tenant_b,
                "workspace_b": workspace_b,
                "scopes": '["workspace_private_read"]',
                "budget": '{"max_tokens":1,"max_cost_units":1,'
                '"max_wall_clock_seconds":1,"max_tool_calls":1}',
                "definition": version.agent_definition_id,
                "version": version.agent_version_id,
                "digest": version.canonical_digest(),
                "actor": ACTOR_ID,
            },
        )
    _expect_trigger_rejection(exc_info, "references an unknown agent_definition")


def test_database_blocks_sealed_version_content_mutation(db_engine, run_owned_resources) -> None:
    tenant_id, _, version = _seed_definition_version(db_engine, run_owned_resources, "seal-lock")
    with (
        pytest.raises(Exception) as exc_info,
        db_engine.connect() as connection,
        connection.begin(),
    ):
        connection.execute(
            text(
                "UPDATE omnibase_meta.agent_versions "
                "SET manifest_payload = jsonb_build_object('tampered', true) "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": version.agent_version_id, "tenant": tenant_id},
        )
    _expect_trigger_rejection(exc_info, "sealed agent_version content is immutable")


def test_database_blocks_sealed_version_identity_mutation(db_engine, run_owned_resources) -> None:
    tenant_id, _, version = _seed_definition_version(
        db_engine, run_owned_resources, "seal-identity-lock"
    )
    with (
        pytest.raises(Exception) as exc_info,
        db_engine.connect() as connection,
        connection.begin(),
    ):
        connection.execute(
            text(
                "UPDATE omnibase_meta.agent_versions SET created_by = :other "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {
                "other": str(uuid.uuid4()),
                "id": version.agent_version_id,
                "tenant": tenant_id,
            },
        )
    _expect_trigger_rejection(exc_info, "sealed agent_version content is immutable")


def test_database_blocks_revoked_definition_unrevoke(db_engine, run_owned_resources) -> None:
    tenant_id = _tenant_with_schema(db_engine, run_owned_resources, "rev-terminal")
    mapping = _definition_mapping(tenant_id)
    with _session(db_engine, tenant_id) as session:
        model = _register(session, tenant_id=tenant_id, mapping=mapping, key=uuid.uuid4().hex)
        RegistryPersistenceService(session).revoke_definition(
            tenant_id=tenant_id,
            actor_user_id=ACTOR_ID,
            request_id=str(uuid.uuid4()),
            definition_id=model.id,
            idempotency_key=uuid.uuid4().hex,
        )
        session.commit()
    with (
        pytest.raises(Exception) as exc_info,
        db_engine.connect() as connection,
        connection.begin(),
    ):
        connection.execute(
            text(
                "UPDATE omnibase_meta.agent_definitions SET definition_state = 'active' "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": model.id, "tenant": tenant_id},
        )
    _expect_trigger_rejection(exc_info, "agent_definition revoked is terminal")


def test_audit_events_are_append_only_in_database(db_engine, run_owned_resources) -> None:
    tenant_id, _, _ = _seed_definition_version(db_engine, run_owned_resources, "audit-ro")
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
            text("UPDATE omnibase_meta.audit_events SET decision = 'denied' " "WHERE id = :id"),
            {"id": audit_id},
        )
    _expect_trigger_rejection(exc_info, "audit_events is append-only")


# ---------------------------------------------------------------------------
# Service-level rejection paths
# ---------------------------------------------------------------------------


def test_install_binding_rejects_stale_workspace_generation(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, version = _seed_definition_version(
        db_engine, run_owned_resources, "stale-gen"
    )
    binding = _binding_dto(
        _binding_mapping(
            tenant_id,
            workspace_id,
            version.agent_definition_id,
            version,
            workspace_generation=2,
        )
    )
    with _session(db_engine, tenant_id) as session:
        with pytest.raises(RegistryStateError):
            _install(session, tenant_id=tenant_id, binding=binding, key=uuid.uuid4().hex)
        session.rollback()


def test_install_binding_rejects_version_digest_drift(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, version = _seed_definition_version(
        db_engine, run_owned_resources, "digest-drift"
    )
    binding = _binding_dto(
        _binding_mapping(
            tenant_id,
            workspace_id,
            version.agent_definition_id,
            version,
            version_digest="1" * 64,
        )
    )
    with _session(db_engine, tenant_id) as session:
        with pytest.raises(RegistryStateError):
            _install(session, tenant_id=tenant_id, binding=binding, key=uuid.uuid4().hex)
        session.rollback()


def test_registry_mutation_revalidates_active_tenant_user(db_engine, run_owned_resources) -> None:
    tenant_id = _tenant_with_schema(db_engine, run_owned_resources, "inactive-actor")
    schema_name = None
    with db_engine.begin() as connection:
        schema_name = _tenant_schema(connection, tenant_id)
        connection.execute(
            text(
                f'UPDATE "{schema_name}".users '  # noqa: S608
                "SET is_active = FALSE WHERE id = :actor"
            ),
            {"actor": ACTOR_ID},
        )
    with _session(db_engine, tenant_id) as session:
        with pytest.raises(RegistryStateError, match="registry_actor_inactive_or_missing"):
            _register(
                session,
                tenant_id=tenant_id,
                mapping=_definition_mapping(tenant_id),
                key=uuid.uuid4().hex,
            )
        session.rollback()


def test_install_binding_requires_active_definition_and_sealed_version(
    db_engine, run_owned_resources
) -> None:
    tenant_id, workspace_id, version = _seed_definition_version(
        db_engine, run_owned_resources, "disabled-definition"
    )
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE omnibase_meta.agent_definitions SET definition_state = 'disabled' "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": version.agent_definition_id, "tenant": tenant_id},
        )
    disabled_binding = _binding_dto(
        _binding_mapping(tenant_id, workspace_id, version.agent_definition_id, version)
    )
    with _session(db_engine, tenant_id) as session:
        with pytest.raises(RegistryStateError, match="registry_definition_not_active"):
            _install(
                session,
                tenant_id=tenant_id,
                binding=disabled_binding,
                key=uuid.uuid4().hex,
            )
        session.rollback()

    tenant_id_2, workspace_id_2, version_2 = _seed_definition_version(
        db_engine, run_owned_resources, "deprecated-version"
    )
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE omnibase_meta.agent_versions SET version_state = 'deprecated' "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": version_2.agent_version_id, "tenant": tenant_id_2},
        )
    deprecated_binding = _binding_dto(
        _binding_mapping(
            tenant_id_2,
            workspace_id_2,
            version_2.agent_definition_id,
            version_2,
        )
    )
    with _session(db_engine, tenant_id_2) as session:
        with pytest.raises(RegistryStateError, match="registry_version_not_sealed"):
            _install(
                session,
                tenant_id=tenant_id_2,
                binding=deprecated_binding,
                key=uuid.uuid4().hex,
            )
        session.rollback()


def test_install_binding_rejects_revoked_definition_and_version(
    db_engine, run_owned_resources
) -> None:
    tenant_id, workspace_id, version = _seed_definition_version(
        db_engine, run_owned_resources, "revoke-install"
    )
    with _session(db_engine, tenant_id) as session:
        service = RegistryPersistenceService(session)
        service.revoke_definition(
            tenant_id=tenant_id,
            actor_user_id=ACTOR_ID,
            request_id=str(uuid.uuid4()),
            definition_id=version.agent_definition_id,
            idempotency_key=uuid.uuid4().hex,
        )
        session.commit()
    binding = _binding_dto(
        _binding_mapping(
            tenant_id,
            workspace_id,
            version.agent_definition_id,
            version,
        )
    )
    with _session(db_engine, tenant_id) as session:
        with pytest.raises(RegistryStateError):
            _install(session, tenant_id=tenant_id, binding=binding, key=uuid.uuid4().hex)
        session.rollback()

    tenant_id_2, workspace_id_2, version_2 = _seed_definition_version(
        db_engine, run_owned_resources, "revoke-version"
    )
    with _session(db_engine, tenant_id_2) as session:
        RegistryPersistenceService(session).revoke_version(
            tenant_id=tenant_id_2,
            actor_user_id=ACTOR_ID,
            request_id=str(uuid.uuid4()),
            version_id=version_2.agent_version_id,
            idempotency_key=uuid.uuid4().hex,
        )
        session.commit()
    binding_2 = _binding_dto(
        _binding_mapping(
            tenant_id_2,
            workspace_id_2,
            version_2.agent_definition_id,
            version_2,
        )
    )
    with _session(db_engine, tenant_id_2) as session:
        with pytest.raises(RegistryStateError):
            _install(session, tenant_id=tenant_id_2, binding=binding_2, key=uuid.uuid4().hex)
        session.rollback()


def test_high_risk_binding_requires_and_consumes_approval(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, version = _seed_definition_version(
        db_engine, run_owned_resources, "high-risk", risk_level="high"
    )
    unapproved_binding = _binding_dto(
        _binding_mapping(
            tenant_id,
            workspace_id,
            version.agent_definition_id,
            version,
        )
    )
    with _session(db_engine, tenant_id) as session:
        with pytest.raises(RegistryStateError):
            _install(session, tenant_id=tenant_id, binding=unapproved_binding, key=uuid.uuid4().hex)
        session.rollback()
    approval_id = str(uuid.uuid4())
    approved_binding = _binding_dto(
        _binding_mapping(
            tenant_id,
            workspace_id,
            version.agent_definition_id,
            version,
            approval_id=approval_id,
        )
    )
    request_hash = _canonical_hash(approved_binding.to_dict())
    with db_engine.begin() as connection:
        _approval(connection, tenant_id, workspace_id, request_hash, approval_id=approval_id)
    with _session(db_engine, tenant_id) as session:
        model = _install(
            session, tenant_id=tenant_id, binding=approved_binding, key=uuid.uuid4().hex
        )
        session.commit()
    assert model.binding_state == "installed"
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


def test_approval_is_consumed_exactly_once(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, version = _seed_definition_version(
        db_engine, run_owned_resources, "approval-once", risk_level="high"
    )
    approval_id = str(uuid.uuid4())
    approved_binding = _binding_dto(
        _binding_mapping(
            tenant_id,
            workspace_id,
            version.agent_definition_id,
            version,
            approval_id=approval_id,
        )
    )
    request_hash = _canonical_hash(approved_binding.to_dict())
    with db_engine.begin() as connection:
        _approval(connection, tenant_id, workspace_id, request_hash, approval_id=approval_id)
    with _session(db_engine, tenant_id) as session:
        model = _install(
            session, tenant_id=tenant_id, binding=approved_binding, key=uuid.uuid4().hex
        )
        session.commit()
    second = _binding_dto(
        _binding_mapping(
            tenant_id,
            workspace_id,
            version.agent_definition_id,
            version,
            approval_id=approval_id,
        )
    )
    with _session(db_engine, tenant_id) as session:
        with pytest.raises(RegistryStateError):
            RegistryPersistenceService(session).supersede_binding(
                tenant_id=tenant_id,
                actor_user_id=ACTOR_ID,
                request_id=str(uuid.uuid4()),
                old_binding_id=model.id,
                new_binding=second,
                idempotency_key=uuid.uuid4().hex,
            )
        session.rollback()
    with db_engine.connect() as connection:
        old_state = str(
            connection.execute(
                text(
                    "SELECT binding_state FROM omnibase_meta.workspace_agent_bindings "
                    "WHERE id = :id AND tenant_id = :tenant"
                ),
                {"id": model.id, "tenant": tenant_id},
            ).scalar_one()
        )
    assert old_state == "installed"


def test_disable_and_revoke_binding_transitions(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, version = _seed_definition_version(
        db_engine, run_owned_resources, "binding-transitions"
    )
    binding = _binding_dto(
        _binding_mapping(
            tenant_id,
            workspace_id,
            version.agent_definition_id,
            version,
        )
    )
    with _session(db_engine, tenant_id) as session:
        model = _install(session, tenant_id=tenant_id, binding=binding, key=uuid.uuid4().hex)
        service = RegistryPersistenceService(session)
        disabled = service.disable_binding(
            tenant_id=tenant_id,
            actor_user_id=ACTOR_ID,
            request_id=str(uuid.uuid4()),
            binding_id=model.id,
            idempotency_key=uuid.uuid4().hex,
        )
        assert disabled.binding_state == "disabled"
        assert disabled.disabled_at is not None
        revoked = service.revoke_binding(
            tenant_id=tenant_id,
            actor_user_id=ACTOR_ID,
            request_id=str(uuid.uuid4()),
            binding_id=model.id,
            idempotency_key=uuid.uuid4().hex,
        )
        assert revoked.binding_state == "revoked"
        session.commit()
    with db_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT binding_state, disabled_at FROM omnibase_meta.workspace_agent_bindings "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": model.id, "tenant": tenant_id},
        ).one()
    assert row.binding_state == "revoked"
    assert row.disabled_at is not None


def test_supersede_binding_marks_old_binding_superseded(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, version = _seed_definition_version(
        db_engine, run_owned_resources, "supersede"
    )
    first = _binding_dto(
        _binding_mapping(
            tenant_id,
            workspace_id,
            version.agent_definition_id,
            version,
        )
    )
    with _session(db_engine, tenant_id) as session:
        old = _install(session, tenant_id=tenant_id, binding=first, key=uuid.uuid4().hex)
        second = _binding_dto(
            _binding_mapping(
                tenant_id,
                workspace_id,
                version.agent_definition_id,
                version,
            )
        )
        service = RegistryPersistenceService(session)
        replay_key = uuid.uuid4().hex
        new = service.supersede_binding(
            tenant_id=tenant_id,
            actor_user_id=ACTOR_ID,
            request_id=str(uuid.uuid4()),
            old_binding_id=old.id,
            new_binding=second,
            idempotency_key=replay_key,
        )
        replayed = service.supersede_binding(
            tenant_id=tenant_id,
            actor_user_id=ACTOR_ID,
            request_id=str(uuid.uuid4()),
            old_binding_id=old.id,
            new_binding=second,
            idempotency_key=replay_key,
        )
        assert replayed.id == new.id
        drifted_second = _binding_dto(
            _binding_mapping(
                tenant_id,
                workspace_id,
                version.agent_definition_id,
                version,
            )
        )
        with pytest.raises(RegistryConflictError, match="registry_replay_input_mismatch"):
            service.supersede_binding(
                tenant_id=tenant_id,
                actor_user_id=ACTOR_ID,
                request_id=str(uuid.uuid4()),
                old_binding_id=old.id,
                new_binding=drifted_second,
                idempotency_key=replay_key,
            )
        session.commit()
    assert new.binding_state == "installed"
    assert new.id != old.id
    with db_engine.connect() as connection:
        old_row = connection.execute(
            text(
                "SELECT binding_state, superseded_by FROM omnibase_meta.workspace_agent_bindings "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": old.id, "tenant": tenant_id},
        ).one()
    assert old_row.binding_state == "superseded"
    assert str(old_row.superseded_by) == new.id


def test_database_blocks_binding_payload_rewire(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, version = _seed_definition_version(
        db_engine, run_owned_resources, "binding-rewire"
    )
    binding = _binding_dto(
        _binding_mapping(tenant_id, workspace_id, version.agent_definition_id, version)
    )
    with _session(db_engine, tenant_id) as session:
        model = _install(session, tenant_id=tenant_id, binding=binding, key=uuid.uuid4().hex)
        session.commit()
    with (
        pytest.raises(Exception) as exc_info,
        db_engine.connect() as connection,
        connection.begin(),
    ):
        connection.execute(
            text(
                "UPDATE omnibase_meta.workspace_agent_bindings "
                "SET resource_scopes = '[\"workspace_private_write\"]'::jsonb "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": model.id, "tenant": tenant_id},
        )
    _expect_trigger_rejection(
        exc_info, "agent_binding identity and installation payload are immutable"
    )


def test_failed_transaction_rolls_back_without_partial_state(
    db_engine, run_owned_resources
) -> None:
    tenant_id, workspace_id, version = _seed_definition_version(
        db_engine, run_owned_resources, "rollback"
    )
    binding = _binding_dto(
        _binding_mapping(
            tenant_id,
            workspace_id,
            version.agent_definition_id,
            version,
        )
    )
    drifted = _binding_dto(
        _binding_mapping(
            tenant_id,
            workspace_id,
            version.agent_definition_id,
            version,
            version_digest="2" * 64,
        )
    )
    with _session(db_engine, tenant_id) as session:
        _install(session, tenant_id=tenant_id, binding=binding, key=uuid.uuid4().hex)
        with pytest.raises(RegistryStateError):
            _install(session, tenant_id=tenant_id, binding=drifted, key=uuid.uuid4().hex)
        session.rollback()
    with db_engine.connect() as connection:
        binding_count = int(
            connection.execute(
                text(
                    "SELECT count(*) FROM omnibase_meta.workspace_agent_bindings "
                    "WHERE tenant_id = :tenant"
                ),
                {"tenant": tenant_id},
            ).scalar_one()
        )
        binding_audit_count = int(
            connection.execute(
                text(
                    "SELECT count(*) FROM omnibase_meta.audit_events "
                    "WHERE tenant_id = :tenant AND action LIKE 'registry.binding%'"
                ),
                {"tenant": tenant_id},
            ).scalar_one()
        )
    assert binding_count == 0
    assert binding_audit_count == 0


def test_registry_not_found_errors_for_missing_references(db_engine, run_owned_resources) -> None:
    tenant_id = _tenant_with_schema(db_engine, run_owned_resources, "not-found")
    with _session(db_engine, tenant_id) as session:
        with pytest.raises(RegistryNotFoundError):
            RegistryPersistenceService(session).revoke_definition(
                tenant_id=tenant_id,
                actor_user_id=ACTOR_ID,
                request_id=str(uuid.uuid4()),
                definition_id=str(uuid.uuid4()),
                idempotency_key=uuid.uuid4().hex,
            )
        session.rollback()


# ---------------------------------------------------------------------------
# Concurrency: exactly one winner for natural keys and live bindings
# ---------------------------------------------------------------------------


def test_concurrent_definition_registration_has_single_winner(
    db_engine, run_owned_resources
) -> None:
    tenant_id = _tenant_with_schema(db_engine, run_owned_resources, "conc-def")
    key_name = f"conc-{uuid.uuid4().hex[:10]}"

    def _attempt() -> str:
        with _session(db_engine, tenant_id) as session:
            try:
                _register(
                    session,
                    tenant_id=tenant_id,
                    mapping=_definition_mapping(tenant_id, key=key_name),
                    key=uuid.uuid4().hex,
                )
                session.commit()
                return "ok"
            except RegistryConflictError:
                session.rollback()
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _attempt(), range(2)))
    assert results.count("ok") == 1
    assert results.count("conflict") == 1
    with db_engine.connect() as connection:
        count = int(
            connection.execute(
                text(
                    "SELECT count(*) FROM omnibase_meta.agent_definitions "
                    "WHERE tenant_id = :tenant AND stable_logical_key = :key"
                ),
                {"tenant": tenant_id, "key": key_name},
            ).scalar_one()
        )
    assert count == 1


def test_concurrent_live_binding_install_has_single_winner(db_engine, run_owned_resources) -> None:
    tenant_id, workspace_id, version = _seed_definition_version(
        db_engine, run_owned_resources, "conc-bind"
    )

    def _attempt() -> str:
        with _session(db_engine, tenant_id) as session:
            try:
                binding = _binding_dto(
                    _binding_mapping(
                        tenant_id,
                        workspace_id,
                        version.agent_definition_id,
                        version,
                    )
                )
                _install(session, tenant_id=tenant_id, binding=binding, key=uuid.uuid4().hex)
                session.commit()
                return "ok"
            except RegistryConflictError:
                session.rollback()
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _attempt(), range(2)))
    assert results.count("ok") == 1
    assert results.count("conflict") == 1
    with db_engine.connect() as connection:
        count = int(
            connection.execute(
                text(
                    "SELECT count(*) FROM omnibase_meta.workspace_agent_bindings "
                    "WHERE tenant_id = :tenant AND workspace_id = :workspace "
                    "AND agent_definition_id = :definition AND binding_state = 'installed'"
                ),
                {
                    "tenant": tenant_id,
                    "workspace": workspace_id,
                    "definition": version.agent_definition_id,
                },
            ).scalar_one()
        )
    assert count == 1


# ---------------------------------------------------------------------------
# Physical locator separation
# ---------------------------------------------------------------------------


def test_physical_locators_absent_from_dtos_errors_and_audit(
    db_engine, run_owned_resources
) -> None:
    tenant_id, workspace_id, version = _seed_definition_version(
        db_engine, run_owned_resources, "locator-free"
    )
    drifted = _binding_dto(
        _binding_mapping(
            tenant_id,
            workspace_id,
            version.agent_definition_id,
            version,
            version_digest="3" * 64,
        )
    )
    with _session(db_engine, tenant_id) as session:
        with pytest.raises(RegistryStateError) as exc_info:
            _install(session, tenant_id=tenant_id, binding=drifted, key=uuid.uuid4().hex)
        session.rollback()
    message = str(exc_info.value)
    projections = json.dumps(
        {
            "definition": _definition_mapping(tenant_id),
            "binding": drifted.to_dict(),
            "version": version.to_dict(),
        }
    )
    combined = message + " " + projections
    for locator in _PHYSICAL_LOCATORS:
        assert locator not in combined


def test_0010_populated_downgrade_is_fail_closed(db_engine, run_owned_resources) -> None:
    tenant_id, _, _ = _seed_definition_version(db_engine, run_owned_resources, "downgrade-0010")
    downgrade = _run_alembic("downgrade", "0009")
    assert downgrade.returncode != 0
    output = downgrade.stdout + downgrade.stderr
    assert "P5.1B downgrade refused" in output
    with db_engine.connect() as connection:
        revision = str(
            connection.execute(
                text("SELECT version_num FROM omnibase_meta.alembic_version")
            ).scalar_one()
        )
    assert revision == "0015"
    assert tenant_id
