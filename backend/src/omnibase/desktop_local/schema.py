"""Transactional SQLite migration definitions for the desktop-local store."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

DESKTOP_APPLICATION_ID = 0x4F4D4E42  # ASCII "OMNB"
DESKTOP_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class DesktopMigration:
    version: int
    migration_id: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        payload = "\n-- statement --\n".join(self.statements).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


DESKTOP_0001 = DesktopMigration(
    version=1,
    migration_id="desktop_0001",
    statements=(
        """
        CREATE TABLE desktop_schema_metadata (
            singleton_key INTEGER PRIMARY KEY CHECK (singleton_key = 1),
            schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
            application_version TEXT NOT NULL CHECK (length(application_version) BETWEEN 1 AND 64),
            updated_at TEXT NOT NULL
        ) STRICT
        """,
        """
        CREATE TABLE desktop_migration_history (
            version INTEGER PRIMARY KEY CHECK (version >= 1),
            migration_id TEXT NOT NULL UNIQUE,
            checksum_sha256 TEXT NOT NULL CHECK (
                length(checksum_sha256) = 64
                AND checksum_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            application_version TEXT NOT NULL CHECK (length(application_version) BETWEEN 1 AND 64),
            applied_at TEXT NOT NULL
        ) STRICT
        """,
        """
        CREATE TABLE owner (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 128),
            singleton_key INTEGER NOT NULL DEFAULT 1 UNIQUE CHECK (singleton_key = 1),
            display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 256),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        ) STRICT
        """,
        """
        CREATE TABLE workspace (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 128),
            owner_id TEXT NOT NULL,
            name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 256),
            state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'archived')),
            row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (id, owner_id),
            FOREIGN KEY (owner_id) REFERENCES owner(id) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE audit_event (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE CHECK (length(event_id) BETWEEN 1 AND 128),
            owner_id TEXT NOT NULL,
            workspace_id TEXT,
            event_type TEXT NOT NULL CHECK (
                length(event_type) BETWEEN 3 AND 64
                AND event_type NOT GLOB '*[^a-z0-9_]*'
            ),
            payload_json TEXT NOT NULL CHECK (json_valid(payload_json) = 1),
            created_at TEXT NOT NULL,
            FOREIGN KEY (owner_id) REFERENCES owner(id) ON DELETE RESTRICT,
            FOREIGN KEY (workspace_id, owner_id)
                REFERENCES workspace(id, owner_id) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE runtime_job (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 128),
            owner_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            job_kind TEXT NOT NULL CHECK (
                length(job_kind) BETWEEN 3 AND 64
                AND job_kind NOT GLOB '*[^a-z0-9_]*'
            ),
            state TEXT NOT NULL DEFAULT 'queued' CHECK (
                state IN ('queued', 'claimed', 'running', 'succeeded', 'failed', 'cancelled')
            ),
            claim_owner TEXT,
            claim_token TEXT,
            claimed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (
                (state = 'queued'
                    AND claim_owner IS NULL AND claim_token IS NULL AND claimed_at IS NULL)
                OR (state IN ('claimed', 'running', 'succeeded', 'failed')
                    AND claim_owner IS NOT NULL AND claim_token IS NOT NULL
                    AND claimed_at IS NOT NULL)
                OR (state = 'cancelled'
                    AND ((claim_owner IS NULL AND claim_token IS NULL AND claimed_at IS NULL)
                        OR (claim_owner IS NOT NULL AND claim_token IS NOT NULL
                            AND claimed_at IS NOT NULL)))
            ),
            FOREIGN KEY (owner_id) REFERENCES owner(id) ON DELETE RESTRICT,
            FOREIGN KEY (workspace_id, owner_id)
                REFERENCES workspace(id, owner_id) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TRIGGER audit_event_update_forbidden
        BEFORE UPDATE ON audit_event
        BEGIN
            SELECT RAISE(ABORT, 'desktop_audit_append_only');
        END
        """,
        """
        CREATE TRIGGER audit_event_delete_forbidden
        BEFORE DELETE ON audit_event
        BEGIN
            SELECT RAISE(ABORT, 'desktop_audit_append_only');
        END
        """,
        """
        CREATE TRIGGER workspace_state_transition_guard
        BEFORE UPDATE OF state ON workspace
        WHEN NEW.state <> OLD.state
             AND NOT (OLD.state = 'active' AND NEW.state = 'archived')
        BEGIN
            SELECT RAISE(ABORT, 'desktop_workspace_state_transition_forbidden');
        END
        """,
        """
        CREATE TRIGGER workspace_identity_guard
        BEFORE UPDATE ON workspace
        WHEN NEW.id <> OLD.id OR NEW.owner_id <> OLD.owner_id OR NEW.created_at <> OLD.created_at
        BEGIN
            SELECT RAISE(ABORT, 'desktop_workspace_identity_immutable');
        END
        """,
        """
        CREATE TRIGGER runtime_job_identity_guard
        BEFORE UPDATE ON runtime_job
        WHEN NEW.id <> OLD.id
          OR NEW.owner_id <> OLD.owner_id
          OR NEW.workspace_id <> OLD.workspace_id
          OR NEW.job_kind <> OLD.job_kind
          OR NEW.created_at <> OLD.created_at
        BEGIN
            SELECT RAISE(ABORT, 'desktop_runtime_job_identity_immutable');
        END
        """,
        """
        CREATE TRIGGER runtime_job_claim_binding_guard
        BEFORE UPDATE ON runtime_job
        WHEN OLD.state <> 'queued'
             AND (NEW.claim_owner IS NOT OLD.claim_owner
                  OR NEW.claim_token IS NOT OLD.claim_token
                  OR NEW.claimed_at IS NOT OLD.claimed_at)
        BEGIN
            SELECT RAISE(ABORT, 'desktop_runtime_job_claim_binding_immutable');
        END
        """,
        """
        CREATE TRIGGER runtime_job_state_transition_guard
        BEFORE UPDATE OF state ON runtime_job
        WHEN NEW.state <> OLD.state
             AND NOT (
                 (OLD.state = 'queued' AND NEW.state IN ('claimed', 'cancelled'))
                 OR (OLD.state = 'claimed' AND NEW.state IN ('running', 'failed', 'cancelled'))
                 OR (OLD.state = 'running' AND NEW.state IN ('succeeded', 'failed', 'cancelled'))
             )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_runtime_job_state_transition_forbidden');
        END
        """,
    ),
)

DESKTOP_MIGRATIONS = (DESKTOP_0001,)
