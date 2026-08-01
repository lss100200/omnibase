"""Injectable durable-idempotency seam for Overlay mutations.

The in-memory implementation is deliberately test-only.  Production wiring
must inject a transactional implementation whose reservation survives process
restart.  A reserved but uncommitted operation is treated as outcome-unknown;
the adapter never auto-replays a mutation after that boundary.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from stat import S_IMODE

from omnibase.workspaces.overlay_adapters.contracts import (
    OverlayAction,
    OverlayDaemonReceipt,
    OverlayOperationReservation,
    OverlayOutcomeUnknown,
    OverlayRejected,
    OverlayState,
    OverlayUnavailable,
)


@dataclass(slots=True)
class _OperationEntry:
    action: OverlayAction
    operation_binding_digest: str
    receipt: OverlayDaemonReceipt | None = None


class InMemoryOverlayOperationLedger:
    """Thread-safe deterministic test ledger; not a production durable store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, _OperationEntry] = {}

    @staticmethod
    def _verify_entry(
        *,
        entry: _OperationEntry,
        action: OverlayAction,
        operation_binding_digest: str,
    ) -> None:
        if entry.action is not action or entry.operation_binding_digest != operation_binding_digest:
            raise OverlayRejected("overlay_operation_binding_conflict")

    def replay(
        self,
        *,
        operation_id: str,
        action: OverlayAction,
        operation_binding_digest: str,
    ) -> OverlayDaemonReceipt | None:
        with self._lock:
            entry = self._entries.get(operation_id)
            if entry is None:
                return None
            self._verify_entry(
                entry=entry,
                action=action,
                operation_binding_digest=operation_binding_digest,
            )
            if entry.receipt is None:
                raise OverlayOutcomeUnknown("overlay_operation_outcome_unknown")
            return entry.receipt

    def reserve(
        self,
        *,
        operation_id: str,
        action: OverlayAction,
        operation_binding_digest: str,
    ) -> OverlayOperationReservation:
        reservation = OverlayOperationReservation(
            operation_id=operation_id,
            action=action,
            operation_binding_digest=operation_binding_digest,
        )
        with self._lock:
            entry = self._entries.get(operation_id)
            if entry is not None:
                self._verify_entry(
                    entry=entry,
                    action=action,
                    operation_binding_digest=operation_binding_digest,
                )
                if entry.receipt is None:
                    raise OverlayOutcomeUnknown("overlay_operation_outcome_unknown")
                return OverlayOperationReservation(
                    operation_id=operation_id,
                    action=action,
                    operation_binding_digest=operation_binding_digest,
                    replayed=True,
                    receipt=entry.receipt,
                )
            self._entries[operation_id] = _OperationEntry(
                action=action,
                operation_binding_digest=operation_binding_digest,
            )
        return reservation

    def commit(
        self,
        *,
        reservation: OverlayOperationReservation,
        receipt: OverlayDaemonReceipt,
    ) -> None:
        if reservation.replayed:
            raise OverlayRejected("overlay_operation_already_committed")
        with self._lock:
            entry = self._entries.get(reservation.operation_id)
            if entry is None:
                raise OverlayRejected("overlay_operation_reservation_missing")
            self._verify_entry(
                entry=entry,
                action=reservation.action,
                operation_binding_digest=reservation.operation_binding_digest,
            )
            if (
                receipt.operation_id != reservation.operation_id
                or receipt.action is not reservation.action
            ):
                raise OverlayRejected("overlay_operation_receipt_binding_rejected")
            if entry.receipt is not None:
                if entry.receipt != receipt:
                    raise OverlayRejected("overlay_operation_receipt_conflict")
                return
            entry.receipt = receipt


