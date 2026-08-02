"""P34.5D live workload identity and read-only Gateway credential tests."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from omnibase.capabilities.service import (
    InvalidCapability,
    TrustedIssuerContext,
)
from omnibase.capabilities.service import (
    VerifiedCapability as CoreVerifiedCapability,
)
from omnibase.capabilities.token import CapabilityTokenClaims
from omnibase.capability_gateway.adapters import (
    CanonicalRagReadAdapter,
    PostgresDataReadAdapter,
)
from omnibase.capability_gateway.app import create_production_gateway_app
from omnibase.capability_gateway.router import get_gateway_db
from omnibase.capability_gateway.security import (
    CapabilityVerificationError,
    CoreCapabilityVerifier,
    RejectingWorkloadAttestor,
    TrustedScopeWorkloadAttestor,
)
from omnibase.capability_gateway.thumbprints import certificate_thumbprint_to_x5t_s256
from omnibase.capability_gateway.workload import (
    EphemeralGatewayCredential,
    GatewayCredentialIssueRequest,
    GatewayCredentialUnavailable,
    RejectingCapabilityPrivateKeyProvider,
    RejectingGatewayCredentialIssuer,
    SqlAlchemyGatewayCredentialIssuer,
    SqlAlchemyRunLeaseWorkloadAttestor,
    TrustedGatewayPeerEvidence,
)

TENANT = "10000000-0000-0000-0000-000000000001"
WORKSPACE = "20000000-0000-0000-0000-000000000001"
RUN = "30000000-0000-0000-0000-000000000001"
RUNTIME = "40000000-0000-0000-0000-000000000001"
NODE = "50000000-0000-0000-0000-000000000001"
LEASE = "60000000-0000-0000-0000-000000000001"
GRANT = "70000000-0000-0000-0000-000000000001"
ACTOR = "80000000-0000-0000-0000-000000000001"
RESOURCE = "90000000-0000-0000-0000-000000000001"
COLUMN = "a0000000-0000-0000-0000-000000000001"
CITATION = "b0000000-0000-0000-0000-000000000001"
DOCUMENT = "c0000000-0000-0000-0000-000000000001"
OTHER_TENANT = "d0000000-0000-0000-0000-000000000001"
THUMBPRINT = "a" * 64
EVIDENCE_DIGEST = "b" * 64
NOW = datetime(2026, 8, 2, 1, 0, tzinfo=UTC)


def _evidence(**changes: object) -> TrustedGatewayPeerEvidence:
    values: dict[str, object] = {
        "peer_kind": "runner",
        "opaque_identity": f"spiffe://omnibase/runtime/{RUNTIME}",
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "run_id": RUN,
        "runtime_instance_id": RUNTIME,
        "node_id": NODE,
        "lease_id": LEASE,
        "workspace_generation": 3,
        "run_fencing_token": 11,
        "node_fencing_token": 7,
        "certificate_thumbprint": THUMBPRINT,
        "evidence_digest": EVIDENCE_DIGEST,
        "expires_at": NOW + timedelta(minutes=2),
    }
    values.update(changes)
    return TrustedGatewayPeerEvidence(**values)  # type: ignore[arg-type]


def _facts(**changes: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "run_id": RUN,
        "runtime_instance_id": RUNTIME,
        "node_id": NODE,
        "lease_id": LEASE,
        "workspace_generation": 3,
        "run_fencing_token": 11,
        "node_fencing_token": 7,
        "workload_identity_digest": THUMBPRINT,
        "expires_at": NOW + timedelta(minutes=4),
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _issue_request(**changes: object) -> GatewayCredentialIssueRequest:
    values: dict[str, object] = {
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "run_id": RUN,
        "runtime_instance_id": RUNTIME,
        "node_id": NODE,
        "lease_id": LEASE,
        "grant_id": GRANT,
        "key_id": "gateway-key-2026-08",
        "opaque_identity": f"spiffe://omnibase/runtime/{RUNTIME}",
        "workspace_generation": 3,
        "run_fencing_token": 11,
        "node_fencing_token": 7,
        "certificate_thumbprint": THUMBPRINT,
    }
    values.update(changes)
    return GatewayCredentialIssueRequest(**values)  # type: ignore[arg-type]


def _issuer_context(*, tenant_id: str = TENANT) -> TrustedIssuerContext:
    return TrustedIssuerContext(
        tenant_id=tenant_id,
        system_actor_id="e0000000-0000-0000-0000-000000000001",
        originating_user_id=ACTOR,
    )


def _core_capability(action: str) -> CoreVerifiedCapability:
    timestamp = int(NOW.timestamp())
    return CoreVerifiedCapability(
        claims=CapabilityTokenClaims(
            jti="gateway-test-jti-0001",
            subject=RUNTIME,
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_user_id=ACTOR,
            grant_id=GRANT,
            grant_version=1,
            delegation_depth=0,
            workload_thumbprint=THUMBPRINT,
            issued_at=timestamp,
            not_before=timestamp,
            expires_at=timestamp + 120,
            approval_id=None,
        ),
        grant_id=GRANT,
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        runtime_instance_id=RUNTIME,
        actor_user_id=ACTOR,
        action=action,
        resource_id=RESOURCE,
        constraints={
            "max_rows": 20,
            "max_result_bytes": 65_536,
            "rag_top_k": 10,
            "timeout_ms": 2_000,
        },
    )


def _resource_record(*, kind: str, policy_class: str, physical_locator: dict[str, object]):
    return SimpleNamespace(
        id=RESOURCE,
        tenant_id=TENANT,
        kind=kind,
        owner_type="workspace",
        owner_id=WORKSPACE,
        parent_id=WORKSPACE,
        state="active",
        version=1,
        policy_class=policy_class,
        physical_locator=physical_locator,
    )


def _production_client(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    *,
    record: SimpleNamespace,
    action: str,
    lease_facts: SimpleNamespace | None = None,
) -> tuple[TestClient, MagicMock, MagicMock, FastAPI]:
    lease_session = MagicMock(name="lease_session")
    gateway_session = MagicMock(name="gateway_session")
    gateway_session.scalar.return_value = record
    verify_lease = MagicMock(return_value=lease_facts or _facts())
    verify_capability = MagicMock(return_value=_core_capability(action))
    consume_budget = MagicMock()
    append_audit = MagicMock()
    monkeypatch.setattr(
        "omnibase.workspaces.service.verify_run_lease_for_sandbox",
        verify_lease,
    )
    monkeypatch.setattr(
        "omnibase.capabilities.service.verify_capability",
        verify_capability,
    )
    monkeypatch.setattr(
        "omnibase.capabilities.service.consume_budget",
        consume_budget,
    )
    monkeypatch.setattr(
        "omnibase.capability_gateway.audit.append_audit_event",
        append_audit,
    )
    attestor = SqlAlchemyRunLeaseWorkloadAttestor(
        lambda: lease_session,
        clock=lambda: NOW,
    )
    app = create_production_gateway_app(
        workload_attestor=attestor,
        cursor_secret=b"p" * 32,
    )
    evidence = _evidence()

    @app.middleware("http")
    async def inject_trusted_peer(request: Request, call_next):
        request.scope["omnibase.mtls_verified"] = True
        request.scope["omnibase.trusted_gateway_peer"] = evidence
        return await call_next(request)

    app.dependency_overrides[get_gateway_db] = lambda: gateway_session
    client = TestClient(app, raise_server_exceptions=False)
    request.addfinalizer(client.close)
    return client, verify_lease, verify_capability, app


def test_trusted_peer_evidence_rejects_runtime_identity_drift() -> None:
    with pytest.raises(ValueError, match="bind the runtime"):
        _evidence(opaque_identity="spiffe://omnibase/runtime/90000000-0000-0000-0000-000000000001")


@pytest.mark.parametrize("peer_kind", ["browser", "sandbox", "overlay_member", ""])
def test_trusted_peer_evidence_accepts_only_runner_or_network_broker(peer_kind: str) -> None:
    with pytest.raises((TypeError, ValueError), match="peer_kind"):
        _evidence(peer_kind=peer_kind)


@pytest.mark.parametrize("peer_kind", ["runner", "network_broker"])
def test_trusted_peer_evidence_accepts_controlled_gateway_peers(peer_kind: str) -> None:
    assert _evidence(peer_kind=peer_kind).peer_kind == peer_kind


def test_attestor_requires_server_injected_mtls_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    session = MagicMock()
    verifier = MagicMock(return_value=_facts())
    monkeypatch.setattr(
        "omnibase.workspaces.service.verify_run_lease_for_sandbox",
        verifier,
    )
    attestor = SqlAlchemyRunLeaseWorkloadAttestor(lambda: session, clock=lambda: NOW)

    with pytest.raises(CapabilityVerificationError):
        attestor.attest(
            {
                "type": "http",
                "headers": [(b"x-omnibase-workload-cert-sha256", THUMBPRINT.encode("ascii"))],
            },
            f"spiffe://omnibase/runtime/{RUNTIME}",
        )

    verifier.assert_not_called()
    session.close.assert_not_called()


def test_attestor_revalidates_complete_live_lease_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    verifier = MagicMock(return_value=_facts())
    monkeypatch.setattr(
        "omnibase.workspaces.service.verify_run_lease_for_sandbox",
        verifier,
    )
    attestor = SqlAlchemyRunLeaseWorkloadAttestor(lambda: session, clock=lambda: NOW)
    evidence = _evidence()

    trusted = attestor.attest(
        {
            "type": "http",
            "omnibase.mtls_verified": True,
            "omnibase.trusted_gateway_peer": evidence,
        },
        evidence.opaque_identity,
    )

    assert trusted.tenant_id == TENANT
    assert trusted.workspace_id == WORKSPACE
    assert trusted.runtime_instance_id == RUNTIME
    assert trusted.certificate_thumbprint == THUMBPRINT
    verifier.assert_called_once_with(
        session,
        tenant_id=TENANT,
        run_id=RUN,
        runtime_instance_id=RUNTIME,
        lease_id=LEASE,
        node_id=NODE,
        generation=3,
        fencing_token=11,
        workload_identity_digest=THUMBPRINT,
    )
    session.close.assert_called_once()


def test_attestor_rejects_node_fencing_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    session = MagicMock()
    monkeypatch.setattr(
        "omnibase.workspaces.service.verify_run_lease_for_sandbox",
        MagicMock(return_value=_facts(node_fencing_token=8)),
    )
    attestor = SqlAlchemyRunLeaseWorkloadAttestor(lambda: session, clock=lambda: NOW)
    evidence = _evidence()

    with pytest.raises(CapabilityVerificationError):
        attestor.attest(
            {
                "type": "http",
                "omnibase.mtls_verified": True,
                "omnibase.trusted_gateway_peer": evidence,
            },
            evidence.opaque_identity,
        )

    session.rollback.assert_called_once()
    session.close.assert_called_once()


@pytest.mark.parametrize(
    ("fact_name", "drifted_value"),
    [
        ("tenant_id", OTHER_TENANT),
        ("workspace_id", "20000000-0000-0000-0000-000000000099"),
        ("run_id", "30000000-0000-0000-0000-000000000099"),
        ("runtime_instance_id", "40000000-0000-0000-0000-000000000099"),
        ("node_id", "50000000-0000-0000-0000-000000000099"),
        ("lease_id", "60000000-0000-0000-0000-000000000099"),
        ("workspace_generation", 4),
        ("run_fencing_token", 12),
        ("node_fencing_token", 8),
        ("workload_identity_digest", "f" * 64),
        ("expires_at", NOW),
    ],
)
def test_attestor_rejects_every_live_lease_binding_drift(
    monkeypatch: pytest.MonkeyPatch,
    fact_name: str,
    drifted_value: object,
) -> None:
    session = MagicMock()
    monkeypatch.setattr(
        "omnibase.workspaces.service.verify_run_lease_for_sandbox",
        MagicMock(return_value=_facts(**{fact_name: drifted_value})),
    )
    attestor = SqlAlchemyRunLeaseWorkloadAttestor(lambda: session, clock=lambda: NOW)
    evidence = _evidence()

    with pytest.raises(CapabilityVerificationError):
        attestor.attest(
            {
                "type": "http",
                "omnibase.mtls_verified": True,
                "omnibase.trusted_gateway_peer": evidence,
            },
            evidence.opaque_identity,
        )

    session.rollback.assert_called_once()
    session.close.assert_called_once()


def test_expired_transport_evidence_fails_before_database_access() -> None:
    session_factory = MagicMock()
    attestor = SqlAlchemyRunLeaseWorkloadAttestor(session_factory, clock=lambda: NOW)
    evidence = _evidence(expires_at=NOW)

    with pytest.raises(CapabilityVerificationError):
        attestor.attest(
            {
                "type": "http",
                "omnibase.mtls_verified": True,
                "omnibase.trusted_gateway_peer": evidence,
            },
            evidence.opaque_identity,
        )

    session_factory.assert_not_called()


class _KeyProvider:
    def load_private_key(self, key_id: str) -> bytes:
        assert key_id == "gateway-key-2026-08"
        return b"private-key-material"


def test_gateway_credential_issuer_binds_token_to_live_run_and_certificate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    verify_lease = MagicMock(return_value=_facts(expires_at=NOW + timedelta(seconds=90)))
    issue_token = MagicMock(return_value="signed.read.capability")
    monkeypatch.setattr(
        "omnibase.workspaces.service.verify_run_lease_for_sandbox",
        verify_lease,
    )
    monkeypatch.setattr("omnibase.capabilities.service.issue_token", issue_token)
    issuer = SqlAlchemyGatewayCredentialIssuer(
        lambda: session,
        _KeyProvider(),
        clock=lambda: NOW,
    )
    issuer_context = TrustedIssuerContext(
        tenant_id=TENANT,
        system_actor_id="gateway-credential-broker",
        originating_user_id="80000000-0000-0000-0000-000000000001",
    )

    credential = issuer.issue(
        _issue_request(),
        issuer_context=issuer_context,
        ttl=timedelta(minutes=5),
    )

    assert isinstance(credential, EphemeralGatewayCredential)
    assert credential.expires_at == NOW + timedelta(seconds=90)
    assert "signed.read.capability" not in repr(credential)
    issue_token.assert_called_once_with(
        session,
        tenant_id=TENANT,
        grant_id=GRANT,
        kid="gateway-key-2026-08",
        private_key_pem=b"private-key-material",
        workload_thumbprint=certificate_thumbprint_to_x5t_s256(THUMBPRINT),
        issuer_context=issuer_context,
        ttl=timedelta(seconds=90),
    )
    session.commit.assert_called_once()
    session.close.assert_called_once()


def test_gateway_credential_issuer_rejects_stale_node_fencing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    monkeypatch.setattr(
        "omnibase.workspaces.service.verify_run_lease_for_sandbox",
        MagicMock(return_value=_facts(node_fencing_token=99)),
    )
    issue_token = MagicMock(return_value="must-not-be-issued")
    monkeypatch.setattr("omnibase.capabilities.service.issue_token", issue_token)
    issuer = SqlAlchemyGatewayCredentialIssuer(
        lambda: session,
        _KeyProvider(),
        clock=lambda: NOW,
    )

    with pytest.raises(GatewayCredentialUnavailable):
        issuer.issue(
            _issue_request(),
            issuer_context=TrustedIssuerContext(
                tenant_id=TENANT,
                system_actor_id="gateway-credential-broker",
                originating_user_id="80000000-0000-0000-0000-000000000001",
            ),
        )

    issue_token.assert_not_called()
    session.rollback.assert_called_once()


@pytest.mark.parametrize(
    ("fact_name", "drifted_value"),
    [
        ("tenant_id", OTHER_TENANT),
        ("workspace_id", "20000000-0000-0000-0000-000000000099"),
        ("run_id", "30000000-0000-0000-0000-000000000099"),
        ("runtime_instance_id", "40000000-0000-0000-0000-000000000099"),
        ("node_id", "50000000-0000-0000-0000-000000000099"),
        ("lease_id", "60000000-0000-0000-0000-000000000099"),
        ("workspace_generation", 4),
        ("run_fencing_token", 12),
        ("node_fencing_token", 8),
        ("workload_identity_digest", "f" * 64),
        ("expires_at", NOW),
    ],
)
def test_credential_issuer_rejects_binding_drift_before_loading_private_key(
    monkeypatch: pytest.MonkeyPatch,
    fact_name: str,
    drifted_value: object,
) -> None:
    session = MagicMock()
    key_provider = MagicMock()
    issue_token = MagicMock(return_value="must-not-be-issued")
    monkeypatch.setattr(
        "omnibase.workspaces.service.verify_run_lease_for_sandbox",
        MagicMock(return_value=_facts(**{fact_name: drifted_value})),
    )
    monkeypatch.setattr("omnibase.capabilities.service.issue_token", issue_token)
    issuer = SqlAlchemyGatewayCredentialIssuer(
        lambda: session,
        key_provider,
        clock=lambda: NOW,
    )

    with pytest.raises(GatewayCredentialUnavailable):
        issuer.issue(_issue_request(), issuer_context=_issuer_context())

    key_provider.load_private_key.assert_not_called()
    issue_token.assert_not_called()
    session.rollback.assert_called_once()
    session.close.assert_called_once()


def test_credential_issuer_rejects_cross_tenant_context_before_key_or_database() -> None:
    session_factory = MagicMock()
    key_provider = MagicMock()
    issuer = SqlAlchemyGatewayCredentialIssuer(
        session_factory,
        key_provider,
        clock=lambda: NOW,
    )

    with pytest.raises(GatewayCredentialUnavailable):
        issuer.issue(
            _issue_request(),
            issuer_context=_issuer_context(tenant_id=OTHER_TENANT),
        )

    session_factory.assert_not_called()
    key_provider.load_private_key.assert_not_called()


@pytest.mark.parametrize(
    "ttl",
    [timedelta(0), timedelta(seconds=-1), timedelta(minutes=5, microseconds=1)],
)
def test_credential_ttl_is_bounded_before_key_or_database(ttl: timedelta) -> None:
    session_factory = MagicMock()
    key_provider = MagicMock()
    issuer = SqlAlchemyGatewayCredentialIssuer(
        session_factory,
        key_provider,
        clock=lambda: NOW,
    )

    with pytest.raises(ValueError, match="at most five minutes"):
        issuer.issue(_issue_request(), issuer_context=_issuer_context(), ttl=ttl)

    session_factory.assert_not_called()
    key_provider.load_private_key.assert_not_called()


def test_rejecting_credential_defaults_never_expose_material() -> None:
    with pytest.raises(GatewayCredentialUnavailable):
        RejectingCapabilityPrivateKeyProvider().load_private_key("gateway-key")
    with pytest.raises(GatewayCredentialUnavailable):
        RejectingGatewayCredentialIssuer().issue(
            _issue_request(),
            issuer_context=TrustedIssuerContext(
                tenant_id=TENANT,
                system_actor_id="gateway-credential-broker",
                originating_user_id="80000000-0000-0000-0000-000000000001",
            ),
        )


def test_production_gateway_requires_explicit_trusted_attestor_and_cursor_secret() -> None:
    with pytest.raises(ValueError, match="live Run Lease workload attestor"):
        create_production_gateway_app(
            workload_attestor=RejectingWorkloadAttestor(),  # type: ignore[arg-type]
            cursor_secret=b"x" * 32,
        )
    with pytest.raises(ValueError, match="live Run Lease workload attestor"):
        create_production_gateway_app(
            workload_attestor=TrustedScopeWorkloadAttestor(),  # type: ignore[arg-type]
            cursor_secret=b"x" * 32,
        )
    with pytest.raises(ValueError, match="at least 32 bytes"):
        create_production_gateway_app(
            workload_attestor=SqlAlchemyRunLeaseWorkloadAttestor(
                lambda: MagicMock(), clock=lambda: NOW
            ),
            cursor_secret=b"short",
        )


def test_production_gateway_composes_read_only_core_verifier() -> None:
    attestor = SqlAlchemyRunLeaseWorkloadAttestor(lambda: MagicMock(), clock=lambda: NOW)
    app = create_production_gateway_app(
        workload_attestor=attestor,
        cursor_secret=b"g" * 32,
    )

    assert app.state.workload_attestor is attestor
    assert isinstance(app.state.capability_verifier, CoreCapabilityVerifier)
    assert set(app.openapi()["paths"]) == {
        "/gateway/v1/data/schema/read",
        "/gateway/v1/data/rows/read",
        "/gateway/v1/rag/search",
        "/gateway/v1/rag/citations/read",
    }


def test_header_spoof_cannot_create_trusted_runner_or_broker_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease_session_factory = MagicMock()
    verify_capability = MagicMock()
    monkeypatch.setattr(
        "omnibase.capabilities.service.verify_capability",
        verify_capability,
    )
    app = create_production_gateway_app(
        workload_attestor=SqlAlchemyRunLeaseWorkloadAttestor(
            lease_session_factory,
            clock=lambda: NOW,
        ),
        cursor_secret=b"s" * 32,
    )
    gateway_session_factory = MagicMock()
    app.dependency_overrides[get_gateway_db] = gateway_session_factory
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/gateway/v1/data/schema/read",
            json={"resource_id": RESOURCE},
            headers={
                "Authorization": "Capability forged-token",
                "X-Omnibase-Workload-Identity": f"spiffe://omnibase/runtime/{RUNTIME}",
                "X-Omnibase-Mtls-Verified": "true",
                "X-Omnibase-Peer-Kind": "runner",
                "X-Omnibase-Workload-Cert-Sha256": THUMBPRINT,
                "X-Omnibase-Tenant-Id": TENANT,
                "X-Omnibase-Workspace-Id": WORKSPACE,
            },
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_capability"
    lease_session_factory.assert_not_called()
    gateway_session_factory.assert_not_called()
    verify_capability.assert_not_called()


def test_live_peer_and_capability_reach_only_postgres_read_adapter(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    record = _resource_record(
        kind="data_table",
        policy_class="workspace_private",
        physical_locator={
            "adapter": "postgres",
            "schema": "tenant_deadbeef",
            "table": "data_1234",
            "columns": {
                COLUMN: {
                    "name": "col_1234",
                    "display_name": "Safe Name",
                    "type": "text",
                    "nullable": False,
                }
            },
        },
    )
    client, verify_lease, verify_capability, app = _production_client(
        monkeypatch,
        request,
        record=record,
        action="data.schema.read",
    )
    components = app.state.gateway_service._components
    assert isinstance(components.data_adapter, PostgresDataReadAdapter)

    response = client.post(
        "/gateway/v1/data/schema/read",
        json={"resource_id": RESOURCE},
        headers={
            "Authorization": "Capability signed.read.capability",
            "X-Omnibase-Workload-Identity": f"spiffe://omnibase/runtime/{RUNTIME}",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "resource_id": RESOURCE,
        "resource_version": 1,
        "columns": [
            {
                "id": COLUMN,
                "display_name": "Safe Name",
                "type": "text",
                "nullable": False,
            }
        ],
    }
    assert "tenant_deadbeef" not in response.text
    assert "data_1234" not in response.text
    verify_lease.assert_called_once()
    verify_capability.assert_called_once_with(
        ANY,
        token="signed.read.capability",  # noqa: S106 - synthetic non-secret test token
        expected_tenant_id=TENANT,
        expected_workspace_id=WORKSPACE,
        expected_runtime_instance_id=RUNTIME,
        expected_workload_thumbprint=certificate_thumbprint_to_x5t_s256(THUMBPRINT),
        action="data.schema.read",
        resource_id=RESOURCE,
    )


@pytest.mark.parametrize(
    "lease_facts",
    [
        _facts(tenant_id=OTHER_TENANT),
        _facts(workspace_id="20000000-0000-0000-0000-000000000099"),
        _facts(workspace_generation=4),
        _facts(run_fencing_token=12),
        _facts(node_fencing_token=8),
        _facts(expires_at=NOW),
    ],
)
def test_stale_live_lease_never_reaches_capability_or_postgres_adapter(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    lease_facts: SimpleNamespace,
) -> None:
    record = _resource_record(
        kind="data_table",
        policy_class="workspace_private",
        physical_locator={
            "adapter": "postgres",
            "schema": "tenant_deadbeef",
            "table": "data_1234",
            "columns": {
                COLUMN: {
                    "name": "col_1234",
                    "display_name": "Safe Name",
                    "type": "text",
                    "nullable": False,
                }
            },
        },
    )
    client, verify_lease, verify_capability, app = _production_client(
        monkeypatch,
        request,
        record=record,
        action="data.schema.read",
        lease_facts=lease_facts,
    )
    adapter_call = MagicMock()
    monkeypatch.setattr(
        app.state.gateway_service._components.data_adapter,
        "read_schema",
        adapter_call,
    )

    response = client.post(
        "/gateway/v1/data/schema/read",
        json={"resource_id": RESOURCE},
        headers={
            "Authorization": "Capability signed.read.capability",
            "X-Omnibase-Workload-Identity": f"spiffe://omnibase/runtime/{RUNTIME}",
        },
    )

    assert response.status_code == 401
    verify_lease.assert_called_once()
    verify_capability.assert_not_called()
    adapter_call.assert_not_called()


def test_cross_tenant_capability_never_reaches_postgres_adapter(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    record = _resource_record(
        kind="data_table",
        policy_class="workspace_private",
        physical_locator={
            "adapter": "postgres",
            "schema": "tenant_deadbeef",
            "table": "data_1234",
            "columns": {
                COLUMN: {
                    "name": "col_1234",
                    "display_name": "Safe Name",
                    "type": "text",
                    "nullable": False,
                }
            },
        },
    )
    client, verify_lease, verify_capability, app = _production_client(
        monkeypatch,
        request,
        record=record,
        action="data.schema.read",
    )
    verify_capability.side_effect = InvalidCapability("cross-tenant token")
    adapter_call = MagicMock()
    monkeypatch.setattr(
        app.state.gateway_service._components.data_adapter,
        "read_schema",
        adapter_call,
    )

    response = client.post(
        "/gateway/v1/data/schema/read",
        json={"resource_id": RESOURCE},
        headers={
            "Authorization": "Capability cross-tenant-token",
            "X-Omnibase-Workload-Identity": f"spiffe://omnibase/runtime/{RUNTIME}",
        },
    )

    assert response.status_code == 401
    verify_lease.assert_called_once()
    verify_capability.assert_called_once()
    adapter_call.assert_not_called()


def test_live_peer_and_capability_reach_only_canonical_rag_read_adapter(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    ranked = SimpleNamespace(
        chunk=SimpleNamespace(
            chunk_id=CITATION,
            document_id=DOCUMENT,
            score=0.91,
            content="bounded citation text",
            metadata={"page": 2},
        )
    )
    hybrid_search = MagicMock(return_value=SimpleNamespace(results=[ranked]))
    rerank = MagicMock(return_value=[ranked])
    monkeypatch.setattr(
        "omnibase.capability_gateway.adapters.hybrid_search_detailed",
        hybrid_search,
    )
    monkeypatch.setattr("omnibase.capability_gateway.adapters.rerank", rerank)
    record = _resource_record(
        kind="derived_index",
        policy_class="workspace_derived",
        physical_locator={
            "adapter": "canonical_rag_v1",
            "schema": "tenant_deadbeef",
        },
    )
    client, verify_lease, verify_capability, app = _production_client(
        monkeypatch,
        request,
        record=record,
        action="rag.search",
    )
    components = app.state.gateway_service._components
    assert isinstance(components.rag_adapter, CanonicalRagReadAdapter)

    response = client.post(
        "/gateway/v1/rag/search",
        json={
            "resource_id": RESOURCE,
            "query": "safe query",
            "top_k": 5,
            "timeout_ms": 1_000,
            "max_bytes": 65_536,
        },
        headers={
            "Authorization": "Capability signed.read.capability",
            "X-Omnibase-Workload-Identity": f"spiffe://omnibase/runtime/{RUNTIME}",
        },
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["citation_id"] == CITATION
    assert response.json()["results"][0]["document_id"] == DOCUMENT
    assert "tenant_deadbeef" not in response.text
    verify_lease.assert_called_once()
    verify_capability.assert_called_once()
    hybrid_search.assert_called_once()
    rerank.assert_called_once()


def test_gateway_contract_rejects_direct_infrastructure_credentials(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    record = _resource_record(
        kind="data_table",
        policy_class="workspace_private",
        physical_locator={
            "adapter": "postgres",
            "schema": "tenant_deadbeef",
            "table": "data_1234",
            "columns": {
                COLUMN: {
                    "name": "col_1234",
                    "display_name": "Safe Name",
                    "type": "text",
                    "nullable": False,
                }
            },
        },
    )
    client, verify_lease, verify_capability, _ = _production_client(
        monkeypatch,
        request,
        record=record,
        action="data.schema.read",
    )

    response = client.post(
        "/gateway/v1/data/schema/read",
        json={
            "resource_id": RESOURCE,
            "database_url": "postgresql://sandbox-direct/forbidden",
            "redis_url": "redis://sandbox-direct/forbidden",
            "minio_endpoint": "http://sandbox-direct/forbidden",
        },
        headers={
            "Authorization": "Capability signed.read.capability",
            "X-Omnibase-Workload-Identity": f"spiffe://omnibase/runtime/{RUNTIME}",
        },
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": {"code": "validation_error", "message": "Request validation failed"}
    }
    verify_lease.assert_called_once()
    verify_capability.assert_not_called()

    forbidden_fields = {
        "database_url",
        "postgresql_url",
        "redis_url",
        "minio_endpoint",
        "private_key",
        "signing_key",
        "overlay_identity",
        "provider_handle",
    }
    for contract in (
        TrustedGatewayPeerEvidence,
        GatewayCredentialIssueRequest,
        EphemeralGatewayCredential,
    ):
        assert forbidden_fields.isdisjoint(field.name for field in fields(contract))


@pytest.mark.parametrize(
    "path",
    [
        "/gateway/v1/database/connect",
        "/gateway/v1/redis/connect",
        "/gateway/v1/minio/connect",
        "/gateway/v1/sql/execute",
    ],
)
def test_production_gateway_has_no_direct_infrastructure_route(path: str) -> None:
    app = create_production_gateway_app(
        workload_attestor=SqlAlchemyRunLeaseWorkloadAttestor(
            lambda: MagicMock(),
            clock=lambda: NOW,
        ),
        cursor_secret=b"n" * 32,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(path, json={})
    assert response.status_code == 404
