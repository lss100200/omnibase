"""Disposable PostgreSQL proof for the P5.4B engineering composition seam."""

from __future__ import annotations

import hashlib
import os
import uuid
from contextlib import contextmanager
from dataclasses import replace
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
from omnibase.agent_executor.gateway_adapter import GatewayAdapterError
from omnibase.agent_executor.service import TypedExecutorError, TypedExecutorPolicyDenied
from omnibase.capabilities.token import encode_capability_token
from omnibase.capability_gateway.audit import ControlPlaneGatewayAuditSink
from omnibase.capability_gateway.contracts import (
    RagSearchResult,
    WorkloadCredential,
)
from omnibase.capability_gateway.resolver import RegistryResourceResolver
from omnibase.capability_gateway.security import CoreCapabilityVerifier
from omnibase.capability_gateway.service import GatewayComponents, GatewayService
from omnibase.capability_gateway.workload import (
    SqlAlchemyRunLeaseWorkloadAttestor,
    TrustedGatewayPeerEvidence,
)
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
RUNTIME_NODE = "bb000000-bb00-bb00-bb00-bb0000000002"
LEASE = "cc000000-cc00-cc00-cc00-cc0000000001"
GRANT = "88888888-8888-8888-8888-888888888888"
RUNTIME = "99999999-9999-9999-9999-999999999999"

DIGEST = "ab" * 32
VERSION_DIGEST = "ae" * 32
CERTIFICATE_THUMBPRINT = "3" * 64
WORKLOAD_DIGEST = "5" * 64
REQUEST_HASH = "cc" * 32
PLAN_ID = "aa000000-aa00-aa00-aa00-aa0000000001"
MODEL_POLICY = "44444444-4444-4444-4444-444444444444"


class _BoundDatabase:
    """Function-scoped transaction shared by every Session in one test."""

    def __init__(self, connection) -> None:  # type: ignore[no-untyped-def]
        self.connection = connection

    @contextmanager
    def begin(self):  # type: ignore[no-untyped-def]
        yield self.connection


@pytest.fixture
def p54b_db(db_engine):  # type: ignore[no-untyped-def]
    _upgrade_head()
    connection = db_engine.connect()
    transaction = connection.begin()
    try:
        yield _BoundDatabase(connection)
    finally:
        if transaction.is_active:
            transaction.rollback()
        connection.close()


def _upgrade_head() -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "src/omnibase/migrations")
    )
    command.upgrade(config, "head")


class _RagAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, session: Session, **kwargs) -> RagSearchResult:
        del session, kwargs
        self.calls += 1
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
    def __init__(
        self,
        *,
        db_engine,  # type: ignore[no-untyped-def]
        authorization: str,
        evidence_overrides: dict[str, object] | None = None,
    ) -> None:
        self._db_engine = db_engine
        self._authorization = authorization
        self._evidence_overrides = evidence_overrides or {}
        self.calls = 0

    def issue(self, *, context: ExecutorInvocationContext) -> WorkloadCredential:
        self.calls += 1
        assert context.tenant_id == TENANT
        assert context.workspace_id == WORKSPACE
        assert context.run_id == RUN
        now = datetime.now(UTC)
        opaque_identity = f"spiffe://omnibase/runtime/{RUNTIME}"
        evidence_values: dict[str, object] = {
            "peer_kind": "runner",
            "opaque_identity": opaque_identity,
            "tenant_id": TENANT,
            "workspace_id": WORKSPACE,
            "run_id": WORKSPACE_RUN,
            "runtime_instance_id": RUNTIME,
            "node_id": RUNTIME_NODE,
            "lease_id": LEASE,
            "workspace_generation": 1,
            "run_fencing_token": 1,
            "node_fencing_token": 7,
            "certificate_thumbprint": CERTIFICATE_THUMBPRINT,
            "workload_identity_digest": WORKLOAD_DIGEST,
            "evidence_digest": "04" * 32,
            "expires_at": now + timedelta(minutes=2),
        }
        evidence_values.update(self._evidence_overrides)
        evidence = TrustedGatewayPeerEvidence(**evidence_values)  # type: ignore[arg-type]
        trusted = SqlAlchemyRunLeaseWorkloadAttestor(
            lambda: Session(self._db_engine.connection), clock=lambda: now
        ).attest(
            {
                "type": "http",
                "omnibase.mtls_verified": True,
                "omnibase.trusted_gateway_peer": evidence,
            },
            opaque_identity,
        )
        return WorkloadCredential(
            authorization=self._authorization,
            identity=opaque_identity,
            trusted_context=trusted,
        )