def _receipt_payload(receipt: OverlayDaemonReceipt) -> str:
    return json.dumps(
        {
            "action": receipt.action.value,
            "binding_digest": receipt.binding_digest,
            "credential_generation": receipt.credential_generation,
            "network_fencing_token": receipt.network_fencing_token,
            "network_lease_id": receipt.network_lease_id,
            "observed_at": receipt.observed_at.isoformat(),
            "operation_id": receipt.operation_id,
            "peer_fencing_token": receipt.peer_fencing_token,
            "provider": receipt.provider,
            "receipt_digest": receipt.receipt_digest,
            "service_generation": receipt.service_generation,
            "source_daemon_attestation_digest": (receipt.source_daemon_attestation_digest),
            "source_node_fencing_token": receipt.source_node_fencing_token,
            "state": receipt.state.value,
            "target_daemon_attestation_digest": (receipt.target_daemon_attestation_digest),
            "target_node_fencing_token": receipt.target_node_fencing_token,
            "workspace_generation": receipt.workspace_generation,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _receipt_from_payload(payload: str) -> OverlayDaemonReceipt:
    try:
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise TypeError
        return OverlayDaemonReceipt(
            action=OverlayAction(str(value["action"])),
            operation_id=str(value["operation_id"]),
            provider=str(value["provider"]),
            state=OverlayState(str(value["state"])),
            network_lease_id=str(value["network_lease_id"]),
            binding_digest=str(value["binding_digest"]),
            workspace_generation=int(value["workspace_generation"]),
            service_generation=int(value["service_generation"]),
            peer_fencing_token=int(value["peer_fencing_token"]),
            network_fencing_token=int(value["network_fencing_token"]),
            source_node_fencing_token=int(value["source_node_fencing_token"]),
            target_node_fencing_token=int(value["target_node_fencing_token"]),
            source_daemon_attestation_digest=str(value["source_daemon_attestation_digest"]),
            target_daemon_attestation_digest=str(value["target_daemon_attestation_digest"]),
            credential_generation=(
                None
                if value["credential_generation"] is None
                else int(value["credential_generation"])
            ),
            observed_at=datetime.fromisoformat(str(value["observed_at"])),
            receipt_digest=str(value["receipt_digest"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise OverlayRejected("overlay_operation_receipt_corrupt") from exc


class SqliteOverlayOperationLedger:
    """Single-node durable ledger for a trusted Node-Daemon state directory.

    The database path must be explicit, absolute, owned by the current POSIX
    user, non-symlinked and inaccessible to group/other users.  SQLite uses
    `BEGIN IMMEDIATE`, WAL and FULL synchronous durability.  Triggers prevent
    delete, binding mutation and any receipt rewrite after the one-way
    pending-to-committed transition.
    """

    def __init__(self, *, database_path: str) -> None:
        if os.name != "posix":
            raise OverlayUnavailable("overlay_operation_ledger_requires_posix")
        candidate = Path(database_path)
        if not candidate.is_absolute():
            raise ValueError("Overlay ledger database_path must be absolute")
        self._database_path = candidate
        self._validate_path(allow_missing_file=True)
        self._initialize()

    def _validate_path(self, *, allow_missing_file: bool) -> None:
        parent = self._database_path.parent
        try:
            parent_stat = parent.lstat()
        except FileNotFoundError as exc:
            raise OverlayUnavailable("overlay_operation_ledger_directory_missing") from exc
        if not parent.is_dir() or parent.is_symlink():
            raise OverlayUnavailable("overlay_operation_ledger_directory_rejected")
        if parent_stat.st_uid != os.geteuid() or S_IMODE(parent_stat.st_mode) & 0o077:
            raise OverlayUnavailable("overlay_operation_ledger_directory_permissions_rejected")
        try:
            file_stat = self._database_path.lstat()
        except FileNotFoundError:
            if allow_missing_file:
                return
            raise OverlayUnavailable("overlay_operation_ledger_file_missing") from None
        if self._database_path.is_symlink() or not self._database_path.is_file():
            raise OverlayUnavailable("overlay_operation_ledger_file_rejected")
        if file_stat.st_uid != os.geteuid() or S_IMODE(file_stat.st_mode) & 0o077:
            raise OverlayUnavailable("overlay_operation_ledger_file_permissions_rejected")

    def _connect(self) -> sqlite3.Connection:
        self._validate_path(allow_missing_file=True)
        connection = sqlite3.connect(
            self._database_path,
            timeout=5.0,
            isolation_level=None,
        )
        if self._database_path.exists():
            os.chmod(self._database_path, 0o600)
        self._validate_path(allow_missing_file=False)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS overlay_operations (
                    operation_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    operation_binding_digest TEXT NOT NULL,
                    receipt_json TEXT NULL
                ) STRICT;

                CREATE TRIGGER IF NOT EXISTS overlay_operations_no_delete
                BEFORE DELETE ON overlay_operations
                BEGIN
                    SELECT RAISE(ABORT, 'overlay_operations_append_only');
                END;

                CREATE TRIGGER IF NOT EXISTS overlay_operations_immutable
                BEFORE UPDATE ON overlay_operations
                WHEN OLD.operation_id != NEW.operation_id
                  OR OLD.action != NEW.action
                  OR OLD.operation_binding_digest != NEW.operation_binding_digest
                  OR (OLD.receipt_json IS NOT NULL AND OLD.receipt_json != NEW.receipt_json)
                  OR (OLD.receipt_json IS NOT NULL AND NEW.receipt_json IS NULL)
                BEGIN
                    SELECT RAISE(ABORT, 'overlay_operations_immutable');
                END;
                """
            )

    @staticmethod
    def _verify_row(
        *,
        row: tuple[str, str, str | None],
        action: OverlayAction,
        operation_binding_digest: str,
    ) -> str | None:
        stored_action, stored_digest, receipt_payload = row
        if stored_action != action.value or stored_digest != operation_binding_digest:
            raise OverlayRejected("overlay_operation_binding_conflict")
        return receipt_payload

    def replay(
        self,
        *,
        operation_id: str,
        action: OverlayAction,
        operation_binding_digest: str,
    ) -> OverlayDaemonReceipt | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT action, operation_binding_digest, receipt_json "
                "FROM overlay_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if row is None:
            return None
        receipt_payload = self._verify_row(
            row=row,
            action=action,
            operation_binding_digest=operation_binding_digest,
        )
        if receipt_payload is None:
            raise OverlayOutcomeUnknown("overlay_operation_outcome_unknown")
        return _receipt_from_payload(receipt_payload)

    def reserve(
        self,
        *,
        operation_id: str,
        action: OverlayAction,
        operation_binding_digest: str,
    ) -> OverlayOperationReservation:
        reservation = OverlayOperationReservation(
            operation_id=operation_id,
            action=action,
            operation_binding_digest=operation_binding_digest,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                inserted = connection.execute(
                    "INSERT INTO overlay_operations "
                    "(operation_id, action, operation_binding_digest, receipt_json) "
                    "VALUES (?, ?, ?, NULL) ON CONFLICT(operation_id) DO NOTHING",
                    (operation_id, action.value, operation_binding_digest),
                )
                row = connection.execute(
                    "SELECT action, operation_binding_digest, receipt_json "
                    "FROM overlay_operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if row is None:
                    raise OverlayUnavailable("overlay_operation_reservation_missing")
                receipt_payload = self._verify_row(
                    row=row,
                    action=action,
                    operation_binding_digest=operation_binding_digest,
                )
                if inserted.rowcount == 0 and receipt_payload is None:
                    raise OverlayOutcomeUnknown("overlay_operation_outcome_unknown")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        if receipt_payload is None:
            return reservation
        return OverlayOperationReservation(
            operation_id=operation_id,
            action=action,
            operation_binding_digest=operation_binding_digest,
            replayed=True,
            receipt=_receipt_from_payload(receipt_payload),
        )

    def commit(
        self,
        *,
        reservation: OverlayOperationReservation,
        receipt: OverlayDaemonReceipt,
    ) -> None:
        if reservation.replayed:
            raise OverlayRejected("overlay_operation_already_committed")
        if (
            receipt.operation_id != reservation.operation_id
            or receipt.action is not reservation.action
        ):
            raise OverlayRejected("overlay_operation_receipt_binding_rejected")
        payload = _receipt_payload(receipt)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT action, operation_binding_digest, receipt_json "
                    "FROM overlay_operations WHERE operation_id = ?",
                    (reservation.operation_id,),
                ).fetchone()
                if row is None:
                    raise OverlayRejected("overlay_operation_reservation_missing")
                existing = self._verify_row(
                    row=row,
                    action=reservation.action,
                    operation_binding_digest=reservation.operation_binding_digest,
                )
                if existing is None:
                    updated = connection.execute(
                        "UPDATE overlay_operations SET receipt_json = ? "
                        "WHERE operation_id = ? AND receipt_json IS NULL",
                        (payload, reservation.operation_id),
                    ).rowcount
                    if updated != 1:
                        raise OverlayOutcomeUnknown("overlay_operation_commit_conflict")
                elif existing != payload:
                    raise OverlayRejected("overlay_operation_receipt_conflict")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise


__all__ = ["InMemoryOverlayOperationLedger", "SqliteOverlayOperationLedger"]
