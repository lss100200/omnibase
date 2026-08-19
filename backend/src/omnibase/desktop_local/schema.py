"""Transactional SQLite migration definitions for the desktop-local store."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

DESKTOP_APPLICATION_ID = 0x4F4D4E42  # ASCII "OMNB"
DESKTOP_SCHEMA_VERSION = 2


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

DESKTOP_0002 = DesktopMigration(
    version=2,
    migration_id="desktop_0002_provider_conversation",
    statements=(
        """
        CREATE TABLE provider (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 128),
            owner_id TEXT NOT NULL,
            display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 256),
            base_url TEXT NOT NULL CHECK (length(base_url) BETWEEN 8 AND 2048),
            model_name TEXT NOT NULL CHECK (length(model_name) BETWEEN 1 AND 256),
            family TEXT NOT NULL CHECK (
                family IN (
                    'deepseek',
                    'openai',
                    'anthropic',
                    'glm',
                    'kimi',
                    'generic-openai-compatible'
                )
            ),
            gear TEXT NOT NULL CHECK (gear IN ('economy', 'standard', 'deep', 'audit')),
            thinking_depth TEXT NOT NULL CHECK (
                thinking_depth IN ('disabled', 'low', 'medium', 'high')
            ),
            timeout_seconds INTEGER NOT NULL CHECK (timeout_seconds BETWEEN 5 AND 120),
            allow_loopback_http INTEGER NOT NULL CHECK (allow_loopback_http IN (0, 1)),
            is_default INTEGER NOT NULL CHECK (is_default IN (0, 1)),
            is_enabled INTEGER NOT NULL CHECK (is_enabled IN (0, 1)),
            credential_reference TEXT NOT NULL CHECK (
                length(credential_reference) BETWEEN 1 AND 128
            ),
            encrypted_secret_blob TEXT NOT NULL CHECK (
                length(encrypted_secret_blob) BETWEEN 1 AND 8192
            ),
            secret_fingerprint TEXT NOT NULL CHECK (
                length(secret_fingerprint) = 64
                AND secret_fingerprint NOT GLOB '*[^0-9a-f]*'
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (owner_id) REFERENCES owner(id) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE UNIQUE INDEX provider_one_default_per_owner
        ON provider(owner_id)
        WHERE is_default = 1
        """,
        """
        CREATE TABLE workspace_agent (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 128),
            owner_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role = 'parent'),
            display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 256),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (workspace_id, role),
            FOREIGN KEY (workspace_id, owner_id)
                REFERENCES workspace(id, owner_id) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE conversation (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 128),
            owner_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 256),
            state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'archived')),
            row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (id, owner_id, workspace_id),
            FOREIGN KEY (workspace_id, owner_id)
                REFERENCES workspace(id, owner_id) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE invocation (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 128),
            owner_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            requested_model TEXT NOT NULL CHECK (length(requested_model) BETWEEN 1 AND 256),
            actual_model TEXT CHECK (
                actual_model IS NULL OR length(actual_model) BETWEEN 1 AND 256
            ),
            family TEXT NOT NULL CHECK (
                family IN (
                    'deepseek',
                    'openai',
                    'anthropic',
                    'glm',
                    'kimi',
                    'generic-openai-compatible'
                )
            ),
            gear TEXT NOT NULL CHECK (gear IN ('economy', 'standard', 'deep', 'audit')),
            thinking_depth TEXT NOT NULL CHECK (
                thinking_depth IN ('disabled', 'low', 'medium', 'high')
            ),
            status TEXT NOT NULL CHECK (
                status IN ('running', 'succeeded', 'failed', 'cancelled', 'unknown')
            ),
            duration_ms INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
            input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
            output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
            total_tokens INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0),
            error_code TEXT CHECK (
                error_code IS NULL
                OR (
                    length(error_code) BETWEEN 3 AND 96
                    AND error_code NOT GLOB '*[^a-z0-9_]*'
                )
            ),
            error_redacted TEXT CHECK (
                error_redacted IS NULL OR length(error_redacted) BETWEEN 1 AND 256
            ),
            retry_of_invocation_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (conversation_id, owner_id, workspace_id)
                REFERENCES conversation(id, owner_id, workspace_id) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE message (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 128),
            owner_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL CHECK (length(content) <= 131072),
            status TEXT NOT NULL CHECK (
                status IN ('streaming', 'completed', 'cancelled', 'failed', 'unknown')
            ),
            invocation_id TEXT,
            retry_of_message_id TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (conversation_id, owner_id, workspace_id)
                REFERENCES conversation(id, owner_id, workspace_id) ON DELETE RESTRICT,
            FOREIGN KEY (invocation_id) REFERENCES invocation(id) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        INSERT INTO workspace_agent (
            id, owner_id, workspace_id, role, display_name, created_at, updated_at
        )
        SELECT
            'agent_' || lower(hex(randomblob(16))),
            owner_id,
            id,
            'parent',
            '父 Agent',
            created_at,
            updated_at
        FROM workspace
        """,
        """
        CREATE TRIGGER workspace_parent_agent_insert
        AFTER INSERT ON workspace
        BEGIN
            INSERT INTO workspace_agent (
                id, owner_id, workspace_id, role, display_name, created_at, updated_at
            ) VALUES (
                'agent_' || lower(hex(randomblob(16))),
                NEW.owner_id,
                NEW.id,
                'parent',
                '父 Agent',
                NEW.created_at,
                NEW.updated_at
            );
        END
        """,
        """
        CREATE TRIGGER conversation_state_transition_guard
        BEFORE UPDATE OF state ON conversation
        WHEN NEW.state <> OLD.state
             AND NOT (OLD.state = 'active' AND NEW.state = 'archived')
        BEGIN
            SELECT RAISE(ABORT, 'desktop_conversation_state_transition_forbidden');
        END
        """,
        """
        CREATE TRIGGER conversation_identity_guard
        BEFORE UPDATE ON conversation
        WHEN NEW.id <> OLD.id
          OR NEW.owner_id <> OLD.owner_id
          OR NEW.workspace_id <> OLD.workspace_id
          OR NEW.created_at <> OLD.created_at
        BEGIN
            SELECT RAISE(ABORT, 'desktop_conversation_identity_immutable');
        END
        """,
        """
        CREATE TRIGGER invocation_identity_guard
        BEFORE UPDATE ON invocation
        WHEN NEW.id <> OLD.id
          OR NEW.owner_id <> OLD.owner_id
          OR NEW.workspace_id <> OLD.workspace_id
          OR NEW.conversation_id <> OLD.conversation_id
          OR NEW.created_at <> OLD.created_at
        BEGIN
            SELECT RAISE(ABORT, 'desktop_invocation_identity_immutable');
        END
        """,
        """
        CREATE TRIGGER invocation_state_transition_guard
        BEFORE UPDATE OF status ON invocation
        WHEN NEW.status <> OLD.status
             AND NOT (
                 (OLD.status = 'running'
                    AND NEW.status IN ('succeeded', 'failed', 'cancelled', 'unknown'))
             )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_invocation_state_transition_forbidden');
        END
        """,
    ),
)

DESKTOP_MIGRATIONS = (DESKTOP_0001, DESKTOP_0002)