class _CountingGateway:
    def __init__(self, inner, *, rag_adapter: _RagAdapter | None = None) -> None:  # type: ignore[no-untyped-def]
        self._inner = inner
        self.rag_adapter = rag_adapter
        self.calls = 0

    def rag_search(self, session, credential, payload, request_id):  # type: ignore[no-untyped-def]
        self.calls += 1
        return self._inner.rag_search(session, credential, payload, request_id)


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


def _seed(
    db_engine,
    *,
    plan_digest: str,
    task_deadline_expired: bool = False,
    run_lease_expired: bool = False,
) -> tuple[str, str]:  # type: ignore[no-untyped-def]
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
                "ON CONFLICT (id) DO UPDATE SET desired_state='running', observed_state='running', "
                "generation=1, version=omnibase_meta.workspaces.version+1, archived_at=NULL"
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
                "ON CONFLICT (id) DO UPDATE SET desired_state='running', observed_state='running', "
                "generation=1, next_fencing_token=2, runtime_instance_id=EXCLUDED.runtime_instance_id, "
                "workload_identity_digest=EXCLUDED.workload_identity_digest"
            ),
            {
                "id": WORKSPACE_RUN,
                "tenant": TENANT,
                "workspace": WORKSPACE,
                "request": REQUEST_HASH,
                "runtime": RUNTIME,
                "workload": WORKLOAD_DIGEST,
                "actor": "00000000-0000-0000-0000-0000000000aa",
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_nodes "
                "(id,tenant_id,workspace_id,owner_user_id,display_name,identity_digest,state,attestation_state,fencing_token,version,last_seen_at) "
                "VALUES (:id,:tenant,:workspace,:actor,'P5.4B node',:digest,'active','verified',7,1,now()) "
                "ON CONFLICT (id) DO UPDATE SET state='active', attestation_state='verified', "
                "fencing_token=7, revoked_at=NULL, last_seen_at=now()"
            ),
            {
                "id": RUNTIME_NODE,
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
            {
                "tenant": TENANT,
                "node": RUNTIME_NODE,
                "nonce": "01" * 32,
                "evidence": "02" * 32,
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
            text("DELETE FROM omnibase_meta.run_leases WHERE id=:lease"),
            {"lease": LEASE},
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.run_leases "
                "(id,tenant_id,run_id,workspace_id,node_id,node_fencing_token,generation,fencing_token,state,heartbeat_at,expires_at) "
                "VALUES (:id,:tenant,:run,:workspace,:node,7,1,1,'active',CASE WHEN :expired THEN clock_timestamp()-interval '10 minutes' ELSE clock_timestamp() END,CASE WHEN :expired THEN clock_timestamp()-interval '1 second' ELSE clock_timestamp()+interval '10 minutes' END) "
                "ON CONFLICT (id) DO UPDATE SET "
                "tenant_id=EXCLUDED.tenant_id, run_id=EXCLUDED.run_id, workspace_id=EXCLUDED.workspace_id, node_id=EXCLUDED.node_id, node_fencing_token=EXCLUDED.node_fencing_token, generation=EXCLUDED.generation, fencing_token=EXCLUDED.fencing_token, state=EXCLUDED.state, heartbeat_at=EXCLUDED.heartbeat_at, expires_at=EXCLUDED.expires_at"
            ),
            {
                "id": LEASE,
                "tenant": TENANT,
                "run": WORKSPACE_RUN,
                "workspace": WORKSPACE,
                "node": RUNTIME_NODE,
                "expired": run_lease_expired,
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
                "(id,tenant_id,workspace_id,workspace_generation,actor_user_id,agent_definition_id,agent_version_id,agent_version_digest,workspace_agent_binding_id,task_generation,plan_id,plan_version,plan_digest,deadline,state,resource_scope_digest,budget_policy_digest,request_hash,created_at) "
                "VALUES (:id,:tenant,:workspace,1,:actor,:definition,:version,:digest,:binding,1,:plan,1,:plan_digest,CASE WHEN :expired THEN clock_timestamp()-interval '1 second' ELSE clock_timestamp()+interval '10 minutes' END,'scheduled',:scope,:budget,:request,CASE WHEN :expired THEN clock_timestamp()-interval '10 minutes' ELSE clock_timestamp() END) "
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
                "expired": task_deadline_expired,
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
                "workload": WORKLOAD_DIGEST,
                "node": RUNTIME_NODE,
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


def _prepare_executor(
    db_engine,  # type: ignore[no-untyped-def]
    *,
    plan=None,
    evidence_overrides: dict[str, object] | None = None,
    gateway_override=None,
    task_deadline_expired: bool = False,
    run_lease_expired: bool = False,
):
    plan = plan or _plan()
    private_key, certificate_thumbprint = _seed(
        db_engine,
        plan_digest=plan.proposal.proposal_digest,
        task_deadline_expired=task_deadline_expired,
        run_lease_expired=run_lease_expired,
    )
    now = datetime.now(UTC)
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
    seam = _CredentialSeam(
        db_engine=db_engine,
        authorization=f"Capability {token}",
        evidence_overrides=evidence_overrides,
    )
    rag_adapter = _RagAdapter()
    gateway_service = gateway_override or GatewayService(
        GatewayComponents(
            verifier=CoreCapabilityVerifier(),
            resolver=RegistryResourceResolver(),
            data_adapter=None,  # type: ignore[arg-type]
            rag_adapter=rag_adapter,
            audit_sink=ControlPlaneGatewayAuditSink(),
        )
    )
    gateway = _CountingGateway(gateway_service, rag_adapter=rag_adapter)
    executor = build_engineering_single_agent_executor(
        enabled=True,
        migration_head="0015",
        feature_gates={
            "agent_runtime_enabled": False,
            "agent_planner_enabled": False,
            "multi_agent_enabled": False,
        },
        gateway=gateway,
        session_factory=lambda: Session(db_engine.connection),
        workload_credential_seam=seam,
    )
    return plan, _context(plan), seam, gateway, executor


def test_engineering_composition_seeds_and_executes_gateway_backed_search(p54b_db) -> None:  # type: ignore[no-untyped-def]
    plan, context, seam, gateway, executor = _prepare_executor(p54b_db)
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
    assert gateway.calls == 1
    with p54b_db.begin() as connection:
        assert connection.execute(
            text("SELECT id <> workspace_run_id FROM omnibase_meta.agent_runs WHERE id=:id"),
            {"id": RUN},
        ).scalar_one()
        assert context.node_id != RUNTIME_NODE
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


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE omnibase_meta.workspaces SET desired_state='paused' WHERE id=:id",
        "UPDATE omnibase_meta.agent_tasks SET state='paused' WHERE id=:id",
        "UPDATE omnibase_meta.agent_runs SET state='paused' WHERE id=:id",
        "UPDATE omnibase_meta.workspace_runs SET desired_state='paused', observed_state='paused' WHERE id=:id",
        "UPDATE omnibase_meta.run_leases SET expires_at=clock_timestamp() WHERE id=:id",
        "UPDATE omnibase_meta.run_leases SET state='revoked' WHERE id=:id",
        "UPDATE omnibase_meta.run_leases SET state='completed' WHERE id=:id",
        "UPDATE omnibase_meta.run_leases SET state='expired' WHERE id=:id",
        "UPDATE omnibase_meta.workspace_nodes SET state='suspended' WHERE id=:id",
        "UPDATE omnibase_meta.workspace_nodes SET state='revoked', revoked_at=clock_timestamp() WHERE id=:id",
        "UPDATE omnibase_meta.node_attestations SET state='rejected' WHERE node_id=:id",
        "UPDATE omnibase_meta.node_attestations SET state='revoked' WHERE node_id=:id",
        "UPDATE omnibase_meta.node_attestations SET verified_at=clock_timestamp()-interval '10 minutes', expires_at=clock_timestamp()-interval '1 second' WHERE node_id=:id",
        "DELETE FROM omnibase_meta.node_attestations WHERE node_id=:id",
        "UPDATE omnibase_meta.workspaces SET generation=2 WHERE id=:id",
    ],
)
def test_live_database_authority_mutations_fail_before_gateway(p54b_db, statement: str) -> None:  # type: ignore[no-untyped-def]
    plan, context, seam, gateway, executor = _prepare_executor(p54b_db)
    target = (
        TASK
        if "agent_tasks" in statement
        else RUN
        if "agent_runs" in statement
        else WORKSPACE_RUN
        if "workspace_runs" in statement
        else LEASE
        if "run_leases" in statement
        else RUNTIME_NODE
        if "workspace_nodes" in statement or "node_attestations" in statement
        else WORKSPACE
    )
    with p54b_db.begin() as connection:
        connection.execute(text(statement), {"id": target})
    with pytest.raises(TypedExecutorError, match="knowledge_search_failed") as exc_info:
        executor.execute(
            context=context,
            plan=plan,
            request=KnowledgeSearchRequest(resource_id=RESOURCE, query="denied"),
        )
    assert isinstance(exc_info.value.__cause__, GatewayAdapterError)
    assert seam.calls == 1
    assert gateway.calls == 0


