"""Transactional SQLite migration definitions for the desktop-local store."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from omnibase.desktop_local.components.catalog import SEEDED_COMPONENT_VERSIONS, canonical_json

DESKTOP_APPLICATION_ID = 0x4F4D4E42  # ASCII "OMNB"
DESKTOP_SCHEMA_VERSION = 11

STANDARD_WORKBENCH_PROFILE = {
    "appearance": {"density": "inherit", "quiet_chrome": True},
    "layout": {
        "agent_panel": "open",
        "bottom_panel": "hidden",
        "focus_mode": False,
        "sidebar": "explorer",
    },
    "schema_version": 1,
    "slots": {
        "agent.rail": True,
        "conversation.transcript": True,
        "event.agent-log": True,
        "event.output": True,
        "knowledge.ebook": False,
        "mcp.catalog": False,
        "provider.settings": True,
        "run.history": True,
        "sandbox.runtime": False,
        "settings.center": True,
        "skills.catalog": False,
        "source-control": False,
        "terminal": False,
        "workspace.brief": True,
        "workspace.explorer": True,
    },
    "template": {"id": "standard-workbench", "version": 1},
}
STANDARD_WORKBENCH_PROFILE_JSON = json.dumps(
    STANDARD_WORKBENCH_PROFILE,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
STANDARD_WORKBENCH_PROFILE_SHA256 = hashlib.sha256(
    STANDARD_WORKBENCH_PROFILE_JSON.encode("utf-8")
).hexdigest()


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

_SPECIALIST_ROLE_SQL = (
    "'product', 'ux', 'frontend', 'backend', 'data', 'security', 'qa', 'operations', 'docs'"
)
_EMPLOYEE_ROLE_SQL = f"'parent', {_SPECIALIST_ROLE_SQL}"

DESKTOP_0003 = DesktopMigration(
    version=3,
    migration_id="desktop_0003_personal_agent_team",
    statements=(
        f"""
        CREATE TABLE workspace_agent_role_config (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 128),
            owner_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            employee_role_id TEXT NOT NULL CHECK (
                employee_role_id IN ({_EMPLOYEE_ROLE_SQL})
            ),
            provider_id TEXT,
            model_name_override TEXT CHECK (
                model_name_override IS NULL
                OR length(model_name_override) BETWEEN 1 AND 256
            ),
            gear TEXT NOT NULL CHECK (gear IN ('economy', 'standard', 'deep', 'audit')),
            thinking_depth TEXT NOT NULL CHECK (
                thinking_depth IN ('disabled', 'low', 'medium', 'high')
            ),
            row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
            verification_state TEXT NOT NULL DEFAULT 'unverified' CHECK (
                verification_state IN ('unverified', 'binding_recorded', 'stale')
            ),
            verified_actual_model TEXT CHECK (
                verified_actual_model IS NULL
                OR length(verified_actual_model) BETWEEN 1 AND 256
            ),
            verification_digest TEXT CHECK (
                verification_digest IS NULL
                OR (
                    length(verification_digest) = 64
                    AND verification_digest NOT GLOB '*[^0-9a-f]*'
                )
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (workspace_id, employee_role_id),
            FOREIGN KEY (workspace_id, owner_id)
                REFERENCES workspace(id, owner_id) ON DELETE RESTRICT,
            FOREIGN KEY (provider_id) REFERENCES provider(id) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE team_run (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 128),
            owner_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            mode TEXT NOT NULL CHECK (mode IN ('single', 'team')),
            state TEXT NOT NULL CHECK (
                state IN (
                    'preparing', 'running', 'cancelling', 'succeeded', 'failed',
                    'cancelled', 'unknown', 'budget_exhausted', 'cannot_complete'
                )
            ),
            staffing_authority TEXT NOT NULL CHECK (staffing_authority = 'parent_proposal'),
            current_plan_revision_id TEXT,
            dispatched_participant_count INTEGER CHECK (
                dispatched_participant_count IS NULL
                OR dispatched_participant_count BETWEEN 0 AND 10
            ),
            current_wave_id TEXT,
            maximum_provider_calls INTEGER NOT NULL CHECK (
                maximum_provider_calls BETWEEN 1 AND 128
            ),
            maximum_wall_time_ms INTEGER NOT NULL CHECK (
                maximum_wall_time_ms BETWEEN 1000 AND 3600000
            ),
            maximum_concurrent_calls INTEGER NOT NULL CHECK (
                maximum_concurrent_calls BETWEEN 1 AND 9
            ),
            maximum_input_characters INTEGER NOT NULL CHECK (
                maximum_input_characters BETWEEN 1 AND 131072
            ),
            maximum_output_characters INTEGER NOT NULL CHECK (
                maximum_output_characters BETWEEN 1 AND 131072
            ),
            consumed_provider_calls INTEGER NOT NULL DEFAULT 0 CHECK (
                consumed_provider_calls >= 0
            ),
            task_text TEXT NOT NULL CHECK (length(task_text) BETWEEN 1 AND 16384),
            allowed_specialist_role_ids TEXT NOT NULL CHECK (
                json_valid(allowed_specialist_role_ids) = 1
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (id, owner_id, workspace_id),
            FOREIGN KEY (conversation_id, owner_id, workspace_id)
                REFERENCES conversation(id, owner_id, workspace_id) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE UNIQUE INDEX team_run_one_live_per_conversation
        ON team_run(conversation_id)
        WHERE state IN ('preparing', 'running', 'cancelling')
        """,
        """
        CREATE TABLE team_plan_revision (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 128),
            team_run_id TEXT NOT NULL,
            revision_ordinal INTEGER NOT NULL CHECK (revision_ordinal >= 1),
            decision TEXT NOT NULL CHECK (
                decision IN (
                    'answer_directly', 'delegate', 'continue',
                    'request_followup', 'finish', 'cannot_complete'
                )
            ),
            proposal_json TEXT NOT NULL CHECK (
                json_valid(proposal_json) = 1
                AND length(proposal_json) BETWEEN 2 AND 65536
            ),
            proposal_json_sha256 TEXT NOT NULL CHECK (
                length(proposal_json_sha256) = 64
                AND proposal_json_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            validated INTEGER NOT NULL CHECK (validated IN (0, 1)),
            validation_error_code TEXT CHECK (
                validation_error_code IS NULL
                OR (
                    length(validation_error_code) BETWEEN 3 AND 96
                    AND validation_error_code NOT GLOB '*[^a-z0-9_]*'
                )
            ),
            created_at TEXT NOT NULL,
            UNIQUE (team_run_id, revision_ordinal),
            FOREIGN KEY (team_run_id) REFERENCES team_run(id) ON DELETE RESTRICT
        ) STRICT
        """,
        f"""
        CREATE TABLE team_assignment (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 128),
            team_run_id TEXT NOT NULL,
            plan_revision_id TEXT NOT NULL,
            wave_id TEXT NOT NULL CHECK (length(wave_id) BETWEEN 1 AND 128),
            assignment_id TEXT NOT NULL CHECK (length(assignment_id) BETWEEN 1 AND 128),
            employee_role_id TEXT NOT NULL CHECK (
                employee_role_id IN ({_SPECIALIST_ROLE_SQL})
            ),
            objective TEXT NOT NULL CHECK (length(objective) BETWEEN 1 AND 16384),
            depends_on_assignment_ids TEXT NOT NULL CHECK (
                json_valid(depends_on_assignment_ids) = 1
            ),
            expected_output TEXT NOT NULL CHECK (length(expected_output) BETWEEN 1 AND 16384),
            context_requirements TEXT NOT NULL CHECK (json_valid(context_requirements) = 1),
            declared_execution TEXT NOT NULL CHECK (
                declared_execution IN ('serial', 'parallel')
            ),
            effective_execution TEXT NOT NULL CHECK (
                effective_execution IN ('serial', 'parallel')
            ),
            state TEXT NOT NULL CHECK (
                state IN (
                    'pending', 'ready', 'running', 'completed', 'failed',
                    'cancelled', 'blocked', 'needs_collaboration'
                )
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (team_run_id, assignment_id),
            FOREIGN KEY (team_run_id) REFERENCES team_run(id) ON DELETE RESTRICT,
            FOREIGN KEY (plan_revision_id) REFERENCES team_plan_revision(id) ON DELETE RESTRICT
        ) STRICT
        """,
        f"""
        CREATE TABLE team_node (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 128),
            team_run_id TEXT NOT NULL,
            assignment_id TEXT NOT NULL CHECK (length(assignment_id) BETWEEN 1 AND 128),
            ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
            employee_role_id TEXT NOT NULL CHECK (
                employee_role_id IN ({_SPECIALIST_ROLE_SQL})
            ),
            invocation_id TEXT,
            state TEXT NOT NULL CHECK (
                state IN ('pending', 'running', 'succeeded', 'failed', 'cancelled', 'unknown')
            ),
            provider_id TEXT,
            requested_model TEXT CHECK (
                requested_model IS NULL OR length(requested_model) BETWEEN 1 AND 256
            ),
            actual_model TEXT CHECK (
                actual_model IS NULL OR length(actual_model) BETWEEN 1 AND 256
            ),
            input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
            output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
            total_tokens INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0),
            answer_sha256 TEXT CHECK (
                answer_sha256 IS NULL
                OR (
                    length(answer_sha256) = 64
                    AND answer_sha256 NOT GLOB '*[^0-9a-f]*'
                )
            ),
            error_code TEXT CHECK (
                error_code IS NULL
                OR (
                    length(error_code) BETWEEN 3 AND 96
                    AND error_code NOT GLOB '*[^a-z0-9_]*'
                )
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (team_run_id, assignment_id, ordinal),
            FOREIGN KEY (team_run_id) REFERENCES team_run(id) ON DELETE RESTRICT
        ) STRICT
        """,
        f"""
        CREATE TABLE team_collaboration_request (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 128),
            team_run_id TEXT NOT NULL,
            from_assignment_id TEXT NOT NULL CHECK (length(from_assignment_id) BETWEEN 1 AND 128),
            from_employee_role_id TEXT NOT NULL CHECK (
                from_employee_role_id IN ({_SPECIALIST_ROLE_SQL})
            ),
            target_role_id TEXT NOT NULL CHECK (
                target_role_id IN ({_SPECIALIST_ROLE_SQL})
            ),
            question TEXT NOT NULL CHECK (length(question) BETWEEN 1 AND 16384),
            reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 16384),
            parent_decision TEXT NOT NULL DEFAULT 'pending' CHECK (
                parent_decision IN (
                    'pending', 'accept_start', 'handle_self', 'merge_existing', 'decline'
                )
            ),
            resolved_assignment_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (team_run_id) REFERENCES team_run(id) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TRIGGER team_run_identity_guard
        BEFORE UPDATE ON team_run
        WHEN NEW.id <> OLD.id
          OR NEW.owner_id <> OLD.owner_id
          OR NEW.workspace_id <> OLD.workspace_id
          OR NEW.conversation_id <> OLD.conversation_id
          OR NEW.created_at <> OLD.created_at
          OR NEW.staffing_authority <> OLD.staffing_authority
        BEGIN
            SELECT RAISE(ABORT, 'desktop_team_run_identity_immutable');
        END
        """,
        """
        CREATE TRIGGER team_run_state_transition_guard
        BEFORE UPDATE OF state ON team_run
        WHEN NEW.state <> OLD.state
             AND NOT (
                 (OLD.state = 'preparing'
                    AND NEW.state IN (
                        'running', 'cancelled', 'failed', 'cannot_complete', 'unknown'
                    ))
                 OR (OLD.state = 'running'
                    AND NEW.state IN (
                        'cancelling', 'succeeded', 'failed', 'cancelled',
                        'unknown', 'budget_exhausted', 'cannot_complete'
                    ))
                 OR (OLD.state = 'cancelling'
                    AND NEW.state IN ('cancelled', 'failed', 'unknown'))
             )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_team_run_state_transition_forbidden');
        END
        """,
        """
        CREATE TRIGGER team_plan_revision_immutable
        BEFORE UPDATE ON team_plan_revision
        BEGIN
            SELECT RAISE(ABORT, 'desktop_team_plan_revision_immutable');
        END
        """,
        """
        CREATE TRIGGER team_plan_revision_delete_forbidden
        BEFORE DELETE ON team_plan_revision
        BEGIN
            SELECT RAISE(ABORT, 'desktop_team_plan_revision_immutable');
        END
        """,
        """
        CREATE TRIGGER workspace_agent_role_config_identity_guard
        BEFORE UPDATE ON workspace_agent_role_config
        WHEN NEW.id <> OLD.id
          OR NEW.owner_id <> OLD.owner_id
          OR NEW.workspace_id <> OLD.workspace_id
          OR NEW.employee_role_id <> OLD.employee_role_id
          OR NEW.created_at <> OLD.created_at
        BEGIN
            SELECT RAISE(ABORT, 'desktop_role_config_identity_immutable');
        END
        """,
    ),
)

DESKTOP_0004 = DesktopMigration(
    version=4,
    migration_id="desktop_0004_personal_team_runtime",
    statements=(
        """
        ALTER TABLE team_node ADD COLUMN wave_id TEXT
        """,
        """
        ALTER TABLE team_node ADD COLUMN node_epoch INTEGER
        """,
        """
        ALTER TABLE team_node ADD COLUMN send_epoch INTEGER
        """,
        """
        ALTER TABLE team_node ADD COLUMN duration_ms INTEGER
        """,
        """
        ALTER TABLE team_run ADD COLUMN parent_final_answer TEXT
        """,
        """
        CREATE UNIQUE INDEX team_node_invocation_unique
        ON team_node(invocation_id)
        WHERE invocation_id IS NOT NULL
        """,
        f"""
        CREATE TABLE team_employee_report (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 128),
            team_run_id TEXT NOT NULL,
            assignment_id TEXT NOT NULL CHECK (length(assignment_id) BETWEEN 1 AND 128),
            node_id TEXT NOT NULL CHECK (length(node_id) BETWEEN 1 AND 128),
            invocation_id TEXT NOT NULL CHECK (length(invocation_id) BETWEEN 1 AND 128),
            employee_role_id TEXT NOT NULL CHECK (
                employee_role_id IN ({_SPECIALIST_ROLE_SQL})
            ),
            status TEXT NOT NULL CHECK (
                status IN ('completed', 'needs_collaboration', 'blocked')
            ),
            report TEXT NOT NULL CHECK (length(report) BETWEEN 1 AND 131072),
            report_sha256 TEXT NOT NULL CHECK (
                length(report_sha256) = 64
                AND report_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            created_at TEXT NOT NULL,
            UNIQUE (team_run_id, assignment_id, node_id),
            UNIQUE (invocation_id),
            FOREIGN KEY (team_run_id) REFERENCES team_run(id) ON DELETE RESTRICT,
            FOREIGN KEY (node_id) REFERENCES team_node(id) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TRIGGER team_employee_report_immutable
        BEFORE UPDATE ON team_employee_report
        BEGIN
            SELECT RAISE(ABORT, 'desktop_team_employee_report_immutable');
        END
        """,
        """
        CREATE TRIGGER team_employee_report_delete_forbidden
        BEFORE DELETE ON team_employee_report
        BEGIN
            SELECT RAISE(ABORT, 'desktop_team_employee_report_immutable');
        END
        """,
    ),
)

DESKTOP_0005 = DesktopMigration(
    version=5,
    migration_id="desktop_0005_team_node_identity_epochs",
    statements=(
        """
        CREATE UNIQUE INDEX team_node_node_epoch_unique
        ON team_node(team_run_id, node_epoch)
        WHERE node_epoch IS NOT NULL
        """,
        """
        CREATE UNIQUE INDEX team_node_send_epoch_unique
        ON team_node(team_run_id, send_epoch)
        WHERE send_epoch IS NOT NULL
        """,
        """
        CREATE TRIGGER team_node_terminal_immutable
        BEFORE UPDATE ON team_node
        WHEN OLD.state IN ('succeeded', 'failed', 'cancelled', 'unknown')
        BEGIN
            SELECT RAISE(ABORT, 'desktop_team_node_terminal_immutable');
        END
        """,
    ),
)

DESKTOP_0006 = DesktopMigration(
    version=6,
    migration_id="desktop_0006_report_collaboration_digest",
    statements=(
        """
        ALTER TABLE team_employee_report ADD COLUMN collaboration_requests_sha256 TEXT
            CHECK (
                collaboration_requests_sha256 IS NULL
                OR (
                    length(collaboration_requests_sha256) = 64
                    AND collaboration_requests_sha256 NOT GLOB '*[^0-9a-f]*'
                )
            )
        """,
    ),
)

DESKTOP_0007 = DesktopMigration(
    version=7,
    migration_id="desktop_0007_recovery_success_downgrade",
    statements=(
        """
        DROP TRIGGER team_run_state_transition_guard
        """,
        """
        CREATE TRIGGER team_run_state_transition_guard
        BEFORE UPDATE OF state ON team_run
        WHEN NEW.state <> OLD.state
             AND NOT (
                 (OLD.state = 'preparing'
                    AND NEW.state IN (
                        'running', 'cancelled', 'failed', 'cannot_complete', 'unknown'
                    ))
                 OR (OLD.state = 'running'
                    AND NEW.state IN (
                        'cancelling', 'succeeded', 'failed', 'cancelled',
                        'unknown', 'budget_exhausted', 'cannot_complete'
                    ))
                 OR (OLD.state = 'cancelling'
                    AND NEW.state IN ('cancelled', 'failed', 'unknown'))
                 OR (OLD.state = 'succeeded'
                    AND NEW.state = 'unknown')
             )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_team_run_state_transition_forbidden');
        END
        """,
    ),
)

DESKTOP_0008 = DesktopMigration(
    version=8,
    migration_id="desktop_0008_collaboration_report_binding",
    statements=(
        """
        ALTER TABLE team_collaboration_request ADD COLUMN report_id TEXT
        """,
    ),
)

DESKTOP_0009 = DesktopMigration(
    version=9,
    migration_id="desktop_0009_parent_call_proof",
    statements=(
        """
        CREATE UNIQUE INDEX team_plan_revision_run_identity_unique
        ON team_plan_revision(id, team_run_id)
        """,
        """
        CREATE TABLE team_provider_call_reservation (
            invocation_id TEXT PRIMARY KEY CHECK (
                length(invocation_id) = 43
                AND invocation_id GLOB 'invocation_[0-9a-f]*'
                AND substr(invocation_id, 12) NOT GLOB '*[^0-9a-f]*'
            ),
            team_run_id TEXT NOT NULL,
            purpose TEXT NOT NULL CHECK (
                purpose IN (
                    'parent-propose', 'parent-replan', 'parent-synthesize', 'employee'
                )
            ),
            provider_id TEXT NOT NULL CHECK (
                length(provider_id) = 41
                AND provider_id GLOB 'provider_[0-9a-f]*'
                AND substr(provider_id, 10) NOT GLOB '*[^0-9a-f]*'
            ),
            requested_model TEXT NOT NULL CHECK (length(requested_model) BETWEEN 1 AND 256),
            created_at TEXT NOT NULL,
            UNIQUE (invocation_id, team_run_id, purpose, provider_id, requested_model),
            FOREIGN KEY (team_run_id) REFERENCES team_run(id) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        INSERT INTO team_provider_call_reservation (
            invocation_id, team_run_id, purpose, provider_id, requested_model, created_at
        )
        SELECT invocation_id, team_run_id, 'employee', provider_id, requested_model, created_at
        FROM team_node
        WHERE invocation_id IS NOT NULL
          AND provider_id IS NOT NULL
          AND requested_model IS NOT NULL
          AND length(invocation_id) = 43
          AND invocation_id GLOB 'invocation_[0-9a-f]*'
          AND substr(invocation_id, 12) NOT GLOB '*[^0-9a-f]*'
          AND length(provider_id) = 41
          AND provider_id GLOB 'provider_[0-9a-f]*'
          AND substr(provider_id, 10) NOT GLOB '*[^0-9a-f]*'
          AND length(requested_model) BETWEEN 1 AND 256
        """,
        """
        CREATE TRIGGER team_node_provider_call_reservation_required
        BEFORE INSERT ON team_node
        WHEN NOT EXISTS (
            SELECT 1
            FROM team_provider_call_reservation AS reservation
            WHERE reservation.invocation_id = NEW.invocation_id
              AND reservation.team_run_id = NEW.team_run_id
              AND reservation.purpose = 'employee'
              AND reservation.provider_id = NEW.provider_id
              AND reservation.requested_model = NEW.requested_model
        )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_team_node_provider_call_reservation_required');
        END
        """,
        """
        CREATE TRIGGER team_node_identity_guard
        BEFORE UPDATE ON team_node
        WHEN NEW.id <> OLD.id
          OR NEW.team_run_id <> OLD.team_run_id
          OR NEW.assignment_id <> OLD.assignment_id
          OR NEW.ordinal <> OLD.ordinal
          OR NEW.employee_role_id <> OLD.employee_role_id
          OR NEW.invocation_id IS NOT OLD.invocation_id
          OR NEW.provider_id IS NOT OLD.provider_id
          OR NEW.requested_model IS NOT OLD.requested_model
          OR NEW.created_at <> OLD.created_at
          OR NEW.wave_id IS NOT OLD.wave_id
          OR NEW.node_epoch IS NOT OLD.node_epoch
          OR NEW.send_epoch IS NOT OLD.send_epoch
        BEGIN
            SELECT RAISE(ABORT, 'desktop_team_node_identity_immutable');
        END
        """,
        """
        CREATE TRIGGER team_provider_call_reservation_update_forbidden
        BEFORE UPDATE ON team_provider_call_reservation
        BEGIN
            SELECT RAISE(ABORT, 'desktop_team_provider_call_reservation_immutable');
        END
        """,
        """
        CREATE TRIGGER team_provider_call_reservation_delete_forbidden
        BEFORE DELETE ON team_provider_call_reservation
        BEGIN
            SELECT RAISE(ABORT, 'desktop_team_provider_call_reservation_immutable');
        END
        """,
        """
        CREATE TABLE team_parent_call (
            invocation_id TEXT PRIMARY KEY CHECK (
                length(invocation_id) = 43
                AND invocation_id GLOB 'invocation_[0-9a-f]*'
                AND substr(invocation_id, 12) NOT GLOB '*[^0-9a-f]*'
            ),
            team_run_id TEXT NOT NULL,
            plan_revision_id TEXT,
            purpose TEXT NOT NULL CHECK (
                purpose IN ('parent-propose', 'parent-replan', 'parent-synthesize')
            ),
            state TEXT NOT NULL DEFAULT 'pending' CHECK (
                state IN ('pending', 'succeeded', 'failed', 'cancelled', 'unknown')
            ),
            provider_id TEXT NOT NULL CHECK (
                length(provider_id) = 41
                AND provider_id GLOB 'provider_[0-9a-f]*'
                AND substr(provider_id, 10) NOT GLOB '*[^0-9a-f]*'
            ),
            requested_model TEXT NOT NULL CHECK (length(requested_model) BETWEEN 1 AND 256),
            actual_model TEXT CHECK (
                actual_model IS NULL OR length(actual_model) BETWEEN 1 AND 256
            ),
            input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
            output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
            total_tokens INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0),
            output_sha256 TEXT CHECK (
                output_sha256 IS NULL
                OR (
                    length(output_sha256) = 64
                    AND output_sha256 NOT GLOB '*[^0-9a-f]*'
                )
            ),
            error_code TEXT CHECK (
                error_code IS NULL
                OR (
                    length(error_code) BETWEEN 3 AND 96
                    AND error_code NOT GLOB '*[^a-z0-9_]*'
                )
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (team_run_id, invocation_id),
            FOREIGN KEY (team_run_id) REFERENCES team_run(id) ON DELETE RESTRICT,
            FOREIGN KEY (plan_revision_id, team_run_id)
                REFERENCES team_plan_revision(id, team_run_id) ON DELETE RESTRICT,
            FOREIGN KEY (
                invocation_id, team_run_id, purpose, provider_id, requested_model
            ) REFERENCES team_provider_call_reservation(
                invocation_id, team_run_id, purpose, provider_id, requested_model
            ) ON DELETE RESTRICT,
            CHECK (
                (state = 'pending'
                    AND actual_model IS NULL
                    AND input_tokens IS NULL
                    AND output_tokens IS NULL
                    AND total_tokens IS NULL
                    AND output_sha256 IS NULL
                    AND error_code IS NULL)
                OR (state = 'succeeded'
                    AND plan_revision_id IS NOT NULL
                    AND actual_model = requested_model
                    AND output_sha256 IS NOT NULL
                    AND error_code IS NULL)
                OR (state IN ('failed', 'cancelled', 'unknown')
                    AND output_sha256 IS NULL
                    AND error_code IS NOT NULL)
            )
        ) STRICT
        """,
        """
        CREATE INDEX team_parent_call_success_proof
        ON team_parent_call(team_run_id, purpose, plan_revision_id, state, output_sha256)
        """,
        """
        CREATE TRIGGER team_parent_call_identity_guard
        BEFORE UPDATE ON team_parent_call
        WHEN NEW.invocation_id <> OLD.invocation_id
          OR NEW.team_run_id <> OLD.team_run_id
          OR NEW.purpose <> OLD.purpose
          OR NEW.provider_id <> OLD.provider_id
          OR NEW.requested_model <> OLD.requested_model
          OR NEW.created_at <> OLD.created_at
        BEGIN
            SELECT RAISE(ABORT, 'desktop_team_parent_call_identity_immutable');
        END
        """,
        """
        CREATE TRIGGER team_parent_call_terminal_immutable
        BEFORE UPDATE ON team_parent_call
        WHEN OLD.state IN ('succeeded', 'failed', 'cancelled', 'unknown')
        BEGIN
            SELECT RAISE(ABORT, 'desktop_team_parent_call_terminal_immutable');
        END
        """,
        """
        CREATE TRIGGER team_parent_call_delete_forbidden
        BEFORE DELETE ON team_parent_call
        BEGIN
            SELECT RAISE(ABORT, 'desktop_team_parent_call_terminal_immutable');
        END
        """,
    ),
)

DESKTOP_0010 = DesktopMigration(
    version=10,
    migration_id="desktop_0010_workspace_composition",
    statements=(
        """
        CREATE TABLE owner_workbench_preference (
            owner_id TEXT PRIMARY KEY,
            density TEXT NOT NULL DEFAULT 'compact' CHECK (
                density IN ('compact', 'comfortable')
            ),
            reduce_motion INTEGER NOT NULL DEFAULT 0 CHECK (reduce_motion IN (0, 1)),
            row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (owner_id) REFERENCES owner(id) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TRIGGER owner_workbench_preference_identity_guard
        BEFORE UPDATE ON owner_workbench_preference
        WHEN NEW.owner_id <> OLD.owner_id
          OR NEW.created_at <> OLD.created_at
          OR NEW.row_version <> OLD.row_version + 1
        BEGIN
            SELECT RAISE(ABORT, 'desktop_workbench_preference_identity_drift');
        END
        """,
        """
        CREATE TRIGGER owner_workbench_preference_delete_forbidden
        BEFORE DELETE ON owner_workbench_preference
        BEGIN
            SELECT RAISE(ABORT, 'desktop_workbench_preference_delete_forbidden');
        END
        """,
        """
        CREATE TABLE workspace_composition_revision (
            workspace_id TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK (revision >= 1),
            template_id TEXT NOT NULL CHECK (template_id = 'standard-workbench'),
            template_version INTEGER NOT NULL CHECK (template_version = 1),
            profile_json TEXT NOT NULL CHECK (json_valid(profile_json) = 1),
            profile_sha256 TEXT NOT NULL CHECK (
                length(profile_sha256) = 64
                AND profile_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            source_kind TEXT NOT NULL CHECK (
                source_kind IN ('system', 'owner', 'assistant', 'rollback')
            ),
            proposal_id TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (workspace_id, revision),
            UNIQUE (workspace_id, owner_id, revision),
            FOREIGN KEY (workspace_id, owner_id)
                REFERENCES workspace(id, owner_id) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE workspace_composition_proposal (
            id TEXT PRIMARY KEY CHECK (
                length(id) = 41
                AND id GLOB 'proposal_[0-9a-f]*'
                AND substr(id, 10) NOT GLOB '*[^0-9a-f]*'
            ),
            workspace_id TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            base_revision INTEGER NOT NULL CHECK (base_revision >= 1),
            base_profile_sha256 TEXT NOT NULL CHECK (
                length(base_profile_sha256) = 64
                AND base_profile_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            source_kind TEXT NOT NULL CHECK (
                source_kind IN ('owner', 'assistant', 'rollback')
            ),
            source_reference TEXT CHECK (
                source_reference IS NULL OR length(source_reference) BETWEEN 1 AND 128
            ),
            desired_profile_json TEXT NOT NULL CHECK (json_valid(desired_profile_json) = 1),
            desired_profile_sha256 TEXT NOT NULL CHECK (
                length(desired_profile_sha256) = 64
                AND desired_profile_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            request_sha256 TEXT NOT NULL UNIQUE CHECK (
                length(request_sha256) = 64
                AND request_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            created_at TEXT NOT NULL,
            UNIQUE (id, workspace_id),
            FOREIGN KEY (workspace_id, owner_id)
                REFERENCES workspace(id, owner_id) ON DELETE RESTRICT,
            FOREIGN KEY (workspace_id, base_revision)
                REFERENCES workspace_composition_revision(workspace_id, revision)
                ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE workspace_composition_current (
            workspace_id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK (revision >= 1),
            profile_sha256 TEXT NOT NULL CHECK (
                length(profile_sha256) = 64
                AND profile_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (workspace_id, owner_id),
            FOREIGN KEY (workspace_id, owner_id)
                REFERENCES workspace(id, owner_id) ON DELETE RESTRICT,
            FOREIGN KEY (workspace_id, revision)
                REFERENCES workspace_composition_revision(workspace_id, revision)
                ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE workspace_composition_decision (
            proposal_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
            request_sha256 TEXT NOT NULL CHECK (
                length(request_sha256) = 64
                AND request_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            decided_by TEXT NOT NULL CHECK (decided_by = 'owner'),
            applied_revision INTEGER,
            decided_at TEXT NOT NULL,
            UNIQUE (proposal_id, workspace_id),
            FOREIGN KEY (proposal_id, workspace_id)
                REFERENCES workspace_composition_proposal(id, workspace_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (workspace_id, applied_revision)
                REFERENCES workspace_composition_revision(workspace_id, revision)
                ON DELETE RESTRICT,
            CHECK (
                (decision = 'approved' AND applied_revision IS NOT NULL)
                OR (decision = 'rejected' AND applied_revision IS NULL)
            )
        ) STRICT
        """,
        """
        CREATE TRIGGER workspace_composition_revision_update_forbidden
        BEFORE UPDATE ON workspace_composition_revision
        BEGIN
            SELECT RAISE(ABORT, 'desktop_composition_revision_immutable');
        END
        """,
        """
        CREATE TRIGGER workspace_composition_revision_delete_forbidden
        BEFORE DELETE ON workspace_composition_revision
        BEGIN
            SELECT RAISE(ABORT, 'desktop_composition_revision_immutable');
        END
        """,
        """
        CREATE TRIGGER workspace_composition_proposal_update_forbidden
        BEFORE UPDATE ON workspace_composition_proposal
        BEGIN
            SELECT RAISE(ABORT, 'desktop_composition_proposal_immutable');
        END
        """,
        """
        CREATE TRIGGER workspace_composition_proposal_delete_forbidden
        BEFORE DELETE ON workspace_composition_proposal
        BEGIN
            SELECT RAISE(ABORT, 'desktop_composition_proposal_immutable');
        END
        """,
        """
        CREATE TRIGGER workspace_composition_decision_update_forbidden
        BEFORE UPDATE ON workspace_composition_decision
        BEGIN
            SELECT RAISE(ABORT, 'desktop_composition_decision_immutable');
        END
        """,
        """
        CREATE TRIGGER workspace_composition_decision_delete_forbidden
        BEFORE DELETE ON workspace_composition_decision
        BEGIN
            SELECT RAISE(ABORT, 'desktop_composition_decision_immutable');
        END
        """,
        """
        CREATE TRIGGER workspace_composition_current_identity_guard
        BEFORE UPDATE ON workspace_composition_current
        WHEN NEW.workspace_id <> OLD.workspace_id
          OR NEW.owner_id <> OLD.owner_id
          OR NEW.created_at <> OLD.created_at
          OR NEW.revision <> OLD.revision + 1
          OR NOT EXISTS (
              SELECT 1
              FROM workspace_composition_revision AS revision
              JOIN workspace_composition_proposal AS proposal
                ON proposal.id = revision.proposal_id
               AND proposal.workspace_id = revision.workspace_id
               AND proposal.owner_id = revision.owner_id
              JOIN workspace_composition_decision AS decision
                ON decision.proposal_id = proposal.id
               AND decision.workspace_id = proposal.workspace_id
              JOIN audit_event AS audit
                ON audit.owner_id = revision.owner_id
               AND audit.workspace_id = revision.workspace_id
               AND audit.event_type = 'workspace_composition_applied'
              WHERE revision.workspace_id = NEW.workspace_id
                AND revision.owner_id = NEW.owner_id
                AND revision.revision = NEW.revision
                AND revision.profile_sha256 = NEW.profile_sha256
                AND decision.decision = 'approved'
                AND decision.request_sha256 = proposal.request_sha256
                AND decision.applied_revision = revision.revision
                AND json_extract(audit.payload_json, '$.proposal_id') = proposal.id
                AND json_extract(audit.payload_json, '$.request_sha256') = proposal.request_sha256
                AND json_extract(audit.payload_json, '$.revision') = revision.revision
                AND json_extract(audit.payload_json, '$.profile_sha256') = revision.profile_sha256
          )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_composition_current_identity_drift');
        END
        """,
        """
        CREATE TRIGGER workspace_composition_current_delete_forbidden
        BEFORE DELETE ON workspace_composition_current
        BEGIN
            SELECT RAISE(ABORT, 'desktop_composition_current_delete_forbidden');
        END
        """,
        """
        CREATE TRIGGER workspace_composition_proposal_binding
        BEFORE INSERT ON workspace_composition_proposal
        WHEN NOT EXISTS (
              SELECT 1
              FROM workspace_composition_revision AS base
              WHERE base.workspace_id = NEW.workspace_id
                AND base.owner_id = NEW.owner_id
                AND base.revision = NEW.base_revision
                AND base.profile_sha256 = NEW.base_profile_sha256
          )
          OR (NEW.source_kind = 'owner' AND NEW.source_reference IS NOT NULL)
          OR (
              NEW.source_kind = 'assistant'
              AND NOT EXISTS (
                  SELECT 1
                  FROM message
                  JOIN invocation
                    ON invocation.id = message.invocation_id
                   AND invocation.owner_id = message.owner_id
                   AND invocation.workspace_id = message.workspace_id
                   AND invocation.conversation_id = message.conversation_id
                  WHERE message.id = NEW.source_reference
                    AND message.workspace_id = NEW.workspace_id
                    AND message.owner_id = NEW.owner_id
                    AND message.role = 'assistant'
                    AND message.status = 'completed'
                    AND invocation.status = 'succeeded'
              )
          )
          OR (
              NEW.source_kind = 'rollback'
              AND NOT EXISTS (
                  SELECT 1
                  FROM workspace_composition_revision AS target
                  WHERE target.workspace_id = NEW.workspace_id
                    AND target.owner_id = NEW.owner_id
                    AND target.revision < NEW.base_revision
                    AND NEW.source_reference = 'revision:' || target.revision
                    AND target.profile_json = NEW.desired_profile_json
                    AND target.profile_sha256 = NEW.desired_profile_sha256
              )
          )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_composition_proposal_binding_invalid');
        END
        """,
        """
        CREATE TRIGGER workspace_composition_revision_proposal_binding
        BEFORE INSERT ON workspace_composition_revision
        WHEN (
              NEW.proposal_id IS NULL
              AND (NEW.revision <> 1 OR NEW.source_kind <> 'system')
          )
          OR (
              NEW.proposal_id IS NOT NULL
              AND NOT EXISTS (
              SELECT 1
              FROM workspace_composition_proposal AS proposal
              WHERE proposal.id = NEW.proposal_id
                AND proposal.workspace_id = NEW.workspace_id
                AND proposal.owner_id = NEW.owner_id
                AND proposal.base_revision + 1 = NEW.revision
                AND proposal.desired_profile_json = NEW.profile_json
                AND proposal.desired_profile_sha256 = NEW.profile_sha256
                AND proposal.source_kind = NEW.source_kind
              )
          )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_composition_proposal_binding_invalid');
        END
        """,
        """
        CREATE TRIGGER workspace_composition_decision_binding
        BEFORE INSERT ON workspace_composition_decision
        WHEN NOT EXISTS (
            SELECT 1
            FROM workspace_composition_proposal AS proposal
            LEFT JOIN workspace_composition_revision AS applied
              ON applied.workspace_id = NEW.workspace_id
             AND applied.revision = NEW.applied_revision
            WHERE proposal.id = NEW.proposal_id
              AND proposal.workspace_id = NEW.workspace_id
              AND proposal.request_sha256 = NEW.request_sha256
              AND (
                  (NEW.decision = 'rejected' AND NEW.applied_revision IS NULL)
                  OR (
                      NEW.decision = 'approved'
                      AND applied.owner_id = proposal.owner_id
                      AND applied.revision = proposal.base_revision + 1
                      AND applied.profile_json = proposal.desired_profile_json
                      AND applied.profile_sha256 = proposal.desired_profile_sha256
                      AND applied.source_kind = proposal.source_kind
                      AND applied.proposal_id = proposal.id
                  )
              )
        )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_composition_decision_binding_invalid');
        END
        """,
        """
        INSERT INTO owner_workbench_preference (
            owner_id, density, reduce_motion, row_version, created_at, updated_at
        )
        SELECT id, 'compact', 0, 1, created_at, updated_at
        FROM owner
        """,
        (
            "INSERT INTO workspace_composition_revision "  # noqa: S608 - source-owned canonical constants
            "(workspace_id, owner_id, revision, template_id, template_version, "
            "profile_json, profile_sha256, source_kind, proposal_id, created_at) "
            "SELECT id, owner_id, 1, 'standard-workbench', 1, "
            f"'{STANDARD_WORKBENCH_PROFILE_JSON}', '{STANDARD_WORKBENCH_PROFILE_SHA256}', "
            "'system', NULL, created_at FROM workspace"
        ),
        (
            "INSERT INTO workspace_composition_current "  # noqa: S608 - source-owned canonical digest
            "(workspace_id, owner_id, revision, profile_sha256, created_at, updated_at) "
            f"SELECT id, owner_id, 1, '{STANDARD_WORKBENCH_PROFILE_SHA256}', "
            "created_at, updated_at FROM workspace"
        ),
    ),
)


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


_COMPONENT_CATALOG_SEEDS = tuple(
    "INSERT INTO component_catalog_version "  # noqa: S608 - canonical source-owned seed
    "(component_id, version, family, publisher_class, display_name, adapter_id, "
    "manifest_json, manifest_sha256, package_sha256, operation_allowlist_json, "
    "requires_network, max_calls, max_bytes_in, max_bytes_out, max_tokens, "
    "max_wall_time_ms, max_cost_units, max_retries, max_concurrency, created_at) VALUES ("
    + ", ".join(
        (
            _sql_literal(item.component_id),
            _sql_literal(item.version),
            _sql_literal(item.family),
            "'source_owned'",
            _sql_literal(item.display_name),
            _sql_literal(item.adapter_id),
            _sql_literal(item.manifest_json),
            _sql_literal(item.manifest_sha256),
            _sql_literal(item.package_sha256),
            _sql_literal(canonical_json(list(item.operations))),
            "1" if item.requires_network else "0",
            str(item.max_calls),
            str(item.max_bytes_in),
            str(item.max_bytes_out),
            str(item.max_tokens),
            str(item.max_wall_time_ms),
            str(item.max_cost_units),
            str(item.max_retries),
            str(item.max_concurrency),
            "'1970-01-01T00:00:00.000000Z'",
        )
    )
    + ")"
    for item in SEEDED_COMPONENT_VERSIONS
)

# Runtime package identity is external evidence.  Catalog-derived digests are
# policy identities and must never self-attest the bytes shipped by Electron.
_COMPONENT_PACKAGE_ATTESTATION_SEEDS: tuple[str, ...] = ()


def _immutable_table_triggers(table: str, error_code: str) -> tuple[str, str]:
    return (
        f"""
        CREATE TRIGGER {table}_update_forbidden
        BEFORE UPDATE ON {table}
        BEGIN
            SELECT RAISE(ABORT, '{error_code}');
        END
        """,
        f"""
        CREATE TRIGGER {table}_delete_forbidden
        BEFORE DELETE ON {table}
        BEGIN
            SELECT RAISE(ABORT, '{error_code}');
        END
        """,
    )


DESKTOP_0011 = DesktopMigration(
    version=11,
    migration_id="desktop_0011_workspace_component_kernel",
    statements=(
        """
        CREATE TABLE workbench_component_slot (
            slot_id TEXT PRIMARY KEY CHECK (
                length(slot_id) BETWEEN 3 AND 64
                AND slot_id NOT GLOB '*[^a-z0-9._-]*'
            ),
            slot_kind TEXT NOT NULL CHECK (
                slot_kind IN ('editor', 'sidebar', 'settings', 'status')
            ),
            component_allowed INTEGER NOT NULL CHECK (component_allowed IN (0, 1)),
            created_at TEXT NOT NULL
        ) STRICT
        """,
        """
        CREATE TABLE component_catalog_version (
            component_id TEXT NOT NULL CHECK (
                length(component_id) BETWEEN 3 AND 128
                AND component_id GLOB '[a-z]*'
                AND component_id NOT GLOB '*[^a-z0-9.-]*'
            ),
            version TEXT NOT NULL CHECK (
                length(version) BETWEEN 5 AND 32
                AND version NOT GLOB '*[^0-9.]*'
            ),
            family TEXT NOT NULL CHECK (
                family IN (
                    'declarative_ui', 'instruction_skill', 'mcp_connector',
                    'sandbox_workload', 'trusted_local_adapter'
                )
            ),
            publisher_class TEXT NOT NULL CHECK (
                publisher_class IN ('source_owned', 'owner_reviewed')
            ),
            display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 128),
            adapter_id TEXT NOT NULL CHECK (
                length(adapter_id) BETWEEN 3 AND 64
                AND adapter_id NOT GLOB '*[^a-z0-9.-]*'
            ),
            manifest_json TEXT NOT NULL CHECK (json_valid(manifest_json) = 1),
            manifest_sha256 TEXT NOT NULL CHECK (
                length(manifest_sha256) = 64
                AND manifest_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            package_sha256 TEXT NOT NULL CHECK (
                length(package_sha256) = 64
                AND package_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            operation_allowlist_json TEXT NOT NULL CHECK (
                json_valid(operation_allowlist_json) = 1
                AND json_type(operation_allowlist_json) = 'array'
                AND json_array_length(operation_allowlist_json) BETWEEN 1 AND 32
            ),
            requires_network INTEGER NOT NULL CHECK (requires_network IN (0, 1)),
            max_calls INTEGER NOT NULL CHECK (max_calls BETWEEN 1 AND 1000000),
            max_bytes_in INTEGER NOT NULL CHECK (max_bytes_in BETWEEN 0 AND 1073741824),
            max_bytes_out INTEGER NOT NULL CHECK (max_bytes_out BETWEEN 0 AND 1073741824),
            max_tokens INTEGER NOT NULL CHECK (max_tokens BETWEEN 0 AND 100000000),
            max_wall_time_ms INTEGER NOT NULL CHECK (max_wall_time_ms BETWEEN 1 AND 86400000),
            max_cost_units INTEGER NOT NULL CHECK (max_cost_units BETWEEN 1 AND 100000000),
            max_retries INTEGER NOT NULL CHECK (max_retries BETWEEN 0 AND 100),
            max_concurrency INTEGER NOT NULL CHECK (max_concurrency BETWEEN 1 AND 64),
            created_at TEXT NOT NULL,
            PRIMARY KEY (component_id, version),
            UNIQUE (component_id, manifest_sha256, package_sha256)
        ) STRICT
        """,
        """
        CREATE TABLE component_package_attestation (
            component_id TEXT NOT NULL,
            version TEXT NOT NULL,
            policy_manifest_sha256 TEXT NOT NULL CHECK (
                length(policy_manifest_sha256) = 64
                AND policy_manifest_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            manifest_sha256 TEXT NOT NULL CHECK (
                length(manifest_sha256) = 64
                AND manifest_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            package_sha256 TEXT NOT NULL CHECK (
                length(package_sha256) = 64
                AND package_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            inventory_sha256 TEXT NOT NULL CHECK (
                length(inventory_sha256) = 64
                AND inventory_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            attested_by TEXT NOT NULL CHECK (
                attested_by IN ('runtime_manifest', 'owner_native_review')
            ),
            created_at TEXT NOT NULL,
            PRIMARY KEY (component_id, version, manifest_sha256, package_sha256),
            FOREIGN KEY (component_id, version)
                REFERENCES component_catalog_version(component_id, version) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE component_catalog_registration (
            component_id TEXT NOT NULL,
            version TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL,
            package_sha256 TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            inventory_sha256 TEXT NOT NULL CHECK (length(inventory_sha256) = 64),
            request_sha256 TEXT NOT NULL UNIQUE CHECK (length(request_sha256) = 64),
            registered_by TEXT NOT NULL CHECK (registered_by = 'owner_native_review'),
            registered_at TEXT NOT NULL,
            PRIMARY KEY (component_id, version, workspace_id),
            FOREIGN KEY (component_id, version)
                REFERENCES component_catalog_version(component_id, version) ON DELETE RESTRICT,
            FOREIGN KEY (component_id, version, manifest_sha256, package_sha256)
                REFERENCES component_package_attestation(
                    component_id, version, manifest_sha256, package_sha256
                ) ON DELETE RESTRICT,
            FOREIGN KEY (workspace_id, owner_id)
                REFERENCES workspace(id, owner_id) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE workspace_component_proposal (
            id TEXT PRIMARY KEY CHECK (
                length(id) = 41 AND id GLOB 'proposal_[0-9a-f]*'
                AND substr(id, 10) NOT GLOB '*[^0-9a-f]*'
            ),
            owner_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            component_id TEXT NOT NULL,
            target_version TEXT NOT NULL,
            change_kind TEXT NOT NULL CHECK (
                change_kind IN (
                    'install', 'bind', 'activate', 'disable', 'upgrade', 'rollback',
                    'revoke', 'uninstall'
                )
            ),
            expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
            manifest_sha256 TEXT NOT NULL CHECK (
                length(manifest_sha256) = 64
                AND manifest_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            package_sha256 TEXT NOT NULL CHECK (
                length(package_sha256) = 64
                AND package_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            requested_grants_json TEXT NOT NULL CHECK (
                json_valid(requested_grants_json) = 1
                AND json_type(requested_grants_json) = 'array'
            ),
            desired_configuration_json TEXT NOT NULL CHECK (
                json_valid(desired_configuration_json) = 1
                AND json_type(desired_configuration_json) = 'object'
            ),
            desired_configuration_sha256 TEXT NOT NULL CHECK (
                length(desired_configuration_sha256) = 64
                AND desired_configuration_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            desired_slot_bindings_json TEXT NOT NULL CHECK (
                json_valid(desired_slot_bindings_json) = 1
                AND json_type(desired_slot_bindings_json) = 'array'
            ),
            desired_slot_bindings_sha256 TEXT NOT NULL CHECK (
                length(desired_slot_bindings_sha256) = 64
                AND desired_slot_bindings_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            dependency_graph_json TEXT NOT NULL CHECK (
                json_valid(dependency_graph_json) = 1
                AND json_type(dependency_graph_json) = 'array'
            ),
            dependency_graph_sha256 TEXT NOT NULL CHECK (
                length(dependency_graph_sha256) = 64
                AND dependency_graph_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            source_kind TEXT NOT NULL CHECK (source_kind IN ('owner', 'assistant')),
            source_reference TEXT,
            idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 128),
            request_json TEXT NOT NULL CHECK (json_valid(request_json) = 1),
            request_sha256 TEXT NOT NULL CHECK (
                length(request_sha256) = 64
                AND request_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            created_at TEXT NOT NULL,
            UNIQUE (id, workspace_id),
            UNIQUE (workspace_id, component_id, idempotency_key),
            UNIQUE (request_sha256),
            FOREIGN KEY (workspace_id, owner_id)
                REFERENCES workspace(id, owner_id) ON DELETE RESTRICT,
            FOREIGN KEY (component_id, target_version)
                REFERENCES component_catalog_version(component_id, version) ON DELETE RESTRICT,
            CHECK (
                (source_kind = 'owner' AND source_reference IS NULL)
                OR (source_kind = 'assistant' AND source_reference IS NOT NULL)
            )
        ) STRICT
        """,
        """
        CREATE TABLE workspace_component_decision (
            proposal_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
            request_sha256 TEXT NOT NULL CHECK (
                length(request_sha256) = 64
                AND request_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            decided_by TEXT NOT NULL CHECK (decided_by = 'owner'),
            decided_at TEXT NOT NULL,
            FOREIGN KEY (proposal_id, workspace_id)
                REFERENCES workspace_component_proposal(id, workspace_id) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE workspace_component_installation (
            id TEXT PRIMARY KEY CHECK (
                length(id) = 45 AND id GLOB 'installation_[0-9a-f]*'
                AND substr(id, 14) NOT GLOB '*[^0-9a-f]*'
            ),
            owner_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            component_id TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK (revision >= 1),
            version TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
            package_sha256 TEXT NOT NULL CHECK (length(package_sha256) = 64),
            state TEXT NOT NULL CHECK (
                state IN ('installed', 'bound', 'active', 'disabled', 'blocked', 'revoked', 'uninstalled')
            ),
            binding_generation INTEGER NOT NULL CHECK (binding_generation >= 1),
            current_runtime_instance_id TEXT,
            proposal_id TEXT NOT NULL,
            request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
            configuration_json TEXT NOT NULL CHECK (
                json_valid(configuration_json) = 1 AND json_type(configuration_json) = 'object'
            ),
            configuration_sha256 TEXT NOT NULL CHECK (length(configuration_sha256) = 64),
            slot_bindings_json TEXT NOT NULL CHECK (
                json_valid(slot_bindings_json) = 1 AND json_type(slot_bindings_json) = 'array'
            ),
            slot_bindings_sha256 TEXT NOT NULL CHECK (length(slot_bindings_sha256) = 64),
            dependency_graph_json TEXT NOT NULL CHECK (
                json_valid(dependency_graph_json) = 1 AND json_type(dependency_graph_json) = 'array'
            ),
            dependency_graph_sha256 TEXT NOT NULL CHECK (length(dependency_graph_sha256) = 64),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (id, workspace_id),
            UNIQUE (id, workspace_id, component_id),
            FOREIGN KEY (workspace_id, owner_id)
                REFERENCES workspace(id, owner_id) ON DELETE RESTRICT,
            FOREIGN KEY (component_id, version)
                REFERENCES component_catalog_version(component_id, version) ON DELETE RESTRICT,
            FOREIGN KEY (proposal_id) REFERENCES workspace_component_decision(proposal_id)
                ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE UNIQUE INDEX workspace_component_one_live_installation
        ON workspace_component_installation(workspace_id, component_id)
        WHERE state <> 'uninstalled'
        """,
        """
        CREATE TABLE workspace_component_binding_generation (
            installation_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            component_id TEXT NOT NULL,
            generation INTEGER NOT NULL CHECK (generation >= 1),
            version TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
            package_sha256 TEXT NOT NULL CHECK (length(package_sha256) = 64),
            proposal_id TEXT NOT NULL,
            request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
            configuration_json TEXT NOT NULL CHECK (
                json_valid(configuration_json) = 1 AND json_type(configuration_json) = 'object'
            ),
            configuration_sha256 TEXT NOT NULL CHECK (length(configuration_sha256) = 64),
            slot_bindings_json TEXT NOT NULL CHECK (
                json_valid(slot_bindings_json) = 1 AND json_type(slot_bindings_json) = 'array'
            ),
            slot_bindings_sha256 TEXT NOT NULL CHECK (length(slot_bindings_sha256) = 64),
            dependency_graph_json TEXT NOT NULL CHECK (
                json_valid(dependency_graph_json) = 1 AND json_type(dependency_graph_json) = 'array'
            ),
            dependency_graph_sha256 TEXT NOT NULL CHECK (length(dependency_graph_sha256) = 64),
            state TEXT NOT NULL CHECK (
                state IN ('installed', 'bound', 'active', 'disabled', 'failed', 'revoked')
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (installation_id, generation),
            UNIQUE (installation_id, generation, workspace_id),
            UNIQUE (installation_id, generation, workspace_id, component_id),
            FOREIGN KEY (installation_id, workspace_id, component_id)
                REFERENCES workspace_component_installation(id, workspace_id, component_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (component_id, version)
                REFERENCES component_catalog_version(component_id, version) ON DELETE RESTRICT,
            FOREIGN KEY (proposal_id) REFERENCES workspace_component_decision(proposal_id)
                ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE UNIQUE INDEX workspace_component_one_active_binding
        ON workspace_component_binding_generation(installation_id)
        WHERE state = 'active'
        """,
        """
        CREATE TABLE workspace_component_slot_binding (
            installation_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            slot_id TEXT NOT NULL,
            binding_key TEXT NOT NULL CHECK (length(binding_key) BETWEEN 3 AND 128),
            order_index INTEGER NOT NULL CHECK (order_index BETWEEN 0 AND 10000),
            config_json TEXT NOT NULL CHECK (json_valid(config_json) = 1),
            config_sha256 TEXT NOT NULL CHECK (length(config_sha256) = 64),
            created_at TEXT NOT NULL,
            PRIMARY KEY (installation_id, generation, slot_id, binding_key),
            FOREIGN KEY (installation_id, generation)
                REFERENCES workspace_component_binding_generation(installation_id, generation)
                ON DELETE RESTRICT,
            FOREIGN KEY (slot_id) REFERENCES workbench_component_slot(slot_id) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE workspace_component_runtime_instance (
            id TEXT PRIMARY KEY CHECK (
                length(id) = 40 AND id GLOB 'runtime_[0-9a-f]*'
                AND substr(id, 9) NOT GLOB '*[^0-9a-f]*'
            ),
            installation_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            generation INTEGER NOT NULL CHECK (generation >= 1),
            operation_generation INTEGER NOT NULL CHECK (operation_generation >= 1),
            workload_identity_digest TEXT NOT NULL CHECK (
                length(workload_identity_digest) = 64
                AND workload_identity_digest NOT GLOB '*[^0-9a-f]*'
            ),
            state TEXT NOT NULL CHECK (
                state IN ('active', 'stopped', 'unknown', 'revoked')
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (id, workspace_id),
            UNIQUE (id, installation_id, generation),
            UNIQUE (id, workspace_id, installation_id),
            UNIQUE (id, workspace_id, installation_id, generation),
            FOREIGN KEY (installation_id, generation, workspace_id)
                REFERENCES workspace_component_binding_generation(
                    installation_id, generation, workspace_id
                ) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE UNIQUE INDEX workspace_component_one_live_runtime
        ON workspace_component_runtime_instance(installation_id, generation)
        WHERE state IN ('active', 'unknown')
        """,
        """
        CREATE TABLE workspace_component_grant (
            id TEXT PRIMARY KEY CHECK (
                length(id) = 38 AND id GLOB 'grant_[0-9a-f]*'
                AND substr(id, 7) NOT GLOB '*[^0-9a-f]*'
            ),
            owner_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            installation_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            runtime_instance_id TEXT NOT NULL,
            component_id TEXT NOT NULL,
            version TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
            package_sha256 TEXT NOT NULL CHECK (length(package_sha256) = 64),
            request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
            workload_identity_digest TEXT NOT NULL CHECK (length(workload_identity_digest) = 64),
            actions_json TEXT NOT NULL CHECK (
                json_valid(actions_json) = 1 AND json_type(actions_json) = 'array'
            ),
            scope_json TEXT NOT NULL CHECK (
                json_valid(scope_json) = 1 AND json_type(scope_json) = 'array'
            ),
            requires_network INTEGER NOT NULL CHECK (requires_network IN (0, 1)),
            state TEXT NOT NULL CHECK (state IN ('active', 'revoked', 'expired')),
            not_before TEXT NOT NULL,
            expires_at TEXT NOT NULL CHECK (expires_at > not_before),
            max_calls INTEGER NOT NULL CHECK (max_calls >= 1),
            max_bytes_in INTEGER NOT NULL CHECK (max_bytes_in >= 0),
            max_bytes_out INTEGER NOT NULL CHECK (max_bytes_out >= 0),
            max_tokens INTEGER NOT NULL CHECK (max_tokens >= 0),
            max_wall_time_ms INTEGER NOT NULL CHECK (max_wall_time_ms >= 1),
            max_cost_units INTEGER NOT NULL CHECK (max_cost_units >= 1),
            max_retries INTEGER NOT NULL CHECK (max_retries >= 0),
            max_concurrency INTEGER NOT NULL CHECK (max_concurrency >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (runtime_instance_id),
            UNIQUE (id, workspace_id),
            UNIQUE (id, workspace_id, runtime_instance_id),
            UNIQUE (id, workspace_id, runtime_instance_id, installation_id, generation),
            FOREIGN KEY (workspace_id, owner_id)
                REFERENCES workspace(id, owner_id) ON DELETE RESTRICT,
            FOREIGN KEY (runtime_instance_id, installation_id, generation)
                REFERENCES workspace_component_runtime_instance(id, installation_id, generation)
                ON DELETE RESTRICT,
            FOREIGN KEY (component_id, version)
                REFERENCES component_catalog_version(component_id, version) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE workspace_component_grant_usage (
            grant_id TEXT PRIMARY KEY,
            calls INTEGER NOT NULL DEFAULT 0 CHECK (calls >= 0),
            bytes_in INTEGER NOT NULL DEFAULT 0 CHECK (bytes_in >= 0),
            bytes_out_reserved INTEGER NOT NULL DEFAULT 0 CHECK (bytes_out_reserved >= 0),
            tokens_reserved INTEGER NOT NULL DEFAULT 0 CHECK (tokens_reserved >= 0),
            wall_time_ms_reserved INTEGER NOT NULL DEFAULT 0 CHECK (wall_time_ms_reserved >= 0),
            cost_units INTEGER NOT NULL DEFAULT 0 CHECK (cost_units >= 0),
            retries INTEGER NOT NULL DEFAULT 0 CHECK (retries >= 0),
            row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
            updated_at TEXT NOT NULL,
            FOREIGN KEY (grant_id) REFERENCES workspace_component_grant(id) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE workspace_component_workload_lease (
            id TEXT PRIMARY KEY CHECK (
                length(id) = 46 AND id GLOB 'workloadlease_[0-9a-f]*'
                AND substr(id, 15) NOT GLOB '*[^0-9a-f]*'
            ),
            workspace_id TEXT NOT NULL,
            installation_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            runtime_instance_id TEXT NOT NULL,
            workload_identity_digest TEXT NOT NULL CHECK (length(workload_identity_digest) = 64),
            fencing_token INTEGER NOT NULL CHECK (fencing_token >= 1),
            state TEXT NOT NULL CHECK (state IN ('active', 'revoked', 'expired', 'consumed')),
            not_before TEXT NOT NULL,
            expires_at TEXT NOT NULL CHECK (expires_at > not_before),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (runtime_instance_id, fencing_token),
            UNIQUE (id, workspace_id, runtime_instance_id, installation_id, generation),
            FOREIGN KEY (runtime_instance_id, workspace_id, installation_id, generation)
                REFERENCES workspace_component_runtime_instance(
                    id, workspace_id, installation_id, generation
                )
                ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE UNIQUE INDEX workspace_component_one_active_workload_lease
        ON workspace_component_workload_lease(runtime_instance_id)
        WHERE state = 'active'
        """,
        """
        CREATE TABLE workspace_component_network_lease (
            id TEXT PRIMARY KEY CHECK (
                length(id) = 45 AND id GLOB 'networklease_[0-9a-f]*'
                AND substr(id, 14) NOT GLOB '*[^0-9a-f]*'
            ),
            workspace_id TEXT NOT NULL,
            grant_id TEXT NOT NULL,
            workload_lease_id TEXT NOT NULL,
            runtime_instance_id TEXT NOT NULL,
            installation_id TEXT NOT NULL,
            generation INTEGER NOT NULL CHECK (generation >= 1),
            logical_service_id TEXT NOT NULL CHECK (length(logical_service_id) BETWEEN 3 AND 128),
            fencing_token INTEGER NOT NULL CHECK (fencing_token >= 1),
            state TEXT NOT NULL CHECK (state IN ('active', 'revoked', 'expired', 'consumed')),
            not_before TEXT NOT NULL,
            expires_at TEXT NOT NULL CHECK (expires_at > not_before),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (grant_id, logical_service_id, fencing_token),
            FOREIGN KEY (grant_id, workspace_id, runtime_instance_id, installation_id, generation)
                REFERENCES workspace_component_grant(
                    id, workspace_id, runtime_instance_id, installation_id, generation
                ) ON DELETE RESTRICT,
            FOREIGN KEY (
                workload_lease_id, workspace_id, runtime_instance_id, installation_id, generation
            ) REFERENCES workspace_component_workload_lease(
                id, workspace_id, runtime_instance_id, installation_id, generation
            ) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE UNIQUE INDEX workspace_component_one_active_network_lease
        ON workspace_component_network_lease(grant_id, logical_service_id, runtime_instance_id)
        WHERE state = 'active'
        """,
        """
        CREATE TABLE workspace_component_operation (
            id TEXT PRIMARY KEY CHECK (
                length(id) = 39 AND id GLOB 'compop_[0-9a-f]*'
                AND substr(id, 8) NOT GLOB '*[^0-9a-f]*'
            ),
            owner_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            component_id TEXT NOT NULL,
            installation_id TEXT,
            kind TEXT NOT NULL CHECK (
                kind IN (
                    'install', 'bind', 'activate', 'disable', 'upgrade', 'rollback',
                    'revoke', 'uninstall', 'invoke', 'emergency_stop', 'reconcile',
                    'recovery'
                )
            ),
            action TEXT,
            operation_generation INTEGER NOT NULL CHECK (operation_generation >= 1),
            expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
            binding_generation INTEGER,
            runtime_instance_id TEXT,
            manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
            package_sha256 TEXT NOT NULL CHECK (length(package_sha256) = 64),
            idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 128),
            request_json TEXT NOT NULL CHECK (json_valid(request_json) = 1),
            request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
            state TEXT NOT NULL CHECK (
                state IN (
                    'accepted', 'authorized', 'dispatching', 'succeeded', 'failed',
                    'ambiguous', 'reconciliation_required',
                    'reconciled_succeeded', 'reconciled_failed'
                )
            ),
            version INTEGER NOT NULL CHECK (version >= 1),
            result_sha256 TEXT CHECK (result_sha256 IS NULL OR length(result_sha256) = 64),
            evidence_sha256 TEXT CHECK (evidence_sha256 IS NULL OR length(evidence_sha256) = 64),
            error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (workspace_id, component_id, operation_generation),
            UNIQUE (workspace_id, component_id, idempotency_key),
            UNIQUE (id, workspace_id),
            UNIQUE (id, workspace_id, component_id),
            FOREIGN KEY (workspace_id, owner_id)
                REFERENCES workspace(id, owner_id) ON DELETE RESTRICT,
            FOREIGN KEY (installation_id, workspace_id, component_id)
                REFERENCES workspace_component_installation(id, workspace_id, component_id)
                ON DELETE RESTRICT,
            CHECK (
                (kind = 'invoke' AND action IS NOT NULL AND installation_id IS NOT NULL)
                OR (kind <> 'invoke' AND action IS NULL)
            )
        ) STRICT
        """,
        """
        CREATE TABLE workspace_component_operation_transition (
            operation_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence >= 1),
            state TEXT NOT NULL CHECK (
                state IN (
                    'accepted', 'authorized', 'dispatching', 'succeeded', 'failed',
                    'ambiguous', 'reconciliation_required',
                    'reconciled_succeeded', 'reconciled_failed'
                )
            ),
            reason_code TEXT NOT NULL CHECK (length(reason_code) BETWEEN 3 AND 96),
            evidence_sha256 TEXT CHECK (
                evidence_sha256 IS NULL OR length(evidence_sha256) = 64
            ),
            recorded_at TEXT NOT NULL,
            PRIMARY KEY (operation_id, sequence),
            FOREIGN KEY (operation_id) REFERENCES workspace_component_operation(id)
                ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE workspace_component_budget_reservation (
            operation_id TEXT PRIMARY KEY,
            grant_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            runtime_instance_id TEXT NOT NULL,
            request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
            calls INTEGER NOT NULL CHECK (calls = 1),
            bytes_in INTEGER NOT NULL CHECK (bytes_in >= 0),
            bytes_out_reserved INTEGER NOT NULL CHECK (bytes_out_reserved >= 0),
            tokens_reserved INTEGER NOT NULL CHECK (tokens_reserved >= 0),
            wall_time_ms_reserved INTEGER NOT NULL CHECK (wall_time_ms_reserved >= 0),
            cost_units INTEGER NOT NULL CHECK (cost_units >= 0),
            retries_reserved INTEGER NOT NULL CHECK (retries_reserved IN (0, 1)),
            created_at TEXT NOT NULL,
            FOREIGN KEY (operation_id, workspace_id)
                REFERENCES workspace_component_operation(id, workspace_id) ON DELETE RESTRICT,
            FOREIGN KEY (grant_id, workspace_id, runtime_instance_id)
                REFERENCES workspace_component_grant(id, workspace_id, runtime_instance_id)
                ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE workspace_component_effect (
            id TEXT PRIMARY KEY CHECK (
                length(id) = 39 AND id GLOB 'effect_[0-9a-f]*'
                AND substr(id, 8) NOT GLOB '*[^0-9a-f]*'
            ),
            operation_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL,
            effect_kind TEXT NOT NULL CHECK (
                effect_kind IN ('lifecycle', 'adapter_invoke', 'emergency_stop')
            ),
            logical_target_id TEXT NOT NULL CHECK (length(logical_target_id) BETWEEN 3 AND 128),
            request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
            state TEXT NOT NULL CHECK (
                state IN (
                    'pending', 'committed', 'failed', 'unknown', 'reconciliation_required',
                    'reconciled_committed', 'reconciled_failed'
                )
            ),
            version INTEGER NOT NULL CHECK (version >= 1),
            result_sha256 TEXT CHECK (result_sha256 IS NULL OR length(result_sha256) = 64),
            evidence_sha256 TEXT CHECK (evidence_sha256 IS NULL OR length(evidence_sha256) = 64),
            error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (id, workspace_id),
            UNIQUE (id, operation_id, workspace_id),
            FOREIGN KEY (operation_id, workspace_id)
                REFERENCES workspace_component_operation(id, workspace_id) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE workspace_component_effect_transition (
            effect_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence >= 1),
            state TEXT NOT NULL CHECK (
                state IN (
                    'pending', 'committed', 'failed', 'unknown', 'reconciliation_required',
                    'reconciled_committed', 'reconciled_failed'
                )
            ),
            reason_code TEXT NOT NULL CHECK (length(reason_code) BETWEEN 3 AND 96),
            evidence_sha256 TEXT CHECK (
                evidence_sha256 IS NULL OR length(evidence_sha256) = 64
            ),
            recorded_at TEXT NOT NULL,
            PRIMARY KEY (effect_id, sequence),
            FOREIGN KEY (effect_id) REFERENCES workspace_component_effect(id) ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE workspace_component_reconciliation (
            id TEXT PRIMARY KEY CHECK (
                length(id) = 42 AND id GLOB 'reconcile_[0-9a-f]*'
                AND substr(id, 11) NOT GLOB '*[^0-9a-f]*'
            ),
            operation_id TEXT NOT NULL UNIQUE,
            effect_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL,
            request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
            outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'failed')),
            evidence_sha256 TEXT NOT NULL CHECK (length(evidence_sha256) = 64),
            decided_by TEXT NOT NULL CHECK (decided_by = 'owner'),
            decided_at TEXT NOT NULL,
            FOREIGN KEY (operation_id, workspace_id)
                REFERENCES workspace_component_operation(id, workspace_id) ON DELETE RESTRICT,
            FOREIGN KEY (effect_id, operation_id, workspace_id)
                REFERENCES workspace_component_effect(id, operation_id, workspace_id)
                ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE workspace_component_lifecycle_dispatch (
            operation_id TEXT PRIMARY KEY,
            effect_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL,
            component_id TEXT NOT NULL,
            installation_id TEXT,
            binding_generation INTEGER,
            action TEXT NOT NULL CHECK (
                action IN (
                    'install', 'bind', 'activate', 'disable', 'upgrade', 'rollback',
                    'revoke', 'uninstall', 'recovery'
                )
            ),
            adapter_id TEXT NOT NULL CHECK (length(adapter_id) BETWEEN 3 AND 64),
            reserved_runtime_instance_id TEXT,
            workload_identity_digest TEXT,
            request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
            manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
            package_sha256 TEXT NOT NULL CHECK (length(package_sha256) = 64),
            created_at TEXT NOT NULL,
            FOREIGN KEY (operation_id, workspace_id, component_id)
                REFERENCES workspace_component_operation(id, workspace_id, component_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (effect_id, operation_id, workspace_id)
                REFERENCES workspace_component_effect(id, operation_id, workspace_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (installation_id, workspace_id, component_id)
                REFERENCES workspace_component_installation(id, workspace_id, component_id)
                ON DELETE RESTRICT,
            CHECK (
                (action IN ('activate', 'recovery') AND installation_id IS NOT NULL
                    AND binding_generation IS NOT NULL
                    AND reserved_runtime_instance_id IS NOT NULL
                    AND workload_identity_digest IS NOT NULL)
                OR (action NOT IN ('activate', 'recovery') AND reserved_runtime_instance_id IS NULL
                    AND workload_identity_digest IS NULL)
            )
        ) STRICT
        """,
        """
        CREATE TABLE workspace_component_lifecycle_receipt (
            operation_id TEXT PRIMARY KEY,
            effect_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL,
            component_id TEXT NOT NULL,
            installation_id TEXT,
            binding_generation INTEGER,
            runtime_instance_id TEXT,
            adapter_id TEXT NOT NULL CHECK (
                length(adapter_id) BETWEEN 3 AND 64
                AND adapter_id NOT GLOB '*[^a-z0-9.-]*'
            ),
            request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
            manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
            package_sha256 TEXT NOT NULL CHECK (length(package_sha256) = 64),
            outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'failed', 'unknown')),
            health_state TEXT CHECK (
                health_state IS NULL OR health_state IN ('healthy', 'unhealthy', 'unknown')
            ),
            workload_identity_digest TEXT CHECK (
                workload_identity_digest IS NULL OR length(workload_identity_digest) = 64
            ),
            result_sha256 TEXT CHECK (result_sha256 IS NULL OR length(result_sha256) = 64),
            evidence_sha256 TEXT NOT NULL CHECK (length(evidence_sha256) = 64),
            error_code TEXT,
            receipt_sha256 TEXT NOT NULL UNIQUE CHECK (length(receipt_sha256) = 64),
            settled_at TEXT NOT NULL,
            FOREIGN KEY (operation_id, workspace_id, component_id)
                REFERENCES workspace_component_operation(id, workspace_id, component_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (effect_id, operation_id, workspace_id)
                REFERENCES workspace_component_effect(id, operation_id, workspace_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (operation_id)
                REFERENCES workspace_component_lifecycle_dispatch(operation_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (installation_id, workspace_id, component_id)
                REFERENCES workspace_component_installation(id, workspace_id, component_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (
                runtime_instance_id, workspace_id, installation_id, binding_generation
            ) REFERENCES workspace_component_runtime_instance(
                id, workspace_id, installation_id, generation
            ) ON DELETE RESTRICT,
            CHECK (
                (runtime_instance_id IS NULL AND workload_identity_digest IS NULL)
                OR (runtime_instance_id IS NOT NULL AND installation_id IS NOT NULL
                    AND binding_generation IS NOT NULL
                    AND workload_identity_digest IS NOT NULL)
            ),
            CHECK (
                (outcome = 'succeeded' AND result_sha256 IS NOT NULL AND error_code IS NULL)
                OR (outcome = 'failed' AND error_code IS NOT NULL)
                OR outcome = 'unknown'
            )
        ) STRICT
        """,
        """
        CREATE TABLE workspace_component_invocation_receipt (
            operation_id TEXT PRIMARY KEY,
            grant_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            runtime_instance_id TEXT NOT NULL,
            request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
            outcome TEXT NOT NULL CHECK (
                outcome IN ('succeeded', 'failed', 'cancelled', 'unknown')
            ),
            reserved_bytes_out INTEGER NOT NULL CHECK (reserved_bytes_out >= 0),
            reserved_tokens INTEGER NOT NULL CHECK (reserved_tokens >= 0),
            reserved_wall_time_ms INTEGER NOT NULL CHECK (reserved_wall_time_ms >= 0),
            actual_bytes_out INTEGER NOT NULL CHECK (actual_bytes_out >= 0),
            actual_tokens INTEGER NOT NULL CHECK (actual_tokens >= 0),
            actual_wall_time_ms INTEGER NOT NULL CHECK (actual_wall_time_ms >= 0),
            result_sha256 TEXT CHECK (result_sha256 IS NULL OR length(result_sha256) = 64),
            evidence_sha256 TEXT NOT NULL CHECK (length(evidence_sha256) = 64),
            error_code TEXT,
            receipt_sha256 TEXT NOT NULL UNIQUE CHECK (length(receipt_sha256) = 64),
            settled_at TEXT NOT NULL,
            FOREIGN KEY (operation_id, workspace_id)
                REFERENCES workspace_component_operation(id, workspace_id) ON DELETE RESTRICT,
            FOREIGN KEY (operation_id)
                REFERENCES workspace_component_budget_reservation(operation_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (grant_id, workspace_id, runtime_instance_id)
                REFERENCES workspace_component_grant(id, workspace_id, runtime_instance_id)
                ON DELETE RESTRICT,
            CHECK (actual_bytes_out <= reserved_bytes_out),
            CHECK (actual_tokens <= reserved_tokens),
            CHECK (actual_wall_time_ms <= reserved_wall_time_ms),
            CHECK (
                (outcome = 'succeeded' AND result_sha256 IS NOT NULL AND error_code IS NULL)
                OR (outcome IN ('failed', 'cancelled') AND error_code IS NOT NULL)
                OR outcome = 'unknown'
            )
        ) STRICT
        """,
        """
        CREATE TABLE workspace_component_emergency_receipt (
            operation_id TEXT PRIMARY KEY,
            effect_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL,
            component_id TEXT NOT NULL,
            installation_id TEXT NOT NULL,
            request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
            outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'failed', 'unknown')),
            evidence_sha256 TEXT NOT NULL CHECK (length(evidence_sha256) = 64),
            error_code TEXT,
            receipt_sha256 TEXT NOT NULL UNIQUE CHECK (length(receipt_sha256) = 64),
            settled_at TEXT NOT NULL,
            FOREIGN KEY (operation_id, workspace_id, component_id)
                REFERENCES workspace_component_operation(id, workspace_id, component_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (effect_id, operation_id, workspace_id)
                REFERENCES workspace_component_effect(id, operation_id, workspace_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (installation_id, workspace_id, component_id)
                REFERENCES workspace_component_installation(id, workspace_id, component_id)
                ON DELETE RESTRICT,
            CHECK (
                (outcome = 'succeeded' AND error_code IS NULL)
                OR (outcome IN ('failed', 'unknown') AND error_code IS NOT NULL)
            )
        ) STRICT
        """,
        """
        CREATE TABLE workspace_component_recovery_request (
            id TEXT PRIMARY KEY CHECK (
                length(id) = 41 AND id GLOB 'recovery_[0-9a-f]*'
                AND substr(id, 10) NOT GLOB '*[^0-9a-f]*'
            ),
            workspace_id TEXT NOT NULL,
            component_id TEXT NOT NULL,
            installation_id TEXT NOT NULL,
            binding_generation INTEGER NOT NULL CHECK (binding_generation >= 1),
            previous_runtime_instance_id TEXT NOT NULL,
            operation_id TEXT NOT NULL UNIQUE,
            request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
            manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
            package_sha256 TEXT NOT NULL CHECK (length(package_sha256) = 64),
            state TEXT NOT NULL CHECK (state IN ('pending_native_revalidation', 'blocked')),
            reason_code TEXT NOT NULL CHECK (length(reason_code) BETWEEN 3 AND 96),
            created_at TEXT NOT NULL,
            FOREIGN KEY (installation_id, workspace_id, component_id)
                REFERENCES workspace_component_installation(id, workspace_id, component_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (previous_runtime_instance_id, workspace_id, installation_id)
                REFERENCES workspace_component_runtime_instance(id, workspace_id, installation_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (operation_id, workspace_id, component_id)
                REFERENCES workspace_component_operation(id, workspace_id, component_id)
                ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE workspace_component_health (
            id TEXT PRIMARY KEY CHECK (
                length(id) = 39 AND id GLOB 'health_[0-9a-f]*'
                AND substr(id, 8) NOT GLOB '*[^0-9a-f]*'
            ),
            workspace_id TEXT NOT NULL,
            installation_id TEXT NOT NULL,
            component_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            runtime_instance_id TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('healthy', 'degraded', 'unhealthy', 'unknown')),
            manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
            package_sha256 TEXT NOT NULL CHECK (length(package_sha256) = 64),
            workload_identity_digest TEXT NOT NULL CHECK (length(workload_identity_digest) = 64),
            evidence_sha256 TEXT NOT NULL CHECK (length(evidence_sha256) = 64),
            observed_at TEXT NOT NULL,
            UNIQUE (runtime_instance_id, operation_id),
            FOREIGN KEY (runtime_instance_id, workspace_id, installation_id, generation)
                REFERENCES workspace_component_runtime_instance(
                    id, workspace_id, installation_id, generation
                )
                ON DELETE RESTRICT,
            FOREIGN KEY (operation_id, workspace_id, component_id)
                REFERENCES workspace_component_operation(id, workspace_id, component_id)
                ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE TABLE workspace_component_revocation (
            id TEXT PRIMARY KEY CHECK (
                length(id) = 43 AND id GLOB 'revocation_[0-9a-f]*'
                AND substr(id, 12) NOT GLOB '*[^0-9a-f]*'
            ),
            workspace_id TEXT NOT NULL,
            installation_id TEXT NOT NULL,
            runtime_instance_id TEXT,
            grant_id TEXT,
            reason_code TEXT NOT NULL CHECK (length(reason_code) BETWEEN 3 AND 96),
            actor_type TEXT NOT NULL CHECK (actor_type IN ('owner', 'system')),
            actor_id TEXT,
            created_at TEXT NOT NULL,
            CHECK (
                (actor_type = 'owner' AND actor_id IS NOT NULL)
                OR (actor_type = 'system' AND actor_id IS NULL)
            ),
            CHECK (runtime_instance_id IS NOT NULL OR grant_id IS NOT NULL),
            FOREIGN KEY (installation_id, workspace_id)
                REFERENCES workspace_component_installation(id, workspace_id) ON DELETE RESTRICT,
            FOREIGN KEY (runtime_instance_id, workspace_id, installation_id)
                REFERENCES workspace_component_runtime_instance(id, workspace_id, installation_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (grant_id, workspace_id, runtime_instance_id)
                REFERENCES workspace_component_grant(id, workspace_id, runtime_instance_id)
                ON DELETE RESTRICT
        ) STRICT
        """,
        """
        CREATE UNIQUE INDEX workspace_component_runtime_revocation_unique
        ON workspace_component_revocation(runtime_instance_id)
        WHERE runtime_instance_id IS NOT NULL
        """,
        """
        CREATE UNIQUE INDEX workspace_component_grant_revocation_unique
        ON workspace_component_revocation(grant_id)
        WHERE grant_id IS NOT NULL
        """,
        *_immutable_table_triggers("workbench_component_slot", "desktop_component_slot_immutable"),
        *_immutable_table_triggers(
            "component_catalog_version", "desktop_component_catalog_immutable"
        ),
        *_immutable_table_triggers(
            "component_package_attestation", "desktop_component_package_attestation_immutable"
        ),
        *_immutable_table_triggers(
            "component_catalog_registration", "desktop_component_catalog_registration_immutable"
        ),
        *_immutable_table_triggers(
            "workspace_component_proposal", "desktop_component_proposal_immutable"
        ),
        *_immutable_table_triggers(
            "workspace_component_decision", "desktop_component_decision_immutable"
        ),
        *_immutable_table_triggers(
            "workspace_component_slot_binding", "desktop_component_slot_binding_immutable"
        ),
        *_immutable_table_triggers(
            "workspace_component_operation_transition",
            "desktop_component_operation_transition_immutable",
        ),
        *_immutable_table_triggers(
            "workspace_component_budget_reservation",
            "desktop_component_budget_reservation_immutable",
        ),
        *_immutable_table_triggers(
            "workspace_component_effect_transition",
            "desktop_component_effect_transition_immutable",
        ),
        *_immutable_table_triggers(
            "workspace_component_reconciliation", "desktop_component_reconciliation_immutable"
        ),
        *_immutable_table_triggers(
            "workspace_component_lifecycle_dispatch",
            "desktop_component_lifecycle_dispatch_immutable",
        ),
        *_immutable_table_triggers(
            "workspace_component_lifecycle_receipt",
            "desktop_component_lifecycle_receipt_immutable",
        ),
        *_immutable_table_triggers(
            "workspace_component_invocation_receipt",
            "desktop_component_invocation_receipt_immutable",
        ),
        *_immutable_table_triggers(
            "workspace_component_emergency_receipt",
            "desktop_component_emergency_receipt_immutable",
        ),
        *_immutable_table_triggers(
            "workspace_component_recovery_request",
            "desktop_component_recovery_request_immutable",
        ),
        *_immutable_table_triggers(
            "workspace_component_health", "desktop_component_health_immutable"
        ),
        *_immutable_table_triggers(
            "workspace_component_revocation", "desktop_component_revocation_immutable"
        ),
        """
        CREATE TRIGGER workspace_component_installation_delete_forbidden
        BEFORE DELETE ON workspace_component_installation
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_installation_delete_forbidden');
        END
        """,
        """
        CREATE TRIGGER workspace_component_binding_delete_forbidden
        BEFORE DELETE ON workspace_component_binding_generation
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_binding_delete_forbidden');
        END
        """,
        """
        CREATE TRIGGER workspace_component_runtime_delete_forbidden
        BEFORE DELETE ON workspace_component_runtime_instance
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_runtime_delete_forbidden');
        END
        """,
        """
        CREATE TRIGGER workspace_component_grant_delete_forbidden
        BEFORE DELETE ON workspace_component_grant
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_grant_delete_forbidden');
        END
        """,
        """
        CREATE TRIGGER workspace_component_grant_usage_delete_forbidden
        BEFORE DELETE ON workspace_component_grant_usage
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_grant_usage_delete_forbidden');
        END
        """,
        """
        CREATE TRIGGER workspace_component_workload_lease_delete_forbidden
        BEFORE DELETE ON workspace_component_workload_lease
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_workload_lease_delete_forbidden');
        END
        """,
        """
        CREATE TRIGGER workspace_component_network_lease_delete_forbidden
        BEFORE DELETE ON workspace_component_network_lease
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_network_lease_delete_forbidden');
        END
        """,
        """
        CREATE TRIGGER workspace_component_operation_delete_forbidden
        BEFORE DELETE ON workspace_component_operation
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_operation_delete_forbidden');
        END
        """,
        """
        CREATE TRIGGER workspace_component_effect_delete_forbidden
        BEFORE DELETE ON workspace_component_effect
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_effect_delete_forbidden');
        END
        """,
        """
        CREATE TRIGGER workspace_component_revocation_target_binding
        BEFORE INSERT ON workspace_component_revocation
        WHEN (
            NEW.runtime_instance_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM workspace_component_runtime_instance AS runtime
                WHERE runtime.id = NEW.runtime_instance_id
                  AND runtime.workspace_id = NEW.workspace_id
                  AND runtime.installation_id = NEW.installation_id
            )
        ) OR (
            NEW.grant_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM workspace_component_grant AS grant
                WHERE grant.id = NEW.grant_id
                  AND grant.workspace_id = NEW.workspace_id
                  AND grant.installation_id = NEW.installation_id
                  AND (
                      NEW.runtime_instance_id IS NULL
                      OR grant.runtime_instance_id = NEW.runtime_instance_id
                  )
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_revocation_target_invalid');
        END
        """,
        """
        CREATE TRIGGER workspace_component_proposal_catalog_binding
        BEFORE INSERT ON workspace_component_proposal
        WHEN NOT EXISTS (
            SELECT 1 FROM component_catalog_version AS version
            WHERE version.component_id = NEW.component_id
              AND version.version = NEW.target_version
        ) OR NOT EXISTS (
            SELECT 1 FROM component_package_attestation AS package
            WHERE package.component_id = NEW.component_id
              AND package.version = NEW.target_version
              AND package.manifest_sha256 = NEW.manifest_sha256
              AND package.package_sha256 = NEW.package_sha256
        ) OR EXISTS (
            SELECT 1 FROM component_catalog_version AS catalog
            WHERE catalog.component_id = NEW.component_id
              AND catalog.version = NEW.target_version
              AND catalog.publisher_class = 'owner_reviewed'
              AND NOT EXISTS (
                  SELECT 1 FROM component_catalog_registration AS registration
                  WHERE registration.component_id = NEW.component_id
                    AND registration.version = NEW.target_version
                    AND registration.workspace_id = NEW.workspace_id
                    AND registration.owner_id = NEW.owner_id
                    AND registration.manifest_sha256 = NEW.manifest_sha256
                    AND registration.package_sha256 = NEW.package_sha256
              )
        ) OR (
            NEW.source_kind = 'assistant' AND NOT EXISTS (
                SELECT 1 FROM message
                JOIN invocation ON invocation.id = message.invocation_id
                  AND invocation.owner_id = message.owner_id
                  AND invocation.workspace_id = message.workspace_id
                  AND invocation.conversation_id = message.conversation_id
                WHERE message.id = NEW.source_reference
                  AND message.owner_id = NEW.owner_id
                  AND message.workspace_id = NEW.workspace_id
                  AND message.role = 'assistant'
                  AND message.status = 'completed'
                  AND invocation.status = 'succeeded'
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_proposal_binding_invalid');
        END
        """,
        """
        CREATE TRIGGER component_package_attestation_policy_binding
        BEFORE INSERT ON component_package_attestation
        WHEN NOT EXISTS (
            SELECT 1 FROM component_catalog_version AS catalog
            WHERE catalog.component_id = NEW.component_id
              AND catalog.version = NEW.version
              AND catalog.manifest_sha256 = NEW.policy_manifest_sha256
        )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_attestation_policy_invalid');
        END
        """,
        """
        CREATE TRIGGER workspace_component_decision_binding
        BEFORE INSERT ON workspace_component_decision
        WHEN NOT EXISTS (
            SELECT 1 FROM workspace_component_proposal AS proposal
            JOIN workspace AS workspace
              ON workspace.id = proposal.workspace_id
             AND workspace.owner_id = proposal.owner_id
             AND workspace.state = 'active'
            LEFT JOIN workspace_component_installation AS installation
              ON installation.workspace_id = proposal.workspace_id
             AND installation.component_id = proposal.component_id
             AND installation.state <> 'uninstalled'
            WHERE proposal.id = NEW.proposal_id
              AND proposal.workspace_id = NEW.workspace_id
              AND proposal.request_sha256 = NEW.request_sha256
              AND (
                  NEW.decision = 'rejected'
                  OR (
                      NEW.decision = 'approved'
                      AND COALESCE(installation.revision, 0) = proposal.expected_revision
                  )
              )
        )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_decision_binding_invalid');
        END
        """,
        """
        CREATE TRIGGER workspace_component_installation_identity_guard
        BEFORE UPDATE ON workspace_component_installation
        WHEN NEW.id <> OLD.id OR NEW.owner_id <> OLD.owner_id
          OR NEW.workspace_id <> OLD.workspace_id OR NEW.component_id <> OLD.component_id
          OR NEW.created_at <> OLD.created_at
          OR NEW.revision <> OLD.revision + 1
          OR NEW.binding_generation < OLD.binding_generation
          OR (
              (
                  NEW.version <> OLD.version
                  OR NEW.manifest_sha256 <> OLD.manifest_sha256
                  OR NEW.package_sha256 <> OLD.package_sha256
                  OR NEW.proposal_id <> OLD.proposal_id
                  OR NEW.request_sha256 <> OLD.request_sha256
                  OR NEW.configuration_sha256 <> OLD.configuration_sha256
                  OR NEW.slot_bindings_sha256 <> OLD.slot_bindings_sha256
                  OR NEW.dependency_graph_sha256 <> OLD.dependency_graph_sha256
              ) AND NOT EXISTS (
                  SELECT 1 FROM workspace_component_binding_generation AS binding
                  WHERE binding.installation_id = NEW.id
                    AND binding.workspace_id = NEW.workspace_id
                    AND binding.component_id = NEW.component_id
                    AND binding.generation = NEW.binding_generation
                    AND binding.version = NEW.version
                    AND binding.manifest_sha256 = NEW.manifest_sha256
                    AND binding.package_sha256 = NEW.package_sha256
                    AND binding.proposal_id = NEW.proposal_id
                    AND binding.request_sha256 = NEW.request_sha256
                    AND binding.configuration_sha256 = NEW.configuration_sha256
                    AND binding.slot_bindings_sha256 = NEW.slot_bindings_sha256
                    AND binding.dependency_graph_sha256 = NEW.dependency_graph_sha256
              )
          )
          OR NOT (
              NEW.state = OLD.state
              OR (OLD.state = 'installed' AND NEW.state IN ('bound', 'blocked', 'revoked', 'uninstalled'))
              OR (OLD.state = 'bound' AND NEW.state IN ('active', 'disabled', 'blocked', 'revoked'))
              OR (OLD.state = 'active' AND NEW.state IN ('bound', 'disabled', 'blocked', 'revoked'))
              OR (OLD.state = 'disabled' AND NEW.state IN ('bound', 'blocked', 'revoked', 'uninstalled'))
              OR (OLD.state = 'blocked' AND NEW.state IN ('bound', 'active', 'disabled', 'revoked', 'uninstalled'))
              OR (OLD.state = 'revoked' AND NEW.state = 'uninstalled')
          )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_installation_identity_drift');
        END
        """,
        """
        CREATE TRIGGER workspace_component_installation_audit_guard
        BEFORE UPDATE ON workspace_component_installation
        WHEN NOT EXISTS (
            SELECT 1 FROM audit_event AS audit
            WHERE audit.owner_id = NEW.owner_id
              AND audit.workspace_id = NEW.workspace_id
              AND audit.event_type = 'workspace_component_state_changed'
              AND json_extract(audit.payload_json, '$.installation_id') = NEW.id
              AND json_extract(audit.payload_json, '$.revision') = NEW.revision
              AND json_extract(audit.payload_json, '$.state') = NEW.state
        )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_installation_audit_missing');
        END
        """,
        """
        CREATE TRIGGER workspace_component_active_evidence_guard
        BEFORE UPDATE OF state ON workspace_component_installation
        WHEN NEW.state = 'active' AND OLD.state <> 'active' AND NOT EXISTS (
            SELECT 1 FROM workspace_component_binding_generation AS binding
            JOIN workspace_component_runtime_instance AS runtime
              ON runtime.id = NEW.current_runtime_instance_id
             AND runtime.installation_id = binding.installation_id
             AND runtime.generation = binding.generation
             AND runtime.state = 'active'
            JOIN workspace_component_health AS health
              ON health.runtime_instance_id = runtime.id
             AND health.installation_id = binding.installation_id
             AND health.generation = binding.generation
             AND health.state = 'healthy'
             AND health.manifest_sha256 = binding.manifest_sha256
             AND health.package_sha256 = binding.package_sha256
            JOIN workspace_component_operation AS operation
              ON operation.id = health.operation_id
             AND operation.state = 'succeeded'
            JOIN workspace_component_effect AS effect
              ON effect.operation_id = operation.id
             AND effect.state = 'committed'
            JOIN workspace_component_lifecycle_receipt AS receipt
              ON receipt.operation_id = operation.id
             AND receipt.effect_id = effect.id
             AND receipt.workspace_id = NEW.workspace_id
             AND receipt.component_id = NEW.component_id
             AND receipt.installation_id = NEW.id
             AND receipt.binding_generation = NEW.binding_generation
             AND receipt.runtime_instance_id = runtime.id
             AND receipt.request_sha256 = operation.request_sha256
             AND receipt.manifest_sha256 = NEW.manifest_sha256
             AND receipt.package_sha256 = NEW.package_sha256
             AND receipt.outcome = 'succeeded'
             AND receipt.health_state = 'healthy'
             AND receipt.workload_identity_digest = runtime.workload_identity_digest
             AND receipt.evidence_sha256 = health.evidence_sha256
            WHERE binding.installation_id = NEW.id
              AND binding.generation = NEW.binding_generation
              AND binding.state = 'active'
              AND binding.manifest_sha256 = NEW.manifest_sha256
              AND binding.package_sha256 = NEW.package_sha256
        )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_activation_evidence_missing');
        END
        """,
        """
        CREATE TRIGGER workspace_component_binding_identity_guard
        BEFORE UPDATE ON workspace_component_binding_generation
        WHEN NEW.installation_id <> OLD.installation_id
          OR NEW.workspace_id <> OLD.workspace_id OR NEW.component_id <> OLD.component_id
          OR NEW.generation <> OLD.generation OR NEW.version <> OLD.version
          OR NEW.manifest_sha256 <> OLD.manifest_sha256
          OR NEW.package_sha256 <> OLD.package_sha256
          OR NEW.proposal_id <> OLD.proposal_id OR NEW.request_sha256 <> OLD.request_sha256
          OR NEW.created_at <> OLD.created_at
          OR NOT (
              (OLD.state = 'installed' AND NEW.state IN ('bound', 'failed', 'revoked'))
              OR (OLD.state = 'bound' AND NEW.state IN ('active', 'disabled', 'failed', 'revoked'))
              OR (OLD.state = 'active' AND NEW.state IN ('disabled', 'failed', 'revoked'))
          )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_binding_identity_drift');
        END
        """,
        """
        CREATE TRIGGER workspace_component_runtime_identity_guard
        BEFORE UPDATE ON workspace_component_runtime_instance
        WHEN NEW.id <> OLD.id OR NEW.installation_id <> OLD.installation_id
          OR NEW.workspace_id <> OLD.workspace_id OR NEW.generation <> OLD.generation
          OR NEW.operation_generation <> OLD.operation_generation
          OR NEW.workload_identity_digest <> OLD.workload_identity_digest
          OR NEW.created_at <> OLD.created_at
          OR NOT (
              OLD.state = 'active' AND NEW.state IN ('stopped', 'unknown', 'revoked')
          )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_runtime_identity_drift');
        END
        """,
        """
        CREATE TRIGGER workspace_component_grant_identity_guard
        BEFORE UPDATE ON workspace_component_grant
        WHEN NEW.id <> OLD.id OR NEW.owner_id <> OLD.owner_id
          OR NEW.workspace_id <> OLD.workspace_id OR NEW.installation_id <> OLD.installation_id
          OR NEW.generation <> OLD.generation OR NEW.runtime_instance_id <> OLD.runtime_instance_id
          OR NEW.component_id <> OLD.component_id OR NEW.version <> OLD.version
          OR NEW.manifest_sha256 <> OLD.manifest_sha256
          OR NEW.package_sha256 <> OLD.package_sha256
          OR NEW.request_sha256 <> OLD.request_sha256
          OR NEW.workload_identity_digest <> OLD.workload_identity_digest
          OR NEW.actions_json <> OLD.actions_json OR NEW.scope_json <> OLD.scope_json
          OR NEW.requires_network <> OLD.requires_network
          OR NEW.not_before <> OLD.not_before OR NEW.expires_at <> OLD.expires_at
          OR NEW.max_calls <> OLD.max_calls OR NEW.max_bytes_in <> OLD.max_bytes_in
          OR NEW.max_bytes_out <> OLD.max_bytes_out OR NEW.max_tokens <> OLD.max_tokens
          OR NEW.max_wall_time_ms <> OLD.max_wall_time_ms
          OR NEW.max_cost_units <> OLD.max_cost_units OR NEW.max_retries <> OLD.max_retries
          OR NEW.max_concurrency <> OLD.max_concurrency OR NEW.created_at <> OLD.created_at
          OR NOT (OLD.state = 'active' AND NEW.state IN ('revoked', 'expired'))
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_grant_identity_drift');
        END
        """,
        """
        CREATE TRIGGER workspace_component_grant_usage_guard
        BEFORE UPDATE ON workspace_component_grant_usage
        WHEN NEW.grant_id <> OLD.grant_id OR NEW.row_version <> OLD.row_version + 1
          OR NEW.calls < OLD.calls OR NEW.bytes_in < OLD.bytes_in
          OR NEW.bytes_out_reserved < OLD.bytes_out_reserved
          OR NEW.tokens_reserved < OLD.tokens_reserved
          OR NEW.wall_time_ms_reserved < OLD.wall_time_ms_reserved
          OR NEW.cost_units < OLD.cost_units OR NEW.retries < OLD.retries
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_grant_usage_invalid');
        END
        """,
        """
        CREATE TRIGGER workspace_component_workload_lease_guard
        BEFORE UPDATE ON workspace_component_workload_lease
        WHEN NEW.id <> OLD.id OR NEW.workspace_id <> OLD.workspace_id
          OR NEW.installation_id <> OLD.installation_id OR NEW.generation <> OLD.generation
          OR NEW.runtime_instance_id <> OLD.runtime_instance_id
          OR NEW.workload_identity_digest <> OLD.workload_identity_digest
          OR NEW.fencing_token <> OLD.fencing_token OR NEW.not_before <> OLD.not_before
          OR NEW.expires_at <> OLD.expires_at OR NEW.created_at <> OLD.created_at
          OR NOT (OLD.state = 'active' AND NEW.state IN ('revoked', 'expired', 'consumed'))
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_workload_lease_invalid');
        END
        """,
        """
        CREATE TRIGGER workspace_component_network_lease_guard
        BEFORE UPDATE ON workspace_component_network_lease
        WHEN NEW.id <> OLD.id OR NEW.workspace_id <> OLD.workspace_id
          OR NEW.grant_id <> OLD.grant_id OR NEW.workload_lease_id <> OLD.workload_lease_id
          OR NEW.runtime_instance_id <> OLD.runtime_instance_id
          OR NEW.installation_id <> OLD.installation_id OR NEW.generation <> OLD.generation
          OR NEW.logical_service_id <> OLD.logical_service_id
          OR NEW.fencing_token <> OLD.fencing_token OR NEW.not_before <> OLD.not_before
          OR NEW.expires_at <> OLD.expires_at OR NEW.created_at <> OLD.created_at
          OR NOT (OLD.state = 'active' AND NEW.state IN ('revoked', 'expired', 'consumed'))
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_network_lease_invalid');
        END
        """,
        """
        CREATE TRIGGER workspace_component_operation_identity_guard
        BEFORE UPDATE ON workspace_component_operation
        WHEN NEW.id <> OLD.id OR NEW.owner_id <> OLD.owner_id
          OR NEW.workspace_id <> OLD.workspace_id OR NEW.component_id <> OLD.component_id
          OR NEW.installation_id IS NOT OLD.installation_id OR NEW.kind <> OLD.kind
          OR NEW.action IS NOT OLD.action OR NEW.operation_generation <> OLD.operation_generation
          OR NEW.expected_revision <> OLD.expected_revision
          OR NEW.binding_generation IS NOT OLD.binding_generation
          OR NEW.runtime_instance_id IS NOT OLD.runtime_instance_id
          OR NEW.manifest_sha256 <> OLD.manifest_sha256
          OR NEW.package_sha256 <> OLD.package_sha256
          OR NEW.idempotency_key <> OLD.idempotency_key
          OR NEW.request_json <> OLD.request_json OR NEW.request_sha256 <> OLD.request_sha256
          OR NEW.created_at <> OLD.created_at OR NEW.version <> OLD.version + 1
          OR NOT (
              (OLD.state = 'accepted' AND NEW.state IN ('authorized', 'failed'))
              OR (OLD.state = 'authorized' AND NEW.state IN ('dispatching', 'failed'))
              OR (OLD.state = 'dispatching' AND NEW.state IN ('succeeded', 'failed', 'ambiguous'))
              OR (OLD.state = 'ambiguous' AND NEW.state = 'reconciliation_required')
              OR (OLD.state = 'reconciliation_required'
                  AND NEW.state IN ('reconciled_succeeded', 'reconciled_failed'))
          )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_operation_transition_invalid');
        END
        """,
        """
        CREATE TRIGGER workspace_component_operation_transition_binding
        BEFORE UPDATE ON workspace_component_operation
        WHEN NOT EXISTS (
            SELECT 1 FROM workspace_component_operation_transition AS transition
            WHERE transition.operation_id = NEW.id
              AND transition.sequence = NEW.version
              AND transition.state = NEW.state
              AND transition.evidence_sha256 IS NEW.evidence_sha256
        )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_operation_transition_missing');
        END
        """,
        """
        CREATE TRIGGER workspace_component_effect_identity_guard
        BEFORE UPDATE ON workspace_component_effect
        WHEN NEW.id <> OLD.id OR NEW.operation_id <> OLD.operation_id
          OR NEW.workspace_id <> OLD.workspace_id OR NEW.effect_kind <> OLD.effect_kind
          OR NEW.logical_target_id <> OLD.logical_target_id
          OR NEW.request_sha256 <> OLD.request_sha256 OR NEW.created_at <> OLD.created_at
          OR NEW.version <> OLD.version + 1
          OR NOT (
              (OLD.state = 'pending' AND NEW.state IN ('committed', 'failed', 'unknown'))
              OR (OLD.state = 'unknown' AND NEW.state = 'reconciliation_required')
              OR (OLD.state = 'reconciliation_required'
                  AND NEW.state IN ('reconciled_committed', 'reconciled_failed'))
          )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_effect_transition_invalid');
        END
        """,
        """
        CREATE TRIGGER workspace_component_effect_transition_binding
        BEFORE UPDATE ON workspace_component_effect
        WHEN NOT EXISTS (
            SELECT 1 FROM workspace_component_effect_transition AS transition
            WHERE transition.effect_id = NEW.id
              AND transition.sequence = NEW.version
              AND transition.state = NEW.state
              AND transition.evidence_sha256 IS NEW.evidence_sha256
        )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_effect_transition_missing');
        END
        """,
        """
        CREATE TRIGGER workspace_component_lifecycle_receipt_binding
        BEFORE INSERT ON workspace_component_lifecycle_receipt
        WHEN NOT EXISTS (
            SELECT 1 FROM workspace_component_operation AS operation
            JOIN workspace_component_effect AS effect
              ON effect.id = NEW.effect_id
             AND effect.operation_id = operation.id
             AND effect.workspace_id = operation.workspace_id
            JOIN workspace_component_lifecycle_dispatch AS dispatch
              ON dispatch.operation_id = operation.id
             AND dispatch.effect_id = effect.id
            JOIN component_catalog_version AS catalog
              ON catalog.component_id = operation.component_id
             AND catalog.adapter_id = NEW.adapter_id
            WHERE operation.id = NEW.operation_id
              AND operation.workspace_id = NEW.workspace_id
              AND operation.component_id = NEW.component_id
              AND operation.installation_id IS NEW.installation_id
              AND operation.binding_generation IS NEW.binding_generation
              AND operation.request_sha256 = NEW.request_sha256
              AND operation.manifest_sha256 = NEW.manifest_sha256
              AND operation.package_sha256 = NEW.package_sha256
              AND operation.state = 'dispatching'
              AND effect.state = 'pending'
              AND dispatch.installation_id IS NEW.installation_id
              AND dispatch.binding_generation IS NEW.binding_generation
              AND dispatch.reserved_runtime_instance_id IS NEW.runtime_instance_id
              AND dispatch.workload_identity_digest IS NEW.workload_identity_digest
              AND dispatch.adapter_id = NEW.adapter_id
              AND dispatch.request_sha256 = NEW.request_sha256
              AND dispatch.manifest_sha256 = NEW.manifest_sha256
              AND dispatch.package_sha256 = NEW.package_sha256
        ) OR (
            NEW.outcome = 'succeeded' AND NEW.runtime_instance_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM workspace_component_runtime_instance AS runtime
                WHERE runtime.id = NEW.runtime_instance_id
                  AND runtime.workspace_id = NEW.workspace_id
                  AND runtime.installation_id = NEW.installation_id
                  AND runtime.generation = NEW.binding_generation
                  AND runtime.workload_identity_digest = NEW.workload_identity_digest
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_lifecycle_receipt_binding_invalid');
        END
        """,
        """
        CREATE TRIGGER workspace_component_invocation_receipt_binding
        BEFORE INSERT ON workspace_component_invocation_receipt
        WHEN NOT EXISTS (
            SELECT 1 FROM workspace_component_operation AS operation
            JOIN workspace_component_budget_reservation AS reservation
              ON reservation.operation_id = operation.id
            WHERE operation.id = NEW.operation_id
              AND operation.kind = 'invoke'
              AND operation.workspace_id = NEW.workspace_id
              AND operation.runtime_instance_id = NEW.runtime_instance_id
              AND operation.request_sha256 = NEW.request_sha256
              AND operation.state = 'dispatching'
              AND reservation.grant_id = NEW.grant_id
              AND reservation.workspace_id = NEW.workspace_id
              AND reservation.runtime_instance_id = NEW.runtime_instance_id
              AND reservation.request_sha256 = NEW.request_sha256
              AND reservation.bytes_out_reserved = NEW.reserved_bytes_out
              AND reservation.tokens_reserved = NEW.reserved_tokens
              AND reservation.wall_time_ms_reserved = NEW.reserved_wall_time_ms
        )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_invocation_receipt_binding_invalid');
        END
        """,
        """
        CREATE TRIGGER workspace_component_emergency_receipt_binding
        BEFORE INSERT ON workspace_component_emergency_receipt
        WHEN NOT EXISTS (
            SELECT 1 FROM workspace_component_operation AS operation
            JOIN workspace_component_effect AS effect
              ON effect.id = NEW.effect_id
             AND effect.operation_id = operation.id
             AND effect.workspace_id = operation.workspace_id
            WHERE operation.id = NEW.operation_id
              AND operation.kind = 'emergency_stop'
              AND operation.workspace_id = NEW.workspace_id
              AND operation.component_id = NEW.component_id
              AND operation.installation_id = NEW.installation_id
              AND operation.request_sha256 = NEW.request_sha256
              AND operation.state = 'dispatching'
              AND effect.effect_kind = 'emergency_stop'
              AND effect.state = 'pending'
              AND effect.request_sha256 = NEW.request_sha256
        )
        BEGIN
            SELECT RAISE(ABORT, 'desktop_component_emergency_receipt_binding_invalid');
        END
        """,
        """
        INSERT INTO workbench_component_slot
        (slot_id, slot_kind, component_allowed, created_at) VALUES
        ('editor.component', 'editor', 1, '1970-01-01T00:00:00.000000Z'),
        ('sidebar.component', 'sidebar', 1, '1970-01-01T00:00:00.000000Z'),
        ('settings.component', 'settings', 1, '1970-01-01T00:00:00.000000Z'),
        ('status.component', 'status', 1, '1970-01-01T00:00:00.000000Z'),
        ('settings.center', 'settings', 0, '1970-01-01T00:00:00.000000Z')
        """,
        *_COMPONENT_CATALOG_SEEDS,
    ),
)

DESKTOP_MIGRATIONS = (
    DESKTOP_0001,
    DESKTOP_0002,
    DESKTOP_0003,
    DESKTOP_0004,
    DESKTOP_0005,
    DESKTOP_0006,
    DESKTOP_0007,
    DESKTOP_0008,
    DESKTOP_0009,
    DESKTOP_0010,
    DESKTOP_0011,
)
