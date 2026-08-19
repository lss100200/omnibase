"""Workspace-bound conversations, invocations and SSE for desktop-local mode."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

from omnibase.desktop_local.database import utc_now_text
from omnibase.desktop_local.endpoint import DesktopEndpointError, resolve_provider_endpoint
from omnibase.desktop_local.errors import DesktopLocalError
from omnibase.desktop_local.family import plan_desktop_adaptation
from omnibase.desktop_local.provider_http import (
    ChatMessage,
    DesktopProviderCallError,
    stream_provider_chat,
)
from omnibase.desktop_local.providers import DesktopApiError, resolve_enabled_provider
from omnibase.desktop_local.redaction import public_error_message
from omnibase.desktop_local.repository import append_audit_event

_WORKSPACE_ID_PATTERN = re.compile(r"workspace_[0-9a-f]{32}\Z")
_CONVERSATION_ID_PATTERN = re.compile(r"conversation_[0-9a-f]{32}\Z")
_MESSAGE_ID_PATTERN = re.compile(r"message_[0-9a-f]{32}\Z")
_INVOCATION_ID_PATTERN = re.compile(r"invocation_[0-9a-f]{32}\Z")
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_MAX_CONVERSATIONS = 64
_MAX_MESSAGES = 200
_MAX_USER_CONTENT = 16_384


def recover_interrupted_invocations(connection: sqlite3.Connection) -> None:
    now = utc_now_text()
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "UPDATE invocation SET status = 'unknown', error_code = 'desktop_invocation_interrupted', "
            "error_redacted = ?, updated_at = ? WHERE status = 'running'",
            (public_error_message("desktop_invocation_interrupted"), now),
        )
        connection.execute("UPDATE message SET status = 'unknown' WHERE status = 'streaming'")
        connection.execute("COMMIT")
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopLocalError("desktop_invocation_recovery_failed") from None


def _require_owner(connection: sqlite3.Connection) -> sqlite3.Row:
    owner = connection.execute("SELECT id FROM owner WHERE singleton_key = 1").fetchone()
    if owner is None:
        raise DesktopApiError(409, "desktop_owner_not_initialized")
    return owner


def _require_workspace(
    connection: sqlite3.Connection,
    owner_id: str,
    workspace_id: str,
    *,
    active: bool,
) -> sqlite3.Row:
    if not _WORKSPACE_ID_PATTERN.fullmatch(workspace_id):
        raise DesktopApiError(404, "desktop_workspace_not_found")
    row = connection.execute(
        "SELECT id, owner_id, name, state FROM workspace WHERE id = ? AND owner_id = ?",
        (workspace_id, owner_id),
    ).fetchone()
    if row is None:
        raise DesktopApiError(404, "desktop_workspace_not_found")
    if active and str(row["state"]) != "active":
        raise DesktopApiError(409, "desktop_workspace_archived")
    return row


def conversation_payload(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "workspace_id": str(row["workspace_id"]),
        "title": str(row["title"]),
        "state": str(row["state"]),
        "row_version": int(row["row_version"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def invocation_payload(row: sqlite3.Row | None) -> dict[str, object] | None:
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "provider_id": str(row["provider_id"]),
        "requested_model": str(row["requested_model"]),
        "actual_model": row["actual_model"],
        "family": str(row["family"]),
        "gear": str(row["gear"]),
        "thinking_depth": str(row["thinking_depth"]),
        "status": str(row["status"]),
        "duration_ms": row["duration_ms"],
        "input_tokens": row["input_tokens"],
        "output_tokens": row["output_tokens"],
        "total_tokens": row["total_tokens"],
        "error_code": row["error_code"],
        "error_redacted": row["error_redacted"],
        "retry_of_invocation_id": row["retry_of_invocation_id"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def message_payload(row: sqlite3.Row, invocation: sqlite3.Row | None) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "role": str(row["role"]),
        "content": str(row["content"]),
        "status": str(row["status"]),
        "invocation_id": row["invocation_id"],
        "retry_of_message_id": row["retry_of_message_id"],
        "created_at": str(row["created_at"]),
        "invocation": invocation_payload(invocation),
    }


def list_conversations(connection: sqlite3.Connection, workspace_id: str) -> dict[str, object]:
    owner = _require_owner(connection)
    _require_workspace(connection, str(owner["id"]), workspace_id, active=False)
    rows = connection.execute(
        "SELECT id, workspace_id, title, state, row_version, created_at, updated_at "
        "FROM conversation WHERE owner_id = ? AND workspace_id = ? "
        "ORDER BY CASE state WHEN 'active' THEN 0 ELSE 1 END, updated_at DESC, id",
        (owner["id"], workspace_id),
    ).fetchall()
    return {"items": [conversation_payload(row) for row in rows]}


def create_conversation(
    connection: sqlite3.Connection,
    workspace_id: str,
    title: str | None,
) -> dict[str, object]:
    normalized = (title or "新会话").strip()
    if not normalized or len(normalized) > 256 or _CONTROL_CHARACTER_PATTERN.search(normalized):
        raise DesktopApiError(422, "desktop_validation_error")
    connection.execute("BEGIN IMMEDIATE")
    try:
        owner = _require_owner(connection)
        _require_workspace(connection, str(owner["id"]), workspace_id, active=True)
        count = connection.execute(
            "SELECT COUNT(*) FROM conversation WHERE owner_id = ? AND workspace_id = ?",
            (owner["id"], workspace_id),
        ).fetchone()
        if count is None or int(count[0]) >= _MAX_CONVERSATIONS:
            raise DesktopApiError(409, "desktop_conversation_capacity_reached")
        conversation_id = f"conversation_{uuid.uuid4().hex}"
        now = utc_now_text()
        connection.execute(
            "INSERT INTO conversation ("
            "id, owner_id, workspace_id, title, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (conversation_id, owner["id"], workspace_id, normalized, now, now),
        )
        append_audit_event(
            connection,
            event_id=f"event_{uuid.uuid4().hex}",
            owner_id=str(owner["id"]),
            workspace_id=workspace_id,
            event_type="conversation_created",
            payload={"conversation_id": conversation_id},
        )
        row = connection.execute(
            "SELECT id, workspace_id, title, state, row_version, created_at, updated_at "
            "FROM conversation WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if row is None:
            raise sqlite3.IntegrityError("conversation insert did not converge")
        connection.execute("COMMIT")
    except DesktopApiError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopLocalError("desktop_conversation_create_failed") from None
    return {"created": True, "conversation": conversation_payload(row)}


def archive_conversation(
    connection: sqlite3.Connection,
    workspace_id: str,
    conversation_id: str,
    expected_row_version: int,
) -> dict[str, object]:
    if not _CONVERSATION_ID_PATTERN.fullmatch(conversation_id):
        raise DesktopApiError(404, "desktop_conversation_not_found")
    connection.execute("BEGIN IMMEDIATE")
    try:
        owner = _require_owner(connection)
        _require_workspace(connection, str(owner["id"]), workspace_id, active=False)
        now = utc_now_text()
        archived = connection.execute(
            "UPDATE conversation SET state = 'archived', row_version = row_version + 1, "
            "updated_at = ? WHERE id = ? AND owner_id = ? AND workspace_id = ? "
            "AND state = 'active' AND row_version = ? "
            "RETURNING id, workspace_id, title, state, row_version, created_at, updated_at",
            (now, conversation_id, owner["id"], workspace_id, expected_row_version),
        ).fetchone()
        if archived is None:
            existing = connection.execute(
                "SELECT id FROM conversation WHERE id = ? AND owner_id = ? AND workspace_id = ?",
                (conversation_id, owner["id"], workspace_id),
            ).fetchone()
            if existing is None:
                raise DesktopApiError(404, "desktop_conversation_not_found")
            raise DesktopApiError(409, "desktop_conversation_version_conflict")
        append_audit_event(
            connection,
            event_id=f"event_{uuid.uuid4().hex}",
            owner_id=str(owner["id"]),
            workspace_id=workspace_id,
            event_type="conversation_archived",
            payload={
                "conversation_id": conversation_id,
                "previous_row_version": expected_row_version,
                "row_version": int(archived["row_version"]),
            },
        )
        connection.execute("COMMIT")
    except DesktopApiError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopLocalError("desktop_conversation_archive_failed") from None
    return {"conversation": conversation_payload(archived)}


def get_conversation(
    connection: sqlite3.Connection,
    workspace_id: str,
    conversation_id: str,
) -> dict[str, object]:
    if not _CONVERSATION_ID_PATTERN.fullmatch(conversation_id):
        raise DesktopApiError(404, "desktop_conversation_not_found")
    owner = _require_owner(connection)
    _require_workspace(connection, str(owner["id"]), workspace_id, active=False)
    conversation = connection.execute(
        "SELECT id, workspace_id, title, state, row_version, created_at, updated_at "
        "FROM conversation WHERE id = ? AND owner_id = ? AND workspace_id = ?",
        (conversation_id, owner["id"], workspace_id),
    ).fetchone()
    if conversation is None:
        raise DesktopApiError(404, "desktop_conversation_not_found")
    messages = connection.execute(
        "SELECT id, role, content, status, invocation_id, retry_of_message_id, created_at "
        "FROM message WHERE conversation_id = ? ORDER BY rowid",
        (conversation_id,),
    ).fetchall()
    invocations = {
        str(row["id"]): row
        for row in connection.execute(
            "SELECT * FROM invocation WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchall()
    }
    items = [
        message_payload(
            row,
            invocations.get(str(row["invocation_id"])) if row["invocation_id"] else None,
        )
        for row in messages
    ]
    return {"conversation": conversation_payload(conversation), "messages": items}


def _title_from_content(content: str) -> str:
    compact = re.sub(r"\s+", " ", content).strip()
    return compact[:40] or "新会话"


def _compile_context(
    rows: list[sqlite3.Row],
    budget: int,
) -> tuple[ChatMessage, ...]:
    selected: list[ChatMessage] = []
    used = 0
    for row in reversed(rows):
        if str(row["status"]) not in {"completed"} or str(row["role"]) not in {"user", "assistant"}:
            continue
        content = str(row["content"])
        if not content or used + len(content) > budget:
            continue
        selected.append(ChatMessage(role=str(row["role"]), content=content))
        used += len(content)
    selected.reverse()
    return tuple(selected)


def _sse(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _finish_invocation(
    connection: sqlite3.Connection,
    lock: threading.RLock,
    *,
    invocation_id: str,
    message_id: str,
    status: str,
    content: str,
    actual_model: str | None,
    duration_ms: int,
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None,
    error_code: str | None,
    error_redacted: str | None,
    message_status: str,
) -> None:
    now = utc_now_text()
    with lock:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "UPDATE invocation SET status = ?, actual_model = ?, duration_ms = ?, "
                "input_tokens = ?, output_tokens = ?, total_tokens = ?, error_code = ?, "
                "error_redacted = ?, updated_at = ? WHERE id = ? AND status = 'running'",
                (
                    status,
                    actual_model,
                    duration_ms,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    error_code,
                    error_redacted,
                    now,
                    invocation_id,
                ),
            )
            connection.execute(
                "UPDATE message SET content = ?, status = ? WHERE id = ?",
                (content, message_status, message_id),
            )
            connection.execute("COMMIT")
        except sqlite3.Error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise


def prepare_send(  # noqa: C901 - retry, context compile and durable insert share one lock
    connection: sqlite3.Connection,
    lock: threading.RLock,
    *,
    workspace_id: str,
    conversation_id: str,
    content: str,
    provider_id: str | None,
    retry_of_message_id: str | None,
) -> dict[str, Any]:
    if not _CONVERSATION_ID_PATTERN.fullmatch(conversation_id):
        raise DesktopApiError(404, "desktop_conversation_not_found")
    normalized = content.strip() if retry_of_message_id is None else content
    if retry_of_message_id is None:
        if (
            not isinstance(normalized, str)
            or not 1 <= len(normalized) <= _MAX_USER_CONTENT
            or _CONTROL_CHARACTER_PATTERN.search(content)
        ):
            raise DesktopApiError(422, "desktop_validation_error")
    elif not _MESSAGE_ID_PATTERN.fullmatch(retry_of_message_id):
        raise DesktopApiError(422, "desktop_validation_error")
    with lock:
        connection.execute("BEGIN IMMEDIATE")
        try:
            owner = _require_owner(connection)
            _require_workspace(connection, str(owner["id"]), workspace_id, active=True)
            conversation = connection.execute(
                "SELECT id, title, state FROM conversation "
                "WHERE id = ? AND owner_id = ? AND workspace_id = ?",
                (conversation_id, owner["id"], workspace_id),
            ).fetchone()
            if conversation is None:
                raise DesktopApiError(404, "desktop_conversation_not_found")
            if str(conversation["state"]) != "active":
                raise DesktopApiError(409, "desktop_conversation_archived")
            running = connection.execute(
                "SELECT COUNT(*) FROM invocation WHERE conversation_id = ? AND status = 'running'",
                (conversation_id,),
            ).fetchone()
            if running is not None and int(running[0]) > 0:
                raise DesktopApiError(409, "desktop_invocation_in_progress")
            message_count = connection.execute(
                "SELECT COUNT(*) FROM message WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            extra = 1 if retry_of_message_id else 2
            if message_count is None or int(message_count[0]) + extra > _MAX_MESSAGES:
                raise DesktopApiError(409, "desktop_conversation_message_capacity_reached")
            provider = resolve_enabled_provider(connection, str(owner["id"]), provider_id)
            retry_of_invocation_id = None
            user_content = normalized
            if retry_of_message_id is not None:
                failed = connection.execute(
                    "SELECT id, content, status, invocation_id, role FROM message "
                    "WHERE id = ? AND conversation_id = ?",
                    (retry_of_message_id, conversation_id),
                ).fetchone()
                if (
                    failed is None
                    or str(failed["role"]) != "assistant"
                    or str(failed["status"]) not in {"failed", "cancelled", "unknown"}
                ):
                    raise DesktopApiError(409, "desktop_retry_not_allowed")
                prior = connection.execute(
                    "SELECT status FROM invocation WHERE id = ?",
                    (failed["invocation_id"],),
                ).fetchone()
                if prior is None or str(prior["status"]) == "running":
                    raise DesktopApiError(409, "desktop_retry_not_allowed")
                retry_of_invocation_id = str(failed["invocation_id"])
                previous_user = connection.execute(
                    "SELECT content FROM message WHERE conversation_id = ? AND role = 'user' "
                    "AND rowid < (SELECT rowid FROM message WHERE id = ?) "
                    "ORDER BY rowid DESC LIMIT 1",
                    (conversation_id, retry_of_message_id),
                ).fetchone()
                if previous_user is None:
                    raise DesktopApiError(409, "desktop_retry_not_allowed")
                user_content = str(previous_user["content"])
            now = utc_now_text()
            if retry_of_message_id is None:
                user_id = f"message_{uuid.uuid4().hex}"
                connection.execute(
                    "INSERT INTO message ("
                    "id, owner_id, workspace_id, conversation_id, role, content, status, created_at"
                    ") VALUES (?, ?, ?, ?, 'user', ?, 'completed', ?)",
                    (user_id, owner["id"], workspace_id, conversation_id, user_content, now),
                )
                if str(conversation["title"]) == "新会话":
                    connection.execute(
                        "UPDATE conversation SET title = ?, updated_at = ? WHERE id = ?",
                        (_title_from_content(user_content), now, conversation_id),
                    )
            invocation_id = f"invocation_{uuid.uuid4().hex}"
            assistant_id = f"message_{uuid.uuid4().hex}"
            connection.execute(
                "INSERT INTO invocation ("
                "id, owner_id, workspace_id, conversation_id, provider_id, requested_model, "
                "family, gear, thinking_depth, status, retry_of_invocation_id, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)",
                (
                    invocation_id,
                    owner["id"],
                    workspace_id,
                    conversation_id,
                    str(provider["id"]),
                    str(provider["model_name"]),
                    str(provider["family"]),
                    str(provider["gear"]),
                    str(provider["thinking_depth"]),
                    retry_of_invocation_id,
                    now,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO message ("
                "id, owner_id, workspace_id, conversation_id, role, content, status, "
                "invocation_id, retry_of_message_id, created_at"
                ") VALUES (?, ?, ?, ?, 'assistant', '', 'streaming', ?, ?, ?)",
                (
                    assistant_id,
                    owner["id"],
                    workspace_id,
                    conversation_id,
                    invocation_id,
                    retry_of_message_id,
                    now,
                ),
            )
            history = connection.execute(
                "SELECT role, content, status FROM message WHERE conversation_id = ? "
                "AND id <> ? ORDER BY rowid",
                (conversation_id, assistant_id),
            ).fetchall()
            adaptation = plan_desktop_adaptation(
                str(provider["model_name"]),
                str(provider["base_url"]),
                str(provider["gear"]),  # type: ignore[arg-type]
                str(provider["thinking_depth"]),  # type: ignore[arg-type]
            )
            context = _compile_context(history, adaptation.context_character_budget)
            messages = (
                ChatMessage(role="system", content=adaptation.stable_prefix),
                *context,
            )
            append_audit_event(
                connection,
                event_id=f"event_{uuid.uuid4().hex}",
                owner_id=str(owner["id"]),
                workspace_id=workspace_id,
                event_type="invocation_started",
                payload={
                    "conversation_id": conversation_id,
                    "invocation_id": invocation_id,
                    "provider_id": str(provider["id"]),
                    "family": str(provider["family"]),
                    "retry": retry_of_invocation_id is not None,
                },
            )
            connection.execute("COMMIT")
        except DesktopApiError:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        except sqlite3.Error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise DesktopLocalError("desktop_conversation_send_failed") from None
    return {
        "provider": provider,
        "adaptation": adaptation,
        "messages": messages,
        "invocation_id": invocation_id,
        "message_id": assistant_id,
        "conversation_id": conversation_id,
        "workspace_id": workspace_id,
    }


def stream_prepared_send(
    connection: sqlite3.Connection,
    lock: threading.RLock,
    prepared: dict[str, Any],
    secret: str,
    cancelled: threading.Event,
) -> Iterator[str]:
    provider = prepared["provider"]
    invocation_id = str(prepared["invocation_id"])
    message_id = str(prepared["message_id"])
    started = time.monotonic()
    assembled = ""
    actual_model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    yield _sse(
        "identity",
        {
            "invocation_id": invocation_id,
            "message_id": message_id,
            "provider_id": str(provider["id"]),
            "provider_name": str(provider["display_name"]),
            "requested_model": str(provider["model_name"]),
            "family": str(provider["family"]),
            "gear": str(provider["gear"]),
            "thinking_depth": str(provider["thinking_depth"]),
        },
    )
    try:
        endpoint = resolve_provider_endpoint(
            str(provider["base_url"]),
            allow_loopback_http=bool(provider["allow_loopback_http"]),
        )
        for event in stream_provider_chat(
            endpoint,
            secret=secret,
            model_name=str(provider["model_name"]),
            messages=prepared["messages"],
            adaptation=prepared["adaptation"],
            timeout_seconds=float(provider["timeout_seconds"]),
            cancelled=cancelled.is_set,
        ):
            if event.actual_model:
                actual_model = event.actual_model
            if event.kind == "delta" and event.text:
                assembled += event.text
                yield _sse(
                    "delta",
                    {
                        "invocation_id": invocation_id,
                        "message_id": message_id,
                        "text": event.text,
                    },
                )
            elif event.kind == "usage":
                input_tokens = event.input_tokens
                output_tokens = event.output_tokens
                total_tokens = event.total_tokens
        duration_ms = int((time.monotonic() - started) * 1000)
        _finish_invocation(
            connection,
            lock,
            invocation_id=invocation_id,
            message_id=message_id,
            status="succeeded",
            content=assembled,
            actual_model=actual_model,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            error_code=None,
            error_redacted=None,
            message_status="completed",
        )
        yield _sse(
            "done",
            {
                "invocation_id": invocation_id,
                "message_id": message_id,
                "answer": assembled,
                "actual_model": actual_model,
                "status": "succeeded",
                "duration_ms": duration_ms,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
            },
        )
    except DesktopProviderCallError as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        cancelled_call = exc.code == "desktop_invocation_cancelled"
        status = "cancelled" if cancelled_call else "failed"
        message_status = "cancelled" if cancelled_call else "failed"
        public = (
            public_error_message("desktop_invocation_cancelled")
            if cancelled_call
            else public_error_message(exc.code, exc.public_message)
        )
        _finish_invocation(
            connection,
            lock,
            invocation_id=invocation_id,
            message_id=message_id,
            status=status,
            content=assembled,
            actual_model=actual_model,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            error_code=exc.code,
            error_redacted=public,
            message_status=message_status,
        )
        event_name = "cancelled" if cancelled_call else "error"
        yield _sse(
            event_name,
            {
                "invocation_id": invocation_id,
                "message_id": message_id,
                "status": status,
                "error_code": exc.code,
                "error_redacted": public,
            },
        )
    except DesktopEndpointError as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        public = public_error_message(exc.code)
        _finish_invocation(
            connection,
            lock,
            invocation_id=invocation_id,
            message_id=message_id,
            status="failed",
            content=assembled,
            actual_model=actual_model,
            duration_ms=duration_ms,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            error_code=exc.code,
            error_redacted=public,
            message_status="failed",
        )
        yield _sse(
            "error",
            {
                "invocation_id": invocation_id,
                "message_id": message_id,
                "status": "failed",
                "error_code": exc.code,
                "error_redacted": public,
            },
        )


def cancel_invocation(
    connection: sqlite3.Connection,
    cancel_events: dict[str, threading.Event],
    invocation_id: str,
) -> dict[str, object]:
    if not _INVOCATION_ID_PATTERN.fullmatch(invocation_id):
        raise DesktopApiError(404, "desktop_invocation_not_found")
    event = cancel_events.get(invocation_id)
    if event is not None:
        event.set()
        return {"cancelled": True, "id": invocation_id, "accepted": True}
    row = connection.execute(
        "SELECT status FROM invocation WHERE id = ?",
        (invocation_id,),
    ).fetchone()
    if row is None:
        raise DesktopApiError(404, "desktop_invocation_not_found")
    if str(row["status"]) != "running":
        return {"cancelled": False, "id": invocation_id, "accepted": False}
    now = utc_now_text()
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "UPDATE invocation SET status = 'cancelled', error_code = ?, error_redacted = ?, "
            "updated_at = ? WHERE id = ? AND status = 'running'",
            (
                "desktop_invocation_cancelled",
                public_error_message("desktop_invocation_cancelled"),
                now,
                invocation_id,
            ),
        )
        connection.execute(
            "UPDATE message SET status = 'cancelled' WHERE invocation_id = ? AND status = 'streaming'",
            (invocation_id,),
        )
        connection.execute("COMMIT")
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopApiError(500, "desktop_invocation_cancel_failed") from None
    return {"cancelled": True, "id": invocation_id, "accepted": True}
