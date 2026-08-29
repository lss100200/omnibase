from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from omnibase.desktop_local.app import (
    DESKTOP_NATIVE_CONTROL_HEADER,
    DesktopLocalAppConfig,
    create_desktop_local_app,
)
from omnibase.desktop_local.config import DesktopLocalConfig
from omnibase.desktop_local.schema import (
    STANDARD_WORKBENCH_PROFILE,
    STANDARD_WORKBENCH_PROFILE_SHA256,
)

_TOKEN = "a" * 64
_PROOF_KEY = "b" * 64
_CONTROL_TOKEN = "c" * 64


def _config(tmp_path: Path) -> DesktopLocalAppConfig:
    return DesktopLocalAppConfig(
        storage=DesktopLocalConfig(
            data_root=tmp_path / "desktop-data",
            application_version="1.0.0",
        ),
        instance_token=_TOKEN,
        native_proof_key=_PROOF_KEY,
        native_control_token=_CONTROL_TOKEN,
    )


def _headers() -> dict[str, str]:
    return {DESKTOP_NATIVE_CONTROL_HEADER: _CONTROL_TOKEN}


def _bootstrap(client: TestClient, name: str = "Workspace") -> tuple[dict[str, object], str]:
    owner = client.post(
        "/desktop/v1/owner/bootstrap",
        headers=_headers(),
        json={"display_name": "Owner"},
    ).json()["owner"]
    workspace = client.post(
        "/desktop/v1/workspaces",
        headers=_headers(),
        json={"name": name},
    ).json()["workspace"]
    return owner, str(workspace["id"])


def _composition(client: TestClient, workspace_id: str) -> dict[str, object]:
    response = client.get(f"/desktop/v1/workspaces/{workspace_id}/composition", headers=_headers())
    assert response.status_code == 200
    return response.json()


def _desired_profile(**changes: object) -> dict[str, object]:
    profile = copy.deepcopy(STANDARD_WORKBENCH_PROFILE)
    for dotted_path, value in changes.items():
        group, key = dotted_path.split("__", 1)
        target = profile[group]
        assert isinstance(target, dict)
        target[key] = value
    return profile


def _owner_proposal(
    client: TestClient, workspace_id: str, desired: dict[str, object]
) -> dict[str, object]:
    current = _composition(client, workspace_id)["profile"]
    assert isinstance(current, dict)
    response = client.post(
        f"/desktop/v1/workspaces/{workspace_id}/composition/proposals",
        headers=_headers(),
        json={
            "expected_revision": current["revision"],
            "expected_profile_sha256": current["profile_sha256"],
            "desired_profile": desired,
        },
    )
    assert response.status_code == 200
    return response.json()["proposal"]


def _approve(
    client: TestClient, workspace_id: str, proposal: dict[str, object]
) -> dict[str, object]:
    response = client.post(
        f"/desktop/v1/workspaces/{workspace_id}/composition/proposals/"
        f"{proposal['id']}/decision",
        headers=_headers(),
        json={"decision": "approve", "request_sha256": proposal["request_sha256"]},
    )
    assert response.status_code == 200
    return response.json()


def _insert_assistant_message(
    connection: sqlite3.Connection,
    *,
    owner_id: str,
    workspace_id: str,
    conversation_id: str,
    message_id: str,
    content: str,
    timestamp: str,
    invocation_status: str = "succeeded",
) -> None:
    invocation_id = "invocation_" + message_id.removeprefix("message_")
    connection.execute(
        "INSERT INTO invocation "
        "(id, owner_id, workspace_id, conversation_id, provider_id, requested_model, "
        "actual_model, family, gear, thinking_depth, status, duration_ms, input_tokens, "
        "output_tokens, total_tokens, error_code, error_redacted, retry_of_invocation_id, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'model', ?, "
        "'generic-openai-compatible', 'standard', 'disabled', ?, 1, 1, 1, 2, ?, ?, NULL, ?, ?)",
        (
            invocation_id,
            owner_id,
            workspace_id,
            conversation_id,
            "provider_" + "a" * 32,
            "model" if invocation_status == "succeeded" else None,
            invocation_status,
            None if invocation_status == "succeeded" else "desktop_provider_request_failed",
            None if invocation_status == "succeeded" else "Provider request failed",
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
            owner_id,
            workspace_id,
            conversation_id,
            content,
            invocation_id,
            timestamp,
        ),
    )


