"""Loopback-only FastAPI application for the personal desktop runtime.

The desktop application is intentionally assembled from explicit arguments.  It
does not import the PostgreSQL application settings, use ``BaseSettings``, load
dotenv files, or inspect ambient provider/database configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import re
import sqlite3
import sys
import threading
import uuid
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from omnibase.desktop_local.config import DesktopLocalConfig
from omnibase.desktop_local.conversations import (
    abandon_if_running,
    archive_conversation,
    cancel_invocation,
    create_conversation,
    get_conversation,
    list_conversations,
    prepare_send,
    recover_interrupted_invocations,
    stream_prepared_send,
)
from omnibase.desktop_local.database import (
    local_health,
    migrate_database,
    open_database,
    utc_now_text,
)
from omnibase.desktop_local.endpoint import DesktopEndpointError, resolve_provider_endpoint
from omnibase.desktop_local.errors import DesktopLocalError
from omnibase.desktop_local.personal_team import (
    EMPLOYEE_ROLE_IDS,
    append_team_run_budget,
    cancel_team_run,
    consume_provider_call,
    create_team_node,
    get_agent_role,
    get_team_blackboard,
    get_team_run,
    list_agent_roles,
    list_team_runs,
    record_collaboration_request,
    record_employee_report,
    recover_interrupted_team_runs,
    resolve_collaboration_request,
    set_assignment_effective_execution,
    set_team_run_state,
    settle_team_node,
    start_team_run,
    submit_parent_proposal,
    test_agent_role,
    update_agent_role,
    update_team_node,
)
from omnibase.desktop_local.providers import (
    DesktopApiError,
    delete_provider,
    list_providers,
    load_provider_secret_material,
    test_provider,
    upsert_provider,
)
from omnibase.desktop_local.repository import append_audit_event, create_owner, create_workspace

DESKTOP_INSTANCE_HEADER = "x-omnibase-desktop-instance"
DESKTOP_CHALLENGE_HEADER = "x-omnibase-desktop-challenge"
DESKTOP_PROOF_HEADER = "x-omnibase-desktop-proof"
DESKTOP_NATIVE_CONTROL_HEADER = "x-omnibase-desktop-native-control"
DESKTOP_INSTANCE_TOKEN_ENV = "OMNIBASE_DESKTOP_INSTANCE_TOKEN"
DESKTOP_NATIVE_PROOF_KEY_ENV = "OMNIBASE_DESKTOP_NATIVE_PROOF_KEY"
DESKTOP_NATIVE_CONTROL_TOKEN_ENV = "OMNIBASE_DESKTOP_NATIVE_CONTROL_TOKEN"
_LOOPBACK_HOST = "127.0.0.1"
_INSTANCE_TOKEN_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_CHALLENGE_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_APPLICATION_VERSION_PATTERN = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,63}\Z")
_WORKSPACE_ID_PATTERN = re.compile(r"workspace_[0-9a-f]{32}\Z")
_TEAM_RUN_ID_PATTERN = re.compile(r"teamrun_[0-9a-f]{32}\Z")
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_NATIVE_ROUTE_ROOT = "/desktop/v1"
_MAX_WORKSPACES = 256


@dataclass(frozen=True, slots=True)
class DesktopLocalAppConfig:
    """Explicit server configuration supplied by the trusted desktop launcher."""

    storage: DesktopLocalConfig
    instance_token: str
    native_proof_key: str
    native_control_token: str
    bind_host: str = _LOOPBACK_HOST
    port: int = 47_431

    def __post_init__(self) -> None:
        if not isinstance(self.instance_token, str) or not _INSTANCE_TOKEN_PATTERN.fullmatch(
            self.instance_token
        ):
            raise ValueError("desktop_instance_token_must_be_64_hex")
        if not isinstance(self.native_proof_key, str) or not _INSTANCE_TOKEN_PATTERN.fullmatch(
            self.native_proof_key
        ):
            raise ValueError("desktop_native_proof_key_must_be_64_hex")
        if not isinstance(self.native_control_token, str) or not _INSTANCE_TOKEN_PATTERN.fullmatch(
            self.native_control_token
        ):
            raise ValueError("desktop_native_control_token_must_be_64_hex")
        if self.bind_host != _LOOPBACK_HOST:
            raise ValueError("desktop_bind_host_must_be_ipv4_loopback")
        if not _APPLICATION_VERSION_PATTERN.fullmatch(self.storage.application_version):
            raise ValueError("desktop_application_version_invalid")
        if (
            not isinstance(self.port, int)
            or isinstance(self.port, bool)
            or not 1 <= self.port <= 65535
        ):
            raise ValueError("desktop_port_out_of_range")


@dataclass(slots=True)
class _DesktopRuntime:
    connection: sqlite3.Connection
    lock: threading.RLock
    cancel_events: dict[str, threading.Event]


class OwnerBootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    display_name: str = Field(min_length=1, max_length=256)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or _CONTROL_CHARACTER_PATTERN.search(value):
            raise ValueError("display_name_must_not_be_blank")
        return normalized


class WorkspaceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    name: str = Field(min_length=1, max_length=256)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or _CONTROL_CHARACTER_PATTERN.search(value):
            raise ValueError("workspace_name_must_not_be_blank")
        return normalized


class WorkspaceArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    expected_row_version: int = Field(ge=1, le=2_147_483_647)


class ProviderUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    id: str | None = None
    display_name: str = Field(min_length=1, max_length=256)
    base_url: str = Field(min_length=8, max_length=2048)
    model_name: str = Field(min_length=1, max_length=256)
    gear: str = Field(min_length=3, max_length=32)
    thinking_depth: str = Field(min_length=3, max_length=32)
    timeout_seconds: int = Field(ge=5, le=120)
    allow_loopback_http: bool
    is_default: bool
    is_enabled: bool
    credential_reference: str | None = None
    encrypted_secret_blob: str | None = None
    secret_fingerprint: str | None = None


class ProviderSecretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    secret: str = Field(min_length=1, max_length=512)


class ProviderEndpointPinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    base_url: str = Field(min_length=8, max_length=2048)
    allow_loopback_http: bool


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    title: str | None = Field(default=None, max_length=256)


class ConversationArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    expected_row_version: int = Field(ge=1, le=2_147_483_647)


class ConversationSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    secret: str = Field(min_length=1, max_length=512)
    content: str = Field(default="", max_length=16384)
    provider_id: str | None = None
    retry_of_message_id: str | None = None


class AgentRoleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    provider_id: str | None = None
    model_name_override: str | None = Field(default=None, max_length=256)
    gear: str = Field(min_length=3, max_length=32)
    thinking_depth: str = Field(min_length=3, max_length=32)
    expected_row_version: int = Field(ge=1, le=2_147_483_647)


class TeamRunStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    conversation_id: str = Field(min_length=13, max_length=45)
    task: str = Field(min_length=1, max_length=16384)
    team_mode: bool
    allowed_specialist_role_ids: list[str] | None = None
    maximum_provider_calls: int = Field(ge=0, le=2_147_483_647)
    maximum_wall_time_ms: int = Field(ge=0, le=2_147_483_647)
    maximum_concurrent_calls: int = Field(ge=0, le=2_147_483_647)
    maximum_input_characters: int = Field(ge=0, le=2_147_483_647)
    maximum_output_characters: int = Field(ge=0, le=2_147_483_647)

    @field_validator("team_mode")
    @classmethod
    def require_team_mode(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("team_mode_must_be_true")
        return value


class TeamRunProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    proposal: dict[str, object]


class TeamCollaborationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    from_assignment_id: str = Field(min_length=1, max_length=128)
    from_employee_role_id: str = Field(min_length=2, max_length=32)
    target_role_id: str = Field(min_length=2, max_length=32)
    question: str = Field(min_length=1, max_length=16384)
    reason: str = Field(min_length=1, max_length=16384)
    node_id: str = Field(min_length=1, max_length=128)
    report_id: str = Field(min_length=1, max_length=128)


class TeamRunBudgetAppendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    maximum_provider_calls: int = Field(ge=0, le=2_147_483_647)
    maximum_wall_time_ms: int = Field(ge=0, le=2_147_483_647)
    maximum_concurrent_calls: int = Field(ge=0, le=2_147_483_647)
    maximum_input_characters: int = Field(ge=0, le=2_147_483_647)
    maximum_output_characters: int = Field(ge=0, le=2_147_483_647)


class TeamRunStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    state: str = Field(min_length=4, max_length=32)
    parent_final_answer: str | None = Field(default=None, max_length=131072)


class TeamAssignmentExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    assignment_id: str = Field(min_length=1, max_length=128)
    effective_execution: str = Field(min_length=6, max_length=8)


class TeamNodeCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    assignment_id: str = Field(min_length=1, max_length=128)
    employee_role_id: str = Field(min_length=2, max_length=32)
    invocation_id: str = Field(min_length=13, max_length=45)
    wave_id: str = Field(min_length=1, max_length=128)
    node_epoch: int = Field(ge=1, le=2_147_483_647)
    send_epoch: int = Field(ge=1, le=2_147_483_647)
    provider_id: str = Field(min_length=13, max_length=45)
    requested_model: str = Field(min_length=1, max_length=256)


class TeamNodeUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    state: str = Field(min_length=4, max_length=16)
    actual_model: str | None = Field(default=None, max_length=256)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    answer_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    error_code: str | None = Field(default=None, max_length=96)
    duration_ms: int | None = Field(default=None, ge=0)


class TeamEmployeeReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    assignment_id: str = Field(min_length=1, max_length=128)
    employee_role_id: str = Field(min_length=2, max_length=32)
    status: str = Field(min_length=7, max_length=32)
    report: str = Field(min_length=1, max_length=131072)
    node_id: str = Field(min_length=1, max_length=128)
    invocation_id: str = Field(min_length=13, max_length=45)
    collaboration_requests: list[dict[str, object]] = Field(default_factory=list)


class TeamNodeSettleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    state: str = Field(min_length=4, max_length=16)
    actual_model: str | None = Field(default=None, max_length=256)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    answer_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    error_code: str | None = Field(default=None, max_length=96)
    duration_ms: int | None = Field(default=None, ge=0)
    invocation_id: str = Field(min_length=13, max_length=45)
    assignment_id: str = Field(min_length=1, max_length=128)
    employee_role_id: str = Field(min_length=2, max_length=32)
    wave_id: str | None = Field(default=None, min_length=1, max_length=128)
    node_epoch: int | None = Field(default=None, ge=1, le=2_147_483_647)
    send_epoch: int | None = Field(default=None, ge=1, le=2_147_483_647)
    status: str = Field(min_length=7, max_length=32)
    report: str = Field(min_length=1, max_length=131072)
    collaboration_requests: list[dict[str, object]] = Field(default_factory=list)


class TeamCollaborationResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    parent_decision: str = Field(min_length=6, max_length=32)
    resolved_assignment_id: str | None = Field(default=None, max_length=128)


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers={"Cache-Control": "no-store"},
    )


def _runtime(request: Request) -> _DesktopRuntime:
    return cast(_DesktopRuntime, request.app.state.desktop_runtime)


def _app_config(request: Request) -> DesktopLocalAppConfig:
    return cast(DesktopLocalAppConfig, request.app.state.desktop_app_config)


def _read_owner(runtime: _DesktopRuntime) -> sqlite3.Row | None:
    try:
        with runtime.lock:
            return runtime.connection.execute(
                "SELECT id, display_name, created_at, updated_at FROM owner "
                "WHERE singleton_key = 1"
            ).fetchone()
    except sqlite3.Error:
        raise DesktopLocalError("desktop_owner_status_unavailable") from None


def _owner_payload(row: sqlite3.Row) -> dict[str, str]:
    return {
        "id": str(row["id"]),
        "display_name": str(row["display_name"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _workspace_payload(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "owner_id": str(row["owner_id"]),
        "name": str(row["name"]),
        "state": str(row["state"]),
        "row_version": int(row["row_version"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _bootstrap_owner(runtime: _DesktopRuntime, display_name: str) -> tuple[sqlite3.Row, bool]:
    """Create the one local Owner and its audit event in one transaction."""

    with runtime.lock:
        connection = runtime.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT id, display_name, created_at, updated_at FROM owner "
                "WHERE singleton_key = 1"
            ).fetchone()
            if existing is not None:
                connection.execute("COMMIT")
                return existing, False

            owner_id = f"owner_{uuid.uuid4().hex}"
            create_owner(connection, owner_id, display_name)
            append_audit_event(
                connection,
                event_id=f"event_{uuid.uuid4().hex}",
                owner_id=owner_id,
                event_type="owner_bootstrapped",
                payload={"authority": "local_owner", "source": "desktop_local"},
            )
            created = connection.execute(
                "SELECT id, display_name, created_at, updated_at FROM owner "
                "WHERE singleton_key = 1"
            ).fetchone()
            if created is None:
                raise sqlite3.IntegrityError("owner insert did not converge")
            connection.execute("COMMIT")
            return created, True
        except sqlite3.Error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise DesktopLocalError("desktop_owner_bootstrap_failed") from None


def _list_owner_workspaces(runtime: _DesktopRuntime) -> list[sqlite3.Row]:
    owner = _read_owner(runtime)
    if owner is None:
        raise DesktopApiError(409, "desktop_owner_not_initialized")
    try:
        with runtime.lock:
            return list(
                runtime.connection.execute(
                    "SELECT id, owner_id, name, state, row_version, created_at, updated_at "
                    "FROM workspace WHERE owner_id = ? "
                    "ORDER BY CASE state WHEN 'active' THEN 0 ELSE 1 END, created_at, id",
                    (owner["id"],),
                ).fetchall()
            )
    except sqlite3.Error:
        raise DesktopLocalError("desktop_workspace_list_unavailable") from None


def _create_owner_workspace(runtime: _DesktopRuntime, name: str) -> sqlite3.Row:
    with runtime.lock:
        connection = runtime.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            owner = connection.execute("SELECT id FROM owner WHERE singleton_key = 1").fetchone()
            if owner is None:
                raise DesktopApiError(409, "desktop_owner_not_initialized")
            workspace_count = connection.execute(
                "SELECT COUNT(*) FROM workspace WHERE owner_id = ?",
                (owner["id"],),
            ).fetchone()
            if workspace_count is None or int(workspace_count[0]) >= _MAX_WORKSPACES:
                raise DesktopApiError(409, "desktop_workspace_capacity_reached")
            workspace_id = f"workspace_{uuid.uuid4().hex}"
            create_workspace(connection, workspace_id, str(owner["id"]), name)
            append_audit_event(
                connection,
                event_id=f"event_{uuid.uuid4().hex}",
                owner_id=str(owner["id"]),
                workspace_id=workspace_id,
                event_type="workspace_created",
                payload={"row_version": 1, "state": "active"},
            )
            created = connection.execute(
                "SELECT id, owner_id, name, state, row_version, created_at, updated_at "
                "FROM workspace WHERE id = ?",
                (workspace_id,),
            ).fetchone()
            if created is None:
                raise sqlite3.IntegrityError("workspace insert did not converge")
            connection.execute("COMMIT")
            return created
        except DesktopApiError:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        except sqlite3.Error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise DesktopLocalError("desktop_workspace_create_failed") from None


def _archive_owner_workspace(
    runtime: _DesktopRuntime,
    workspace_id: str,
    expected_row_version: int,
) -> sqlite3.Row:
    if not _WORKSPACE_ID_PATTERN.fullmatch(workspace_id):
        raise DesktopApiError(404, "desktop_workspace_not_found")
    with runtime.lock:
        connection = runtime.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            owner = connection.execute("SELECT id FROM owner WHERE singleton_key = 1").fetchone()
            if owner is None:
                raise DesktopApiError(409, "desktop_owner_not_initialized")
            existing = connection.execute(
                "SELECT id, owner_id, name, state, row_version, created_at, updated_at "
                "FROM workspace WHERE id = ? AND owner_id = ?",
                (workspace_id, owner["id"]),
            ).fetchone()
            if existing is None:
                raise DesktopApiError(404, "desktop_workspace_not_found")
            if (
                str(existing["state"]) != "active"
                or int(existing["row_version"]) != expected_row_version
            ):
                raise DesktopApiError(409, "desktop_workspace_version_conflict")
            updated_at = utc_now_text()
            archived = connection.execute(
                "UPDATE workspace SET state = 'archived', row_version = row_version + 1, "
                "updated_at = ? WHERE id = ? AND owner_id = ? AND state = 'active' "
                "AND row_version = ? "
                "RETURNING id, owner_id, name, state, row_version, created_at, updated_at",
                (updated_at, workspace_id, owner["id"], expected_row_version),
            ).fetchone()
            if archived is None:
                raise DesktopApiError(409, "desktop_workspace_version_conflict")
            append_audit_event(
                connection,
                event_id=f"event_{uuid.uuid4().hex}",
                owner_id=str(owner["id"]),
                workspace_id=workspace_id,
                event_type="workspace_archived",
                payload={
                    "from_state": "active",
                    "previous_row_version": expected_row_version,
                    "row_version": int(archived["row_version"]),
                    "to_state": "archived",
                },
            )
            connection.execute("COMMIT")
            return archived
        except DesktopApiError:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        except sqlite3.Error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise DesktopLocalError("desktop_workspace_archive_failed") from None


async def _instance_binding_middleware(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    config = _app_config(request)
    if request.url.path == _NATIVE_ROUTE_ROOT or request.url.path.startswith(
        f"{_NATIVE_ROUTE_ROOT}/"
    ):
        supplied_native = request.headers.getlist(DESKTOP_NATIVE_CONTROL_HEADER)
        has_conflicting_identity = any(
            request.headers.getlist(name)
            for name in (
                DESKTOP_INSTANCE_HEADER,
                DESKTOP_CHALLENGE_HEADER,
                DESKTOP_PROOF_HEADER,
            )
        )
        if (
            len(supplied_native) != 1
            or not _INSTANCE_TOKEN_PATTERN.fullmatch(supplied_native[0])
            or not hmac.compare_digest(supplied_native[0], config.native_control_token)
            or has_conflicting_identity
        ):
            return _error_response(
                401,
                "desktop_native_control_unauthorized",
                "Desktop native control authorization failed",
            )
        request.state.desktop_challenge = None
    else:
        if request.headers.getlist(DESKTOP_NATIVE_CONTROL_HEADER):
            return _error_response(
                400,
                "desktop_native_control_invalid",
                "Desktop native control header rejected",
            )
        supplied = request.headers.getlist(DESKTOP_INSTANCE_HEADER)
        if (
            len(supplied) != 1
            or not _INSTANCE_TOKEN_PATTERN.fullmatch(supplied[0])
            or not hmac.compare_digest(supplied[0], config.instance_token)
        ):
            return _error_response(
                401,
                "desktop_instance_unauthorized",
                "Desktop instance authorization failed",
            )
        challenges = request.headers.getlist(DESKTOP_CHALLENGE_HEADER)
        if challenges and (
            request.url.path != "/health"
            or len(challenges) != 1
            or not _CHALLENGE_PATTERN.fullmatch(challenges[0])
        ):
            return _error_response(
                400,
                "desktop_challenge_invalid",
                "Desktop identity challenge rejected",
            )
        if request.headers.getlist(DESKTOP_PROOF_HEADER):
            return _error_response(
                400,
                "desktop_proof_header_invalid",
                "Desktop identity proof header rejected",
            )
        request.state.desktop_challenge = challenges[0] if challenges else None
    try:
        response = await call_next(request)
    except DesktopApiError as exc:
        response = _error_response(exc.status_code, exc.code, "Desktop request rejected")
    except DesktopLocalError as exc:
        response = _error_response(503, exc.code, "Desktop local service unavailable")
    except Exception:
        response = _error_response(
            500,
            "desktop_internal_error",
            "Desktop local request failed",
        )
    response.headers["Cache-Control"] = "no-store"
    return response


async def _validation_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
    return _error_response(
        422,
        "desktop_validation_error",
        "Desktop request validation failed",
    )


async def _http_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    status_code = exc.status_code if isinstance(exc, StarletteHTTPException) else 500
    code = "desktop_not_found" if status_code == 404 else "desktop_http_error"
    return _error_response(status_code, code, "Desktop request rejected")


def _health(request: Request, response: Response) -> dict[str, object]:
    config = _app_config(request)
    challenge = cast(str | None, request.state.desktop_challenge)
    if challenge is not None:
        proof = hmac.new(
            bytes.fromhex(config.native_proof_key),
            challenge.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        response.headers[DESKTOP_PROOF_HEADER] = proof
        response.headers["Cache-Control"] = "no-store"
    return {
        "status": "ok",
        "service": "omnibase_desktop_local",
        "version": config.storage.application_version,
        "bind": "ipv4_loopback",
    }


def _readiness(request: Request) -> dict[str, object]:
    runtime = _runtime(request)
    try:
        with runtime.lock:
            report = local_health(runtime.connection)
    except DesktopLocalError:
        raise
    except sqlite3.Error:
        raise DesktopLocalError("desktop_readiness_unavailable") from None
    if report.status != "healthy":
        raise DesktopLocalError("desktop_readiness_not_healthy")
    return {
        "status": "ready",
        "storage": "sqlite",
        "schema_version": report.schema_version,
        "application_version": report.application_version,
        "integrity": report.integrity,
    }


def _owner_status(request: Request) -> dict[str, object]:
    row = _read_owner(_runtime(request))
    return {
        "initialized": row is not None,
        "owner": _owner_payload(row) if row is not None else None,
    }


def _owner_bootstrap(payload: OwnerBootstrapRequest, request: Request) -> dict[str, object]:
    row, created = _bootstrap_owner(_runtime(request), payload.display_name)
    return {
        "initialized": True,
        "created": created,
        "owner": _owner_payload(row),
    }


def _workspace_list(request: Request) -> dict[str, object]:
    return {"items": [_workspace_payload(row) for row in _list_owner_workspaces(_runtime(request))]}


def _workspace_create(payload: WorkspaceCreateRequest, request: Request) -> dict[str, object]:
    return {
        "created": True,
        "workspace": _workspace_payload(_create_owner_workspace(_runtime(request), payload.name)),
    }


def _workspace_archive(
    workspace_id: str,
    payload: WorkspaceArchiveRequest,
    request: Request,
) -> dict[str, object]:
    return {
        "workspace": _workspace_payload(
            _archive_owner_workspace(_runtime(request), workspace_id, payload.expected_row_version)
        )
    }


def _workspace_parent_agent(workspace_id: str, request: Request) -> dict[str, object]:
    if not _WORKSPACE_ID_PATTERN.fullmatch(workspace_id):
        raise DesktopApiError(404, "desktop_workspace_not_found")
    runtime = _runtime(request)
    with runtime.lock:
        owner = runtime.connection.execute(
            "SELECT id FROM owner WHERE singleton_key = 1"
        ).fetchone()
        if owner is None:
            raise DesktopApiError(409, "desktop_owner_not_initialized")
        row = runtime.connection.execute(
            "SELECT id, workspace_id, role, display_name, created_at, updated_at "
            "FROM workspace_agent WHERE workspace_id = ? AND owner_id = ? AND role = 'parent'",
            (workspace_id, owner["id"]),
        ).fetchone()
    if row is None:
        raise DesktopApiError(404, "desktop_workspace_not_found")
    return {
        "agent": {
            "id": str(row["id"]),
            "workspace_id": str(row["workspace_id"]),
            "role": str(row["role"]),
            "display_name": str(row["display_name"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }
    }


def _providers_list(request: Request) -> dict[str, object]:
    runtime = _runtime(request)
    with runtime.lock:
        return list_providers(runtime.connection)


def _providers_upsert(payload: ProviderUpsertRequest, request: Request) -> dict[str, object]:
    runtime = _runtime(request)
    with runtime.lock:
        return upsert_provider(runtime.connection, payload.model_dump())


def _providers_delete(provider_id: str, request: Request) -> dict[str, object]:
    runtime = _runtime(request)
    with runtime.lock:
        return delete_provider(runtime.connection, provider_id)


def _providers_vault(provider_id: str, request: Request) -> dict[str, str]:
    runtime = _runtime(request)
    with runtime.lock:
        return load_provider_secret_material(runtime.connection, provider_id)


def _providers_pin_endpoint(
    payload: ProviderEndpointPinRequest, request: Request
) -> dict[str, object]:
    _runtime(request)
    try:
        endpoint = resolve_provider_endpoint(
            payload.base_url, allow_loopback_http=payload.allow_loopback_http
        )
    except DesktopEndpointError as exc:
        raise DesktopApiError(400, exc.code) from exc
    return {
        "scheme": endpoint.scheme,
        "hostname": endpoint.hostname,
        "port": endpoint.port,
        "chat_path": endpoint.chat_path,
        "connect_addrs": list(endpoint.connect_addrs),
        "loopback": endpoint.loopback,
    }


def _providers_test(
    provider_id: str,
    payload: ProviderSecretRequest,
    request: Request,
) -> dict[str, object]:
    runtime = _runtime(request)
    cancelled = threading.Event()
    runtime.cancel_events[f"test:{provider_id}"] = cancelled
    try:
        with runtime.lock:
            connection = runtime.connection
        return test_provider(connection, runtime.lock, provider_id, payload.secret, cancelled)
    finally:
        runtime.cancel_events.pop(f"test:{provider_id}", None)


def _conversations_list(workspace_id: str, request: Request) -> dict[str, object]:
    runtime = _runtime(request)
    with runtime.lock:
        return list_conversations(runtime.connection, workspace_id)


def _conversations_create(
    workspace_id: str,
    payload: ConversationCreateRequest,
    request: Request,
) -> dict[str, object]:
    runtime = _runtime(request)
    with runtime.lock:
        return create_conversation(runtime.connection, workspace_id, payload.title)


def _conversations_archive(
    workspace_id: str,
    conversation_id: str,
    payload: ConversationArchiveRequest,
    request: Request,
) -> dict[str, object]:
    runtime = _runtime(request)
    with runtime.lock:
        return archive_conversation(
            runtime.connection,
            workspace_id,
            conversation_id,
            payload.expected_row_version,
        )


def _conversations_get(
    workspace_id: str,
    conversation_id: str,
    request: Request,
) -> dict[str, object]:
    runtime = _runtime(request)
    with runtime.lock:
        return get_conversation(runtime.connection, workspace_id, conversation_id)


def _conversations_send(
    workspace_id: str,
    conversation_id: str,
    payload: ConversationSendRequest,
    request: Request,
) -> StreamingResponse:
    runtime = _runtime(request)
    prepared = prepare_send(
        runtime.connection,
        runtime.lock,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        content=payload.content,
        provider_id=payload.provider_id,
        retry_of_message_id=payload.retry_of_message_id,
    )
    cancelled = threading.Event()
    runtime.cancel_events[str(prepared["invocation_id"])] = cancelled

    def generate() -> Iterator[str]:
        invocation_id = str(prepared["invocation_id"])
        try:
            yield from stream_prepared_send(
                runtime.connection,
                runtime.lock,
                prepared,
                payload.secret,
                cancelled,
            )
        finally:
            try:
                abandon_if_running(
                    runtime.connection,
                    runtime.lock,
                    invocation_id=invocation_id,
                    cancelled=cancelled,
                )
            finally:
                runtime.cancel_events.pop(invocation_id, None)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _invocations_cancel(invocation_id: str, request: Request) -> dict[str, object]:
    runtime = _runtime(request)
    in_flight = runtime.cancel_events.get(invocation_id)
    if in_flight is not None:
        in_flight.set()
    with runtime.lock:
        return cancel_invocation(
            runtime.connection,
            runtime.lock,
            runtime.cancel_events,
            invocation_id,
        )


def _agent_roles_list(workspace_id: str, request: Request) -> dict[str, object]:
    runtime = _runtime(request)
    with runtime.lock:
        return list_agent_roles(runtime.connection, workspace_id)


def _agent_roles_get(workspace_id: str, role_id: str, request: Request) -> dict[str, object]:
    if role_id not in EMPLOYEE_ROLE_IDS:
        raise DesktopApiError(404, "desktop_agent_role_not_found")
    runtime = _runtime(request)
    with runtime.lock:
        return get_agent_role(runtime.connection, workspace_id, role_id)


def _agent_roles_update(
    workspace_id: str,
    role_id: str,
    payload: AgentRoleUpdateRequest,
    request: Request,
) -> dict[str, object]:
    if role_id not in EMPLOYEE_ROLE_IDS:
        raise DesktopApiError(404, "desktop_agent_role_not_found")
    runtime = _runtime(request)
    with runtime.lock:
        return update_agent_role(runtime.connection, workspace_id, role_id, payload.model_dump())


def _agent_roles_test(workspace_id: str, role_id: str, request: Request) -> dict[str, object]:
    if role_id not in EMPLOYEE_ROLE_IDS:
        raise DesktopApiError(404, "desktop_agent_role_not_found")
    runtime = _runtime(request)
    with runtime.lock:
        return test_agent_role(runtime.connection, workspace_id, role_id)


def _team_runs_list(workspace_id: str, request: Request) -> dict[str, object]:
    runtime = _runtime(request)
    with runtime.lock:
        return list_team_runs(runtime.connection, workspace_id)


def _team_runs_start(
    workspace_id: str, payload: TeamRunStartRequest, request: Request
) -> dict[str, object]:
    runtime = _runtime(request)
    with runtime.lock:
        return start_team_run(runtime.connection, workspace_id, payload.model_dump())


def _team_runs_get(workspace_id: str, team_run_id: str, request: Request) -> dict[str, object]:
    if not _TEAM_RUN_ID_PATTERN.fullmatch(team_run_id):
        raise DesktopApiError(404, "desktop_team_run_not_found")
    runtime = _runtime(request)
    with runtime.lock:
        return get_team_run(runtime.connection, workspace_id, team_run_id)


def _team_runs_cancel(workspace_id: str, team_run_id: str, request: Request) -> dict[str, object]:
    if not _TEAM_RUN_ID_PATTERN.fullmatch(team_run_id):
        raise DesktopApiError(409, "desktop_team_run_not_found")
    runtime = _runtime(request)
    with runtime.lock:
        return cancel_team_run(runtime.connection, workspace_id, team_run_id)


def _team_runs_propose(
    workspace_id: str,
    team_run_id: str,
    payload: TeamRunProposalRequest,
    request: Request,
) -> dict[str, object]:
    if not _TEAM_RUN_ID_PATTERN.fullmatch(team_run_id):
        raise DesktopApiError(404, "desktop_team_run_not_found")
    runtime = _runtime(request)
    with runtime.lock:
        return submit_parent_proposal(
            runtime.connection,
            workspace_id,
            team_run_id,
            payload.proposal,
        )


def _team_runs_blackboard(
    workspace_id: str, team_run_id: str, request: Request
) -> dict[str, object]:
    if not _TEAM_RUN_ID_PATTERN.fullmatch(team_run_id):
        raise DesktopApiError(404, "desktop_team_run_not_found")
    runtime = _runtime(request)
    with runtime.lock:
        return get_team_blackboard(runtime.connection, workspace_id, team_run_id)


def _team_runs_collaboration(
    workspace_id: str,
    team_run_id: str,
    payload: TeamCollaborationCreateRequest,
    request: Request,
) -> dict[str, object]:
    if not _TEAM_RUN_ID_PATTERN.fullmatch(team_run_id):
        raise DesktopApiError(404, "desktop_team_run_not_found")
    runtime = _runtime(request)
    with runtime.lock:
        return record_collaboration_request(
            runtime.connection,
            workspace_id,
            team_run_id,
            {
                "fromAssignmentId": payload.from_assignment_id,
                "fromEmployeeRoleId": payload.from_employee_role_id,
                "targetRoleId": payload.target_role_id,
                "question": payload.question,
                "reason": payload.reason,
                "node_id": payload.node_id,
                "report_id": payload.report_id,
            },
        )


def _team_runs_append_budget(
    workspace_id: str,
    team_run_id: str,
    payload: TeamRunBudgetAppendRequest,
    request: Request,
) -> dict[str, object]:
    if not _TEAM_RUN_ID_PATTERN.fullmatch(team_run_id):
        raise DesktopApiError(404, "desktop_team_run_not_found")
    runtime = _runtime(request)
    with runtime.lock:
        return append_team_run_budget(
            runtime.connection,
            workspace_id,
            team_run_id,
            {
                "maximumProviderCalls": payload.maximum_provider_calls,
                "maximumWallTimeMs": payload.maximum_wall_time_ms,
                "maximumConcurrentCalls": payload.maximum_concurrent_calls,
                "maximumInputCharacters": payload.maximum_input_characters,
                "maximumOutputCharacters": payload.maximum_output_characters,
            },
        )


def _team_runs_set_state(
    workspace_id: str,
    team_run_id: str,
    payload: TeamRunStateRequest,
    request: Request,
) -> dict[str, object]:
    if not _TEAM_RUN_ID_PATTERN.fullmatch(team_run_id):
        raise DesktopApiError(404, "desktop_team_run_not_found")
    runtime = _runtime(request)
    with runtime.lock:
        return set_team_run_state(
            runtime.connection,
            workspace_id,
            team_run_id,
            payload.state,
            parent_final_answer=payload.parent_final_answer,
        )


def _team_runs_consume_call(
    workspace_id: str, team_run_id: str, request: Request
) -> dict[str, object]:
    if not _TEAM_RUN_ID_PATTERN.fullmatch(team_run_id):
        raise DesktopApiError(404, "desktop_team_run_not_found")
    runtime = _runtime(request)
    with runtime.lock:
        return consume_provider_call(runtime.connection, workspace_id, team_run_id)


def _team_runs_assignment_execution(
    workspace_id: str,
    team_run_id: str,
    payload: TeamAssignmentExecutionRequest,
    request: Request,
) -> dict[str, object]:
    if not _TEAM_RUN_ID_PATTERN.fullmatch(team_run_id):
        raise DesktopApiError(404, "desktop_team_run_not_found")
    runtime = _runtime(request)
    with runtime.lock:
        return set_assignment_effective_execution(
            runtime.connection,
            workspace_id,
            team_run_id,
            payload.assignment_id,
            payload.effective_execution,
        )


def _team_runs_create_node(
    workspace_id: str,
    team_run_id: str,
    payload: TeamNodeCreateRequest,
    request: Request,
) -> dict[str, object]:
    if not _TEAM_RUN_ID_PATTERN.fullmatch(team_run_id):
        raise DesktopApiError(404, "desktop_team_run_not_found")
    runtime = _runtime(request)
    with runtime.lock:
        return create_team_node(
            runtime.connection,
            workspace_id,
            team_run_id,
            payload.model_dump(),
        )


def _team_runs_update_node(
    workspace_id: str,
    team_run_id: str,
    node_id: str,
    payload: TeamNodeUpdateRequest,
    request: Request,
) -> dict[str, object]:
    if not _TEAM_RUN_ID_PATTERN.fullmatch(team_run_id):
        raise DesktopApiError(404, "desktop_team_run_not_found")
    runtime = _runtime(request)
    with runtime.lock:
        return update_team_node(
            runtime.connection,
            workspace_id,
            team_run_id,
            node_id,
            payload.model_dump(),
        )


def _team_runs_record_report(
    workspace_id: str,
    team_run_id: str,
    payload: TeamEmployeeReportRequest,
    request: Request,
) -> dict[str, object]:
    if not _TEAM_RUN_ID_PATTERN.fullmatch(team_run_id):
        raise DesktopApiError(404, "desktop_team_run_not_found")
    runtime = _runtime(request)
    with runtime.lock:
        return record_employee_report(
            runtime.connection,
            workspace_id,
            team_run_id,
            payload.model_dump(),
        )


def _team_runs_settle_node(
    workspace_id: str,
    team_run_id: str,
    node_id: str,
    payload: TeamNodeSettleRequest,
    request: Request,
) -> dict[str, object]:
    if not _TEAM_RUN_ID_PATTERN.fullmatch(team_run_id):
        raise DesktopApiError(404, "desktop_team_run_not_found")
    runtime = _runtime(request)
    with runtime.lock:
        return settle_team_node(
            runtime.connection,
            workspace_id,
            team_run_id,
            node_id,
            payload.model_dump(),
        )


def _team_runs_resolve_collaboration(
    workspace_id: str,
    team_run_id: str,
    request_id: str,
    payload: TeamCollaborationResolveRequest,
    request: Request,
) -> dict[str, object]:
    if not _TEAM_RUN_ID_PATTERN.fullmatch(team_run_id):
        raise DesktopApiError(404, "desktop_team_run_not_found")
    runtime = _runtime(request)
    with runtime.lock:
        return resolve_collaboration_request(
            runtime.connection,
            workspace_id,
            team_run_id,
            request_id,
            payload.parent_decision,
            payload.resolved_assignment_id,
        )


def create_desktop_local_app(config: DesktopLocalAppConfig) -> FastAPI:
    """Create an isolated desktop app without importing the server Settings singleton."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        connection = open_database(config.storage)
        try:
            migrate_database(connection, config.storage)
            app.state.desktop_runtime = _DesktopRuntime(
                connection=connection,
                lock=threading.RLock(),
                cancel_events={},
            )
            recover_interrupted_invocations(connection)
            recover_interrupted_team_runs(connection)
            yield
        finally:
            connection.close()

    app = FastAPI(
        title="OmniBase Desktop Local",
        version=config.storage.application_version,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    # This non-secret state lets the native shell verify what it asked Uvicorn
    # to bind without importing or serializing the storage path.
    app.state.desktop_app_config = config
    app.state.desktop_bind_host = config.bind_host
    app.state.desktop_port = config.port

    app.middleware("http")(_instance_binding_middleware)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_api_route("/health", _health, methods=["GET"])
    app.add_api_route("/health/ready", _readiness, methods=["GET"])
    app.add_api_route(
        "/desktop/v1/owner",
        _owner_status,
        methods=["GET"],
    )
    app.add_api_route(
        "/desktop/v1/owner/bootstrap",
        _owner_bootstrap,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces",
        _workspace_list,
        methods=["GET"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces",
        _workspace_create,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/archive",
        _workspace_archive,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/agent",
        _workspace_parent_agent,
        methods=["GET"],
    )
    app.add_api_route("/desktop/v1/providers", _providers_list, methods=["GET"])
    app.add_api_route("/desktop/v1/providers", _providers_upsert, methods=["POST"])
    app.add_api_route(
        "/desktop/v1/provider-endpoints/pin",
        _providers_pin_endpoint,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/providers/{provider_id}",
        _providers_delete,
        methods=["DELETE"],
    )
    app.add_api_route(
        "/desktop/v1/providers/{provider_id}/vault",
        _providers_vault,
        methods=["GET"],
    )
    app.add_api_route(
        "/desktop/v1/providers/{provider_id}/test",
        _providers_test,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/conversations",
        _conversations_list,
        methods=["GET"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/conversations",
        _conversations_create,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/conversations/{conversation_id}",
        _conversations_get,
        methods=["GET"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/conversations/{conversation_id}/archive",
        _conversations_archive,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/conversations/{conversation_id}/messages",
        _conversations_send,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/invocations/{invocation_id}/cancel",
        _invocations_cancel,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/agent-roles",
        _agent_roles_list,
        methods=["GET"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/agent-roles/{role_id}",
        _agent_roles_get,
        methods=["GET"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/agent-roles/{role_id}",
        _agent_roles_update,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/agent-roles/{role_id}/test",
        _agent_roles_test,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/team-runs",
        _team_runs_list,
        methods=["GET"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/team-runs",
        _team_runs_start,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}",
        _team_runs_get,
        methods=["GET"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/cancel",
        _team_runs_cancel,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/proposals",
        _team_runs_propose,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/blackboard",
        _team_runs_blackboard,
        methods=["GET"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/collaboration-requests",
        _team_runs_collaboration,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/budget",
        _team_runs_append_budget,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/state",
        _team_runs_set_state,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/consume-call",
        _team_runs_consume_call,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/assignments/execution",
        _team_runs_assignment_execution,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes",
        _team_runs_create_node,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/{node_id}",
        _team_runs_update_node,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/nodes/{node_id}/settle",
        _team_runs_settle_node,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/reports",
        _team_runs_record_report,
        methods=["POST"],
    )
    app.add_api_route(
        "/desktop/v1/workspaces/{workspace_id}/team-runs/{team_run_id}/collaboration-requests/{request_id}/resolve",
        _team_runs_resolve_collaboration,
        methods=["POST"],
    )

    return app


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m omnibase.desktop_local.app",
        description="Run the loopback-only OmniBase personal desktop backend.",
    )
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--application-version", default="1.0.0")
    parser.add_argument("--host", choices=(_LOOPBACK_HOST,), default=_LOOPBACK_HOST)
    parser.add_argument("--port", type=int, default=47_431)
    return parser


def _load_runtime_secrets(environ: Mapping[str, str]) -> tuple[str, str, str]:
    """Read only launcher-owned secrets; never parse ambient configuration."""

    authorization_token = environ.get(DESKTOP_INSTANCE_TOKEN_ENV)
    native_proof_key = environ.get(DESKTOP_NATIVE_PROOF_KEY_ENV)
    native_control_token = environ.get(DESKTOP_NATIVE_CONTROL_TOKEN_ENV)
    if authorization_token is None:
        raise ValueError("desktop_instance_token_environment_missing")
    if native_proof_key is None:
        raise ValueError("desktop_native_proof_key_environment_missing")
    if native_control_token is None:
        raise ValueError("desktop_native_control_token_environment_missing")
    return authorization_token, native_proof_key, native_control_token


def main(argv: Sequence[str] | None = None) -> int:
    """Run the desktop-local server from explicit command-line arguments only."""

    parser = _build_parser()
    cli_args = list(sys.argv[1:] if argv is None else argv)
    if any(
        argument in {"--instance-token", "--native-proof-key", "--native-control-token"}
        or argument.startswith("--instance-token=")
        or argument.startswith("--native-proof-key=")
        or argument.startswith("--native-control-token=")
        for argument in cli_args
    ):
        parser.error("desktop_runtime_secret_cli_forbidden")
    args = parser.parse_args(cli_args)
    try:
        instance_token, native_proof_key, native_control_token = _load_runtime_secrets(os.environ)
        config = DesktopLocalAppConfig(
            storage=DesktopLocalConfig(
                data_root=args.data_root,
                application_version=args.application_version,
            ),
            instance_token=instance_token,
            native_proof_key=native_proof_key,
            native_control_token=native_control_token,
            bind_host=args.host,
            port=args.port,
        )
    except (DesktopLocalError, ValueError) as exc:
        parser.error(str(exc))

    uvicorn.run(
        create_desktop_local_app(config),
        host=_LOOPBACK_HOST,
        port=config.port,
        access_log=False,
        env_file=None,
        forwarded_allow_ips="",
        http="h11",
        lifespan="on",
        loop="asyncio",
        proxy_headers=False,
        reload=False,
        server_header=False,
        ws="none",
        workers=1,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through ``main``
    raise SystemExit(main())
