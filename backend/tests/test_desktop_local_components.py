from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from omnibase.desktop_local import (
    DesktopLocalConfig,
    create_owner,
    create_workspace,
    initialized_database,
)
from omnibase.desktop_local.components import service as component_service
from omnibase.desktop_local.components.catalog import (
    SEEDED_BY_ID_VERSION,
    digest_json,
    validate_closed_manifest,
)
from omnibase.desktop_local.components.service import (
    apply_component_action_v2,
    attest_component_package,
    begin_component_invocation,
    create_assistant_component_proposal,
    create_component_proposal,
    decide_component_proposal,
    emergency_stop_components,
    get_component_snapshot,
    reconcile_component_effect,
    recover_component_kernel,
    register_owner_reviewed_component,
    settle_component_invocation,
    settle_component_recovery,
)
from omnibase.desktop_local.conversations import create_conversation
from omnibase.desktop_local.providers import DesktopApiError

WORKSPACE_ID = "workspace_00000000000000000000000000000001"
OWNER_ID = "owner-local"
COMPONENT_ID = "builtin.workspace-canvas"
VERSION = "1.0.0"


@pytest.fixture
def component_database(tmp_path: Path) -> sqlite3.Connection:
    config = DesktopLocalConfig(data_root=tmp_path / "data", application_version="1.0.0")
    with initialized_database(config) as connection:
        create_owner(connection, OWNER_ID, "Owner")
        create_workspace(connection, WORKSPACE_ID, OWNER_ID, "Workspace")
        yield connection


def _grant() -> dict[str, object]:
    return {
        "action": "ui.render",
        "logical_resource_id": "workspace.component.input",
        "resource_version": 1,
        "logical_service_id": None,
        "expires_in_seconds": 3_600,
        "maximum_invocations": 8,
        "maximum_bytes_in": 4_096,
        "maximum_bytes_out": 8_192,
        "maximum_tokens": 0,
        "maximum_wall_time_ms": 30_000,
        "maximum_cost_units": 8,
    }


def _attest(connection: sqlite3.Connection, version: str = VERSION) -> tuple[str, str]:
    policy = SEEDED_BY_ID_VERSION[(COMPONENT_ID, version)]
    manifest_sha = digest_json({"bundle": COMPONENT_ID, "version": version})
    package_sha = digest_json({"files": ["app.asar"], "version": version})
    attest_component_package(
        connection,
        component_id=COMPONENT_ID,
        version=version,
        policy_manifest_sha256=policy.manifest_sha256,
        manifest_sha256=manifest_sha,
        package_sha256=package_sha,
        inventory_sha256=digest_json({"inventory": package_sha}),
        adapter_id=policy.adapter_id,
    )
    return manifest_sha, package_sha


def _manifest_dependency(component_id: str, version: str = "1.0.0") -> dict[str, object]:
    policy = SEEDED_BY_ID_VERSION[(component_id, version)]
    return {
        "component_id": component_id,
        "version": version,
        "policy_manifest_sha256": policy.manifest_sha256,
        "manifest_sha256": digest_json({"manifest": component_id, "version": version}),
        "package_sha256": digest_json({"package": component_id, "version": version}),
    }


def _register_owner_conflicting_component(
    connection: sqlite3.Connection,
    *,
    workspace_id: str = WORKSPACE_ID,
) -> str:
    component_id = "owner.conflicting-canvas"
    manifest = deepcopy(SEEDED_BY_ID_VERSION[(COMPONENT_ID, VERSION)].manifest)
    manifest["component_id"] = component_id
    manifest["publisher"] = {"classification": "owner_reviewed", "id": "owner.local"}
    manifest["conflicts"] = [COMPONENT_ID]
    manifest_sha = digest_json(manifest)
    register_owner_reviewed_component(
        connection,
        workspace_id=workspace_id,
        manifest=manifest,
        manifest_sha256=manifest_sha,
        package_sha256=digest_json({"declarative": manifest_sha}),
        inventory_sha256=digest_json({"manifest.json": manifest_sha}),
    )
    return component_id


def _insert_assistant_message(
    connection: sqlite3.Connection,
    *,
    message_id: str,
    content: str,
) -> None:
    conversation_result = create_conversation(connection, WORKSPACE_ID, "Component proposal")
    conversation = conversation_result["conversation"]
    assert isinstance(conversation, dict)
    conversation_id = str(conversation["id"])
    timestamp = str(conversation["created_at"])
    invocation_id = "invocation_" + message_id.removeprefix("message_")
    connection.execute(
        "INSERT INTO invocation "
        "(id, owner_id, workspace_id, conversation_id, provider_id, requested_model, "
        "actual_model, family, gear, thinking_depth, status, duration_ms, input_tokens, "
        "output_tokens, total_tokens, error_code, error_redacted, retry_of_invocation_id, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'model', 'model', "
        "'generic-openai-compatible', 'standard', 'disabled', 'succeeded', 1, 1, 1, 2, "
        "NULL, NULL, NULL, ?, ?)",
        (
            invocation_id,
            OWNER_ID,
            WORKSPACE_ID,
            conversation_id,
            "provider_" + "a" * 32,
            timestamp,
            timestamp,
        ),
    )
    connection.execute(
        "INSERT INTO message "
        "(id, owner_id, workspace_id, conversation_id, role, content, status, "
        "invocation_id, retry_of_message_id, created_at) "
        "VALUES (?, ?, ?, ?, 'assistant', ?, 'completed', ?, NULL, ?)",
        (
            message_id,
            OWNER_ID,
            WORKSPACE_ID,
            conversation_id,
            content,
            invocation_id,
            timestamp,
        ),
    )
    connection.commit()


def _proposal(
    connection: sqlite3.Connection,
    *,
    change_kind: str,
    expected_revision: int,
    manifest_sha: str,
    package_sha: str,
    target_version: str = VERSION,
    workspace_id: str = WORKSPACE_ID,
) -> dict[str, object]:
    result = create_component_proposal(
        connection,
        workspace_id=workspace_id,
        component_id=COMPONENT_ID,
        target_version=target_version,
        change_kind=change_kind,
        expected_revision=expected_revision,
        requested_grants=[_grant()],
        desired_configuration={},
        desired_slot_bindings=[
            {
                "slot_id": "editor.component",
                "binding_key": "workspace.canvas",
                "order_index": 10,
                "configuration": {},
            }
        ],
        dependency_graph=[],
        source_kind="owner",
        source_reference=None,
        idempotency_key=f"proposal:{change_kind}:{target_version}:{expected_revision}",
    )
    proposal = result["proposal"]
    assert isinstance(proposal, dict)
    assert proposal["manifest_sha256"] == manifest_sha
    assert proposal["package_sha256"] == package_sha
    decide_component_proposal(
        connection,
        workspace_id=workspace_id,
        proposal_id=str(proposal["proposal_id"]),
        decision="approve",
        request_sha256=str(proposal["request_sha256"]),
    )
    return proposal


def _lifecycle(
    connection: sqlite3.Connection,
    *,
    action: str,
    expected_revision: int,
    proposal: dict[str, object],
    workspace_id: str = WORKSPACE_ID,
) -> dict[str, object]:
    common = {
        "workspace_id": workspace_id,
        "component_id": COMPONENT_ID,
        "action": action,
        "proposal_id": str(proposal["proposal_id"]),
        "request_sha256": str(proposal["request_sha256"]),
        "expected_revision": expected_revision,
        "manifest_sha256": str(proposal["manifest_sha256"]),
        "package_sha256": str(proposal["package_sha256"]),
        "idempotency_key": f"action:{action}:{expected_revision}",
    }
    prepared = apply_component_action_v2(
        connection,
        **common,
        phase="prepare",
        operation_id=None,
        outcome=None,
        evidence_sha256=None,
        health_state=None,
    )
    ticket = prepared["lifecycle_ticket"]
    assert isinstance(ticket, dict)
    return apply_component_action_v2(
        connection,
        **common,
        phase="settle",
        operation_id=str(ticket["operation_id"]),
        outcome="succeeded",
        evidence_sha256=digest_json({"action": action, "native": "ok"}),
        health_state="healthy" if action == "activate" else None,
        runtime_instance_id=ticket["runtime_instance_id"],
        workload_identity_digest=ticket["workload_identity_digest"],
    )


def _activate_default_component(
    connection: sqlite3.Connection,
) -> tuple[str, str, int]:
    manifest_sha, package_sha = _attest(connection)
    revision = 0
    for action in ("install", "bind", "activate"):
        proposal = _proposal(
            connection,
            change_kind=action,
            expected_revision=revision,
            manifest_sha=manifest_sha,
            package_sha=package_sha,
        )
        revision = int(
            _lifecycle(
                connection,
                action=action,
                expected_revision=revision,
                proposal=proposal,
            )["installation"]["revision"]
        )
    return manifest_sha, package_sha, revision


def _prepare_emergency(
    connection: sqlite3.Connection,
    *,
    idempotency_key: str = "emergency:owner:1",
    reason_code: str = "owner_emergency_stop",
) -> dict[str, object]:
    result = emergency_stop_components(
        connection,
        workspace_id=WORKSPACE_ID,
        phase="prepare",
        idempotency_key=idempotency_key,
        reason_code=reason_code,
    )
    tickets = result["tickets"]
    assert isinstance(tickets, list)
    assert len(tickets) == 1
    ticket = tickets[0]
    assert isinstance(ticket, dict)
    return ticket


def _begin_default_invocation(
    connection: sqlite3.Connection,
    *,
    manifest_sha: str,
    package_sha: str,
    revision: int,
    idempotency_key: str = "matrix:invoke:1",
    workspace_id: str = WORKSPACE_ID,
    binding_generation: int = 1,
    bytes_in: int = 1,
    bytes_out_reserved: int = 1,
    tokens_reserved: int = 0,
    wall_time_ms: int = 1,
    cost_units: int = 1,
) -> dict[str, object]:
    return begin_component_invocation(
        connection,
        workspace_id=workspace_id,
        component_id=COMPONENT_ID,
        action="ui.render",
        expected_revision=revision,
        binding_generation=binding_generation,
        manifest_sha256=manifest_sha,
        package_sha256=package_sha,
        idempotency_key=idempotency_key,
        arguments_sha256=digest_json({"idempotency_key": idempotency_key}),
        logical_resource_id="workspace.component.input",
        resource_version=1,
        logical_service_id=None,
        bytes_in=bytes_in,
        bytes_out_reserved=bytes_out_reserved,
        tokens_reserved=tokens_reserved,
        wall_time_ms=wall_time_ms,
        cost_units=cost_units,
    )


def _activate_readonly_mcp(
    connection: sqlite3.Connection,
) -> tuple[str, str, int]:
    component_id = "builtin.readonly-mcp"
    version = "1.0.0"
    policy = SEEDED_BY_ID_VERSION[(component_id, version)]
    manifest_sha = digest_json({"bundle": component_id, "version": version})
    package_sha = digest_json({"files": ["mcp.json"], "version": version})
    attest_component_package(
        connection,
        component_id=component_id,
        version=version,
        policy_manifest_sha256=policy.manifest_sha256,
        manifest_sha256=manifest_sha,
        package_sha256=package_sha,
        inventory_sha256=digest_json({"inventory": package_sha}),
        adapter_id=policy.adapter_id,
    )
    grant = _grant()
    grant["action"] = "mcp.call"
    grant["logical_service_id"] = "reviewed_https"
    revision = 0
    for action in ("install", "bind", "activate"):
        proposed = create_component_proposal(
            connection,
            workspace_id=WORKSPACE_ID,
            component_id=component_id,
            target_version=version,
            change_kind=action,
            expected_revision=revision,
            requested_grants=[grant],
            desired_configuration={},
            desired_slot_bindings=[],
            dependency_graph=[],
            source_kind="owner",
            source_reference=None,
            idempotency_key=f"mcp:proposal:{action}:{revision}",
        )
        proposal = proposed["proposal"]
        assert isinstance(proposal, dict)
        decide_component_proposal(
            connection,
            workspace_id=WORKSPACE_ID,
            proposal_id=str(proposal["proposal_id"]),
            decision="approve",
            request_sha256=str(proposal["request_sha256"]),
        )
        common = {
            "workspace_id": WORKSPACE_ID,
            "component_id": component_id,
            "action": action,
            "proposal_id": str(proposal["proposal_id"]),
            "request_sha256": str(proposal["request_sha256"]),
            "expected_revision": revision,
            "manifest_sha256": manifest_sha,
            "package_sha256": package_sha,
            "idempotency_key": f"mcp:action:{action}:{revision}",
        }
        prepared = apply_component_action_v2(
            connection,
            **common,
            phase="prepare",
            operation_id=None,
            outcome=None,
            evidence_sha256=None,
            health_state=None,
        )
        ticket = prepared["lifecycle_ticket"]
        assert isinstance(ticket, dict)
        settled = apply_component_action_v2(
            connection,
            **common,
            phase="settle",
            operation_id=str(ticket["operation_id"]),
            outcome="succeeded",
            evidence_sha256=digest_json({"action": action, "native": "ok"}),
            health_state="healthy" if action == "activate" else None,
            runtime_instance_id=ticket["runtime_instance_id"],
            workload_identity_digest=ticket["workload_identity_digest"],
        )
        installation = settled["installation"]
        assert isinstance(installation, dict)
        revision = int(installation["revision"])
    return manifest_sha, package_sha, revision


