"""Focused P34.6 workload-write capability and Gateway safety tests."""

from __future__ import annotations

import base64
import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from omnibase.capabilities import service as capability_service
from omnibase.capabilities.service import VerifiedWorkspaceDataCapabilityFacts
from omnibase.capability_gateway.app import create_gateway_app
from omnibase.capability_gateway.contracts import (
    ArtifactReadRequest,
    CapabilityConstraints,
    ResourceDescriptor,
    TrustedWorkloadContext,
    VerifiedCapability,
    WorkloadCredential,
    WorkspaceDataWriteResult,
)
from omnibase.capability_gateway.router import get_gateway_db
from omnibase.capability_gateway.security import (
    CapabilityScopeError,
    CapabilityVerificationError,
    WorkspaceDataConflictError,
)
from omnibase.capability_gateway.service import GatewayComponents, GatewayFailure
from omnibase.capability_gateway.thumbprints import certificate_thumbprint_to_x5t_s256
from omnibase.capability_gateway.write_adapters import (
    WorkspaceDataAdapterError,
    WorkspaceDataEffectUnknown,
)
from omnibase.capability_gateway.write_service import (
    WorkspaceDataGatewayComponents,
    WorkspaceDataGatewayService,
)

TENANT = "10000000-0000-0000-0000-000000000001"
WORKSPACE = "20000000-0000-0000-0000-000000000001"
RUNTIME = "30000000-0000-0000-0000-000000000001"
ACTOR = "40000000-0000-0000-0000-000000000001"
GRANT = "50000000-0000-0000-0000-000000000001"
ARTIFACT = "60000000-0000-0000-0000-000000000001"
THUMBPRINT = "a" * 64
NOW = datetime(2026, 8, 2, 5, 0, tzinfo=UTC)
HEADERS = {
    "Authorization": "Capability workspace-data-token",
    "X-Omnibase-Workload-Identity": f"spiffe://omnibase/runtime/{RUNTIME}",
}


def _issuer() -> capability_service.TrustedIssuerContext:
    return capability_service.TrustedIssuerContext(
        tenant_id=TENANT,
        system_actor_id=str(uuid.uuid4()),
        originating_user_id=ACTOR,
    )


def test_workspace_data_profile_is_short_lived_non_delegable_and_disjoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_create(*args: object, **kwargs: object) -> str:
        del args
        actions = frozenset(kwargs["actions"])
        allowed = frozenset(kwargs["allowed_actions"])
        if not actions or not actions <= allowed:
            raise capability_service.CapabilityScopeDenied("capability scope denied")
        captured.update(kwargs)
        return "workspace-data-grant"

    monkeypatch.setattr(capability_service, "_create_grant", fake_create)
    result = capability_service.create_workspace_data_grant(
        MagicMock(),
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        runtime_instance_id=RUNTIME,
        workload_identity_digest=THUMBPRINT,
        issuer_context=_issuer(),
        actions={"artifact.write", "rag.derived.create"},
        resource_ids={WORKSPACE},
        not_before=NOW,
        expires_at=NOW + timedelta(minutes=5),
        max_calls=2,
        max_bytes=4096,
        max_cost_units=2,
        constraints={"timeout_ms": 1000},
    )
    assert result == "workspace-data-grant"
    assert captured["allowed_actions"] == capability_service.WORKSPACE_DATA_ACTIONS
    assert captured["delegation_depth"] == 0
    assert captured["delegation_depth_limit"] == 0
    assert captured["parent_grant_id"] is None
    assert captured["approval_id"] is None

    for actions in (
        {"data.rows.read"},
        {"artifact.write", "data.rows.read"},
        {"artifact.write", "sandbox.start"},
    ):
        with pytest.raises(capability_service.CapabilityScopeDenied):
            capability_service.create_workspace_data_grant(
                MagicMock(),
                tenant_id=TENANT,
                workspace_id=WORKSPACE,
                runtime_instance_id=RUNTIME,
                workload_identity_digest=THUMBPRINT,
                issuer_context=_issuer(),
                actions=actions,
                resource_ids={WORKSPACE},
                not_before=NOW,
                expires_at=NOW + timedelta(minutes=5),
                max_calls=1,
                max_bytes=1,
                max_cost_units=1,
                constraints={"timeout_ms": 1000},
            )

    with pytest.raises(capability_service.CapabilityScopeDenied, match="five minutes"):
        capability_service.create_workspace_data_grant(
            MagicMock(),
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            runtime_instance_id=RUNTIME,
            workload_identity_digest=THUMBPRINT,
            issuer_context=_issuer(),
            actions={"artifact.write"},
            resource_ids={WORKSPACE},
            not_before=NOW,
            expires_at=NOW + timedelta(minutes=5, microseconds=1),
            max_calls=1,
            max_bytes=1,
            max_cost_units=1,
            constraints={"timeout_ms": 1000},
        )


