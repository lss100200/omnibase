"""Attack-focused tests for the personal single-Owner production Gate."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from omnibase.production import personal_owner_gate as gate_module
from omnibase.production.personal_owner_gate import (
    PersonalGateConfigurationError,
    PersonalGateState,
    PersonalOwnerGate,
    PersonalOwnerGateConfig,
    PersonalOwnerGateRequest,
)
from omnibase.workspaces.service import LeaseRejected

NOW = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
DIGEST = "a" * 64


def _ids() -> dict[str, str]:
    return {
        name: str(uuid.uuid4())
        for name in (
            "approval",
            "grant",
            "lease",
            "node",
            "operation",
            "owner",
            "requester",
            "resource",
            "run",
            "runtime",
            "tenant",
            "workspace",
        )
    }


def _evidence(tmp_path: Path) -> tuple[str, str]:
    path = tmp_path / "docs" / "evidence" / "personal.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "passed": True,
                "profile": "personal_single_owner",
                "root_env_accessed": False,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return "docs/evidence/personal.json", hashlib.sha256(path.read_bytes()).hexdigest()


def _config_mapping(tmp_path: Path) -> dict[str, object]:
    path, digest = _evidence(tmp_path)
    return {
        "schema_version": 1,
        "policy": {
            "profile": "personal_single_owner",
            "sandbox_mode": "workspace_network_scoped",
            "approval_policy": "owner_preapproved_exact_scope",
            "network": {
                "default_deny": True,
                "destinations": ["github.read", "model.provider"],
            },
            "external_side_effects": False,
        },
        "engineering_evidence": {
            "path": path,
            "sha256": digest,
            "assertions": {
                "passed": True,
                "profile": "personal_single_owner",
                "root_env_accessed": False,
            },
        },
        "migration_head": "0015",
        "migration_0013_created": True,
        "agent_runtime_enabled": False,
        "agent_planner_enabled": False,
        "multi_agent_enabled": False,
        "enterprise_approved_digest_present": False,
    }


def _request_mapping(ids: dict[str, str]) -> dict[str, object]:
    return {
        "tenant_id": ids["tenant"],
        "workspace_id": ids["workspace"],
        "run_id": ids["run"],
        "runtime_instance_id": ids["runtime"],
        "lease_id": ids["lease"],
        "node_id": ids["node"],
        "generation": 3,
        "run_fencing_token": 7,
        "workload_identity_digest": DIGEST,
        "approval_id": ids["approval"],
        "approval_expected_version": 2,
        "operation_id": ids["operation"],
        "requester_type": "agent",
        "requester_id": ids["requester"],
        "action": "rag.search",
        "resource_id": ids["resource"],
        "resource_version": 4,
        "request_digest": "b" * 64,
        "plan_digest": "c" * 64,
        "tool_schema_digest": "d" * 64,
        "grant_id": ids["grant"],
        "requested_calls": 1,
        "requested_bytes": 512,
        "requested_cost_units": 1,
    }


def _live_objects(
    ids: dict[str, str], config: PersonalOwnerGateConfig, request: PersonalOwnerGateRequest
) -> dict[str, object]:
    metadata = {
        "approval_policy": config.policy.approval_policy,
        "external_side_effects": config.policy.external_side_effects,
        "network_policy_sha256": config.policy.network.canonical_digest(),
        "plan_sha256": request.plan_digest,
        "profile": "personal_single_owner",
        "sandbox_mode": config.policy.sandbox_mode,
        "tool_schema_sha256": request.tool_schema_digest,
    }
    return {
        "membership": SimpleNamespace(role="owner", user_id=ids["owner"]),
        "owner": SimpleNamespace(id=ids["owner"], is_active=True, is_tenant_admin=True),
        "approval": SimpleNamespace(
            id=ids["approval"],
            version=2,
            state="approved",
            consumed_at=None,
            expires_at=NOW + timedelta(minutes=10),
            created_at=NOW - timedelta(minutes=2),
            decided_at=NOW - timedelta(minutes=1),
            requester_type="agent",
            requester_id=ids["requester"],
            workspace_id=ids["workspace"],
            run_id=ids["run"],
            resource_id=ids["resource"],
            resource_version=4,
            operation_id=ids["operation"],
            grant_id=ids["grant"],
            action="rag.search",
            request_hash="b" * 64,
            required_approver_role="tenant_admin",
            risk_level="R2",
            decided_by_actor_type="user",
            decided_by_actor_id=ids["owner"],
            approval_metadata=metadata,
        ),
        "operation": SimpleNamespace(
            id=ids["operation"],
            state="pending_approval",
            actor_type="agent",
            actor_id=ids["requester"],
            workspace_id=ids["workspace"],
            run_id=ids["run"],
            resource_id=ids["resource"],
            resource_version=4,
            request_hash="b" * 64,
            kind="rag.search",
            risk_level="R2",
            approval_id=None,
        ),
        "grant": SimpleNamespace(
            id=ids["grant"],
            state="active",
            not_before=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(minutes=5),
            revoked_at=None,
            workspace_id=ids["workspace"],
            runtime_instance_id=ids["runtime"],
            workload_identity_digest=DIGEST,
            actor_user_id=ids["owner"],
            delegation_depth=0,
            delegation_depth_limit=0,
            parent_grant_id=None,
            approval_id=None,
            actions=["rag.search"],
            resource_ids=[ids["resource"]],
            max_calls=10,
            max_bytes=10_000,
            max_cost_units=10,
        ),
        "resource": SimpleNamespace(
            id=ids["resource"], version=4, state="active", policy_class="workspace_private"
        ),
        "usage": SimpleNamespace(calls=1, bytes_in=100, bytes_out=100, cost_units=1),
    }


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


def _session(objects: dict[str, object], *, memberships: list[object] | None = None) -> MagicMock:
    session = MagicMock()
    active_memberships = memberships or [objects["membership"]]
    session.scalars.return_value = active_memberships
    results = [
        _Result(objects["approval"]),
        _Result(objects["operation"]),
        _Result(objects["grant"]),
        _Result(objects["resource"]),
        _Result(objects["usage"]),
        _Result(None),
    ]
    if len(active_memberships) == 1 and active_memberships[0].role == "owner":
        results.insert(0, _Result(objects["owner"]))
    session.execute.side_effect = results
    session.scalar.return_value = NOW
    return session


def _verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mutate=None,
    memberships: list[object] | str | None = None,
):
    ids = _ids()
    config = PersonalOwnerGateConfig.from_mapping(_config_mapping(tmp_path))
    request = PersonalOwnerGateRequest.from_mapping(_request_mapping(ids))
    objects = _live_objects(ids, config, request)
    if memberships == "second_member":
        memberships = [
            objects["membership"],
            SimpleNamespace(role="member", user_id=str(uuid.uuid4())),
        ]
    if mutate is not None:
        mutate(objects, ids, config, request)
    monkeypatch.setattr(
        gate_module,
        "verify_run_lease_for_sandbox",
        lambda *args, **kwargs: SimpleNamespace(
            workspace_id=ids["workspace"], verification_digest="e" * 64
        ),
    )
    report = PersonalOwnerGate(tmp_path).verify(
        _session(objects, memberships=memberships), config=config, request=request
    )
    return report, objects, ids


def test_exact_live_single_owner_path_is_ready_but_does_not_activate_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report, _, ids = _verify(tmp_path, monkeypatch)

    assert report.state is PersonalGateState.READY
    assert report.personal_activation_ready is True
    assert report.runtime_activated is False
    assert report.owner_user_id == ids["owner"]
    assert report.blockers == ()
    assert report.vetoes == ()
    payload = report.to_dict()
    assert payload["activation_allowed"] is False
    assert payload["enterprise_track_frozen"] is True
    assert payload["agent_runtime_enabled"] is False


@pytest.mark.parametrize(
    "destination",
    [
        "*",
        "127.0.0.1",
        "10.0.0.8",
        "localhost",
        "postgresql.primary",
        "redis.cache",
        "minio.object-store",
        "unix/socket",
        "docker.sock",
        ".env",
        "https://example.com",
        "GitHub.read",
    ],
)
def test_network_policy_rejects_non_logical_or_direct_infrastructure_destinations(
    tmp_path: Path, destination: str
) -> None:
    mapping = _config_mapping(tmp_path)
    mapping["policy"]["network"]["destinations"] = [destination]

    with pytest.raises(PersonalGateConfigurationError, match="logical service"):
        PersonalOwnerGateConfig.from_mapping(mapping)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("migration_head", "0016"),
        ("migration_0013_created", False),
        ("agent_runtime_enabled", True),
        ("agent_planner_enabled", True),
        ("multi_agent_enabled", True),
        ("enterprise_approved_digest_present", True),
    ],
)
def test_readiness_config_rejects_preactivation_or_enterprise_shortcuts(
    tmp_path: Path, field: str, value: object
) -> None:
    mapping = _config_mapping(tmp_path)
    mapping[field] = value

    with pytest.raises(PersonalGateConfigurationError):
        PersonalOwnerGateConfig.from_mapping(mapping)


def test_json_boolean_coercion_and_unknown_fields_fail_closed(tmp_path: Path) -> None:
    mapping = _config_mapping(tmp_path)
    mapping["agent_runtime_enabled"] = "false"
    with pytest.raises(PersonalGateConfigurationError, match="JSON boolean"):
        PersonalOwnerGateConfig.from_mapping(mapping)

    mapping = _config_mapping(tmp_path)
    mapping["policy"]["surprise"] = False
    with pytest.raises(PersonalGateConfigurationError, match="closed set"):
        PersonalOwnerGateConfig.from_mapping(mapping)


def test_enterprise_profile_cannot_use_personal_owner_shortcut(tmp_path: Path) -> None:
    mapping = _config_mapping(tmp_path)
    mapping["policy"]["profile"] = "enterprise_separated_authority"

    with pytest.raises(PersonalGateConfigurationError, match="personal_single_owner"):
        PersonalOwnerGateConfig.from_mapping(mapping)


def test_second_active_member_is_a_veto(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report, _, _ = _verify(tmp_path, monkeypatch, memberships="second_member")

    assert report.state is PersonalGateState.INVALID
    assert any("exactly one active Owner" in item for item in report.vetoes)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda values, *_: setattr(values["owner"], "is_active", False),
        lambda values, *_: setattr(values["owner"], "is_tenant_admin", False),
        lambda values, *_: setattr(values["approval"], "decided_by_actor_id", str(uuid.uuid4())),
        lambda values, *_: setattr(values["approval"], "request_hash", "f" * 64),
        lambda values, *_: setattr(values["approval"], "resource_version", 5),
        lambda values, *_: setattr(values["approval"], "grant_id", str(uuid.uuid4())),
        lambda values, *_: setattr(values["approval"], "approval_metadata", {}),
        lambda values, *_: setattr(values["operation"], "actor_id", str(uuid.uuid4())),
        lambda values, *_: setattr(values["operation"], "state", "queued"),
        lambda values, *_: setattr(values["grant"], "state", "revoked"),
        lambda values, *_: setattr(values["grant"], "workload_identity_digest", "f" * 64),
        lambda values, *_: setattr(values["grant"], "delegation_depth_limit", 1),
        lambda values, *_: setattr(values["grant"], "actor_user_id", str(uuid.uuid4())),
        lambda values, *_: setattr(values["resource"], "policy_class", "system_internal"),
    ],
)
def test_live_binding_attacks_are_vetoed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate
) -> None:
    report, _, _ = _verify(tmp_path, monkeypatch, mutate=mutate)

    assert report.state is PersonalGateState.INVALID
    assert report.personal_activation_ready is False
    assert report.vetoes


@pytest.mark.parametrize("state", ["pending", "rejected", "consumed", "expired"])
def test_non_consumable_owner_approval_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    def mutate(values, *_):
        values["approval"].state = state
        if state == "consumed":
            values["approval"].consumed_at = NOW

    report, _, _ = _verify(tmp_path, monkeypatch, mutate=mutate)

    assert report.state is PersonalGateState.BLOCKED
    assert any("not approved and unconsumed" in item for item in report.blockers)


def test_expired_owner_approval_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def mutate(values, *_):
        values["approval"].expires_at = NOW
        values["approval"].decided_at = NOW - timedelta(seconds=1)

    report, _, _ = _verify(tmp_path, monkeypatch, mutate=mutate)

    assert report.state is PersonalGateState.BLOCKED
    assert "Owner approval expired" in report.blockers


def test_capability_budget_exhaustion_is_a_veto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def mutate(values, *_):
        values["usage"].calls = values["grant"].max_calls

    report, _, _ = _verify(tmp_path, monkeypatch, mutate=mutate)

    assert report.state is PersonalGateState.INVALID
    assert "Capability budget is insufficient" in report.vetoes


def test_self_approval_through_owner_identity_is_rejected_at_request_parse() -> None:
    ids = _ids()
    mapping = _request_mapping(ids)
    mapping["requester_type"] = "user"
    mapping["requester_id"] = ids["owner"]

    with pytest.raises(PersonalGateConfigurationError, match="agent, run, or system"):
        PersonalOwnerGateRequest.from_mapping(mapping)


def test_stale_run_lease_is_a_veto(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gate_module,
        "verify_run_lease_for_sandbox",
        lambda *args, **kwargs: (_ for _ in ()).throw(LeaseRejected("stale lease")),
    )
    ids = _ids()
    config = PersonalOwnerGateConfig.from_mapping(_config_mapping(tmp_path))
    request = PersonalOwnerGateRequest.from_mapping(_request_mapping(ids))
    objects = _live_objects(ids, config, request)

    report = PersonalOwnerGate(tmp_path).verify(_session(objects), config=config, request=request)

    assert report.state is PersonalGateState.INVALID
    assert any("RunLease" in item for item in report.vetoes)


def test_evidence_byte_drift_is_a_veto(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ids = _ids()
    mapping = _config_mapping(tmp_path)
    config = PersonalOwnerGateConfig.from_mapping(mapping)
    request = PersonalOwnerGateRequest.from_mapping(_request_mapping(ids))
    objects = _live_objects(ids, config, request)
    (tmp_path / config.evidence.path).write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        gate_module,
        "verify_run_lease_for_sandbox",
        lambda *args, **kwargs: SimpleNamespace(
            workspace_id=ids["workspace"], verification_digest="e" * 64
        ),
    )

    report = PersonalOwnerGate(tmp_path).verify(_session(objects), config=config, request=request)

    assert report.state is PersonalGateState.INVALID
    assert any("sealed evidence SHA-256 drifted" in item for item in report.vetoes)


def test_observe_mode_cannot_authorize_mutation(tmp_path: Path) -> None:
    mapping = _config_mapping(tmp_path)
    mapping["policy"]["sandbox_mode"] = "observe"
    mapping["policy"]["network"]["destinations"] = []
    config = PersonalOwnerGateConfig.from_mapping(mapping)
    ids = _ids()
    request_mapping = _request_mapping(ids)
    request_mapping["action"] = "sandbox.exec"
    request = PersonalOwnerGateRequest.from_mapping(request_mapping)
    assert config.policy.sandbox_mode == "observe"
    assert request.action == "sandbox.exec"