def test_catalog_requires_native_package_attestation(
    component_database: sqlite3.Connection,
) -> None:
    snapshot = get_component_snapshot(component_database, WORKSPACE_ID)
    canvas = next(
        item
        for item in snapshot["catalog"]
        if item["component_id"] == COMPONENT_ID and item["version"] == VERSION
    )
    assert canvas["available"] is False
    assert canvas["unavailable_reason"] == "package_not_attested"
    assert (
        canvas["policy_manifest_sha256"]
        == SEEDED_BY_ID_VERSION[(COMPONENT_ID, VERSION)].manifest_sha256
    )
    assert canvas["manifest_sha256"] is None
    assert canvas["package_sha256"] is None
    with pytest.raises(DesktopApiError, match="desktop_component_package_not_attested"):
        _proposal(
            component_database,
            change_kind="install",
            expected_revision=0,
            manifest_sha="0" * 64,
            package_sha="1" * 64,
        )


def test_attestation_rejects_arbitrary_policy_digest_and_adapter_collision(
    component_database: sqlite3.Connection,
) -> None:
    with pytest.raises(DesktopApiError, match="desktop_component_attestation_policy_mismatch"):
        attest_component_package(
            component_database,
            component_id=COMPONENT_ID,
            version=VERSION,
            policy_manifest_sha256="a" * 64,
            manifest_sha256="b" * 64,
            package_sha256="c" * 64,
            inventory_sha256="d" * 64,
            adapter_id="builtin-ui.v1",
        )
    with pytest.raises(DesktopApiError, match="desktop_component_attestation_adapter_mismatch"):
        attest_component_package(
            component_database,
            component_id=COMPONENT_ID,
            version=VERSION,
            policy_manifest_sha256=SEEDED_BY_ID_VERSION[(COMPONENT_ID, VERSION)].manifest_sha256,
            manifest_sha256="b" * 64,
            package_sha256="c" * 64,
            inventory_sha256="d" * 64,
            adapter_id="trusted-local-app.v1",
        )


@pytest.mark.parametrize(
    "logical_resource_id",
    [
        "C:\\Users\\Owner\\secret.txt",
        "https://example.test/resource",
        "workspace/other",
        "workspace.component.other",
    ],
)
def test_grants_reject_physical_locators_and_undeclared_resource_classes(
    component_database: sqlite3.Connection,
    logical_resource_id: str,
) -> None:
    _attest(component_database)
    grant = _grant()
    grant["logical_resource_id"] = logical_resource_id

    with pytest.raises(DesktopApiError, match="desktop_component_resource_scope_invalid"):
        create_component_proposal(
            component_database,
            workspace_id=WORKSPACE_ID,
            component_id=COMPONENT_ID,
            target_version=VERSION,
            change_kind="install",
            expected_revision=0,
            requested_grants=[grant],
            desired_configuration={},
            desired_slot_bindings=[],
            dependency_graph=[],
            source_kind="owner",
            source_reference=None,
            idempotency_key="hostile-resource-scope",
        )


def test_network_grant_accepts_only_the_manifest_reviewed_service_class(
    component_database: sqlite3.Connection,
) -> None:
    component_id = "builtin.readonly-mcp"
    version = "1.0.0"
    policy = SEEDED_BY_ID_VERSION[(component_id, version)]
    attest_component_package(
        component_database,
        component_id=component_id,
        version=version,
        policy_manifest_sha256=policy.manifest_sha256,
        manifest_sha256=digest_json({"bundle": component_id, "version": version}),
        package_sha256=digest_json({"files": ["mcp.json"], "version": version}),
        inventory_sha256=digest_json({"inventory": component_id}),
        adapter_id=policy.adapter_id,
    )
    grant = _grant()
    grant["action"] = "mcp.call"
    grant["logical_service_id"] = "https://api.example.test"

    with pytest.raises(DesktopApiError, match="desktop_component_network_scope_invalid"):
        create_component_proposal(
            component_database,
            workspace_id=WORKSPACE_ID,
            component_id=component_id,
            target_version=version,
            change_kind="install",
            expected_revision=0,
            requested_grants=[grant],
            desired_configuration={},
            desired_slot_bindings=[],
            dependency_graph=[],
            source_kind="owner",
            source_reference=None,
            idempotency_key="hostile-network-scope",
        )


def test_lifecycle_prepare_settle_never_self_completes(
    component_database: sqlite3.Connection,
) -> None:
    manifest_sha, package_sha = _attest(component_database)
    proposal = _proposal(
        component_database,
        change_kind="install",
        expected_revision=0,
        manifest_sha=manifest_sha,
        package_sha=package_sha,
    )
    common = {
        "workspace_id": WORKSPACE_ID,
        "component_id": COMPONENT_ID,
        "action": "install",
        "proposal_id": str(proposal["proposal_id"]),
        "request_sha256": str(proposal["request_sha256"]),
        "expected_revision": 0,
        "manifest_sha256": manifest_sha,
        "package_sha256": package_sha,
        "idempotency_key": "action:install:0",
    }
    prepared = apply_component_action_v2(
        component_database,
        **common,
        phase="prepare",
        operation_id=None,
        outcome=None,
        evidence_sha256=None,
        health_state=None,
    )
    assert prepared["operation"]["state"] == "pending"
    assert prepared["installation"] is None
    assert (
        component_database.execute("SELECT COUNT(*) FROM workspace_component_health").fetchone()[0]
        == 0
    )
    ticket = prepared["lifecycle_ticket"]
    settled = apply_component_action_v2(
        component_database,
        **common,
        phase="settle",
        operation_id=ticket["operation_id"],
        outcome="succeeded",
        evidence_sha256=digest_json({"native": "installed"}),
        health_state=None,
    )
    assert settled["operation"]["state"] == "succeeded"
    assert settled["installation"]["state"] == "installed"
    replayed = apply_component_action_v2(
        component_database,
        **common,
        phase="settle",
        operation_id=ticket["operation_id"],
        outcome="succeeded",
        evidence_sha256=digest_json({"native": "installed"}),
        health_state=None,
    )
    assert replayed["replayed"] is True
    assert replayed["installation"]["state"] == "installed"


def test_install_bind_activate_uses_exact_native_health(
    component_database: sqlite3.Connection,
) -> None:
    manifest_sha, package_sha = _attest(component_database)
    install = _proposal(
        component_database,
        change_kind="install",
        expected_revision=0,
        manifest_sha=manifest_sha,
        package_sha=package_sha,
    )
    assert (
        _lifecycle(component_database, action="install", expected_revision=0, proposal=install)[
            "installation"
        ]["revision"]
        == 1
    )
    bind = _proposal(
        component_database,
        change_kind="bind",
        expected_revision=1,
        manifest_sha=manifest_sha,
        package_sha=package_sha,
    )
    assert (
        _lifecycle(component_database, action="bind", expected_revision=1, proposal=bind)[
            "installation"
        ]["revision"]
        == 2
    )
    activate = _proposal(
        component_database,
        change_kind="activate",
        expected_revision=2,
        manifest_sha=manifest_sha,
        package_sha=package_sha,
    )
    active = _lifecycle(
        component_database, action="activate", expected_revision=2, proposal=activate
    )
    assert active["installation"]["state"] == "active"
    assert (
        get_component_snapshot(component_database, WORKSPACE_ID)["installations"][0]["health"]
        == "healthy"
    )
    assert (
        component_database.execute(
            "SELECT COUNT(*) FROM workspace_component_lifecycle_receipt"
        ).fetchone()[0]
        == 3
    )


def test_lifecycle_transition_matrix_upgrade_and_verified_rollback(
    component_database: sqlite3.Connection,
) -> None:
    manifest_v1, package_v1 = _attest(component_database)
    manifest_v11, package_v11 = _attest(component_database, "1.1.0")
    revision = 0
    for action in ("install", "bind", "activate"):
        proposal = _proposal(
            component_database,
            change_kind=action,
            expected_revision=revision,
            manifest_sha=manifest_v1,
            package_sha=package_v1,
        )
        revision = _lifecycle(
            component_database,
            action=action,
            expected_revision=revision,
            proposal=proposal,
        )["installation"]["revision"]

    with pytest.raises(DesktopApiError, match="desktop_component_bind_transition_invalid"):
        _proposal(
            component_database,
            change_kind="bind",
            expected_revision=revision,
            manifest_sha=manifest_v1,
            package_sha=package_v1,
        )
    with pytest.raises(DesktopApiError, match="desktop_component_upgrade_version_invalid"):
        _proposal(
            component_database,
            change_kind="upgrade",
            expected_revision=revision,
            manifest_sha=manifest_v1,
            package_sha=package_v1,
        )

    upgrade = _proposal(
        component_database,
        change_kind="upgrade",
        expected_revision=revision,
        manifest_sha=manifest_v11,
        package_sha=package_v11,
        target_version="1.1.0",
    )
    upgraded = _lifecycle(
        component_database,
        action="upgrade",
        expected_revision=revision,
        proposal=upgrade,
    )
    assert upgraded["installation"]["version"] == "1.1.0"
    assert upgraded["installation"]["state"] == "bound"
    revision = upgraded["installation"]["revision"]

    activate_v11 = _proposal(
        component_database,
        change_kind="activate",
        expected_revision=revision,
        manifest_sha=manifest_v11,
        package_sha=package_v11,
        target_version="1.1.0",
    )
    revision = _lifecycle(
        component_database,
        action="activate",
        expected_revision=revision,
        proposal=activate_v11,
    )["installation"]["revision"]

    rollback = _proposal(
        component_database,
        change_kind="rollback",
        expected_revision=revision,
        manifest_sha=manifest_v1,
        package_sha=package_v1,
    )
    rolled_back = _lifecycle(
        component_database,
        action="rollback",
        expected_revision=revision,
        proposal=rollback,
    )
    assert rolled_back["installation"]["version"] == VERSION
    assert rolled_back["installation"]["binding_generation"] == 3

    revoke = _proposal(
        component_database,
        change_kind="revoke",
        expected_revision=rolled_back["installation"]["revision"],
        manifest_sha=manifest_v1,
        package_sha=package_v1,
    )
    revoked = _lifecycle(
        component_database,
        action="revoke",
        expected_revision=rolled_back["installation"]["revision"],
        proposal=revoke,
    )
    with pytest.raises(DesktopApiError, match="desktop_component_revoked"):
        _proposal(
            component_database,
            change_kind="bind",
            expected_revision=revoked["installation"]["revision"],
            manifest_sha=manifest_v1,
            package_sha=package_v1,
        )


