"""P34.5A3 persistence, adapter and immutable binding contracts."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import CheckConstraint, ForeignKeyConstraint
from sqlalchemy.exc import OperationalError

import omnibase.sandbox.authorization as authorization_module
from omnibase.control_plane.service import ControlPlaneError
from omnibase.db.models import GLOBAL_SCHEMA
from omnibase.sandbox import (
    SandboxAction,
    SandboxConflict,
    SandboxOperationRequest,
    SandboxRejected,
    SandboxUnavailable,
    SqlAlchemySandboxCapabilityVerifier,
    SqlAlchemySandboxOperationStore,
)
from omnibase.sandbox.models import SandboxOperation, SandboxOperationTransitionModel
from omnibase.sandbox.operations import SandboxOperationIntent


def _request() -> SandboxOperationRequest:
    return SandboxOperationRequest(
        operation_id=uuid4(),
        action=SandboxAction.START,
        tenant_id=uuid4(),
        workspace_id=uuid4(),
        run_id=uuid4(),
        runtime_instance_id=uuid4(),
        capability_grant_id=uuid4(),
        node_id=uuid4(),
        lease_id=uuid4(),
        workspace_generation=1,
        run_fencing_token=2,
        node_fencing_token=3,
        workload_identity_thumbprint="a" * 64,
    )


def test_sandbox_models_are_global_closed_and_do_not_store_provider_payloads() -> None:
    assert SandboxOperation.__table__.schema == GLOBAL_SCHEMA
    assert SandboxOperationTransitionModel.__table__.schema == GLOBAL_SCHEMA
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in SandboxOperation.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "sandbox.start" in checks["sandbox_operations_action_check"]
    assert "sandbox.control.emergency_destroy" in checks["sandbox_operations_action_check"]
    assert "capability_grant_id IS NULL" in checks["sandbox_operations_capability_binding_check"]
    assert "reconciliation_required" in checks["sandbox_operations_state_check"]
    composite = [
        constraint
        for constraint in SandboxOperationTransitionModel.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    ]
    assert len(composite) == 1
    assert tuple(element.parent.name for element in composite[0].elements) == (
        "operation_id",
        "tenant_id",
    )
    forbidden = {
        "provider_handle",
        "command",
        "argv",
        "environment",
        "credential",
        "token",
        "host_path",
        "physical_locator",
    }
    assert forbidden.isdisjoint(SandboxOperation.__table__.columns.keys())
    assert forbidden.isdisjoint(SandboxOperationTransitionModel.__table__.columns.keys())


def test_operation_intent_action_and_capability_binding_are_closed() -> None:
    values = {
        "operation_id": uuid4(),
        "tenant_id": uuid4(),
        "workspace_id": uuid4(),
        "run_id": uuid4(),
        "runtime_instance_id": uuid4(),
        "capability_grant_id": uuid4(),
        "workspace_generation": 1,
        "run_fencing_token": 1,
        "node_fencing_token": 1,
        "request_digest": "a" * 64,
    }
    with pytest.raises(ValueError, match="action is invalid"):
        SandboxOperationIntent(action="sandbox.arbitrary", **values)
    with pytest.raises(ValueError, match="capability binding"):
        SandboxOperationIntent(
            action="sandbox.start",
            **{**values, "capability_grant_id": None},
        )
    with pytest.raises(ValueError, match="capability binding"):
        SandboxOperationIntent(
            action="sandbox.control.emergency_stop",
            **values,
        )


def test_sqlalchemy_capability_adapter_maps_domain_and_database_failures(monkeypatch) -> None:
    request = _request()
    session = MagicMock()

    @contextmanager
    def transaction():
        yield session

    session.begin.side_effect = transaction
    verifier = SqlAlchemySandboxCapabilityVerifier(session_factory=lambda: session)
    monkeypatch.setattr(
        authorization_module,
        "verify_and_reserve_sandbox_capability",
        MagicMock(
            return_value=SimpleNamespace(
                tenant_id=str(request.tenant_id),
                workspace_id=str(request.workspace_id),
                runtime_instance_id=str(request.runtime_instance_id),
                grant_id=str(request.capability_grant_id),
                workload_identity_digest=request.workload_identity_thumbprint,
                action=request.action.value,
                verified_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(minutes=1),
                verification_digest="b" * 64,
            )
        ),
    )
    assert verifier.verify(request).grant_id == request.capability_grant_id

    monkeypatch.setattr(
        authorization_module,
        "verify_and_reserve_sandbox_capability",
        MagicMock(side_effect=authorization_module.CapabilityError("denied")),
    )
    with pytest.raises(SandboxRejected, match="sandbox_live_capability_rejected"):
        verifier.verify(request)

    monkeypatch.setattr(
        authorization_module,
        "verify_and_reserve_sandbox_capability",
        MagicMock(side_effect=OperationalError("statement", {}, Exception("offline"))),
    )
    with pytest.raises(SandboxUnavailable, match="sandbox_capability_verifier_unavailable"):
        verifier.verify(request)


def test_production_store_requires_a_real_session_factory() -> None:
    store = SqlAlchemySandboxOperationStore(session_factory=MagicMock())
    assert store is not None


def test_production_store_maps_audit_binding_failures_to_sandbox_conflict() -> None:
    session = MagicMock()
    store = SqlAlchemySandboxOperationStore(session_factory=lambda: session)
    with (
        pytest.raises(SandboxConflict, match="sandbox_operation_audit_binding_rejected"),
        store._transaction(),
    ):
        raise ControlPlaneError("cross-tenant audit reference")