def test_workspace_data_grant_cannot_delegate_or_issue_for_another_certificate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    grant = SimpleNamespace(
        id=GRANT,
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        runtime_instance_id=RUNTIME,
        actor_user_id=ACTOR,
        actions=["artifact.write"],
        resource_ids=[WORKSPACE],
        constraints={"timeout_ms": 1000},
        version=1,
        state="active",
        not_before=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=4),
        delegation_depth=0,
        delegation_depth_limit=0,
        parent_grant_id=None,
        approval_id=None,
        workload_identity_digest=THUMBPRINT,
    )
    monkeypatch.setattr(capability_service, "get_grant", lambda *args, **kwargs: grant)
    monkeypatch.setattr(capability_service, "_now", lambda: NOW)
    monkeypatch.setattr(
        capability_service,
        "_assert_active_ancestry",
        lambda *args, **kwargs: grant,
    )

    with pytest.raises(capability_service.CapabilityScopeDenied, match="delegation depth"):
        capability_service.delegate_grant(
            MagicMock(),
            tenant_id=TENANT,
            parent_grant_id=GRANT,
            runtime_instance_id=str(uuid.uuid4()),
            actions={"artifact.write"},
            resource_ids={WORKSPACE},
            expires_at=NOW + timedelta(minutes=1),
            max_calls=1,
            max_bytes=1,
            max_cost_units=1,
            issuer_context=_issuer(),
            constraints={"timeout_ms": 1000},
        )

    wrong_x5t = certificate_thumbprint_to_x5t_s256("b" * 64)
    with pytest.raises(capability_service.CapabilityScopeDenied, match="binding is invalid"):
        capability_service.issue_token(
            MagicMock(),
            tenant_id=TENANT,
            grant_id=GRANT,
            kid="test-key-0001",
            private_key_pem="unused",
            workload_thumbprint=wrong_x5t,
            issuer_context=_issuer(),
        )


class StaticAttestor:
    def __init__(self, *, fail_after: int | None = None) -> None:
        self.calls = 0
        self.fail_after = fail_after

    def attest(self, scope: object, opaque_identity: str) -> TrustedWorkloadContext:
        del scope
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise CapabilityVerificationError
        return TrustedWorkloadContext(
            opaque_identity=opaque_identity,
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            runtime_instance_id=RUNTIME,
            certificate_thumbprint=THUMBPRINT,
            workload_identity_digest=THUMBPRINT,
        )


class FakeVerifier:
    def __init__(self, *, actions: frozenset[str]) -> None:
        self.capability = VerifiedCapability(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            runtime_instance_id=RUNTIME,
            actor_user_id=ACTOR,
            grant_id=GRANT,
            token_jti=uuid.uuid4().hex,
            actions=actions,
            resource_ids=frozenset({WORKSPACE}),
            constraints=CapabilityConstraints(),
        )
        self.states: dict[str, str] = {}
        self.hashes: dict[str, str] = {}
        self.finalized: list[str] = []
        self.budget_requests: list[dict[str, int]] = []
        self.fail_committed_finalize = False

    def verify(self, session: object, credential: object, *, action: str, resource_id: str):
        del session, credential, resource_id
        if action not in self.capability.actions:
            raise CapabilityScopeError
        return self.capability

    def consume_budget(self, *args: object, **kwargs: object) -> None:
        del args
        self.budget_requests.append(
            {
                "calls": int(kwargs["calls"]),
                "bytes_in": int(kwargs["bytes_in"]),
                "bytes_out_reserved": int(kwargs["bytes_out_reserved"]),
            }
        )

    def reserve_workspace_data(
        self,
        session: object,
        credential: object,
        capability: object,
        *,
        operation_id: str,
        request_hash: str,
        action: str,
        resource_id: str,
        resource_version: int,
        bytes_in: int,
        bytes_out_reserved: int,
        cost_units: int,
    ) -> VerifiedWorkspaceDataCapabilityFacts:
        del session, credential, capability, bytes_in, bytes_out_reserved, cost_units
        previous_hash = self.hashes.get(operation_id)
        if previous_hash is not None and previous_hash != request_hash:
            raise WorkspaceDataConflictError("workspace_data_binding_conflict")
        state = self.states.get(operation_id)
        if state in {"pending", "unknown"}:
            raise WorkspaceDataConflictError("workspace_data_reconciliation_required")
        replayed = state == "committed"
        self.hashes[operation_id] = request_hash
        self.states.setdefault(operation_id, "pending")
        return VerifiedWorkspaceDataCapabilityFacts(
            grant_id=GRANT,
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            runtime_instance_id=RUNTIME,
            workload_identity_digest=THUMBPRINT,
            operation_id=operation_id,
            action=action,
            resource_id=resource_id,
            resource_version=resource_version,
            request_hash=request_hash,
            grant_version=1,
            reservation_state="committed" if replayed else "pending",
            replayed=replayed,
            verified_at=NOW,
            expires_at=NOW + timedelta(minutes=1),
            verification_digest="c" * 64,
        )

    def finalize_workspace_data(
        self,
        session: object,
        *,
        operation_id: str,
        final_state: str,
        result_digest: str | None,
    ) -> object:
        del session, result_digest
        if final_state == "committed" and self.fail_committed_finalize:
            self.fail_committed_finalize = False
            raise RuntimeError("simulated post-provider finalization failure")
        self.states[operation_id] = final_state
        self.finalized.append(final_state)
        return object()