def test_destructive_prepare_fences_authority_and_unknown_blocks_reuse(
    component_database: sqlite3.Connection,
) -> None:
    manifest_sha, package_sha = _attest(component_database)
    revision = 0
    for action in ("install", "bind", "activate"):
        proposal = _proposal(
            component_database,
            change_kind=action,
            expected_revision=revision,
            manifest_sha=manifest_sha,
            package_sha=package_sha,
        )
        revision = _lifecycle(
            component_database,
            action=action,
            expected_revision=revision,
            proposal=proposal,
        )["installation"]["revision"]

    disable = _proposal(
        component_database,
        change_kind="disable",
        expected_revision=revision,
        manifest_sha=manifest_sha,
        package_sha=package_sha,
    )
    common = {
        "workspace_id": WORKSPACE_ID,
        "component_id": COMPONENT_ID,
        "action": "disable",
        "proposal_id": str(disable["proposal_id"]),
        "request_sha256": str(disable["request_sha256"]),
        "expected_revision": revision,
        "manifest_sha256": manifest_sha,
        "package_sha256": package_sha,
        "idempotency_key": f"action:disable:unknown:{revision}",
    }
    prepared = apply_component_action_v2(
        component_database,
        **common,
        phase="prepare",
        operation_id=None,
        outcome=None,
        evidence_sha256=None,
        health_state=None,
    )
    assert (
        component_database.execute(
            "SELECT state FROM workspace_component_grant ORDER BY created_at DESC LIMIT 1"
        ).fetchone()[0]
        == "revoked"
    )
    assert (
        component_database.execute(
            "SELECT state FROM workspace_component_runtime_instance ORDER BY created_at DESC LIMIT 1"
        ).fetchone()[0]
        == "revoked"
    )

    ticket = prepared["lifecycle_ticket"]
    evidence_sha = digest_json({"native": "outcome-unknown"})
    settled = apply_component_action_v2(
        component_database,
        **common,
        phase="settle",
        operation_id=str(ticket["operation_id"]),
        outcome="unknown",
        evidence_sha256=evidence_sha,
        health_state="unknown",
        error_code="desktop_component_lifecycle_outcome_unknown",
    )
    assert settled["installation"]["state"] == "blocked"
    assert (
        component_database.execute(
            "SELECT state FROM workspace_component_binding_generation ORDER BY generation DESC LIMIT 1"
        ).fetchone()[0]
        == "failed"
    )
    with pytest.raises(DesktopApiError, match="desktop_component_lifecycle_recovery_required"):
        reconcile_component_effect(
            component_database,
            workspace_id=WORKSPACE_ID,
            operation_id=str(ticket["operation_id"]),
            effect_id=str(ticket["effect_id"]),
            request_sha256=str(ticket["request_sha256"]),
            outcome="succeeded",
            evidence_sha256=evidence_sha,
        )


def test_emergency_prepare_durably_fences_before_native_cleanup(
    component_database: sqlite3.Connection,
) -> None:
    _activate_default_component(component_database)

    ticket = _prepare_emergency(component_database)

    assert (
        component_database.execute(
            "SELECT state FROM workspace_component_installation WHERE component_id = ?",
            (COMPONENT_ID,),
        ).fetchone()[0]
        == "blocked"
    )
    assert (
        component_database.execute(
            "SELECT state FROM workspace_component_operation WHERE id = ?",
            (ticket["operation_id"],),
        ).fetchone()[0]
        == "dispatching"
    )
    assert (
        component_database.execute(
            "SELECT state FROM workspace_component_effect WHERE id = ?",
            (ticket["effect_id"],),
        ).fetchone()[0]
        == "pending"
    )
    assert {
        row[0] for row in component_database.execute("SELECT state FROM workspace_component_grant")
    } == {"revoked"}
    assert {
        row[0]
        for row in component_database.execute(
            "SELECT state FROM workspace_component_runtime_instance"
        )
    } == {"revoked"}
    replayed = emergency_stop_components(
        component_database,
        workspace_id=WORKSPACE_ID,
        phase="prepare",
        idempotency_key="emergency:owner:1",
        reason_code="owner_emergency_stop",
    )
    assert replayed["replayed"] is True
    assert replayed["tickets"] == [ticket]


def test_emergency_prepare_revokes_runtime_even_after_its_grant_expired(
    component_database: sqlite3.Connection,
) -> None:
    _activate_default_component(component_database)
    component_database.execute(
        "UPDATE workspace_component_grant SET state = 'expired', updated_at = updated_at "
        "WHERE state = 'active'"
    )

    _prepare_emergency(component_database)

    assert (
        component_database.execute(
            "SELECT state FROM workspace_component_runtime_instance"
        ).fetchone()[0]
        == "revoked"
    )
    assert (
        component_database.execute(
            "SELECT COUNT(*) FROM workspace_component_revocation "
            "WHERE runtime_instance_id IS NOT NULL AND grant_id IS NULL"
        ).fetchone()[0]
        == 1
    )


@pytest.mark.parametrize(
    ("outcome", "error_code", "operation_state", "effect_state", "installation_state"),
    [
        ("succeeded", None, "succeeded", "succeeded", "revoked"),
        ("failed", "native_cleanup_failed", "failed", "failed", "blocked"),
        (
            "unknown",
            "native_cleanup_unknown",
            "unknown",
            "unknown",
            "blocked",
        ),
    ],
)
def test_emergency_settlement_never_restores_fenced_authority(
    component_database: sqlite3.Connection,
    outcome: str,
    error_code: str | None,
    operation_state: str,
    effect_state: str,
    installation_state: str,
) -> None:
    _activate_default_component(component_database)
    ticket = _prepare_emergency(component_database)
    evidence_sha = digest_json({"native_cleanup": outcome})

    settled = emergency_stop_components(
        component_database,
        workspace_id=WORKSPACE_ID,
        phase="settle",
        idempotency_key="emergency:owner:1",
        reason_code="owner_emergency_stop",
        component_id=COMPONENT_ID,
        operation_id=str(ticket["operation_id"]),
        effect_id=str(ticket["effect_id"]),
        request_sha256=str(ticket["request_sha256"]),
        outcome=outcome,
        evidence_sha256=evidence_sha,
        error_code=error_code,
    )

    assert settled["operation"]["state"] == operation_state
    assert settled["effect"]["state"] == effect_state
    assert (
        component_database.execute(
            "SELECT state FROM workspace_component_operation WHERE id = ?",
            (ticket["operation_id"],),
        ).fetchone()[0]
        == {
            "succeeded": "succeeded",
            "failed": "failed",
            "unknown": "reconciliation_required",
        }[outcome]
    )
    assert (
        component_database.execute(
            "SELECT state FROM workspace_component_effect WHERE id = ?",
            (ticket["effect_id"],),
        ).fetchone()[0]
        == {
            "succeeded": "committed",
            "failed": "failed",
            "unknown": "reconciliation_required",
        }[outcome]
    )
    assert (
        component_database.execute(
            "SELECT state FROM workspace_component_installation WHERE component_id = ?",
            (COMPONENT_ID,),
        ).fetchone()[0]
        == installation_state
    )
    assert (
        component_database.execute(
            "SELECT COUNT(*) FROM workspace_component_emergency_receipt WHERE operation_id = ?",
            (ticket["operation_id"],),
        ).fetchone()[0]
        == 1
    )


@pytest.mark.parametrize(
    ("reconciliation_outcome", "installation_state"),
    [("succeeded", "revoked"), ("failed", "blocked")],
)
def test_unknown_emergency_cleanup_requires_owner_reconciliation(
    component_database: sqlite3.Connection,
    reconciliation_outcome: str,
    installation_state: str,
) -> None:
    _activate_default_component(component_database)
    ticket = _prepare_emergency(component_database)
    unknown_evidence = digest_json({"native_cleanup": "unknown"})
    emergency_stop_components(
        component_database,
        workspace_id=WORKSPACE_ID,
        phase="settle",
        idempotency_key="emergency:owner:1",
        reason_code="owner_emergency_stop",
        component_id=COMPONENT_ID,
        operation_id=str(ticket["operation_id"]),
        effect_id=str(ticket["effect_id"]),
        request_sha256=str(ticket["request_sha256"]),
        outcome="unknown",
        evidence_sha256=unknown_evidence,
        error_code="native_cleanup_unknown",
    )

    reconciled = reconcile_component_effect(
        component_database,
        workspace_id=WORKSPACE_ID,
        operation_id=str(ticket["operation_id"]),
        effect_id=str(ticket["effect_id"]),
        request_sha256=str(ticket["request_sha256"]),
        outcome=reconciliation_outcome,
        evidence_sha256=digest_json({"owner_reconciliation": reconciliation_outcome}),
    )

    assert reconciled["operation"]["state"] == reconciliation_outcome
    assert (
        component_database.execute(
            "SELECT state FROM workspace_component_operation WHERE id = ?",
            (ticket["operation_id"],),
        ).fetchone()[0]
        == f"reconciled_{reconciliation_outcome}"
    )
    assert (
        component_database.execute(
            "SELECT state FROM workspace_component_installation WHERE component_id = ?",
            (COMPONENT_ID,),
        ).fetchone()[0]
        == installation_state
    )


def test_emergency_receipt_replays_exactly_and_rejects_drift(
    component_database: sqlite3.Connection,
) -> None:
    _activate_default_component(component_database)
    ticket = _prepare_emergency(component_database)
    arguments = {
        "workspace_id": WORKSPACE_ID,
        "phase": "settle",
        "idempotency_key": "emergency:owner:1",
        "reason_code": "owner_emergency_stop",
        "component_id": COMPONENT_ID,
        "operation_id": str(ticket["operation_id"]),
        "effect_id": str(ticket["effect_id"]),
        "request_sha256": str(ticket["request_sha256"]),
        "outcome": "unknown",
        "evidence_sha256": digest_json({"native_cleanup": "unknown"}),
        "error_code": "native_cleanup_unknown",
    }
    first = emergency_stop_components(component_database, **arguments)
    replayed = emergency_stop_components(component_database, **arguments)
    assert first["replayed"] is False
    assert replayed["replayed"] is True
    assert replayed["operation"]["state"] == "unknown"
    assert (
        component_database.execute(
            "SELECT COUNT(*) FROM workspace_component_emergency_receipt"
        ).fetchone()[0]
        == 1
    )

    with pytest.raises(DesktopApiError, match="desktop_component_emergency_receipt_payload_drift"):
        emergency_stop_components(
            component_database,
            **{
                **arguments,
                "evidence_sha256": digest_json({"native_cleanup": "different"}),
            },
        )


def test_emergency_settlement_rejects_cross_workspace_substitution(
    component_database: sqlite3.Connection,
) -> None:
    workspace_b = "workspace_" + "2" * 32
    create_workspace(component_database, workspace_b, OWNER_ID, "Workspace B")
    _activate_default_component(component_database)
    ticket = _prepare_emergency(component_database)

    with pytest.raises(DesktopApiError, match="desktop_component_emergency_operation_not_found"):
        emergency_stop_components(
            component_database,
            workspace_id=workspace_b,
            phase="settle",
            idempotency_key="emergency:owner:1",
            reason_code="owner_emergency_stop",
            component_id=COMPONENT_ID,
            operation_id=str(ticket["operation_id"]),
            effect_id=str(ticket["effect_id"]),
            request_sha256=str(ticket["request_sha256"]),
            outcome="succeeded",
            evidence_sha256=digest_json({"native_cleanup": "succeeded"}),
            error_code=None,
        )


