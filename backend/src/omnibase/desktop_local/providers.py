"""Native-control Provider registry for the desktop-local SQLite store."""

from __future__ import annotations

import re
import sqlite3
import threading
import uuid
from typing import Any

from omnibase.desktop_local.database import utc_now_text
from omnibase.desktop_local.endpoint import DesktopEndpointError, resolve_provider_endpoint
from omnibase.desktop_local.errors import DesktopLocalError
from omnibase.desktop_local.family import (
    plan_desktop_adaptation,
    resolve_desktop_family,
)
from omnibase.desktop_local.provider_http import (
    DesktopProviderCallError,
    test_provider_endpoint,
)
from omnibase.desktop_local.redaction import public_error_message
from omnibase.desktop_local.repository import append_audit_event

_PROVIDER_ID_PATTERN = re.compile(r"provider_[0-9a-f]{32}\Z")
_FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_MAX_PROVIDERS = 16
_ALLOWED_GEARS = frozenset({"economy", "standard", "deep", "audit"})
_ALLOWED_DEPTHS = frozenset({"disabled", "low", "medium", "high"})
_CREDENTIAL_REFERENCE = "electron-safe-storage:v1"


class DesktopApiError(DesktopLocalError):
    def __init__(self, status_code: int, code: str) -> None:
        self.status_code = status_code
        super().__init__(code)


def _require_owner(connection: sqlite3.Connection) -> sqlite3.Row:
    owner = connection.execute("SELECT id FROM owner WHERE singleton_key = 1").fetchone()
    if owner is None:
        raise DesktopApiError(409, "desktop_owner_not_initialized")
    return owner


