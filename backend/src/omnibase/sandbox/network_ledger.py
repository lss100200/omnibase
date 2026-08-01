"""POSIX durable budget ledger for the Workspace Network Broker.

This store contains only operation bindings, aggregate lease budget charges,
one-way outcome state and a bounded receipt.  It is deliberately independent
from the OmniBase business database.  A pending or unknown operation is never
automatically replayed after process restart.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from datetime import datetime
from pathlib import Path

from omnibase.sandbox.broker import (
    BrokerConnectionReceipt,
    NetworkBudgetOperationState,
    NetworkBudgetReservation,
    network_budget_binding_digest,
)
from omnibase.sandbox.contracts import SandboxConflict, SandboxRejected, SandboxUnavailable
from omnibase.sandbox.network import VerifiedSandboxNetworkAuthorization


def _effective_uid() -> int:
    get_effective_uid = getattr(os, "geteuid", None)
    return int(get_effective_uid()) if get_effective_uid is not None else 0


def _receipt_payload(receipt: BrokerConnectionReceipt) -> str:
    return json.dumps(
        {
            "accepted_at": receipt.accepted_at.isoformat(),
            "bytes_in": receipt.bytes_in,
            "bytes_out": receipt.bytes_out,
            "connections": receipt.connections,
            "destination_resolution_digest": receipt.destination_resolution_digest,
            "namespace_evidence_digest": receipt.namespace_evidence_digest,
            "operation_id": str(receipt.operation_id),
            "plan_digest": receipt.plan_digest,
            "request_binding_digest": receipt.request_binding_digest,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _receipt_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SandboxRejected("sandbox_network_receipt_corrupt")
    return value


def _receipt_string(value: object) -> str:
    if not isinstance(value, str):
        raise SandboxRejected("sandbox_network_receipt_corrupt")
    return value


def _receipt_from_payload(payload: str) -> BrokerConnectionReceipt:
    try:
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise TypeError
        expected_keys = {
            "accepted_at",
            "bytes_in",
            "bytes_out",
            "connections",
            "destination_resolution_digest",
            "namespace_evidence_digest",
            "operation_id",
            "plan_digest",
            "request_binding_digest",
        }
        if set(value) != expected_keys:
            raise ValueError
        from uuid import UUID

        return BrokerConnectionReceipt(
            operation_id=UUID(_receipt_string(value["operation_id"])),
            request_binding_digest=_receipt_string(value["request_binding_digest"]),
            plan_digest=_receipt_string(value["plan_digest"]),
            namespace_evidence_digest=_receipt_string(value["namespace_evidence_digest"]),
            destination_resolution_digest=_receipt_string(value["destination_resolution_digest"]),
            connections=_receipt_int(value["connections"]),
            bytes_in=_receipt_int(value["bytes_in"]),
            bytes_out=_receipt_int(value["bytes_out"]),
            accepted_at=datetime.fromisoformat(_receipt_string(value["accepted_at"])),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SandboxRejected("sandbox_network_receipt_corrupt") from exc


class SqliteNetworkBudgetLedger:
    """Single-Broker durable ledger with private-path and one-way-state guards."""

    def __init__(self, *, database_path: Path) -> None:
        if os.name != "posix":
            raise SandboxUnavailable("sandbox_network_budget_ledger_requires_posix")
        if not database_path.is_absolute() or database_path.suffix != ".sqlite3":
            raise ValueError("network budget database path is invalid")
        self._database_path = database_path
        self._validate_parent()
        self._prepare_database_file()
        self._initialize()

    def reserve(
        self,
        *,
        authorization: VerifiedSandboxNetworkAuthorization,
    ) -> NetworkBudgetReservation:
        request = authorization.request
        binding_digest = network_budget_binding_digest(authorization)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT binding_digest, state, receipt_json "
                    "FROM network_budget_operations WHERE operation_id = ?",
                    (str(request.operation_id),),
                ).fetchone()
                if row is not None:
                    stored_binding, stored_state, receipt_payload = row
                    if stored_binding != binding_digest:
                        raise SandboxConflict("sandbox_network_operation_binding_conflict")
                    if stored_state != NetworkBudgetOperationState.COMMITTED.value:
                        raise SandboxConflict("sandbox_network_operation_outcome_unknown")
                    if not isinstance(receipt_payload, str):
                        raise SandboxConflict("sandbox_network_operation_outcome_unknown")
                    connection.commit()
                    return NetworkBudgetReservation(
                        operation_id=request.operation_id,
                        binding_digest=binding_digest,
                        replayed=True,
                        receipt=_receipt_from_payload(receipt_payload),
                    )

                used = connection.execute(
                    "SELECT COALESCE(SUM(connections), 0), "
                    "COALESCE(SUM(bytes_in), 0), COALESCE(SUM(bytes_out), 0) "
                    "FROM network_budget_operations WHERE network_lease_id = ?",
                    (str(request.network_lease_id),),
                ).fetchone()
                if used is None:
                    raise SandboxUnavailable("sandbox_network_budget_ledger_unavailable")
                used_connections, used_bytes_in, used_bytes_out = map(int, used)
                budget = authorization.budget
                if used_connections + request.requested_connections > budget.max_connections:
                    raise SandboxRejected("sandbox_network_connection_budget_exceeded")
                if used_bytes_in + request.requested_bytes_in > budget.max_bytes_in:
                    raise SandboxRejected("sandbox_network_bytes_in_budget_exceeded")
                if used_bytes_out + request.requested_bytes_out > budget.max_bytes_out:
                    raise SandboxRejected("sandbox_network_bytes_out_budget_exceeded")
                connection.execute(
                    "INSERT INTO network_budget_operations "
                    "(operation_id, binding_digest, request_binding_digest, "
                    "network_lease_id, connections, bytes_in, bytes_out, state, receipt_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                    (
                        str(request.operation_id),
                        binding_digest,
                        request.binding_digest(),
                        str(request.network_lease_id),
                        request.requested_connections,
                        request.requested_bytes_in,
                        request.requested_bytes_out,
                        NetworkBudgetOperationState.PENDING.value,
                    ),
                )
                connection.commit()
        except (SandboxConflict, SandboxRejected, SandboxUnavailable):
            raise
        except sqlite3.DatabaseError as exc:
            raise SandboxUnavailable("sandbox_network_budget_ledger_unavailable") from exc
        self._verify_database_file()
        return NetworkBudgetReservation(
            operation_id=request.operation_id,
            binding_digest=binding_digest,
            replayed=False,
            receipt=None,
        )

    def commit(
        self,
        *,
        reservation: NetworkBudgetReservation,
        receipt: BrokerConnectionReceipt,
    ) -> None:
        if reservation.replayed:
            raise SandboxConflict("sandbox_network_operation_already_committed")
        if receipt.operation_id != reservation.operation_id:
            raise SandboxRejected("sandbox_network_transport_receipt_rejected")
        payload = _receipt_payload(receipt)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = self._load_bound_row(connection, reservation)
                state, request_binding_digest, existing_receipt = row
                if receipt.request_binding_digest != request_binding_digest:
                    raise SandboxRejected("sandbox_network_transport_receipt_rejected")
                if state == NetworkBudgetOperationState.COMMITTED.value:
                    if existing_receipt != payload:
                        raise SandboxConflict("sandbox_network_receipt_conflict")
                    connection.commit()
                    return
                if state != NetworkBudgetOperationState.PENDING.value:
                    raise SandboxConflict("sandbox_network_operation_outcome_unknown")
                updated = connection.execute(
                    "UPDATE network_budget_operations SET state = ?, receipt_json = ? "
                    "WHERE operation_id = ? AND binding_digest = ? AND state = ?",
                    (
                        NetworkBudgetOperationState.COMMITTED.value,
                        payload,
                        str(reservation.operation_id),
                        reservation.binding_digest,
                        NetworkBudgetOperationState.PENDING.value,
                    ),
                ).rowcount
                if updated != 1:
                    raise SandboxConflict("sandbox_network_operation_outcome_unknown")
                connection.commit()
        except (SandboxConflict, SandboxRejected, SandboxUnavailable):
            raise
        except sqlite3.DatabaseError as exc:
            raise SandboxUnavailable("sandbox_network_budget_ledger_unavailable") from exc
        self._verify_database_file()

    def mark_unknown(self, *, reservation: NetworkBudgetReservation) -> None:
        if reservation.replayed:
            return
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                state, _, _ = self._load_bound_row(connection, reservation)
                if state == NetworkBudgetOperationState.PENDING.value:
                    updated = connection.execute(
                        "UPDATE network_budget_operations SET state = ? "
                        "WHERE operation_id = ? AND binding_digest = ? AND state = ?",
                        (
                            NetworkBudgetOperationState.UNKNOWN.value,
                            str(reservation.operation_id),
                            reservation.binding_digest,
                            NetworkBudgetOperationState.PENDING.value,
                        ),
                    ).rowcount
                    if updated != 1:
                        raise SandboxConflict("sandbox_network_operation_outcome_unknown")
                connection.commit()
        except (SandboxConflict, SandboxRejected, SandboxUnavailable):
            raise
        except sqlite3.DatabaseError as exc:
            raise SandboxUnavailable("sandbox_network_budget_ledger_unavailable") from exc
        self._verify_database_file()

    def _load_bound_row(
        self,
        connection: sqlite3.Connection,
        reservation: NetworkBudgetReservation,
    ) -> tuple[str, str, str | None]:
        row = connection.execute(
            "SELECT binding_digest, state, request_binding_digest, receipt_json "
            "FROM network_budget_operations WHERE operation_id = ?",
            (str(reservation.operation_id),),
        ).fetchone()
        if row is None:
            raise SandboxConflict("sandbox_network_operation_reservation_missing")
        stored_binding, state, request_binding_digest, receipt_payload = row
        if stored_binding != reservation.binding_digest:
            raise SandboxConflict("sandbox_network_operation_binding_conflict")
        return str(state), str(request_binding_digest), receipt_payload

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.executescript(
                    """
                    PRAGMA journal_mode = DELETE;
                    PRAGMA synchronous = FULL;
                    PRAGMA foreign_keys = ON;
                    CREATE TABLE IF NOT EXISTS network_budget_operations (
                        operation_id TEXT PRIMARY KEY,
                        binding_digest TEXT NOT NULL,
                        request_binding_digest TEXT NOT NULL,
                        network_lease_id TEXT NOT NULL,
                        connections INTEGER NOT NULL CHECK (connections > 0),
                        bytes_in INTEGER NOT NULL CHECK (bytes_in >= 0),
                        bytes_out INTEGER NOT NULL CHECK (bytes_out >= 0),
                        state TEXT NOT NULL CHECK (state IN ('pending', 'committed', 'unknown')),
                        receipt_json TEXT NULL,
                        CHECK ((state = 'committed' AND receipt_json IS NOT NULL)
                            OR (state != 'committed' AND receipt_json IS NULL))
                    ) STRICT;

                    CREATE INDEX IF NOT EXISTS network_budget_operations_lease_idx
                    ON network_budget_operations (network_lease_id);

                    CREATE TRIGGER IF NOT EXISTS network_budget_operations_no_delete
                    BEFORE DELETE ON network_budget_operations
                    BEGIN
                        SELECT RAISE(ABORT, 'network_budget_operations_append_only');
                    END;

                    CREATE TRIGGER IF NOT EXISTS network_budget_operations_immutable
                    BEFORE UPDATE ON network_budget_operations
                    WHEN OLD.operation_id != NEW.operation_id
                      OR OLD.binding_digest != NEW.binding_digest
                      OR OLD.request_binding_digest != NEW.request_binding_digest
                      OR OLD.network_lease_id != NEW.network_lease_id
                      OR OLD.connections != NEW.connections
                      OR OLD.bytes_in != NEW.bytes_in
                      OR OLD.bytes_out != NEW.bytes_out
                      OR OLD.state != 'pending'
                      OR NEW.state NOT IN ('committed', 'unknown')
                      OR (NEW.state = 'committed' AND NEW.receipt_json IS NULL)
                      OR (NEW.state = 'unknown' AND NEW.receipt_json IS NOT NULL)
                    BEGIN
                        SELECT RAISE(ABORT, 'network_budget_operations_immutable');
                    END;
                    """
                )
        except sqlite3.DatabaseError as exc:
            raise SandboxUnavailable("sandbox_network_budget_ledger_unavailable") from exc
        self._verify_database_file()

    def _validate_parent(self) -> None:
        parent = self._database_path.parent
        try:
            info = parent.lstat()
        except OSError as exc:
            raise SandboxUnavailable("sandbox_network_budget_directory_missing") from exc
        if (
            not stat.S_ISDIR(info.st_mode)
            or parent.is_symlink()
            or info.st_uid != _effective_uid()
            or info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
        ):
            raise SandboxUnavailable("sandbox_network_budget_directory_rejected")

    def _prepare_database_file(self) -> None:
        try:
            info = self._database_path.lstat()
        except FileNotFoundError:
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(self._database_path, flags, 0o600)
            except OSError as exc:
                raise SandboxUnavailable("sandbox_network_budget_file_create_failed") from exc
            os.close(descriptor)
            return
        except OSError as exc:
            raise SandboxUnavailable("sandbox_network_budget_file_unavailable") from exc
        self._verify_file_info(info)

    def _connect(self) -> sqlite3.Connection:
        self._validate_parent()
        self._verify_database_file()
        connection = sqlite3.connect(
            self._database_path,
            timeout=5.0,
            isolation_level=None,
        )
        self._verify_database_file()
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _verify_database_file(self) -> None:
        try:
            info = self._database_path.lstat()
        except OSError as exc:
            raise SandboxUnavailable("sandbox_network_budget_file_unavailable") from exc
        self._verify_file_info(info)

    def _verify_file_info(self, info: os.stat_result) -> None:
        if (
            not stat.S_ISREG(info.st_mode)
            or self._database_path.is_symlink()
            or info.st_uid != _effective_uid()
            or info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
        ):
            raise SandboxUnavailable("sandbox_network_budget_file_rejected")


__all__ = ["SqliteNetworkBudgetLedger"]