@pytest.mark.parametrize(
    ("component_id", "operation", "logical_service_id"),
    [
        ("builtin.workspace-canvas", "ui.render", None),
        ("builtin.instruction-skill", "skill.resolve", None),
        ("builtin.readonly-mcp", "mcp.call", "reviewed_https"),
        ("builtin.sandbox-workload", "sandbox.run", None),
        ("knowledge.ebook", "local_adapter.open", None),
    ],
)
def test_all_five_families_share_the_complete_lifecycle(
    component_database: sqlite3.Connection,
    component_id: str,
    operation: str,
    logical_service_id: str | None,
) -> None:
    identities: dict[str, tuple[str, str]] = {}
    for version in ("1.0.0", "1.1.0"):
        policy = SEEDED_BY_ID_VERSION[(component_id, version)]
        manifest_sha = digest_json({"bundle": component_id, "version": version})
        package_sha = digest_json({"files": [component_id], "version": version})
        attest_component_package(
            component_database,
            component_id=component_id,
            version=version,
            policy_manifest_sha256=policy.manifest_sha256,
            manifest_sha256=manifest_sha,
            package_sha256=package_sha,
            inventory_sha256=digest_json({"inventory": package_sha}),
            adapter_id=policy.adapter_id,
        )
        identities[version] = (manifest_sha, package_sha)

    grant = {
        "action": operation,
        "logical_resource_id": "workspace.component.input",
        "resource_version": 1,
        "logical_service_id": logical_service_id,
        "expires_in_seconds": 3_600,
        "maximum_invocations": 16,
        "maximum_bytes_in": 4_096,
        "maximum_bytes_out": 8_192,
        "maximum_tokens": 0,
        "maximum_wall_time_ms": 30_000,
        "maximum_cost_units": 16,
    }

    def proposal_for(action: str, revision: int, version: str) -> dict[str, object]:
        manifest_sha, package_sha = identities[version]
        result = create_component_proposal(
            component_database,
            workspace_id=WORKSPACE_ID,
            component_id=component_id,
            target_version=version,
            change_kind=action,
            expected_revision=revision,
            requested_grants=[grant],
            desired_configuration={},
            desired_slot_bindings=[],
            dependency_graph=[],
            source_kind="owner",
            source_reference=None,
            idempotency_key=f"five:{component_id}:{version}:{action}:{revision}",
        )["proposal"]
        assert isinstance(result, dict)
        decide_component_proposal(
            component_database,
            workspace_id=WORKSPACE_ID,
            proposal_id=str(result["proposal_id"]),
            decision="approve",
            request_sha256=str(result["request_sha256"]),
        )
        assert result["manifest_sha256"] == manifest_sha
        assert result["package_sha256"] == package_sha
        return result

    def lifecycle_for(
        action: str, revision: int, version: str
    ) -> tuple[dict[str, object], dict[str, object]]:
        proposal = proposal_for(action, revision, version)
        common = {
            "workspace_id": WORKSPACE_ID,
            "component_id": component_id,
            "action": action,
            "proposal_id": str(proposal["proposal_id"]),
            "request_sha256": str(proposal["request_sha256"]),
            "expected_revision": revision,
            "manifest_sha256": str(proposal["manifest_sha256"]),
            "package_sha256": str(proposal["package_sha256"]),
            "idempotency_key": f"five:action:{component_id}:{version}:{action}:{revision}",
        }
        prepared = apply_component_action_v2(
            component_database,
            **common,
            phase="prepare",
            operation_id=None,
            outcome=None,
            evidence_sha256=None,
            health_state=None,
        )
        ticket = prepared["lifecycle_ticket"]
        assert isinstance(ticket, dict)
        settled = apply_component_action_v2(
            component_database,
            **common,
            phase="settle",
            operation_id=str(ticket["operation_id"]),
            outcome="succeeded",
            evidence_sha256=digest_json(
                {"action": action, "component_id": component_id, "native": "ok"}
            ),
            health_state="healthy" if action == "activate" else None,
            runtime_instance_id=ticket["runtime_instance_id"],
            workload_identity_digest=ticket["workload_identity_digest"],
        )
        return settled, ticket

    revision = 0
    for action in ("install", "bind", "activate"):
        settled, _ = lifecycle_for(action, revision, "1.0.0")
        installation = settled["installation"]
        assert isinstance(installation, dict)
        revision = int(installation["revision"])

    manifest_v1, package_v1 = identities["1.0.0"]
    begun = begin_component_invocation(
        component_database,
        workspace_id=WORKSPACE_ID,
        component_id=component_id,
        action=operation,
        expected_revision=revision,
        binding_generation=1,
        manifest_sha256=manifest_v1,
        package_sha256=package_v1,
        idempotency_key=f"five:invoke:{component_id}:1",
        arguments_sha256=digest_json({"component_id": component_id}),
        logical_resource_id="workspace.component.input",
        resource_version=1,
        logical_service_id=logical_service_id,
        bytes_in=64,
        bytes_out_reserved=256,
        tokens_reserved=0,
        wall_time_ms=1_000,
        cost_units=1,
    )
    ticket = begun["ticket"]
    assert isinstance(ticket, dict)
    result_sha = digest_json({"component_id": component_id, "invoked": True})
    invocation = settle_component_invocation(
        component_database,
        workspace_id=WORKSPACE_ID,
        operation_id=str(ticket["operation_id"]),
        request_sha256=str(ticket["request_sha256"]),
        state="succeeded",
        result_sha256=result_sha,
        evidence_sha256=digest_json({"adapter_receipt": result_sha}),
        error_code=None,
        actual_bytes_out=128,
        actual_tokens=0,
        actual_wall_time_ms=100,
    )
    assert invocation["operation"]["state"] == "succeeded"

    disabled, _ = lifecycle_for("disable", revision, "1.0.0")
    revision = int(disabled["installation"]["revision"])
    assert disabled["installation"]["state"] == "disabled"
    upgraded, _ = lifecycle_for("upgrade", revision, "1.1.0")
    revision = int(upgraded["installation"]["revision"])
    assert upgraded["installation"]["version"] == "1.1.0"
    activated_v11, _ = lifecycle_for("activate", revision, "1.1.0")
    revision = int(activated_v11["installation"]["revision"])
    rolled_back, _ = lifecycle_for("rollback", revision, "1.0.0")
    revision = int(rolled_back["installation"]["revision"])
    assert rolled_back["installation"]["binding_generation"] == 3
    revoked, _ = lifecycle_for("revoke", revision, "1.0.0")
    revision = int(revoked["installation"]["revision"])
    assert revoked["installation"]["state"] == "revoked"
    uninstalled, _ = lifecycle_for("uninstall", revision, "1.0.0")
    assert uninstalled["installation"] is None


@pytest.mark.parametrize(
    ("starting_state", "actions", "expected_binding_state"),
    [
        ("bound", ("install", "bind"), "revoked"),
        ("active", ("install", "bind", "activate"), "revoked"),
        ("disabled", ("install", "bind", "disable"), "disabled"),
        ("revoked", ("install", "revoke"), "revoked"),
    ],
)
def test_uninstall_from_supported_states_preserves_exact_binding_identity(
    component_database: sqlite3.Connection,
    starting_state: str,
    actions: tuple[str, ...],
    expected_binding_state: str,
) -> None:
    manifest_sha, package_sha = _attest(component_database)
    revision = 0
    for action in actions:
        proposal = _proposal(
            component_database,
            change_kind=action,
            expected_revision=revision,
            manifest_sha=manifest_sha,
            package_sha=package_sha,
        )
        settled = _lifecycle(
            component_database,
            action=action,
            expected_revision=revision,
            proposal=proposal,
        )
        installation = settled["installation"]
        assert isinstance(installation, dict)
        revision = int(installation["revision"])
    assert installation["state"] == starting_state

    before = component_database.execute(
        "SELECT * FROM workspace_component_installation WHERE workspace_id = ? "
        "AND component_id = ?",
        (WORKSPACE_ID, COMPONENT_ID),
    ).fetchone()
    assert before is not None
    binding_before = component_database.execute(
        "SELECT * FROM workspace_component_binding_generation WHERE installation_id = ? "
        "AND generation = ?",
        (before["id"], before["binding_generation"]),
    ).fetchone()
    assert binding_before is not None
    uninstall_proposal = _proposal(
        component_database,
        change_kind="uninstall",
        expected_revision=revision,
        manifest_sha=manifest_sha,
        package_sha=package_sha,
    )

    uninstalled = _lifecycle(
        component_database,
        action="uninstall",
        expected_revision=revision,
        proposal=uninstall_proposal,
    )

    assert uninstalled["installation"] is None
    after = component_database.execute(
        "SELECT * FROM workspace_component_installation WHERE id = ?", (before["id"],)
    ).fetchone()
    assert after is not None
    assert after["state"] == "uninstalled"
    expected_revision = revision + (2 if starting_state in {"bound", "active"} else 1)
    assert after["revision"] == expected_revision
    assert after["current_runtime_instance_id"] is None
    immutable_installation_fields = (
        "id",
        "owner_id",
        "workspace_id",
        "component_id",
        "version",
        "manifest_sha256",
        "package_sha256",
        "binding_generation",
        "proposal_id",
        "request_sha256",
        "configuration_sha256",
        "slot_bindings_sha256",
        "dependency_graph_sha256",
        "created_at",
    )
    assert {field: after[field] for field in immutable_installation_fields} == {
        field: before[field] for field in immutable_installation_fields
    }
    binding_after = component_database.execute(
        "SELECT * FROM workspace_component_binding_generation WHERE installation_id = ? "
        "AND generation = ?",
        (after["id"], after["binding_generation"]),
    ).fetchone()
    assert binding_after is not None
    assert binding_after["state"] == expected_binding_state
    immutable_binding_fields = (
        "installation_id",
        "workspace_id",
        "component_id",
        "generation",
        "version",
        "manifest_sha256",
        "package_sha256",
        "proposal_id",
        "request_sha256",
        "configuration_sha256",
        "slot_bindings_sha256",
        "dependency_graph_sha256",
        "created_at",
    )
    assert {field: binding_after[field] for field in immutable_binding_fields} == {
        field: binding_before[field] for field in immutable_binding_fields
    }
    decision = component_database.execute(
        "SELECT * FROM workspace_component_decision WHERE proposal_id = ?",
        (uninstall_proposal["proposal_id"],),
    ).fetchone()
    assert decision is not None
    assert decision["workspace_id"] == WORKSPACE_ID
    assert decision["request_sha256"] == uninstall_proposal["request_sha256"]
    ticket = uninstalled["lifecycle_ticket"]
    assert isinstance(ticket, dict)
    operation = component_database.execute(
        "SELECT * FROM workspace_component_operation WHERE id = ?", (ticket["operation_id"],)
    ).fetchone()
    assert operation is not None
    operation_request = json.loads(str(operation["request_json"]))
    assert operation_request["proposal_id"] == uninstall_proposal["proposal_id"]
    assert operation_request["request_sha256"] == uninstall_proposal["request_sha256"]
    assert operation["manifest_sha256"] == manifest_sha
    assert operation["package_sha256"] == package_sha
    audit_states = {
        json.loads(str(row["payload_json"]))["state"]
        for row in component_database.execute(
            "SELECT payload_json FROM audit_event WHERE workspace_id = ? "
            "AND event_type = 'workspace_component_state_changed' "
            "AND json_extract(payload_json, '$.operation_id') = ?",
            (WORKSPACE_ID, ticket["operation_id"]),
        )
    }
    assert audit_states == (
        {"revoked", "uninstalled"} if starting_state in {"bound", "active"} else {"uninstalled"}
    )


def test_binding_identity_guard_still_rejects_installed_to_disabled_drift(
    component_database: sqlite3.Connection,
) -> None:
    manifest_sha, package_sha = _attest(component_database)
    proposal = _proposal(
        component_database,
        change_kind="install",
        expected_revision=0,
        manifest_sha=manifest_sha,
        package_sha=package_sha,
    )
    installed = _lifecycle(
        component_database,
        action="install",
        expected_revision=0,
        proposal=proposal,
    )
    installation = installed["installation"]
    assert isinstance(installation, dict)

    with pytest.raises(sqlite3.IntegrityError, match="desktop_component_binding_identity_drift"):
        component_database.execute(
            "UPDATE workspace_component_binding_generation SET state = 'disabled', "
            "updated_at = updated_at WHERE installation_id = ? AND generation = ?",
            (installation["installation_id"], installation["binding_generation"]),
        )

    assert (
        component_database.execute(
            "SELECT state FROM workspace_component_binding_generation WHERE installation_id = ? "
            "AND generation = ?",
            (installation["installation_id"], installation["binding_generation"]),
        ).fetchone()[0]
        == "installed"
    )


def test_component_history_and_receipts_are_database_immutable(
    component_database: sqlite3.Connection,
) -> None:
    _attest(component_database)
    with pytest.raises(
        sqlite3.IntegrityError, match="desktop_component_package_attestation_immutable"
    ):
        component_database.execute(
            "UPDATE component_package_attestation SET inventory_sha256 = ?",
            ("f" * 64,),
        )


def test_lifecycle_invocation_and_emergency_receipts_are_immutable(
    component_database: sqlite3.Connection,
) -> None:
    manifest_sha, package_sha, revision = _activate_default_component(component_database)
    with pytest.raises(
        sqlite3.IntegrityError, match="desktop_component_lifecycle_receipt_immutable"
    ):
        component_database.execute(
            "UPDATE workspace_component_lifecycle_receipt SET evidence_sha256 = ? "
            "WHERE operation_id = (SELECT operation_id FROM workspace_component_lifecycle_receipt "
            "LIMIT 1)",
            ("f" * 64,),
        )

    begun = begin_component_invocation(
        component_database,
        workspace_id=WORKSPACE_ID,
        component_id=COMPONENT_ID,
        action="ui.render",
        expected_revision=revision,
        binding_generation=1,
        manifest_sha256=manifest_sha,
        package_sha256=package_sha,
        idempotency_key="immutable:invoke:1",
        arguments_sha256=digest_json({"view": "immutable"}),
        logical_resource_id="workspace.component.input",
        resource_version=1,
        logical_service_id=None,
        bytes_in=64,
        bytes_out_reserved=128,
        tokens_reserved=0,
        wall_time_ms=1_000,
        cost_units=1,
    )
    invocation_ticket = begun["ticket"]
    settle_component_invocation(
        component_database,
        workspace_id=WORKSPACE_ID,
        operation_id=str(invocation_ticket["operation_id"]),
        request_sha256=str(invocation_ticket["request_sha256"]),
        state="succeeded",
        result_sha256=digest_json({"rendered": True}),
        evidence_sha256=digest_json({"adapter": "receipt"}),
        error_code=None,
        actual_bytes_out=64,
        actual_tokens=0,
        actual_wall_time_ms=100,
    )
    with pytest.raises(
        sqlite3.IntegrityError, match="desktop_component_invocation_receipt_immutable"
    ):
        component_database.execute(
            "DELETE FROM workspace_component_invocation_receipt WHERE operation_id = ?",
            (invocation_ticket["operation_id"],),
        )

    emergency_ticket = _prepare_emergency(component_database)
    emergency_stop_components(
        component_database,
        workspace_id=WORKSPACE_ID,
        phase="settle",
        idempotency_key="emergency:owner:1",
        reason_code="owner_emergency_stop",
        component_id=COMPONENT_ID,
        operation_id=str(emergency_ticket["operation_id"]),
        effect_id=str(emergency_ticket["effect_id"]),
        request_sha256=str(emergency_ticket["request_sha256"]),
        outcome="succeeded",
        evidence_sha256=digest_json({"native_cleanup": "succeeded"}),
        error_code=None,
    )
    with pytest.raises(
        sqlite3.IntegrityError, match="desktop_component_emergency_receipt_immutable"
    ):
        component_database.execute(
            "UPDATE workspace_component_emergency_receipt SET evidence_sha256 = ?",
            ("e" * 64,),
        )


