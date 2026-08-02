"""Durability and filesystem safety tests for the Overlay operation ledger."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from omnibase.workspaces.overlay_adapters import (
    OverlayAction,
    OverlayDaemonReceipt,
    OverlayOperationReservation,
    OverlayOutcomeUnknown,
    OverlayRejected,
    OverlayState,
    OverlayUnavailable,
    SqliteOverlayOperationLedger,
)

OPERATION_ID = "20000000-0000-4000-8000-000000000001"
DIGEST = "a" * 64


def _directory(tmp_path: Path) -> Path:
    state = tmp_path / "node-daemon-state"
    state.mkdir(mode=0o700)
    state.chmod(0o700)
    return state


def _receipt() -> OverlayDaemonReceipt:
    return OverlayDaemonReceipt(
        action=OverlayAction.ACTIVATE,
        operation_id=OPERATION_ID,
        provider="headscale",
        state=OverlayState.ACTIVE,
        network_lease_id="20000000-0000-4000-8000-000000000002",
        binding_digest="b" * 64,
        workspace_generation=3,
        service_generation=5,
        peer_fencing_token=7,
        network_fencing_token=11,
        source_node_fencing_token=13,
        target_node_fencing_token=17,
        source_daemon_attestation_digest="c" * 64,
        target_daemon_attestation_digest="d" * 64,
        credential_generation=19,
        observed_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
        receipt_digest="e" * 64,
    )


def _reserve(ledger: SqliteOverlayOperationLedger) -> OverlayOperationReservation:
    return ledger.reserve(
        operation_id=OPERATION_ID,
        action=OverlayAction.ACTIVATE,
        operation_binding_digest=DIGEST,
    )


@pytest.mark.skipif(os.name != "posix", reason="hardened ledger is POSIX-only")
def test_sqlite_ledger_replays_across_instances_and_processes(tmp_path: Path) -> None:
    database = _directory(tmp_path) / "operations.sqlite3"
    first = SqliteOverlayOperationLedger(database_path=str(database))
    first.commit(reservation=_reserve(first), receipt=_receipt())

    second = SqliteOverlayOperationLedger(database_path=str(database))
    assert (
        second.replay(
            operation_id=OPERATION_ID,
            action=OverlayAction.ACTIVATE,
            operation_binding_digest=DIGEST,
        )
        == _receipt()
    )

    code = (
        "from omnibase.workspaces.overlay_adapters import "
        "SqliteOverlayOperationLedger,OverlayAction;"
        f"ledger=SqliteOverlayOperationLedger(database_path={str(database)!r});"
        f"receipt=ledger.replay(operation_id={OPERATION_ID!r},"
        "action=OverlayAction.ACTIVATE,"
        f"operation_binding_digest={DIGEST!r});"
        "print(receipt.receipt_digest)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "e" * 64


@pytest.mark.skipif(os.name != "posix", reason="hardened ledger is POSIX-only")
def test_pending_reservation_survives_crash_as_ambiguous(tmp_path: Path) -> None:
    database = _directory(tmp_path) / "operations.sqlite3"
    code = (
        "from omnibase.workspaces.overlay_adapters import "
        "SqliteOverlayOperationLedger,OverlayAction;"
        f"ledger=SqliteOverlayOperationLedger(database_path={str(database)!r});"
        f"ledger.reserve(operation_id={OPERATION_ID!r},"
        "action=OverlayAction.ACTIVATE,"
        f"operation_binding_digest={DIGEST!r})"
    )
    subprocess.run([sys.executable, "-c", code], check=True)

    recovered = SqliteOverlayOperationLedger(database_path=str(database))
    with pytest.raises(OverlayOutcomeUnknown, match="operation_outcome_unknown"):
        recovered.replay(
            operation_id=OPERATION_ID,
            action=OverlayAction.ACTIVATE,
            operation_binding_digest=DIGEST,
        )
    with pytest.raises(OverlayOutcomeUnknown, match="operation_outcome_unknown"):
        _reserve(recovered)


@pytest.mark.skipif(os.name != "posix", reason="hardened ledger is POSIX-only")
def test_sqlite_ledger_binding_cas_and_receipt_are_append_only(tmp_path: Path) -> None:
    database = _directory(tmp_path) / "operations.sqlite3"
    ledger = SqliteOverlayOperationLedger(database_path=str(database))
    reservation = _reserve(ledger)

    with pytest.raises(OverlayRejected, match="binding_conflict"):
        ledger.replay(
            operation_id=OPERATION_ID,
            action=OverlayAction.ROTATE,
            operation_binding_digest=DIGEST,
        )
    ledger.commit(reservation=reservation, receipt=_receipt())

    connection = sqlite3.connect(database)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE overlay_operations SET receipt_json = '{}' WHERE operation_id = ?",
                (OPERATION_ID,),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            connection.execute(
                "DELETE FROM overlay_operations WHERE operation_id = ?",
                (OPERATION_ID,),
            )
    finally:
        connection.close()


@pytest.mark.skipif(os.name != "posix", reason="hardened ledger is POSIX-only")
def test_sqlite_ledger_rejects_symlink_and_broad_permissions(tmp_path: Path) -> None:
    state = _directory(tmp_path)
    target = state / "target.sqlite3"
    sqlite3.connect(target).close()
    target.chmod(0o600)
    link = state / "linked.sqlite3"
    link.symlink_to(target)

    with pytest.raises(OverlayUnavailable, match="file_rejected"):
        SqliteOverlayOperationLedger(database_path=str(link))

    state.chmod(0o755)
    with pytest.raises(OverlayUnavailable, match="directory_permissions_rejected"):
        SqliteOverlayOperationLedger(database_path=str(state / "other.sqlite3"))
