"""P5.1B Agent Registry persistence service unit tests (no database)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from omnibase.agent_registry.service import (
    _RISK_TO_R_LEVEL,
    RegistryConflictError,
    RegistryNotFoundError,
    RegistryPersistenceService,
    RegistryStateError,
    _canonical_request_hash,
)
from omnibase.production.phase5_registry_contract import (
    AgentDefinition,
    AgentVersionManifest,
    BudgetCeilings,
    WorkspaceAgentBinding,
)

DEFINITION_ID = "00000000-0000-0000-0000-000000000001"
TENANT_ID = "00000000-0000-0000-0000-00000000000a"
VERSION_ID = "11111111-1111-1111-1111-111111111111"
MODEL_POLICY_ID = "22222222-2222-2222-2222-222222222222"
MEMORY_POLICY_ID = "44444444-4444-4444-4444-444444444444"
BINDING_ID = "55555555-5555-5555-5555-555555555555"
WORKSPACE_ID = "66666666-6666-6666-6666-666666666666"
ACTOR_ID = "00000000-0000-0000-0000-0000000000aa"
INSTRUCTIONS_DIGEST = "3333333333333333333333333333333333333333333333333333333333333333"


def _definition() -> AgentDefinition:
    return AgentDefinition.from_mapping(
        {
            "schema_version": 1,
            "agent_definition_id": DEFINITION_ID,
            "tenant_id": TENANT_ID,
            "stable_logical_key": "repository-inspector",
            "display_name": "Repository Inspector",
            "description": "Read-only repository inspection agent",
            "risk_level": "low",
            "allowed_installation_scopes": ["workspace"],
            "definition_state": "active",
            "created_by": ACTOR_ID,
            "created_at": "2026-08-03T00:00:00Z",
            "metadata_version": 1,
        }
    )


def _version() -> AgentVersionManifest:
    mapping: dict[str, object] = {
        "schema_version": 1,
        "agent_version_id": VERSION_ID,
        "agent_definition_id": DEFINITION_ID,
        "tenant_id": TENANT_ID,
        "version": "1.0.0",
        "manifest_digest": "0" * 64,
        "model_policy_id": MODEL_POLICY_ID,
        "instructions_digest": INSTRUCTIONS_DIGEST,
        "max_context_tokens": 200000,
        "allowed_tool_ids": ["rag_search", "artifact_read"],
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 1}},
        },
        "output_schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
        "risk_level": "low",
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
    payload = {k: v for k, v in mapping.items() if k != "manifest_digest"}
    import hashlib
    import json

    mapping["manifest_digest"] = hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return AgentVersionManifest.from_mapping(
        mapping,
        ceilings=BudgetCeilings.from_mapping(
            {
                "max_tokens": 10000000,
                "max_cost_units": 100000,
                "max_wall_clock_seconds": 3600,
                "max_tool_calls": 1000,
                "max_concurrency": 64,
                "max_context_tokens": 2000000,
            }
        ).as_mapping(),
    )


def _binding() -> WorkspaceAgentBinding:
    return WorkspaceAgentBinding.from_mapping(
        {
            "schema_version": 1,
            "workspace_agent_binding_id": BINDING_ID,
            "tenant_id": TENANT_ID,
            "workspace_id": WORKSPACE_ID,
            "workspace_generation": 1,
            "agent_definition_id": DEFINITION_ID,
            "agent_version_id": VERSION_ID,
            "agent_version_digest": _version().manifest_digest,
            "installation_state": "installed",
            "resource_scopes": ["workspace_private_read"],
            "default_budget_policy": {
                "max_tokens": 50000,
                "max_cost_units": 500,
                "max_wall_clock_seconds": 300,
                "max_tool_calls": 50,
            },
            "installed_by": ACTOR_ID,
            "approval_id": None,
            "created_at": "2026-08-03T00:00:00Z",
            "disabled_at": None,
            "superseded_by": None,
        },
        ceilings=BudgetCeilings.from_mapping(
            {
                "max_tokens": 10000000,
                "max_cost_units": 100000,
                "max_wall_clock_seconds": 3600,
                "max_tool_calls": 1000,
                "max_concurrency": 64,
                "max_context_tokens": 2000000,
            }
        ).as_mapping(),
    )


def _session(*, tenant_exists: bool = True) -> MagicMock:
    session = MagicMock()
    tenant_row = None if not tenant_exists else SimpleNamespace(id=TENANT_ID, is_active=True)
    result = MagicMock()
    result.scalar_one_or_none.return_value = tenant_row
    session.execute.return_value = result
    return session


def test_risk_to_r_level_mapping_is_closed() -> None:
    assert _RISK_TO_R_LEVEL == {"low": "R1", "medium": "R2", "high": "R3", "critical": "R4"}


def test_canonical_request_hash_is_stable_and_order_independent() -> None:
    first = _canonical_request_hash({"a": 1, "b": {"x": [1, 2]}})
    second = _canonical_request_hash({"b": {"x": [1, 2]}, "a": 1})
    assert first == second
    assert len(first) == 64


def test_register_definition_maps_dto_to_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    inserted_record = SimpleNamespace(id="r1")
    session.execute.return_value.scalar_one_or_none.side_effect = [
        SimpleNamespace(id=TENANT_ID, is_active=True),
        SimpleNamespace(id=ACTOR_ID, is_active=True),
        None,
        SimpleNamespace(id=ACTOR_ID, is_active=True),
    ]
    monkeypatch.setattr(
        "omnibase.agent_registry.service.reserve_idempotency",
        lambda *a, **kw: (inserted_record, True),
    )
    monkeypatch.setattr(
        "omnibase.agent_registry.service.complete_idempotency",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "omnibase.agent_registry.service.append_audit_event",
        lambda *a, **kw: None,
    )
    service = RegistryPersistenceService(session)

    model = service.register_definition(
        tenant_id=TENANT_ID,
        actor_user_id=ACTOR_ID,
        request_id="p51b-unit-test",
        definition=_definition(),
        idempotency_key="key-1",
    )

    assert model.id == DEFINITION_ID
    assert model.tenant_id == TENANT_ID
    assert model.stable_logical_key == "repository-inspector"
    assert model.risk_level == "low"
    assert model.definition_state == "active"
    assert model.installation_scopes == ["workspace"]
    assert model.metadata_version == 1


def test_register_definition_rejects_missing_tenant() -> None:
    session = _session(tenant_exists=False)
    service = RegistryPersistenceService(session)
    with pytest.raises(RegistryNotFoundError, match="registry_tenant_not_found"):
        service.register_definition(
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            request_id="p51b-unit-test",
            definition=_definition(),
            idempotency_key="key-1",
        )


def test_register_definition_rejects_dto_tenant_drift_before_database_access() -> None:
    session = _session()
    definition = AgentDefinition.from_mapping(
        {**_definition().to_dict(), "tenant_id": "99999999-9999-9999-9999-999999999999"}
    )
    with pytest.raises(RegistryStateError, match="registry_tenant_mismatch"):
        RegistryPersistenceService(session).register_definition(
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            request_id="p51b-unit-test",
            definition=definition,
            idempotency_key="key-1",
        )
    session.execute.assert_not_called()


def test_register_definition_rejects_dto_actor_drift_before_database_access() -> None:
    session = _session()
    definition = AgentDefinition.from_mapping(
        {**_definition().to_dict(), "created_by": "99999999-9999-9999-9999-999999999999"}
    )
    with pytest.raises(RegistryStateError, match="registry_actor_mismatch"):
        RegistryPersistenceService(session).register_definition(
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            request_id="p51b-unit-test",
            definition=definition,
            idempotency_key="key-1",
        )
    session.execute.assert_not_called()


def test_register_definition_rejects_inactive_actor() -> None:
    session = _session()
    session.execute.return_value.scalar_one_or_none.side_effect = [
        SimpleNamespace(id=TENANT_ID, is_active=True),
        None,
    ]
    with pytest.raises(RegistryStateError, match="registry_actor_inactive_or_missing"):
        RegistryPersistenceService(session).register_definition(
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            request_id="p51b-unit-test",
            definition=_definition(),
            idempotency_key="key-1",
        )


def test_register_definition_converts_integrity_error_to_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    session.execute.return_value.scalar_one_or_none.side_effect = [
        SimpleNamespace(id=TENANT_ID, is_active=True),
        SimpleNamespace(id=ACTOR_ID, is_active=True),
        None,
    ]
    monkeypatch.setattr(
        "omnibase.agent_registry.service.reserve_idempotency",
        lambda *a, **kw: (SimpleNamespace(id="r1"), True),
    )
    monkeypatch.setattr(
        "omnibase.agent_registry.service.complete_idempotency",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "omnibase.agent_registry.service.append_audit_event",
        lambda *a, **kw: None,
    )
    session.flush.side_effect = IntegrityError("stmt", {}, Exception("duplicate"))
    service = RegistryPersistenceService(session)

    with pytest.raises(RegistryConflictError, match="registry_definition_conflict"):
        service.register_definition(
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            request_id="p51b-unit-test",
            definition=_definition(),
            idempotency_key="key-1",
        )


def test_register_definition_exact_replay_returns_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    existing = SimpleNamespace(id=DEFINITION_ID, tenant_id=TENANT_ID, risk_level="low")
    replay_result = MagicMock()
    replay_result.scalar_one_or_none.side_effect = [
        SimpleNamespace(id=TENANT_ID, is_active=True),
        SimpleNamespace(id=ACTOR_ID, is_active=True),
        existing,
    ]
    session.execute.return_value = replay_result
    monkeypatch.setattr(
        "omnibase.agent_registry.service.reserve_idempotency",
        lambda *a, **kw: (
            SimpleNamespace(id="r1", response_ref={"agent_definition_id": DEFINITION_ID}),
            False,
        ),
    )
    monkeypatch.setattr(
        "omnibase.agent_registry.service.append_audit_event",
        lambda *a, **kw: None,
    )
    service = RegistryPersistenceService(session)

    model = service.register_definition(
        tenant_id=TENANT_ID,
        actor_user_id=ACTOR_ID,
        request_id="p51b-unit-test",
        definition=_definition(),
        idempotency_key="key-1",
    )

    assert cast(object, model) is existing
    session.add.assert_not_called()


def test_seal_version_requires_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.side_effect = [
        SimpleNamespace(id=TENANT_ID, is_active=True),
        SimpleNamespace(id=ACTOR_ID, is_active=True),
        None,
    ]
    session.execute.return_value = execute_result
    service = RegistryPersistenceService(session)
    with pytest.raises(RegistryNotFoundError, match="registry_definition_not_found"):
        service.seal_version(
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            request_id="p51b-unit-test",
            version=_version(),
            idempotency_key="key-1",
        )


def test_seal_version_maps_manifest_and_seals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    definition_row = SimpleNamespace(id=DEFINITION_ID, definition_state="active")
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.side_effect = [
        SimpleNamespace(id=TENANT_ID, is_active=True),
        SimpleNamespace(id=ACTOR_ID, is_active=True),
        definition_row,
        None,
        SimpleNamespace(id=ACTOR_ID, is_active=True),
    ]
    session.execute.return_value = execute_result
    monkeypatch.setattr(
        "omnibase.agent_registry.service.reserve_idempotency",
        lambda *a, **kw: (SimpleNamespace(id="r1"), True),
    )
    monkeypatch.setattr(
        "omnibase.agent_registry.service.complete_idempotency",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "omnibase.agent_registry.service.append_audit_event",
        lambda *a, **kw: None,
    )
    service = RegistryPersistenceService(session)

    model = service.seal_version(
        tenant_id=TENANT_ID,
        actor_user_id=ACTOR_ID,
        request_id="p51b-unit-test",
        version=_version(),
        idempotency_key="key-1",
    )

    assert model.id == VERSION_ID
    assert model.version_state == "sealed"
    assert model.manifest_digest == _version().manifest_digest
    assert model.manifest_payload["agent_version_id"] == VERSION_ID
    assert model.risk_level == "low"


def test_install_binding_rejects_stale_workspace_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    workspace_row = SimpleNamespace(id=WORKSPACE_ID, generation=2)
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.side_effect = [
        SimpleNamespace(id=TENANT_ID, is_active=True),
        SimpleNamespace(id=ACTOR_ID, is_active=True),
        workspace_row,
    ]
    session.execute.return_value = execute_result
    service = RegistryPersistenceService(session)
    with pytest.raises(RegistryStateError, match="registry_workspace_generation_stale"):
        service.install_binding(
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            request_id="p51b-unit-test",
            binding=_binding(),
            idempotency_key="key-1",
        )


def test_install_binding_rejects_version_digest_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    workspace_row = SimpleNamespace(id=WORKSPACE_ID, generation=1)
    definition_row = SimpleNamespace(
        id=DEFINITION_ID, definition_state="active", installation_scopes=["workspace"]
    )
    version_row = SimpleNamespace(
        id=VERSION_ID,
        version_state="sealed",
        definition_id=DEFINITION_ID,
        manifest_digest="0" * 64,
        risk_level="low",
    )
    live_row = None
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.side_effect = [
        SimpleNamespace(id=TENANT_ID, is_active=True),
        SimpleNamespace(id=ACTOR_ID, is_active=True),
        workspace_row,
        definition_row,
        version_row,
        live_row,
    ]
    session.execute.return_value = execute_result
    service = RegistryPersistenceService(session)
    with pytest.raises(RegistryStateError, match="registry_version_digest_mismatch"):
        service.install_binding(
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            request_id="p51b-unit-test",
            binding=_binding(),
            idempotency_key="key-1",
        )


def test_install_binding_requires_approval_for_high_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    workspace_row = SimpleNamespace(id=WORKSPACE_ID, generation=1)
    definition_row = SimpleNamespace(
        id=DEFINITION_ID, definition_state="active", installation_scopes=["workspace"]
    )
    version_row = SimpleNamespace(
        id=VERSION_ID,
        version_state="sealed",
        definition_id=DEFINITION_ID,
        manifest_digest=_version().manifest_digest,
        risk_level="high",
    )
    live_row = None
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.side_effect = [
        SimpleNamespace(id=TENANT_ID, is_active=True),
        SimpleNamespace(id=ACTOR_ID, is_active=True),
        workspace_row,
        definition_row,
        version_row,
        live_row,
    ]
    session.execute.return_value = execute_result
    monkeypatch.setattr(
        "omnibase.agent_registry.service.reserve_idempotency",
        lambda *a, **kw: (SimpleNamespace(id="r1"), True),
    )
    service = RegistryPersistenceService(session)
    binding = _binding()
    binding = binding.__class__.from_mapping(
        {**binding.to_dict(), "approval_id": None},
        ceilings=BudgetCeilings.from_mapping(
            {
                "max_tokens": 10000000,
                "max_cost_units": 100000,
                "max_wall_clock_seconds": 3600,
                "max_tool_calls": 1000,
                "max_concurrency": 64,
                "max_context_tokens": 2000000,
            }
        ).as_mapping(),
    )
    with pytest.raises(RegistryStateError, match="registry_approval_required"):
        service.install_binding(
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            request_id="p51b-unit-test",
            binding=binding,
            idempotency_key="key-1",
        )


def test_install_binding_converts_integrity_error_to_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    workspace_row = SimpleNamespace(id=WORKSPACE_ID, generation=1)
    definition_row = SimpleNamespace(
        id=DEFINITION_ID, definition_state="active", installation_scopes=["workspace"]
    )
    version_row = SimpleNamespace(
        id=VERSION_ID,
        version_state="sealed",
        definition_id=DEFINITION_ID,
        manifest_digest=_version().manifest_digest,
        risk_level="low",
    )
    live_row = None
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.side_effect = [
        SimpleNamespace(id=TENANT_ID, is_active=True),
        SimpleNamespace(id=ACTOR_ID, is_active=True),
        workspace_row,
        definition_row,
        version_row,
        live_row,
    ]
    session.execute.return_value = execute_result
    monkeypatch.setattr(
        "omnibase.agent_registry.service.reserve_idempotency",
        lambda *a, **kw: (SimpleNamespace(id="r1"), True),
    )
    monkeypatch.setattr(
        "omnibase.agent_registry.service.complete_idempotency",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "omnibase.agent_registry.service.append_audit_event",
        lambda *a, **kw: None,
    )
    session.flush.side_effect = IntegrityError("stmt", {}, Exception("duplicate"))
    service = RegistryPersistenceService(session)
    with pytest.raises(RegistryConflictError, match="registry_binding_conflict"):
        service.install_binding(
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            request_id="p51b-unit-test",
            binding=_binding(),
            idempotency_key="key-1",
        )


def test_revoke_definition_missing_row_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.side_effect = [
        SimpleNamespace(id=TENANT_ID, is_active=True),
        SimpleNamespace(id=ACTOR_ID, is_active=True),
        None,
    ]
    session.execute.return_value = execute_result
    monkeypatch.setattr(
        "omnibase.agent_registry.service.reserve_idempotency",
        lambda *a, **kw: (SimpleNamespace(id="r1"), True),
    )
    service = RegistryPersistenceService(session)
    with pytest.raises(RegistryNotFoundError, match="registry_definition_not_found"):
        service.revoke_definition(
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            request_id="p51b-unit-test",
            definition_id=DEFINITION_ID,
            idempotency_key="key-1",
        )


def test_transition_binding_requires_existing_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.side_effect = [
        SimpleNamespace(id=TENANT_ID, is_active=True),
        SimpleNamespace(id=ACTOR_ID, is_active=True),
        None,
    ]
    session.execute.return_value = execute_result
    monkeypatch.setattr(
        "omnibase.agent_registry.service.reserve_idempotency",
        lambda *a, **kw: (SimpleNamespace(id="r1"), True),
    )
    service = RegistryPersistenceService(session)
    with pytest.raises(RegistryNotFoundError, match="registry_binding_not_found"):
        service.disable_binding(
            tenant_id=TENANT_ID,
            actor_user_id=ACTOR_ID,
            request_id="p51b-unit-test",
            binding_id=BINDING_ID,
            idempotency_key="key-1",
        )