def test_database_rejects_unaudited_state_and_cross_workspace_authority_substitution(
    component_database: sqlite3.Connection,
) -> None:
    workspace_b = "workspace_" + "2" * 32
    create_workspace(component_database, workspace_b, OWNER_ID, "Workspace B")
    _, _, revision = _activate_default_component(component_database)

    with pytest.raises(
        sqlite3.IntegrityError, match="desktop_component_installation_audit_missing"
    ):
        component_database.execute(
            "UPDATE workspace_component_installation SET state = 'disabled', revision = ?, "
            "updated_at = updated_at WHERE workspace_id = ? AND component_id = ?",
            (revision + 1, WORKSPACE_ID, COMPONENT_ID),
        )
    with pytest.raises(sqlite3.IntegrityError):
        component_database.execute(
            "UPDATE workspace_component_runtime_instance SET workspace_id = ? "
            "WHERE workspace_id = ?",
            (workspace_b, WORKSPACE_ID),
        )
    with pytest.raises(sqlite3.IntegrityError):
        component_database.execute(
            "UPDATE workspace_component_grant SET workspace_id = ? WHERE workspace_id = ?",
            (workspace_b, WORKSPACE_ID),
        )
    with pytest.raises(sqlite3.IntegrityError):
        component_database.execute(
            "UPDATE workspace_component_effect SET workspace_id = ? WHERE workspace_id = ?",
            (workspace_b, WORKSPACE_ID),
        )
    emergency_ticket = _prepare_emergency(component_database)
    emergency_stop_components(
        component_database,
        workspace_id=WORKSPACE_ID,
        phase="settle",
        idempotency_key="emergency:owner:1",
        reason_code="owner_emergency_stop",
        component_id=COMPONENT_ID,
        operation_id=str(emergency_ticket["operation_id"]),
        effect_id=str(emergency_ticket["effect_id"]),
        request_sha256=str(emergency_ticket["request_sha256"]),
        outcome="succeeded",
        evidence_sha256=digest_json({"native_cleanup": "succeeded"}),
        error_code=None,
    )
    with pytest.raises(
        sqlite3.IntegrityError, match="desktop_component_emergency_receipt_immutable"
    ):
        component_database.execute(
            "UPDATE workspace_component_emergency_receipt SET workspace_id = ?",
            (workspace_b,),
        )


def test_grant_usage_rejects_regression_and_non_cas_updates(
    component_database: sqlite3.Connection,
) -> None:
    manifest_sha, package_sha, revision = _activate_default_component(component_database)
    begin_component_invocation(
        component_database,
        workspace_id=WORKSPACE_ID,
        component_id=COMPONENT_ID,
        action="ui.render",
        expected_revision=revision,
        binding_generation=1,
        manifest_sha256=manifest_sha,
        package_sha256=package_sha,
        idempotency_key="grant-usage:invoke:1",
        arguments_sha256=digest_json({"view": "usage"}),
        logical_resource_id="workspace.component.input",
        resource_version=1,
        logical_service_id=None,
        bytes_in=64,
        bytes_out_reserved=128,
        tokens_reserved=0,
        wall_time_ms=1_000,
        cost_units=1,
    )

    with pytest.raises(sqlite3.IntegrityError, match="desktop_component_grant_usage_invalid"):
        component_database.execute(
            "UPDATE workspace_component_grant_usage SET calls = calls - 1, "
            "row_version = row_version + 1"
        )
    with pytest.raises(sqlite3.IntegrityError, match="desktop_component_grant_usage_invalid"):
        component_database.execute(
            "UPDATE workspace_component_grant_usage SET row_version = row_version + 2"
        )


@pytest.mark.parametrize(
    ("case", "error_code"),
    [
        ("duplicate_dependency", "component_manifest_dependencies_invalid"),
        ("self_dependency", "component_manifest_dependencies_invalid"),
        ("self_conflict", "component_manifest_conflicts_invalid"),
        ("dependency_conflict_overlap", "component_manifest_conflicts_invalid"),
    ],
)
def test_closed_manifest_rejects_ambiguous_dependency_and_conflict_graphs(
    case: str,
    error_code: str,
) -> None:
    manifest = deepcopy(SEEDED_BY_ID_VERSION[(COMPONENT_ID, VERSION)].manifest)
    dependency = _manifest_dependency("builtin.readonly-mcp")
    if case == "duplicate_dependency":
        manifest["dependencies"] = [
            dependency,
            _manifest_dependency("builtin.readonly-mcp", "1.1.0"),
        ]
    elif case == "self_dependency":
        manifest["dependencies"] = [_manifest_dependency(COMPONENT_ID)]
    elif case == "self_conflict":
        manifest["conflicts"] = [COMPONENT_ID]
    else:
        manifest["dependencies"] = [dependency]
        manifest["conflicts"] = [dependency["component_id"]]

    with pytest.raises(ValueError, match=rf"^{error_code}$"):
        validate_closed_manifest(manifest)


def test_closed_manifest_accepts_disjoint_dependencies_and_conflicts() -> None:
    manifest = deepcopy(SEEDED_BY_ID_VERSION[(COMPONENT_ID, VERSION)].manifest)
    manifest["dependencies"] = [_manifest_dependency("builtin.readonly-mcp")]
    manifest["conflicts"] = ["builtin.instruction-skill"]

    validated = validate_closed_manifest(manifest)

    assert validated["dependencies"] == manifest["dependencies"]
    assert validated["conflicts"] == manifest["conflicts"]


def test_proposal_rejects_live_conflict_in_the_same_workspace(
    component_database: sqlite3.Connection,
) -> None:
    manifest_sha, package_sha = _attest(component_database)
    proposal = _proposal(
        component_database,
        change_kind="install",
        expected_revision=0,
        manifest_sha=manifest_sha,
        package_sha=package_sha,
    )
    _lifecycle(
        component_database,
        action="install",
        expected_revision=0,
        proposal=proposal,
    )
    owner_component_id = _register_owner_conflicting_component(component_database)

    with pytest.raises(DesktopApiError, match="^desktop_component_conflict_installed$"):
        create_component_proposal(
            component_database,
            workspace_id=WORKSPACE_ID,
            component_id=owner_component_id,
            target_version=VERSION,
            change_kind="install",
            expected_revision=0,
            requested_grants=[_grant()],
            desired_configuration={},
            desired_slot_bindings=[],
            dependency_graph=[],
            source_kind="owner",
            source_reference=None,
            idempotency_key="owner-package-conflict-same-workspace",
        )
    assert (
        component_database.execute(
            "SELECT COUNT(*) FROM workspace_component_proposal WHERE workspace_id = ? "
            "AND component_id = ?",
            (WORKSPACE_ID, owner_component_id),
        ).fetchone()[0]
        == 0
    )


def test_proposal_rejects_reverse_conflict_declared_by_live_component(
    component_database: sqlite3.Connection,
) -> None:
    owner_component_id = _register_owner_conflicting_component(component_database)
    owner_proposal = create_component_proposal(
        component_database,
        workspace_id=WORKSPACE_ID,
        component_id=owner_component_id,
        target_version=VERSION,
        change_kind="install",
        expected_revision=0,
        requested_grants=[_grant()],
        desired_configuration={},
        desired_slot_bindings=[],
        dependency_graph=[],
        source_kind="owner",
        source_reference=None,
        idempotency_key="owner-package-conflict-install-first",
    )["proposal"]
    assert isinstance(owner_proposal, dict)
    decide_component_proposal(
        component_database,
        workspace_id=WORKSPACE_ID,
        proposal_id=str(owner_proposal["proposal_id"]),
        decision="approve",
        request_sha256=str(owner_proposal["request_sha256"]),
    )
    action = {
        "workspace_id": WORKSPACE_ID,
        "component_id": owner_component_id,
        "action": "install",
        "proposal_id": str(owner_proposal["proposal_id"]),
        "request_sha256": str(owner_proposal["request_sha256"]),
        "expected_revision": 0,
        "manifest_sha256": str(owner_proposal["manifest_sha256"]),
        "package_sha256": str(owner_proposal["package_sha256"]),
        "idempotency_key": "owner-package-conflict-install-first-action",
    }
    prepared = apply_component_action_v2(
        component_database,
        **action,
        phase="prepare",
        operation_id=None,
        outcome=None,
        evidence_sha256=None,
        health_state=None,
    )
    ticket = prepared["lifecycle_ticket"]
    assert isinstance(ticket, dict)
    apply_component_action_v2(
        component_database,
        **action,
        phase="settle",
        operation_id=str(ticket["operation_id"]),
        outcome="succeeded",
        evidence_sha256=digest_json({"installed": owner_component_id}),
        health_state=None,
    )
    _attest(component_database)

    with pytest.raises(DesktopApiError, match="^desktop_component_conflict_installed$"):
        create_component_proposal(
            component_database,
            workspace_id=WORKSPACE_ID,
            component_id=COMPONENT_ID,
            target_version=VERSION,
            change_kind="install",
            expected_revision=0,
            requested_grants=[_grant()],
            desired_configuration={},
            desired_slot_bindings=[],
            dependency_graph=[],
            source_kind="owner",
            source_reference=None,
            idempotency_key="builtin-conflict-reverse-declaration",
        )


def test_proposal_ignores_conflict_installed_in_another_workspace(
    component_database: sqlite3.Connection,
) -> None:
    workspace_b = "workspace_" + "2" * 32
    create_workspace(component_database, workspace_b, OWNER_ID, "Workspace B")
    manifest_sha, package_sha = _attest(component_database)
    conflicting_proposal = _proposal(
        component_database,
        change_kind="install",
        expected_revision=0,
        manifest_sha=manifest_sha,
        package_sha=package_sha,
        workspace_id=workspace_b,
    )
    _lifecycle(
        component_database,
        action="install",
        expected_revision=0,
        proposal=conflicting_proposal,
        workspace_id=workspace_b,
    )
    owner_component_id = _register_owner_conflicting_component(component_database)

    result = create_component_proposal(
        component_database,
        workspace_id=WORKSPACE_ID,
        component_id=owner_component_id,
        target_version=VERSION,
        change_kind="install",
        expected_revision=0,
        requested_grants=[_grant()],
        desired_configuration={},
        desired_slot_bindings=[],
        dependency_graph=[],
        source_kind="owner",
        source_reference=None,
        idempotency_key="owner-package-conflict-other-workspace",
    )

    proposal = result["proposal"]
    assert isinstance(proposal, dict)
    assert proposal["component_id"] == owner_component_id


def test_proposal_ignores_historical_uninstalled_conflict(
    component_database: sqlite3.Connection,
) -> None:
    manifest_sha, package_sha = _attest(component_database)
    install_proposal = _proposal(
        component_database,
        change_kind="install",
        expected_revision=0,
        manifest_sha=manifest_sha,
        package_sha=package_sha,
    )
    installed = _lifecycle(
        component_database,
        action="install",
        expected_revision=0,
        proposal=install_proposal,
    )
    installation = installed["installation"]
    assert isinstance(installation, dict)
    revision = int(installation["revision"])
    uninstall_proposal = _proposal(
        component_database,
        change_kind="uninstall",
        expected_revision=revision,
        manifest_sha=manifest_sha,
        package_sha=package_sha,
    )
    _lifecycle(
        component_database,
        action="uninstall",
        expected_revision=revision,
        proposal=uninstall_proposal,
    )
    owner_component_id = _register_owner_conflicting_component(component_database)

    result = create_component_proposal(
        component_database,
        workspace_id=WORKSPACE_ID,
        component_id=owner_component_id,
        target_version=VERSION,
        change_kind="install",
        expected_revision=0,
        requested_grants=[_grant()],
        desired_configuration={},
        desired_slot_bindings=[],
        dependency_graph=[],
        source_kind="owner",
        source_reference=None,
        idempotency_key="owner-package-conflict-uninstalled-history",
    )

    proposal = result["proposal"]
    assert isinstance(proposal, dict)
    assert proposal["component_id"] == owner_component_id


