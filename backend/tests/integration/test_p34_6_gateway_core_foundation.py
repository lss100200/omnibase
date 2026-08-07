"""Real PostgreSQL proof for the P34.6 Core Gateway reservation lifecycle."""

from __future__ import annotations

import base64
import hashlib
import os
import subprocess
import sys
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from omnibase.capabilities.models import CapabilityUsage, WorkspaceDataUsageReservation
from omnibase.capabilities.service import VerifiedCapability as CoreVerifiedCapability
from omnibase.capabilities.token import CapabilityTokenClaims
from omnibase.capability_gateway.audit import ControlPlaneGatewayAuditSink
from omnibase.capability_gateway.contracts import (
    ArtifactWriteRequest,
    CapabilityConstraints,
    ResourceDescriptor,
    TrustedWorkloadContext,
    VerifiedCapability,
    WorkloadCredential,
    WorkspaceDataWriteResult,
)
from omnibase.capability_gateway.security import CoreCapabilityVerifier
from omnibase.capability_gateway.write_service import (
    WorkspaceDataGatewayComponents,
    WorkspaceDataGatewayService,
)
from omnibase.control_plane.models import AuditEvent, OperationRecord

if os.environ.get("OMNIBASE_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "P34.6 integration tests require OMNIBASE_INTEGRATION_TESTS=1",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=_BACKEND_ROOT,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )


class _PreparedCoreVerifier:
    """Use a preverified token envelope but the real Core reserve/finalize path."""

    def __init__(self, capability: VerifiedCapability) -> None:
        self.capability = capability
        self.core = CoreCapabilityVerifier()

    def verify(self, *args, **kwargs) -> VerifiedCapability:
        del args, kwargs
        return self.capability

    def consume_budget(self, *args, **kwargs) -> None:
        self.core.consume_budget(*args, **kwargs)

    def reserve_workspace_data(self, *args, **kwargs):
        return self.core.reserve_workspace_data(*args, **kwargs)

    def finalize_workspace_data(self, *args, **kwargs):
        return self.core.finalize_workspace_data(*args, **kwargs)


class _WorkspaceResolver:
    def __init__(self, descriptor: ResourceDescriptor) -> None:
        self.descriptor = descriptor

    def resolve(self, *args, **kwargs) -> ResourceDescriptor:
        del args, kwargs
        return self.descriptor


class _ArtifactAdapter:
    supports_workspace_data_effects: Literal[True] = True

    def __init__(self) -> None:
        self.calls = 0
        self.result: WorkspaceDataWriteResult | None = None

    def write_artifact(self, _session: Session, *, reservation, workspace, payload, **kwargs):
        del kwargs
        self.calls += 1
        self.result = WorkspaceDataWriteResult(
            operation_id=reservation.operation_id,
            resource_id=workspace.id,
            resource_version=workspace.version,
            action="artifact.write",
            media_type=payload.media_type,
            size_bytes=payload.size_bytes,
            content_sha256=payload.content_sha256,
        )
        return self.result

    def replay_workspace_data(self, *args, **kwargs) -> WorkspaceDataWriteResult:
        del args, kwargs
        assert self.result is not None
        return replace(self.result, replayed=True)


def test_core_gateway_first_write_creates_operation_reservation_budget_and_audit(
    db_engine,
) -> None:
    upgrade = _run_alembic("upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

    suffix = uuid.uuid4().hex[:10]
    tenant_id = str(uuid.uuid4())
    tenant_schema = f"tenant_{suffix}"
    workspace_id = str(uuid.uuid4())
    template_id = str(uuid.uuid4())
    actor_id = str(uuid.uuid4())
    grant_id = str(uuid.uuid4())
    runtime_instance_id = str(uuid.uuid4())
    workload_digest = "3" * 64
    with db_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{tenant_schema}"'))
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.tenants "
                "(id, name, slug, schema_name, is_default, is_active) "
                "VALUES (:id, 'P34.6 Core Gateway', :slug, :schema, FALSE, TRUE)"
            ),
            {"id": tenant_id, "slug": f"p346-gateway-{suffix}", "schema": tenant_schema},
        )
    tenant_upgrade = _run_alembic("upgrade", "head")
    assert tenant_upgrade.returncode == 0, tenant_upgrade.stdout + tenant_upgrade.stderr

    with db_engine.begin() as connection:
        connection.execute(
            text(
                f'INSERT INTO "{tenant_schema}".users '  # noqa: S608
                "(id, email, password_hash, is_tenant_admin, is_active) "
                "VALUES (:id, :email, :password_hash, TRUE, TRUE)"
            ),
            {
                "id": actor_id,
                "email": f"p346-gateway-{suffix}@example.invalid",
                "password_hash": "integration-test-not-a-real-password-hash",
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.resource_registry "
                "(id, tenant_id, kind, owner_type, owner_id, display_name, state, "
                "version, policy_class) VALUES (:id, :tenant, 'workspace', "
                "'workspace', :id, 'P34.6 Gateway workspace', 'active', 1, "
                "'workspace_private')"
            ),
            {"id": workspace_id, "tenant": tenant_id},
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_templates "
                "(id, tenant_id, template_key, version, display_name, digest, "
                "template_spec, state, created_by_user_id) VALUES "
                "(:id, :tenant, :key, 1, 'P34.6 Gateway template', :digest, "
                "'{}'::jsonb, 'active', :actor)"
            ),
            {
                "id": template_id,
                "tenant": tenant_id,
                "key": f"p346-gateway-{suffix}",
                "digest": "e" * 64,
                "actor": actor_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspaces "
                "(id, tenant_id, template_id, owner_user_id, display_name, "
                "desired_state, observed_state, generation, version, quota) VALUES "
                "(:id, :tenant, :template, :actor, 'P34.6 Gateway workspace', "
                "'running', 'running', 1, 1, '{}'::jsonb)"
            ),
            {
                "id": workspace_id,
                "tenant": tenant_id,
                "template": template_id,
                "actor": actor_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_memberships "
                "(tenant_id, workspace_id, user_id, role, state, version, "
                "created_by_user_id) VALUES "
                "(:tenant, :workspace, :actor, 'owner', 'active', 1, :actor)"
            ),
            {"tenant": tenant_id, "workspace": workspace_id, "actor": actor_id},
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.capability_grants "
                "(id, tenant_id, workspace_id, runtime_instance_id, "
                "workload_identity_digest, actor_user_id, actions, resource_ids, "
                "constraints, version, state, not_before, expires_at, max_calls, "
                "max_bytes, max_cost_units, delegation_depth, delegation_depth_limit, "
                "created_by_actor_type, created_by_actor_id) VALUES "
                "(:id, :tenant, :workspace, :runtime, :workload, :actor, "
                "ARRAY['artifact.write']::varchar[], ARRAY[:workspace]::uuid[], "
                "CAST(:constraints AS jsonb), 1, 'active', now(), "
                "now() + interval '4 minutes', 10, 1048576, 10, 0, 0, 'system', :actor)"
            ),
            {
                "id": grant_id,
                "tenant": tenant_id,
                "workspace": workspace_id,
                "runtime": runtime_instance_id,
                "workload": workload_digest,
                "actor": actor_id,
                "constraints": '{"timeout_ms":2000}',
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.capability_usage "
                "(grant_id, tenant_id, calls, bytes_in, bytes_out, cost_units) "
                "VALUES (:grant, :tenant, 0, 0, 0, 0)"
            ),
            {"grant": grant_id, "tenant": tenant_id},
        )

    now = int(datetime.now(UTC).timestamp())
    claims = CapabilityTokenClaims(
        jti=uuid.uuid4().hex,
        subject=f"workspace:{workspace_id}",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        grant_id=grant_id,
        grant_version=1,
        delegation_depth=0,
        workload_thumbprint="trusted-preverified-test-envelope",
        issued_at=now,
        not_before=now,
        expires_at=now + 300,
        approval_id=None,
    )
    core_facts = CoreVerifiedCapability(
        claims=claims,
        grant_id=grant_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        runtime_instance_id=runtime_instance_id,
        actor_user_id=actor_id,
        action="artifact.write",
        resource_id=workspace_id,
        constraints={"timeout_ms": 2000},
    )
    capability = VerifiedCapability(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        runtime_instance_id=runtime_instance_id,
        actor_user_id=actor_id,
        grant_id=grant_id,
        token_jti=claims.jti,
        actions=frozenset({"artifact.write"}),
        resource_ids=frozenset({workspace_id}),
        constraints=CapabilityConstraints(max_timeout_ms=2000),
        core_verification=core_facts,
    )
    credential = WorkloadCredential(
        authorization="preverified-test-envelope",
        identity=f"spiffe://omnibase/runtime/{runtime_instance_id}",
        trusted_context=TrustedWorkloadContext(
            opaque_identity=f"spiffe://omnibase/runtime/{runtime_instance_id}",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            runtime_instance_id=runtime_instance_id,
            certificate_thumbprint=workload_digest,
            workload_identity_digest=workload_digest,
        ),
    )
    descriptor = ResourceDescriptor(
        id=workspace_id,
        tenant_id=tenant_id,
        kind="workspace",
        owner_type="workspace",
        owner_id=workspace_id,
        parent_id=workspace_id,
        state="active",
        version=1,
        policy_class="workspace_private",
    )
    adapter = _ArtifactAdapter()
    service = WorkspaceDataGatewayService(
        WorkspaceDataGatewayComponents(
            verifier=_PreparedCoreVerifier(capability),
            resolver=_WorkspaceResolver(descriptor),
            adapter=adapter,
            audit_sink=ControlPlaneGatewayAuditSink(),
            audit_session_factory=lambda: Session(db_engine),
        )
    )
    content = b"ok"
    payload = ArtifactWriteRequest.model_validate(
        {
            "idempotency_key": "gateway-core-first-write",
            "display_name": "result.txt",
            "media_type": "text/plain",
            "size_bytes": len(content),
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "content_base64": base64.b64encode(content).decode("ascii"),
            "source_resource_ids": [],
        }
    )

    with Session(db_engine) as session:
        first = service.write_artifact(
            session,
            credential,
            payload,
            "p34-6-core-gateway-first",
            live_revalidator=lambda: credential,
        )
        replay = service.write_artifact(
            session,
            credential,
            payload,
            "p34-6-core-gateway-replay",
            live_revalidator=lambda: credential,
        )
        operation = session.scalar(
            select(OperationRecord).where(OperationRecord.id == str(first.operation_id))
        )
        reservation = session.scalar(
            select(WorkspaceDataUsageReservation).where(
                WorkspaceDataUsageReservation.operation_id == str(first.operation_id)
            )
        )
        usage = session.scalar(select(CapabilityUsage).where(CapabilityUsage.grant_id == grant_id))
        audit_count = len(
            list(
                session.scalars(
                    select(AuditEvent).where(
                        AuditEvent.operation_id == str(first.operation_id),
                        AuditEvent.action == "artifact.write",
                        AuditEvent.decision == "allowed",
                    )
                )
            )
        )

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.operation_id == first.operation_id
    assert adapter.calls == 1
    assert operation is not None
    assert operation.state == "succeeded"
    assert reservation is not None
    assert reservation.state == "committed"
    assert reservation.result_digest is not None
    assert usage is not None
    assert usage.calls == 1
    assert audit_count == 2
