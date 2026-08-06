"""Disposable PostgreSQL proof for the P5.4B engineering composition seam."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from omnibase.agent_executor import (
    CapabilityGatewayKnowledgeSearchPort,
    ExecutorInvocationContext,
    KnowledgeSearchRequest,
    TypedSingleAgentExecutor,
    UnavailableTypedSingleAgentExecutor,
    build_engineering_typed_executor,
)
from omnibase.capabilities.service import VerifiedCapability as CoreVerifiedCapability
from omnibase.capabilities.token import CapabilityTokenClaims
from omnibase.capability_gateway.audit import ControlPlaneGatewayAuditSink
from omnibase.capability_gateway.contracts import (
    CapabilityConstraints,
    RagSearchResult,
    ResourceDescriptor,
    SearchHitRead,
    TrustedWorkloadContext,
    VerifiedCapability,
    WorkloadCredential,
)
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
ACTOR = "00000000-0000-0000-0000-0000000000aa"
GRANT = "88888888-8888-8888-8888-888888888888"
RUNTIME = "99999999-9999-9999-9999-999999999999"


def _upgrade_head() -> None:
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "head")


class _PreparedVerifier:
    def __init__(self, capability: VerifiedCapability) -> None:
        self.capability = capability
        self.core = CoreCapabilityVerifier()

    def verify(self, *args, **kwargs) -> VerifiedCapability:
        del args, kwargs
        return self.capability

    def consume_budget(self, *args, **kwargs) -> None:
        self.core.consume_budget(*args, **kwargs)


class _Resolver:
    def resolve(self, *args, **kwargs) -> ResourceDescriptor:
        del args, kwargs
        return ResourceDescriptor(
            id=RESOURCE,
            tenant_id=TENANT,
            kind="knowledge_resource",
            owner_type="workspace",
            owner_id=WORKSPACE,
            parent_id=WORKSPACE,
            state="active",
            version=1,
            policy_class="workspace_private",
        )


class _RagAdapter:
    def search(self, session: Session, **kwargs) -> RagSearchResult:
        del session, kwargs
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


class _Authority:
    def validate(
        self, *, context: ExecutorInvocationContext, credential: WorkloadCredential
    ) -> None:
        assert credential.trusted_context.tenant_id == context.tenant_id
        assert credential.trusted_context.workspace_id == context.workspace_id
        assert context.run_fencing_token == 7


def _seed(db_engine) -> None:  # type: ignore[no-untyped-def]
    schema = "tenant_p54b0001"
    with db_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.tenants (id,name,slug,schema_name,is_default,is_active) VALUES (:id,'P5.4B','p54b',:schema,FALSE,TRUE) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": TENANT, "schema": schema},
        )
    _upgrade_head()
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenant_p54b0001.users (id,email,password_hash,is_tenant_admin,is_active) VALUES (:id,:email,:hash,TRUE,TRUE) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": ACTOR, "email": "p54b@example.invalid", "hash": "integration-test"},
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.resource_registry (id,tenant_id,kind,owner_type,owner_id,display_name,state,version,policy_class) VALUES (:id,:tenant,'knowledge_resource','workspace',:workspace,'P5.4B resource','active',1,'workspace_private') ON CONFLICT (id) DO NOTHING"
            ),
            {"id": RESOURCE, "tenant": TENANT, "workspace": WORKSPACE},
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.capability_grants (id,tenant_id,workspace_id,runtime_instance_id,workload_identity_digest,actor_user_id,actions,resource_ids,constraints,version,state,not_before,expires_at,max_calls,max_bytes,max_cost_units,delegation_depth,delegation_depth_limit,created_by_actor_type,created_by_actor_id) VALUES (:id,:tenant,:workspace,:runtime,:digest,:actor,ARRAY['rag.search']::varchar[],ARRAY[:resource]::uuid[],'{\"max_bytes\":262144,\"timeout_ms\":3000}'::jsonb,1,'active',now(),now()+interval '10 minutes',10,1048576,10,0,0,'system',:actor) ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": GRANT,
                "tenant": TENANT,
                "workspace": WORKSPACE,
                "runtime": RUNTIME,
                "digest": "3" * 64,
                "actor": ACTOR,
                "resource": RESOURCE,
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.capability_usage (grant_id,tenant_id,calls,bytes_in,bytes_out,cost_units) VALUES (:grant,:tenant,0,0,0,0) ON CONFLICT (grant_id) DO NOTHING"
            ),
            {"grant": GRANT, "tenant": TENANT},
        )


def test_engineering_composition_seeds_and_executes_gateway_backed_search(db_engine) -> None:  # type: ignore[no-untyped-def]
    _seed(db_engine)
    plan = _plan()
    context = _context(plan)
    now = int(datetime.now(UTC).timestamp())
    claims = CapabilityTokenClaims(
        jti=uuid.uuid4().hex,
        subject=f"workspace:{WORKSPACE}",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_user_id=ACTOR,
        grant_id=GRANT,
        grant_version=1,
        delegation_depth=0,
        workload_thumbprint="test",
        issued_at=now,
        not_before=now,
        expires_at=now + 600,
        approval_id=None,
    )
    core = CoreVerifiedCapability(
        claims=claims,
        grant_id=GRANT,
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        runtime_instance_id=RUNTIME,
        actor_user_id=ACTOR,
        action="rag.search",
        resource_id=RESOURCE,
        constraints={"max_bytes": 262144, "timeout_ms": 3000},
    )
    capability = VerifiedCapability(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        runtime_instance_id=RUNTIME,
        actor_user_id=ACTOR,
        grant_id=GRANT,
        token_jti=claims.jti,
        actions=frozenset({"rag.search"}),
        resource_ids=frozenset({RESOURCE}),
        constraints=CapabilityConstraints(max_bytes=262144, max_timeout_ms=3000),
        core_verification=core,
    )
    credential = WorkloadCredential(
        authorization="server-owned",
        identity="runtime",
        trusted_context=TrustedWorkloadContext(
            opaque_identity="runtime",
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            runtime_instance_id=RUNTIME,
            certificate_thumbprint="3" * 64,
        ),
    )
    gateway = GatewayService(
        GatewayComponents(
            verifier=_PreparedVerifier(capability),
            resolver=_Resolver(),
            data_adapter=None,
            rag_adapter=_RagAdapter(),
            audit_sink=ControlPlaneGatewayAuditSink(),
        )
    )  # type: ignore[arg-type]
    port = CapabilityGatewayKnowledgeSearchPort(
        gateway=gateway,
        session_factory=lambda: Session(db_engine),
        credential_provider=lambda *, context: credential,
        authority_validator=_Authority(),
    )
    executor = build_engineering_typed_executor(
        enabled=True,
        feature_gates={
            "agent_runtime_enabled": False,
            "agent_planner_enabled": False,
            "multi_agent_enabled": False,
        },
        knowledge_search=port,
    )
    assert isinstance(executor, TypedSingleAgentExecutor)
    result = executor.execute(
        context=context,
        plan=plan,
        request=KnowledgeSearchRequest(resource_id=RESOURCE, query="composition"),
    )
    assert result.output.resource_id == RESOURCE
    assert result.receipt.status == "succeeded"
    with db_engine.connect() as connection:
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
                    "SELECT count(*) FROM omnibase_meta.audit_events WHERE tenant_id=:tenant AND action='rag.search' AND decision='allowed'"
                ),
                {"tenant": TENANT},
            ).scalar_one()
            == 1
        )


def test_engineering_composition_remains_disabled_by_default() -> None:
    assert isinstance(
        build_engineering_typed_executor(enabled=False, feature_gates={}, knowledge_search=None),
        UnavailableTypedSingleAgentExecutor,
    )