def test_owner_reviewed_declarative_package_uses_closed_manifest_and_settings_schema(
    component_database: sqlite3.Connection,
) -> None:
    manifest = deepcopy(SEEDED_BY_ID_VERSION[(COMPONENT_ID, VERSION)].manifest)
    manifest["component_id"] = "owner.reviewed-canvas"
    manifest["publisher"] = {"classification": "owner_reviewed", "id": "owner.local"}
    manifest["configuration_schema"] = {
        "additional_properties": False,
        "kind": "closed_object",
        "properties": {
            "title": {"type": "string", "max_length": 12},
            "columns": {"type": "integer", "minimum": 1, "maximum": 4},
        },
        "required": ["title"],
        "version": 1,
    }
    manifest_sha = digest_json(manifest)
    package_sha = digest_json({"declarative": manifest_sha})
    registered = register_owner_reviewed_component(
        component_database,
        workspace_id=WORKSPACE_ID,
        manifest=manifest,
        manifest_sha256=manifest_sha,
        package_sha256=package_sha,
        inventory_sha256=digest_json({"manifest.json": manifest_sha}),
    )
    assert registered["publisher_class"] == "owner_reviewed"
    catalog = next(
        item
        for item in get_component_snapshot(component_database, WORKSPACE_ID)["catalog"]
        if item["component_id"] == "owner.reviewed-canvas"
    )
    assert catalog["available"] is True
    assert catalog["permissions"] == manifest["permissions"]
    assert catalog["settings_schema"]["properties"]["title"]["max_length"] == 12
    with pytest.raises(DesktopApiError, match="desktop_component_configuration_invalid"):
        create_component_proposal(
            component_database,
            workspace_id=WORKSPACE_ID,
            component_id="owner.reviewed-canvas",
            target_version=VERSION,
            change_kind="install",
            expected_revision=0,
            requested_grants=[_grant()],
            desired_configuration={"title": "this title is too long", "columns": 2},
            desired_slot_bindings=[],
            dependency_graph=[],
            source_kind="owner",
            source_reference=None,
            idempotency_key="owner-package-invalid-config",
        )


def test_owner_reviewed_package_requires_explicit_registration_in_each_workspace(
    component_database: sqlite3.Connection,
) -> None:
    workspace_b = "workspace_" + "2" * 32
    create_workspace(component_database, workspace_b, OWNER_ID, "Workspace B")
    manifest = deepcopy(SEEDED_BY_ID_VERSION[(COMPONENT_ID, VERSION)].manifest)
    manifest["component_id"] = "owner.workspace-scoped-canvas"
    manifest["publisher"] = {"classification": "owner_reviewed", "id": "owner.local"}
    manifest_sha = digest_json(manifest)
    package_sha = digest_json({"declarative": manifest_sha})
    inventory_sha = digest_json({"manifest.json": manifest_sha})
    register_owner_reviewed_component(
        component_database,
        workspace_id=WORKSPACE_ID,
        manifest=manifest,
        manifest_sha256=manifest_sha,
        package_sha256=package_sha,
        inventory_sha256=inventory_sha,
    )

    catalog_b = next(
        item
        for item in get_component_snapshot(component_database, workspace_b)["catalog"]
        if item["component_id"] == manifest["component_id"]
    )
    assert catalog_b["available"] is False
    assert catalog_b["manifest_sha256"] is None
    with pytest.raises(DesktopApiError, match="desktop_component_package_not_attested"):
        create_component_proposal(
            component_database,
            workspace_id=workspace_b,
            component_id=str(manifest["component_id"]),
            target_version=VERSION,
            change_kind="install",
            expected_revision=0,
            requested_grants=[_grant()],
            desired_configuration={},
            desired_slot_bindings=[],
            dependency_graph=[],
            source_kind="owner",
            source_reference=None,
            idempotency_key="workspace-b-before-review",
        )

    registered_b = register_owner_reviewed_component(
        component_database,
        workspace_id=workspace_b,
        manifest=manifest,
        manifest_sha256=manifest_sha,
        package_sha256=package_sha,
        inventory_sha256=inventory_sha,
    )
    assert registered_b["replayed"] is False
    assert (
        next(
            item
            for item in get_component_snapshot(component_database, workspace_b)["catalog"]
            if item["component_id"] == manifest["component_id"]
        )["available"]
        is True
    )


def test_owner_package_rejects_manifest_authority_smuggling(
    component_database: sqlite3.Connection,
) -> None:
    manifest = deepcopy(SEEDED_BY_ID_VERSION[(COMPONENT_ID, VERSION)].manifest)
    manifest["component_id"] = "owner.hostile-canvas"
    manifest["publisher"] = {"classification": "owner_reviewed", "id": "owner.local"}
    manifest["entrypoint"] = {
        "adapter_id": "builtin-ui.v1",
        "kind": "https://attacker.invalid/plugin.js",
    }
    with pytest.raises(DesktopApiError, match="component_manifest_entrypoint_invalid"):
        register_owner_reviewed_component(
            component_database,
            workspace_id=WORKSPACE_ID,
            manifest=manifest,
            manifest_sha256=digest_json(manifest),
            package_sha256="a" * 64,
            inventory_sha256="b" * 64,
        )


def test_renderer_cannot_self_assert_an_assistant_proposal_source(
    component_database: sqlite3.Connection,
) -> None:
    _attest(component_database)
    with pytest.raises(
        DesktopApiError, match="desktop_component_assistant_source_requires_message_route"
    ):
        create_component_proposal(
            component_database,
            workspace_id=WORKSPACE_ID,
            component_id=COMPONENT_ID,
            target_version=VERSION,
            change_kind="install",
            expected_revision=0,
            requested_grants=[_grant()],
            desired_configuration={},
            desired_slot_bindings=[],
            dependency_graph=[],
            source_kind="assistant",
            source_reference="message_00000000000000000000000000000001",
            idempotency_key="assistant-forged-source",
        )


def test_assistant_proposal_is_derived_from_the_exact_completed_message(
    component_database: sqlite3.Connection,
) -> None:
    manifest_sha, package_sha = _attest(component_database)
    policy = SEEDED_BY_ID_VERSION[(COMPONENT_ID, VERSION)]
    message_id = "message_" + "b" * 32
    envelope = {
        "type": "omnibase.workspace-component.proposal.v1",
        "component_id": COMPONENT_ID,
        "target_version": VERSION,
        "change_kind": "install",
        "expected_revision": 0,
        "policy_manifest_sha256": policy.manifest_sha256,
        "manifest_sha256": manifest_sha,
        "package_sha256": package_sha,
        "requested_grants": [_grant()],
        "desired_configuration": {},
        "desired_slot_bindings": [
            {
                "slot_id": "editor.component",
                "binding_key": "workspace.canvas",
                "order_index": 10,
                "configuration": {},
            }
        ],
        "dependency_graph": [],
    }
    _insert_assistant_message(
        component_database,
        message_id=message_id,
        content=json.dumps(envelope, sort_keys=True, separators=(",", ":")),
    )

    result = create_assistant_component_proposal(
        component_database,
        workspace_id=WORKSPACE_ID,
        message_id=message_id,
    )

    proposal = result["proposal"]
    assert isinstance(proposal, dict)
    assert proposal["source_kind"] == "assistant"
    assert proposal["source_reference"] == message_id
    assert proposal["desired_slot_bindings"] == envelope["desired_slot_bindings"]
    assert proposal["request_sha256"]


def test_unrelated_assistant_message_cannot_authorize_renderer_payload(
    component_database: sqlite3.Connection,
) -> None:
    _attest(component_database)
    message_id = "message_" + "c" * 32
    _insert_assistant_message(
        component_database,
        message_id=message_id,
        content=json.dumps({"type": "unrelated.answer", "text": "looks good"}),
    )

    with pytest.raises(DesktopApiError, match="desktop_component_assistant_payload_invalid"):
        create_assistant_component_proposal(
            component_database,
            workspace_id=WORKSPACE_ID,
            message_id=message_id,
        )


def test_invocation_reserves_before_dispatch_and_records_actual_receipt(
    component_database: sqlite3.Connection,
) -> None:
    manifest_sha, package_sha = _attest(component_database)
    revision = 0
    for action in ("install", "bind", "activate"):
        proposal = _proposal(
            component_database,
            change_kind=action,
            expected_revision=revision,
            manifest_sha=manifest_sha,
            package_sha=package_sha,
        )
        result = _lifecycle(
            component_database, action=action, expected_revision=revision, proposal=proposal
        )
        revision = result["installation"]["revision"]
    begun = begin_component_invocation(
        component_database,
        workspace_id=WORKSPACE_ID,
        component_id=COMPONENT_ID,
        action="ui.render",
        expected_revision=revision,
        binding_generation=1,
        manifest_sha256=manifest_sha,
        package_sha256=package_sha,
        idempotency_key="invoke:canvas:1",
        arguments_sha256=digest_json({"view": "canvas"}),
        logical_resource_id="workspace.component.input",
        resource_version=1,
        logical_service_id=None,
        bytes_in=128,
        bytes_out_reserved=1_024,
        tokens_reserved=0,
        wall_time_ms=2_000,
        cost_units=1,
    )
    ticket = begun["ticket"]
    assert ticket["arguments_sha256"] == digest_json({"view": "canvas"})
    assert (
        component_database.execute(
            "SELECT COUNT(*) FROM workspace_component_budget_reservation"
        ).fetchone()[0]
        == 1
    )
    result_sha = digest_json({"rendered": True})
    settled = settle_component_invocation(
        component_database,
        workspace_id=WORKSPACE_ID,
        operation_id=ticket["operation_id"],
        request_sha256=ticket["request_sha256"],
        state="succeeded",
        result_sha256=result_sha,
        evidence_sha256=digest_json({"adapter_receipt": result_sha}),
        error_code=None,
        actual_bytes_out=256,
        actual_tokens=0,
        actual_wall_time_ms=500,
    )
    assert settled["operation"]["state"] == "succeeded"
    receipt = component_database.execute(
        "SELECT * FROM workspace_component_invocation_receipt"
    ).fetchone()
    assert receipt["actual_bytes_out"] == 256
    assert receipt["reserved_bytes_out"] == 1_024


@pytest.mark.parametrize("fence_action", ["disable", "revoke", "emergency"])
def test_pre_fence_invocation_can_never_settle_as_success(
    component_database: sqlite3.Connection,
    fence_action: str,
) -> None:
    manifest_sha, package_sha, revision = _activate_default_component(component_database)
    begun = _begin_default_invocation(
        component_database,
        manifest_sha=manifest_sha,
        package_sha=package_sha,
        revision=revision,
        idempotency_key=f"invoke:before:{fence_action}",
    )
    ticket = begun["ticket"]
    assert isinstance(ticket, dict)

    if fence_action == "emergency":
        _prepare_emergency(component_database)
    else:
        proposal = _proposal(
            component_database,
            change_kind=fence_action,
            expected_revision=revision,
            manifest_sha=manifest_sha,
            package_sha=package_sha,
        )
        apply_component_action_v2(
            component_database,
            workspace_id=WORKSPACE_ID,
            component_id=COMPONENT_ID,
            action=fence_action,
            phase="prepare",
            proposal_id=str(proposal["proposal_id"]),
            request_sha256=str(proposal["request_sha256"]),
            expected_revision=revision,
            manifest_sha256=manifest_sha,
            package_sha256=package_sha,
            idempotency_key=f"action:{fence_action}:{revision}",
            operation_id=None,
            outcome=None,
            evidence_sha256=None,
            health_state=None,
        )

    with pytest.raises(
        DesktopApiError, match="desktop_component_invocation_reconciliation_required"
    ):
        settle_component_invocation(
            component_database,
            workspace_id=WORKSPACE_ID,
            operation_id=str(ticket["operation_id"]),
            request_sha256=str(ticket["request_sha256"]),
            state="succeeded",
            result_sha256=digest_json({"late": "success"}),
            evidence_sha256=digest_json({"adapter": "late-success"}),
            error_code=None,
            actual_bytes_out=1,
            actual_tokens=0,
            actual_wall_time_ms=1,
        )
    assert (
        component_database.execute(
            "SELECT state FROM workspace_component_operation WHERE id = ?",
            (ticket["operation_id"],),
        ).fetchone()[0]
        == "reconciliation_required"
    )
    assert (
        component_database.execute(
            "SELECT state FROM workspace_component_effect WHERE operation_id = ?",
            (ticket["operation_id"],),
        ).fetchone()[0]
        == "reconciliation_required"
    )
    assert (
        component_database.execute(
            "SELECT COUNT(*) FROM workspace_component_invocation_receipt WHERE operation_id = ?",
            (ticket["operation_id"],),
        ).fetchone()[0]
        == 0
    )


