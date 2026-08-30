"""Transactional P7.3 Workspace component kernel.

The service owns logical registry and authority state only.  It never executes
an adapter, resolves a host path, reads a secret, or accepts a command line.
Invocation begin and settle form the durable boundary around a trusted native
adapter call.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

from omnibase.desktop_local.components.catalog import (
    canonical_json,
    digest_json,
    validate_closed_manifest,
)
from omnibase.desktop_local.database import utc_now_text
from omnibase.desktop_local.providers import DesktopApiError
from omnibase.desktop_local.repository import append_audit_event

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPERATION = re.compile(r"^[a-z][a-z0-9_.]{2,63}$")
_REASON_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_MESSAGE_ID = re.compile(r"^message_[0-9a-f]{32}$")
_LOGICAL_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._:-][a-z0-9]+)*$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_ASSISTANT_PROPOSAL_KEYS = frozenset(
    {
        "change_kind",
        "component_id",
        "dependency_graph",
        "desired_configuration",
        "desired_slot_bindings",
        "expected_revision",
        "manifest_sha256",
        "package_sha256",
        "policy_manifest_sha256",
        "requested_grants",
        "target_version",
        "type",
    }
)
_TERMINAL_OPERATIONS = {
    "succeeded",
    "failed",
    "reconciled_succeeded",
    "reconciled_failed",
}


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _decode_json(value: str) -> object:
    return json.loads(value)


def _live_workspace(connection: sqlite3.Connection, workspace_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT workspace.* FROM workspace JOIN owner ON owner.id = workspace.owner_id "
        "WHERE workspace.id = ?",
        (workspace_id,),
    ).fetchone()
    if row is None:
        raise DesktopApiError(404, "desktop_workspace_not_found")
    if row["state"] != "active":
        raise DesktopApiError(409, "desktop_workspace_archived")
    return row


def _catalog_row(connection: sqlite3.Connection, component_id: str, version: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM component_catalog_version WHERE component_id = ? AND version = ?",
        (component_id, version),
    ).fetchone()
    if row is None:
        raise DesktopApiError(404, "desktop_component_version_not_found")
    manifest = _decode_json(str(row["manifest_json"]))
    try:
        validate_closed_manifest(manifest)
    except ValueError as exc:
        raise DesktopApiError(409, "desktop_component_catalog_manifest_invalid") from exc
    if digest_json(manifest) != row["manifest_sha256"]:
        raise DesktopApiError(409, "desktop_component_catalog_digest_drift")
    return row


def _find_package_identity(
    connection: sqlite3.Connection, catalog: sqlite3.Row, workspace_id: str
) -> sqlite3.Row | None:
    if catalog["publisher_class"] == "owner_reviewed":
        row = connection.execute(
            "SELECT package.* FROM component_package_attestation AS package "
            "JOIN component_catalog_registration AS registration "
            "ON registration.component_id = package.component_id "
            "AND registration.version = package.version "
            "AND registration.manifest_sha256 = package.manifest_sha256 "
            "AND registration.package_sha256 = package.package_sha256 "
            "WHERE package.component_id = ? AND package.version = ? "
            "AND package.attested_by = 'owner_native_review' "
            "AND registration.workspace_id = ? "
            "ORDER BY package.created_at DESC, package.manifest_sha256, package.package_sha256 LIMIT 1",
            (catalog["component_id"], catalog["version"], workspace_id),
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT * FROM component_package_attestation WHERE component_id = ? AND version = ? "
            "AND attested_by = 'runtime_manifest' "
            "ORDER BY created_at DESC, manifest_sha256, package_sha256 LIMIT 1",
            (catalog["component_id"], catalog["version"]),
        ).fetchone()
    return row


def _package_identity(
    connection: sqlite3.Connection, catalog: sqlite3.Row, workspace_id: str
) -> sqlite3.Row:
    row = _find_package_identity(connection, catalog, workspace_id)
    if row is None:
        raise DesktopApiError(409, "desktop_component_package_not_attested")
    return row


def attest_component_package(
    connection: sqlite3.Connection,
    *,
    component_id: str,
    version: str,
    policy_manifest_sha256: str,
    manifest_sha256: str,
    package_sha256: str,
    inventory_sha256: str,
    adapter_id: str,
) -> dict[str, object]:
    """Bind a verified runtime-manifest package to the closed source policy."""

    if any(
        _SHA256.fullmatch(value) is None
        for value in (policy_manifest_sha256, manifest_sha256, package_sha256, inventory_sha256)
    ):
        raise DesktopApiError(400, "desktop_component_attestation_invalid")
    try:
        connection.execute("BEGIN IMMEDIATE")
        catalog = _catalog_row(connection, component_id, version)
        if catalog["adapter_id"] != adapter_id:
            raise DesktopApiError(409, "desktop_component_attestation_adapter_mismatch")
        if catalog["manifest_sha256"] != policy_manifest_sha256:
            raise DesktopApiError(409, "desktop_component_attestation_policy_mismatch")
        existing = connection.execute(
            "SELECT * FROM component_package_attestation WHERE component_id = ? AND version = ? "
            "AND attested_by = 'runtime_manifest'",
            (component_id, version),
        ).fetchone()
        if existing is not None:
            expected = (policy_manifest_sha256, manifest_sha256, package_sha256, inventory_sha256)
            actual = (
                existing["policy_manifest_sha256"],
                existing["manifest_sha256"],
                existing["package_sha256"],
                existing["inventory_sha256"],
            )
            if actual != expected:
                raise DesktopApiError(409, "desktop_component_attestation_version_identity_drift")
            connection.execute("COMMIT")
            return {**dict(existing), "adapter_id": adapter_id, "replayed": True}
        now = utc_now_text()
        connection.execute(
            "INSERT INTO component_package_attestation "
            "(component_id, version, policy_manifest_sha256, manifest_sha256, package_sha256, "
            "inventory_sha256, attested_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'runtime_manifest', ?)",
            (
                component_id,
                version,
                policy_manifest_sha256,
                manifest_sha256,
                package_sha256,
                inventory_sha256,
                now,
            ),
        )
        connection.execute("COMMIT")
        return {
            "component_id": component_id,
            "version": version,
            "adapter_id": adapter_id,
            "policy_manifest_sha256": policy_manifest_sha256,
            "manifest_sha256": manifest_sha256,
            "package_sha256": package_sha256,
            "inventory_sha256": inventory_sha256,
            "attested_by": "runtime_manifest",
            "created_at": now,
            "replayed": False,
        }
    except DesktopApiError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopApiError(503, "desktop_component_attestation_failed") from exc


def register_owner_reviewed_component(  # noqa: C901 - registry, attestation and scope bind atomically
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    manifest: dict[str, object],
    manifest_sha256: str,
    package_sha256: str,
    inventory_sha256: str,
) -> dict[str, object]:
    """Register one native-verified declarative package without exposing its filesystem path."""

    try:
        validated = validate_closed_manifest(manifest)
    except ValueError as exc:
        raise DesktopApiError(400, str(exc)) from exc
    publisher = validated["publisher"]
    if not isinstance(publisher, dict):
        raise DesktopApiError(409, "desktop_component_owner_package_identity_invalid")
    if (
        validated["family"] != "declarative_ui"
        or publisher.get("classification") != "owner_reviewed"
        or validated["entrypoint"] != {"adapter_id": "builtin-ui.v1", "kind": "host_view_v1"}
        or digest_json(validated) != manifest_sha256
        or any(
            _SHA256.fullmatch(value) is None
            for value in (manifest_sha256, package_sha256, inventory_sha256)
        )
    ):
        raise DesktopApiError(409, "desktop_component_owner_package_identity_invalid")
    component_id = str(validated["component_id"])
    version = str(validated["version"])
    request = {
        "inventory_sha256": inventory_sha256,
        "manifest_sha256": manifest_sha256,
        "package_sha256": package_sha256,
        "workspace_id": workspace_id,
    }
    request_sha256 = digest_json(request)
    try:
        connection.execute("BEGIN IMMEDIATE")
        workspace = _live_workspace(connection, workspace_id)
        existing = connection.execute(
            "SELECT registration.*, catalog.publisher_class FROM component_catalog_registration "
            "AS registration JOIN component_catalog_version AS catalog "
            "ON catalog.component_id = registration.component_id AND catalog.version = registration.version "
            "WHERE registration.component_id = ? AND registration.version = ? "
            "AND registration.workspace_id = ?",
            (component_id, version, workspace_id),
        ).fetchone()
        if existing is not None:
            if existing["request_sha256"] != request_sha256:
                raise DesktopApiError(409, "desktop_component_owner_package_version_drift")
            connection.execute("COMMIT")
            return {
                "component_id": component_id,
                "version": version,
                "manifest_sha256": manifest_sha256,
                "package_sha256": package_sha256,
                "publisher_class": "owner_reviewed",
                "registered_at": existing["registered_at"],
                "replayed": True,
            }
        catalog_existing = connection.execute(
            "SELECT * FROM component_catalog_version WHERE component_id = ? AND version = ?",
            (component_id, version),
        ).fetchone()
        if catalog_existing is not None and (
            catalog_existing["publisher_class"] != "owner_reviewed"
            or catalog_existing["manifest_sha256"] != manifest_sha256
            or catalog_existing["package_sha256"] != package_sha256
            or catalog_existing["manifest_json"] != canonical_json(validated)
        ):
            raise DesktopApiError(409, "desktop_component_owner_package_catalog_conflict")
        budgets = validated["budgets"]
        network = validated["network"]
        entrypoint = validated["entrypoint"]
        assert isinstance(budgets, dict)
        assert isinstance(network, dict)
        assert isinstance(entrypoint, dict)
        now = utc_now_text()
        if catalog_existing is None:
            connection.execute(
                "INSERT INTO component_catalog_version "
                "(component_id, version, family, publisher_class, display_name, adapter_id, manifest_json, "
                "manifest_sha256, package_sha256, operation_allowlist_json, requires_network, max_calls, "
                "max_bytes_in, max_bytes_out, max_tokens, max_wall_time_ms, max_cost_units, max_retries, "
                "max_concurrency, created_at) VALUES (?, ?, 'declarative_ui', 'owner_reviewed', ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    component_id,
                    version,
                    component_id,
                    entrypoint["adapter_id"],
                    canonical_json(validated),
                    manifest_sha256,
                    package_sha256,
                    canonical_json(validated["operations"]),
                    int(bool(network["required"])),
                    budgets["max_calls"],
                    budgets["max_bytes_in"],
                    budgets["max_bytes_out"],
                    budgets["max_tokens"],
                    budgets["max_wall_time_ms"],
                    budgets["max_cost_units"],
                    budgets["max_retries"],
                    budgets["max_concurrency"],
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO component_package_attestation "
                "(component_id, version, policy_manifest_sha256, manifest_sha256, package_sha256, "
                "inventory_sha256, attested_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'owner_native_review', ?)",
                (
                    component_id,
                    version,
                    manifest_sha256,
                    manifest_sha256,
                    package_sha256,
                    inventory_sha256,
                    now,
                ),
            )
        else:
            package_existing = connection.execute(
                "SELECT * FROM component_package_attestation WHERE component_id = ? AND version = ? "
                "AND manifest_sha256 = ? AND package_sha256 = ? "
                "AND attested_by = 'owner_native_review'",
                (component_id, version, manifest_sha256, package_sha256),
            ).fetchone()
            if package_existing is None or package_existing["inventory_sha256"] != inventory_sha256:
                raise DesktopApiError(409, "desktop_component_owner_package_version_drift")
        connection.execute(
            "INSERT INTO component_catalog_registration "
            "(component_id, version, manifest_sha256, package_sha256, workspace_id, owner_id, "
            "inventory_sha256, request_sha256, registered_by, registered_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'owner_native_review', ?)",
            (
                component_id,
                version,
                manifest_sha256,
                package_sha256,
                workspace_id,
                workspace["owner_id"],
                inventory_sha256,
                request_sha256,
                now,
            ),
        )
        connection.execute("COMMIT")
        return {
            "component_id": component_id,
            "version": version,
            "manifest_sha256": manifest_sha256,
            "package_sha256": package_sha256,
            "publisher_class": "owner_reviewed",
            "registered_at": now,
            "replayed": False,
        }
    except DesktopApiError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopApiError(503, "desktop_component_owner_package_registration_failed") from exc


def _installation(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    component_id: str,
    required: bool = True,
) -> sqlite3.Row | None:
    row = connection.execute(
        "SELECT installation.*, "
        "COALESCE((SELECT CASE health.state WHEN 'unhealthy' THEN 'unavailable' ELSE health.state END "
        "FROM workspace_component_health AS health WHERE health.runtime_instance_id = "
        "installation.current_runtime_instance_id AND health.generation = installation.binding_generation "
        "ORDER BY health.observed_at DESC LIMIT 1), 'unknown') AS projected_health, "
        "(SELECT operation.error_code FROM workspace_component_operation AS operation "
        "WHERE operation.installation_id = installation.id AND operation.error_code IS NOT NULL "
        "ORDER BY operation.updated_at DESC LIMIT 1) AS projected_error_code "
        "FROM workspace_component_installation AS installation "
        "WHERE installation.workspace_id = ? AND installation.component_id = ? "
        "AND installation.state <> 'uninstalled'",
        (workspace_id, component_id),
    ).fetchone()
    if row is None and required:
        raise DesktopApiError(404, "desktop_component_installation_not_found")
    return row


def _version_key(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
        raise DesktopApiError(409, "desktop_component_version_invalid")
    return cast(tuple[int, int, int], tuple(int(part) for part in value.split(".")))


def _validate_lifecycle_transition(  # noqa: C901 - one closed lifecycle matrix
    connection: sqlite3.Connection,
    *,
    current: sqlite3.Row | None,
    action: str,
    target_version: str,
    manifest_sha256: str,
    package_sha256: str,
) -> None:
    """Validate the live installation transition, including version history."""

    allowed = {
        "install",
        "bind",
        "activate",
        "disable",
        "upgrade",
        "rollback",
        "revoke",
        "uninstall",
    }
    if action not in allowed:
        raise DesktopApiError(400, "desktop_component_action_invalid")
    if current is None:
        if action != "install":
            raise DesktopApiError(409, "desktop_component_not_installed")
        return
    if action == "install":
        raise DesktopApiError(409, "desktop_component_already_installed")

    state = str(current["state"])
    if state == "revoked" and action != "uninstall":
        raise DesktopApiError(409, "desktop_component_revoked")
    if state == "uninstalled":
        raise DesktopApiError(409, "desktop_component_not_installed")
    states_by_action = {
        "bind": {"installed"},
        "activate": {"bound", "blocked"},
        "disable": {"installed", "bound", "active", "blocked"},
        "upgrade": {"installed", "bound", "active", "disabled", "blocked"},
        "rollback": {"installed", "bound", "active", "disabled", "blocked"},
        "revoke": {"installed", "bound", "active", "disabled", "blocked"},
        "uninstall": {"installed", "bound", "active", "disabled", "blocked", "revoked"},
    }
    if state not in states_by_action[action]:
        error = (
            "desktop_component_not_bound"
            if action == "activate"
            else f"desktop_component_{action}_transition_invalid"
        )
        raise DesktopApiError(409, error)
    if action == "activate":
        binding = connection.execute(
            "SELECT state FROM workspace_component_binding_generation "
            "WHERE installation_id = ? AND generation = ?",
            (current["id"], current["binding_generation"]),
        ).fetchone()
        if binding is None or binding["state"] != "bound":
            raise DesktopApiError(409, "desktop_component_not_bound")

    same_identity = (
        target_version == current["version"]
        and manifest_sha256 == current["manifest_sha256"]
        and package_sha256 == current["package_sha256"]
    )
    if action not in {"upgrade", "rollback"} and not same_identity:
        raise DesktopApiError(409, "desktop_component_version_transition_invalid")
    if action == "upgrade" and _version_key(target_version) <= _version_key(current["version"]):
        raise DesktopApiError(409, "desktop_component_upgrade_version_invalid")
    if action == "rollback":
        if same_identity:
            raise DesktopApiError(409, "desktop_component_rollback_version_invalid")
        historical = connection.execute(
            "SELECT 1 FROM workspace_component_binding_generation "
            "WHERE installation_id = ? AND generation < ? AND version = ? "
            "AND manifest_sha256 = ? AND package_sha256 = ? "
            "AND state IN ('bound','active','disabled','revoked')",
            (
                current["id"],
                current["binding_generation"],
                target_version,
                manifest_sha256,
                package_sha256,
            ),
        ).fetchone()
        if historical is None:
            raise DesktopApiError(409, "desktop_component_rollback_history_missing")


def _proposal_payload(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
    decision = connection.execute(
        "SELECT decision, decided_at FROM workspace_component_decision WHERE proposal_id = ?",
        (row["id"],),
    ).fetchone()
    return {
        "proposal_id": row["id"],
        "workspace_id": row["workspace_id"],
        "component_id": row["component_id"],
        "target_version": row["target_version"],
        "change_kind": row["change_kind"],
        "base_revision": row["expected_revision"],
        "manifest_sha256": row["manifest_sha256"],
        "package_sha256": row["package_sha256"],
        "requested_grants": _decode_json(str(row["requested_grants_json"])),
        "desired_configuration": _decode_json(str(row["desired_configuration_json"])),
        "desired_slot_bindings": _decode_json(str(row["desired_slot_bindings_json"])),
        "dependency_graph": _decode_json(str(row["dependency_graph_json"])),
        "source_kind": row["source_kind"],
        "source_reference": row["source_reference"],
        "request_sha256": row["request_sha256"],
        "decision": None if decision is None else decision["decision"],
        "created_at": row["created_at"],
    }


def _operation_payload(row: sqlite3.Row) -> dict[str, object]:
    state = {
        "accepted": "pending",
        "authorized": "pending",
        "dispatching": "pending",
        "succeeded": "succeeded",
        "failed": "failed",
        "ambiguous": "unknown",
        "reconciliation_required": "unknown",
        "reconciled_succeeded": "succeeded",
        "reconciled_failed": "failed",
    }[str(row["state"])]
    return {
        "operation_id": row["id"],
        "workspace_id": row["workspace_id"],
        "component_id": row["component_id"],
        "installation_id": row["installation_id"],
        "action": row["action"] or row["kind"],
        "request_sha256": row["request_sha256"],
        "binding_generation": row["binding_generation"] or 0,
        "state": state,
        "result_sha256": row["result_sha256"],
        "evidence_sha256": row["evidence_sha256"],
        "error_code": row["error_code"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _effect_payload(row: sqlite3.Row) -> dict[str, object]:
    state = {
        "pending": "pending",
        "committed": "succeeded",
        "failed": "failed",
        "unknown": "unknown",
        "reconciliation_required": "unknown",
        "reconciled_committed": "succeeded",
        "reconciled_failed": "failed",
    }[str(row["state"])]
    return {
        "effect_id": row["id"],
        "operation_id": row["operation_id"],
        "workspace_id": row["workspace_id"],
        "component_id": row["logical_target_id"],
        "state": state,
        "evidence_sha256": row["evidence_sha256"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _installation_payload(row: sqlite3.Row) -> dict[str, object]:
    column_names = frozenset(row.keys())
    return {
        "installation_id": row["id"],
        "workspace_id": row["workspace_id"],
        "component_id": row["component_id"],
        "revision": row["revision"],
        "version": row["version"],
        "manifest_sha256": row["manifest_sha256"],
        "package_sha256": row["package_sha256"],
        "state": row["state"],
        "binding_generation": row["binding_generation"],
        "desired_configuration": _decode_json(str(row["configuration_json"])),
        "current_slot_bindings": _decode_json(str(row["slot_bindings_json"])),
        "dependency_graph": _decode_json(str(row["dependency_graph_json"])),
        "health": row["projected_health"] if "projected_health" in column_names else "unknown",
        "last_error_code": row["projected_error_code"]
        if "projected_error_code" in column_names
        else None,
        "updated_at": row["updated_at"],
    }


def _catalog_payload(row: sqlite3.Row, package: sqlite3.Row | None = None) -> dict[str, object]:
    manifest = _decode_json(str(row["manifest_json"]))
    assert isinstance(manifest, dict)
    return {
        "component_id": row["component_id"],
        "version": row["version"],
        "family": row["family"],
        "publisher_class": row["publisher_class"],
        "display_name": row["display_name"],
        "adapter_id": row["adapter_id"],
        "policy_manifest_sha256": row["manifest_sha256"],
        "manifest_sha256": None if package is None else package["manifest_sha256"],
        "package_sha256": None if package is None else package["package_sha256"],
        "operations": manifest["operations"],
        "permissions": manifest["permissions"],
        "slots": manifest["slots"],
        "dependencies": manifest["dependencies"],
        "conflicts": manifest["conflicts"],
        "budgets": manifest["budgets"],
        "network": manifest["network"],
        "recovery": manifest["recovery"],
        "state_schema": manifest["state_schema"],
        "settings_schema": manifest["configuration_schema"],
        "available": package is not None,
        "unavailable_reason": None if package is not None else "package_not_attested",
    }


def get_component_snapshot(connection: sqlite3.Connection, workspace_id: str) -> dict[str, object]:
    workspace = _live_workspace(connection, workspace_id)
    catalog = []
    for row in connection.execute(
        "SELECT * FROM component_catalog_version ORDER BY component_id, version"
    ):
        package = _find_package_identity(connection, row, workspace_id)
        catalog.append(_catalog_payload(row, package))
    installations = [
        _installation_payload(row)
        for row in connection.execute(
            "SELECT installation.*, "
            "COALESCE((SELECT CASE health.state WHEN 'unhealthy' THEN 'unavailable' ELSE health.state END "
            "FROM workspace_component_health AS health WHERE health.runtime_instance_id = "
            "installation.current_runtime_instance_id AND health.generation = installation.binding_generation "
            "ORDER BY health.observed_at DESC LIMIT 1), 'unknown') AS projected_health, "
            "(SELECT operation.error_code FROM workspace_component_operation AS operation "
            "WHERE operation.installation_id = installation.id AND operation.error_code IS NOT NULL "
            "ORDER BY operation.updated_at DESC LIMIT 1) AS projected_error_code "
            "FROM workspace_component_installation AS installation WHERE installation.workspace_id = ? "
            "ORDER BY component_id, created_at",
            (workspace_id,),
        )
    ]
    proposals = [
        _proposal_payload(connection, row)
        for row in connection.execute(
            "SELECT * FROM workspace_component_proposal WHERE workspace_id = ? "
            "ORDER BY created_at DESC, id DESC",
            (workspace_id,),
        )
    ]
    operations = [
        _operation_payload(row)
        for row in connection.execute(
            "SELECT * FROM workspace_component_operation WHERE workspace_id = ? "
            "ORDER BY created_at DESC, id DESC",
            (workspace_id,),
        )
    ]
    effects = [
        _effect_payload(row)
        for row in connection.execute(
            "SELECT * FROM workspace_component_effect WHERE workspace_id = ? "
            "ORDER BY created_at DESC, id DESC",
            (workspace_id,),
        )
    ]
    grants: list[dict[str, object]] = []
    for row in connection.execute(
        "SELECT grant.*, usage.calls, usage.bytes_in, usage.bytes_out_reserved, "
        "usage.tokens_reserved, usage.wall_time_ms_reserved, usage.cost_units, usage.retries "
        "FROM workspace_component_grant AS grant "
        "JOIN workspace_component_grant_usage AS usage ON usage.grant_id = grant.id "
        "WHERE grant.workspace_id = ? ORDER BY grant.created_at DESC",
        (workspace_id,),
    ):
        grants.append(
            {
                "id": row["id"],
                "workspace_id": row["workspace_id"],
                "installation_id": row["installation_id"],
                "binding_generation": row["generation"],
                "runtime_instance_id": row["runtime_instance_id"],
                "component_id": row["component_id"],
                "version": row["version"],
                "actions": _decode_json(str(row["actions_json"])),
                "scope": _decode_json(str(row["scope_json"])),
                "requires_network": bool(row["requires_network"]),
                "state": row["state"],
                "not_before": row["not_before"],
                "expires_at": row["expires_at"],
                "limits": {
                    "calls": row["max_calls"],
                    "bytes_in": row["max_bytes_in"],
                    "bytes_out": row["max_bytes_out"],
                    "tokens": row["max_tokens"],
                    "wall_time_ms": row["max_wall_time_ms"],
                    "cost_units": row["max_cost_units"],
                    "retries": row["max_retries"],
                    "concurrency": row["max_concurrency"],
                },
                "used": {
                    "calls": row["calls"],
                    "bytes_in": row["bytes_in"],
                    "bytes_out": row["bytes_out_reserved"],
                    "tokens": row["tokens_reserved"],
                    "wall_time_ms": row["wall_time_ms_reserved"],
                    "cost_units": row["cost_units"],
                    "retries": row["retries"],
                },
                "remaining": {
                    "calls": row["max_calls"] - row["calls"],
                    "bytes_in": row["max_bytes_in"] - row["bytes_in"],
                    "bytes_out": row["max_bytes_out"] - row["bytes_out_reserved"],
                    "tokens": row["max_tokens"] - row["tokens_reserved"],
                    "wall_time_ms": row["max_wall_time_ms"] - row["wall_time_ms_reserved"],
                    "cost_units": row["max_cost_units"] - row["cost_units"],
                    "retries": row["max_retries"] - row["retries"],
                },
            }
        )
    revocations = [
        {
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "installation_id": row["installation_id"],
            "component_id": row["component_id"],
            "binding_generation": row["binding_generation"],
            "runtime_instance_id": row["runtime_instance_id"],
            "grant_id": row["grant_id"],
            "reason_code": row["reason_code"],
            "actor_type": row["actor_type"],
            "created_at": row["created_at"],
        }
        for row in connection.execute(
            "SELECT revocation.*, installation.component_id, installation.binding_generation "
            "FROM workspace_component_revocation AS revocation "
            "JOIN workspace_component_installation AS installation ON installation.id = revocation.installation_id "
            "WHERE revocation.workspace_id = ? "
            "ORDER BY created_at DESC, id DESC",
            (workspace_id,),
        )
    ]
    reconciliations = [
        {
            "reconciliation_id": row["id"],
            "operation_id": row["operation_id"],
            "effect_id": row["effect_id"],
            "workspace_id": row["workspace_id"],
            "outcome": row["outcome"],
            "evidence_sha256": row["evidence_sha256"],
            "created_at": row["decided_at"],
        }
        for row in connection.execute(
            "SELECT * FROM workspace_component_reconciliation WHERE workspace_id = ? "
            "ORDER BY decided_at DESC, id DESC",
            (workspace_id,),
        )
    ]
    audit = [
        {
            "sequence": row["sequence"],
            "event_id": row["event_id"],
            "event_type": row["event_type"],
            "payload": _decode_json(str(row["payload_json"])),
            "created_at": row["created_at"],
        }
        for row in connection.execute(
            "SELECT * FROM audit_event WHERE owner_id = ? AND workspace_id = ? "
            "AND event_type GLOB 'workspace_component_*' ORDER BY sequence DESC",
            (workspace["owner_id"], workspace_id),
        )
    ]
    return {
        "workspace_id": workspace_id,
        "catalog": catalog,
        "installations": installations,
        "proposals": proposals,
        "operations": operations,
        "effects": effects,
        "grants": grants,
        "revocations": revocations,
        "recoveries": [
            {
                "recovery_id": row["id"],
                "workspace_id": row["workspace_id"],
                "component_id": row["component_id"],
                "installation_id": row["installation_id"],
                "binding_generation": row["binding_generation"],
                "previous_runtime_instance_id": row["previous_runtime_instance_id"],
                "operation_id": row["operation_id"],
                "request_sha256": row["request_sha256"],
                "manifest_sha256": row["manifest_sha256"],
                "package_sha256": row["package_sha256"],
                "state": {
                    "dispatching": "pending",
                    "succeeded": "succeeded",
                    "failed": "failed",
                    "ambiguous": "unknown",
                    "reconciliation_required": "unknown",
                    "reconciled_succeeded": "succeeded",
                    "reconciled_failed": "failed",
                }[str(row["operation_state"])],
                "reason_code": row["reason_code"],
                "effect_id": row["effect_id"],
                "adapter_id": row["adapter_id"],
                "runtime_instance_id": row["reserved_runtime_instance_id"],
                "workload_identity_digest": row["workload_identity_digest"],
                "created_at": row["created_at"],
            }
            for row in connection.execute(
                "SELECT recovery.*, operation.state AS operation_state, dispatch.effect_id, "
                "dispatch.adapter_id, dispatch.reserved_runtime_instance_id, "
                "dispatch.workload_identity_digest "
                "FROM workspace_component_recovery_request AS recovery "
                "JOIN workspace_component_operation AS operation ON operation.id = recovery.operation_id "
                "JOIN workspace_component_lifecycle_dispatch AS dispatch "
                "ON dispatch.operation_id = recovery.operation_id "
                "WHERE recovery.workspace_id = ? ORDER BY recovery.created_at DESC, recovery.id DESC",
                (workspace_id,),
            )
        ],
        "reconciliations": reconciliations,
        "audit": audit,
    }


def _validate_grants(  # noqa: C901
    catalog: sqlite3.Row, requested: list[dict[str, object]]
) -> str:
    if len(requested) > 32:
        raise DesktopApiError(400, "desktop_component_grants_invalid")
    decoded_operations = _decode_json(str(catalog["operation_allowlist_json"]))
    if not isinstance(decoded_operations, list) or any(
        not isinstance(operation, str) for operation in decoded_operations
    ):
        raise DesktopApiError(409, "desktop_component_catalog_manifest_invalid")
    operations = set(decoded_operations)
    manifest = _decode_json(str(catalog["manifest_json"]))
    if not isinstance(manifest, dict):
        raise DesktopApiError(409, "desktop_component_catalog_manifest_invalid")
    permissions = manifest.get("permissions")
    network = manifest.get("network")
    if not isinstance(permissions, list) or not isinstance(network, dict):
        raise DesktopApiError(409, "desktop_component_catalog_manifest_invalid")
    permission_by_action = {
        permission.get("action"): permission
        for permission in permissions
        if isinstance(permission, dict) and isinstance(permission.get("action"), str)
    }
    service_classes = network.get("service_classes")
    if set(permission_by_action) != operations or not isinstance(service_classes, list):
        raise DesktopApiError(409, "desktop_component_catalog_manifest_invalid")
    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in requested:
        if not isinstance(raw, dict) or set(raw) != {
            "action",
            "expires_in_seconds",
            "logical_resource_id",
            "logical_service_id",
            "maximum_bytes_in",
            "maximum_bytes_out",
            "maximum_cost_units",
            "maximum_invocations",
            "maximum_tokens",
            "maximum_wall_time_ms",
            "resource_version",
        }:
            raise DesktopApiError(400, "desktop_component_grants_invalid")
        action = raw["action"]
        resource_id = raw["logical_resource_id"]
        resource_version = raw["resource_version"]
        logical_service_id = raw["logical_service_id"]
        limits = {
            "expires_in_seconds": (raw["expires_in_seconds"], 86_400),
            "maximum_invocations": (raw["maximum_invocations"], catalog["max_calls"]),
            "maximum_bytes_in": (raw["maximum_bytes_in"], catalog["max_bytes_in"]),
            "maximum_bytes_out": (raw["maximum_bytes_out"], catalog["max_bytes_out"]),
            "maximum_tokens": (raw["maximum_tokens"], catalog["max_tokens"]),
            "maximum_wall_time_ms": (
                raw["maximum_wall_time_ms"],
                catalog["max_wall_time_ms"],
            ),
            "maximum_cost_units": (raw["maximum_cost_units"], catalog["max_cost_units"]),
        }
        if any(
            type(value) is not int
            or value < (60 if name == "expires_in_seconds" else 0)
            or value > int(maximum)
            for name, (value, maximum) in limits.items()
        ):
            raise DesktopApiError(409, "desktop_component_grant_exceeds_manifest")
        maximum_invocations = cast(int, raw["maximum_invocations"])
        maximum_wall_time_ms = cast(int, raw["maximum_wall_time_ms"])
        if maximum_invocations < 1 or maximum_wall_time_ms < 1:
            raise DesktopApiError(409, "desktop_component_grant_exceeds_manifest")
        if not isinstance(action, str) or action not in operations or action in seen:
            raise DesktopApiError(409, "desktop_component_grant_exceeds_manifest")
        permission = permission_by_action[action]
        declared_resource_classes = permission.get("logical_resource_classes")
        data_scope = permission.get("data_scope")
        if not isinstance(declared_resource_classes, list) or any(
            not isinstance(item, str) for item in declared_resource_classes
        ):
            raise DesktopApiError(409, "desktop_component_catalog_manifest_invalid")
        if data_scope == "none":
            if resource_id is not None or resource_version is not None:
                raise DesktopApiError(409, "desktop_component_resource_scope_invalid")
        elif (
            data_scope != "workspace_logical"
            or not isinstance(resource_id, str)
            or not 3 <= len(resource_id) <= 128
            or _LOGICAL_ID.fullmatch(resource_id) is None
            or resource_id not in declared_resource_classes
        ):
            raise DesktopApiError(409, "desktop_component_resource_scope_invalid")
        if resource_version is not None and (
            type(resource_version) is not int or not 1 <= resource_version <= 2_147_483_647
        ):
            raise DesktopApiError(400, "desktop_component_grants_invalid")
        if (resource_id is None) != (resource_version is None):
            raise DesktopApiError(400, "desktop_component_grants_invalid")
        if bool(catalog["requires_network"]) != (logical_service_id is not None):
            raise DesktopApiError(409, "desktop_component_network_scope_invalid")
        if logical_service_id is not None and (
            not isinstance(logical_service_id, str)
            or not 3 <= len(logical_service_id) <= 128
            or _LOGICAL_ID.fullmatch(logical_service_id) is None
            or logical_service_id not in service_classes
        ):
            raise DesktopApiError(409, "desktop_component_network_scope_invalid")
        normalized.append(
            {
                "action": action,
                "logical_resource_id": resource_id,
                "logical_service_id": logical_service_id,
                "resource_version": resource_version,
                **{name: value for name, (value, _) in limits.items()},
            }
        )
        seen.add(action)
    if seen != operations:
        raise DesktopApiError(409, "desktop_component_grant_incomplete")
    return canonical_json(sorted(normalized, key=lambda value: str(value["action"])))


def _validate_component_composition(  # noqa: C901
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    catalog: sqlite3.Row,
    desired_configuration: dict[str, object],
    desired_slot_bindings: list[dict[str, object]],
    dependency_graph: list[dict[str, object]],
) -> tuple[str, str, str]:
    manifest = _decode_json(str(catalog["manifest_json"]))
    assert isinstance(manifest, dict)
    schema = manifest["configuration_schema"]
    assert isinstance(schema, dict)
    properties = schema.get("properties")
    required = schema.get("required")
    if (
        not isinstance(desired_configuration, dict)
        or not isinstance(properties, dict)
        or not isinstance(required, list)
        or any(not isinstance(key, str) for key in desired_configuration)
        or not set(desired_configuration).issubset(properties)
        or not set(required).issubset(desired_configuration)
    ):
        raise DesktopApiError(409, "desktop_component_configuration_invalid")
    for key, value in desired_configuration.items():
        specification = properties[key]
        assert isinstance(specification, dict)
        expected_type = specification["type"]
        valid_type = {
            "boolean": type(value) is bool,
            "integer": type(value) is int,
            "number": type(value) in {int, float},
            "string": isinstance(value, str),
        }[str(expected_type)]
        if (
            not valid_type
            or ("enum" in specification and value not in specification["enum"])
            or (
                "minimum" in specification
                and (type(value) not in {int, float} or value < specification["minimum"])
            )
            or (
                "maximum" in specification
                and (type(value) not in {int, float} or value > specification["maximum"])
            )
            or (
                "max_length" in specification
                and (not isinstance(value, str) or len(value) > specification["max_length"])
            )
        ):
            raise DesktopApiError(409, "desktop_component_configuration_invalid")
    declared_slots = {
        item["slot_id"]
        for item in manifest["slots"]  # type: ignore[index]
        if isinstance(item, dict) and isinstance(item.get("slot_id"), str)
    }
    slot_constraints = {
        item["slot_id"]: item
        for item in manifest["slots"]  # type: ignore[index]
        if isinstance(item, dict) and isinstance(item.get("slot_id"), str)
    }
    normalized_slots: list[dict[str, object]] = []
    seen_bindings: set[tuple[str, str]] = set()
    for item in desired_slot_bindings:
        if not isinstance(item, dict) or set(item) != {
            "binding_key",
            "configuration",
            "order_index",
            "slot_id",
        }:
            raise DesktopApiError(400, "desktop_component_slot_binding_invalid")
        slot_id = item["slot_id"]
        binding_key = item["binding_key"]
        if (
            not isinstance(slot_id, str)
            or slot_id not in declared_slots
            or not isinstance(binding_key, str)
            or not isinstance(item["configuration"], dict)
            or type(item["order_index"]) is not int
            or not 0 <= item["order_index"] <= 10_000
            or not isinstance(binding_key, str)
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9._:-]{2,127}", binding_key) is None
        ):
            raise DesktopApiError(409, "desktop_component_slot_binding_invalid")
        if (slot_id, binding_key) in seen_bindings:
            raise DesktopApiError(409, "desktop_component_slot_binding_duplicate")
        admitted = connection.execute(
            "SELECT 1 FROM workbench_component_slot WHERE slot_id = ? AND component_allowed = 1",
            (slot_id,),
        ).fetchone()
        if admitted is None:
            raise DesktopApiError(409, "desktop_component_slot_not_admitted")
        seen_bindings.add((slot_id, binding_key))
        normalized_slots.append(item)
    for slot_id, constraint in slot_constraints.items():
        count = sum(1 for binding in normalized_slots if binding["slot_id"] == slot_id)
        if constraint["cardinality"] == "one" and count > 1:
            raise DesktopApiError(409, "desktop_component_slot_cardinality_exceeded")
        if any(
            not constraint["minimum_order"] <= binding["order_index"] <= constraint["maximum_order"]
            for binding in normalized_slots
            if binding["slot_id"] == slot_id
        ):
            raise DesktopApiError(409, "desktop_component_slot_order_invalid")
    expected_dependencies = manifest.get("dependencies")
    if not isinstance(expected_dependencies, list):
        raise DesktopApiError(409, "desktop_component_dependencies_invalid")
    normalized_dependencies = sorted(
        dependency_graph, key=lambda item: str(item.get("component_id", ""))
    )
    if canonical_json(normalized_dependencies) != canonical_json(
        sorted(expected_dependencies, key=lambda item: str(item.get("component_id", "")))
    ):
        raise DesktopApiError(409, "desktop_component_dependency_graph_mismatch")
    for dependency in normalized_dependencies:
        if not isinstance(dependency, dict) or set(dependency) != {
            "component_id",
            "manifest_sha256",
            "package_sha256",
            "policy_manifest_sha256",
            "version",
        }:
            raise DesktopApiError(400, "desktop_component_dependencies_invalid")
        installed = _installation(
            connection,
            workspace_id=workspace_id,
            component_id=str(dependency["component_id"]),
            required=False,
        )
        if (
            installed is None
            or installed["state"] not in {"bound", "active", "disabled"}
            or any(
                installed[field] != dependency[field]
                for field in ("version", "manifest_sha256", "package_sha256")
            )
        ):
            raise DesktopApiError(409, "desktop_component_dependency_unavailable")
        dependency_catalog = _catalog_row(
            connection, str(dependency["component_id"]), str(dependency["version"])
        )
        if dependency_catalog["manifest_sha256"] != dependency["policy_manifest_sha256"]:
            raise DesktopApiError(409, "desktop_component_dependency_policy_mismatch")
    conflicts = manifest.get("conflicts")
    if not isinstance(conflicts, list) or any(not isinstance(item, str) for item in conflicts):
        raise DesktopApiError(409, "desktop_component_conflicts_invalid")
    for conflict_id in conflicts:
        installed_conflict = connection.execute(
            "SELECT 1 FROM workspace_component_installation WHERE workspace_id = ? "
            "AND component_id = ? AND state <> 'uninstalled' LIMIT 1",
            (workspace_id, conflict_id),
        ).fetchone()
        if installed_conflict is not None:
            raise DesktopApiError(409, "desktop_component_conflict_installed")
    installed_components = connection.execute(
        "SELECT component_id, version FROM workspace_component_installation "
        "WHERE workspace_id = ? AND component_id <> ? AND state <> 'uninstalled'",
        (workspace_id, catalog["component_id"]),
    ).fetchall()
    for installed in installed_components:
        installed_catalog = _catalog_row(
            connection, str(installed["component_id"]), str(installed["version"])
        )
        installed_manifest = _decode_json(str(installed_catalog["manifest_json"]))
        assert isinstance(installed_manifest, dict)
        installed_conflicts = installed_manifest.get("conflicts")
        if not isinstance(installed_conflicts, list) or any(
            not isinstance(item, str) for item in installed_conflicts
        ):
            raise DesktopApiError(409, "desktop_component_conflicts_invalid")
        if catalog["component_id"] in installed_conflicts:
            raise DesktopApiError(409, "desktop_component_conflict_installed")
    configuration_json = canonical_json(desired_configuration)
    slots_json = canonical_json(
        sorted(normalized_slots, key=lambda item: (str(item["slot_id"]), str(item["binding_key"])))
    )
    dependencies_json = canonical_json(normalized_dependencies)
    return configuration_json, slots_json, dependencies_json


def _trusted_assistant_message_content(
    connection: sqlite3.Connection,
    *,
    owner_id: str,
    workspace_id: str,
    message_id: str,
) -> str:
    row = connection.execute(
        "SELECT message.content FROM message JOIN invocation ON invocation.id = message.invocation_id "
        "AND invocation.owner_id = message.owner_id "
        "AND invocation.workspace_id = message.workspace_id "
        "AND invocation.conversation_id = message.conversation_id "
        "WHERE message.id = ? AND message.owner_id = ? AND message.workspace_id = ? "
        "AND message.role = 'assistant' AND message.status = 'completed' "
        "AND invocation.status = 'succeeded'",
        (message_id, owner_id, workspace_id),
    ).fetchone()
    if row is None:
        raise DesktopApiError(409, "desktop_component_assistant_source_not_trusted")
    return str(row["content"])


def _assert_proposal_source(
    connection: sqlite3.Connection,
    *,
    owner_id: str,
    workspace_id: str,
    source_kind: str,
    source_reference: str | None,
) -> None:
    if source_kind == "owner":
        if source_reference is not None:
            raise DesktopApiError(400, "desktop_component_owner_source_reference_invalid")
        return
    if source_kind != "assistant" or source_reference is None:
        raise DesktopApiError(400, "desktop_component_proposal_source_invalid")
    _trusted_assistant_message_content(
        connection,
        owner_id=owner_id,
        workspace_id=workspace_id,
        message_id=source_reference,
    )


def _assistant_component_envelope(content: str) -> dict[str, object]:
    text = content.strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[8:-4].strip()
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        raise DesktopApiError(409, "desktop_component_assistant_payload_invalid") from None
    if not isinstance(value, dict) or set(value) != _ASSISTANT_PROPOSAL_KEYS:
        raise DesktopApiError(409, "desktop_component_assistant_payload_invalid")
    if value["type"] != "omnibase.workspace-component.proposal.v1":
        raise DesktopApiError(409, "desktop_component_assistant_payload_invalid")
    scalar_types = {
        "component_id": str,
        "target_version": str,
        "change_kind": str,
        "policy_manifest_sha256": str,
        "manifest_sha256": str,
        "package_sha256": str,
    }
    if any(not isinstance(value[key], expected) for key, expected in scalar_types.items()):
        raise DesktopApiError(409, "desktop_component_assistant_payload_invalid")
    if type(value["expected_revision"]) is not int:
        raise DesktopApiError(409, "desktop_component_assistant_payload_invalid")
    if not isinstance(value["desired_configuration"], dict):
        raise DesktopApiError(409, "desktop_component_assistant_payload_invalid")
    for key in ("requested_grants", "desired_slot_bindings", "dependency_graph"):
        items = value[key]
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise DesktopApiError(409, "desktop_component_assistant_payload_invalid")
    return value


def _create_component_proposal(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    component_id: str,
    target_version: str,
    change_kind: str,
    expected_revision: int,
    requested_grants: list[dict[str, object]],
    desired_configuration: dict[str, object],
    desired_slot_bindings: list[dict[str, object]],
    dependency_graph: list[dict[str, object]],
    source_kind: str,
    source_reference: str | None,
    idempotency_key: str,
) -> dict[str, object]:
    if not _IDEMPOTENCY.fullmatch(idempotency_key):
        raise DesktopApiError(400, "desktop_component_idempotency_key_invalid")
    catalog = _catalog_row(connection, component_id, target_version)
    grants_json = _validate_grants(catalog, requested_grants)
    request = {
        "change_kind": change_kind,
        "component_id": component_id,
        "expected_revision": expected_revision,
        "desired_configuration": desired_configuration,
        "desired_slot_bindings": desired_slot_bindings,
        "dependency_graph": dependency_graph,
        "requested_grants": _decode_json(grants_json),
        "source_kind": source_kind,
        "source_reference": source_reference,
        "target_version": target_version,
        "workspace_id": workspace_id,
    }
    request_json = canonical_json(request)
    request_sha256 = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
    try:
        connection.execute("BEGIN IMMEDIATE")
        workspace = _live_workspace(connection, workspace_id)
        _assert_proposal_source(
            connection,
            owner_id=str(workspace["owner_id"]),
            workspace_id=workspace_id,
            source_kind=source_kind,
            source_reference=source_reference,
        )
        package = _package_identity(connection, catalog, workspace_id)
        configuration_json, slots_json, dependencies_json = _validate_component_composition(
            connection,
            workspace_id=workspace_id,
            catalog=catalog,
            desired_configuration=desired_configuration,
            desired_slot_bindings=desired_slot_bindings,
            dependency_graph=dependency_graph,
        )
        request["manifest_sha256"] = package["manifest_sha256"]
        request["package_sha256"] = package["package_sha256"]
        request["desired_configuration"] = _decode_json(configuration_json)
        request["desired_slot_bindings"] = _decode_json(slots_json)
        request["dependency_graph"] = _decode_json(dependencies_json)
        request_json = canonical_json(request)
        request_sha256 = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        current = _installation(
            connection,
            workspace_id=workspace_id,
            component_id=component_id,
            required=False,
        )
        current_revision = 0 if current is None else int(current["revision"])
        if current_revision != expected_revision:
            raise DesktopApiError(409, "desktop_component_revision_conflict")
        _validate_lifecycle_transition(
            connection,
            current=current,
            action=change_kind,
            target_version=target_version,
            manifest_sha256=str(package["manifest_sha256"]),
            package_sha256=str(package["package_sha256"]),
        )
        existing = connection.execute(
            "SELECT * FROM workspace_component_proposal WHERE workspace_id = ? "
            "AND component_id = ? AND idempotency_key = ?",
            (workspace_id, component_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            if existing["request_sha256"] != request_sha256:
                raise DesktopApiError(409, "desktop_component_idempotency_payload_drift")
            connection.execute("COMMIT")
            return {"proposal": _proposal_payload(connection, existing), "replayed": True}
        proposal_id = _id("proposal")
        now = utc_now_text()
        connection.execute(
            "INSERT INTO workspace_component_proposal "
            "(id, owner_id, workspace_id, component_id, target_version, change_kind, "
            "expected_revision, manifest_sha256, package_sha256, requested_grants_json, "
            "desired_configuration_json, desired_configuration_sha256, "
            "desired_slot_bindings_json, desired_slot_bindings_sha256, "
            "dependency_graph_json, dependency_graph_sha256, source_kind, source_reference, "
            "idempotency_key, request_json, request_sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                proposal_id,
                workspace["owner_id"],
                workspace_id,
                component_id,
                target_version,
                change_kind,
                expected_revision,
                package["manifest_sha256"],
                package["package_sha256"],
                grants_json,
                configuration_json,
                hashlib.sha256(configuration_json.encode()).hexdigest(),
                slots_json,
                hashlib.sha256(slots_json.encode()).hexdigest(),
                dependencies_json,
                hashlib.sha256(dependencies_json.encode()).hexdigest(),
                source_kind,
                source_reference,
                idempotency_key,
                request_json,
                request_sha256,
                now,
            ),
        )
        append_audit_event(
            connection,
            event_id=_id("event"),
            owner_id=str(workspace["owner_id"]),
            workspace_id=workspace_id,
            event_type="workspace_component_proposed",
            payload={
                "component_id": component_id,
                "proposal_id": proposal_id,
                "request_sha256": request_sha256,
                "target_version": target_version,
            },
        )
        row = connection.execute(
            "SELECT * FROM workspace_component_proposal WHERE id = ?", (proposal_id,)
        ).fetchone()
        connection.execute("COMMIT")
        assert row is not None
        return {"proposal": _proposal_payload(connection, row), "replayed": False}
    except DesktopApiError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopApiError(503, "desktop_component_proposal_failed") from None


def create_component_proposal(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    component_id: str,
    target_version: str,
    change_kind: str,
    expected_revision: int,
    requested_grants: list[dict[str, object]],
    desired_configuration: dict[str, object],
    desired_slot_bindings: list[dict[str, object]],
    dependency_graph: list[dict[str, object]],
    source_kind: str,
    source_reference: str | None,
    idempotency_key: str,
) -> dict[str, object]:
    """Create an Owner-authored proposal; assistant payloads use the message route."""

    if source_kind != "owner" or source_reference is not None:
        raise DesktopApiError(409, "desktop_component_assistant_source_requires_message_route")
    return _create_component_proposal(
        connection,
        workspace_id=workspace_id,
        component_id=component_id,
        target_version=target_version,
        change_kind=change_kind,
        expected_revision=expected_revision,
        requested_grants=requested_grants,
        desired_configuration=desired_configuration,
        desired_slot_bindings=desired_slot_bindings,
        dependency_graph=dependency_graph,
        source_kind="owner",
        source_reference=None,
        idempotency_key=idempotency_key,
    )


def create_assistant_component_proposal(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    message_id: str,
) -> dict[str, object]:
    """Derive an exact proposal exclusively from a trusted completed assistant message."""

    if _MESSAGE_ID.fullmatch(message_id) is None:
        raise DesktopApiError(400, "desktop_component_assistant_source_invalid")
    workspace = _live_workspace(connection, workspace_id)
    envelope = _assistant_component_envelope(
        _trusted_assistant_message_content(
            connection,
            owner_id=str(workspace["owner_id"]),
            workspace_id=workspace_id,
            message_id=message_id,
        )
    )
    component_id = str(envelope["component_id"])
    target_version = str(envelope["target_version"])
    catalog = _catalog_row(connection, component_id, target_version)
    package = _package_identity(connection, catalog, workspace_id)
    if (
        envelope["policy_manifest_sha256"] != catalog["manifest_sha256"]
        or envelope["manifest_sha256"] != package["manifest_sha256"]
        or envelope["package_sha256"] != package["package_sha256"]
    ):
        raise DesktopApiError(409, "desktop_component_assistant_package_identity_drift")
    return _create_component_proposal(
        connection,
        workspace_id=workspace_id,
        component_id=component_id,
        target_version=target_version,
        change_kind=str(envelope["change_kind"]),
        expected_revision=cast(int, envelope["expected_revision"]),
        requested_grants=cast(list[dict[str, object]], envelope["requested_grants"]),
        desired_configuration=cast(dict[str, object], envelope["desired_configuration"]),
        desired_slot_bindings=cast(list[dict[str, object]], envelope["desired_slot_bindings"]),
        dependency_graph=cast(list[dict[str, object]], envelope["dependency_graph"]),
        source_kind="assistant",
        source_reference=message_id,
        idempotency_key=f"assistant:{message_id}",
    )


def decide_component_proposal(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    proposal_id: str,
    decision: str,
    request_sha256: str,
) -> dict[str, object]:
    if decision not in {"approve", "reject"} or not _SHA256.fullmatch(request_sha256):
        raise DesktopApiError(400, "desktop_component_decision_invalid")
    try:
        connection.execute("BEGIN IMMEDIATE")
        workspace = _live_workspace(connection, workspace_id)
        proposal = connection.execute(
            "SELECT * FROM workspace_component_proposal WHERE id = ? AND workspace_id = ?",
            (proposal_id, workspace_id),
        ).fetchone()
        if proposal is None:
            raise DesktopApiError(404, "desktop_component_proposal_not_found")
        if proposal["request_sha256"] != request_sha256:
            raise DesktopApiError(409, "desktop_component_request_digest_conflict")
        if connection.execute(
            "SELECT 1 FROM workspace_component_decision WHERE proposal_id = ?", (proposal_id,)
        ).fetchone():
            raise DesktopApiError(409, "desktop_component_proposal_decided")
        current = _installation(
            connection,
            workspace_id=workspace_id,
            component_id=str(proposal["component_id"]),
            required=False,
        )
        revision = 0 if current is None else int(current["revision"])
        if decision == "approve" and revision != proposal["expected_revision"]:
            raise DesktopApiError(409, "desktop_component_revision_conflict")
        stored_decision = "approved" if decision == "approve" else "rejected"
        now = utc_now_text()
        connection.execute(
            "INSERT INTO workspace_component_decision "
            "(proposal_id, workspace_id, decision, request_sha256, decided_by, decided_at) "
            "VALUES (?, ?, ?, ?, 'owner', ?)",
            (proposal_id, workspace_id, stored_decision, request_sha256, now),
        )
        append_audit_event(
            connection,
            event_id=_id("event"),
            owner_id=str(workspace["owner_id"]),
            workspace_id=workspace_id,
            event_type="workspace_component_decided",
            payload={
                "component_id": proposal["component_id"],
                "decision": stored_decision,
                "proposal_id": proposal_id,
                "request_sha256": request_sha256,
            },
        )
        connection.execute("COMMIT")
        return {
            "workspace_id": workspace_id,
            "proposal_id": proposal_id,
            "request_sha256": request_sha256,
            "decision": stored_decision,
            "installation_revision": revision,
        }
    except DesktopApiError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopApiError(503, "desktop_component_decision_failed") from None


def _append_operation_transition(
    connection: sqlite3.Connection,
    operation: sqlite3.Row,
    state: str,
    reason_code: str,
    evidence_sha256: str | None = None,
    *,
    result_sha256: str | None = None,
    error_code: str | None = None,
) -> sqlite3.Row:
    sequence = int(operation["version"]) + 1
    now = utc_now_text()
    connection.execute(
        "INSERT INTO workspace_component_operation_transition "
        "(operation_id, sequence, state, reason_code, evidence_sha256, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (operation["id"], sequence, state, reason_code, evidence_sha256, now),
    )
    connection.execute(
        "UPDATE workspace_component_operation SET state = ?, version = ?, result_sha256 = ?, "
        "evidence_sha256 = ?, error_code = ?, updated_at = ? WHERE id = ?",
        (state, sequence, result_sha256, evidence_sha256, error_code, now, operation["id"]),
    )
    row = connection.execute(
        "SELECT * FROM workspace_component_operation WHERE id = ?", (operation["id"],)
    ).fetchone()
    assert row is not None
    return row


def _create_operation(
    connection: sqlite3.Connection,
    *,
    workspace: sqlite3.Row,
    component_id: str,
    installation: sqlite3.Row | None,
    kind: str,
    action: str | None,
    expected_revision: int,
    binding_generation: int | None,
    runtime_instance_id: str | None,
    manifest_sha256: str,
    package_sha256: str,
    idempotency_key: str,
    request: dict[str, object],
) -> tuple[sqlite3.Row, bool]:
    request_json = canonical_json(request)
    request_sha256 = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
    existing = connection.execute(
        "SELECT * FROM workspace_component_operation WHERE workspace_id = ? "
        "AND component_id = ? AND idempotency_key = ?",
        (workspace["id"], component_id, idempotency_key),
    ).fetchone()
    if existing is not None:
        if existing["request_sha256"] != request_sha256:
            raise DesktopApiError(409, "desktop_component_idempotency_payload_drift")
        return existing, True
    generation = int(
        connection.execute(
            "SELECT COALESCE(MAX(operation_generation), 0) + 1 FROM workspace_component_operation "
            "WHERE workspace_id = ? AND component_id = ?",
            (workspace["id"], component_id),
        ).fetchone()[0]
    )
    operation_id = _id("compop")
    now = utc_now_text()
    connection.execute(
        "INSERT INTO workspace_component_operation "
        "(id, owner_id, workspace_id, component_id, installation_id, kind, action, "
        "operation_generation, expected_revision, binding_generation, runtime_instance_id, "
        "manifest_sha256, package_sha256, idempotency_key, request_json, request_sha256, "
        "state, version, result_sha256, evidence_sha256, error_code, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'accepted', 1, NULL, NULL, NULL, ?, ?)",
        (
            operation_id,
            workspace["owner_id"],
            workspace["id"],
            component_id,
            None if installation is None else installation["id"],
            kind,
            action,
            generation,
            expected_revision,
            binding_generation,
            runtime_instance_id,
            manifest_sha256,
            package_sha256,
            idempotency_key,
            request_json,
            request_sha256,
            now,
            now,
        ),
    )
    connection.execute(
        "INSERT INTO workspace_component_operation_transition "
        "(operation_id, sequence, state, reason_code, evidence_sha256, recorded_at) "
        "VALUES (?, 1, 'accepted', 'operation_accepted', NULL, ?)",
        (operation_id, now),
    )
    row = connection.execute(
        "SELECT * FROM workspace_component_operation WHERE id = ?", (operation_id,)
    ).fetchone()
    assert row is not None
    return row, False


def _create_effect(
    connection: sqlite3.Connection,
    operation: sqlite3.Row,
    effect_kind: str,
    logical_target_id: str,
) -> sqlite3.Row:
    effect_id = _id("effect")
    now = utc_now_text()
    connection.execute(
        "INSERT INTO workspace_component_effect "
        "(id, operation_id, workspace_id, effect_kind, logical_target_id, request_sha256, "
        "state, version, result_sha256, evidence_sha256, error_code, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending', 1, NULL, NULL, NULL, ?, ?)",
        (
            effect_id,
            operation["id"],
            operation["workspace_id"],
            effect_kind,
            logical_target_id,
            operation["request_sha256"],
            now,
            now,
        ),
    )
    connection.execute(
        "INSERT INTO workspace_component_effect_transition "
        "(effect_id, sequence, state, reason_code, evidence_sha256, recorded_at) "
        "VALUES (?, 1, 'pending', 'effect_pending', NULL, ?)",
        (effect_id, now),
    )
    row = connection.execute(
        "SELECT * FROM workspace_component_effect WHERE id = ?", (effect_id,)
    ).fetchone()
    assert row is not None
    return row


def _append_effect_transition(
    connection: sqlite3.Connection,
    effect: sqlite3.Row,
    state: str,
    reason_code: str,
    evidence_sha256: str | None = None,
    *,
    result_sha256: str | None = None,
    error_code: str | None = None,
) -> sqlite3.Row:
    sequence = int(effect["version"]) + 1
    now = utc_now_text()
    connection.execute(
        "INSERT INTO workspace_component_effect_transition "
        "(effect_id, sequence, state, reason_code, evidence_sha256, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (effect["id"], sequence, state, reason_code, evidence_sha256, now),
    )
    connection.execute(
        "UPDATE workspace_component_effect SET state = ?, version = ?, result_sha256 = ?, "
        "evidence_sha256 = ?, error_code = ?, updated_at = ? WHERE id = ?",
        (state, sequence, result_sha256, evidence_sha256, error_code, now, effect["id"]),
    )
    row = connection.execute(
        "SELECT * FROM workspace_component_effect WHERE id = ?", (effect["id"],)
    ).fetchone()
    assert row is not None
    return row


def _approved_proposal(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    component_id: str,
    proposal_id: str,
    request_sha256: str,
    action: str,
    expected_revision: int,
    manifest_sha256: str,
    package_sha256: str,
) -> sqlite3.Row:
    proposal = connection.execute(
        "SELECT proposal.* FROM workspace_component_proposal AS proposal "
        "JOIN workspace_component_decision AS decision ON decision.proposal_id = proposal.id "
        "AND decision.workspace_id = proposal.workspace_id "
        "WHERE proposal.id = ? AND proposal.workspace_id = ? AND proposal.component_id = ? "
        "AND proposal.request_sha256 = ? AND decision.request_sha256 = proposal.request_sha256 "
        "AND decision.decision = 'approved'",
        (proposal_id, workspace_id, component_id, request_sha256),
    ).fetchone()
    if proposal is None:
        raise DesktopApiError(409, "desktop_component_approval_required")
    if proposal["change_kind"] != action:
        raise DesktopApiError(409, "desktop_component_approval_action_mismatch")
    if (
        proposal["expected_revision"] != expected_revision
        or proposal["manifest_sha256"] != manifest_sha256
        or proposal["package_sha256"] != package_sha256
    ):
        raise DesktopApiError(409, "desktop_component_approval_binding_mismatch")
    return proposal


def _audit_state(
    connection: sqlite3.Connection,
    workspace: sqlite3.Row,
    installation: sqlite3.Row,
    *,
    revision: int,
    state: str,
    operation_id: str,
) -> None:
    append_audit_event(
        connection,
        event_id=_id("event"),
        owner_id=str(workspace["owner_id"]),
        workspace_id=str(workspace["id"]),
        event_type="workspace_component_state_changed",
        payload={
            "component_id": installation["component_id"],
            "installation_id": installation["id"],
            "operation_id": operation_id,
            "revision": revision,
            "state": state,
        },
    )


def _fence_invocation_operation(
    connection: sqlite3.Connection,
    operation: sqlite3.Row,
    *,
    reason_prefix: str,
) -> None:
    if operation["state"] != "dispatching":
        return
    effect = connection.execute(
        "SELECT * FROM workspace_component_effect WHERE operation_id = ?",
        (operation["id"],),
    ).fetchone()
    if effect is not None and effect["state"] == "pending":
        effect = _append_effect_transition(
            connection,
            effect,
            "unknown",
            f"{reason_prefix}_outcome_unknown",
        )
        _append_effect_transition(
            connection,
            effect,
            "reconciliation_required",
            f"{reason_prefix}_reconciliation_required",
        )
    operation = _append_operation_transition(
        connection,
        operation,
        "ambiguous",
        f"{reason_prefix}_outcome_ambiguous",
    )
    _append_operation_transition(
        connection,
        operation,
        "reconciliation_required",
        f"{reason_prefix}_reconciliation_required",
    )


def _fence_pending_invocations(
    connection: sqlite3.Connection,
    *,
    installation_id: str,
    reason_prefix: str,
) -> None:
    for operation in list(
        connection.execute(
            "SELECT * FROM workspace_component_operation WHERE installation_id = ? "
            "AND kind = 'invoke' AND state = 'dispatching' ORDER BY created_at, id",
            (installation_id,),
        )
    ):
        _fence_invocation_operation(connection, operation, reason_prefix=reason_prefix)


def _revoke_authority(
    connection: sqlite3.Connection,
    *,
    workspace: sqlite3.Row,
    installation: sqlite3.Row,
    reason_code: str,
    actor_type: str,
) -> None:
    now = utc_now_text()
    grants = list(
        connection.execute(
            "SELECT * FROM workspace_component_grant WHERE installation_id = ? "
            "AND state = 'active'",
            (installation["id"],),
        )
    )
    for grant in grants:
        connection.execute(
            "UPDATE workspace_component_grant SET state = 'revoked', updated_at = ? WHERE id = ?",
            (now, grant["id"]),
        )
        connection.execute(
            "INSERT INTO workspace_component_revocation "
            "(id, workspace_id, installation_id, runtime_instance_id, grant_id, reason_code, "
            "actor_type, actor_id, created_at) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?)",
            (
                _id("revocation"),
                workspace["id"],
                installation["id"],
                grant["id"],
                reason_code,
                actor_type,
                workspace["owner_id"] if actor_type == "owner" else None,
                now,
            ),
        )
    runtimes = list(
        connection.execute(
            "SELECT * FROM workspace_component_runtime_instance WHERE installation_id = ? "
            "AND state = 'active'",
            (installation["id"],),
        )
    )
    for runtime in runtimes:
        connection.execute(
            "UPDATE workspace_component_workload_lease SET state = 'revoked', updated_at = ? "
            "WHERE runtime_instance_id = ? AND state = 'active'",
            (now, runtime["id"]),
        )
        connection.execute(
            "UPDATE workspace_component_network_lease SET state = 'revoked', updated_at = ? "
            "WHERE runtime_instance_id = ? AND state = 'active'",
            (now, runtime["id"]),
        )
        connection.execute(
            "UPDATE workspace_component_runtime_instance SET state = 'revoked', updated_at = ? "
            "WHERE id = ?",
            (now, runtime["id"]),
        )
        connection.execute(
            "INSERT INTO workspace_component_revocation "
            "(id, workspace_id, installation_id, runtime_instance_id, grant_id, reason_code, "
            "actor_type, actor_id, created_at) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?)",
            (
                _id("revocation"),
                workspace["id"],
                installation["id"],
                runtime["id"],
                reason_code,
                actor_type,
                workspace["owner_id"] if actor_type == "owner" else None,
                now,
            ),
        )


def _lifecycle_ticket(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
    operation = connection.execute(
        "SELECT request_json FROM workspace_component_operation WHERE id = ?",
        (row["operation_id"],),
    ).fetchone()
    if operation is None:
        raise DesktopApiError(409, "desktop_component_lifecycle_ticket_invalid")
    request = _decode_json(str(operation["request_json"]))
    if not isinstance(request, dict) or not isinstance(request.get("proposal_id"), str):
        raise DesktopApiError(409, "desktop_component_lifecycle_ticket_invalid")
    proposal = connection.execute(
        "SELECT * FROM workspace_component_proposal WHERE id = ? AND workspace_id = ? "
        "AND component_id = ?",
        (request["proposal_id"], row["workspace_id"], row["component_id"]),
    ).fetchone()
    if proposal is None:
        raise DesktopApiError(409, "desktop_component_lifecycle_ticket_invalid")
    catalog = _catalog_row(connection, str(row["component_id"]), str(proposal["target_version"]))
    manifest = _decode_json(str(catalog["manifest_json"]))
    if not isinstance(manifest, dict) or type(manifest.get("quiesce_timeout_ms")) is not int:
        raise DesktopApiError(409, "desktop_component_lifecycle_ticket_invalid")
    return {
        "operation_id": row["operation_id"],
        "effect_id": row["effect_id"],
        "workspace_id": row["workspace_id"],
        "component_id": row["component_id"],
        "action": row["action"],
        "adapter_id": row["adapter_id"],
        "installation_id": row["installation_id"],
        "binding_generation": row["binding_generation"],
        "runtime_instance_id": row["reserved_runtime_instance_id"],
        "workload_identity_digest": row["workload_identity_digest"],
        "request_sha256": row["request_sha256"],
        "manifest_sha256": row["manifest_sha256"],
        "package_sha256": row["package_sha256"],
        "configuration": _decode_json(str(proposal["desired_configuration_json"])),
        "configuration_sha256": proposal["desired_configuration_sha256"],
        "slot_bindings": _decode_json(str(proposal["desired_slot_bindings_json"])),
        "slot_bindings_sha256": proposal["desired_slot_bindings_sha256"],
        "dependency_graph": _decode_json(str(proposal["dependency_graph_json"])),
        "dependency_graph_sha256": proposal["dependency_graph_sha256"],
        "quiesce_timeout_ms": manifest["quiesce_timeout_ms"],
    }


def _install_slot_bindings(
    connection: sqlite3.Connection,
    installation_id: str,
    generation: int,
    bindings_json: str,
    now: str,
) -> None:
    bindings = _decode_json(bindings_json)
    assert isinstance(bindings, list)
    for binding in bindings:
        assert isinstance(binding, dict)
        config_json = canonical_json(binding["configuration"])
        connection.execute(
            "INSERT INTO workspace_component_slot_binding "
            "(installation_id, generation, slot_id, binding_key, order_index, config_json, "
            "config_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                installation_id,
                generation,
                binding["slot_id"],
                binding["binding_key"],
                binding["order_index"],
                config_json,
                hashlib.sha256(config_json.encode()).hexdigest(),
                now,
            ),
        )


def _insert_lifecycle_receipt(
    connection: sqlite3.Connection,
    *,
    dispatch: sqlite3.Row,
    outcome: str,
    health_state: str | None,
    result_sha256: str | None,
    evidence_sha256: str,
    error_code: str | None,
) -> tuple[str, bool]:
    receipt = {
        "adapter_id": dispatch["adapter_id"],
        "binding_generation": dispatch["binding_generation"],
        "component_id": dispatch["component_id"],
        "effect_id": dispatch["effect_id"],
        "error_code": error_code,
        "evidence_sha256": evidence_sha256,
        "health_state": health_state,
        "installation_id": dispatch["installation_id"],
        "manifest_sha256": dispatch["manifest_sha256"],
        "operation_id": dispatch["operation_id"],
        "outcome": outcome,
        "package_sha256": dispatch["package_sha256"],
        "request_sha256": dispatch["request_sha256"],
        "result_sha256": result_sha256,
        "runtime_instance_id": dispatch["reserved_runtime_instance_id"],
        "workload_identity_digest": dispatch["workload_identity_digest"],
        "workspace_id": dispatch["workspace_id"],
    }
    receipt_sha256 = digest_json(receipt)
    existing = connection.execute(
        "SELECT receipt_sha256 FROM workspace_component_lifecycle_receipt WHERE operation_id = ?",
        (dispatch["operation_id"],),
    ).fetchone()
    if existing is not None:
        if existing["receipt_sha256"] != receipt_sha256:
            raise DesktopApiError(409, "desktop_component_lifecycle_receipt_drift")
        return receipt_sha256, True
    connection.execute(
        "INSERT INTO workspace_component_lifecycle_receipt "
        "(operation_id, effect_id, workspace_id, component_id, installation_id, "
        "binding_generation, runtime_instance_id, adapter_id, request_sha256, manifest_sha256, "
        "package_sha256, outcome, health_state, workload_identity_digest, result_sha256, "
        "evidence_sha256, error_code, receipt_sha256, settled_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            dispatch["operation_id"],
            dispatch["effect_id"],
            dispatch["workspace_id"],
            dispatch["component_id"],
            dispatch["installation_id"],
            dispatch["binding_generation"],
            dispatch["reserved_runtime_instance_id"],
            dispatch["adapter_id"],
            dispatch["request_sha256"],
            dispatch["manifest_sha256"],
            dispatch["package_sha256"],
            outcome,
            health_state,
            dispatch["workload_identity_digest"],
            result_sha256,
            evidence_sha256,
            error_code,
            receipt_sha256,
            utc_now_text(),
        ),
    )
    return receipt_sha256, False


def apply_component_action_v2(  # noqa: C901
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    component_id: str,
    action: str,
    phase: str,
    proposal_id: str,
    request_sha256: str,
    expected_revision: int,
    manifest_sha256: str,
    package_sha256: str,
    idempotency_key: str,
    operation_id: str | None,
    outcome: str | None,
    evidence_sha256: str | None,
    health_state: str | None,
    runtime_instance_id: str | None = None,
    workload_identity_digest: str | None = None,
    error_code: str | None = None,
) -> dict[str, object]:
    """Prepare or settle one exact native lifecycle effect; never execute an adapter here."""

    allowed_actions = {
        "install",
        "bind",
        "activate",
        "disable",
        "upgrade",
        "rollback",
        "revoke",
        "uninstall",
    }
    if action not in allowed_actions or phase not in {"prepare", "settle"}:
        raise DesktopApiError(400, "desktop_component_action_invalid")
    if not _IDEMPOTENCY.fullmatch(idempotency_key):
        raise DesktopApiError(400, "desktop_component_idempotency_key_invalid")
    if phase == "prepare" and any(
        value is not None
        for value in (
            operation_id,
            outcome,
            evidence_sha256,
            health_state,
            runtime_instance_id,
            workload_identity_digest,
            error_code,
        )
    ):
        raise DesktopApiError(400, "desktop_component_prepare_payload_invalid")
    if phase == "settle" and (
        operation_id is None
        or outcome not in {"succeeded", "failed", "unknown"}
        or evidence_sha256 is None
        or _SHA256.fullmatch(evidence_sha256) is None
    ):
        raise DesktopApiError(400, "desktop_component_settle_payload_invalid")
    if outcome == "failed" and not error_code:
        raise DesktopApiError(400, "desktop_component_settle_payload_invalid")
    if outcome == "succeeded" and error_code is not None:
        raise DesktopApiError(400, "desktop_component_settle_payload_invalid")
    if (
        action == "activate"
        and phase == "settle"
        and outcome == "succeeded"
        and (
            health_state != "healthy"
            or runtime_instance_id is None
            or workload_identity_digest is None
            or _SHA256.fullmatch(workload_identity_digest) is None
        )
    ):
        raise DesktopApiError(409, "desktop_component_activation_health_required")
    try:
        connection.execute("BEGIN IMMEDIATE")
        workspace = _live_workspace(connection, workspace_id)
        current = _installation(
            connection,
            workspace_id=workspace_id,
            component_id=component_id,
            required=action != "install",
        )
        proposal = _approved_proposal(
            connection,
            workspace_id=workspace_id,
            component_id=component_id,
            proposal_id=proposal_id,
            request_sha256=request_sha256,
            action=action,
            expected_revision=expected_revision,
            manifest_sha256=manifest_sha256,
            package_sha256=package_sha256,
        )
        catalog = _catalog_row(connection, component_id, str(proposal["target_version"]))
        package = _package_identity(connection, catalog, workspace_id)
        if (
            package["manifest_sha256"] != manifest_sha256
            or package["package_sha256"] != package_sha256
        ):
            raise DesktopApiError(409, "desktop_component_package_identity_stale")
        revision = 0 if current is None else int(current["revision"])
        if phase == "prepare":
            if revision != expected_revision:
                raise DesktopApiError(409, "desktop_component_revision_conflict")
            _validate_lifecycle_transition(
                connection,
                current=current,
                action=action,
                target_version=str(proposal["target_version"]),
                manifest_sha256=manifest_sha256,
                package_sha256=package_sha256,
            )
        action_request = {
            "action": action,
            "component_id": component_id,
            "expected_revision": expected_revision,
            "manifest_sha256": manifest_sha256,
            "package_sha256": package_sha256,
            "proposal_id": proposal_id,
            "request_sha256": request_sha256,
            "workspace_id": workspace_id,
        }
        if phase == "prepare":
            unresolved = connection.execute(
                "SELECT 1 FROM workspace_component_operation WHERE workspace_id = ? "
                "AND component_id = ? AND kind <> 'invoke' "
                "AND idempotency_key <> ? "
                "AND state IN ('accepted','authorized','dispatching','ambiguous',"
                "'reconciliation_required') LIMIT 1",
                (workspace_id, component_id, idempotency_key),
            ).fetchone()
            if unresolved is not None:
                raise DesktopApiError(409, "desktop_component_lifecycle_reconciliation_required")
            operation, replayed = _create_operation(
                connection,
                workspace=workspace,
                component_id=component_id,
                installation=current,
                kind=action,
                action=None,
                expected_revision=expected_revision,
                binding_generation=None if current is None else int(current["binding_generation"]),
                runtime_instance_id=None,
                manifest_sha256=manifest_sha256,
                package_sha256=package_sha256,
                idempotency_key=idempotency_key,
                request=action_request,
            )
            dispatch = connection.execute(
                "SELECT * FROM workspace_component_lifecycle_dispatch WHERE operation_id = ?",
                (operation["id"],),
            ).fetchone()
            if not replayed:
                operation = _append_operation_transition(
                    connection, operation, "authorized", "owner_approval_verified"
                )
                operation = _append_operation_transition(
                    connection, operation, "dispatching", "lifecycle_dispatch_started"
                )
                effect = _create_effect(connection, operation, "lifecycle", component_id)
                reserved_runtime_id = _id("runtime") if action == "activate" else None
                reserved_workload = (
                    digest_json(
                        {
                            "binding_generation": current["binding_generation"],
                            "operation_id": operation["id"],
                            "runtime_instance_id": reserved_runtime_id,
                            "workspace_id": workspace_id,
                        }
                    )
                    if action == "activate" and current is not None
                    else None
                )
                connection.execute(
                    "INSERT INTO workspace_component_lifecycle_dispatch "
                    "(operation_id, effect_id, workspace_id, component_id, installation_id, "
                    "binding_generation, action, adapter_id, reserved_runtime_instance_id, "
                    "workload_identity_digest, request_sha256, manifest_sha256, package_sha256, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        operation["id"],
                        effect["id"],
                        workspace_id,
                        component_id,
                        None if current is None else current["id"],
                        None if current is None else current["binding_generation"],
                        action,
                        catalog["adapter_id"],
                        reserved_runtime_id,
                        reserved_workload,
                        operation["request_sha256"],
                        manifest_sha256,
                        package_sha256,
                        utc_now_text(),
                    ),
                )
                dispatch = connection.execute(
                    "SELECT * FROM workspace_component_lifecycle_dispatch WHERE operation_id = ?",
                    (operation["id"],),
                ).fetchone()
                if action in {"disable", "upgrade", "rollback", "revoke", "uninstall"}:
                    assert current is not None
                    _fence_pending_invocations(
                        connection,
                        installation_id=str(current["id"]),
                        reason_prefix=f"component_{action}_prepare_fence",
                    )
                    _revoke_authority(
                        connection,
                        workspace=workspace,
                        installation=current,
                        reason_code=f"component_{action}_prepare_fence",
                        actor_type="owner",
                    )
            if dispatch is None or operation["state"] not in {
                "dispatching",
                "succeeded",
                "failed",
                "ambiguous",
                "reconciliation_required",
                "reconciled_succeeded",
                "reconciled_failed",
            }:
                raise DesktopApiError(409, "desktop_component_lifecycle_reconciliation_required")
            connection.execute("COMMIT")
            result_installation = _installation(
                connection,
                workspace_id=workspace_id,
                component_id=component_id,
                required=False,
            )
            return {
                "operation": _operation_payload(operation),
                "installation": None
                if result_installation is None
                else _installation_payload(result_installation),
                "lifecycle_ticket": _lifecycle_ticket(connection, dispatch),
                "replayed": replayed,
            }

        operation = connection.execute(
            "SELECT * FROM workspace_component_operation WHERE id = ? AND workspace_id = ? "
            "AND component_id = ?",
            (operation_id, workspace_id, component_id),
        ).fetchone()
        dispatch = connection.execute(
            "SELECT * FROM workspace_component_lifecycle_dispatch WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        effect = connection.execute(
            "SELECT * FROM workspace_component_effect WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if operation is None or dispatch is None or effect is None:
            raise DesktopApiError(404, "desktop_component_lifecycle_operation_not_found")
        if (
            operation["request_sha256"] != digest_json(action_request)
            or dispatch["request_sha256"] != operation["request_sha256"]
            or dispatch["action"] != action
            or dispatch["manifest_sha256"] != manifest_sha256
            or dispatch["package_sha256"] != package_sha256
            or (
                action == "activate"
                and (
                    runtime_instance_id != dispatch["reserved_runtime_instance_id"]
                    or workload_identity_digest != dispatch["workload_identity_digest"]
                )
            )
        ):
            raise DesktopApiError(409, "desktop_component_lifecycle_identity_drift")
        result_sha256 = evidence_sha256 if outcome == "succeeded" else None
        existing_receipt = connection.execute(
            "SELECT 1 FROM workspace_component_lifecycle_receipt WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if existing_receipt is not None:
            _, replayed = _insert_lifecycle_receipt(
                connection,
                dispatch=dispatch,
                outcome=str(outcome),
                health_state=health_state,
                result_sha256=result_sha256,
                evidence_sha256=str(evidence_sha256),
                error_code=error_code,
            )
            assert replayed
            connection.execute("COMMIT")
            result_installation = _installation(
                connection, workspace_id=workspace_id, component_id=component_id, required=False
            )
            return {
                "operation": _operation_payload(operation),
                "installation": None
                if result_installation is None
                else _installation_payload(result_installation),
                "lifecycle_ticket": _lifecycle_ticket(connection, dispatch),
                "replayed": True,
            }
        if revision != expected_revision:
            raise DesktopApiError(409, "desktop_component_revision_conflict")
        _validate_lifecycle_transition(
            connection,
            current=current,
            action=action,
            target_version=str(proposal["target_version"]),
            manifest_sha256=manifest_sha256,
            package_sha256=package_sha256,
        )
        if operation["state"] != "dispatching" or effect["state"] != "pending":
            raise DesktopApiError(409, "desktop_component_lifecycle_reconciliation_required")
        if action == "activate" and outcome == "succeeded":
            assert current is not None
            connection.execute(
                "INSERT INTO workspace_component_runtime_instance "
                "(id, installation_id, workspace_id, generation, operation_generation, "
                "workload_identity_digest, state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                (
                    runtime_instance_id,
                    current["id"],
                    workspace_id,
                    current["binding_generation"],
                    operation["operation_generation"],
                    workload_identity_digest,
                    utc_now_text(),
                    utc_now_text(),
                ),
            )
        _, receipt_replayed = _insert_lifecycle_receipt(
            connection,
            dispatch=dispatch,
            outcome=str(outcome),
            health_state=health_state,
            result_sha256=result_sha256,
            evidence_sha256=str(evidence_sha256),
            error_code=error_code,
        )
        assert not receipt_replayed
        if outcome != "succeeded":
            effect = _append_effect_transition(
                connection,
                effect,
                "unknown" if outcome == "unknown" else "failed",
                f"lifecycle_{outcome}",
                evidence_sha256,
                error_code=error_code,
            )
            operation = _append_operation_transition(
                connection,
                operation,
                "ambiguous" if outcome == "unknown" else "failed",
                f"lifecycle_{outcome}",
                evidence_sha256,
                error_code=error_code,
            )
            if current is not None and action in {
                "activate",
                "disable",
                "upgrade",
                "rollback",
                "revoke",
                "uninstall",
            }:
                now = utc_now_text()
                if action in {"disable", "upgrade", "rollback", "revoke", "uninstall"}:
                    connection.execute(
                        "UPDATE workspace_component_binding_generation SET state = ?, updated_at = ? "
                        "WHERE installation_id = ? AND generation = ? "
                        "AND state IN ('installed','bound','active')",
                        (
                            "revoked" if action == "revoke" else "failed",
                            now,
                            current["id"],
                            current["binding_generation"],
                        ),
                    )
                blocked_state = "revoked" if action == "revoke" else "blocked"
                next_revision = int(current["revision"]) + 1
                _audit_state(
                    connection,
                    workspace,
                    current,
                    revision=next_revision,
                    state=blocked_state,
                    operation_id=str(operation_id),
                )
                connection.execute(
                    "UPDATE workspace_component_installation SET state = ?, revision = ?, "
                    "current_runtime_instance_id = NULL, updated_at = ? WHERE id = ?",
                    (blocked_state, next_revision, now, current["id"]),
                )
            connection.execute("COMMIT")
            result_installation = _installation(
                connection,
                workspace_id=workspace_id,
                component_id=component_id,
                required=False,
            )
            return {
                "operation": _operation_payload(operation),
                "installation": None
                if result_installation is None
                else _installation_payload(result_installation),
                "lifecycle_ticket": _lifecycle_ticket(connection, dispatch),
                "replayed": False,
            }
        now = utc_now_text()
        if action == "install":
            installation_id = _id("installation")
            config_values = (
                proposal["desired_configuration_json"],
                proposal["desired_configuration_sha256"],
                proposal["desired_slot_bindings_json"],
                proposal["desired_slot_bindings_sha256"],
                proposal["dependency_graph_json"],
                proposal["dependency_graph_sha256"],
            )
            connection.execute(
                "INSERT INTO workspace_component_installation "
                "(id, owner_id, workspace_id, component_id, revision, version, manifest_sha256, "
                "package_sha256, state, binding_generation, current_runtime_instance_id, "
                "proposal_id, request_sha256, configuration_json, configuration_sha256, "
                "slot_bindings_json, slot_bindings_sha256, dependency_graph_json, "
                "dependency_graph_sha256, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 1, ?, ?, ?, 'installed', 1, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    installation_id,
                    workspace["owner_id"],
                    workspace_id,
                    component_id,
                    proposal["target_version"],
                    manifest_sha256,
                    package_sha256,
                    proposal_id,
                    request_sha256,
                    *config_values,
                    now,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO workspace_component_binding_generation "
                "(installation_id, workspace_id, component_id, generation, version, "
                "manifest_sha256, package_sha256, proposal_id, request_sha256, "
                "configuration_json, configuration_sha256, slot_bindings_json, "
                "slot_bindings_sha256, dependency_graph_json, dependency_graph_sha256, "
                "state, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'installed', ?, ?)",
                (
                    installation_id,
                    workspace_id,
                    component_id,
                    proposal["target_version"],
                    manifest_sha256,
                    package_sha256,
                    proposal_id,
                    request_sha256,
                    *config_values,
                    now,
                    now,
                ),
            )
            _install_slot_bindings(
                connection, installation_id, 1, str(proposal["desired_slot_bindings_json"]), now
            )
            current = _installation(
                connection, workspace_id=workspace_id, component_id=component_id
            )
        elif action == "bind":
            assert current is not None
            connection.execute(
                "UPDATE workspace_component_binding_generation SET state = 'bound', updated_at = ? "
                "WHERE installation_id = ? AND generation = ? AND state = 'installed'",
                (now, current["id"], current["binding_generation"]),
            )
            next_revision = int(current["revision"]) + 1
            _audit_state(
                connection,
                workspace,
                current,
                revision=next_revision,
                state="bound",
                operation_id=str(operation_id),
            )
            connection.execute(
                "UPDATE workspace_component_installation SET state = 'bound', revision = ?, updated_at = ? WHERE id = ?",
                (next_revision, now, current["id"]),
            )
        elif action in {"upgrade", "rollback"}:
            assert current is not None
            _revoke_authority(
                connection,
                workspace=workspace,
                installation=current,
                reason_code=f"component_{action}",
                actor_type="owner",
            )
            connection.execute(
                "UPDATE workspace_component_binding_generation SET state = 'disabled', updated_at = ? "
                "WHERE installation_id = ? AND state = 'active'",
                (now, current["id"]),
            )
            generation = int(current["binding_generation"]) + 1
            config_values = (
                proposal["desired_configuration_json"],
                proposal["desired_configuration_sha256"],
                proposal["desired_slot_bindings_json"],
                proposal["desired_slot_bindings_sha256"],
                proposal["dependency_graph_json"],
                proposal["dependency_graph_sha256"],
            )
            connection.execute(
                "INSERT INTO workspace_component_binding_generation "
                "(installation_id, workspace_id, component_id, generation, version, manifest_sha256, "
                "package_sha256, proposal_id, request_sha256, configuration_json, "
                "configuration_sha256, slot_bindings_json, slot_bindings_sha256, "
                "dependency_graph_json, dependency_graph_sha256, state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'bound', ?, ?)",
                (
                    current["id"],
                    workspace_id,
                    component_id,
                    generation,
                    proposal["target_version"],
                    manifest_sha256,
                    package_sha256,
                    proposal_id,
                    request_sha256,
                    *config_values,
                    now,
                    now,
                ),
            )
            _install_slot_bindings(
                connection,
                str(current["id"]),
                generation,
                str(proposal["desired_slot_bindings_json"]),
                now,
            )
            next_revision = int(current["revision"]) + 1
            _audit_state(
                connection,
                workspace,
                current,
                revision=next_revision,
                state="bound",
                operation_id=str(operation_id),
            )
            connection.execute(
                "UPDATE workspace_component_installation SET revision = ?, version = ?, manifest_sha256 = ?, "
                "package_sha256 = ?, state = 'bound', binding_generation = ?, current_runtime_instance_id = NULL, "
                "proposal_id = ?, request_sha256 = ?, configuration_json = ?, configuration_sha256 = ?, "
                "slot_bindings_json = ?, slot_bindings_sha256 = ?, dependency_graph_json = ?, "
                "dependency_graph_sha256 = ?, updated_at = ? WHERE id = ?",
                (
                    next_revision,
                    proposal["target_version"],
                    manifest_sha256,
                    package_sha256,
                    generation,
                    proposal_id,
                    request_sha256,
                    *config_values,
                    now,
                    current["id"],
                ),
            )
        elif action == "activate":
            assert current is not None
            if current["state"] not in {"bound", "blocked"}:
                raise DesktopApiError(409, "desktop_component_not_bound")
            requested = _decode_json(str(proposal["requested_grants_json"]))
            assert isinstance(requested, list)
            grant_id = _id("grant")
            seconds = min(
                int(item["expires_in_seconds"]) for item in requested if isinstance(item, dict)
            )
            not_before_dt = datetime.now(UTC)
            not_before = not_before_dt.isoformat(timespec="microseconds").replace("+00:00", "Z")
            expires_at = (
                (not_before_dt + timedelta(seconds=seconds))
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z")
            )
            actions_json = canonical_json(
                sorted(str(item["action"]) for item in requested if isinstance(item, dict))
            )
            limits = {
                "max_calls": min(
                    int(catalog["max_calls"]),
                    sum(
                        int(item["maximum_invocations"])
                        for item in requested
                        if isinstance(item, dict)
                    ),
                ),
                "max_bytes_in": min(
                    int(catalog["max_bytes_in"]),
                    sum(
                        int(item["maximum_bytes_in"])
                        for item in requested
                        if isinstance(item, dict)
                    ),
                ),
                "max_bytes_out": min(
                    int(catalog["max_bytes_out"]),
                    sum(
                        int(item["maximum_bytes_out"])
                        for item in requested
                        if isinstance(item, dict)
                    ),
                ),
                "max_tokens": min(
                    int(catalog["max_tokens"]),
                    sum(
                        int(item["maximum_tokens"]) for item in requested if isinstance(item, dict)
                    ),
                ),
                "max_wall_time_ms": min(
                    int(catalog["max_wall_time_ms"]),
                    sum(
                        int(item["maximum_wall_time_ms"])
                        for item in requested
                        if isinstance(item, dict)
                    ),
                ),
                "max_cost_units": min(
                    int(catalog["max_cost_units"]),
                    sum(
                        int(item["maximum_cost_units"])
                        for item in requested
                        if isinstance(item, dict)
                    ),
                ),
            }
            connection.execute(
                "INSERT INTO workspace_component_grant "
                "(id, owner_id, workspace_id, installation_id, generation, runtime_instance_id, component_id, "
                "version, manifest_sha256, package_sha256, request_sha256, workload_identity_digest, "
                "actions_json, scope_json, requires_network, state, not_before, expires_at, max_calls, "
                "max_bytes_in, max_bytes_out, max_tokens, max_wall_time_ms, max_cost_units, max_retries, "
                "max_concurrency, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    grant_id,
                    workspace["owner_id"],
                    workspace_id,
                    current["id"],
                    current["binding_generation"],
                    runtime_instance_id,
                    component_id,
                    proposal["target_version"],
                    manifest_sha256,
                    package_sha256,
                    request_sha256,
                    workload_identity_digest,
                    actions_json,
                    canonical_json(requested),
                    catalog["requires_network"],
                    not_before,
                    expires_at,
                    limits["max_calls"],
                    limits["max_bytes_in"],
                    limits["max_bytes_out"],
                    limits["max_tokens"],
                    limits["max_wall_time_ms"],
                    limits["max_cost_units"],
                    catalog["max_retries"],
                    catalog["max_concurrency"],
                    now,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO workspace_component_grant_usage "
                "(grant_id, calls, bytes_in, bytes_out_reserved, tokens_reserved, wall_time_ms_reserved, "
                "cost_units, retries, row_version, updated_at) VALUES (?, 0, 0, 0, 0, 0, 0, 0, 1, ?)",
                (grant_id, now),
            )
            workload_lease_id = _id("workloadlease")
            connection.execute(
                "INSERT INTO workspace_component_workload_lease "
                "(id, workspace_id, installation_id, generation, runtime_instance_id, workload_identity_digest, "
                "fencing_token, state, not_before, expires_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)",
                (
                    workload_lease_id,
                    workspace_id,
                    current["id"],
                    current["binding_generation"],
                    runtime_instance_id,
                    workload_identity_digest,
                    current["binding_generation"],
                    not_before,
                    expires_at,
                    now,
                    now,
                ),
            )
            if catalog["requires_network"]:
                services = sorted(
                    {
                        str(item["logical_service_id"])
                        for item in requested
                        if isinstance(item, dict) and item.get("logical_service_id")
                    }
                )
                for service in services:
                    connection.execute(
                        "INSERT INTO workspace_component_network_lease "
                        "(id, workspace_id, grant_id, workload_lease_id, runtime_instance_id, installation_id, "
                        "generation, logical_service_id, fencing_token, state, not_before, expires_at, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'active', ?, ?, ?, ?)",
                        (
                            _id("networklease"),
                            workspace_id,
                            grant_id,
                            workload_lease_id,
                            runtime_instance_id,
                            current["id"],
                            current["binding_generation"],
                            service,
                            not_before,
                            expires_at,
                            now,
                            now,
                        ),
                    )
        elif action in {"disable", "revoke", "uninstall"}:
            assert current is not None
            _revoke_authority(
                connection,
                workspace=workspace,
                installation=current,
                reason_code=f"component_{action}",
                actor_type="owner",
            )
            binding_state = "revoked" if action in {"revoke", "uninstall"} else "disabled"
            connection.execute(
                "UPDATE workspace_component_binding_generation SET state = ?, updated_at = ? "
                "WHERE installation_id = ? AND state IN ('installed','bound','active')",
                (binding_state, now, current["id"]),
            )
            target_state = {"disable": "disabled", "revoke": "revoked", "uninstall": "uninstalled"}[
                action
            ]
            next_revision = int(current["revision"]) + 1
            if action == "uninstall" and current["state"] in {"bound", "active"}:
                _audit_state(
                    connection,
                    workspace,
                    current,
                    revision=next_revision,
                    state="revoked",
                    operation_id=str(operation_id),
                )
                connection.execute(
                    "UPDATE workspace_component_installation SET state = 'revoked', revision = ?, "
                    "current_runtime_instance_id = NULL, updated_at = ? WHERE id = ?",
                    (next_revision, now, current["id"]),
                )
                next_revision += 1
            _audit_state(
                connection,
                workspace,
                current,
                revision=next_revision,
                state=target_state,
                operation_id=str(operation_id),
            )
            connection.execute(
                "UPDATE workspace_component_installation SET state = ?, revision = ?, current_runtime_instance_id = NULL, updated_at = ? WHERE id = ?",
                (target_state, next_revision, now, current["id"]),
            )
        effect = _append_effect_transition(
            connection,
            effect,
            "committed",
            "native_lifecycle_committed",
            evidence_sha256,
            result_sha256=result_sha256,
        )
        operation = _append_operation_transition(
            connection,
            operation,
            "succeeded",
            "native_lifecycle_succeeded",
            evidence_sha256,
            result_sha256=result_sha256,
        )
        if action == "activate":
            assert current is not None
            connection.execute(
                "INSERT INTO workspace_component_health "
                "(id, workspace_id, installation_id, component_id, generation, runtime_instance_id, operation_id, "
                "state, manifest_sha256, package_sha256, workload_identity_digest, evidence_sha256, observed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'healthy', ?, ?, ?, ?, ?)",
                (
                    _id("health"),
                    workspace_id,
                    current["id"],
                    component_id,
                    current["binding_generation"],
                    runtime_instance_id,
                    operation_id,
                    manifest_sha256,
                    package_sha256,
                    workload_identity_digest,
                    evidence_sha256,
                    now,
                ),
            )
            connection.execute(
                "UPDATE workspace_component_binding_generation SET state = 'active', updated_at = ? "
                "WHERE installation_id = ? AND generation = ?",
                (now, current["id"], current["binding_generation"]),
            )
            next_revision = int(current["revision"]) + 1
            _audit_state(
                connection,
                workspace,
                current,
                revision=next_revision,
                state="active",
                operation_id=str(operation_id),
            )
            connection.execute(
                "UPDATE workspace_component_installation SET state = 'active', revision = ?, "
                "current_runtime_instance_id = ?, updated_at = ? WHERE id = ?",
                (next_revision, runtime_instance_id, now, current["id"]),
            )
        result_installation = _installation(
            connection, workspace_id=workspace_id, component_id=component_id, required=False
        )
        connection.execute("COMMIT")
        return {
            "operation": _operation_payload(operation),
            "installation": None
            if result_installation is None
            else _installation_payload(result_installation),
            "lifecycle_ticket": _lifecycle_ticket(connection, dispatch),
            "replayed": False,
        }
    except DesktopApiError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopApiError(503, "desktop_component_action_failed") from exc


def begin_component_invocation(  # noqa: C901
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    component_id: str,
    action: str,
    expected_revision: int,
    binding_generation: int,
    manifest_sha256: str,
    package_sha256: str,
    idempotency_key: str,
    arguments_sha256: str,
    logical_resource_id: str | None,
    resource_version: int | None,
    logical_service_id: str | None,
    bytes_in: int,
    bytes_out_reserved: int,
    tokens_reserved: int,
    wall_time_ms: int,
    cost_units: int,
) -> dict[str, object]:
    if (
        _OPERATION.fullmatch(action) is None
        or not _IDEMPOTENCY.fullmatch(idempotency_key)
        or _SHA256.fullmatch(arguments_sha256) is None
    ):
        raise DesktopApiError(400, "desktop_component_invocation_invalid")
    request = {
        "action": action,
        "arguments_sha256": arguments_sha256,
        "binding_generation": binding_generation,
        "bytes_in": bytes_in,
        "bytes_out_reserved": bytes_out_reserved,
        "component_id": component_id,
        "cost_units": cost_units,
        "expected_revision": expected_revision,
        "logical_resource_id": logical_resource_id,
        "logical_service_id": logical_service_id,
        "manifest_sha256": manifest_sha256,
        "package_sha256": package_sha256,
        "resource_version": resource_version,
        "tokens_reserved": tokens_reserved,
        "wall_time_ms": wall_time_ms,
        "workspace_id": workspace_id,
    }
    try:
        connection.execute("BEGIN IMMEDIATE")
        workspace = _live_workspace(connection, workspace_id)
        installation = _installation(
            connection, workspace_id=workspace_id, component_id=component_id
        )
        assert installation is not None
        if (
            installation["state"] != "active"
            or installation["revision"] != expected_revision
            or installation["binding_generation"] != binding_generation
            or installation["manifest_sha256"] != manifest_sha256
            or installation["package_sha256"] != package_sha256
        ):
            raise DesktopApiError(409, "desktop_component_invocation_binding_stale")
        if (
            connection.execute(
                "SELECT 1 FROM component_package_attestation WHERE component_id = ? AND version = ? "
                "AND manifest_sha256 = ? AND package_sha256 = ? "
                "AND attested_by IN ('runtime_manifest', 'owner_native_review')",
                (component_id, installation["version"], manifest_sha256, package_sha256),
            ).fetchone()
            is None
        ):
            raise DesktopApiError(409, "desktop_component_package_attestation_stale")
        runtime_id = str(installation["current_runtime_instance_id"])
        runtime = connection.execute(
            "SELECT * FROM workspace_component_runtime_instance WHERE id = ? AND workspace_id = ? "
            "AND installation_id = ? AND generation = ? AND state = 'active'",
            (runtime_id, workspace_id, installation["id"], binding_generation),
        ).fetchone()
        if (
            runtime is None
            or connection.execute(
                "SELECT 1 FROM workspace_component_health WHERE runtime_instance_id = ? "
                "AND installation_id = ? AND component_id = ? AND generation = ? AND state = 'healthy' "
                "AND manifest_sha256 = ? AND package_sha256 = ? "
                "AND workload_identity_digest = ? ORDER BY observed_at DESC LIMIT 1",
                (
                    runtime_id,
                    installation["id"],
                    component_id,
                    binding_generation,
                    manifest_sha256,
                    package_sha256,
                    None if runtime is None else runtime["workload_identity_digest"],
                ),
            ).fetchone()
            is None
        ):
            raise DesktopApiError(409, "desktop_component_runtime_health_unavailable")
        operation, replayed = _create_operation(
            connection,
            workspace=workspace,
            component_id=component_id,
            installation=installation,
            kind="invoke",
            action=action,
            expected_revision=expected_revision,
            binding_generation=binding_generation,
            runtime_instance_id=runtime_id,
            manifest_sha256=manifest_sha256,
            package_sha256=package_sha256,
            idempotency_key=idempotency_key,
            request=request,
        )
        grant = connection.execute(
            "SELECT grant.*, usage.calls, usage.bytes_in, usage.bytes_out_reserved, "
            "usage.tokens_reserved, usage.wall_time_ms_reserved, usage.cost_units, "
            "usage.retries, usage.row_version FROM workspace_component_grant AS grant "
            "JOIN workspace_component_grant_usage AS usage ON usage.grant_id = grant.id "
            "WHERE grant.runtime_instance_id = ? AND grant.state = 'active'",
            (runtime_id,),
        ).fetchone()
        lease = connection.execute(
            "SELECT * FROM workspace_component_workload_lease "
            "WHERE runtime_instance_id = ? AND state = 'active'",
            (runtime_id,),
        ).fetchone()
        if grant is None or lease is None:
            raise DesktopApiError(409, "desktop_component_authority_unavailable")
        if replayed:
            effect = connection.execute(
                "SELECT * FROM workspace_component_effect WHERE operation_id = ?",
                (operation["id"],),
            ).fetchone()
            if effect is None:
                raise DesktopApiError(409, "desktop_component_invocation_reconciliation_required")
            network = connection.execute(
                "SELECT fencing_token FROM workspace_component_network_lease "
                "WHERE grant_id = ? AND logical_service_id IS ? AND state = 'active'",
                (grant["id"], logical_service_id),
            ).fetchone()
            connection.execute("COMMIT")
            return {
                "ticket": _ticket(
                    connection,
                    operation,
                    installation,
                    grant,
                    lease,
                    None if network is None else int(network["fencing_token"]),
                ),
                "replayed": True,
            }
        now = utc_now_text()
        if not (grant["not_before"] <= now < grant["expires_at"]):
            raise DesktopApiError(409, "desktop_component_grant_expired")
        if not (lease["not_before"] <= now < lease["expires_at"]):
            raise DesktopApiError(409, "desktop_component_workload_lease_expired")
        granted_actions = _decode_json(str(grant["actions_json"]))
        if not isinstance(granted_actions, list) or any(
            not isinstance(item, str) for item in granted_actions
        ):
            raise DesktopApiError(409, "desktop_component_grant_payload_invalid")
        if action not in set(granted_actions):
            raise DesktopApiError(403, "desktop_component_action_not_granted")
        scope = _decode_json(str(grant["scope_json"]))
        if not isinstance(scope, list) or not any(
            isinstance(item, dict)
            and item.get("action") == action
            and item.get("logical_resource_id") == logical_resource_id
            and item.get("resource_version") == resource_version
            and item.get("logical_service_id") == logical_service_id
            for item in scope
        ):
            raise DesktopApiError(403, "desktop_component_scope_not_granted")
        if connection.execute(
            "SELECT 1 FROM workspace_component_revocation WHERE grant_id = ? "
            "OR runtime_instance_id = ? LIMIT 1",
            (grant["id"], runtime_id),
        ).fetchone():
            raise DesktopApiError(403, "desktop_component_authority_revoked")
        pending = int(
            connection.execute(
                "SELECT COUNT(*) FROM workspace_component_budget_reservation AS reservation "
                "JOIN workspace_component_operation AS operation ON operation.id = reservation.operation_id "
                "WHERE reservation.grant_id = ? AND operation.state NOT IN "
                "('succeeded', 'failed', 'reconciled_succeeded', 'reconciled_failed')",
                (grant["id"],),
            ).fetchone()[0]
        )
        dimensions = {
            "calls": (grant["calls"] + 1, grant["max_calls"]),
            "bytes_in": (grant["bytes_in"] + bytes_in, grant["max_bytes_in"]),
            "bytes_out": (
                grant["bytes_out_reserved"] + bytes_out_reserved,
                grant["max_bytes_out"],
            ),
            "tokens": (grant["tokens_reserved"] + tokens_reserved, grant["max_tokens"]),
            "wall_time": (
                grant["wall_time_ms_reserved"] + wall_time_ms,
                grant["max_wall_time_ms"],
            ),
            "cost": (grant["cost_units"] + cost_units, grant["max_cost_units"]),
            "concurrency": (pending + 1, grant["max_concurrency"]),
        }
        exhausted = next((name for name, (used, limit) in dimensions.items() if used > limit), None)
        if exhausted is not None:
            raise DesktopApiError(409, f"desktop_component_budget_{exhausted}_exhausted")
        network_fencing: int | None = None
        if grant["requires_network"]:
            network = connection.execute(
                "SELECT * FROM workspace_component_network_lease "
                "WHERE grant_id = ? AND logical_service_id = ? AND runtime_instance_id = ? "
                "AND state = 'active'",
                (grant["id"], logical_service_id, runtime_id),
            ).fetchone()
            if network is None or not (network["not_before"] <= now < network["expires_at"]):
                raise DesktopApiError(409, "desktop_component_network_lease_unavailable")
            network_fencing = int(network["fencing_token"])
        operation = _append_operation_transition(
            connection, operation, "authorized", "component_authority_verified"
        )
        connection.execute(
            "INSERT INTO workspace_component_budget_reservation "
            "(operation_id, grant_id, workspace_id, runtime_instance_id, request_sha256, calls, "
            "bytes_in, bytes_out_reserved, tokens_reserved, wall_time_ms_reserved, cost_units, "
            "retries_reserved, created_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 0, ?)",
            (
                operation["id"],
                grant["id"],
                workspace_id,
                runtime_id,
                operation["request_sha256"],
                bytes_in,
                bytes_out_reserved,
                tokens_reserved,
                wall_time_ms,
                cost_units,
                now,
            ),
        )
        connection.execute(
            "UPDATE workspace_component_grant_usage SET calls = calls + 1, "
            "bytes_in = bytes_in + ?, bytes_out_reserved = bytes_out_reserved + ?, "
            "tokens_reserved = tokens_reserved + ?, "
            "wall_time_ms_reserved = wall_time_ms_reserved + ?, cost_units = cost_units + ?, "
            "row_version = row_version + 1, updated_at = ? WHERE grant_id = ?",
            (
                bytes_in,
                bytes_out_reserved,
                tokens_reserved,
                wall_time_ms,
                cost_units,
                now,
                grant["id"],
            ),
        )
        operation = _append_operation_transition(
            connection, operation, "dispatching", "adapter_dispatch_started"
        )
        _create_effect(connection, operation, "adapter_invoke", action)
        append_audit_event(
            connection,
            event_id=_id("event"),
            owner_id=str(workspace["owner_id"]),
            workspace_id=workspace_id,
            event_type="workspace_component_invocation_begun",
            payload={
                "action": action,
                "component_id": component_id,
                "operation_id": operation["id"],
                "request_sha256": operation["request_sha256"],
            },
        )
        connection.execute("COMMIT")
        return {
            "ticket": _ticket(connection, operation, installation, grant, lease, network_fencing),
            "replayed": False,
        }
    except DesktopApiError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopApiError(503, "desktop_component_invocation_begin_failed") from exc


def _ticket(
    connection: sqlite3.Connection,
    operation: sqlite3.Row,
    installation: sqlite3.Row,
    grant: sqlite3.Row,
    lease: sqlite3.Row,
    network_fencing_token: int | None,
) -> dict[str, object]:
    request = _decode_json(str(operation["request_json"]))
    assert isinstance(request, dict)
    catalog = _catalog_row(connection, str(operation["component_id"]), str(grant["version"]))
    binding = connection.execute(
        "SELECT * FROM workspace_component_binding_generation WHERE installation_id = ? "
        "AND generation = ?",
        (installation["id"], installation["binding_generation"]),
    ).fetchone()
    if binding is None:
        raise DesktopApiError(409, "desktop_component_binding_generation_missing")
    return {
        "operation_id": operation["id"],
        "workspace_id": operation["workspace_id"],
        "component_id": operation["component_id"],
        "action": operation["action"],
        "request_sha256": operation["request_sha256"],
        "arguments_sha256": request["arguments_sha256"],
        "adapter_id": catalog["adapter_id"],
        "configuration": _decode_json(str(binding["configuration_json"])),
        "configuration_sha256": binding["configuration_sha256"],
        "slot_bindings": _decode_json(str(binding["slot_bindings_json"])),
        "slot_bindings_sha256": binding["slot_bindings_sha256"],
        "dependency_graph": _decode_json(str(binding["dependency_graph_json"])),
        "dependency_graph_sha256": binding["dependency_graph_sha256"],
        "manifest_sha256": binding["manifest_sha256"],
        "package_sha256": binding["package_sha256"],
        "binding_generation": installation["binding_generation"],
        "runtime_instance_id": grant["runtime_instance_id"],
        "workload_identity_digest": grant["workload_identity_digest"],
        "workload_fencing_token": lease["fencing_token"],
        "network_fencing_token": network_fencing_token,
        "expires_at": min(str(grant["expires_at"]), str(lease["expires_at"])),
    }


def _invocation_authority_is_current(
    connection: sqlite3.Connection,
    *,
    operation: sqlite3.Row,
    reservation: sqlite3.Row,
    now: str,
) -> bool:
    installation_id = operation["installation_id"]
    runtime_instance_id = reservation["runtime_instance_id"]
    generation = operation["binding_generation"]
    if (
        installation_id is None
        or generation is None
        or operation["workspace_id"] != reservation["workspace_id"]
        or operation["runtime_instance_id"] != runtime_instance_id
        or operation["request_sha256"] != reservation["request_sha256"]
    ):
        return False
    installation = connection.execute(
        "SELECT * FROM workspace_component_installation WHERE id = ? AND workspace_id = ? "
        "AND component_id = ?",
        (installation_id, operation["workspace_id"], operation["component_id"]),
    ).fetchone()
    if (
        installation is None
        or installation["state"] != "active"
        or installation["revision"] != operation["expected_revision"]
        or installation["binding_generation"] != generation
        or installation["current_runtime_instance_id"] != runtime_instance_id
        or installation["manifest_sha256"] != operation["manifest_sha256"]
        or installation["package_sha256"] != operation["package_sha256"]
    ):
        return False
    grant = connection.execute(
        "SELECT * FROM workspace_component_grant WHERE id = ? AND workspace_id = ? "
        "AND installation_id = ? AND generation = ? AND runtime_instance_id = ? "
        "AND component_id = ?",
        (
            reservation["grant_id"],
            operation["workspace_id"],
            installation_id,
            generation,
            runtime_instance_id,
            operation["component_id"],
        ),
    ).fetchone()
    if (
        grant is None
        or grant["state"] != "active"
        or not (grant["not_before"] <= now < grant["expires_at"])
        or grant["version"] != installation["version"]
        or grant["manifest_sha256"] != operation["manifest_sha256"]
        or grant["package_sha256"] != operation["package_sha256"]
    ):
        return False
    runtime = connection.execute(
        "SELECT * FROM workspace_component_runtime_instance WHERE id = ? AND workspace_id = ? "
        "AND installation_id = ? AND generation = ?",
        (runtime_instance_id, operation["workspace_id"], installation_id, generation),
    ).fetchone()
    if (
        runtime is None
        or runtime["state"] != "active"
        or runtime["workload_identity_digest"] != grant["workload_identity_digest"]
    ):
        return False
    lease = connection.execute(
        "SELECT * FROM workspace_component_workload_lease WHERE workspace_id = ? "
        "AND installation_id = ? AND generation = ? AND runtime_instance_id = ?",
        (operation["workspace_id"], installation_id, generation, runtime_instance_id),
    ).fetchone()
    if (
        lease is None
        or lease["state"] != "active"
        or not (lease["not_before"] <= now < lease["expires_at"])
        or lease["workload_identity_digest"] != grant["workload_identity_digest"]
    ):
        return False
    if connection.execute(
        "SELECT 1 FROM workspace_component_revocation WHERE grant_id = ? "
        "OR runtime_instance_id = ? LIMIT 1",
        (grant["id"], runtime_instance_id),
    ).fetchone():
        return False
    if grant["requires_network"]:
        request = _decode_json(str(operation["request_json"]))
        if not isinstance(request, dict) or not isinstance(request.get("logical_service_id"), str):
            return False
        network = connection.execute(
            "SELECT * FROM workspace_component_network_lease WHERE grant_id = ? "
            "AND workload_lease_id = ? AND runtime_instance_id = ? AND installation_id = ? "
            "AND generation = ? AND logical_service_id = ?",
            (
                grant["id"],
                lease["id"],
                runtime_instance_id,
                installation_id,
                generation,
                request["logical_service_id"],
            ),
        ).fetchone()
        if (
            network is None
            or network["state"] != "active"
            or not (network["not_before"] <= now < network["expires_at"])
        ):
            return False
    return True


def settle_component_invocation(  # noqa: C901
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    operation_id: str,
    request_sha256: str,
    state: str,
    result_sha256: str | None,
    evidence_sha256: str | None,
    error_code: str | None,
    actual_bytes_out: int,
    actual_tokens: int,
    actual_wall_time_ms: int,
) -> dict[str, object]:
    if state not in {"succeeded", "failed", "cancelled", "unknown"}:
        raise DesktopApiError(400, "desktop_component_settlement_invalid")
    if evidence_sha256 is None or _SHA256.fullmatch(evidence_sha256) is None:
        raise DesktopApiError(400, "desktop_component_settlement_invalid")
    if state == "succeeded" and result_sha256 is None:
        raise DesktopApiError(400, "desktop_component_settlement_invalid")
    if state != "succeeded" and not error_code:
        raise DesktopApiError(400, "desktop_component_settlement_invalid")
    try:
        connection.execute("BEGIN IMMEDIATE")
        workspace = _live_workspace(connection, workspace_id)
        operation = connection.execute(
            "SELECT * FROM workspace_component_operation WHERE id = ? AND workspace_id = ? "
            "AND kind = 'invoke'",
            (operation_id, workspace_id),
        ).fetchone()
        if operation is None:
            raise DesktopApiError(404, "desktop_component_operation_not_found")
        if operation["request_sha256"] != request_sha256:
            raise DesktopApiError(409, "desktop_component_request_digest_conflict")
        effect = connection.execute(
            "SELECT * FROM workspace_component_effect WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if effect is None:
            raise DesktopApiError(409, "desktop_component_effect_missing")
        reservation = connection.execute(
            "SELECT * FROM workspace_component_budget_reservation WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if reservation is None:
            raise DesktopApiError(409, "desktop_component_budget_reservation_missing")
        if (
            actual_bytes_out > reservation["bytes_out_reserved"]
            or actual_tokens > reservation["tokens_reserved"]
            or actual_wall_time_ms > reservation["wall_time_ms_reserved"]
        ):
            raise DesktopApiError(409, "desktop_component_actual_usage_exceeds_reservation")
        if state == "unknown" and (
            actual_bytes_out != reservation["bytes_out_reserved"]
            or actual_tokens != reservation["tokens_reserved"]
            or actual_wall_time_ms != reservation["wall_time_ms_reserved"]
        ):
            raise DesktopApiError(409, "desktop_component_unknown_usage_must_remain_reserved")
        receipt_payload = {
            "actual_bytes_out": actual_bytes_out,
            "actual_tokens": actual_tokens,
            "actual_wall_time_ms": actual_wall_time_ms,
            "error_code": error_code,
            "evidence_sha256": evidence_sha256,
            "grant_id": reservation["grant_id"],
            "operation_id": operation_id,
            "outcome": state,
            "request_sha256": request_sha256,
            "reserved_bytes_out": reservation["bytes_out_reserved"],
            "reserved_tokens": reservation["tokens_reserved"],
            "reserved_wall_time_ms": reservation["wall_time_ms_reserved"],
            "result_sha256": result_sha256,
            "runtime_instance_id": reservation["runtime_instance_id"],
            "workspace_id": workspace_id,
        }
        receipt_sha256 = digest_json(receipt_payload)
        receipt = connection.execute(
            "SELECT * FROM workspace_component_invocation_receipt WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if receipt is not None and receipt["receipt_sha256"] != receipt_sha256:
            raise DesktopApiError(409, "desktop_component_settlement_payload_drift")
        target_operation = {
            "succeeded": "succeeded",
            "failed": "failed",
            "cancelled": "failed",
            "unknown": "ambiguous",
        }[state]
        target_effect = {
            "succeeded": "committed",
            "failed": "failed",
            "cancelled": "failed",
            "unknown": "unknown",
        }[state]
        if operation["state"] == target_operation and effect["state"] == target_effect:
            if (
                receipt is None
                or operation["result_sha256"] != result_sha256
                or operation["error_code"] != error_code
            ):
                raise DesktopApiError(409, "desktop_component_settlement_payload_drift")
            connection.execute("COMMIT")
            return {
                "operation": _operation_payload(operation),
                "effect": _effect_payload(effect),
                "replayed": True,
            }
        if operation["state"] != "dispatching" or effect["state"] != "pending":
            raise DesktopApiError(409, "desktop_component_invocation_reconciliation_required")
        if not _invocation_authority_is_current(
            connection,
            operation=operation,
            reservation=reservation,
            now=utc_now_text(),
        ):
            _fence_invocation_operation(
                connection,
                operation,
                reason_prefix="invocation_authority_lost",
            )
            connection.execute("COMMIT")
            raise DesktopApiError(409, "desktop_component_invocation_reconciliation_required")
        connection.execute(
            "INSERT INTO workspace_component_invocation_receipt "
            "(operation_id, grant_id, workspace_id, runtime_instance_id, request_sha256, outcome, "
            "reserved_bytes_out, reserved_tokens, reserved_wall_time_ms, actual_bytes_out, "
            "actual_tokens, actual_wall_time_ms, result_sha256, evidence_sha256, error_code, "
            "receipt_sha256, settled_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                operation_id,
                reservation["grant_id"],
                workspace_id,
                reservation["runtime_instance_id"],
                request_sha256,
                state,
                reservation["bytes_out_reserved"],
                reservation["tokens_reserved"],
                reservation["wall_time_ms_reserved"],
                actual_bytes_out,
                actual_tokens,
                actual_wall_time_ms,
                result_sha256,
                evidence_sha256,
                error_code,
                receipt_sha256,
                utc_now_text(),
            ),
        )
        effect = _append_effect_transition(
            connection,
            effect,
            target_effect,
            f"adapter_{state}",
            evidence_sha256,
            result_sha256=result_sha256,
            error_code=error_code,
        )
        operation = _append_operation_transition(
            connection,
            operation,
            target_operation,
            f"adapter_{state}",
            evidence_sha256,
            result_sha256=result_sha256,
            error_code=error_code,
        )
        append_audit_event(
            connection,
            event_id=_id("event"),
            owner_id=str(workspace["owner_id"]),
            workspace_id=workspace_id,
            event_type="workspace_component_invocation_settled",
            payload={
                "component_id": operation["component_id"],
                "operation_id": operation_id,
                "request_sha256": request_sha256,
                "state": state,
            },
        )
        connection.execute("COMMIT")
        return {
            "operation": _operation_payload(operation),
            "effect": _effect_payload(effect),
            "replayed": False,
        }
    except DesktopApiError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopApiError(503, "desktop_component_invocation_settle_failed") from exc


def _insert_emergency_receipt(
    connection: sqlite3.Connection,
    *,
    operation: sqlite3.Row,
    effect: sqlite3.Row,
    outcome: str,
    evidence_sha256: str,
    error_code: str | None,
) -> tuple[str, bool]:
    receipt = {
        "component_id": operation["component_id"],
        "effect_id": effect["id"],
        "evidence_sha256": evidence_sha256,
        "error_code": error_code,
        "installation_id": operation["installation_id"],
        "operation_id": operation["id"],
        "outcome": outcome,
        "request_sha256": operation["request_sha256"],
        "workspace_id": operation["workspace_id"],
    }
    receipt_sha256 = digest_json(receipt)
    existing = connection.execute(
        "SELECT * FROM workspace_component_emergency_receipt WHERE operation_id = ?",
        (operation["id"],),
    ).fetchone()
    if existing is not None:
        if existing["receipt_sha256"] != receipt_sha256:
            raise DesktopApiError(409, "desktop_component_emergency_receipt_payload_drift")
        return receipt_sha256, True
    connection.execute(
        "INSERT INTO workspace_component_emergency_receipt "
        "(operation_id, effect_id, workspace_id, component_id, installation_id, "
        "request_sha256, outcome, evidence_sha256, error_code, receipt_sha256, settled_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            operation["id"],
            effect["id"],
            operation["workspace_id"],
            operation["component_id"],
            operation["installation_id"],
            operation["request_sha256"],
            outcome,
            evidence_sha256,
            error_code,
            receipt_sha256,
            utc_now_text(),
        ),
    )
    return receipt_sha256, False


def emergency_stop_components(  # noqa: C901
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    phase: str,
    idempotency_key: str,
    reason_code: str,
    component_id: str | None = None,
    operation_id: str | None = None,
    effect_id: str | None = None,
    request_sha256: str | None = None,
    outcome: str | None = None,
    evidence_sha256: str | None = None,
    error_code: str | None = None,
) -> dict[str, object]:
    """Fence all components, then settle each exact native cleanup receipt."""

    if phase not in {"prepare", "settle"}:
        raise DesktopApiError(400, "desktop_component_emergency_phase_invalid")
    if not _IDEMPOTENCY.fullmatch(idempotency_key) or _REASON_CODE.fullmatch(reason_code) is None:
        raise DesktopApiError(400, "desktop_component_emergency_identity_invalid")
    settlement = (
        component_id,
        operation_id,
        effect_id,
        request_sha256,
        outcome,
        evidence_sha256,
        error_code,
    )
    if phase == "prepare" and any(item is not None for item in settlement):
        raise DesktopApiError(400, "desktop_component_emergency_prepare_payload_invalid")
    if phase == "settle" and (
        component_id is None
        or operation_id is None
        or effect_id is None
        or request_sha256 is None
        or _SHA256.fullmatch(request_sha256) is None
        or outcome not in {"succeeded", "failed", "unknown"}
        or evidence_sha256 is None
        or _SHA256.fullmatch(evidence_sha256) is None
        or (outcome == "succeeded" and error_code is not None)
        or (outcome in {"failed", "unknown"} and not error_code)
    ):
        raise DesktopApiError(400, "desktop_component_emergency_settle_payload_invalid")
    try:
        connection.execute("BEGIN IMMEDIATE")
        workspace = _live_workspace(connection, workspace_id)
        if phase == "prepare":
            existing = list(
                connection.execute(
                    "SELECT operation.*, effect.id AS effect_id FROM workspace_component_operation "
                    "AS operation JOIN workspace_component_effect AS effect "
                    "ON effect.operation_id = operation.id "
                    "WHERE operation.workspace_id = ? AND operation.kind = 'emergency_stop' "
                    "AND substr(operation.idempotency_key, 1, ?) = ? ORDER BY operation.component_id",
                    (
                        workspace_id,
                        len(idempotency_key) + 1,
                        f"{idempotency_key}:",
                    ),
                )
            )
            if existing:
                tickets: list[dict[str, object]] = []
                for operation in existing:
                    request = _decode_json(str(operation["request_json"]))
                    if (
                        not isinstance(request, dict)
                        or request
                        != {
                            "component_id": operation["component_id"],
                            "reason_code": reason_code,
                            "workspace_id": workspace_id,
                        }
                        or operation["idempotency_key"]
                        != f"{idempotency_key}:{operation['component_id']}"
                    ):
                        raise DesktopApiError(
                            409, "desktop_component_emergency_idempotency_payload_drift"
                        )
                    tickets.append(
                        {
                            "component_id": operation["component_id"],
                            "effect_id": operation["effect_id"],
                            "operation_id": operation["id"],
                            "request_sha256": operation["request_sha256"],
                        }
                    )
                connection.execute("COMMIT")
                return {
                    "workspace_id": workspace_id,
                    "tickets": tickets,
                    "fenced_component_ids": [ticket["component_id"] for ticket in tickets],
                    "replayed": True,
                }

            rows = list(
                connection.execute(
                    "SELECT * FROM workspace_component_installation WHERE workspace_id = ? "
                    "AND state IN ('installed', 'bound', 'active', 'disabled', 'blocked') "
                    "ORDER BY component_id",
                    (workspace_id,),
                )
            )
            tickets = []
            fenced: list[str] = []
            for installation in rows:
                request = {
                    "component_id": installation["component_id"],
                    "reason_code": reason_code,
                    "workspace_id": workspace_id,
                }
                operation, replayed = _create_operation(
                    connection,
                    workspace=workspace,
                    component_id=str(installation["component_id"]),
                    installation=installation,
                    kind="emergency_stop",
                    action=None,
                    expected_revision=int(installation["revision"]),
                    binding_generation=int(installation["binding_generation"]),
                    runtime_instance_id=installation["current_runtime_instance_id"],
                    manifest_sha256=str(installation["manifest_sha256"]),
                    package_sha256=str(installation["package_sha256"]),
                    idempotency_key=f"{idempotency_key}:{installation['component_id']}",
                    request=request,
                )
                if replayed:
                    raise DesktopApiError(409, "desktop_component_emergency_partial_replay")
                operation = _append_operation_transition(
                    connection, operation, "authorized", "emergency_authority_verified"
                )
                operation = _append_operation_transition(
                    connection, operation, "dispatching", "emergency_cleanup_required"
                )
                effect = _create_effect(
                    connection, operation, "emergency_stop", str(installation["component_id"])
                )
                _fence_pending_invocations(
                    connection,
                    installation_id=str(installation["id"]),
                    reason_prefix="emergency_stop",
                )
                _revoke_authority(
                    connection,
                    workspace=workspace,
                    installation=installation,
                    reason_code=reason_code,
                    actor_type="owner",
                )
                now = utc_now_text()
                connection.execute(
                    "UPDATE workspace_component_binding_generation SET state = 'revoked', "
                    "updated_at = ? WHERE installation_id = ? "
                    "AND state IN ('installed','bound','active')",
                    (now, installation["id"]),
                )
                next_revision = int(installation["revision"]) + 1
                _audit_state(
                    connection,
                    workspace,
                    installation,
                    revision=next_revision,
                    state="blocked",
                    operation_id=str(operation["id"]),
                )
                connection.execute(
                    "UPDATE workspace_component_installation SET state = 'blocked', revision = ?, "
                    "current_runtime_instance_id = NULL, updated_at = ? WHERE id = ?",
                    (next_revision, now, installation["id"]),
                )
                tickets.append(
                    {
                        "component_id": installation["component_id"],
                        "effect_id": effect["id"],
                        "operation_id": operation["id"],
                        "request_sha256": operation["request_sha256"],
                    }
                )
                fenced.append(str(installation["component_id"]))
            append_audit_event(
                connection,
                event_id=_id("event"),
                owner_id=str(workspace["owner_id"]),
                workspace_id=workspace_id,
                event_type="workspace_component_emergency_fenced",
                payload={"component_ids": fenced, "reason_code": reason_code},
            )
            connection.execute("COMMIT")
            return {
                "workspace_id": workspace_id,
                "tickets": tickets,
                "fenced_component_ids": fenced,
                "replayed": False,
            }

        assert component_id is not None
        assert operation_id is not None
        assert effect_id is not None
        assert request_sha256 is not None
        assert outcome is not None
        assert evidence_sha256 is not None
        operation = connection.execute(
            "SELECT * FROM workspace_component_operation WHERE id = ? AND workspace_id = ? "
            "AND component_id = ? AND kind = 'emergency_stop'",
            (operation_id, workspace_id, component_id),
        ).fetchone()
        effect = connection.execute(
            "SELECT * FROM workspace_component_effect WHERE id = ? AND operation_id = ? "
            "AND workspace_id = ? AND effect_kind = 'emergency_stop'",
            (effect_id, operation_id, workspace_id),
        ).fetchone()
        if operation is None or effect is None:
            raise DesktopApiError(404, "desktop_component_emergency_operation_not_found")
        request = _decode_json(str(operation["request_json"]))
        if (
            operation["request_sha256"] != request_sha256
            or effect["request_sha256"] != request_sha256
            or operation["idempotency_key"] != f"{idempotency_key}:{component_id}"
            or not isinstance(request, dict)
            or request
            != {
                "component_id": component_id,
                "reason_code": reason_code,
                "workspace_id": workspace_id,
            }
        ):
            raise DesktopApiError(409, "desktop_component_emergency_identity_drift")
        _, receipt_replayed = _insert_emergency_receipt(
            connection,
            operation=operation,
            effect=effect,
            outcome=outcome,
            evidence_sha256=evidence_sha256,
            error_code=error_code,
        )
        if receipt_replayed:
            connection.execute("COMMIT")
            return {
                "workspace_id": workspace_id,
                "component_id": component_id,
                "operation": _operation_payload(operation),
                "effect": _effect_payload(effect),
                "replayed": True,
            }
        if operation["state"] != "dispatching" or effect["state"] != "pending":
            raise DesktopApiError(409, "desktop_component_emergency_reconciliation_required")
        if outcome == "succeeded":
            effect = _append_effect_transition(
                connection,
                effect,
                "committed",
                "emergency_native_cleanup_succeeded",
                evidence_sha256,
                result_sha256=evidence_sha256,
            )
            operation = _append_operation_transition(
                connection,
                operation,
                "succeeded",
                "emergency_native_cleanup_succeeded",
                evidence_sha256,
                result_sha256=evidence_sha256,
            )
            installation = _installation(
                connection,
                workspace_id=workspace_id,
                component_id=component_id,
            )
            assert installation is not None
            revision = int(installation["revision"]) + 1
            _audit_state(
                connection,
                workspace,
                installation,
                revision=revision,
                state="revoked",
                operation_id=operation_id,
            )
            connection.execute(
                "UPDATE workspace_component_installation SET state = 'revoked', revision = ?, "
                "updated_at = ? WHERE id = ?",
                (revision, utc_now_text(), installation["id"]),
            )
        elif outcome == "failed":
            effect = _append_effect_transition(
                connection,
                effect,
                "failed",
                "emergency_native_cleanup_failed",
                evidence_sha256,
                error_code=error_code,
            )
            operation = _append_operation_transition(
                connection,
                operation,
                "failed",
                "emergency_native_cleanup_failed",
                evidence_sha256,
                error_code=error_code,
            )
        else:
            effect = _append_effect_transition(
                connection,
                effect,
                "unknown",
                "emergency_native_cleanup_unknown",
                evidence_sha256,
                error_code=error_code,
            )
            effect = _append_effect_transition(
                connection,
                effect,
                "reconciliation_required",
                "emergency_native_cleanup_reconciliation_required",
                evidence_sha256,
                error_code=error_code,
            )
            operation = _append_operation_transition(
                connection,
                operation,
                "ambiguous",
                "emergency_native_cleanup_unknown",
                evidence_sha256,
                error_code=error_code,
            )
            operation = _append_operation_transition(
                connection,
                operation,
                "reconciliation_required",
                "emergency_native_cleanup_reconciliation_required",
                evidence_sha256,
                error_code=error_code,
            )
        append_audit_event(
            connection,
            event_id=_id("event"),
            owner_id=str(workspace["owner_id"]),
            workspace_id=workspace_id,
            event_type="workspace_component_emergency_cleanup_settled",
            payload={
                "component_id": component_id,
                "operation_id": operation_id,
                "outcome": outcome,
                "request_sha256": request_sha256,
            },
        )
        connection.execute("COMMIT")
        return {
            "workspace_id": workspace_id,
            "component_id": component_id,
            "operation": _operation_payload(operation),
            "effect": _effect_payload(effect),
            "replayed": False,
        }
    except DesktopApiError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopApiError(503, "desktop_component_emergency_stop_failed") from exc


def reconcile_component_effect(  # noqa: C901
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    operation_id: str,
    effect_id: str,
    request_sha256: str,
    outcome: str,
    evidence_sha256: str,
) -> dict[str, object]:
    if outcome not in {"succeeded", "failed"} or not _SHA256.fullmatch(evidence_sha256):
        raise DesktopApiError(400, "desktop_component_reconciliation_invalid")
    try:
        connection.execute("BEGIN IMMEDIATE")
        workspace = _live_workspace(connection, workspace_id)
        operation = connection.execute(
            "SELECT * FROM workspace_component_operation WHERE id = ? AND workspace_id = ?",
            (operation_id, workspace_id),
        ).fetchone()
        effect = connection.execute(
            "SELECT * FROM workspace_component_effect WHERE id = ? AND operation_id = ? "
            "AND workspace_id = ?",
            (effect_id, operation_id, workspace_id),
        ).fetchone()
        if operation is None or effect is None:
            raise DesktopApiError(404, "desktop_component_reconciliation_target_not_found")
        if (
            operation["request_sha256"] != request_sha256
            or effect["request_sha256"] != request_sha256
        ):
            raise DesktopApiError(409, "desktop_component_request_digest_conflict")
        if effect["effect_kind"] == "lifecycle" and outcome == "succeeded":
            raise DesktopApiError(409, "desktop_component_lifecycle_recovery_required")
        existing = connection.execute(
            "SELECT * FROM workspace_component_reconciliation WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if existing is not None:
            if existing["outcome"] != outcome or existing["evidence_sha256"] != evidence_sha256:
                raise DesktopApiError(409, "desktop_component_reconciliation_payload_drift")
            connection.execute("COMMIT")
            return {
                "operation": _operation_payload(operation),
                "effect": _effect_payload(effect),
                "reconciliation_id": existing["id"],
                "replayed": True,
            }
        if operation["state"] == "ambiguous":
            operation = _append_operation_transition(
                connection,
                operation,
                "reconciliation_required",
                "owner_reconciliation_required",
            )
        if effect["state"] == "unknown":
            effect = _append_effect_transition(
                connection,
                effect,
                "reconciliation_required",
                "owner_reconciliation_required",
            )
        if (
            operation["state"] != "reconciliation_required"
            or effect["state"] != "reconciliation_required"
        ):
            raise DesktopApiError(409, "desktop_component_reconciliation_not_required")
        reconciliation_id = _id("reconcile")
        now = utc_now_text()
        connection.execute(
            "INSERT INTO workspace_component_reconciliation "
            "(id, operation_id, effect_id, workspace_id, request_sha256, outcome, "
            "evidence_sha256, decided_by, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'owner', ?)",
            (
                reconciliation_id,
                operation_id,
                effect_id,
                workspace_id,
                request_sha256,
                outcome,
                evidence_sha256,
                now,
            ),
        )
        effect = _append_effect_transition(
            connection,
            effect,
            "reconciled_committed" if outcome == "succeeded" else "reconciled_failed",
            f"owner_reconciled_{outcome}",
            evidence_sha256,
            result_sha256=evidence_sha256 if outcome == "succeeded" else None,
            error_code=None if outcome == "succeeded" else "owner_reconciled_failed",
        )
        operation = _append_operation_transition(
            connection,
            operation,
            "reconciled_succeeded" if outcome == "succeeded" else "reconciled_failed",
            f"owner_reconciled_{outcome}",
            evidence_sha256,
            result_sha256=evidence_sha256 if outcome == "succeeded" else None,
            error_code=None if outcome == "succeeded" else "owner_reconciled_failed",
        )
        if effect["effect_kind"] == "emergency_stop" and outcome == "succeeded":
            installation = _installation(
                connection,
                workspace_id=workspace_id,
                component_id=str(operation["component_id"]),
            )
            if installation is None or installation["state"] != "blocked":
                raise DesktopApiError(409, "desktop_component_emergency_reconciliation_invalid")
            revision = int(installation["revision"]) + 1
            _audit_state(
                connection,
                workspace,
                installation,
                revision=revision,
                state="revoked",
                operation_id=operation_id,
            )
            connection.execute(
                "UPDATE workspace_component_installation SET state = 'revoked', revision = ?, "
                "updated_at = ? WHERE id = ?",
                (revision, utc_now_text(), installation["id"]),
            )
        append_audit_event(
            connection,
            event_id=_id("event"),
            owner_id=str(workspace["owner_id"]),
            workspace_id=workspace_id,
            event_type="workspace_component_reconciled",
            payload={
                "effect_id": effect_id,
                "operation_id": operation_id,
                "outcome": outcome,
                "request_sha256": request_sha256,
            },
        )
        connection.execute("COMMIT")
        return {
            "operation": _operation_payload(operation),
            "effect": _effect_payload(effect),
            "reconciliation_id": reconciliation_id,
            "replayed": False,
        }
    except DesktopApiError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopApiError(503, "desktop_component_reconciliation_failed") from exc


def settle_component_recovery(  # noqa: C901
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    recovery_id: str,
    operation_id: str,
    outcome: str,
    evidence_sha256: str,
    health_state: str | None,
    runtime_instance_id: str,
    workload_identity_digest: str,
    error_code: str | None,
) -> dict[str, object]:
    if (
        outcome not in {"succeeded", "failed", "unknown"}
        or _SHA256.fullmatch(evidence_sha256) is None
        or _SHA256.fullmatch(workload_identity_digest) is None
        or (outcome == "succeeded" and (health_state != "healthy" or error_code is not None))
        or (outcome == "failed" and not error_code)
    ):
        raise DesktopApiError(400, "desktop_component_recovery_settlement_invalid")
    try:
        connection.execute("BEGIN IMMEDIATE")
        workspace = _live_workspace(connection, workspace_id)
        recovery = connection.execute(
            "SELECT recovery.*, dispatch.effect_id, dispatch.adapter_id, "
            "dispatch.reserved_runtime_instance_id, dispatch.workload_identity_digest, "
            "operation.state AS operation_state, effect.state AS effect_state "
            "FROM workspace_component_recovery_request AS recovery "
            "JOIN workspace_component_lifecycle_dispatch AS dispatch "
            "ON dispatch.operation_id = recovery.operation_id "
            "JOIN workspace_component_operation AS operation ON operation.id = recovery.operation_id "
            "JOIN workspace_component_effect AS effect ON effect.id = dispatch.effect_id "
            "WHERE recovery.id = ? AND recovery.workspace_id = ? AND recovery.operation_id = ?",
            (recovery_id, workspace_id, operation_id),
        ).fetchone()
        if recovery is None:
            raise DesktopApiError(404, "desktop_component_recovery_not_found")
        if (
            recovery["reserved_runtime_instance_id"] != runtime_instance_id
            or recovery["workload_identity_digest"] != workload_identity_digest
        ):
            raise DesktopApiError(409, "desktop_component_recovery_identity_drift")
        operation = connection.execute(
            "SELECT * FROM workspace_component_operation WHERE id = ?", (operation_id,)
        ).fetchone()
        effect = connection.execute(
            "SELECT * FROM workspace_component_effect WHERE id = ?", (recovery["effect_id"],)
        ).fetchone()
        dispatch = connection.execute(
            "SELECT * FROM workspace_component_lifecycle_dispatch WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        assert operation is not None
        assert effect is not None
        assert dispatch is not None
        result_sha256 = evidence_sha256 if outcome == "succeeded" else None
        existing_receipt = connection.execute(
            "SELECT 1 FROM workspace_component_lifecycle_receipt WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if existing_receipt is not None:
            _, replayed = _insert_lifecycle_receipt(
                connection,
                dispatch=dispatch,
                outcome=outcome,
                health_state=health_state,
                result_sha256=result_sha256,
                evidence_sha256=evidence_sha256,
                error_code=error_code,
            )
            assert replayed
            connection.execute("COMMIT")
            return {
                "recovery_id": recovery_id,
                "operation": _operation_payload(operation),
                "effect": _effect_payload(effect),
                "replayed": True,
            }
        installation = _installation(
            connection,
            workspace_id=workspace_id,
            component_id=str(recovery["component_id"]),
        )
        assert installation is not None
        if installation["state"] != "blocked":
            raise DesktopApiError(409, "desktop_component_recovery_not_pending")
        previous_grant: sqlite3.Row | None = None
        inherited_usage: dict[str, int] | None = None
        if outcome == "succeeded":
            now = utc_now_text()
            previous_grant = connection.execute(
                "SELECT grant.*, usage.calls AS inherited_calls, "
                "usage.bytes_in AS inherited_bytes_in, "
                "usage.bytes_out_reserved AS inherited_bytes_out_reserved, "
                "usage.tokens_reserved AS inherited_tokens_reserved, "
                "usage.wall_time_ms_reserved AS inherited_wall_time_ms_reserved, "
                "usage.cost_units AS inherited_cost_units, usage.retries AS inherited_retries, "
                "usage.row_version AS inherited_row_version "
                "FROM workspace_component_grant AS grant "
                "JOIN workspace_component_grant_usage AS usage ON usage.grant_id = grant.id "
                "WHERE grant.runtime_instance_id = ? AND grant.workspace_id = ? "
                "AND grant.installation_id = ? AND grant.generation = ? "
                "AND grant.component_id = ? AND grant.version = ? "
                "AND grant.manifest_sha256 = ? AND grant.package_sha256 = ?",
                (
                    recovery["previous_runtime_instance_id"],
                    workspace_id,
                    installation["id"],
                    recovery["binding_generation"],
                    installation["component_id"],
                    installation["version"],
                    recovery["manifest_sha256"],
                    recovery["package_sha256"],
                ),
            ).fetchone()
            if previous_grant is None:
                raise DesktopApiError(409, "desktop_component_recovery_grant_missing")
            if not (previous_grant["not_before"] <= now < previous_grant["expires_at"]):
                raise DesktopApiError(409, "desktop_component_recovery_grant_expired")
            if (
                previous_grant["state"] != "revoked"
                or connection.execute(
                    "SELECT 1 FROM workspace_component_revocation WHERE grant_id = ? "
                    "AND workspace_id = ? AND installation_id = ? LIMIT 1",
                    (previous_grant["id"], workspace_id, installation["id"]),
                ).fetchone()
                is None
            ):
                raise DesktopApiError(409, "desktop_component_recovery_authority_invalid")
            inherited_usage = {
                "calls": int(previous_grant["inherited_calls"]),
                "bytes_in": int(previous_grant["inherited_bytes_in"]),
                "bytes_out_reserved": int(previous_grant["inherited_bytes_out_reserved"]),
                "tokens_reserved": int(previous_grant["inherited_tokens_reserved"]),
                "wall_time_ms_reserved": int(previous_grant["inherited_wall_time_ms_reserved"]),
                "cost_units": int(previous_grant["inherited_cost_units"]),
                "retries": int(previous_grant["inherited_retries"]),
                "row_version": int(previous_grant["inherited_row_version"]),
            }
            cumulative_limits = {
                "calls": int(previous_grant["max_calls"]),
                "bytes_in": int(previous_grant["max_bytes_in"]),
                "bytes_out_reserved": int(previous_grant["max_bytes_out"]),
                "tokens_reserved": int(previous_grant["max_tokens"]),
                "wall_time_ms_reserved": int(previous_grant["max_wall_time_ms"]),
                "cost_units": int(previous_grant["max_cost_units"]),
                "retries": int(previous_grant["max_retries"]),
            }
            if any(
                used > cumulative_limits[name]
                or (cumulative_limits[name] > 0 and used == cumulative_limits[name])
                for name, used in inherited_usage.items()
                if name != "row_version"
            ):
                raise DesktopApiError(409, "desktop_component_recovery_budget_exhausted")
            connection.execute(
                "INSERT INTO workspace_component_runtime_instance "
                "(id, installation_id, workspace_id, generation, operation_generation, "
                "workload_identity_digest, state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                (
                    runtime_instance_id,
                    installation["id"],
                    workspace_id,
                    installation["binding_generation"],
                    operation["operation_generation"],
                    workload_identity_digest,
                    now,
                    now,
                ),
            )
        _insert_lifecycle_receipt(
            connection,
            dispatch=dispatch,
            outcome=outcome,
            health_state=health_state,
            result_sha256=result_sha256,
            evidence_sha256=evidence_sha256,
            error_code=error_code,
        )
        if outcome != "succeeded":
            effect = _append_effect_transition(
                connection,
                effect,
                "unknown" if outcome == "unknown" else "failed",
                f"recovery_{outcome}",
                evidence_sha256,
                error_code=error_code,
            )
            operation = _append_operation_transition(
                connection,
                operation,
                "ambiguous" if outcome == "unknown" else "failed",
                f"recovery_{outcome}",
                evidence_sha256,
                error_code=error_code,
            )
            connection.execute("COMMIT")
            return {
                "recovery_id": recovery_id,
                "operation": _operation_payload(operation),
                "effect": _effect_payload(effect),
                "replayed": False,
            }
        assert previous_grant is not None
        assert inherited_usage is not None
        now = utc_now_text()
        not_before = str(previous_grant["not_before"])
        expires_at = str(previous_grant["expires_at"])
        grant_id = _id("grant")
        connection.execute(
            "INSERT INTO workspace_component_grant "
            "(id, owner_id, workspace_id, installation_id, generation, runtime_instance_id, "
            "component_id, version, manifest_sha256, package_sha256, request_sha256, "
            "workload_identity_digest, actions_json, scope_json, requires_network, state, "
            "not_before, expires_at, max_calls, max_bytes_in, max_bytes_out, max_tokens, "
            "max_wall_time_ms, max_cost_units, max_retries, max_concurrency, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                grant_id,
                workspace["owner_id"],
                workspace_id,
                installation["id"],
                installation["binding_generation"],
                runtime_instance_id,
                installation["component_id"],
                installation["version"],
                installation["manifest_sha256"],
                installation["package_sha256"],
                installation["request_sha256"],
                workload_identity_digest,
                previous_grant["actions_json"],
                previous_grant["scope_json"],
                previous_grant["requires_network"],
                not_before,
                expires_at,
                previous_grant["max_calls"],
                previous_grant["max_bytes_in"],
                previous_grant["max_bytes_out"],
                previous_grant["max_tokens"],
                previous_grant["max_wall_time_ms"],
                previous_grant["max_cost_units"],
                previous_grant["max_retries"],
                previous_grant["max_concurrency"],
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO workspace_component_grant_usage "
            "(grant_id, calls, bytes_in, bytes_out_reserved, tokens_reserved, wall_time_ms_reserved, "
            "cost_units, retries, row_version, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                grant_id,
                inherited_usage["calls"],
                inherited_usage["bytes_in"],
                inherited_usage["bytes_out_reserved"],
                inherited_usage["tokens_reserved"],
                inherited_usage["wall_time_ms_reserved"],
                inherited_usage["cost_units"],
                inherited_usage["retries"],
                inherited_usage["row_version"],
                now,
            ),
        )
        workload_lease_id = _id("workloadlease")
        connection.execute(
            "INSERT INTO workspace_component_workload_lease "
            "(id, workspace_id, installation_id, generation, runtime_instance_id, "
            "workload_identity_digest, fencing_token, state, not_before, expires_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)",
            (
                workload_lease_id,
                workspace_id,
                installation["id"],
                installation["binding_generation"],
                runtime_instance_id,
                workload_identity_digest,
                installation["binding_generation"],
                not_before,
                expires_at,
                now,
                now,
            ),
        )
        old_services = connection.execute(
            "SELECT DISTINCT logical_service_id FROM workspace_component_network_lease "
            "WHERE runtime_instance_id = ?",
            (recovery["previous_runtime_instance_id"],),
        ).fetchall()
        for service in old_services:
            connection.execute(
                "INSERT INTO workspace_component_network_lease "
                "(id, workspace_id, grant_id, workload_lease_id, runtime_instance_id, "
                "installation_id, generation, logical_service_id, fencing_token, state, "
                "not_before, expires_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'active', ?, ?, ?, ?)",
                (
                    _id("networklease"),
                    workspace_id,
                    grant_id,
                    workload_lease_id,
                    runtime_instance_id,
                    installation["id"],
                    installation["binding_generation"],
                    service["logical_service_id"],
                    not_before,
                    expires_at,
                    now,
                    now,
                ),
            )
        append_audit_event(
            connection,
            event_id=_id("event"),
            owner_id=str(workspace["owner_id"]),
            workspace_id=workspace_id,
            event_type="workspace_component_recovery_authority_reissued",
            payload={
                "ancestor_grant_id": previous_grant["id"],
                "ancestor_runtime_instance_id": recovery["previous_runtime_instance_id"],
                "ancestor_usage": inherited_usage,
                "ancestor_usage_sha256": digest_json(inherited_usage),
                "expires_at": expires_at,
                "grant_id": grant_id,
                "installation_id": installation["id"],
                "operation_id": operation_id,
                "recovery_id": recovery_id,
                "runtime_instance_id": runtime_instance_id,
            },
        )
        effect = _append_effect_transition(
            connection,
            effect,
            "committed",
            "recovery_native_committed",
            evidence_sha256,
            result_sha256=result_sha256,
        )
        operation = _append_operation_transition(
            connection,
            operation,
            "succeeded",
            "recovery_succeeded",
            evidence_sha256,
            result_sha256=result_sha256,
        )
        connection.execute(
            "INSERT INTO workspace_component_health "
            "(id, workspace_id, installation_id, component_id, generation, runtime_instance_id, "
            "operation_id, state, manifest_sha256, package_sha256, workload_identity_digest, "
            "evidence_sha256, observed_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'healthy', ?, ?, ?, ?, ?)",
            (
                _id("health"),
                workspace_id,
                installation["id"],
                installation["component_id"],
                installation["binding_generation"],
                runtime_instance_id,
                operation_id,
                installation["manifest_sha256"],
                installation["package_sha256"],
                workload_identity_digest,
                evidence_sha256,
                now,
            ),
        )
        revision = int(installation["revision"]) + 1
        _audit_state(
            connection,
            workspace,
            installation,
            revision=revision,
            state="active",
            operation_id=operation_id,
        )
        connection.execute(
            "UPDATE workspace_component_installation SET state = 'active', revision = ?, "
            "current_runtime_instance_id = ?, updated_at = ? WHERE id = ?",
            (revision, runtime_instance_id, now, installation["id"]),
        )
        connection.execute("COMMIT")
        return {
            "recovery_id": recovery_id,
            "operation": _operation_payload(operation),
            "effect": _effect_payload(effect),
            "replayed": False,
        }
    except DesktopApiError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopApiError(503, "desktop_component_recovery_settle_failed") from exc


def recover_component_kernel(connection: sqlite3.Connection) -> None:
    """Fail closed after a host restart without replaying external effects."""

    try:
        connection.execute("BEGIN IMMEDIATE")
        unresolved_destructive = list(
            connection.execute(
                "SELECT * FROM workspace_component_operation WHERE installation_id IS NOT NULL "
                "AND state IN ('accepted', 'authorized', 'dispatching', 'ambiguous', "
                "'reconciliation_required') AND kind IN ('disable', 'upgrade', 'rollback', "
                "'revoke', 'uninstall', 'emergency_stop') ORDER BY created_at, id"
            )
        )
        destructive_by_installation = {
            str(operation["installation_id"]): operation for operation in unresolved_destructive
        }
        for operation in list(
            connection.execute(
                "SELECT * FROM workspace_component_operation WHERE state = 'dispatching'"
            )
        ):
            effect = connection.execute(
                "SELECT * FROM workspace_component_effect WHERE operation_id = ?",
                (operation["id"],),
            ).fetchone()
            if effect is not None and effect["state"] == "pending":
                effect = _append_effect_transition(
                    connection, effect, "unknown", "startup_outcome_unknown"
                )
                _append_effect_transition(
                    connection,
                    effect,
                    "reconciliation_required",
                    "startup_reconciliation_required",
                )
            operation = _append_operation_transition(
                connection, operation, "ambiguous", "startup_outcome_ambiguous"
            )
            _append_operation_transition(
                connection,
                operation,
                "reconciliation_required",
                "startup_reconciliation_required",
            )
        for operation in unresolved_destructive:
            if operation["state"] in {"accepted", "authorized"}:
                _append_operation_transition(
                    connection,
                    operation,
                    "failed",
                    "startup_aborted_before_dispatch",
                    error_code="startup_aborted_before_dispatch",
                )
        for installation in list(
            connection.execute(
                "SELECT * FROM workspace_component_installation WHERE state = 'active'"
            )
        ):
            workspace = connection.execute(
                "SELECT * FROM workspace WHERE id = ? AND state = 'active'",
                (installation["workspace_id"],),
            ).fetchone()
            if workspace is None:
                continue
            destructive_operation = destructive_by_installation.get(str(installation["id"]))
            if destructive_operation is not None:
                _revoke_authority(
                    connection,
                    workspace=workspace,
                    installation=installation,
                    reason_code="startup_destructive_operation_unresolved",
                    actor_type="system",
                )
                now = utc_now_text()
                connection.execute(
                    "UPDATE workspace_component_binding_generation SET state = 'revoked', "
                    "updated_at = ? WHERE installation_id = ? "
                    "AND state IN ('installed','bound','active')",
                    (now, installation["id"]),
                )
                revision = int(installation["revision"]) + 1
                _audit_state(
                    connection,
                    workspace,
                    installation,
                    revision=revision,
                    state="blocked",
                    operation_id=str(destructive_operation["id"]),
                )
                connection.execute(
                    "UPDATE workspace_component_installation SET state = 'blocked', revision = ?, "
                    "current_runtime_instance_id = NULL, updated_at = ? WHERE id = ?",
                    (revision, now, installation["id"]),
                )
                append_audit_event(
                    connection,
                    event_id=_id("event"),
                    owner_id=str(workspace["owner_id"]),
                    workspace_id=str(workspace["id"]),
                    event_type="workspace_component_destructive_recovery_blocked",
                    payload={
                        "component_id": installation["component_id"],
                        "installation_id": installation["id"],
                        "operation_id": destructive_operation["id"],
                        "reason_code": "startup_destructive_operation_unresolved",
                    },
                )
                continue
            _revoke_authority(
                connection,
                workspace=workspace,
                installation=installation,
                reason_code="startup_identity_not_reused",
                actor_type="system",
            )
            now = utc_now_text()
            request = {
                "binding_generation": installation["binding_generation"],
                "component_id": installation["component_id"],
                "installation_id": installation["id"],
                "manifest_sha256": installation["manifest_sha256"],
                "package_sha256": installation["package_sha256"],
                "reason_code": "startup_native_revalidation_required",
                "workspace_id": installation["workspace_id"],
            }
            operation, _ = _create_operation(
                connection,
                workspace=workspace,
                component_id=str(installation["component_id"]),
                installation=installation,
                kind="recovery",
                action=None,
                expected_revision=int(installation["revision"]),
                binding_generation=int(installation["binding_generation"]),
                runtime_instance_id=str(installation["current_runtime_instance_id"]),
                manifest_sha256=str(installation["manifest_sha256"]),
                package_sha256=str(installation["package_sha256"]),
                idempotency_key=(
                    f"startup-recovery:{installation['id']}:{installation['binding_generation']}"
                ),
                request=request,
            )
            operation = _append_operation_transition(
                connection, operation, "authorized", "committed_generation_revalidated"
            )
            operation = _append_operation_transition(
                connection, operation, "dispatching", "startup_recovery_dispatch_required"
            )
            effect = _create_effect(
                connection, operation, "lifecycle", str(installation["component_id"])
            )
            catalog = _catalog_row(
                connection, str(installation["component_id"]), str(installation["version"])
            )
            runtime_id = _id("runtime")
            workload_digest = digest_json(
                {
                    "binding_generation": installation["binding_generation"],
                    "operation_id": operation["id"],
                    "runtime_instance_id": runtime_id,
                    "workspace_id": installation["workspace_id"],
                }
            )
            connection.execute(
                "INSERT INTO workspace_component_lifecycle_dispatch "
                "(operation_id, effect_id, workspace_id, component_id, installation_id, "
                "binding_generation, action, adapter_id, reserved_runtime_instance_id, "
                "workload_identity_digest, request_sha256, manifest_sha256, package_sha256, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, 'recovery', ?, ?, ?, ?, ?, ?, ?)",
                (
                    operation["id"],
                    effect["id"],
                    installation["workspace_id"],
                    installation["component_id"],
                    installation["id"],
                    installation["binding_generation"],
                    catalog["adapter_id"],
                    runtime_id,
                    workload_digest,
                    operation["request_sha256"],
                    installation["manifest_sha256"],
                    installation["package_sha256"],
                    now,
                ),
            )
            revision = int(installation["revision"]) + 1
            _audit_state(
                connection,
                workspace,
                installation,
                revision=revision,
                state="blocked",
                operation_id=str(operation["id"]),
            )
            connection.execute(
                "UPDATE workspace_component_installation SET state = 'blocked', revision = ?, "
                "current_runtime_instance_id = NULL, updated_at = ? WHERE id = ?",
                (revision, now, installation["id"]),
            )
            connection.execute(
                "INSERT INTO workspace_component_recovery_request "
                "(id, workspace_id, component_id, installation_id, binding_generation, "
                "previous_runtime_instance_id, operation_id, request_sha256, manifest_sha256, "
                "package_sha256, state, reason_code, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_native_revalidation', "
                "'startup_native_revalidation_required', ?)",
                (
                    _id("recovery"),
                    installation["workspace_id"],
                    installation["component_id"],
                    installation["id"],
                    installation["binding_generation"],
                    installation["current_runtime_instance_id"],
                    operation["id"],
                    operation["request_sha256"],
                    installation["manifest_sha256"],
                    installation["package_sha256"],
                    now,
                ),
            )
            append_audit_event(
                connection,
                event_id=_id("event"),
                owner_id=str(workspace["owner_id"]),
                workspace_id=str(workspace["id"]),
                event_type="workspace_component_recovery_blocked",
                payload={
                    "component_id": installation["component_id"],
                    "installation_id": installation["id"],
                    "operation_id": operation["id"],
                    "reason_code": "startup_native_revalidation_required",
                },
            )
        connection.execute("COMMIT")
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


__all__ = [
    "apply_component_action_v2",
    "attest_component_package",
    "begin_component_invocation",
    "create_assistant_component_proposal",
    "create_component_proposal",
    "decide_component_proposal",
    "emergency_stop_components",
    "get_component_snapshot",
    "reconcile_component_effect",
    "recover_component_kernel",
    "register_owner_reviewed_component",
    "settle_component_invocation",
    "settle_component_recovery",
]