def test_default_profile_and_application_preference_are_real_persistent_state(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        owner, workspace_id = _bootstrap(client)
        preference = client.get("/desktop/v1/settings/application", headers=_headers()).json()[
            "preference"
        ]
        composition = _composition(client, workspace_id)

        assert preference == {
            "density": "compact",
            "reduce_motion": False,
            "row_version": 1,
            "updated_at": preference["updated_at"],
        }
        assert composition["profile"]["revision"] == 1
        assert composition["profile"]["profile_sha256"] == STANDARD_WORKBENCH_PROFILE_SHA256
        assert composition["profile"]["value"] == STANDARD_WORKBENCH_PROFILE
        assert composition["proposals"] == []
        assert composition["audit"] == []
        assert {item["id"] for item in composition["slot_catalog"]} == set(
            STANDARD_WORKBENCH_PROFILE["slots"]
        )

        updated = client.post(
            "/desktop/v1/settings/application",
            headers=_headers(),
            json={
                "density": "comfortable",
                "reduce_motion": True,
                "expected_row_version": 1,
            },
        )
        stale = client.post(
            "/desktop/v1/settings/application",
            headers=_headers(),
            json={
                "density": "compact",
                "reduce_motion": False,
                "expected_row_version": 1,
            },
        )

    assert updated.status_code == 200
    assert updated.json()["preference"]["row_version"] == 2
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "desktop_workbench_preference_version_conflict"
    connection = sqlite3.connect(config.storage.database_path)
    try:
        assert connection.execute(
            "SELECT density, reduce_motion, row_version FROM owner_workbench_preference "
            "WHERE owner_id = ?",
            (owner["id"],),
        ).fetchone() == ("comfortable", 1, 2)
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM audit_event WHERE event_type = 'workbench_preference_updated'"
            ).fetchone()[0]
            == 1
        )
    finally:
        connection.close()