def provider_public_payload(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "display_name": str(row["display_name"]),
        "base_url": str(row["base_url"]),
        "model_name": str(row["model_name"]),
        "family": str(row["family"]),
        "gear": str(row["gear"]),
        "thinking_depth": str(row["thinking_depth"]),
        "timeout_seconds": int(row["timeout_seconds"]),
        "allow_loopback_http": bool(row["allow_loopback_http"]),
        "is_default": bool(row["is_default"]),
        "is_enabled": bool(row["is_enabled"]),
        "has_secret": True,
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def list_providers(connection: sqlite3.Connection) -> dict[str, object]:
    owner = _require_owner(connection)
    rows = connection.execute(
        "SELECT id, display_name, base_url, model_name, family, gear, thinking_depth, "
        "timeout_seconds, allow_loopback_http, is_default, is_enabled, created_at, updated_at "
        "FROM provider WHERE owner_id = ? ORDER BY is_default DESC, created_at, id",
        (owner["id"],),
    ).fetchall()
    return {"items": [provider_public_payload(row) for row in rows]}


def load_provider_secret_material(
    connection: sqlite3.Connection,
    provider_id: str,
) -> dict[str, str]:
    if not _PROVIDER_ID_PATTERN.fullmatch(provider_id):
        raise DesktopApiError(404, "desktop_provider_not_found")
    owner = _require_owner(connection)
    row = connection.execute(
        "SELECT id, credential_reference, encrypted_secret_blob FROM provider "
        "WHERE id = ? AND owner_id = ?",
        (provider_id, owner["id"]),
    ).fetchone()
    if row is None:
        raise DesktopApiError(404, "desktop_provider_not_found")
    return {
        "id": str(row["id"]),
        "credential_reference": str(row["credential_reference"]),
        "encrypted_secret_blob": str(row["encrypted_secret_blob"]),
    }


def _validate_upsert(payload: dict[str, Any]) -> dict[str, Any]:
    display_name = payload.get("display_name")
    base_url = payload.get("base_url")
    model_name = payload.get("model_name")
    gear = payload.get("gear")
    thinking_depth = payload.get("thinking_depth")
    timeout_seconds = payload.get("timeout_seconds")
    allow_loopback_http = payload.get("allow_loopback_http")
    is_default = payload.get("is_default")
    is_enabled = payload.get("is_enabled")
    if (
        not isinstance(display_name, str)
        or not isinstance(base_url, str)
        or not isinstance(model_name, str)
        or gear not in _ALLOWED_GEARS
        or thinking_depth not in _ALLOWED_DEPTHS
        or not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or not 5 <= timeout_seconds <= 120
        or not isinstance(allow_loopback_http, bool)
        or not isinstance(is_default, bool)
        or not isinstance(is_enabled, bool)
    ):
        raise DesktopApiError(422, "desktop_validation_error")
    normalized_name = display_name.strip()
    normalized_model = model_name.strip()
    if (
        not normalized_name
        or not normalized_model
        or _CONTROL_CHARACTER_PATTERN.search(display_name)
        or _CONTROL_CHARACTER_PATTERN.search(model_name)
        or len(normalized_name) > 256
        or len(normalized_model) > 256
    ):
        raise DesktopApiError(422, "desktop_validation_error")
    try:
        resolve_provider_endpoint(base_url, allow_loopback_http=allow_loopback_http)
    except DesktopEndpointError as exc:
        raise DesktopApiError(422, exc.code) from exc
    return {
        "display_name": normalized_name,
        "base_url": base_url.strip(),
        "model_name": normalized_model,
        "gear": gear,
        "thinking_depth": thinking_depth,
        "timeout_seconds": timeout_seconds,
        "allow_loopback_http": 1 if allow_loopback_http else 0,
        "is_default": 1 if is_default else 0,
        "is_enabled": 1 if is_enabled else 0,
        "family": resolve_desktop_family(normalized_model, base_url.strip()),
    }


def upsert_provider(  # noqa: C901 - create/update, default rotation and vault checks
    connection: sqlite3.Connection, payload: dict[str, Any]
) -> dict[str, object]:
    fields = _validate_upsert(payload)
    provider_id = payload.get("id")
    rotating = payload.get("encrypted_secret_blob") is not None
    if rotating:
        blob = payload.get("encrypted_secret_blob")
        fingerprint = payload.get("secret_fingerprint")
        reference = payload.get("credential_reference")
        if (
            not isinstance(blob, str)
            or not isinstance(fingerprint, str)
            or not isinstance(reference, str)
            or not 1 <= len(blob) <= 8192
            or not _FINGERPRINT_PATTERN.fullmatch(fingerprint)
            or reference != _CREDENTIAL_REFERENCE
            or blob.startswith("sk-")
            or "Bearer " in blob
            or "Authorization" in blob
        ):
            raise DesktopApiError(422, "desktop_validation_error")
    elif provider_id is None:
        raise DesktopApiError(422, "desktop_provider_secret_required")
    connection.execute("BEGIN IMMEDIATE")
    try:
        owner = _require_owner(connection)
        now = utc_now_text()
        if provider_id is None:
            count = connection.execute(
                "SELECT COUNT(*) FROM provider WHERE owner_id = ?",
                (owner["id"],),
            ).fetchone()
            if count is None or int(count[0]) >= _MAX_PROVIDERS:
                raise DesktopApiError(409, "desktop_provider_capacity_reached")
            provider_id = f"provider_{uuid.uuid4().hex}"
            if fields["is_default"]:
                connection.execute(
                    "UPDATE provider SET is_default = 0, updated_at = ? WHERE owner_id = ?",
                    (now, owner["id"]),
                )
            connection.execute(
                "INSERT INTO provider ("
                "id, owner_id, display_name, base_url, model_name, family, gear, "
                "thinking_depth, timeout_seconds, allow_loopback_http, is_default, "
                "is_enabled, credential_reference, encrypted_secret_blob, "
                "secret_fingerprint, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    provider_id,
                    owner["id"],
                    fields["display_name"],
                    fields["base_url"],
                    fields["model_name"],
                    fields["family"],
                    fields["gear"],
                    fields["thinking_depth"],
                    fields["timeout_seconds"],
                    fields["allow_loopback_http"],
                    fields["is_default"],
                    fields["is_enabled"],
                    _CREDENTIAL_REFERENCE,
                    payload["encrypted_secret_blob"],
                    payload["secret_fingerprint"],
                    now,
                    now,
                ),
            )
            event_type = "provider_created"
        else:
            if not isinstance(provider_id, str) or not _PROVIDER_ID_PATTERN.fullmatch(provider_id):
                raise DesktopApiError(404, "desktop_provider_not_found")
            existing = connection.execute(
                "SELECT id FROM provider WHERE id = ? AND owner_id = ?",
                (provider_id, owner["id"]),
            ).fetchone()
            if existing is None:
                raise DesktopApiError(404, "desktop_provider_not_found")
            if fields["is_default"]:
                connection.execute(
                    "UPDATE provider SET is_default = 0, updated_at = ? "
                    "WHERE owner_id = ? AND id <> ?",
                    (now, owner["id"], provider_id),
                )
            assignments = [
                "display_name = ?",
                "base_url = ?",
                "model_name = ?",
                "family = ?",
                "gear = ?",
                "thinking_depth = ?",
                "timeout_seconds = ?",
                "allow_loopback_http = ?",
                "is_default = ?",
                "is_enabled = ?",
                "updated_at = ?",
            ]
            values: list[object] = [
                fields["display_name"],
                fields["base_url"],
                fields["model_name"],
                fields["family"],
                fields["gear"],
                fields["thinking_depth"],
                fields["timeout_seconds"],
                fields["allow_loopback_http"],
                fields["is_default"],
                fields["is_enabled"],
                now,
            ]
            if rotating:
                assignments.extend(
                    [
                        "credential_reference = ?",
                        "encrypted_secret_blob = ?",
                        "secret_fingerprint = ?",
                    ]
                )
                values.extend(
                    [
                        _CREDENTIAL_REFERENCE,
                        payload["encrypted_secret_blob"],
                        payload["secret_fingerprint"],
                    ]
                )
            values.extend([provider_id, owner["id"]])
            connection.execute(
                "UPDATE provider SET "  # noqa: S608
                + ", ".join(assignments)
                + " WHERE id = ? AND owner_id = ?",
                values,
            )
            event_type = "provider_updated"
        append_audit_event(
            connection,
            event_id=f"event_{uuid.uuid4().hex}",
            owner_id=str(owner["id"]),
            event_type=event_type,
            payload={
                "provider_id": provider_id,
                "family": fields["family"],
                "is_default": bool(fields["is_default"]),
                "is_enabled": bool(fields["is_enabled"]),
                "secret_rotated": rotating,
            },
        )
        row = connection.execute(
            "SELECT id, display_name, base_url, model_name, family, gear, thinking_depth, "
            "timeout_seconds, allow_loopback_http, is_default, is_enabled, created_at, "
            "updated_at FROM provider WHERE id = ?",
            (provider_id,),
        ).fetchone()
        if row is None:
            raise sqlite3.IntegrityError("provider upsert did not converge")
        connection.execute("COMMIT")
    except DesktopApiError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopLocalError("desktop_provider_upsert_failed") from None
    return {"provider": provider_public_payload(row)}


