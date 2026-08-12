"""Disposable PostgreSQL proof for the personal single-Owner admission chain."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from omnibase.production.personal_owner_gate import (
    PersonalGateState,
    PersonalOwnerGate,
    PersonalOwnerGateConfig,
    PersonalOwnerGateRequest,
)

if os.environ.get("OMNIBASE_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "personal Owner integration tests require OMNIBASE_INTEGRATION_TESTS=1",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _upgrade_head() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_BACKEND_ROOT,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(scope="module", autouse=True)
def personal_gate_schema(db_engine) -> None:
    _upgrade_head()


def _config(tmp_path: Path) -> PersonalOwnerGateConfig:
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "passed": True,
                "profile": "personal_single_owner",
                "migration_0013_created": True,
                "migration_head": "0015",
                "root_env_accessed": False,
                "business_database_accessed": False,
                "business_database_migrated": False,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return PersonalOwnerGateConfig.from_mapping(
        {
            "schema_version": 1,
            "policy": {
                "profile": "personal_single_owner",
                "sandbox_mode": "workspace_auto",
                "approval_policy": "owner_preapproved_exact_scope",
                "network": {"default_deny": True, "destinations": []},
                "external_side_effects": False,
            },
            "engineering_evidence": {
                "path": "evidence.json",
                "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                "assertions": {
                    "passed": True,
                    "profile": "personal_single_owner",
                    "migration_0013_created": True,
                    "migration_head": "0015",
                    "root_env_accessed": False,
                    "business_database_accessed": False,
                    "business_database_migrated": False,
                },
            },
            "migration_head": "0015",
            "migration_0013_created": True,
            "agent_runtime_enabled": False,
            "agent_planner_enabled": False,
            "multi_agent_enabled": False,
            "enterprise_approved_digest_present": False,
        }
    )


def _create_tenant(db_engine) -> tuple[str, str]:
    tenant_id = str(uuid.uuid4())
    suffix = uuid.uuid4().hex[:8]
    schema = f"tenant_{suffix}"
    with db_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.tenants "
                "(id, name, slug, schema_name, is_default, is_active) "
                "VALUES (:id, :name, :slug, :schema, FALSE, TRUE)"
            ),
            {
                "id": tenant_id,
                "name": "P34.7 personal Owner Gate",
                "slug": f"personal-{suffix}",
                "schema": schema,
            },
        )
    _upgrade_head()
    return tenant_id, schema


def _seed(session: Session, tenant_id: str, schema: str, config: PersonalOwnerGateConfig):
    ids = {
        name: str(uuid.uuid4())
        for name in (
            "agent",
            "approval",
            "grant",
            "lease",
            "node",
            "operation",
            "owner",
            "run",
            "runtime",
            "template",
            "workspace",
        )
    }
    now = datetime.now(UTC)
    workload = "a" * 64
    request_digest = "b" * 64
    plan_digest = "c" * 64
    tool_digest = "d" * 64
    session.execute(text(f'SET LOCAL search_path TO "{schema}", omnibase_meta, public'))
    session.execute(
        text(
            f'INSERT INTO "{schema}".users '  # noqa: S608 - generated safe tenant schema
            "(id, email, password_hash, is_tenant_admin, is_active) "
            "VALUES (:id, :email, :password, TRUE, TRUE)"
        ),
        {
            "id": ids["owner"],
            "email": f"owner-{uuid.uuid4().hex}@example.invalid",
            "password": uuid.uuid4().hex,
        },
    )
    session.execute(
        text(
            "INSERT INTO omnibase_meta.workspace_templates "
            "(id, tenant_id, template_key, version, display_name, digest, template_spec, "
            "created_by_user_id) VALUES (:id, :tenant, :key, 1, 'Personal', :digest, "
            '\'{"profile":"personal"}\'::jsonb, :owner)'
        ),
        {
            "id": ids["template"],
            "tenant": tenant_id,
            "key": f"personal-{uuid.uuid4().hex[:8]}",
            "digest": "e" * 64,
            "owner": ids["owner"],
        },
    )
    session.execute(
        text(
            "INSERT INTO omnibase_meta.resource_registry "
            "(id, tenant_id, kind, owner_type, owner_id, display_name, state, policy_class, "
            "created_by_actor_id) VALUES (:id, :tenant, 'workspace', 'user', :owner, "
            "'Personal AI space', 'stopped', 'workspace_private', :owner)"
        ),
        {"id": ids["workspace"], "tenant": tenant_id, "owner": ids["owner"]},
    )
    session.execute(
        text(
            "INSERT INTO omnibase_meta.workspaces "
            "(id, tenant_id, template_id, owner_user_id, display_name, desired_state, "
            "observed_state, generation, quota) VALUES (:id, :tenant, :template, :owner, "
            "'Personal AI space', 'running', 'running', 1, CAST(:quota AS jsonb))"
        ),
        {
            "id": ids["workspace"],
            "tenant": tenant_id,
            "template": ids["template"],
            "owner": ids["owner"],
            "quota": json.dumps({"max_active_runs": 1}),
        },
    )
    session.execute(
        text(
            "INSERT INTO omnibase_meta.workspace_memberships "
            "(tenant_id, workspace_id, user_id, role, state, created_by_user_id) "
            "VALUES (:tenant, :workspace, :owner, 'owner', 'active', :owner)"
        ),
        {"tenant": tenant_id, "workspace": ids["workspace"], "owner": ids["owner"]},
    )
    for resource_id, kind, owner_type, owner_id, parent_id, label, state in (
        (
            ids["run"],
            "run",
            "workspace",
            ids["workspace"],
            ids["workspace"],
            "Personal run",
            "running",
        ),
        (
            ids["agent"],
            "agent",
            "workspace",
            ids["workspace"],
            ids["workspace"],
            "Personal agent",
            "active",
        ),
    ):
        session.execute(
            text(
                "INSERT INTO omnibase_meta.resource_registry "
                "(id, tenant_id, kind, owner_type, owner_id, parent_id, display_name, state, "
                "policy_class, created_by_actor_id) VALUES (:id, :tenant, :kind, :owner_type, "
                ":owner_id, :parent, :label, :state, 'workspace_private', :created_by)"
            ),
            {
                "id": resource_id,
                "tenant": tenant_id,
                "kind": kind,
                "owner_type": owner_type,
                "owner_id": owner_id,
                "parent": parent_id,
                "label": label,
                "state": state,
                "created_by": ids["owner"],
            },
        )
    session.execute(
        text(
            "INSERT INTO omnibase_meta.workspace_runs "
            "(id, tenant_id, workspace_id, kind, generation, desired_state, observed_state, "
            "next_fencing_token, request_digest, runtime_instance_id, workload_identity_digest, "
            "created_by_user_id) VALUES (:id, :tenant, :workspace, 'interactive', 1, "
            "'running', 'leased', 2, :request, :runtime, :workload, :owner)"
        ),
        {
            "id": ids["run"],
            "tenant": tenant_id,
            "workspace": ids["workspace"],
            "request": request_digest,
            "runtime": ids["runtime"],
            "workload": workload,
            "owner": ids["owner"],
        },
    )
    session.execute(
        text(
            "INSERT INTO omnibase_meta.workspace_nodes "
            "(id, tenant_id, workspace_id, owner_user_id, display_name, identity_digest, "
            "state, attestation_state, fencing_token) VALUES (:id, :tenant, :workspace, "
            ":owner, 'Personal Runner', :digest, 'active', 'verified', 1)"
        ),
        {
            "id": ids["node"],
            "tenant": tenant_id,
            "workspace": ids["workspace"],
            "owner": ids["owner"],
            "digest": "f" * 64,
        },
    )
    session.execute(
        text(
            "INSERT INTO omnibase_meta.node_attestations "
            "(tenant_id, node_id, nonce_digest, evidence_digest, verifier, state, verified_at, "
            "expires_at) VALUES (:tenant, :node, :nonce, :evidence, 'personal-gate-test', "
            "'verified', :verified, :expires)"
        ),
        {
            "tenant": tenant_id,
            "node": ids["node"],
            "nonce": "1" * 64,
            "evidence": "2" * 64,
            "verified": now - timedelta(minutes=1),
            "expires": now + timedelta(minutes=10),
        },
    )
    session.execute(
        text(
            "INSERT INTO omnibase_meta.run_leases "
            "(id, tenant_id, run_id, workspace_id, node_id, node_fencing_token, generation, "
            "fencing_token, state, heartbeat_at, expires_at) VALUES (:id, :tenant, :run, "
            ":workspace, :node, 1, 1, 1, 'active', :heartbeat, :expires)"
        ),
        {
            "id": ids["lease"],
            "tenant": tenant_id,
            "run": ids["run"],
            "workspace": ids["workspace"],
            "node": ids["node"],
            "heartbeat": now,
            "expires": now + timedelta(minutes=5),
        },
    )
    session.execute(
        text(
            "INSERT INTO omnibase_meta.capability_grants "
            "(id, tenant_id, workspace_id, runtime_instance_id, workload_identity_digest, "
            "actor_user_id, actions, resource_ids, constraints, not_before, expires_at, "
            "max_calls, max_bytes, max_cost_units, delegation_depth, delegation_depth_limit, "
            "created_by_actor_type, created_by_actor_id) VALUES (:id, :tenant, :workspace, "
            ":runtime, :workload, :owner, ARRAY['sandbox.exec']::varchar[], "
            "ARRAY[:workspace]::uuid[], CAST(:constraints AS jsonb), :not_before, "
            ":expires, 10, 10000, 10, 0, 0, 'system', :issuer)"
        ),
        {
            "id": ids["grant"],
            "tenant": tenant_id,
            "workspace": ids["workspace"],
            "runtime": ids["runtime"],
            "workload": workload,
            "owner": ids["owner"],
            "constraints": json.dumps({"timeout_ms": 1500}),
            "not_before": now - timedelta(minutes=1),
            "expires": now + timedelta(minutes=5),
            "issuer": str(uuid.uuid4()),
        },
    )
    session.execute(
        text(
            "INSERT INTO omnibase_meta.capability_usage (grant_id, tenant_id) "
            "VALUES (:grant, :tenant)"
        ),
        {"grant": ids["grant"], "tenant": tenant_id},
    )
    session.execute(
        text(
            "INSERT INTO omnibase_meta.operations "
            "(id, tenant_id, workspace_id, run_id, actor_type, actor_id, resource_id, "
            "resource_version, request_hash, kind, state, risk_level) VALUES (:id, :tenant, "
            ":workspace, :run, 'agent', :agent, :resource, 1, :request, 'sandbox.exec', "
            "'pending_approval', 'R2')"
        ),
        {
            "id": ids["operation"],
            "tenant": tenant_id,
            "workspace": ids["workspace"],
            "run": ids["run"],
            "agent": ids["agent"],
            "resource": ids["workspace"],
            "request": request_digest,
        },
    )
    metadata = {
        "approval_policy": config.policy.approval_policy,
        "external_side_effects": config.policy.external_side_effects,
        "network_policy_sha256": config.policy.network.canonical_digest(),
        "plan_sha256": plan_digest,
        "profile": "personal_single_owner",
        "sandbox_mode": config.policy.sandbox_mode,
        "tool_schema_sha256": tool_digest,
    }
    session.execute(
        text(
            "INSERT INTO omnibase_meta.approval_requests "
            "(id, tenant_id, requester_type, requester_id, workspace_id, run_id, resource_id, "
            "operation_id, grant_id, action, risk_level, required_approver_role, state, "
            "request_hash, resource_version, version, decided_by_actor_type, "
            "decided_by_actor_id, expires_at, decided_at, metadata, created_at) VALUES (:id, :tenant, "
            "'agent', :agent, :workspace, :run, :resource, :operation, :grant, 'sandbox.exec', "
            "'R2', 'tenant_admin', 'approved', :request, 1, 2, 'user', :owner, :expires, "
            ":decided, CAST(:metadata AS jsonb), :created)"
        ),
        {
            "id": ids["approval"],
            "tenant": tenant_id,
            "agent": ids["agent"],
            "workspace": ids["workspace"],
            "run": ids["run"],
            "resource": ids["workspace"],
            "operation": ids["operation"],
            "grant": ids["grant"],
            "request": request_digest,
            "owner": ids["owner"],
            "expires": now + timedelta(minutes=5),
            "decided": now,
            "created": now - timedelta(minutes=1),
            "metadata": json.dumps(metadata, sort_keys=True),
        },
    )
    session.flush()
    request = PersonalOwnerGateRequest.from_mapping(
        {
            "tenant_id": tenant_id,
            "workspace_id": ids["workspace"],
            "run_id": ids["run"],
            "runtime_instance_id": ids["runtime"],
            "lease_id": ids["lease"],
            "node_id": ids["node"],
            "generation": 1,
            "run_fencing_token": 1,
            "workload_identity_digest": workload,
            "approval_id": ids["approval"],
            "approval_expected_version": 2,
            "operation_id": ids["operation"],
            "requester_type": "agent",
            "requester_id": ids["agent"],
            "action": "sandbox.exec",
            "resource_id": ids["workspace"],
            "resource_version": 1,
            "request_digest": request_digest,
            "plan_digest": plan_digest,
            "tool_schema_digest": tool_digest,
            "grant_id": ids["grant"],
            "requested_calls": 1,
            "requested_bytes": 512,
            "requested_cost_units": 1,
        }
    )
    return ids, request


def test_live_persisted_owner_approval_capability_and_lease_chain_is_ready(
    db_engine, tmp_path: Path
) -> None:
    tenant_id, schema = _create_tenant(db_engine)
    config = _config(tmp_path)
    with Session(db_engine) as session, session.begin():
        ids, request = _seed(session, tenant_id, schema, config)
        report = PersonalOwnerGate(tmp_path).verify(session, config=config, request=request)

        assert report.state is PersonalGateState.READY
        assert report.owner_user_id == ids["owner"]
        assert report.personal_activation_ready is True
        assert report.runtime_activated is False
        assert report.vetoes == ()


def test_second_member_and_node_fencing_drift_fail_closed(db_engine, tmp_path: Path) -> None:
    tenant_id, schema = _create_tenant(db_engine)
    config = _config(tmp_path)
    with Session(db_engine) as session, session.begin():
        ids, request = _seed(session, tenant_id, schema, config)
        second_user = str(uuid.uuid4())
        session.execute(
            text(
                f'INSERT INTO "{schema}".users '  # noqa: S608 - generated safe tenant schema
                "(id, email, password_hash, is_tenant_admin, is_active) "
                "VALUES (:id, :email, :password, FALSE, TRUE)"
            ),
            {
                "id": second_user,
                "email": f"member-{uuid.uuid4().hex}@example.invalid",
                "password": uuid.uuid4().hex,
            },
        )
        session.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_memberships "
                "(tenant_id, workspace_id, user_id, role, state, created_by_user_id) "
                "VALUES (:tenant, :workspace, :user, 'member', 'active', :owner)"
            ),
            {
                "tenant": tenant_id,
                "workspace": ids["workspace"],
                "user": second_user,
                "owner": ids["owner"],
            },
        )
        report = PersonalOwnerGate(tmp_path).verify(session, config=config, request=request)
        assert report.state is PersonalGateState.INVALID
        assert any("exactly one active Owner" in item for item in report.vetoes)

        session.execute(
            text(
                "UPDATE omnibase_meta.workspace_memberships SET state='revoked' "
                "WHERE tenant_id=:tenant AND workspace_id=:workspace AND user_id=:user"
            ),
            {"tenant": tenant_id, "workspace": ids["workspace"], "user": second_user},
        )
        session.execute(
            text(
                "UPDATE omnibase_meta.workspace_nodes SET fencing_token=2 "
                "WHERE tenant_id=:tenant AND id=:node"
            ),
            {"tenant": tenant_id, "node": ids["node"]},
        )
        session.expire_all()
        report = PersonalOwnerGate(tmp_path).verify(session, config=config, request=request)
        assert report.state is PersonalGateState.INVALID
        assert any("RunLease" in item for item in report.vetoes)


def test_consumed_approval_is_not_reusable(db_engine, tmp_path: Path) -> None:
    tenant_id, schema = _create_tenant(db_engine)
    config = _config(tmp_path)
    with Session(db_engine) as session, session.begin():
        ids, request = _seed(session, tenant_id, schema, config)
        session.execute(
            text(
                "UPDATE omnibase_meta.approval_requests SET state='consumed', consumed_at=now(), "
                "version=3 WHERE tenant_id=:tenant AND id=:approval"
            ),
            {"tenant": tenant_id, "approval": ids["approval"]},
        )
        report = PersonalOwnerGate(tmp_path).verify(session, config=config, request=request)

        assert report.state is PersonalGateState.INVALID
        assert any("not approved and unconsumed" in item for item in report.blockers)
        assert any("exact binding drifted" in item for item in report.vetoes)