def test_owner_proposal_requires_exact_digest_and_applies_one_immutable_revision(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    desired = _desired_profile(
        appearance__density="compact",
        layout__focus_mode=True,
        layout__agent_panel="closed",
    )
    with TestClient(create_desktop_local_app(config)) as client:
        _, workspace_id = _bootstrap(client)
        proposal = _owner_proposal(client, workspace_id, desired)
        replay = _owner_proposal(client, workspace_id, desired)
        wrong_digest = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/composition/proposals/"
            f"{proposal['id']}/decision",
            headers=_headers(),
            json={"decision": "approve", "request_sha256": "f" * 64},
        )
        applied = _approve(client, workspace_id, proposal)
        repeated = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/composition/proposals/"
            f"{proposal['id']}/decision",
            headers=_headers(),
            json={"decision": "approve", "request_sha256": proposal["request_sha256"]},
        )
        composition = _composition(client, workspace_id)

    assert replay["id"] == proposal["id"]
    assert wrong_digest.status_code == 409
    assert wrong_digest.json()["error"]["code"] == "desktop_composition_digest_conflict"
    assert applied["decision"] == "approved"
    assert applied["workspace_id"] == workspace_id
    assert applied["proposal_id"] == proposal["id"]
    assert applied["request_sha256"] == proposal["request_sha256"]
    assert applied["applied_revision"] == 2
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "desktop_composition_proposal_decided"
    assert composition["profile"]["revision"] == 2
    assert composition["profile"]["value"] == desired
    assert [row["revision"] for row in composition["revisions"]] == [2, 1]
    assert [row["event_type"] for row in composition["audit"]] == [
        "workspace_composition_applied",
        "workspace_composition_proposed",
    ]
    assert composition["audit"][0]["payload"] == {
        "profile_sha256": proposal["desired_profile_sha256"],
        "proposal_id": proposal["id"],
        "request_sha256": proposal["request_sha256"],
        "revision": 2,
        "source_kind": "owner",
    }
    assert composition["audit"][1]["payload"] == {
        "base_revision": 1,
        "desired_profile_sha256": proposal["desired_profile_sha256"],
        "proposal_id": proposal["id"],
        "request_sha256": proposal["request_sha256"],
        "source_kind": "owner",
    }

    connection = sqlite3.connect(config.storage.database_path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="composition_revision_immutable"):
            connection.execute(
                "UPDATE workspace_composition_revision SET source_kind = 'system' "
                "WHERE workspace_id = ? AND revision = 2",
                (workspace_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="composition_proposal_immutable"):
            connection.execute(
                "DELETE FROM workspace_composition_proposal WHERE id = ?", (proposal["id"],)
            )
        with pytest.raises(sqlite3.IntegrityError, match="composition_decision_immutable"):
            connection.execute(
                "UPDATE workspace_composition_decision SET decision = 'rejected' "
                "WHERE proposal_id = ?",
                (proposal["id"],),
            )
    finally:
        connection.close()


def test_owner_rejection_is_terminal_and_preserves_the_current_revision(tmp_path: Path) -> None:
    config = _config(tmp_path)
    desired = _desired_profile(appearance__density="comfortable")
    with TestClient(create_desktop_local_app(config)) as client:
        _, workspace_id = _bootstrap(client)
        proposal = _owner_proposal(client, workspace_id, desired)
        rejected = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/composition/proposals/"
            f"{proposal['id']}/decision",
            headers=_headers(),
            json={"decision": "reject", "request_sha256": proposal["request_sha256"]},
        )
        late_approval = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/composition/proposals/"
            f"{proposal['id']}/decision",
            headers=_headers(),
            json={"decision": "approve", "request_sha256": proposal["request_sha256"]},
        )
        composition = _composition(client, workspace_id)

    assert rejected.status_code == 200
    assert rejected.json() == {
        "workspace_id": workspace_id,
        "proposal_id": proposal["id"],
        "request_sha256": proposal["request_sha256"],
        "decision": "rejected",
        "applied_revision": None,
    }
    assert late_approval.status_code == 409
    assert late_approval.json()["error"]["code"] == "desktop_composition_proposal_decided"
    assert composition["profile"]["revision"] == 1
    assert composition["profile"]["value"] == STANDARD_WORKBENCH_PROFILE
    assert composition["proposals"][0]["decision"] == "rejected"
    assert composition["proposals"][0]["applied_revision"] is None
    assert [row["event_type"] for row in composition["audit"]] == [
        "workspace_composition_rejected",
        "workspace_composition_proposed",
    ]
    assert composition["audit"][0]["payload"] == {
        "proposal_id": proposal["id"],
        "request_sha256": proposal["request_sha256"],
    }