def delete_provider(connection: sqlite3.Connection, provider_id: str) -> dict[str, object]:
    if not _PROVIDER_ID_PATTERN.fullmatch(provider_id):
        raise DesktopApiError(404, "desktop_provider_not_found")
    connection.execute("BEGIN IMMEDIATE")
    try:
        owner = _require_owner(connection)
        in_use = connection.execute(
            "SELECT COUNT(*) FROM invocation WHERE provider_id = ? AND status = 'running'",
            (provider_id,),
        ).fetchone()
        if in_use is not None and int(in_use[0]) > 0:
            raise DesktopApiError(409, "desktop_provider_in_use")
        deleted = connection.execute(
            "DELETE FROM provider WHERE id = ? AND owner_id = ?",
            (provider_id, owner["id"]),
        )
        if deleted.rowcount != 1:
            raise DesktopApiError(404, "desktop_provider_not_found")
        append_audit_event(
            connection,
            event_id=f"event_{uuid.uuid4().hex}",
            owner_id=str(owner["id"]),
            event_type="provider_deleted",
            payload={"provider_id": provider_id},
        )
        connection.execute("COMMIT")
    except DesktopApiError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopLocalError("desktop_provider_delete_failed") from None
    return {"deleted": True, "id": provider_id}


def test_provider(
    connection: sqlite3.Connection,
    lock: threading.RLock,
    provider_id: str,
    secret: str,
    cancelled: threading.Event,
) -> dict[str, object]:
    if not _PROVIDER_ID_PATTERN.fullmatch(provider_id):
        raise DesktopApiError(404, "desktop_provider_not_found")
    if not isinstance(secret, str) or not 1 <= len(secret) <= 512:
        raise DesktopApiError(422, "desktop_provider_secret_required")
    with lock:
        owner = _require_owner(connection)
        row = connection.execute(
            "SELECT * FROM provider WHERE id = ? AND owner_id = ?",
            (provider_id, owner["id"]),
        ).fetchone()
    if row is None:
        raise DesktopApiError(404, "desktop_provider_not_found")
    try:
        endpoint = resolve_provider_endpoint(
            str(row["base_url"]),
            allow_loopback_http=bool(row["allow_loopback_http"]),
        )
        adaptation = plan_desktop_adaptation(
            str(row["model_name"]),
            str(row["base_url"]),
            str(row["gear"]),  # type: ignore[arg-type]
            str(row["thinking_depth"]),  # type: ignore[arg-type]
        )
        result = test_provider_endpoint(
            endpoint,
            secret=secret,
            model_name=str(row["model_name"]),
            adaptation=adaptation,
            timeout_seconds=float(row["timeout_seconds"]),
            cancelled=cancelled.is_set,
        )
        result["provider_id"] = provider_id
        result["provider_name"] = str(row["display_name"])
        return result
    except DesktopEndpointError as exc:
        raise DesktopApiError(422, exc.code) from exc
    except DesktopProviderCallError as exc:
        return {
            "ok": False,
            "provider_id": provider_id,
            "provider_name": str(row["display_name"]),
            "requested_model": str(row["model_name"]),
            "actual_model": None,
            "family": str(row["family"]),
            "error_code": exc.code,
            "error_redacted": public_error_message(exc.code, exc.public_message),
        }


def resolve_enabled_provider(
    connection: sqlite3.Connection,
    owner_id: str,
    provider_id: str | None,
) -> sqlite3.Row:
    if provider_id is not None:
        if not _PROVIDER_ID_PATTERN.fullmatch(provider_id):
            raise DesktopApiError(404, "desktop_provider_not_found")
        row = connection.execute(
            "SELECT * FROM provider WHERE id = ? AND owner_id = ? AND is_enabled = 1",
            (provider_id, owner_id),
        ).fetchone()
        if row is None:
            raise DesktopApiError(404, "desktop_provider_not_found")
        return row
    rows = connection.execute(
        "SELECT * FROM provider WHERE owner_id = ? AND is_enabled = 1 AND is_default = 1",
        (owner_id,),
    ).fetchall()
    if len(rows) != 1:
        raise DesktopApiError(409, "desktop_provider_ambiguous")
    return rows[0]
