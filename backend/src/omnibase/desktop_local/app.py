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
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from omnibase.desktop_local.config import DesktopLocalConfig
from omnibase.desktop_local.database import local_health, migrate_database, open_database
from omnibase.desktop_local.errors import DesktopLocalError
from omnibase.desktop_local.repository import append_audit_event, create_owner

DESKTOP_INSTANCE_HEADER = "x-omnibase-desktop-instance"
DESKTOP_CHALLENGE_HEADER = "x-omnibase-desktop-challenge"
DESKTOP_PROOF_HEADER = "x-omnibase-desktop-proof"
DESKTOP_INSTANCE_TOKEN_ENV = "OMNIBASE_DESKTOP_INSTANCE_TOKEN"
DESKTOP_NATIVE_PROOF_KEY_ENV = "OMNIBASE_DESKTOP_NATIVE_PROOF_KEY"
_LOOPBACK_HOST = "127.0.0.1"
_INSTANCE_TOKEN_PATTERN = re.compile(r"[0-9a-fA-F]{64}\Z")
_CHALLENGE_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_APPLICATION_VERSION_PATTERN = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,63}\Z")


@dataclass(frozen=True, slots=True)
class DesktopLocalAppConfig:
    """Explicit server configuration supplied by the trusted desktop launcher."""

    storage: DesktopLocalConfig
    instance_token: str
    native_proof_key: str
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
        object.__setattr__(self, "instance_token", self.instance_token.lower())
        object.__setattr__(self, "native_proof_key", self.native_proof_key.lower())


@dataclass(slots=True)
class _DesktopRuntime:
    connection: sqlite3.Connection
    lock: threading.RLock


class OwnerBootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    display_name: str = Field(min_length=1, max_length=256)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("display_name_must_not_be_blank")
        return normalized


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
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


async def _instance_binding_middleware(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    config = _app_config(request)
    supplied = request.headers.getlist(DESKTOP_INSTANCE_HEADER)
    if (
        len(supplied) != 1
        or not _INSTANCE_TOKEN_PATTERN.fullmatch(supplied[0])
        or not hmac.compare_digest(supplied[0].lower(), config.instance_token)
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
    request.state.desktop_challenge = challenges[0] if challenges else None
    try:
        response = await call_next(request)
    except DesktopLocalError as exc:
        response = _error_response(503, exc.code, "Desktop local service unavailable")
    except Exception:
        response = _error_response(
            500,
            "desktop_internal_error",
            "Desktop local request failed",
        )
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
            )
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
    app.add_api_route("/api/v1/owner", _owner_status, methods=["GET"])
    app.add_api_route(
        "/api/v1/owner/bootstrap",
        _owner_bootstrap,
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


def _load_runtime_secrets(environ: Mapping[str, str]) -> tuple[str, str]:
    """Read only the two launcher-owned secrets; never parse ambient config."""

    authorization_token = environ.get(DESKTOP_INSTANCE_TOKEN_ENV)
    native_proof_key = environ.get(DESKTOP_NATIVE_PROOF_KEY_ENV)
    if authorization_token is None:
        raise ValueError("desktop_instance_token_environment_missing")
    if native_proof_key is None:
        raise ValueError("desktop_native_proof_key_environment_missing")
    return authorization_token, native_proof_key


def main(argv: Sequence[str] | None = None) -> int:
    """Run the desktop-local server from explicit command-line arguments only."""

    parser = _build_parser()
    cli_args = list(sys.argv[1:] if argv is None else argv)
    if any(
        argument in {"--instance-token", "--native-proof-key"}
        or argument.startswith("--instance-token=")
        or argument.startswith("--native-proof-key=")
        for argument in cli_args
    ):
        parser.error("desktop_runtime_secret_cli_forbidden")
    args = parser.parse_args(cli_args)
    try:
        instance_token, native_proof_key = _load_runtime_secrets(os.environ)
        config = DesktopLocalAppConfig(
            storage=DesktopLocalConfig(
                data_root=args.data_root,
                application_version=args.application_version,
            ),
            instance_token=instance_token,
            native_proof_key=native_proof_key,
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