@pytest.mark.parametrize("authority_drift", ["workload_lease", "revocation", "expiry"])
def test_invocation_settlement_fences_live_authority_drift(
    component_database: sqlite3.Connection,
    authority_drift: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_sha, package_sha, revision = _activate_default_component(component_database)
    begun = _begin_default_invocation(
        component_database,
        manifest_sha=manifest_sha,
        package_sha=package_sha,
        revision=revision,
        idempotency_key=f"invoke:drift:{authority_drift}",
    )
    ticket = begun["ticket"]
    assert isinstance(ticket, dict)
    runtime_id = str(ticket["runtime_instance_id"])
    if authority_drift == "workload_lease":
        component_database.execute(
            "UPDATE workspace_component_workload_lease SET state = 'revoked' "
            "WHERE runtime_instance_id = ?",
            (runtime_id,),
        )
    elif authority_drift == "revocation":
        installation_id = component_database.execute(
            "SELECT installation_id FROM workspace_component_runtime_instance WHERE id = ?",
            (runtime_id,),
        ).fetchone()[0]
        component_database.execute(
            "INSERT INTO workspace_component_revocation "
            "(id, workspace_id, installation_id, runtime_instance_id, grant_id, reason_code, "
            "actor_type, actor_id, created_at) VALUES (?, ?, ?, ?, NULL, ?, 'system', NULL, ?)",
            (
                "revocation_" + "d" * 32,
                WORKSPACE_ID,
                installation_id,
                runtime_id,
                "test_authority_drift",
                component_service.utc_now_text(),
            ),
        )
    else:
        monkeypatch.setattr(
            component_service,
            "utc_now_text",
            lambda: "9999-01-01T00:00:00.000000Z",
        )
    component_database.commit()

    with pytest.raises(
        DesktopApiError, match="desktop_component_invocation_reconciliation_required"
    ):
        settle_component_invocation(
            component_database,
            workspace_id=WORKSPACE_ID,
            operation_id=str(ticket["operation_id"]),
            request_sha256=str(ticket["request_sha256"]),
            state="succeeded",
            result_sha256=digest_json({"drift": authority_drift}),
            evidence_sha256=digest_json({"adapter": "stale-authority"}),
            error_code=None,
            actual_bytes_out=1,
            actual_tokens=0,
            actual_wall_time_ms=1,
        )
    assert (
        component_database.execute(
            "SELECT state FROM workspace_component_operation WHERE id = ?",
            (ticket["operation_id"],),
        ).fetchone()[0]
        == "reconciliation_required"
    )
    assert (
        component_database.execute(
            "SELECT state FROM workspace_component_effect WHERE operation_id = ?",
            (ticket["operation_id"],),
        ).fetchone()[0]
        == "reconciliation_required"
    )


@pytest.mark.parametrize(
    ("dimension", "request_overrides", "error_code"),
    [
        ("calls", {}, "desktop_component_budget_calls_exhausted"),
        ("bytes_in", {"bytes_in": 1}, "desktop_component_budget_bytes_in_exhausted"),
        (
            "bytes_out",
            {"bytes_out_reserved": 1},
            "desktop_component_budget_bytes_out_exhausted",
        ),
        ("tokens", {"tokens_reserved": 1}, "desktop_component_budget_tokens_exhausted"),
        (
            "wall_time",
            {"wall_time_ms": 1},
            "desktop_component_budget_wall_time_exhausted",
        ),
        ("cost", {"cost_units": 1}, "desktop_component_budget_cost_exhausted"),
    ],
)
def test_invocation_budget_veto_matrix_fails_before_dispatch(
    component_database: sqlite3.Connection,
    dimension: str,
    request_overrides: dict[str, int],
    error_code: str,
) -> None:
    manifest_sha, package_sha, revision = _activate_default_component(component_database)
    usage_updates = {
        "calls": "UPDATE workspace_component_grant_usage SET calls = "
        "(SELECT max_calls FROM workspace_component_grant WHERE id = grant_id), "
        "row_version = row_version + 1",
        "bytes_in": "UPDATE workspace_component_grant_usage SET bytes_in = "
        "(SELECT max_bytes_in FROM workspace_component_grant WHERE id = grant_id), "
        "row_version = row_version + 1",
        "bytes_out": "UPDATE workspace_component_grant_usage SET bytes_out_reserved = "
        "(SELECT max_bytes_out FROM workspace_component_grant WHERE id = grant_id), "
        "row_version = row_version + 1",
        "wall_time": "UPDATE workspace_component_grant_usage SET wall_time_ms_reserved = "
        "(SELECT max_wall_time_ms FROM workspace_component_grant WHERE id = grant_id), "
        "row_version = row_version + 1",
        "cost": "UPDATE workspace_component_grant_usage SET cost_units = "
        "(SELECT max_cost_units FROM workspace_component_grant WHERE id = grant_id), "
        "row_version = row_version + 1",
    }
    if dimension in usage_updates:
        component_database.execute(usage_updates[dimension])

    with pytest.raises(DesktopApiError, match=error_code):
        _begin_default_invocation(
            component_database,
            manifest_sha=manifest_sha,
            package_sha=package_sha,
            revision=revision,
            idempotency_key=f"matrix:budget:{dimension}",
            **request_overrides,
        )
    assert (
        component_database.execute(
            "SELECT COUNT(*) FROM workspace_component_operation WHERE kind = 'invoke'"
        ).fetchone()[0]
        == 0
    )
    assert (
        component_database.execute(
            "SELECT COUNT(*) FROM workspace_component_effect WHERE effect_kind = 'adapter_invoke'"
        ).fetchone()[0]
        == 0
    )


def test_invocation_expired_grant_is_rejected_before_dispatch(
    component_database: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_sha, package_sha, revision = _activate_default_component(component_database)
    monkeypatch.setattr(
        component_service,
        "utc_now_text",
        lambda: "9999-01-01T00:00:00.000000Z",
    )

    with pytest.raises(DesktopApiError, match="desktop_component_grant_expired"):
        _begin_default_invocation(
            component_database,
            manifest_sha=manifest_sha,
            package_sha=package_sha,
            revision=revision,
            idempotency_key="matrix:expired:grant",
        )
    assert (
        component_database.execute(
            "SELECT COUNT(*) FROM workspace_component_operation WHERE kind = 'invoke'"
        ).fetchone()[0]
        == 0
    )


@pytest.mark.parametrize(
    ("authority_state", "error_code"),
    [
        ("revoked_grant", "desktop_component_authority_unavailable"),
        ("revoked_workload_lease", "desktop_component_authority_unavailable"),
        ("revoked_runtime", "desktop_component_runtime_health_unavailable"),
        ("stale_binding_generation", "desktop_component_invocation_binding_stale"),
        ("revocation_ledger", "desktop_component_authority_revoked"),
    ],
)
def test_invocation_authority_veto_matrix(
    component_database: sqlite3.Connection,
    authority_state: str,
    error_code: str,
) -> None:
    manifest_sha, package_sha, revision = _activate_default_component(component_database)
    binding_generation = 1
    if authority_state == "revoked_grant":
        component_database.execute(
            "UPDATE workspace_component_grant SET state = 'revoked', updated_at = updated_at"
        )
    elif authority_state == "revoked_workload_lease":
        component_database.execute(
            "UPDATE workspace_component_workload_lease SET state = 'revoked', "
            "updated_at = updated_at"
        )
    elif authority_state == "revoked_runtime":
        component_database.execute(
            "UPDATE workspace_component_runtime_instance SET state = 'revoked', "
            "updated_at = updated_at"
        )
    elif authority_state == "stale_binding_generation":
        binding_generation = 2
    else:
        installation = component_database.execute(
            "SELECT id, updated_at FROM workspace_component_installation WHERE workspace_id = ? "
            "AND component_id = ?",
            (WORKSPACE_ID, COMPONENT_ID),
        ).fetchone()
        grant_id = component_database.execute(
            "SELECT id FROM workspace_component_grant WHERE workspace_id = ?",
            (WORKSPACE_ID,),
        ).fetchone()[0]
        component_database.execute(
            "INSERT INTO workspace_component_revocation "
            "(id, workspace_id, installation_id, runtime_instance_id, grant_id, reason_code, "
            "actor_type, actor_id, created_at) VALUES (?, ?, ?, NULL, ?, "
            "'owner_revoked', 'owner', ?, ?)",
            (
                "revocation_" + "e" * 32,
                WORKSPACE_ID,
                installation["id"],
                grant_id,
                OWNER_ID,
                installation["updated_at"],
            ),
        )

    with pytest.raises(DesktopApiError, match=error_code):
        _begin_default_invocation(
            component_database,
            manifest_sha=manifest_sha,
            package_sha=package_sha,
            revision=revision,
            binding_generation=binding_generation,
            idempotency_key=f"matrix:authority:{authority_state}",
        )
    assert (
        component_database.execute(
            "SELECT COUNT(*) FROM workspace_component_operation WHERE kind = 'invoke'"
        ).fetchone()[0]
        == 0
    )


def test_invocation_concurrency_saturation_rejects_the_third_dispatch(
    component_database: sqlite3.Connection,
) -> None:
    manifest_sha, package_sha, revision = _activate_default_component(component_database)
    for index in (1, 2):
        _begin_default_invocation(
            component_database,
            manifest_sha=manifest_sha,
            package_sha=package_sha,
            revision=revision,
            idempotency_key=f"matrix:concurrency:{index}",
        )

    with pytest.raises(DesktopApiError, match="desktop_component_budget_concurrency_exhausted"):
        _begin_default_invocation(
            component_database,
            manifest_sha=manifest_sha,
            package_sha=package_sha,
            revision=revision,
            idempotency_key="matrix:concurrency:3",
        )
    assert (
        component_database.execute(
            "SELECT COUNT(*) FROM workspace_component_operation WHERE kind = 'invoke'"
        ).fetchone()[0]
        == 2
    )


def test_invocation_stale_network_fencing_is_rejected_before_dispatch(
    component_database: sqlite3.Connection,
) -> None:
    manifest_sha, package_sha, revision = _activate_readonly_mcp(component_database)
    component_database.execute(
        "UPDATE workspace_component_network_lease SET state = 'revoked', updated_at = updated_at"
    )

    with pytest.raises(DesktopApiError, match="desktop_component_network_lease_unavailable"):
        begin_component_invocation(
            component_database,
            workspace_id=WORKSPACE_ID,
            component_id="builtin.readonly-mcp",
            action="mcp.call",
            expected_revision=revision,
            binding_generation=1,
            manifest_sha256=manifest_sha,
            package_sha256=package_sha,
            idempotency_key="matrix:network:fencing",
            arguments_sha256=digest_json({"request": "network"}),
            logical_resource_id="workspace.component.input",
            resource_version=1,
            logical_service_id="reviewed_https",
            bytes_in=1,
            bytes_out_reserved=1,
            tokens_reserved=0,
            wall_time_ms=1,
            cost_units=1,
        )
    assert (
        component_database.execute(
            "SELECT COUNT(*) FROM workspace_component_operation WHERE kind = 'invoke'"
        ).fetchone()[0]
        == 0
    )


def test_invocation_cannot_cross_workspace_scope(
    component_database: sqlite3.Connection,
) -> None:
    workspace_b = "workspace_" + "2" * 32
    create_workspace(component_database, workspace_b, OWNER_ID, "Workspace B")
    manifest_sha, package_sha, revision = _activate_default_component(component_database)

    with pytest.raises(DesktopApiError, match="desktop_component_installation_not_found"):
        _begin_default_invocation(
            component_database,
            manifest_sha=manifest_sha,
            package_sha=package_sha,
            revision=revision,
            workspace_id=workspace_b,
            idempotency_key="matrix:workspace:cross",
        )
    assert (
        component_database.execute(
            "SELECT COUNT(*) FROM workspace_component_operation WHERE kind = 'invoke'"
        ).fetchone()[0]
        == 0
    )


def test_restart_recovery_fences_old_identity_and_requires_native_settlement(
    component_database: sqlite3.Connection,
) -> None:
    manifest_sha, package_sha = _attest(component_database)
    revision = 0
    for action in ("install", "bind", "activate"):
        proposal = _proposal(
            component_database,
            change_kind=action,
            expected_revision=revision,
            manifest_sha=manifest_sha,
            package_sha=package_sha,
        )
        revision = _lifecycle(
            component_database, action=action, expected_revision=revision, proposal=proposal
        )["installation"]["revision"]
    previous_runtime = component_database.execute(
        "SELECT current_runtime_instance_id FROM workspace_component_installation"
    ).fetchone()[0]
    recover_component_kernel(component_database)
    snapshot = get_component_snapshot(component_database, WORKSPACE_ID)
    assert snapshot["installations"][0]["state"] == "blocked"
    recovery = snapshot["recoveries"][0]
    assert recovery["state"] == "pending"
    assert (
        component_database.execute(
            "SELECT state FROM workspace_component_runtime_instance WHERE id = ?",
            (previous_runtime,),
        ).fetchone()[0]
        == "revoked"
    )
    settled = settle_component_recovery(
        component_database,
        workspace_id=WORKSPACE_ID,
        recovery_id=recovery["recovery_id"],
        operation_id=recovery["operation_id"],
        outcome="succeeded",
        evidence_sha256=digest_json({"native": "recovered"}),
        health_state="healthy",
        runtime_instance_id=recovery["runtime_instance_id"],
        workload_identity_digest=recovery["workload_identity_digest"],
        error_code=None,
    )
    assert settled["operation"]["state"] == "succeeded"
    recovered = get_component_snapshot(component_database, WORKSPACE_ID)
    assert recovered["installations"][0]["state"] == "active"
    assert recovered["installations"][0]["health"] == "healthy"


def test_restart_recovery_preserves_near_expiry_and_cumulative_usage(
    component_database: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FrozenActivationDateTime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return datetime(2099, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(component_service, "datetime", FrozenActivationDateTime)
    monkeypatch.setattr(
        component_service,
        "utc_now_text",
        lambda: "2099-01-01T00:00:01.000000Z",
    )
    manifest_sha, package_sha, revision = _activate_default_component(component_database)
    begun = _begin_default_invocation(
        component_database,
        manifest_sha=manifest_sha,
        package_sha=package_sha,
        revision=revision,
        idempotency_key="recovery:usage:before-restart",
        bytes_in=13,
        bytes_out_reserved=21,
        wall_time_ms=34,
        cost_units=2,
    )
    ticket = begun["ticket"]
    assert isinstance(ticket, dict)
    settle_component_invocation(
        component_database,
        workspace_id=WORKSPACE_ID,
        operation_id=str(ticket["operation_id"]),
        request_sha256=str(ticket["request_sha256"]),
        state="succeeded",
        result_sha256=digest_json({"rendered": "before-restart"}),
        evidence_sha256=digest_json({"adapter": "before-restart"}),
        error_code=None,
        actual_bytes_out=20,
        actual_tokens=0,
        actual_wall_time_ms=30,
    )
    previous_grant = component_database.execute(
        "SELECT * FROM workspace_component_grant WHERE state = 'active'"
    ).fetchone()
    assert previous_grant is not None
    previous_usage = component_database.execute(
        "SELECT * FROM workspace_component_grant_usage WHERE grant_id = ?",
        (previous_grant["id"],),
    ).fetchone()
    assert previous_usage is not None
    not_before = "2099-01-01T00:00:00.000000Z"
    expires_at = "2099-01-01T01:00:00.000000Z"
    assert previous_grant["not_before"] == not_before
    assert previous_grant["expires_at"] == expires_at
    monkeypatch.setattr(
        component_service,
        "utc_now_text",
        lambda: "2099-01-01T00:59:59.000000Z",
    )

    recover_component_kernel(component_database)
    recovery = get_component_snapshot(component_database, WORKSPACE_ID)["recoveries"][0]
    settle_component_recovery(
        component_database,
        workspace_id=WORKSPACE_ID,
        recovery_id=recovery["recovery_id"],
        operation_id=recovery["operation_id"],
        outcome="succeeded",
        evidence_sha256=digest_json({"native": "near-expiry-recovered"}),
        health_state="healthy",
        runtime_instance_id=recovery["runtime_instance_id"],
        workload_identity_digest=recovery["workload_identity_digest"],
        error_code=None,
    )

    recovered_grant = component_database.execute(
        "SELECT * FROM workspace_component_grant WHERE runtime_instance_id = ?",
        (recovery["runtime_instance_id"],),
    ).fetchone()
    assert recovered_grant is not None
    assert recovered_grant["not_before"] == not_before
    assert recovered_grant["expires_at"] == expires_at
    recovered_usage = component_database.execute(
        "SELECT * FROM workspace_component_grant_usage WHERE grant_id = ?",
        (recovered_grant["id"],),
    ).fetchone()
    assert recovered_usage is not None
    usage_fields = (
        "calls",
        "bytes_in",
        "bytes_out_reserved",
        "tokens_reserved",
        "wall_time_ms_reserved",
        "cost_units",
        "retries",
        "row_version",
    )
    assert {field: recovered_usage[field] for field in usage_fields} == {
        field: previous_usage[field] for field in usage_fields
    }
    audit = component_database.execute(
        "SELECT payload_json FROM audit_event "
        "WHERE event_type = 'workspace_component_recovery_authority_reissued'"
    ).fetchone()
    assert audit is not None
    payload = json.loads(str(audit["payload_json"]))
    assert payload["ancestor_grant_id"] == previous_grant["id"]
    assert payload["grant_id"] == recovered_grant["id"]
    assert payload["ancestor_runtime_instance_id"] == previous_grant["runtime_instance_id"]
    assert payload["runtime_instance_id"] == recovery["runtime_instance_id"]
    assert payload["ancestor_usage_sha256"] == digest_json(payload["ancestor_usage"])


def test_restart_recovery_never_refreshes_an_exhausted_budget(
    component_database: sqlite3.Connection,
) -> None:
    manifest_sha, package_sha, revision = _activate_default_component(component_database)
    for index in range(8):
        begun = _begin_default_invocation(
            component_database,
            manifest_sha=manifest_sha,
            package_sha=package_sha,
            revision=revision,
            idempotency_key=f"recovery:exhaust:{index}",
            bytes_in=0,
            bytes_out_reserved=0,
            wall_time_ms=0,
            cost_units=0,
        )
        ticket = begun["ticket"]
        assert isinstance(ticket, dict)
        settle_component_invocation(
            component_database,
            workspace_id=WORKSPACE_ID,
            operation_id=str(ticket["operation_id"]),
            request_sha256=str(ticket["request_sha256"]),
            state="succeeded",
            result_sha256=digest_json({"call": index}),
            evidence_sha256=digest_json({"adapter_call": index}),
            error_code=None,
            actual_bytes_out=0,
            actual_tokens=0,
            actual_wall_time_ms=0,
        )
    previous_grant = component_database.execute(
        "SELECT grant.*, usage.calls FROM workspace_component_grant AS grant "
        "JOIN workspace_component_grant_usage AS usage ON usage.grant_id = grant.id "
        "WHERE grant.state = 'active'"
    ).fetchone()
    assert previous_grant is not None
    assert previous_grant["calls"] == previous_grant["max_calls"] == 8

    recover_component_kernel(component_database)
    recovery = get_component_snapshot(component_database, WORKSPACE_ID)["recoveries"][0]
    with pytest.raises(DesktopApiError, match="desktop_component_recovery_budget_exhausted"):
        settle_component_recovery(
            component_database,
            workspace_id=WORKSPACE_ID,
            recovery_id=recovery["recovery_id"],
            operation_id=recovery["operation_id"],
            outcome="succeeded",
            evidence_sha256=digest_json({"native": "must-not-refresh-budget"}),
            health_state="healthy",
            runtime_instance_id=recovery["runtime_instance_id"],
            workload_identity_digest=recovery["workload_identity_digest"],
            error_code=None,
        )
    assert (
        component_database.execute(
            "SELECT state FROM workspace_component_installation WHERE component_id = ?",
            (COMPONENT_ID,),
        ).fetchone()[0]
        == "blocked"
    )
    assert (
        component_database.execute(
            "SELECT COUNT(*) FROM workspace_component_grant WHERE runtime_instance_id = ?",
            (recovery["runtime_instance_id"],),
        ).fetchone()[0]
        == 0
    )
    assert (
        component_database.execute(
            "SELECT COUNT(*) FROM audit_event "
            "WHERE event_type = 'workspace_component_recovery_authority_reissued'"
        ).fetchone()[0]
        == 0
    )


def test_restart_recovery_never_renews_an_expired_grant(
    component_database: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _activate_default_component(component_database)
    recover_component_kernel(component_database)
    recovery = get_component_snapshot(component_database, WORKSPACE_ID)["recoveries"][0]
    monkeypatch.setattr(
        component_service,
        "utc_now_text",
        lambda: "9999-01-01T00:00:00.000000Z",
    )

    with pytest.raises(DesktopApiError, match="desktop_component_recovery_grant_expired"):
        settle_component_recovery(
            component_database,
            workspace_id=WORKSPACE_ID,
            recovery_id=recovery["recovery_id"],
            operation_id=recovery["operation_id"],
            outcome="succeeded",
            evidence_sha256=digest_json({"native": "must-not-renew-expired-grant"}),
            health_state="healthy",
            runtime_instance_id=recovery["runtime_instance_id"],
            workload_identity_digest=recovery["workload_identity_digest"],
            error_code=None,
        )
    assert (
        component_database.execute(
            "SELECT state FROM workspace_component_installation WHERE component_id = ?",
            (COMPONENT_ID,),
        ).fetchone()[0]
        == "blocked"
    )
    assert (
        component_database.execute(
            "SELECT COUNT(*) FROM workspace_component_grant WHERE runtime_instance_id = ?",
            (recovery["runtime_instance_id"],),
        ).fetchone()[0]
        == 0
    )


def test_restart_with_unresolved_destructive_operation_never_recovers_active_authority(
    component_database: sqlite3.Connection,
) -> None:
    _activate_default_component(component_database)
    installation = component_database.execute(
        "SELECT * FROM workspace_component_installation WHERE workspace_id = ? "
        "AND component_id = ?",
        (WORKSPACE_ID, COMPONENT_ID),
    ).fetchone()
    assert installation is not None
    operation_id = "compop_" + "d" * 32
    request = {
        "component_id": COMPONENT_ID,
        "reason_code": "test_crash_before_destructive_dispatch",
        "workspace_id": WORKSPACE_ID,
    }
    request_json = json.dumps(request, sort_keys=True, separators=(",", ":"))
    request_sha = digest_json(request)
    generation = component_database.execute(
        "SELECT MAX(operation_generation) + 1 FROM workspace_component_operation "
        "WHERE workspace_id = ? AND component_id = ?",
        (WORKSPACE_ID, COMPONENT_ID),
    ).fetchone()[0]
    timestamp = str(installation["updated_at"])
    component_database.execute(
        "INSERT INTO workspace_component_operation "
        "(id, owner_id, workspace_id, component_id, installation_id, kind, action, "
        "operation_generation, expected_revision, binding_generation, runtime_instance_id, "
        "manifest_sha256, package_sha256, idempotency_key, request_json, request_sha256, "
        "state, version, result_sha256, evidence_sha256, error_code, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'disable', NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "'accepted', 1, NULL, NULL, NULL, ?, ?)",
        (
            operation_id,
            OWNER_ID,
            WORKSPACE_ID,
            COMPONENT_ID,
            installation["id"],
            generation,
            installation["revision"],
            installation["binding_generation"],
            installation["current_runtime_instance_id"],
            installation["manifest_sha256"],
            installation["package_sha256"],
            "crash:destructive:disable",
            request_json,
            request_sha,
            timestamp,
            timestamp,
        ),
    )
    component_database.execute(
        "INSERT INTO workspace_component_operation_transition "
        "(operation_id, sequence, state, reason_code, evidence_sha256, recorded_at) "
        "VALUES (?, 1, 'accepted', 'operation_accepted', NULL, ?)",
        (operation_id, timestamp),
    )

    recover_component_kernel(component_database)

    snapshot = get_component_snapshot(component_database, WORKSPACE_ID)
    assert snapshot["installations"][0]["state"] == "blocked"
    assert snapshot["recoveries"] == []
    assert (
        component_database.execute(
            "SELECT state FROM workspace_component_operation WHERE id = ?", (operation_id,)
        ).fetchone()[0]
        == "failed"
    )
    assert (
        component_database.execute(
            "SELECT current_runtime_instance_id FROM workspace_component_installation "
            "WHERE workspace_id = ? AND component_id = ?",
            (WORKSPACE_ID, COMPONENT_ID),
        ).fetchone()[0]
        is None
    )
    assert {
        row[0] for row in component_database.execute("SELECT state FROM workspace_component_grant")
    } == {"revoked"}
