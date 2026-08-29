"""Fail-closed desktop-local workbench preferences and Workspace composition."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Mapping
from typing import cast

from omnibase.desktop_local.database import utc_now_text
from omnibase.desktop_local.providers import DesktopApiError
from omnibase.desktop_local.repository import append_audit_event
from omnibase.desktop_local.schema import (
    STANDARD_WORKBENCH_PROFILE_JSON,
    STANDARD_WORKBENCH_PROFILE_SHA256,
)

_WORKSPACE_ID_PATTERN = re.compile(r"workspace_[0-9a-f]{32}\Z")
_MESSAGE_ID_PATTERN = re.compile(r"message_[0-9a-f]{32}\Z")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_PROFILE_KEYS = frozenset({"appearance", "layout", "schema_version", "slots", "template"})
_APPEARANCE_KEYS = frozenset({"density", "quiet_chrome"})
_LAYOUT_KEYS = frozenset({"agent_panel", "bottom_panel", "focus_mode", "sidebar"})
_TEMPLATE_KEYS = frozenset({"id", "version"})
_SLOT_KEYS = frozenset(
    {
        "agent.rail",
        "conversation.transcript",
        "event.agent-log",
        "event.output",
        "knowledge.ebook",
        "mcp.catalog",
        "provider.settings",
        "run.history",
        "sandbox.runtime",
        "settings.center",
        "skills.catalog",
        "source-control",
        "terminal",
        "workspace.brief",
        "workspace.explorer",
    }
)
_LOCKED_ENABLED_SLOTS = frozenset({"conversation.transcript", "settings.center"})
_UNAVAILABLE_SLOTS = frozenset(
    {
        "knowledge.ebook",
        "mcp.catalog",
        "sandbox.runtime",
        "skills.catalog",
        "source-control",
        "terminal",
    }
)
_SLOT_CATALOG = (
    ("workspace.explorer", "资源管理器", "sidebar", "admitted"),
    ("conversation.transcript", "会话记录", "editor", "required"),
    ("workspace.brief", "任务简报", "editor", "admitted"),
    ("agent.rail", "Agent 面板", "right", "admitted"),
    ("run.history", "运行历史", "sidebar", "admitted"),
    ("provider.settings", "Provider 设置", "settings", "admitted"),
    ("event.output", "输出", "bottom", "admitted"),
    ("event.agent-log", "Agent Log", "bottom", "admitted"),
    ("settings.center", "设置中心", "editor", "required"),
    ("knowledge.ebook", "知识电子书", "editor", "unavailable"),
    ("terminal", "终端", "bottom", "unavailable"),
    ("source-control", "源码管理", "sidebar", "unavailable"),
    ("mcp.catalog", "MCP", "settings", "unavailable"),
    ("skills.catalog", "Skills", "settings", "unavailable"),
    ("sandbox.runtime", "沙箱", "settings", "unavailable"),
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _exact_mapping(value: object, keys: frozenset[str], code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise DesktopApiError(400, code)
    return cast(Mapping[str, object], value)


def validate_workspace_profile(  # noqa: C901 - the complete closed profile is validated in one gate
    value: object,
) -> dict[str, object]:
    """Return a canonical closed profile or reject the complete value."""

    profile = _exact_mapping(value, _PROFILE_KEYS, "desktop_composition_profile_invalid")
    if type(profile["schema_version"]) is not int or profile["schema_version"] != 1:
        raise DesktopApiError(400, "desktop_composition_profile_invalid")

    template = _exact_mapping(
        profile["template"], _TEMPLATE_KEYS, "desktop_composition_template_invalid"
    )
    if (
        template["id"] != "standard-workbench"
        or type(template["version"]) is not int
        or template["version"] != 1
    ):
        raise DesktopApiError(409, "desktop_composition_template_conflict")

    appearance = _exact_mapping(
        profile["appearance"], _APPEARANCE_KEYS, "desktop_composition_appearance_invalid"
    )
    if appearance["density"] not in {"inherit", "compact", "comfortable"}:
        raise DesktopApiError(400, "desktop_composition_appearance_invalid")
    if type(appearance["quiet_chrome"]) is not bool:
        raise DesktopApiError(400, "desktop_composition_appearance_invalid")

    layout = _exact_mapping(profile["layout"], _LAYOUT_KEYS, "desktop_composition_layout_invalid")
    if layout["agent_panel"] not in {"open", "closed"}:
        raise DesktopApiError(400, "desktop_composition_layout_invalid")
    if layout["bottom_panel"] not in {"hidden", "output", "agent-log"}:
        raise DesktopApiError(400, "desktop_composition_layout_invalid")
    if layout["sidebar"] not in {"explorer", "run", "blackboard", "hidden"}:
        raise DesktopApiError(400, "desktop_composition_layout_invalid")
    if type(layout["focus_mode"]) is not bool:
        raise DesktopApiError(400, "desktop_composition_layout_invalid")

    slots = _exact_mapping(profile["slots"], _SLOT_KEYS, "desktop_composition_slots_invalid")
    if any(type(slots[key]) is not bool for key in _SLOT_KEYS):
        raise DesktopApiError(400, "desktop_composition_slots_invalid")
    if any(slots[key] is not True for key in _LOCKED_ENABLED_SLOTS):
        raise DesktopApiError(409, "desktop_composition_required_slot_conflict")
    if any(slots[key] is not False for key in _UNAVAILABLE_SLOTS):
        raise DesktopApiError(409, "desktop_composition_capability_unavailable")
    if slots["agent.rail"] is False and layout["agent_panel"] != "closed":
        raise DesktopApiError(400, "desktop_composition_layout_invalid")
    if slots["workspace.explorer"] is False and layout["sidebar"] == "explorer":
        raise DesktopApiError(400, "desktop_composition_layout_invalid")
    if slots["run.history"] is False and layout["sidebar"] == "run":
        raise DesktopApiError(400, "desktop_composition_layout_invalid")
    if slots["workspace.brief"] is False and layout["sidebar"] == "blackboard":
        raise DesktopApiError(400, "desktop_composition_layout_invalid")
    if layout["bottom_panel"] == "output" and slots["event.output"] is False:
        raise DesktopApiError(400, "desktop_composition_layout_invalid")
    if layout["bottom_panel"] == "agent-log" and slots["event.agent-log"] is False:
        raise DesktopApiError(400, "desktop_composition_layout_invalid")

    return cast(dict[str, object], json.loads(_canonical_json(profile)))


def _profile_material(value: object) -> tuple[dict[str, object], str, str]:
    profile = validate_workspace_profile(value)
    profile_json = _canonical_json(profile)
    return profile, profile_json, _sha256_text(profile_json)


def initialize_owner_preference(
    connection: sqlite3.Connection, owner_id: str, *, timestamp: str | None = None
) -> None:
    now = timestamp or utc_now_text()
    connection.execute(
        "INSERT INTO owner_workbench_preference "
        "(owner_id, density, reduce_motion, row_version, created_at, updated_at) "
        "VALUES (?, 'compact', 0, 1, ?, ?)",
        (owner_id, now, now),
    )


def initialize_workspace_composition(
    connection: sqlite3.Connection,
    owner_id: str,
    workspace_id: str,
    *,
    timestamp: str | None = None,
) -> None:
    now = timestamp or utc_now_text()
    connection.execute(
        "INSERT INTO workspace_composition_revision "
        "(workspace_id, owner_id, revision, template_id, template_version, profile_json, "
        "profile_sha256, source_kind, proposal_id, created_at) "
        "VALUES (?, ?, 1, 'standard-workbench', 1, ?, ?, 'system', NULL, ?)",
        (
            workspace_id,
            owner_id,
            STANDARD_WORKBENCH_PROFILE_JSON,
            STANDARD_WORKBENCH_PROFILE_SHA256,
            now,
        ),
    )
    connection.execute(
        "INSERT INTO workspace_composition_current "
        "(workspace_id, owner_id, revision, profile_sha256, created_at, updated_at) "
        "VALUES (?, ?, 1, ?, ?, ?)",
        (workspace_id, owner_id, STANDARD_WORKBENCH_PROFILE_SHA256, now, now),
    )


def _live_owner(connection: sqlite3.Connection) -> sqlite3.Row:
    owner = connection.execute("SELECT id FROM owner WHERE singleton_key = 1").fetchone()
    if owner is None:
        raise DesktopApiError(409, "desktop_owner_not_initialized")
    return owner


def _live_workspace(connection: sqlite3.Connection, workspace_id: str) -> sqlite3.Row:
    if not _WORKSPACE_ID_PATTERN.fullmatch(workspace_id):
        raise DesktopApiError(404, "desktop_workspace_not_found")
    owner = _live_owner(connection)
    workspace = connection.execute(
        "SELECT id, owner_id, state FROM workspace WHERE id = ? AND owner_id = ?",
        (workspace_id, owner["id"]),
    ).fetchone()
    if workspace is None:
        raise DesktopApiError(404, "desktop_workspace_not_found")
    if workspace["state"] != "active":
        raise DesktopApiError(409, "desktop_workspace_archived")
    return workspace


def _preference_payload(row: sqlite3.Row) -> dict[str, object]:
    return {
        "density": str(row["density"]),
        "reduce_motion": bool(row["reduce_motion"]),
        "row_version": int(row["row_version"]),
        "updated_at": str(row["updated_at"]),
    }


def get_application_preference(connection: sqlite3.Connection) -> dict[str, object]:
    owner = _live_owner(connection)
    row = connection.execute(
        "SELECT density, reduce_motion, row_version, updated_at "
        "FROM owner_workbench_preference WHERE owner_id = ?",
        (owner["id"],),
    ).fetchone()
    if row is None:
        raise DesktopApiError(503, "desktop_workbench_preference_unavailable")
    return {"preference": _preference_payload(row)}


def update_application_preference(
    connection: sqlite3.Connection,
    *,
    density: str,
    reduce_motion: bool,
    expected_row_version: int,
) -> dict[str, object]:
    if density not in {"compact", "comfortable"} or type(reduce_motion) is not bool:
        raise DesktopApiError(400, "desktop_workbench_preference_invalid")
    try:
        connection.execute("BEGIN IMMEDIATE")
        owner = _live_owner(connection)
        current = connection.execute(
            "SELECT density, reduce_motion, row_version, updated_at "
            "FROM owner_workbench_preference WHERE owner_id = ?",
            (owner["id"],),
        ).fetchone()
        if current is None:
            raise DesktopApiError(503, "desktop_workbench_preference_unavailable")
        if int(current["row_version"]) != expected_row_version:
            raise DesktopApiError(409, "desktop_workbench_preference_version_conflict")
        if str(current["density"]) == density and bool(current["reduce_motion"]) is reduce_motion:
            connection.execute("COMMIT")
            return {"preference": _preference_payload(current)}
        now = utc_now_text()
        updated = connection.execute(
            "UPDATE owner_workbench_preference "
            "SET density = ?, reduce_motion = ?, row_version = row_version + 1, updated_at = ? "
            "WHERE owner_id = ? AND row_version = ? "
            "RETURNING density, reduce_motion, row_version, updated_at",
            (density, int(reduce_motion), now, owner["id"], expected_row_version),
        ).fetchone()
        if updated is None:
            raise DesktopApiError(409, "desktop_workbench_preference_version_conflict")
        append_audit_event(
            connection,
            event_id=f"event_{uuid.uuid4().hex}",
            owner_id=str(owner["id"]),
            event_type="workbench_preference_updated",
            payload={
                "density": density,
                "reduce_motion": reduce_motion,
                "row_version": int(updated["row_version"]),
            },
        )
        connection.execute("COMMIT")
        return {"preference": _preference_payload(updated)}
    except DesktopApiError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopApiError(503, "desktop_workbench_preference_update_failed") from None


def _revision_payload(row: sqlite3.Row) -> dict[str, object]:
    return {
        "workspace_id": str(row["workspace_id"]),
        "revision": int(row["revision"]),
        "profile_sha256": str(row["profile_sha256"]),
        "source_kind": str(row["source_kind"]),
        "proposal_id": None if row["proposal_id"] is None else str(row["proposal_id"]),
        "value": json.loads(str(row["profile_json"])),
        "created_at": str(row["created_at"]),
    }


def _proposal_payload(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "workspace_id": str(row["workspace_id"]),
        "base_revision": int(row["base_revision"]),
        "base_profile_sha256": str(row["base_profile_sha256"]),
        "source_kind": str(row["source_kind"]),
        "source_reference": (
            None if row["source_reference"] is None else str(row["source_reference"])
        ),
        "desired_profile_sha256": str(row["desired_profile_sha256"]),
        "request_sha256": str(row["request_sha256"]),
        "desired_profile": json.loads(str(row["desired_profile_json"])),
        "decision": None if row["decision"] is None else str(row["decision"]),
        "applied_revision": (
            None if row["applied_revision"] is None else int(row["applied_revision"])
        ),
        "created_at": str(row["created_at"]),
        "decided_at": None if row["decided_at"] is None else str(row["decided_at"]),
    }


def _slot_catalog_payload() -> list[dict[str, object]]:
    return [
        {"id": slot_id, "label": label, "region": region, "posture": posture}
        for slot_id, label, region, posture in _SLOT_CATALOG
    ]


def get_workspace_composition(
    connection: sqlite3.Connection, workspace_id: str
) -> dict[str, object]:
    workspace = _live_workspace(connection, workspace_id)
    current = connection.execute(
        "SELECT revision.workspace_id, revision.revision, revision.profile_sha256, "
        "revision.source_kind, revision.proposal_id, revision.profile_json, revision.created_at "
        "FROM workspace_composition_current AS current "
        "JOIN workspace_composition_revision AS revision "
        "ON revision.workspace_id = current.workspace_id "
        "AND revision.revision = current.revision "
        "AND revision.profile_sha256 = current.profile_sha256 "
        "WHERE current.workspace_id = ? AND current.owner_id = ?",
        (workspace_id, workspace["owner_id"]),
    ).fetchone()
    if current is None:
        raise DesktopApiError(503, "desktop_composition_unavailable")
    revisions = connection.execute(
        "SELECT workspace_id, revision, profile_sha256, source_kind, proposal_id, "
        "profile_json, created_at FROM workspace_composition_revision "
        "WHERE workspace_id = ? AND owner_id = ? ORDER BY revision DESC LIMIT 25",
        (workspace_id, workspace["owner_id"]),
    ).fetchall()
    proposals = connection.execute(
        "SELECT proposal.id, proposal.workspace_id, proposal.base_revision, "
        "proposal.base_profile_sha256, proposal.source_kind, proposal.source_reference, "
        "proposal.desired_profile_json, proposal.desired_profile_sha256, "
        "proposal.request_sha256, proposal.created_at, decision.decision, "
        "decision.applied_revision, decision.decided_at "
        "FROM workspace_composition_proposal AS proposal "
        "LEFT JOIN workspace_composition_decision AS decision "
        "ON decision.proposal_id = proposal.id AND decision.workspace_id = proposal.workspace_id "
        "WHERE proposal.workspace_id = ? AND proposal.owner_id = ? "
        "ORDER BY proposal.created_at DESC, proposal.id DESC LIMIT 25",
        (workspace_id, workspace["owner_id"]),
    ).fetchall()
    audit_rows = connection.execute(
        "SELECT sequence, event_type, payload_json, created_at FROM audit_event "
        "WHERE workspace_id = ? AND event_type GLOB 'workspace_composition_*' "
        "ORDER BY sequence DESC LIMIT 50",
        (workspace_id,),
    ).fetchall()
    return {
        "profile": _revision_payload(current),
        "revisions": [_revision_payload(row) for row in revisions],
        "proposals": [_proposal_payload(row) for row in proposals],
        "slot_catalog": _slot_catalog_payload(),
        "audit": [
            {
                "sequence": int(row["sequence"]),
                "event_type": str(row["event_type"]),
                "payload": json.loads(str(row["payload_json"])),
                "created_at": str(row["created_at"]),
            }
            for row in audit_rows
        ],
    }


def _request_sha256(
    *,
    workspace_id: str,
    base_revision: int,
    base_profile_sha256: str,
    source_kind: str,
    source_reference: str | None,
    desired_profile_sha256: str,
) -> str:
    return _sha256_text(
        _canonical_json(
            {
                "base_profile_sha256": base_profile_sha256,
                "base_revision": base_revision,
                "desired_profile_sha256": desired_profile_sha256,
                "schema_version": 1,
                "source_kind": source_kind,
                "source_reference": source_reference,
                "template": {"id": "standard-workbench", "version": 1},
                "workspace_id": workspace_id,
            }
        )
    )


def _extract_assistant_profile(content: str) -> dict[str, object]:
    text = content.strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[8:-4].strip()
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        raise DesktopApiError(409, "desktop_composition_assistant_payload_invalid") from None
    envelope = _exact_mapping(
        value,
        frozenset({"desired_profile", "type"}),
        "desktop_composition_assistant_payload_invalid",
    )
    if envelope["type"] != "omnibase.workspace-composition.proposal.v1":
        raise DesktopApiError(409, "desktop_composition_assistant_payload_invalid")
    return validate_workspace_profile(envelope["desired_profile"])


def _current_profile_row(connection: sqlite3.Connection, workspace_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT current.workspace_id, current.owner_id, current.revision, "
        "current.profile_sha256, revision.profile_json "
        "FROM workspace_composition_current AS current "
        "JOIN workspace_composition_revision AS revision "
        "ON revision.workspace_id = current.workspace_id AND revision.revision = current.revision "
        "WHERE current.workspace_id = ?",
        (workspace_id,),
    ).fetchone()
    if row is None:
        raise DesktopApiError(503, "desktop_composition_unavailable")
    return row


def _insert_proposal(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    expected_revision: int,
    expected_profile_sha256: str,
    source_kind: str,
    source_reference: str | None,
    desired_profile: object,
) -> dict[str, object]:
    if not _SHA256_PATTERN.fullmatch(expected_profile_sha256):
        raise DesktopApiError(400, "desktop_composition_digest_invalid")
    profile, profile_json, desired_sha256 = _profile_material(desired_profile)
    request_sha256 = _request_sha256(
        workspace_id=workspace_id,
        base_revision=expected_revision,
        base_profile_sha256=expected_profile_sha256,
        source_kind=source_kind,
        source_reference=source_reference,
        desired_profile_sha256=desired_sha256,
    )
    try:
        connection.execute("BEGIN IMMEDIATE")
        workspace = _live_workspace(connection, workspace_id)
        current = _current_profile_row(connection, workspace_id)
        if (
            int(current["revision"]) != expected_revision
            or str(current["profile_sha256"]) != expected_profile_sha256
        ):
            raise DesktopApiError(409, "desktop_composition_version_conflict")
        if desired_sha256 == expected_profile_sha256:
            raise DesktopApiError(409, "desktop_composition_no_change")
        existing = connection.execute(
            "SELECT proposal.id, proposal.workspace_id, proposal.base_revision, "
            "proposal.base_profile_sha256, proposal.source_kind, proposal.source_reference, "
            "proposal.desired_profile_json, proposal.desired_profile_sha256, "
            "proposal.request_sha256, proposal.created_at, decision.decision, "
            "decision.applied_revision, decision.decided_at "
            "FROM workspace_composition_proposal AS proposal "
            "LEFT JOIN workspace_composition_decision AS decision "
            "ON decision.proposal_id = proposal.id "
            "WHERE proposal.request_sha256 = ?",
            (request_sha256,),
        ).fetchone()
        if existing is not None:
            connection.execute("COMMIT")
            return {"proposal": _proposal_payload(existing), "replayed": True}
        proposal_id = f"proposal_{uuid.uuid4().hex}"
        now = utc_now_text()
        connection.execute(
            "INSERT INTO workspace_composition_proposal "
            "(id, workspace_id, owner_id, base_revision, base_profile_sha256, source_kind, "
            "source_reference, desired_profile_json, desired_profile_sha256, request_sha256, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                proposal_id,
                workspace_id,
                workspace["owner_id"],
                expected_revision,
                expected_profile_sha256,
                source_kind,
                source_reference,
                profile_json,
                desired_sha256,
                request_sha256,
                now,
            ),
        )
        append_audit_event(
            connection,
            event_id=f"event_{uuid.uuid4().hex}",
            owner_id=str(workspace["owner_id"]),
            workspace_id=workspace_id,
            event_type="workspace_composition_proposed",
            payload={
                "base_revision": expected_revision,
                "desired_profile_sha256": desired_sha256,
                "proposal_id": proposal_id,
                "request_sha256": request_sha256,
                "source_kind": source_kind,
            },
        )
        connection.execute("COMMIT")
        return {
            "proposal": {
                "id": proposal_id,
                "workspace_id": workspace_id,
                "base_revision": expected_revision,
                "base_profile_sha256": expected_profile_sha256,
                "source_kind": source_kind,
                "source_reference": source_reference,
                "desired_profile_sha256": desired_sha256,
                "request_sha256": request_sha256,
                "desired_profile": profile,
                "decision": None,
                "applied_revision": None,
                "created_at": now,
                "decided_at": None,
            },
            "replayed": False,
        }
    except DesktopApiError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopApiError(503, "desktop_composition_proposal_failed") from None


def create_owner_proposal(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    expected_revision: int,
    expected_profile_sha256: str,
    desired_profile: object,
) -> dict[str, object]:
    return _insert_proposal(
        connection,
        workspace_id=workspace_id,
        expected_revision=expected_revision,
        expected_profile_sha256=expected_profile_sha256,
        source_kind="owner",
        source_reference=None,
        desired_profile=desired_profile,
    )


def create_assistant_proposal(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    expected_revision: int,
    expected_profile_sha256: str,
    message_id: str,
) -> dict[str, object]:
    if not _MESSAGE_ID_PATTERN.fullmatch(message_id):
        raise DesktopApiError(400, "desktop_composition_assistant_reference_invalid")
    workspace = _live_workspace(connection, workspace_id)
    row = connection.execute(
        "SELECT message.content FROM message "
        "JOIN invocation ON invocation.id = message.invocation_id "
        "AND invocation.owner_id = message.owner_id "
        "AND invocation.workspace_id = message.workspace_id "
        "AND invocation.conversation_id = message.conversation_id "
        "WHERE message.id = ? AND message.workspace_id = ? AND message.owner_id = ? "
        "AND message.role = 'assistant' AND message.status = 'completed' "
        "AND invocation.status = 'succeeded'",
        (message_id, workspace_id, workspace["owner_id"]),
    ).fetchone()
    if row is None:
        raise DesktopApiError(409, "desktop_composition_assistant_reference_invalid")
    desired_profile = _extract_assistant_profile(str(row["content"]))
    return _insert_proposal(
        connection,
        workspace_id=workspace_id,
        expected_revision=expected_revision,
        expected_profile_sha256=expected_profile_sha256,
        source_kind="assistant",
        source_reference=message_id,
        desired_profile=desired_profile,
    )


def create_rollback_proposal(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    expected_revision: int,
    expected_profile_sha256: str,
    target_revision: int,
) -> dict[str, object]:
    workspace = _live_workspace(connection, workspace_id)
    target = connection.execute(
        "SELECT profile_json FROM workspace_composition_revision "
        "WHERE workspace_id = ? AND owner_id = ? AND revision = ?",
        (workspace_id, workspace["owner_id"], target_revision),
    ).fetchone()
    if target is None or target_revision >= expected_revision:
        raise DesktopApiError(409, "desktop_composition_rollback_target_invalid")
    return _insert_proposal(
        connection,
        workspace_id=workspace_id,
        expected_revision=expected_revision,
        expected_profile_sha256=expected_profile_sha256,
        source_kind="rollback",
        source_reference=f"revision:{target_revision}",
        desired_profile=json.loads(str(target["profile_json"])),
    )


def decide_workspace_proposal(  # noqa: C901 - decision, revision CAS and audit share one transaction
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    proposal_id: str,
    request_sha256: str,
    decision: str,
) -> dict[str, object]:
    if decision not in {"approve", "reject"} or not _SHA256_PATTERN.fullmatch(request_sha256):
        raise DesktopApiError(400, "desktop_composition_decision_invalid")
    try:
        connection.execute("BEGIN IMMEDIATE")
        workspace = _live_workspace(connection, workspace_id)
        proposal = connection.execute(
            "SELECT * FROM workspace_composition_proposal "
            "WHERE id = ? AND workspace_id = ? AND owner_id = ?",
            (proposal_id, workspace_id, workspace["owner_id"]),
        ).fetchone()
        if proposal is None:
            raise DesktopApiError(404, "desktop_composition_proposal_not_found")
        if str(proposal["request_sha256"]) != request_sha256:
            raise DesktopApiError(409, "desktop_composition_digest_conflict")
        decided = connection.execute(
            "SELECT decision FROM workspace_composition_decision WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        if decided is not None:
            raise DesktopApiError(409, "desktop_composition_proposal_decided")
        now = utc_now_text()
        if decision == "reject":
            connection.execute(
                "INSERT INTO workspace_composition_decision "
                "(proposal_id, workspace_id, decision, request_sha256, decided_by, "
                "applied_revision, decided_at) VALUES (?, ?, 'rejected', ?, 'owner', NULL, ?)",
                (proposal_id, workspace_id, request_sha256, now),
            )
            append_audit_event(
                connection,
                event_id=f"event_{uuid.uuid4().hex}",
                owner_id=str(workspace["owner_id"]),
                workspace_id=workspace_id,
                event_type="workspace_composition_rejected",
                payload={"proposal_id": proposal_id, "request_sha256": request_sha256},
            )
            connection.execute("COMMIT")
            return {
                "workspace_id": workspace_id,
                "proposal_id": proposal_id,
                "request_sha256": request_sha256,
                "decision": "rejected",
                "applied_revision": None,
            }

        current = _current_profile_row(connection, workspace_id)
        if int(current["revision"]) != int(proposal["base_revision"]) or str(
            current["profile_sha256"]
        ) != str(proposal["base_profile_sha256"]):
            raise DesktopApiError(409, "desktop_composition_version_conflict")
        desired_profile, desired_json, desired_sha256 = _profile_material(
            json.loads(str(proposal["desired_profile_json"]))
        )
        if desired_sha256 != str(proposal["desired_profile_sha256"]):
            raise DesktopApiError(409, "desktop_composition_digest_conflict")
        expected_request_sha256 = _request_sha256(
            workspace_id=workspace_id,
            base_revision=int(proposal["base_revision"]),
            base_profile_sha256=str(proposal["base_profile_sha256"]),
            source_kind=str(proposal["source_kind"]),
            source_reference=(
                None if proposal["source_reference"] is None else str(proposal["source_reference"])
            ),
            desired_profile_sha256=desired_sha256,
        )
        if expected_request_sha256 != request_sha256:
            raise DesktopApiError(409, "desktop_composition_digest_conflict")
        new_revision = int(current["revision"]) + 1
        connection.execute(
            "INSERT INTO workspace_composition_revision "
            "(workspace_id, owner_id, revision, template_id, template_version, profile_json, "
            "profile_sha256, source_kind, proposal_id, created_at) "
            "VALUES (?, ?, ?, 'standard-workbench', 1, ?, ?, ?, ?, ?)",
            (
                workspace_id,
                workspace["owner_id"],
                new_revision,
                desired_json,
                desired_sha256,
                proposal["source_kind"],
                proposal_id,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO workspace_composition_decision "
            "(proposal_id, workspace_id, decision, request_sha256, decided_by, "
            "applied_revision, decided_at) VALUES (?, ?, 'approved', ?, 'owner', ?, ?)",
            (proposal_id, workspace_id, request_sha256, new_revision, now),
        )
        append_audit_event(
            connection,
            event_id=f"event_{uuid.uuid4().hex}",
            owner_id=str(workspace["owner_id"]),
            workspace_id=workspace_id,
            event_type="workspace_composition_applied",
            payload={
                "profile_sha256": desired_sha256,
                "proposal_id": proposal_id,
                "request_sha256": request_sha256,
                "revision": new_revision,
                "source_kind": str(proposal["source_kind"]),
            },
        )
        updated = connection.execute(
            "UPDATE workspace_composition_current SET revision = ?, profile_sha256 = ?, "
            "updated_at = ? WHERE workspace_id = ? AND owner_id = ? AND revision = ? "
            "AND profile_sha256 = ?",
            (
                new_revision,
                desired_sha256,
                now,
                workspace_id,
                workspace["owner_id"],
                current["revision"],
                current["profile_sha256"],
            ),
        )
        if updated.rowcount != 1:
            raise DesktopApiError(409, "desktop_composition_version_conflict")
        connection.execute("COMMIT")
        return {
            "workspace_id": workspace_id,
            "proposal_id": proposal_id,
            "request_sha256": request_sha256,
            "decision": "approved",
            "applied_revision": new_revision,
            "profile": {
                "workspace_id": workspace_id,
                "revision": new_revision,
                "profile_sha256": desired_sha256,
                "source_kind": str(proposal["source_kind"]),
                "proposal_id": proposal_id,
                "value": desired_profile,
                "created_at": now,
            },
        }
    except DesktopApiError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopApiError(503, "desktop_composition_decision_failed") from None


__all__ = [
    "create_assistant_proposal",
    "create_owner_proposal",
    "create_rollback_proposal",
    "decide_workspace_proposal",
    "get_application_preference",
    "get_workspace_composition",
    "initialize_owner_preference",
    "initialize_workspace_composition",
    "update_application_preference",
    "validate_workspace_profile",
]
