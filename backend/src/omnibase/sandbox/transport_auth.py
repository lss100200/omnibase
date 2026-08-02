"""Authenticated, replay-protected Runner transport envelopes.

The HMAC implementation is limited to an explicitly injected local/dev key.
It never reads a key from an environment variable or file.  A remote production
adapter must additionally provide mutually authenticated transport identity.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from omnibase.sandbox.contracts import SandboxRejected, SandboxUnavailable, utc_now

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_KEY_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,63}$")
_ACTION_RE = re.compile(r"^sandbox(?:\.control)?\.[a-z_]{2,64}$")
_AUDIENCE = "omnibase-sandbox-runner-v1"


def _aware(value: datetime, *, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class RunnerTransportEnvelope:
    audience: str
    runner_id: UUID
    node_id: UUID
    operation_id: UUID
    action: str
    payload_digest: str
    host_evidence_digest: str
    key_id: str
    nonce: UUID
    sequence: int
    sent_at: datetime
    expires_at: datetime
    signature: str

    def __post_init__(self) -> None:
        if self.audience != _AUDIENCE:
            raise ValueError("runner transport audience is invalid")
        identifiers = (self.runner_id, self.node_id, self.operation_id, self.nonce)
        if any(not isinstance(value, UUID) for value in identifiers):
            raise TypeError("runner transport identifiers must be UUID values")
        if _ACTION_RE.fullmatch(self.action) is None:
            raise ValueError("runner transport action is invalid")
        for name, value in (
            ("payload_digest", self.payload_digest),
            ("host_evidence_digest", self.host_evidence_digest),
        ):
            if _SHA256_RE.fullmatch(value) is None:
                raise ValueError(f"{name} must be sha256")
        if _KEY_ID_RE.fullmatch(self.key_id) is None:
            raise ValueError("runner transport key_id is invalid")
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
            or self.sequence > 2**63 - 1
        ):
            raise ValueError("runner transport sequence is invalid")
        _aware(self.sent_at, name="sent_at")
        _aware(self.expires_at, name="expires_at")
        if self.expires_at <= self.sent_at or self.expires_at - self.sent_at > timedelta(
            seconds=30
        ):
            raise ValueError("runner transport validity window is invalid")
        if self.signature and _SHA256_RE.fullmatch(self.signature) is None:
            raise ValueError("runner transport signature is invalid")

    def unsigned_value(self) -> dict[str, object]:
        return {
            "action": self.action,
            "audience": self.audience,
            "expires_at": self.expires_at.isoformat(),
            "host_evidence_digest": self.host_evidence_digest,
            "key_id": self.key_id,
            "node_id": str(self.node_id),
            "nonce": str(self.nonce),
            "operation_id": str(self.operation_id),
            "payload_digest": self.payload_digest,
            "runner_id": str(self.runner_id),
            "sent_at": self.sent_at.isoformat(),
            "sequence": self.sequence,
        }


class RunnerReplayStore(Protocol):
    def accept(self, envelope: RunnerTransportEnvelope, *, now: datetime) -> None: ...


class InMemoryRunnerReplayStore:
    """Thread-safe local/dev replay ledger; remote production needs durable state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._nonces: dict[tuple[UUID, UUID], datetime] = {}
        self._sequences: dict[tuple[UUID, str], int] = {}

    def accept(self, envelope: RunnerTransportEnvelope, *, now: datetime) -> None:
        _aware(now, name="replay clock")
        nonce_key = (envelope.runner_id, envelope.nonce)
        sequence_key = (envelope.runner_id, envelope.key_id)
        with self._lock:
            expired = [key for key, expiry in self._nonces.items() if expiry <= now]
            for key in expired:
                self._nonces.pop(key, None)
            if nonce_key in self._nonces:
                raise SandboxRejected("sandbox_runner_transport_replay_rejected")
            last_sequence = self._sequences.get(sequence_key, 0)
            if envelope.sequence <= last_sequence:
                raise SandboxRejected("sandbox_runner_transport_sequence_rejected")
            self._nonces[nonce_key] = envelope.expires_at
            self._sequences[sequence_key] = envelope.sequence


