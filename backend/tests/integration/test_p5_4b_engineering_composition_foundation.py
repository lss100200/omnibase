"""Disposable PostgreSQL proof for the P5.4B engineering composition seam."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import text
from sqlalchemy.orm import Session

from omnibase.agent_executor import (
    ExecutorInvocationContext,
    KnowledgeSearchRequest,
    build_engineering_single_agent_executor,
)
from omnibase.capabilities.token import encode_capability_token
from omnibase.capability_gateway.audit import ControlPlaneGatewayAuditSink
from omnibase.capability_gateway.contracts import (
    RagSearchResult,
    TrustedWorkloadContext,
    WorkloadCredential,
)
from omnibase.capability_gateway.resolver import RegistryResourceResolver
from omnibase.capability_gateway.security import CoreCapabilityVerifier
from omnibase.capability_gateway.service import GatewayComponents, GatewayService
from tests.test_p5_4a_typed_executor import _context, _plan

if os.environ.get("OMNIBASE_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "P5.4B integration tests require OMNIBASE_INTEGRATION_TESTS=1", allow_module_level=True
    )

pytestmark = pytest.mark.integration
TENANT = "00000000-0000-0000-0000-00000000000a"
WORKSPACE = "66666666-6666-6666-6666-666666666666"
RESOURCE = "77777777-7777-7777-7777-777777777777"
TASK = "00000000-0000-0000-0000-0000000000e1"
RUN = "00000000-0000-0000-0000-0000000000f1"
WORKSPACE_RUN = "00000000-0000-0000-0000-0000000000f2"
DEFINITION = "00000000-0000-0000-0000-000000000001"
VERSION = "11111111-1111-1111-1111-111111111111"
BINDING = "22222222-2222-2222-2222-222222222222"
TEMPLATE = "33333333-3333-3333-3333-333333333333"
NODE = "bb000000-bb00-bb00-bb00-bb0000000001"
LEASE = "cc000000-cc00-cc00-cc00-cc0000000001"
GRANT = "88888888-8888-8888-8888-888888888888"
RUNTIME = "99999999-9999-9999-9999-999999999999"

DIGEST = "ab" * 32
VERSION_DIGEST = "ae" * 32
CERTIFICATE_THUMBPRINT = "3" * 64
REQUEST_HASH = "cc" * 32
PLAN_ID = "aa000000-aa00-aa00-aa00-aa0000000001"
MODEL_POLICY = "44444444-4444-4444-4444-444444444444"


def _upgrade_head() -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "src/omnibase/migrations")
    )
    command.upgrade(config, "head")


class _RagAdapter:
    def search(self, session: Session, **kwargs) -> RagSearchResult:
        del session, kwargs
        from omnibase.capability_gateway.contracts import SearchHitRead

        return RagSearchResult(
            hits=[
                SearchHitRead(
                    citation_id=uuid.uuid4(),
                    document_id=uuid.uuid4(),
                    score=0.95,
                    snippet="composition result",
                    page_number=1,
                )
            ],
            bytes_out=128,
            truncated=False,
        )

    def read_citations(self, *args, **kwargs):
        raise AssertionError("citation reads are outside P5.4B scope")


class _CredentialSeam:
    def __init__(self, credential: WorkloadCredential) -> None:
        self.credential = credential
        self.calls = 0

    def issue(self, *, context: ExecutorInvocationContext) -> WorkloadCredential:
        self.calls += 1
        assert context.tenant_id == TENANT
        assert context.workspace_id == WORKSPACE
        assert context.run_id == RUN
        return self.credential


def _resource(
    connection, *, resource_id: str, kind: str, owner_type: str, owner_id: str | None, policy: str
) -> None:
    connection.execute(
        text(
            "INSERT INTO omnibase_meta.resource_registry "
            "(id,tenant_id,kind,owner_type,owner_id,parent_id,display_name,state,version,policy_class) "
            "VALUES (:id,:tenant,:kind,:owner_type,:owner_id,:parent,:name,'active',1,:policy) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {
            "id": resource_id,
            "tenant": TENANT,
            "kind": kind,
            "owner_type": owner_type,
            "owner_id": owner_id,
            "parent": WORKSPACE if owner_type == "workspace" and resource_id != WORKSPACE else None,
            "name": f"P5.4B {kind}",
            "policy": policy,
        },
    )


def _seed(db_engine, *, plan_digest: str) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    schema = "tenant_p54b0001"
    _upgrade_head()
    from omnibase.tenants.service import _initialize_tenant_schema

    with db_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        _initialize_tenant_schema(connection, schema)
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.tenants "
                "(id,name,slug,schema_name,is_default,is_active) "
                "VALUES (:id,'P5.4B','p54b',:schema,FALSE,TRUE) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": TENANT, "schema": schema},
        )
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenant_p54b0001.users "
                "(id,email,password_hash,is_tenant_admin,is_active) "
                "VALUES (:id,:email,:hash,TRUE,TRUE) ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": "00000000-0000-0000-0000-0000000000aa",
                "email": "p54b@example.invalid",
                "hash": "integration-test",
            },
        )
        _resource(
            connection,
            resource_id=WORKSPACE,
            kind="workspace",
            owner_type="system",
            owner_id=None,
            policy="workspace_private",
        )
        _resource(
            connection,
            resource_id=RESOURCE,
            kind="derived_index",
            owner_type="workspace",
            owner_id=WORKSPACE,
            policy="workspace_derived",
        )
        _resource(
            connection,
            resource_id=TASK,
            kind="agent_task",
            owner_type="workspace",
            owner_id=WORKSPACE,
            policy="workspace_private",
        )
        _resource(
            connection,
            resource_id=WORKSPACE_RUN,
            kind="workspace_run",
            owner_type="workspace",
            owner_id=WORKSPACE,
            policy="workspace_private",
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_templates "
                "(id,tenant_id,template_key,version,display_name,digest,template_spec,state,created_by_user_id) "
                "VALUES (:id,:tenant,'p54b',1,'P5.4B',:digest,'{}'::jsonb,'active',:actor) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": TEMPLATE,
                "tenant": TENANT,
                "digest": DIGEST,
                "actor": "00000000-0000-0000-0000-0000000000aa",
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspaces "
                "(id,tenant_id,template_id,owner_user_id,display_name,desired_state,observed_state,generation,version,quota) "
                "VALUES (:id,:tenant,:template,:actor,'P5.4B','running','running',1,1,'{}'::jsonb) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": WORKSPACE,
                "tenant": TENANT,
                "template": TEMPLATE,
                "actor": "00000000-0000-0000-0000-0000000000aa",
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_memberships "
                "(tenant_id,workspace_id,user_id,role,state,version,created_by_user_id) "
                "SELECT :tenant,:workspace,:actor,'owner','active',1,:actor "
                "WHERE NOT EXISTS (SELECT 1 FROM omnibase_meta.workspace_memberships "
                "WHERE tenant_id=:tenant AND workspace_id=:workspace AND user_id=:actor "
                "AND state IN ('active','suspended'))"
            ),
            {
                "tenant": TENANT,
                "workspace": WORKSPACE,
                "actor": "00000000-0000-0000-0000-0000000000aa",
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.agent_definitions "
                "(id,tenant_id,stable_logical_key,display_name,description,risk_level,installation_scopes,definition_state,created_by,metadata_version) "
                "VALUES (:id,:tenant,'p54b-agent','P5.4B agent','integration','low','[\"workspace\"]'::jsonb,'active',:actor,1) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": DEFINITION, "tenant": TENANT, "actor": "00000000-0000-0000-0000-0000000000aa"},
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.agent_versions "
                "(id,tenant_id,definition_id,version,version_state,manifest_payload,manifest_digest,model_policy_id,instructions_digest,max_context_tokens,allowed_tool_ids,input_schema,output_schema,max_concurrency,default_budget,risk_level,created_by) "
                "VALUES (:id,:tenant,:definition,'1.0.0','sealed','{}'::jsonb,:version_digest,:model,:instructions_digest,4096,'[\"knowledge_search\"]'::jsonb,'{}'::jsonb,'{}'::jsonb,1,'{}'::jsonb,'low',:actor) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": VERSION,
                "tenant": TENANT,
                "definition": DEFINITION,
                "version_digest": VERSION_DIGEST,
                "model": MODEL_POLICY,
                "instructions_digest": DIGEST,
                "actor": "00000000-0000-0000-0000-0000000000aa",
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_agent_bindings "
                "(id,tenant_id,workspace_id,workspace_generation,agent_definition_id,agent_version_id,agent_version_digest,binding_state,resource_scopes,default_budget_policy,installed_by) "
                "VALUES (:id,:tenant,:workspace,1,:definition,:version,:digest,'installed','[\"workspace-docs\"]'::jsonb,'{}'::jsonb,:actor) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": BINDING,
                "tenant": TENANT,
                "workspace": WORKSPACE,
                "definition": DEFINITION,
                "version": VERSION,
                "digest": VERSION_DIGEST,
                "actor": "00000000-0000-0000-0000-0000000000aa",
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_runs "
                "(id,tenant_id,workspace_id,kind,generation,desired_state,observed_state,next_fencing_token,version,request_digest,runtime_instance_id,workload_identity_digest,created_by_user_id) "
                "VALUES (:id,:tenant,:workspace,'batch',1,'running','running',2,1,:request,:runtime,:workload,:actor) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": WORKSPACE_RUN,
                "tenant": TENANT,
                "workspace": WORKSPACE,
                "request": REQUEST_HASH,
                "runtime": RUNTIME,
                "workload": DIGEST,
                "actor": "00000000-0000-0000-0000-0000000000aa",
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_nodes "
                "(id,tenant_id,workspace_id,owner_user_id,display_name,identity_digest,state,attestation_state,fencing_token,version,last_seen_at) "
                "VALUES (:id,:tenant,:workspace,:actor,'P5.4B node',:digest,'active','verified',7,1,now()) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": NODE,
                "tenant": TENANT,
                "workspace": WORKSPACE,
                "actor": "00000000-0000-0000-0000-0000000000aa",
                "digest": DIGEST,
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.node_attestations "
                "(tenant_id,node_id,nonce_digest,evidence_digest,verifier,state,verified_at,expires_at) "
                "VALUES (:tenant,:node,:nonce,:evidence,'p54b','verified',now()-interval '1 second',now()+interval '10 minutes') "
                "ON CONFLICT (tenant_id,nonce_digest) DO UPDATE SET "
                "node_id=EXCLUDED.node_id, evidence_digest=EXCLUDED.evidence_digest, verifier=EXCLUDED.verifier, state=EXCLUDED.state, verified_at=EXCLUDED.verified_at, expires_at=EXCLUDED.expires_at"
            ),
            {"tenant": TENANT, "node": NODE, "nonce": "01" * 32, "evidence": "02" * 32},
        )
        connection.execute(
            text("DELETE FROM omnibase_meta.agent_runs WHERE id=:run"),
            {"run": RUN},
        )
        connection.execute(
            text("DELETE FROM omnibase_meta.agent_tasks WHERE id=:task"),
            {"task": TASK},
        )
        connection.execute(
            text("DELETE FROM omnibase_meta.run_leases WHERE id=:lease"),
            {"lease": LEASE},
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.run_leases "
                "(id,tenant_id,run_id,workspace_id,node_id,node_fencing_token,generation,fencing_token,state,heartbeat_at,expires_at) "
                "VALUES (:id,:tenant,:run,:workspace,:node,7,1,1,'active',now(),now()+interval '10 minutes') "
                "ON CONFLICT (id) DO UPDATE SET "
                "tenant_id=EXCLUDED.tenant_id, run_id=EXCLUDED.run_id, workspace_id=EXCLUDED.workspace_id, node_id=EXCLUDED.node_id, node_fencing_token=EXCLUDED.node_fencing_token, generation=EXCLUDED.generation, fencing_token=EXCLUDED.fencing_token, state=EXCLUDED.state, heartbeat_at=EXCLUDED.heartbeat_at, expires_at=EXCLUDED.expires_at"
            ),
            {
                "id": LEASE,
                "tenant": TENANT,
                "run": WORKSPACE_RUN,
                "workspace": WORKSPACE,
                "node": NODE,
            },
        )
        connection.execute(
            text("DELETE FROM omnibase_meta.agent_runs WHERE id=:run"),
            {"run": RUN},
        )
        connection.execute(
            text("DELETE FROM omnibase_meta.agent_tasks WHERE id=:task"),
            {"task": TASK},
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.agent_tasks "
                "(id,tenant_id,workspace_id,workspace_generation,actor_user_id,agent_definition_id,agent_version_id,agent_version_digest,workspace_agent_binding_id,task_generation,plan_id,plan_version,plan_digest,deadline,state,resource_scope_digest,budget_policy_digest,request_hash) "
                "VALUES (:id,:tenant,:workspace,1,:actor,:definition,:version,:digest,:binding,1,:plan,1,:plan_digest,now()+interval '10 minutes','scheduled',:scope,:budget,:request) "
                "ON CONFLICT (id) DO UPDATE SET "
                "tenant_id=EXCLUDED.tenant_id, workspace_id=EXCLUDED.workspace_id, workspace_generation=EXCLUDED.workspace_generation, actor_user_id=EXCLUDED.actor_user_id, agent_definition_id=EXCLUDED.agent_definition_id, agent_version_id=EXCLUDED.agent_version_id, agent_version_digest=EXCLUDED.agent_version_digest, workspace_agent_binding_id=EXCLUDED.workspace_agent_binding_id, task_generation=EXCLUDED.task_generation, plan_id=EXCLUDED.plan_id, plan_version=EXCLUDED.plan_version, plan_digest=EXCLUDED.plan_digest, deadline=EXCLUDED.deadline, state=EXCLUDED.state, resource_scope_digest=EXCLUDED.resource_scope_digest, budget_policy_digest=EXCLUDED.budget_policy_digest, request_hash=EXCLUDED.request_hash"
            ),
            {
                "id": TASK,
                "tenant": TENANT,
                "workspace": WORKSPACE,
                "actor": "00000000-0000-0000-0000-0000000000aa",
                "definition": DEFINITION,
                "version": VERSION,
                "digest": VERSION_DIGEST,
                "binding": BINDING,
                "plan": PLAN_ID,
                "plan_digest": plan_digest,
                "scope": "c1" * 32,
                "budget": "c2" * 32,
                "request": REQUEST_HASH,
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.agent_runs "
                "(id,tenant_id,task_id,workspace_id,workspace_generation,workspace_run_id,runtime_instance_id,workload_identity_digest,node_id,node_fencing_token,run_lease_id,run_fencing_token,state) "
                "VALUES (:id,:tenant,:task,:workspace,1,:workspace_run,:runtime,:workload,:node,7,:lease,1,'leased') "
                "ON CONFLICT (id) DO UPDATE SET "
                "tenant_id=EXCLUDED.tenant_id, task_id=EXCLUDED.task_id, workspace_id=EXCLUDED.workspace_id, workspace_generation=EXCLUDED.workspace_generation, workspace_run_id=EXCLUDED.workspace_run_id, runtime_instance_id=EXCLUDED.runtime_instance_id, workload_identity_digest=EXCLUDED.workload_identity_digest, node_id=EXCLUDED.node_id, node_fencing_token=EXCLUDED.node_fencing_token, run_lease_id=EXCLUDED.run_lease_id, run_fencing_token=EXCLUDED.run_fencing_token, state=EXCLUDED.state"
            ),
            {
                "id": RUN,
                "tenant": TENANT,
                "task": TASK,
                "workspace": WORKSPACE,
                "workspace_run": WORKSPACE_RUN,
                "runtime": RUNTIME,
                "workload": DIGEST,
                "node": NODE,
                "lease": LEASE,
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.capability_grants "
                "(id,tenant_id,workspace_id,runtime_instance_id,actor_user_id,actions,resource_ids,constraints,version,state,not_before,expires_at,max_calls,max_bytes,max_cost_units,delegation_depth,delegation_depth_limit,created_by_actor_type,created_by_actor_id) "
                "VALUES (:id,:tenant,:workspace,:runtime,:actor,ARRAY['rag.search']::varchar[],ARRAY[:resource]::uuid[],jsonb_build_object('max_result_bytes',1048576,'timeout_ms',3000),1,'active',now(),now()+interval '4 minutes',100,10485760,100,0,0,'system',:actor) "
                "ON CONFLICT (id) DO UPDATE SET "
                "tenant_id=EXCLUDED.tenant_id, workspace_id=EXCLUDED.workspace_id, "
                "runtime_instance_id=EXCLUDED.runtime_instance_id, actor_user_id=EXCLUDED.actor_user_id, "
                "actions=EXCLUDED.actions, resource_ids=EXCLUDED.resource_ids, constraints=EXCLUDED.constraints, "
                "version=EXCLUDED.version, state=EXCLUDED.state, not_before=EXCLUDED.not_before, "
                "expires_at=EXCLUDED.expires_at, max_calls=EXCLUDED.max_calls, max_bytes=EXCLUDED.max_bytes, "
                "max_cost_units=EXCLUDED.max_cost_units, delegation_depth=EXCLUDED.delegation_depth, "
                "delegation_depth_limit=EXCLUDED.delegation_depth_limit, created_by_actor_type=EXCLUDED.created_by_actor_type, "
                "created_by_actor_id=EXCLUDED.created_by_actor_id"
            ),
            {
                "id": GRANT,
                "tenant": TENANT,
                "workspace": WORKSPACE,
                "runtime": RUNTIME,
                "actor": "00000000-0000-0000-0000-0000000000aa",
                "resource": RESOURCE,
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.capability_usage (grant_id,tenant_id,calls,bytes_in,bytes_out,cost_units) "
                "VALUES (:grant,:tenant,0,0,0,0) "
                "ON CONFLICT (grant_id) DO UPDATE SET calls=0, bytes_in=0, bytes_out=0, cost_units=0"
            ),
            {"grant": GRANT, "tenant": TENANT},
        )
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.capability_signing_keys "
                "(kid,algorithm,public_key_pem,public_key_sha256,state,not_before,expires_at) "
                "VALUES ('p54b-key', 'RS256', :public, :fingerprint, 'active', now(),now()+interval '10 minutes') "
                "ON CONFLICT (kid) DO UPDATE SET "
                "public_key_pem=EXCLUDED.public_key_pem, "
                "public_key_sha256=EXCLUDED.public_key_sha256, "
                "state=EXCLUDED.state, not_before=EXCLUDED.not_before, "
                "expires_at=EXCLUDED.expires_at"
            ),
            {
                "public": public_pem,
                "fingerprint": hashlib.sha256(
                    private_key.public_key().public_bytes(
                        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
                    )
                ).hexdigest(),
            },
        )
    return private_pem, CERTIFICATE_THUMBPRINT


def test_engineering_composition_seeds_and_executes_gateway_backed_search(db_engine) -> None:  # type: ignore[no-untyped-def]
    plan = _plan()
    private_key, certificate_thumbprint = _seed(
        db_engine, plan_digest=plan.proposal.proposal_digest
    )
    context = _context(plan)
    now = datetime.now(UTC)
    with db_engine.begin() as connection:
        token = encode_capability_token(
            private_key_pem=private_key,
            kid="p54b-key",
            jti=uuid.uuid4().hex,
            subject=RUNTIME,
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_user_id="00000000-0000-0000-0000-0000000000aa",
            grant_id=GRANT,
            grant_version=1,
            delegation_depth=0,
            workload_thumbprint=__import__(
                "omnibase.capability_gateway.thumbprints",
                fromlist=["certificate_thumbprint_to_x5t_s256"],
            ).certificate_thumbprint_to_x5t_s256(certificate_thumbprint),
            issued_at=now,
            expires_at=now + timedelta(minutes=2),
            approval_id=None,
        )
    credential = WorkloadCredential(
        authorization=f"Capability {token}",
        identity="runtime",
        trusted_context=TrustedWorkloadContext(
            opaque_identity="runtime",
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            runtime_instance_id=RUNTIME,
            certificate_thumbprint=certificate_thumbprint,
            workload_identity_digest=DIGEST,
        ),
    )
    seam = _CredentialSeam(credential)
    gateway = GatewayService(
        GatewayComponents(
            verifier=CoreCapabilityVerifier(),
            resolver=RegistryResourceResolver(),
            data_adapter=None,  # type: ignore[arg-type]
            rag_adapter=_RagAdapter(),
            audit_sink=ControlPlaneGatewayAuditSink(),
        )
    )
    executor = build_engineering_single_agent_executor(
        enabled=True,
        migration_head="0012",
        feature_gates={
            "agent_runtime_enabled": False,
            "agent_planner_enabled": False,
            "multi_agent_enabled": False,
        },
        gateway=gateway,
        session_factory=lambda: Session(db_engine),
        workload_credential_seam=seam,
    )
    result = executor.execute(
        context=context,
        plan=plan,
        request=KnowledgeSearchRequest(
            resource_id=RESOURCE, query="composition", max_bytes=1_048_576
        ),
    )
    assert result.output.resource_id == RESOURCE
    assert result.receipt.status == "succeeded"
    assert seam.calls == 1
    with db_engine.connect() as connection:
        assert connection.execute(
            text("SELECT id <> workspace_run_id FROM omnibase_meta.agent_runs WHERE id=:id"),
            {"id": RUN},
        ).scalar_one()
        assert connection.execute(
            text("SELECT run_id = :workspace_run FROM omnibase_meta.run_leases WHERE id=:id"),
            {"id": LEASE, "workspace_run": WORKSPACE_RUN},
        ).scalar_one()
        assert (
            connection.execute(
                text("SELECT calls FROM omnibase_meta.capability_usage WHERE grant_id=:grant"),
                {"grant": GRANT},
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM omnibase_meta.audit_events "
                    "WHERE tenant_id=:tenant AND action='rag.search' AND decision='allowed' "
                    "AND request_id LIKE 'p54a-%'"
                ),
                {"tenant": TENANT},
            ).scalar_one()
            >= 1
        )


def test_engineering_composition_remains_disabled_by_default() -> None:
    assert (
        type(build_engineering_single_agent_executor(enabled=False, feature_gates={})).__name__
        == "UnavailableEngineeringSingleAgentExecutor"
    )