def test_database_guards_bind_proposal_revision_current_and_decision_identity(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    desired = _desired_profile(appearance__density="comfortable")
    with TestClient(create_desktop_local_app(config)) as client:
        owner, workspace_id = _bootstrap(client)
        proposal = _owner_proposal(client, workspace_id, desired)

    connection = sqlite3.connect(config.storage.database_path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="proposal_binding_invalid"):
            connection.execute(
                "INSERT INTO workspace_composition_proposal "
                "(id, workspace_id, owner_id, base_revision, base_profile_sha256, source_kind, "
                "source_reference, desired_profile_json, desired_profile_sha256, "
                "request_sha256, created_at) VALUES (?, ?, ?, 1, ?, 'owner', NULL, ?, ?, ?, ?)",
                (
                    "proposal_" + "e" * 32,
                    workspace_id,
                    owner["id"],
                    "f" * 64,
                    json.dumps(desired, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    proposal["desired_profile_sha256"],
                    "e" * 64,
                    proposal["created_at"],
                ),
            )
        with pytest.raises(sqlite3.IntegrityError, match="proposal_binding_invalid"):
            connection.execute(
                "INSERT INTO workspace_composition_revision "
                "(workspace_id, owner_id, revision, template_id, template_version, profile_json, "
                "profile_sha256, source_kind, proposal_id, created_at) "
                "VALUES (?, ?, 2, 'standard-workbench', 1, ?, ?, 'system', NULL, ?)",
                (
                    workspace_id,
                    owner["id"],
                    json.dumps(desired, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    proposal["desired_profile_sha256"],
                    proposal["created_at"],
                ),
            )
        connection.execute(
            "INSERT INTO workspace_composition_revision "
            "(workspace_id, owner_id, revision, template_id, template_version, profile_json, "
            "profile_sha256, source_kind, proposal_id, created_at) "
            "VALUES (?, ?, 2, 'standard-workbench', 1, ?, ?, 'owner', ?, ?)",
            (
                workspace_id,
                owner["id"],
                json.dumps(desired, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                proposal["desired_profile_sha256"],
                proposal["id"],
                proposal["created_at"],
            ),
        )
        with pytest.raises(sqlite3.IntegrityError, match="current_identity_drift"):
            connection.execute(
                "UPDATE workspace_composition_current SET revision = 2, profile_sha256 = ? "
                "WHERE workspace_id = ?",
                ("0" * 64, workspace_id),
            )
        with pytest.raises(sqlite3.IntegrityError, match="current_identity_drift"):
            connection.execute(
                "UPDATE workspace_composition_current SET revision = 2, profile_sha256 = ? "
                "WHERE workspace_id = ?",
                (proposal["desired_profile_sha256"], workspace_id),
            )
        with pytest.raises(sqlite3.IntegrityError, match="decision_binding_invalid"):
            connection.execute(
                "INSERT INTO workspace_composition_decision "
                "(proposal_id, workspace_id, decision, request_sha256, decided_by, "
                "applied_revision, decided_at) VALUES (?, ?, 'approved', ?, 'owner', 2, ?)",
                (proposal["id"], workspace_id, "0" * 64, proposal["created_at"]),
            )
    finally:
        connection.close()


def test_unavailable_slots_archived_workspaces_and_cross_workspace_decisions_fail_closed(
    tmp_path: Path,
) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        _, first_id = _bootstrap(client, "First")
        second = client.post(
            "/desktop/v1/workspaces",
            headers=_headers(),
            json={"name": "Second"},
        ).json()["workspace"]
        second_id = str(second["id"])
        current = _composition(client, first_id)["profile"]
        invalid = _desired_profile()
        slots = invalid["slots"]
        assert isinstance(slots, dict)
        slots["terminal"] = True
        unavailable = client.post(
            f"/desktop/v1/workspaces/{first_id}/composition/proposals",
            headers=_headers(),
            json={
                "expected_revision": current["revision"],
                "expected_profile_sha256": current["profile_sha256"],
                "desired_profile": invalid,
            },
        )
        proposal = _owner_proposal(
            client, first_id, _desired_profile(appearance__density="comfortable")
        )
        cross = client.post(
            f"/desktop/v1/workspaces/{second_id}/composition/proposals/"
            f"{proposal['id']}/decision",
            headers=_headers(),
            json={"decision": "approve", "request_sha256": proposal["request_sha256"]},
        )
        archived = client.post(
            f"/desktop/v1/workspaces/{first_id}/archive",
            headers=_headers(),
            json={"expected_row_version": 1},
        )
        after_archive = client.get(
            f"/desktop/v1/workspaces/{first_id}/composition", headers=_headers()
        )

    assert unavailable.status_code == 409
    assert unavailable.json()["error"]["code"] == "desktop_composition_capability_unavailable"
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "desktop_composition_proposal_not_found"
    assert archived.status_code == 200
    assert after_archive.status_code == 409
    assert after_archive.json()["error"]["code"] == "desktop_workspace_archived"


@pytest.mark.parametrize(
    ("sidebar", "disabled_slot"),
    [("run", "run.history"), ("blackboard", "workspace.brief")],
)
def test_sidebar_layout_requires_its_admitted_slot(
    tmp_path: Path, sidebar: str, disabled_slot: str
) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        _, workspace_id = _bootstrap(client)
        current = _composition(client, workspace_id)["profile"]
        invalid = _desired_profile(layout__sidebar=sidebar)
        slots = invalid["slots"]
        assert isinstance(slots, dict)
        slots[disabled_slot] = False
        response = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/composition/proposals",
            headers=_headers(),
            json={
                "expected_revision": current["revision"],
                "expected_profile_sha256": current["profile_sha256"],
                "desired_profile": invalid,
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "desktop_composition_layout_invalid"


def test_completed_assistant_message_can_propose_but_cannot_apply_itself(tmp_path: Path) -> None:
    config = _config(tmp_path)
    desired = _desired_profile(appearance__density="comfortable")
    with TestClient(create_desktop_local_app(config)) as client:
        owner, workspace_id = _bootstrap(client)
        conversation = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/conversations",
            headers=_headers(),
            json={"title": "Composition"},
        ).json()["conversation"]
        message_id = "message_" + "d" * 32
        connection = sqlite3.connect(config.storage.database_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            _insert_assistant_message(
                connection,
                owner_id=str(owner["id"]),
                workspace_id=workspace_id,
                conversation_id=str(conversation["id"]),
                message_id=message_id,
                content=json.dumps(
                    {
                        "type": "omnibase.workspace-composition.proposal.v1",
                        "desired_profile": desired,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                timestamp=str(conversation["created_at"]),
            )
            connection.commit()
        finally:
            connection.close()
        current = _composition(client, workspace_id)["profile"]
        proposed = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/composition/proposals/from-assistant",
            headers=_headers(),
            json={
                "expected_revision": current["revision"],
                "expected_profile_sha256": current["profile_sha256"],
                "message_id": message_id,
            },
        )
        unchanged = _composition(client, workspace_id)

    assert proposed.status_code == 200
    assert proposed.json()["proposal"]["source_kind"] == "assistant"
    assert proposed.json()["proposal"]["source_reference"] == message_id
    assert unchanged["profile"]["revision"] == 1
    assert unchanged["proposals"][0]["decision"] is None


@pytest.mark.parametrize(
    ("field", "invalid_value", "expected_code"),
    [
        ("schema_version", 1.0, "desktop_composition_profile_invalid"),
        ("schema_version", True, "desktop_composition_profile_invalid"),
        ("template.version", 1.0, "desktop_composition_template_conflict"),
        ("template.version", True, "desktop_composition_template_conflict"),
    ],
)
def test_profile_versions_require_exact_integers_without_persisting_proposals(
    tmp_path: Path,
    field: str,
    invalid_value: object,
    expected_code: str,
) -> None:
    config = _config(tmp_path)
    invalid = _desired_profile(appearance__density="comfortable")
    if field == "schema_version":
        invalid["schema_version"] = invalid_value
    else:
        template = invalid["template"]
        assert isinstance(template, dict)
        template["version"] = invalid_value

    with TestClient(create_desktop_local_app(config)) as client:
        owner, workspace_id = _bootstrap(client)
        conversation = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/conversations",
            headers=_headers(),
            json={"title": "Hostile composition version"},
        ).json()["conversation"]
        current = _composition(client, workspace_id)["profile"]
        owner_response = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/composition/proposals",
            headers=_headers(),
            json={
                "expected_revision": current["revision"],
                "expected_profile_sha256": current["profile_sha256"],
                "desired_profile": invalid,
            },
        )

        message_id = "message_" + ("1" if field == "schema_version" else "2") * 32
        if invalid_value is True:
            message_id = "message_" + ("3" if field == "schema_version" else "4") * 32
        connection = sqlite3.connect(config.storage.database_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            _insert_assistant_message(
                connection,
                owner_id=str(owner["id"]),
                workspace_id=workspace_id,
                conversation_id=str(conversation["id"]),
                message_id=message_id,
                content=json.dumps(
                    {
                        "type": "omnibase.workspace-composition.proposal.v1",
                        "desired_profile": invalid,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                timestamp=str(conversation["created_at"]),
            )
            connection.commit()
        finally:
            connection.close()

        assistant_response = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/composition/proposals/from-assistant",
            headers=_headers(),
            json={
                "expected_revision": current["revision"],
                "expected_profile_sha256": current["profile_sha256"],
                "message_id": message_id,
            },
        )
        unchanged = _composition(client, workspace_id)

    assert owner_response.status_code in {400, 409}
    assert owner_response.json()["error"]["code"] == expected_code
    assert assistant_response.status_code in {400, 409}
    assert assistant_response.json()["error"]["code"] == expected_code
    assert unchanged["profile"]["revision"] == 1
    assert unchanged["proposals"] == []
    assert unchanged["audit"] == []


def test_assistant_proposal_requires_a_succeeded_same_scope_invocation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    desired = _desired_profile(appearance__density="comfortable")
    envelope = json.dumps(
        {"type": "omnibase.workspace-composition.proposal.v1", "desired_profile": desired},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with TestClient(create_desktop_local_app(config)) as client:
        owner, workspace_id = _bootstrap(client)
        conversation = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/conversations",
            headers=_headers(),
            json={"title": "Composition identity"},
        ).json()["conversation"]
        connection = sqlite3.connect(config.storage.database_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            failed_message_id = "message_" + "e" * 32
            _insert_assistant_message(
                connection,
                owner_id=str(owner["id"]),
                workspace_id=workspace_id,
                conversation_id=str(conversation["id"]),
                message_id=failed_message_id,
                content=envelope,
                timestamp=str(conversation["created_at"]),
                invocation_status="failed",
            )
            orphan_message_id = "message_" + "f" * 32
            connection.execute(
                "INSERT INTO message "
                "(id, owner_id, workspace_id, conversation_id, role, content, status, "
                "invocation_id, retry_of_message_id, created_at) "
                "VALUES (?, ?, ?, ?, 'assistant', ?, 'completed', NULL, NULL, ?)",
                (
                    orphan_message_id,
                    owner["id"],
                    workspace_id,
                    conversation["id"],
                    envelope,
                    conversation["created_at"],
                ),
            )
            connection.commit()
        finally:
            connection.close()
        current = _composition(client, workspace_id)["profile"]
        responses = [
            client.post(
                f"/desktop/v1/workspaces/{workspace_id}/composition/proposals/from-assistant",
                headers=_headers(),
                json={
                    "expected_revision": current["revision"],
                    "expected_profile_sha256": current["profile_sha256"],
                    "message_id": message_id,
                },
            )
            for message_id in (failed_message_id, orphan_message_id)
        ]

    assert [response.status_code for response in responses] == [409, 409]
    assert {response.json()["error"]["code"] for response in responses} == {
        "desktop_composition_assistant_reference_invalid"
    }


def test_rollback_is_a_reviewed_new_revision_and_preserves_history(tmp_path: Path) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        _, workspace_id = _bootstrap(client)
        changed = _owner_proposal(
            client, workspace_id, _desired_profile(appearance__density="comfortable")
        )
        _approve(client, workspace_id, changed)
        current = _composition(client, workspace_id)["profile"]
        rollback = client.post(
            f"/desktop/v1/workspaces/{workspace_id}/composition/proposals/rollback",
            headers=_headers(),
            json={
                "expected_revision": current["revision"],
                "expected_profile_sha256": current["profile_sha256"],
                "target_revision": 1,
            },
        )
        assert rollback.status_code == 200
        rollback_proposal = rollback.json()["proposal"]
        assert _composition(client, workspace_id)["profile"]["revision"] == 2
        applied = _approve(client, workspace_id, rollback_proposal)
        composition = _composition(client, workspace_id)

    assert rollback_proposal["source_kind"] == "rollback"
    assert applied["applied_revision"] == 3
    assert composition["profile"]["value"] == STANDARD_WORKBENCH_PROFILE
    assert [row["revision"] for row in composition["revisions"]] == [3, 2, 1]