def test_expired_task_deadline_seed_fails_before_gateway(p54b_db) -> None:  # type: ignore[no-untyped-def]
    plan, context, seam, gateway, executor = _prepare_executor(p54b_db, task_deadline_expired=True)
    with pytest.raises(TypedExecutorError, match="knowledge_search_failed"):
        executor.execute(
            context=context,
            plan=plan,
            request=KnowledgeSearchRequest(resource_id=RESOURCE, query="expired"),
        )
    assert seam.calls == 1
    assert gateway.calls == 0


def test_expired_active_run_lease_seed_fails_before_gateway(p54b_db) -> None:  # type: ignore[no-untyped-def]
    plan, context, seam, gateway, executor = _prepare_executor(p54b_db, run_lease_expired=True)
    with pytest.raises(TypedExecutorError, match="knowledge_search_failed"):
        executor.execute(
            context=context,
            plan=plan,
            request=KnowledgeSearchRequest(resource_id=RESOURCE, query="expired lease"),
        )
    assert seam.calls == 1
    assert gateway.calls == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"tenant_id": "00000000-0000-0000-0000-00000000000b"},
        {"workspace_id": "66666666-6666-6666-6666-666666666667"},
        {"run_id": RUN},
        {"run_fencing_token": 2},
        {"node_fencing_token": 8},
        {"workload_identity_digest": "4" * 64},
        {
            "runtime_instance_id": "99999999-9999-9999-9999-999999999998",
            "opaque_identity": "spiffe://omnibase/runtime/99999999-9999-9999-9999-999999999998",
        },
    ],
)
def test_server_owned_workload_evidence_mismatch_fails_before_gateway(
    p54b_db, overrides: dict[str, object]
) -> None:  # type: ignore[no-untyped-def]
    plan, context, seam, gateway, executor = _prepare_executor(
        p54b_db, evidence_overrides=overrides
    )
    with pytest.raises(TypedExecutorError, match="knowledge_search_failed") as exc_info:
        executor.execute(
            context=context,
            plan=plan,
            request=KnowledgeSearchRequest(resource_id=RESOURCE, query="denied"),
        )
    cause = exc_info.value.__cause__
    assert isinstance(cause, GatewayAdapterError)
    assert cause.code == "workload_credential_unavailable"
    assert seam.calls == 1
    assert gateway.calls == 0