class FakeResolver:
    def __init__(self, *, policy_class: str = "workspace_private") -> None:
        self.policy_class = policy_class

    def resolve(self, session: object, *, capability: object, resource_id: str):
        del session, capability
        return ResourceDescriptor(
            id=resource_id,
            tenant_id=TENANT,
            kind="workspace",
            owner_type="workspace",
            owner_id=WORKSPACE,
            parent_id=None,
            state="active",
            version=1,
            policy_class=self.policy_class,
        )


class UnusedReadAdapter:
    def read_schema(self, *args: object, **kwargs: object) -> object:
        raise AssertionError

    def read_rows(self, *args: object, **kwargs: object) -> object:
        raise AssertionError


class UnusedRagAdapter:
    def search(self, *args: object, **kwargs: object) -> object:
        raise AssertionError

    def read_citations(self, *args: object, **kwargs: object) -> object:
        raise AssertionError


@dataclass
class FakeAudit:
    records: list[object] = field(default_factory=list)

    def append(self, session: object, *, capability: object, record: object) -> None:
        del session, capability
        self.records.append(record)


class FakeWorkspaceDataAdapter:
    supports_workspace_data_effects = True

    def __init__(self, *, unknown: bool = False) -> None:
        self.unknown = unknown
        self.write_calls = 0
        self.replay_calls = 0
        self.result: WorkspaceDataWriteResult | None = None

    def write_artifact(self, session: object, **kwargs: object) -> WorkspaceDataWriteResult:
        del session, kwargs
        self.write_calls += 1
        if self.unknown:
            raise WorkspaceDataEffectUnknown
        self.result = WorkspaceDataWriteResult(
            operation_id="70000000-0000-0000-0000-000000000001",
            resource_id=ARTIFACT,
            resource_version=1,
            action="artifact.write",
            media_type="text/plain",
            size_bytes=2,
            content_sha256=hashlib.sha256(b"ok").hexdigest(),
        )
        return self.result

    def replay_workspace_data(self, session: object, **kwargs: object) -> WorkspaceDataWriteResult:
        del session, kwargs
        self.replay_calls += 1
        assert self.result is not None
        return WorkspaceDataWriteResult(**{**self.result.__dict__, "replayed": True})

    def mutate_private_rows(self, *args: object, **kwargs: object) -> WorkspaceDataWriteResult:
        raise AssertionError

    def read_artifact(self, *args: object, **kwargs: object) -> object:
        raise AssertionError

    def create_derived(self, *args: object, **kwargs: object) -> WorkspaceDataWriteResult:
        raise AssertionError

    def delete_derived(self, *args: object, **kwargs: object) -> WorkspaceDataWriteResult:
        raise AssertionError