class SqliteRunnerReplayStore:
    """Durable single-Runner replay ledger for an independent Node Daemon.

    The database path is explicitly injected and must live in a private
    directory owned by the Runner identity.  It is not the OmniBase business
    database and contains only nonce expiry and monotonic sequence metadata.
    """

    def __init__(self, *, database_path: Path) -> None:
        if not database_path.is_absolute() or database_path.suffix != ".sqlite3":
            raise ValueError("runner replay database path is invalid")
        parent = database_path.parent
        try:
            parent_info = parent.lstat()
        except OSError as exc:
            raise ValueError("runner replay database parent is unavailable") from exc
        if (
            not stat.S_ISDIR(parent_info.st_mode)
            or parent.is_symlink()
            or parent_info.st_uid != _effective_uid()
            or parent_info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
        ):
            raise ValueError("runner replay database parent is not private")
        self._database_path = database_path
        self._prepare_database_file()
        self._initialize()

    def accept(self, envelope: RunnerTransportEnvelope, *, now: datetime) -> None:
        _aware(now, name="replay clock")
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "DELETE FROM runner_replay_nonces WHERE expires_at <= ?",
                    (_epoch_micros(now),),
                )
                try:
                    connection.execute(
                        """
                        INSERT INTO runner_replay_nonces
                            (runner_id, nonce, expires_at)
                        VALUES (?, ?, ?)
                        """,
                        (
                            str(envelope.runner_id),
                            str(envelope.nonce),
                            _epoch_micros(envelope.expires_at),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise SandboxRejected("sandbox_runner_transport_replay_rejected") from exc
                current = connection.execute(
                    """
                    SELECT sequence
                    FROM runner_replay_sequences
                    WHERE runner_id = ? AND key_id = ?
                    """,
                    (str(envelope.runner_id), envelope.key_id),
                ).fetchone()
                if current is not None and envelope.sequence <= int(current[0]):
                    raise SandboxRejected("sandbox_runner_transport_sequence_rejected")
                connection.execute(
                    """
                    INSERT INTO runner_replay_sequences (runner_id, key_id, sequence)
                    VALUES (?, ?, ?)
                    ON CONFLICT (runner_id, key_id)
                    DO UPDATE SET sequence = excluded.sequence
                    """,
                    (str(envelope.runner_id), envelope.key_id, envelope.sequence),
                )
                connection.commit()
        except SandboxRejected:
            raise
        except sqlite3.DatabaseError as exc:
            raise SandboxUnavailable("sandbox_runner_replay_store_unavailable") from exc
        self._verify_database_file()

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.executescript(
                    """
                    PRAGMA journal_mode = DELETE;
                    PRAGMA synchronous = FULL;
                    PRAGMA foreign_keys = ON;
                    CREATE TABLE IF NOT EXISTS runner_replay_nonces (
                        runner_id TEXT NOT NULL,
                        nonce TEXT NOT NULL,
                        expires_at INTEGER NOT NULL CHECK (expires_at > 0),
                        PRIMARY KEY (runner_id, nonce)
                    ) WITHOUT ROWID;
                    CREATE TABLE IF NOT EXISTS runner_replay_sequences (
                        runner_id TEXT NOT NULL,
                        key_id TEXT NOT NULL,
                        sequence INTEGER NOT NULL CHECK (sequence > 0),
                        PRIMARY KEY (runner_id, key_id)
                    ) WITHOUT ROWID;
                    """
                )
        except sqlite3.DatabaseError as exc:
            raise ValueError("runner replay database cannot be initialized") from exc
        self._verify_database_file()

    def _prepare_database_file(self) -> None:
        try:
            info = self._database_path.lstat()
        except FileNotFoundError:
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(self._database_path, flags, 0o600)
            except OSError as exc:
                raise ValueError("runner replay database cannot be created securely") from exc
            os.close(descriptor)
            return
        except OSError as exc:
            raise ValueError("runner replay database is unavailable") from exc
        if (
            not stat.S_ISREG(info.st_mode)
            or self._database_path.is_symlink()
            or info.st_uid != _effective_uid()
            or info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
        ):
            raise ValueError("runner replay database is not trusted")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(
            self._database_path,
            timeout=2.0,
            isolation_level=None,
        )

    def _verify_database_file(self) -> None:
        try:
            info = self._database_path.lstat()
        except OSError as exc:
            raise SandboxUnavailable("sandbox_runner_replay_store_unavailable") from exc
        if (
            not stat.S_ISREG(info.st_mode)
            or self._database_path.is_symlink()
            or info.st_uid != _effective_uid()
            or info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
        ):
            raise SandboxUnavailable("sandbox_runner_replay_store_untrusted")


def _effective_uid() -> int:
    get_effective_uid = getattr(os, "geteuid", None)
    return int(get_effective_uid()) if get_effective_uid is not None else 0


def _epoch_micros(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000)


@dataclass(frozen=True, slots=True)
class TrustedRunnerMtlsPeer:
    """Peer identity injected only by a verified Runner mTLS terminator."""

    runner_id: UUID
    node_id: UUID
    certificate_thumbprint: str
    verified_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.runner_id, UUID) or not isinstance(self.node_id, UUID):
            raise TypeError("Runner mTLS peer identifiers must be UUID values")
        if _SHA256_RE.fullmatch(self.certificate_thumbprint) is None:
            raise ValueError("Runner mTLS certificate thumbprint must be sha256")
        _aware(self.verified_at, name="Runner mTLS verified_at")
        _aware(self.expires_at, name="Runner mTLS expires_at")
        if self.expires_at <= self.verified_at or self.expires_at - self.verified_at > timedelta(
            minutes=5
        ):
            raise ValueError("Runner mTLS peer evidence validity is invalid")

    @property
    def key_id(self) -> str:
        return f"mtls.{self.certificate_thumbprint[:32]}"


class RunnerTransportAuthenticator(Protocol):
    def verify(
        self,
        envelope: RunnerTransportEnvelope,
        *,
        expected_runner_id: UUID,
        expected_node_id: UUID,
        expected_operation_id: UUID,
        expected_action: str,
        expected_payload_digest: str,
        expected_host_evidence_digest: str,
        expected_runner_identity_thumbprint: str,
    ) -> None: ...


class RejectingRunnerTransportAuthenticator:
    def verify(
        self,
        envelope: RunnerTransportEnvelope,
        *,
        expected_runner_id: UUID,
        expected_node_id: UUID,
        expected_operation_id: UUID,
        expected_action: str,
        expected_payload_digest: str,
        expected_host_evidence_digest: str,
        expected_runner_identity_thumbprint: str,
    ) -> None:
        del (
            envelope,
            expected_runner_id,
            expected_node_id,
            expected_operation_id,
            expected_action,
            expected_payload_digest,
            expected_host_evidence_digest,
            expected_runner_identity_thumbprint,
        )
        raise SandboxUnavailable("sandbox_runner_transport_authenticator_unavailable")


class MtlsRunnerTransportAuthenticator:
    """Production channel authenticator backed by trusted mTLS peer evidence.

    TLS provides message integrity and possession of the private key.  The
    envelope therefore carries no application HMAC; it still binds the exact
    operation and is checked against a durable replay ledger.
    """

    def __init__(
        self,
        *,
        peer: TrustedRunnerMtlsPeer,
        replay_store: RunnerReplayStore,
        clock=utc_now,
    ) -> None:
        if not isinstance(peer, TrustedRunnerMtlsPeer):
            raise TypeError("peer must be TrustedRunnerMtlsPeer")
        self._peer = peer
        self._replay_store = replay_store
        self._clock = clock

    def verify(
        self,
        envelope: RunnerTransportEnvelope,
        *,
        expected_runner_id: UUID,
        expected_node_id: UUID,
        expected_operation_id: UUID,
        expected_action: str,
        expected_payload_digest: str,
        expected_host_evidence_digest: str,
        expected_runner_identity_thumbprint: str,
    ) -> None:
        now = self._clock()
        if (
            self._peer.verified_at > now
            or self._peer.expires_at <= now
            or envelope.sent_at > now
            or envelope.expires_at <= now
        ):
            raise SandboxRejected("sandbox_runner_transport_expired")
        if envelope.signature != "" or envelope.key_id != self._peer.key_id:
            raise SandboxRejected("sandbox_runner_transport_authentication_rejected")
        supplied = (
            envelope.runner_id,
            envelope.node_id,
            envelope.operation_id,
            envelope.action,
            envelope.payload_digest,
            envelope.host_evidence_digest,
        )
        expected = (
            expected_runner_id,
            expected_node_id,
            expected_operation_id,
            expected_action,
            expected_payload_digest,
            expected_host_evidence_digest,
        )
        if (
            envelope.runner_id != self._peer.runner_id
            or envelope.node_id != self._peer.node_id
            or self._peer.certificate_thumbprint != expected_runner_identity_thumbprint
            or supplied != expected
        ):
            raise SandboxRejected("sandbox_runner_transport_binding_rejected")
        self._replay_store.accept(envelope, now=now)


class HmacRunnerTransportAuthenticator:
    """Injected-key local/dev authenticator with strict replay protection."""

    def __init__(
        self,
        *,
        key_id: str,
        secret: bytes,
        replay_store: RunnerReplayStore | None = None,
        clock=utc_now,
    ) -> None:
        if _KEY_ID_RE.fullmatch(key_id) is None:
            raise ValueError("runner transport key_id is invalid")
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("runner transport secret must contain at least 32 bytes")
        self._key_id = key_id
        self._secret = bytes(secret)
        self._replay_store = replay_store or InMemoryRunnerReplayStore()
        self._clock = clock

    def sign(
        self,
        *,
        runner_id: UUID,
        node_id: UUID,
        operation_id: UUID,
        action: str,
        payload_digest: str,
        host_evidence_digest: str,
        sequence: int,
        validity_seconds: int = 10,
    ) -> RunnerTransportEnvelope:
        if isinstance(validity_seconds, bool) or validity_seconds < 1 or validity_seconds > 30:
            raise ValueError("runner transport validity is outside the safe range")
        now = self._clock()
        envelope = RunnerTransportEnvelope(
            audience=_AUDIENCE,
            runner_id=runner_id,
            node_id=node_id,
            operation_id=operation_id,
            action=action,
            payload_digest=payload_digest,
            host_evidence_digest=host_evidence_digest,
            key_id=self._key_id,
            nonce=uuid4(),
            sequence=sequence,
            sent_at=now,
            expires_at=now + timedelta(seconds=validity_seconds),
            signature="",
        )
        return replace(envelope, signature=self._signature(envelope))

    def verify(
        self,
        envelope: RunnerTransportEnvelope,
        *,
        expected_runner_id: UUID,
        expected_node_id: UUID,
        expected_operation_id: UUID,
        expected_action: str,
        expected_payload_digest: str,
        expected_host_evidence_digest: str,
        expected_runner_identity_thumbprint: str,
    ) -> None:
        if _SHA256_RE.fullmatch(expected_runner_identity_thumbprint) is None:
            raise SandboxRejected("sandbox_runner_transport_binding_rejected")
        if envelope.key_id != self._key_id or not hmac.compare_digest(
            envelope.signature,
            self._signature(envelope),
        ):
            raise SandboxRejected("sandbox_runner_transport_authentication_rejected")
        now = self._clock()
        if envelope.sent_at > now or envelope.expires_at <= now:
            raise SandboxRejected("sandbox_runner_transport_expired")
        supplied = (
            envelope.runner_id,
            envelope.node_id,
            envelope.operation_id,
            envelope.action,
            envelope.payload_digest,
            envelope.host_evidence_digest,
        )
        expected = (
            expected_runner_id,
            expected_node_id,
            expected_operation_id,
            expected_action,
            expected_payload_digest,
            expected_host_evidence_digest,
        )
        if supplied != expected:
            raise SandboxRejected("sandbox_runner_transport_binding_rejected")
        self._replay_store.accept(envelope, now=now)

    def _signature(self, envelope: RunnerTransportEnvelope) -> str:
        payload = json.dumps(
            envelope.unsigned_value(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()


__all__ = [
    "HmacRunnerTransportAuthenticator",
    "InMemoryRunnerReplayStore",
    "MtlsRunnerTransportAuthenticator",
    "RejectingRunnerTransportAuthenticator",
    "RunnerReplayStore",
    "RunnerTransportAuthenticator",
    "RunnerTransportEnvelope",
    "SqliteRunnerReplayStore",
    "TrustedRunnerMtlsPeer",
]
