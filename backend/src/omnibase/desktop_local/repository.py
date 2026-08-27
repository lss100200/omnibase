"""Small, explicit persistence API for the desktop-local foundation."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass

from omnibase.desktop_local.database import utc_now_text


@dataclass(frozen=True, slots=True)
class ClaimedRuntimeJob:
    id: str
    owner_id: str
    workspace_id: str
    job_kind: str
    claim_owner: str
    claim_token: str
    claimed_at: str


def create_owner(connection: sqlite3.Connection, owner_id: str, display_name: str) -> None:
    now = utc_now_text()
    connection.execute(
        "INSERT INTO owner (id, display_name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (owner_id, display_name, now, now),
    )


def create_workspace(
    connection: sqlite3.Connection,
    workspace_id: str,
    owner_id: str,
    name: str,
) -> None:
    now = utc_now_text()
    connection.execute(
        "INSERT INTO workspace (id, owner_id, name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (workspace_id, owner_id, name, now, now),
    )


def append_audit_event(
    connection: sqlite3.Connection,
    *,
    event_id: str,
    owner_id: str,
    event_type: str,
    payload: object,
    workspace_id: str | None = None,
) -> None:
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    connection.execute(
        "INSERT INTO audit_event "
        "(event_id, owner_id, workspace_id, event_type, payload_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (event_id, owner_id, workspace_id, event_type, payload_json, utc_now_text()),
    )


def enqueue_runtime_job(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    owner_id: str,
    workspace_id: str,
    job_kind: str,
) -> None:
    now = utc_now_text()
    connection.execute(
        "INSERT INTO runtime_job "
        "(id, owner_id, workspace_id, job_kind, state, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'queued', ?, ?)",
        (job_id, owner_id, workspace_id, job_kind, now, now),
    )


def claim_next_runtime_job(
    connection: sqlite3.Connection,
    *,
    claim_owner: str,
) -> ClaimedRuntimeJob | None:
    """Atomically claim the oldest queued job; concurrent callers have one winner."""

    claim_token = str(uuid.uuid4())
    claimed_at = utc_now_text()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT id FROM runtime_job WHERE state = 'queued' ORDER BY created_at, id LIMIT 1"
        ).fetchone()
        if row is None:
            connection.execute("COMMIT")
            return None
        claimed = connection.execute(
            "UPDATE runtime_job SET state = 'claimed', claim_owner = ?, claim_token = ?, "
            "claimed_at = ?, updated_at = ? WHERE id = ? AND state = 'queued' "
            "RETURNING id, owner_id, workspace_id, job_kind, claim_owner, claim_token, claimed_at",
            (claim_owner, claim_token, claimed_at, claimed_at, row["id"]),
        ).fetchone()
        connection.execute("COMMIT")
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    if claimed is None:
        return None
    return ClaimedRuntimeJob(
        id=claimed["id"],
        owner_id=claimed["owner_id"],
        workspace_id=claimed["workspace_id"],
        job_kind=claimed["job_kind"],
        claim_owner=claimed["claim_owner"],
        claim_token=claimed["claim_token"],
        claimed_at=claimed["claimed_at"],
    )


def start_runtime_job(connection: sqlite3.Connection, job_id: str, claim_token: str) -> bool:
    now = utc_now_text()
    cursor = connection.execute(
        "UPDATE runtime_job SET state = 'running', updated_at = ? "
        "WHERE id = ? AND state = 'claimed' AND claim_token = ?",
        (now, job_id, claim_token),
    )
    return cursor.rowcount == 1


def finish_runtime_job(
    connection: sqlite3.Connection,
    job_id: str,
    claim_token: str,
    outcome: str,
) -> bool:
    if outcome not in {"succeeded", "failed", "cancelled"}:
        raise ValueError("outcome must be succeeded, failed, or cancelled")
    now = utc_now_text()
    cursor = connection.execute(
        "UPDATE runtime_job SET state = ?, updated_at = ? "
        "WHERE id = ? AND state = 'running' AND claim_token = ?",
        (outcome, now, job_id, claim_token),
    )
    return cursor.rowcount == 1