class FailingArtifactReadAdapter(FakeWorkspaceDataAdapter):
    def read_artifact(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        raise WorkspaceDataAdapterError("provider object_key must stay private")


class ArtifactResolver:
    def resolve(self, session: object, *, capability: object, resource_id: str):
        del session, capability
        return ResourceDescriptor(
            id=resource_id,
            tenant_id=TENANT,
            kind="artifact",
            owner_type="workspace",
            owner_id=WORKSPACE,
            parent_id=WORKSPACE,
            state="active",
            version=1,
            policy_class="workspace_private",
        )


def _payload(*, idempotency_key: str = "artifact-write-0001") -> dict[str, object]:
    content = b"ok"
    return {
        "idempotency_key": idempotency_key,
        "display_name": "result.txt",
        "media_type": "text/plain",
        "size_bytes": len(content),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_base64": base64.b64encode(content).decode("ascii"),
        "source_resource_ids": [],
    }


def test_artifact_read_adapter_failure_writes_code_only_audit_in_independent_transaction() -> None:
    verifier = FakeVerifier(actions=frozenset({"artifact.read"}))
    verifier.capability = VerifiedCapability(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        runtime_instance_id=RUNTIME,
        actor_user_id=ACTOR,
        grant_id=GRANT,
        token_jti=uuid.uuid4().hex,
        actions=frozenset({"artifact.read"}),
        resource_ids=frozenset({ARTIFACT}),
        constraints=CapabilityConstraints(),
    )
    audit = FakeAudit()
    audit_session = MagicMock(name="artifact_read_audit_session")
    service = WorkspaceDataGatewayService(
        WorkspaceDataGatewayComponents(
            verifier=verifier,
            resolver=ArtifactResolver(),
            adapter=FailingArtifactReadAdapter(),
            audit_sink=audit,
            audit_session_factory=lambda: audit_session,
        )
    )
    request_session = MagicMock(name="artifact_read_request_session")
    credential = WorkloadCredential(
        authorization="workspace-data-token",
        identity=f"spiffe://omnibase/runtime/{RUNTIME}",
        trusted_context=TrustedWorkloadContext(
            opaque_identity=f"spiffe://omnibase/runtime/{RUNTIME}",
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            runtime_instance_id=RUNTIME,
            certificate_thumbprint=THUMBPRINT,
            workload_identity_digest=THUMBPRINT,
        ),
    )

    with pytest.raises(GatewayFailure) as failure:
        service.read_artifact(
            request_session,
            credential,
            ArtifactReadRequest(resource_id=ARTIFACT, resource_version=1, max_bytes=1024),
            "artifact-read-request-0001",
        )

    assert failure.value.status_code == 503
    assert failure.value.code == "write_adapter_unavailable"
    request_session.commit.assert_called_once()
    request_session.rollback.assert_called_once()
    audit_session.commit.assert_called_once()
    audit_session.close.assert_called_once()
    assert len(audit.records) == 1
    record = audit.records[0]
    assert record.decision == "error"
    assert record.status_code == 503
    assert record.reason_code == "artifact_read_adapter_unavailable"
    assert record.resource_id == ARTIFACT
    assert "provider" not in repr(record)
    assert "object_key" not in repr(record)
    assert verifier.budget_requests[0]["bytes_out_reserved"] == 1368


def _client(
    request: pytest.FixtureRequest,
    *,
    actions: frozenset[str] = frozenset({"artifact.write"}),
    adapter: FakeWorkspaceDataAdapter | None = None,
    attestor: StaticAttestor | None = None,
    policy_class: str = "workspace_private",
) -> tuple[TestClient, FakeVerifier, FakeWorkspaceDataAdapter | None, StaticAttestor]:
    verifier = FakeVerifier(actions=actions)
    active_attestor = attestor or StaticAttestor()
    app = create_gateway_app(
        GatewayComponents(
            verifier=verifier,
            resolver=FakeResolver(policy_class=policy_class),
            data_adapter=UnusedReadAdapter(),
            rag_adapter=UnusedRagAdapter(),
            audit_sink=FakeAudit(),
        ),
        workload_attestor=active_attestor,
        workspace_data_adapter=adapter,
        workspace_data_session_factory=MagicMock,
    )
    session = MagicMock()
    app.dependency_overrides[get_gateway_db] = lambda: session
    client = TestClient(app, raise_server_exceptions=False)
    request.addfinalizer(client.close)
    return client, verifier, adapter, active_attestor


def test_default_write_adapter_is_unavailable_and_browser_or_read_tokens_cannot_write(
    request: pytest.FixtureRequest,
) -> None:
    client, _, _, _ = _client(request)
    unavailable = client.post("/gateway/v1/artifacts/write", json=_payload(), headers=HEADERS)
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "write_adapter_unavailable"

    browser = client.post(
        "/gateway/v1/artifacts/write",
        json=_payload(),
        headers={"Authorization": "Bearer browser.jwt"},
    )
    assert browser.status_code == 401

    read_client, _, _, _ = _client(
        request,
        actions=frozenset({"data.rows.read"}),
        adapter=FakeWorkspaceDataAdapter(),
    )
    read_token = read_client.post("/gateway/v1/artifacts/write", json=_payload(), headers=HEADERS)
    assert read_token.status_code == 403


@pytest.mark.parametrize("field", ["physical_locator", "schema", "sql", "object_key"])
def test_write_contract_rejects_and_does_not_echo_physical_locator_fields(
    request: pytest.FixtureRequest,
    field: str,
) -> None:
    client, _, _, _ = _client(request, adapter=FakeWorkspaceDataAdapter())
    response = client.post(
        "/gateway/v1/artifacts/write",
        json={**_payload(), field: "attacker-controlled"},
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert response.json() == {
        "error": {"code": "validation_error", "message": "Request validation failed"}
    }
    assert field not in response.text


def test_artifact_write_exact_replay_uses_read_only_replay_and_binding_drift_rejects(
    request: pytest.FixtureRequest,
) -> None:
    adapter = FakeWorkspaceDataAdapter()
    client, verifier, _, attestor = _client(request, adapter=adapter)
    first = client.post("/gateway/v1/artifacts/write", json=_payload(), headers=HEADERS)
    second = client.post("/gateway/v1/artifacts/write", json=_payload(), headers=HEADERS)
    assert first.status_code == 200
    assert second.status_code == 200
    assert adapter.write_calls == 1
    assert adapter.replay_calls == 1
    assert second.json()["replayed"] is True
    assert verifier.finalized == ["committed"]
    assert attestor.calls >= 3

    drift = client.post(
        "/gateway/v1/artifacts/write",
        json=_payload(idempotency_key="artifact-write-0001") | {"display_name": "changed.txt"},
        headers=HEADERS,
    )
    assert drift.status_code == 409
    assert adapter.write_calls == 1


def test_unknown_or_stale_post_effect_outcome_is_not_replayed(
    request: pytest.FixtureRequest,
) -> None:
    adapter = FakeWorkspaceDataAdapter(unknown=True)
    client, verifier, _, _ = _client(request, adapter=adapter)
    first = client.post("/gateway/v1/artifacts/write", json=_payload(), headers=HEADERS)
    second = client.post("/gateway/v1/artifacts/write", json=_payload(), headers=HEADERS)
    assert first.status_code == 503
    assert first.json()["error"]["code"] == "workspace_data_effect_unknown"
    assert second.status_code == 409
    assert adapter.write_calls == 1
    assert "unknown" in verifier.finalized

    stale_adapter = FakeWorkspaceDataAdapter()
    stale_attestor = StaticAttestor(fail_after=1)
    stale_client, stale_verifier, _, _ = _client(
        request,
        adapter=stale_adapter,
        attestor=stale_attestor,
    )
    stale = stale_client.post(
        "/gateway/v1/artifacts/write",
        json=_payload(idempotency_key="stale-write-0001"),
        headers=HEADERS,
    )
    assert stale.status_code == 503
    assert stale_adapter.write_calls == 1
    assert stale_verifier.finalized == ["unknown"]


def test_post_provider_finalize_failure_is_marked_unknown_in_fresh_transaction(
    request: pytest.FixtureRequest,
) -> None:
    adapter = FakeWorkspaceDataAdapter()
    client, verifier, _, _ = _client(request, adapter=adapter)
    verifier.fail_committed_finalize = True

    first = client.post(
        "/gateway/v1/artifacts/write",
        json=_payload(idempotency_key="finalize-failure-0001"),
        headers=HEADERS,
    )
    second = client.post(
        "/gateway/v1/artifacts/write",
        json=_payload(idempotency_key="finalize-failure-0001"),
        headers=HEADERS,
    )

    assert first.status_code == 503
    assert first.json()["error"]["code"] == "workspace_data_effect_unknown"
    assert second.status_code == 409
    assert adapter.write_calls == 1
    assert verifier.finalized == ["unknown"]


def test_canonical_write_and_promotion_surface_remain_closed(
    request: pytest.FixtureRequest,
) -> None:
    client, _, adapter, _ = _client(
        request,
        adapter=FakeWorkspaceDataAdapter(),
        policy_class="canonical_readonly",
    )
    denied = client.post("/gateway/v1/artifacts/write", json=_payload(), headers=HEADERS)
    assert denied.status_code == 403
    assert adapter is not None
    assert adapter.write_calls == 0

    promotion = client.post("/gateway/v1/rag/promotion/create", json={}, headers=HEADERS)
    assert promotion.status_code == 404
