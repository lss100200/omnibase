"""Focused CAP-01..05 tests for the P34.2 capability core."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from omnibase.capabilities import service
from omnibase.capabilities.models import CapabilityGrant, CapabilitySigningKey
from omnibase.capabilities.token import (
    ALGORITHM,
    AUDIENCE,
    ISSUER,
    TOKEN_TYPE,
    CapabilityTokenError,
    decode_capability_token,
    encode_capability_token,
    get_trusted_kid,
    public_key_fingerprint,
)


@pytest.fixture(scope="module")
def rsa_keys() -> tuple[str, str]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = (
        private.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return private_pem, public_pem


def _ids() -> dict[str, str]:
    return {
        name: str(uuid.uuid4())
        for name in ("tenant", "workspace", "runtime", "user", "grant", "resource", "issuer")
    }


def _token(ids: dict[str, str], private_pem: str, *, version: int = 1) -> str:
    now = datetime.now(UTC)
    return encode_capability_token(
        private_key_pem=private_pem,
        kid="test-key-0001",
        jti=uuid.uuid4().hex,
        subject=ids["runtime"],
        tenant_id=ids["tenant"],
        workspace_id=ids["workspace"],
        actor_user_id=ids["user"],
        grant_id=ids["grant"],
        grant_version=version,
        delegation_depth=0,
        workload_thumbprint="A" * 43,
        issued_at=now,
        expires_at=now + timedelta(minutes=2),
        approval_id=None,
    )


def _raw_payload(
    ids: dict[str, str],
    *,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    issued_at: int | None = None,
    not_before: int | None = None,
    expires_at: int | None = None,
    version: int = 1,
) -> dict[str, object]:
    now = int(datetime.now(UTC).timestamp())
    issued_at = now if issued_at is None else issued_at
    not_before = issued_at if not_before is None else not_before
    expires_at = now + 60 if expires_at is None else expires_at
    return {
        "iss": issuer,
        "aud": audience,
        "jti": uuid.uuid4().hex,
        "sub": ids["runtime"],
        "tenant_id": ids["tenant"],
        "workspace_id": ids["workspace"],
        "actor_user_id": ids["user"],
        "grant_id": ids["grant"],
        "grant_version": version,
        "delegation_depth": 0,
        "cnf": {"x5t#S256": "A" * 43},
        "iat": issued_at,
        "nbf": not_before,
        "exp": expires_at,
    }


def _raw_token(
    ids: dict[str, str],
    private_pem: str,
    *,
    payload: dict[str, object] | None = None,
    kid: str = "test-key-0001",
    protected_type: str = TOKEN_TYPE,
) -> str:
    return jwt.encode(
        _raw_payload(ids) if payload is None else payload,
        private_pem,
        algorithm=ALGORITHM,
        headers={"alg": ALGORITHM, "kid": kid, "typ": protected_type},
    )


def _result(*, scalar: object = None, scalars: list[str] | None = None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar
    result.scalar_one.return_value = scalar
    result.scalars.return_value = [] if scalars is None else scalars
    return result


def test_cap01_token_contract_is_fixed_asymmetric_and_workload_bound(rsa_keys) -> None:
    ids = _ids()
    private_pem, public_pem = rsa_keys
    token = _token(ids, private_pem)

    assert get_trusted_kid(token) == "test-key-0001"
    claims = decode_capability_token(token=token, public_key_pem=public_pem)
    assert claims.subject == ids["runtime"]
    assert claims.tenant_id == ids["tenant"]
    assert claims.workspace_id == ids["workspace"]
    assert claims.grant_version == 1
    assert claims.workload_thumbprint == "A" * 43
    header = jwt.get_unverified_header(token)
    assert header == {"alg": ALGORITHM, "kid": "test-key-0001", "typ": TOKEN_TYPE}


def test_cap01_remote_or_embedded_key_discovery_headers_are_rejected(rsa_keys) -> None:
    ids = _ids()
    private_pem, _ = rsa_keys
    now = int(datetime.now(UTC).timestamp())
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "jti": uuid.uuid4().hex,
        "sub": ids["runtime"],
        "tenant_id": ids["tenant"],
        "workspace_id": ids["workspace"],
        "actor_user_id": ids["user"],
        "grant_id": ids["grant"],
        "grant_version": 1,
        "delegation_depth": 0,
        "cnf": {"x5t#S256": "A" * 43},
        "iat": now,
        "nbf": now,
        "exp": now + 60,
    }
    token = jwt.encode(
        payload,
        private_pem,
        algorithm=ALGORITHM,
        headers={
            "alg": ALGORITHM,
            "kid": "test-key-0001",
            "typ": TOKEN_TYPE,
            "jku": "https://attacker.invalid/jwks.json",
        },
    )
    with pytest.raises(CapabilityTokenError, match="invalid capability"):
        get_trusted_kid(token)


@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("iss", "attacker-capability-issuer"),
        ("aud", "attacker-capability-gateway"),
    ],
)
def test_cap01_wrong_issuer_or_audience_is_rejected(
    rsa_keys,
    claim: str,
    value: str,
) -> None:
    ids = _ids()
    private_pem, public_pem = rsa_keys
    payload = _raw_payload(ids)
    payload[claim] = value
    token = _raw_token(ids, private_pem, payload=payload)
    with pytest.raises(CapabilityTokenError, match="invalid capability"):
        decode_capability_token(token=token, public_key_pem=public_pem)


def test_cap01_wrong_algorithm_or_type_is_rejected(rsa_keys) -> None:
    ids = _ids()
    private_pem, _ = rsa_keys
    wrong_algorithm = jwt.encode(
        _raw_payload(ids),
        "not-a-capability-key",
        algorithm="HS256",
        headers={"alg": "HS256", "kid": "test-key-0001", "typ": TOKEN_TYPE},
    )
    wrong_type = _raw_token(ids, private_pem, protected_type="JWT")
    for token in (wrong_algorithm, wrong_type):
        with pytest.raises(CapabilityTokenError, match="invalid capability"):
            get_trusted_kid(token)


def test_cap01_expired_and_future_not_before_tokens_are_rejected(rsa_keys) -> None:
    ids = _ids()
    private_pem, public_pem = rsa_keys
    now = int(datetime.now(UTC).timestamp())
    expired = _raw_token(
        ids,
        private_pem,
        payload=_raw_payload(
            ids,
            issued_at=now - 120,
            not_before=now - 120,
            expires_at=now - 60,
        ),
    )
    future = _raw_token(
        ids,
        private_pem,
        payload=_raw_payload(
            ids,
            issued_at=now + 60,
            not_before=now + 60,
            expires_at=now + 120,
        ),
    )
    for token in (expired, future):
        with pytest.raises(CapabilityTokenError, match="invalid capability"):
            decode_capability_token(token=token, public_key_pem=public_pem)


def test_cap01_unknown_kid_is_rejected_before_signature_verification(rsa_keys) -> None:
    ids = _ids()
    private_pem, _ = rsa_keys
    token = _raw_token(ids, private_pem, kid="unknown-key-0001")
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = None
    with pytest.raises(service.InvalidCapability, match="invalid capability"):
        service.verify_capability(
            session,
            token=token,
            expected_tenant_id=ids["tenant"],
            expected_workspace_id=ids["workspace"],
            expected_runtime_instance_id=ids["runtime"],
            expected_workload_thumbprint="A" * 43,
            action="rag.search",
            resource_id=ids["resource"],
        )
    assert session.execute.call_count == 1


def test_cap01_jti_charset_and_p34_2_approval_claim_are_closed(rsa_keys) -> None:
    ids = _ids()
    private_pem, public_pem = rsa_keys
    invalid_jti = _raw_payload(ids)
    invalid_jti["jti"] = "invalid:jti:value"
    approval = _raw_payload(ids)
    approval["approval_id"] = str(uuid.uuid4())

    for payload in (invalid_jti, approval):
        token = _raw_token(ids, private_pem, payload=payload)
        with pytest.raises(CapabilityTokenError, match="invalid capability"):
            decode_capability_token(token=token, public_key_pem=public_pem)

    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="jti"):
        encode_capability_token(
            private_key_pem=private_pem,
            kid="test-key-0001",
            jti="invalid:jti:value",
            subject=ids["runtime"],
            tenant_id=ids["tenant"],
            workspace_id=ids["workspace"],
            actor_user_id=ids["user"],
            grant_id=ids["grant"],
            grant_version=1,
            delegation_depth=0,
            workload_thumbprint="A" * 43,
            issued_at=now,
            expires_at=now + timedelta(minutes=1),
            approval_id=None,
        )


def test_cap02_create_grant_rejects_workspace_self_issuance_before_db_access() -> None:
    ids = _ids()
    session = MagicMock()
    with pytest.raises(service.CapabilityScopeDenied, match="trusted issuer"):
        service.create_grant(
            session,
            tenant_id=ids["tenant"],
            workspace_id=ids["workspace"],
            runtime_instance_id=ids["runtime"],
            issuer_context=SimpleNamespace(
                tenant_id=ids["tenant"],
                system_actor_id=ids["workspace"],
                originating_user_id=ids["user"],
            ),
            actions={"data.rows.read"},
            resource_ids={ids["resource"]},
            not_before=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            max_calls=5,
            max_bytes=1000,
            max_cost_units=5,
            delegation_depth_limit=0,
        )
    session.execute.assert_not_called()


def test_cap02_grant_rejects_wildcards_and_write_actions_before_db_access() -> None:
    ids = _ids()
    for actions in ({"*"}, {"data.rows.insert"}, {"rag.citations.read"}):
        session = MagicMock()
        with pytest.raises(service.CapabilityScopeDenied):
            service.create_grant(
                session,
                tenant_id=ids["tenant"],
                workspace_id=ids["workspace"],
                runtime_instance_id=ids["runtime"],
                issuer_context=service.TrustedIssuerContext(
                    tenant_id=ids["tenant"],
                    system_actor_id=ids["issuer"],
                    originating_user_id=ids["user"],
                ),
                actions=actions,
                resource_ids={ids["resource"]},
                not_before=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
                max_calls=5,
                max_bytes=1000,
                max_cost_units=5,
                delegation_depth_limit=0,
            )
        session.execute.assert_not_called()


def test_cap02_p34_2_grant_rejects_approval_before_db_access() -> None:
    ids = _ids()
    session = MagicMock()
    with pytest.raises(service.CapabilityScopeDenied, match="cannot bind an approval"):
        service.create_grant(
            session,
            tenant_id=ids["tenant"],
            workspace_id=ids["workspace"],
            runtime_instance_id=ids["runtime"],
            issuer_context=service.TrustedIssuerContext(
                tenant_id=ids["tenant"],
                system_actor_id=ids["issuer"],
                originating_user_id=ids["user"],
            ),
            actions={"rag.search"},
            resource_ids={ids["resource"]},
            not_before=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            max_calls=5,
            max_bytes=1000,
            max_cost_units=5,
            delegation_depth_limit=0,
            approval_id=str(uuid.uuid4()),
        )
    session.execute.assert_not_called()


def test_cap02_timeout_constraint_cannot_exceed_gateway_ceiling() -> None:
    ids = _ids()
    session = MagicMock()
    with pytest.raises(ValueError, match="cannot exceed 5000"):
        service.create_grant(
            session,
            tenant_id=ids["tenant"],
            workspace_id=ids["workspace"],
            runtime_instance_id=ids["runtime"],
            issuer_context=service.TrustedIssuerContext(
                tenant_id=ids["tenant"],
                system_actor_id=ids["issuer"],
                originating_user_id=ids["user"],
            ),
            actions={"rag.search"},
            resource_ids={ids["resource"]},
            not_before=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            max_calls=5,
            max_bytes=1000,
            max_cost_units=5,
            delegation_depth_limit=0,
            constraints={"timeout_ms": 5001},
        )
    session.execute.assert_not_called()


@pytest.mark.parametrize("constraints", [None, {}, {"rag_top_k": 10}])
def test_cap02_timeout_constraint_is_required_before_db_access(
    constraints: dict[str, object] | None,
) -> None:
    ids = _ids()
    session = MagicMock()
    with pytest.raises(ValueError, match="timeout_ms is required"):
        service.create_grant(
            session,
            tenant_id=ids["tenant"],
            workspace_id=ids["workspace"],
            runtime_instance_id=ids["runtime"],
            issuer_context=service.TrustedIssuerContext(
                tenant_id=ids["tenant"],
                system_actor_id=ids["issuer"],
                originating_user_id=ids["user"],
            ),
            actions={"rag.search"},
            resource_ids={ids["resource"]},
            not_before=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            max_calls=5,
            max_bytes=1000,
            max_cost_units=5,
            delegation_depth_limit=0,
            constraints=constraints,
        )
    session.execute.assert_not_called()


def test_cap03_delegation_cannot_widen_action_or_resource_scope(monkeypatch) -> None:
    ids = _ids()
    parent = SimpleNamespace(
        id=ids["grant"],
        tenant_id=ids["tenant"],
        workspace_id=ids["workspace"],
        runtime_instance_id=ids["runtime"],
        actor_user_id=ids["user"],
        actions=["rag.search"],
        resource_ids=[ids["resource"]],
        constraints={"timeout_ms": 2000},
        version=1,
        state="active",
        not_before=datetime.now(UTC) - timedelta(minutes=1),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        max_calls=5,
        max_bytes=1000,
        max_cost_units=5,
        delegation_depth=0,
        delegation_depth_limit=1,
        approval_id=None,
    )
    monkeypatch.setattr(service, "get_grant", lambda *args, **kwargs: parent)
    with pytest.raises(service.CapabilityScopeDenied, match="widen"):
        service.delegate_grant(
            MagicMock(),
            tenant_id=ids["tenant"],
            parent_grant_id=ids["grant"],
            runtime_instance_id=str(uuid.uuid4()),
            actions={"rag.search", "rag.citation.read"},
            resource_ids={ids["resource"]},
            expires_at=parent.expires_at - timedelta(minutes=1),
            max_calls=4,
            max_bytes=900,
            max_cost_units=4,
            issuer_context=service.TrustedIssuerContext(
                tenant_id=ids["tenant"],
                system_actor_id=ids["issuer"],
                originating_user_id=ids["user"],
            ),
        )


def test_cap03_sibling_delegations_reserve_parent_budget_atomically(monkeypatch) -> None:
    ids = _ids()
    parent = SimpleNamespace(
        id=ids["grant"],
        tenant_id=ids["tenant"],
        workspace_id=ids["workspace"],
        runtime_instance_id=ids["runtime"],
        actor_user_id=ids["user"],
        actions=["rag.search"],
        resource_ids=[ids["resource"]],
        constraints={"rag_top_k": 10, "timeout_ms": 2000},
        version=1,
        state="active",
        not_before=datetime.now(UTC) - timedelta(minutes=1),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        max_calls=5,
        max_bytes=1000,
        max_cost_units=5,
        delegation_depth=0,
        delegation_depth_limit=1,
        approval_id=None,
    )
    monkeypatch.setattr(service, "get_grant", lambda *args, **kwargs: parent)
    monkeypatch.setattr(service, "_create_grant", lambda *args, **kwargs: "child")
    session = MagicMock()
    first = _result(scalar=SimpleNamespace(calls=3))
    exhausted = _result(scalar=None)
    session.execute.side_effect = [first, exhausted]
    issuer = service.TrustedIssuerContext(
        tenant_id=ids["tenant"],
        system_actor_id=ids["issuer"],
        originating_user_id=ids["user"],
    )
    arguments = {
        "tenant_id": ids["tenant"],
        "parent_grant_id": ids["grant"],
        "runtime_instance_id": str(uuid.uuid4()),
        "actions": {"rag.search"},
        "resource_ids": {ids["resource"]},
        "expires_at": parent.expires_at - timedelta(minutes=1),
        "max_calls": 3,
        "max_bytes": 600,
        "max_cost_units": 3,
        "issuer_context": issuer,
        "constraints": {"rag_top_k": 5, "timeout_ms": 1500},
    }
    assert service.delegate_grant(session, **arguments) == "child"
    with pytest.raises(service.CapabilityBudgetExceeded):
        service.delegate_grant(session, **arguments)
    for call in session.execute.call_args_list:
        assert str(call.args[0]).lstrip().startswith("UPDATE")


def test_cap04_verify_checks_online_version_scope_and_revocation(rsa_keys) -> None:
    ids = _ids()
    private_pem, public_pem = rsa_keys
    token = _token(ids, private_pem)
    now = datetime.now(UTC)
    key = CapabilitySigningKey(
        kid="test-key-0001",
        algorithm=ALGORITHM,
        public_key_pem=public_pem,
        public_key_sha256=public_key_fingerprint(public_pem),
        state="active",
        not_before=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
    )
    grant = CapabilityGrant(
        id=ids["grant"],
        tenant_id=ids["tenant"],
        workspace_id=ids["workspace"],
        runtime_instance_id=ids["runtime"],
        actor_user_id=ids["user"],
        actions=["rag.search"],
        resource_ids=[ids["resource"]],
        constraints={"rag_top_k": 10, "timeout_ms": 2000},
        version=1,
        state="active",
        not_before=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=5),
        max_calls=5,
        max_bytes=1000,
        max_cost_units=5,
        delegation_depth=0,
        delegation_depth_limit=0,
        created_by_actor_type="system",
        created_by_actor_id=ids["issuer"],
    )
    session = MagicMock()
    session.execute.side_effect = [
        _result(scalar=key),
        _result(scalar=grant),
        _result(scalar=None),
        _result(scalar=ids["resource"]),
    ]
    verified = service.verify_capability(
        session,
        token=token,
        expected_tenant_id=ids["tenant"],
        expected_workspace_id=ids["workspace"],
        expected_runtime_instance_id=ids["runtime"],
        expected_workload_thumbprint="A" * 43,
        action="rag.search",
        resource_id=ids["resource"],
    )
    assert verified.grant_id == ids["grant"]
    assert verified.constraints == {"rag_top_k": 10, "timeout_ms": 2000}

    session = MagicMock()
    session.execute.side_effect = [
        _result(scalar=key),
        _result(scalar=grant),
        _result(scalar="revoked"),
    ]
    with pytest.raises(service.InvalidCapability, match="invalid capability"):
        service.verify_capability(
            session,
            token=token,
            expected_tenant_id=ids["tenant"],
            expected_workspace_id=ids["workspace"],
            expected_runtime_instance_id=ids["runtime"],
            expected_workload_thumbprint="A" * 43,
            action="rag.search",
            resource_id=ids["resource"],
        )


def test_cap04_grant_version_mismatch_is_rejected_online(rsa_keys) -> None:
    ids = _ids()
    private_pem, public_pem = rsa_keys
    token = _token(ids, private_pem, version=1)
    now = datetime.now(UTC)
    key = SimpleNamespace(
        kid="test-key-0001",
        algorithm=ALGORITHM,
        public_key_pem=public_pem,
        state="active",
        not_before=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
    )
    grant = SimpleNamespace(
        id=ids["grant"],
        tenant_id=ids["tenant"],
        workspace_id=ids["workspace"],
        runtime_instance_id=ids["runtime"],
        actor_user_id=ids["user"],
        actions=["rag.search"],
        resource_ids=[ids["resource"]],
        constraints={"timeout_ms": 2000},
        version=2,
        state="active",
        not_before=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=5),
        delegation_depth=0,
        approval_id=None,
    )
    session = MagicMock()
    session.execute.side_effect = [_result(scalar=key), _result(scalar=grant)]
    with pytest.raises(service.InvalidCapability, match="invalid capability"):
        service.verify_capability(
            session,
            token=token,
            expected_tenant_id=ids["tenant"],
            expected_workspace_id=ids["workspace"],
            expected_runtime_instance_id=ids["runtime"],
            expected_workload_thumbprint="A" * 43,
            action="rag.search",
            resource_id=ids["resource"],
        )
    assert session.execute.call_count == 2


@pytest.mark.parametrize(
    "mismatch",
    ["tenant", "workspace", "runtime", "thumbprint"],
)
def test_cap04_workload_context_mismatch_is_rejected(
    rsa_keys,
    mismatch: str,
) -> None:
    ids = _ids()
    private_pem, public_pem = rsa_keys
    token = _token(ids, private_pem)
    now = datetime.now(UTC)
    key = SimpleNamespace(
        kid="test-key-0001",
        algorithm=ALGORITHM,
        public_key_pem=public_pem,
        state="active",
        not_before=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
    )
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = key
    expected = {
        "expected_tenant_id": ids["tenant"],
        "expected_workspace_id": ids["workspace"],
        "expected_runtime_instance_id": ids["runtime"],
        "expected_workload_thumbprint": "A" * 43,
    }
    field = {
        "tenant": "expected_tenant_id",
        "workspace": "expected_workspace_id",
        "runtime": "expected_runtime_instance_id",
        "thumbprint": "expected_workload_thumbprint",
    }[mismatch]
    expected[field] = "B" * 43 if mismatch == "thumbprint" else str(uuid.uuid4())
    with pytest.raises(service.InvalidCapability, match="invalid capability"):
        service.verify_capability(
            session,
            token=token,
            action="rag.search",
            resource_id=ids["resource"],
            **expected,
        )
    assert session.execute.call_count == 1


@pytest.mark.parametrize("escalation", ["action", "resource"])
def test_cap04_action_or_resource_escalation_is_denied(rsa_keys, escalation: str) -> None:
    ids = _ids()
    private_pem, public_pem = rsa_keys
    token = _token(ids, private_pem)
    now = datetime.now(UTC)
    key = SimpleNamespace(
        algorithm=ALGORITHM,
        public_key_pem=public_pem,
        state="active",
        not_before=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
    )
    grant = SimpleNamespace(
        id=ids["grant"],
        tenant_id=ids["tenant"],
        workspace_id=ids["workspace"],
        runtime_instance_id=ids["runtime"],
        actor_user_id=ids["user"],
        actions=["rag.search"],
        resource_ids=[ids["resource"]],
        constraints={"timeout_ms": 2000},
        version=1,
        state="active",
        not_before=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=5),
        delegation_depth=0,
        approval_id=None,
    )
    session = MagicMock()
    session.execute.side_effect = [
        _result(scalar=key),
        _result(scalar=grant),
        _result(scalar=None),
    ]
    action = "data.rows.read" if escalation == "action" else "rag.search"
    resource_id = str(uuid.uuid4()) if escalation == "resource" else ids["resource"]
    with pytest.raises(service.CapabilityScopeDenied, match="scope denied"):
        service.verify_capability(
            session,
            token=token,
            expected_tenant_id=ids["tenant"],
            expected_workspace_id=ids["workspace"],
            expected_runtime_instance_id=ids["runtime"],
            expected_workload_thumbprint="A" * 43,
            action=action,
            resource_id=resource_id,
        )
    assert session.execute.call_count == 3


def test_cap05_root_budget_locks_grant_before_conditional_update() -> None:
    ids = _ids()
    _, root = _delegation_chain(ids)
    root.id = ids["grant"]
    root.delegation_depth_limit = 0
    verified = SimpleNamespace(
        tenant_id=ids["tenant"],
        grant_id=ids["grant"],
        claims=SimpleNamespace(grant_version=1, delegation_depth=0),
    )
    session = MagicMock()
    session.execute.side_effect = [_result(scalar=root), _result(scalar=None)]
    with pytest.raises(service.CapabilityBudgetExceeded):
        service.consume_budget(
            session,
            verified=verified,
            calls=1,
            bytes_in=10,
            bytes_out=20,
            cost_units=1,
        )
    assert session.execute.call_count == 2
    lock_statement = str(session.execute.call_args_list[0].args[0])
    update_statement = str(session.execute.call_args_list[1].args[0])
    assert "FOR UPDATE" in lock_statement
    assert update_statement.lstrip().startswith("UPDATE")
    assert "capability_usage.calls" in update_statement
    assert "capability_usage.bytes_in" in update_statement
    assert "capability_usage.bytes_out" in update_statement
    assert "capability_usage.cost_units" in update_statement
    assert "capability_grants.version" in update_statement


def test_issue_token_private_key_must_match_local_registry(rsa_keys) -> None:
    ids = _ids()
    private_pem, public_pem = rsa_keys
    other_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_private_pem = other_private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    now = datetime.now(UTC)
    grant = SimpleNamespace(
        id=ids["grant"],
        tenant_id=ids["tenant"],
        workspace_id=ids["workspace"],
        runtime_instance_id=ids["runtime"],
        actor_user_id=ids["user"],
        parent_grant_id=None,
        actions=["rag.search"],
        resource_ids=[ids["resource"]],
        constraints={"timeout_ms": 2000},
        version=1,
        delegation_depth=0,
        delegation_depth_limit=0,
        approval_id=None,
        state="active",
        not_before=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=5),
        max_calls=5,
        max_bytes=1000,
        max_cost_units=5,
    )
    key = SimpleNamespace(
        kid="test-key-0001",
        algorithm=ALGORITHM,
        public_key_sha256=public_key_fingerprint(public_pem),
        state="active",
        not_before=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
    )
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = key
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(service, "get_grant", lambda *args, **kwargs: grant)
    try:
        with pytest.raises(service.CapabilityConflict, match="does not match"):
            service.issue_token(
                session,
                tenant_id=ids["tenant"],
                grant_id=ids["grant"],
                kid=key.kid,
                private_key_pem=other_private_pem,
                workload_thumbprint="A" * 43,
                issuer_context=service.TrustedIssuerContext(
                    tenant_id=ids["tenant"],
                    system_actor_id=ids["issuer"],
                    originating_user_id=ids["user"],
                ),
            )
        assert private_pem != other_private_pem
    finally:
        monkeypatch.undo()


def _delegation_chain(ids: dict[str, str], *, parent_state: str = "active"):
    now = datetime.now(UTC)
    parent_id = str(uuid.uuid4())
    parent = SimpleNamespace(
        id=parent_id,
        tenant_id=ids["tenant"],
        workspace_id=ids["workspace"],
        runtime_instance_id=ids["runtime"],
        actor_user_id=ids["user"],
        parent_grant_id=None,
        actions=["rag.search"],
        resource_ids=[ids["resource"]],
        constraints={"rag_top_k": 10, "timeout_ms": 2000},
        version=1,
        state=parent_state,
        not_before=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=5),
        max_calls=10,
        max_bytes=10_000,
        max_cost_units=10,
        delegation_depth=0,
        delegation_depth_limit=2,
        approval_id=None,
    )
    child = SimpleNamespace(
        id=ids["grant"],
        tenant_id=ids["tenant"],
        workspace_id=ids["workspace"],
        runtime_instance_id=ids["runtime"],
        actor_user_id=ids["user"],
        parent_grant_id=parent_id,
        actions=["rag.search"],
        resource_ids=[ids["resource"]],
        constraints={"rag_top_k": 5, "timeout_ms": 1500},
        version=1,
        state="active",
        not_before=now,
        expires_at=now + timedelta(minutes=4),
        max_calls=5,
        max_bytes=5_000,
        max_cost_units=5,
        delegation_depth=1,
        delegation_depth_limit=2,
        approval_id=None,
    )
    return child, parent


def test_cap03_delegate_issuer_must_match_parent_originating_user() -> None:
    ids = _ids()
    _, parent = _delegation_chain(ids)
    session = MagicMock()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(service, "get_grant", lambda *args, **kwargs: parent)
    try:
        with pytest.raises(service.CapabilityScopeDenied, match="parent grant user"):
            service.delegate_grant(
                session,
                tenant_id=ids["tenant"],
                parent_grant_id=parent.id,
                runtime_instance_id=str(uuid.uuid4()),
                actions={"rag.search"},
                resource_ids={ids["resource"]},
                expires_at=parent.expires_at - timedelta(minutes=1),
                max_calls=4,
                max_bytes=4_000,
                max_cost_units=4,
                issuer_context=service.TrustedIssuerContext(
                    tenant_id=ids["tenant"],
                    system_actor_id=ids["issuer"],
                    originating_user_id=str(uuid.uuid4()),
                ),
            )
    finally:
        monkeypatch.undo()


def test_cap04_revoked_ancestor_rejects_child_verification(rsa_keys) -> None:
    ids = _ids()
    child, parent = _delegation_chain(ids, parent_state="revoked")
    private_pem, public_pem = rsa_keys
    now = datetime.now(UTC)
    token = encode_capability_token(
        private_key_pem=private_pem,
        kid="test-key-0001",
        jti=uuid.uuid4().hex,
        subject=child.runtime_instance_id,
        tenant_id=child.tenant_id,
        workspace_id=child.workspace_id,
        actor_user_id=child.actor_user_id,
        grant_id=child.id,
        grant_version=child.version,
        delegation_depth=child.delegation_depth,
        workload_thumbprint="A" * 43,
        issued_at=now,
        expires_at=now + timedelta(minutes=2),
        approval_id=None,
    )
    key = SimpleNamespace(
        algorithm=ALGORITHM,
        public_key_pem=public_pem,
        state="active",
        not_before=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
    )
    session = MagicMock()
    session.execute.side_effect = [
        _result(scalar=key),
        _result(scalar=child),
        _result(scalar=parent),
    ]
    with pytest.raises(service.InvalidCapability, match="ancestry"):
        service.verify_capability(
            session,
            token=token,
            expected_tenant_id=ids["tenant"],
            expected_workspace_id=ids["workspace"],
            expected_runtime_instance_id=ids["runtime"],
            expected_workload_thumbprint="A" * 43,
            action="rag.search",
            resource_id=ids["resource"],
        )
    assert session.execute.call_count == 3


def test_cap04_expired_ancestor_rejects_child_token_issue(rsa_keys) -> None:
    ids = _ids()
    child, parent = _delegation_chain(ids)
    parent.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    grants = iter((child, parent))
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(service, "get_grant", lambda *args, **kwargs: next(grants))
    try:
        with pytest.raises(service.CapabilityConflict, match="ancestry"):
            service.issue_token(
                MagicMock(),
                tenant_id=ids["tenant"],
                grant_id=ids["grant"],
                kid="test-key-0001",
                private_key_pem=rsa_keys[0],
                workload_thumbprint="A" * 43,
                issuer_context=service.TrustedIssuerContext(
                    tenant_id=ids["tenant"],
                    system_actor_id=ids["issuer"],
                    originating_user_id=ids["user"],
                ),
            )
    finally:
        monkeypatch.undo()


def test_cap05_revoked_ancestor_rejects_child_budget_before_update() -> None:
    ids = _ids()
    child, parent = _delegation_chain(ids, parent_state="revoked")
    claims = SimpleNamespace(grant_version=1, delegation_depth=1)
    verified = SimpleNamespace(
        tenant_id=ids["tenant"],
        grant_id=child.id,
        claims=claims,
    )
    session = MagicMock()
    session.execute.side_effect = [_result(scalar=child), _result(scalar=parent)]
    with pytest.raises(service.CapabilityBudgetExceeded, match="ancestry"):
        service.consume_budget(session, verified=verified)
    assert session.execute.call_count == 2
    assert all(
        not str(call.args[0]).lstrip().startswith("UPDATE")
        for call in session.execute.call_args_list
    )


def test_cap05_cyclic_or_overdeep_ancestry_fails_closed() -> None:
    ids = _ids()
    child, parent = _delegation_chain(ids)
    parent.parent_grant_id = child.id
    session = MagicMock()
    session.execute.side_effect = [_result(scalar=child), _result(scalar=parent)]
    with pytest.raises(service.CapabilityBudgetExceeded, match="ancestry"):
        service.consume_budget(
            session,
            verified=SimpleNamespace(
                tenant_id=ids["tenant"],
                grant_id=child.id,
                claims=SimpleNamespace(grant_version=1, delegation_depth=1),
            ),
        )


def test_internal_key_and_revocation_changes_require_typed_context(rsa_keys) -> None:
    ids = _ids()
    now = datetime.now(UTC)
    with pytest.raises(service.CapabilityScopeDenied, match="platform context"):
        service.register_signing_key(
            MagicMock(),
            platform_context=SimpleNamespace(system_actor_id=ids["issuer"]),
            kid="test-key-0001",
            public_key_pem=rsa_keys[1],
            not_before=now,
            expires_at=now + timedelta(hours=1),
        )
    with pytest.raises(service.CapabilityScopeDenied, match="issuer context"):
        service.revoke_grant(
            MagicMock(),
            tenant_id=ids["tenant"],
            grant_id=ids["grant"],
            reason_code="security.test",
            issuer_context=SimpleNamespace(
                tenant_id=ids["tenant"],
                system_actor_id=ids["issuer"],
                originating_user_id=ids["user"],
            ),
        )