def test_certificate_thumbprint_drift_is_rejected_by_core_before_rag(p54b_db) -> None:  # type: ignore[no-untyped-def]
    plan, context, seam, gateway, executor = _prepare_executor(
        p54b_db, evidence_overrides={"certificate_thumbprint": "4" * 64}
    )
    with pytest.raises(TypedExecutorError, match="knowledge_search_failed"):
        executor.execute(
            context=context,
            plan=plan,
            request=KnowledgeSearchRequest(resource_id=RESOURCE, query="wrong certificate"),
        )
    assert seam.calls == 1
    assert gateway.calls == 1
    assert gateway.rag_adapter is not None
    assert gateway.rag_adapter.calls == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"tenant_id": "00000000-0000-0000-0000-00000000000b"},
        {"workspace_id": "66666666-6666-6666-6666-666666666667"},
        {"workspace_generation": 2},
        {"actor_user_id": "00000000-0000-0000-0000-0000000000ab"},
        {"task_generation": 2},
        {"agent_version_id": "11111111-1111-1111-1111-111111111112"},
        {"agent_version_digest": "ff" * 32},
        {"proposal_digest": "ff" * 32},
        {"proposal_version": 2},
        {"resource_scope_digest": "ff" * 32},
        {"budget_policy_digest": "ff" * 32},
    ],
)
def test_context_authority_drift_is_rejected_without_credential_or_gateway(
    p54b_db, changes: dict[str, object]
) -> None:  # type: ignore[no-untyped-def]
    plan, context, seam, gateway, executor = _prepare_executor(p54b_db)
    with pytest.raises(TypedExecutorPolicyDenied):
        executor.execute(
            context=replace(context, **changes),
            plan=plan,
            request=KnowledgeSearchRequest(resource_id=RESOURCE, query="denied"),
        )
    assert seam.calls == 0
    assert gateway.calls == 0


@pytest.mark.parametrize(
    ("mutation", "resource_id"),
    [
        (
            "UPDATE omnibase_meta.capability_grants SET actions=ARRAY['data.schema.read']::varchar[] WHERE id=:grant",
            RESOURCE,
        ),
        (
            "UPDATE omnibase_meta.capability_usage SET calls=100 WHERE grant_id=:grant",
            RESOURCE,
        ),
        (None, "77777777-7777-7777-7777-777777777778"),
    ],
)
def test_gateway_scope_and_budget_denials_do_not_produce_receipts(
    p54b_db, mutation: str | None, resource_id: str
) -> None:  # type: ignore[no-untyped-def]
    plan, context, seam, gateway, executor = _prepare_executor(p54b_db)
    if mutation is not None:
        with p54b_db.begin() as connection:
            connection.execute(text(mutation), {"grant": GRANT})
    with pytest.raises(TypedExecutorError, match="knowledge_search_failed") as exc_info:
        executor.execute(
            context=context,
            plan=plan,
            request=KnowledgeSearchRequest(resource_id=resource_id, query="denied"),
        )
    assert isinstance(exc_info.value.__cause__, GatewayAdapterError)
    assert seam.calls == 1
    assert gateway.calls == 1


class _UnknownGateway:
    def rag_search(self, session, credential, payload, request_id):  # type: ignore[no-untyped-def]
        del session, credential, payload, request_id
        raise RuntimeError("unknown provider outcome")


def test_unknown_gateway_outcome_is_not_replayed_and_has_no_success_receipt(p54b_db) -> None:  # type: ignore[no-untyped-def]
    plan, context, seam, gateway, executor = _prepare_executor(
        p54b_db, gateway_override=_UnknownGateway()
    )
    with pytest.raises(TypedExecutorError, match="knowledge_search_failed") as exc_info:
        executor.execute(
            context=context,
            plan=plan,
            request=KnowledgeSearchRequest(resource_id=RESOURCE, query="unknown"),
        )
    cause = exc_info.value.__cause__
    assert isinstance(cause, GatewayAdapterError)
    assert cause.code == "gateway_invocation_failed"
    assert seam.calls == 1
    assert gateway.calls == 1


def test_terminal_agent_run_and_physical_locator_are_rejected(p54b_db) -> None:  # type: ignore[no-untyped-def]
    plan, context, seam, gateway, executor = _prepare_executor(p54b_db)
    with p54b_db.begin() as connection:
        connection.execute(
            text(
                "UPDATE omnibase_meta.agent_runs SET state='failed', runtime_instance_id=NULL, "
                "workload_identity_digest=NULL, node_id=NULL, node_fencing_token=NULL, "
                "run_lease_id=NULL, run_fencing_token=NULL WHERE id=:run"
            ),
            {"run": RUN},
        )
    with pytest.raises(TypedExecutorError, match="knowledge_search_failed"):
        executor.execute(
            context=context,
            plan=plan,
            request=KnowledgeSearchRequest(resource_id=RESOURCE, query="terminal"),
        )
    assert gateway.calls == 0
    with pytest.raises(ValueError, match="logical UUID"):
        KnowledgeSearchRequest(resource_id="public.schema.table", query="forbidden")


def test_engineering_composition_remains_disabled_by_default() -> None:
    assert (
        type(build_engineering_single_agent_executor(enabled=False, feature_gates={})).__name__
        == "UnavailableEngineeringSingleAgentExecutor"
    )
