"""P5.2B durable Agent Task ledger foundation (engineering-only).

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-04

This global-only revision persists the P5.2A logical Task ledger.  It does not
activate Agent Runtime, a model provider, tools, or any Phase 5 feature gate.
Tenant migrations deliberately remain a no-op while still advancing their
Alembic revision row to the unique repository head.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "omnibase_meta"

_DDL: tuple[str, ...] = (
    "CREATE TABLE omnibase_meta.agent_tasks (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tworkspace_generation INTEGER NOT NULL, \n\tactor_user_id UUID NOT NULL, \n\tagent_definition_id UUID NOT NULL, \n\tagent_version_id UUID NOT NULL, \n\tagent_version_digest VARCHAR(64) NOT NULL, \n\tworkspace_agent_binding_id UUID NOT NULL, \n\ttask_generation INTEGER DEFAULT 1 NOT NULL, \n\tplan_id UUID NOT NULL, \n\tplan_version INTEGER NOT NULL, \n\tplan_digest VARCHAR(64) NOT NULL, \n\tdeadline TIMESTAMP WITH TIME ZONE NOT NULL, \n\tstate VARCHAR(24) DEFAULT 'created' NOT NULL, \n\tresource_scope_digest VARCHAR(64) NOT NULL, \n\tbudget_policy_digest VARCHAR(64) NOT NULL, \n\trequest_hash VARCHAR(64) NOT NULL, \n\tapproval_id UUID, \n\tcreation_operation_id UUID, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT agent_tasks_state_check CHECK (state IN ('created', 'planning', 'awaiting_approval', 'scheduled', 'running', 'paused', 'blocked_unknown', 'succeeded', 'failed', 'cancelled')), \n\tCONSTRAINT agent_tasks_workspace_generation_check CHECK (workspace_generation >= 1), \n\tCONSTRAINT agent_tasks_generation_check CHECK (task_generation >= 1), \n\tCONSTRAINT agent_tasks_plan_version_check CHECK (plan_version >= 1), \n\tCONSTRAINT agent_tasks_version_digest_check CHECK (agent_version_digest ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT agent_tasks_plan_digest_check CHECK (plan_digest ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT agent_tasks_resource_scope_digest_check CHECK (resource_scope_digest ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT agent_tasks_budget_policy_digest_check CHECK (budget_policy_digest ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT agent_tasks_request_hash_check CHECK (request_hash ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT agent_tasks_deadline_check CHECK (deadline > created_at), \n\tCONSTRAINT agent_tasks_id_tenant_uq UNIQUE (id, tenant_id), \n\tCONSTRAINT agent_tasks_workspace_tenant_fk FOREIGN KEY(workspace_id, tenant_id) REFERENCES omnibase_meta.workspaces (id, tenant_id) ON DELETE RESTRICT, \n\tCONSTRAINT agent_tasks_definition_tenant_fk FOREIGN KEY(agent_definition_id, tenant_id) REFERENCES omnibase_meta.agent_definitions (id, tenant_id) ON DELETE RESTRICT, \n\tCONSTRAINT agent_tasks_version_tenant_fk FOREIGN KEY(agent_version_id, tenant_id) REFERENCES omnibase_meta.agent_versions (id, tenant_id) ON DELETE RESTRICT, \n\tCONSTRAINT agent_tasks_binding_tenant_fk FOREIGN KEY(workspace_agent_binding_id, tenant_id) REFERENCES omnibase_meta.workspace_agent_bindings (id, tenant_id) ON DELETE RESTRICT, \n\tCONSTRAINT agent_tasks_approval_tenant_fk FOREIGN KEY(approval_id, tenant_id) REFERENCES omnibase_meta.approval_requests (id, tenant_id) ON DELETE RESTRICT, \n\tCONSTRAINT agent_tasks_operation_tenant_fk FOREIGN KEY(creation_operation_id, tenant_id) REFERENCES omnibase_meta.operations (id, tenant_id) ON DELETE RESTRICT, \n\tCONSTRAINT agent_tasks_resource_tenant_fk FOREIGN KEY(id, tenant_id) REFERENCES omnibase_meta.resource_registry (id, tenant_id) ON DELETE RESTRICT, \n\tFOREIGN KEY(tenant_id) REFERENCES omnibase_meta.tenants (id) ON DELETE RESTRICT\n)",
    "CREATE INDEX agent_tasks_binding_state_idx ON omnibase_meta.agent_tasks (tenant_id, workspace_agent_binding_id, state)",
    "CREATE INDEX agent_tasks_workspace_state_idx ON omnibase_meta.agent_tasks (tenant_id, workspace_id, state, created_at)",
    "CREATE TABLE omnibase_meta.agent_runs (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\ttask_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tworkspace_generation INTEGER NOT NULL, \n\tworkspace_run_id UUID NOT NULL, \n\truntime_instance_id UUID, \n\tworkload_identity_digest VARCHAR(64), \n\tnode_id UUID, \n\tnode_fencing_token BIGINT, \n\trun_lease_id UUID, \n\trun_fencing_token BIGINT, \n\tstate VARCHAR(16) DEFAULT 'created' NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT agent_runs_state_check CHECK (state IN ('created', 'leased', 'running', 'paused', 'succeeded', 'failed', 'cancelled')), \n\tCONSTRAINT agent_runs_workspace_generation_check CHECK (workspace_generation >= 1), \n\tCONSTRAINT agent_runs_binding_state_check CHECK ((state = 'created' AND run_lease_id IS NULL AND run_fencing_token IS NULL AND node_id IS NULL AND node_fencing_token IS NULL AND runtime_instance_id IS NULL AND workload_identity_digest IS NULL) OR (state IN ('leased', 'running', 'paused') AND run_lease_id IS NOT NULL AND run_fencing_token IS NOT NULL AND node_id IS NOT NULL AND node_fencing_token IS NOT NULL AND runtime_instance_id IS NOT NULL AND workload_identity_digest IS NOT NULL) OR (state IN ('succeeded', 'failed', 'cancelled') AND run_lease_id IS NULL AND run_fencing_token IS NULL AND node_id IS NULL AND node_fencing_token IS NULL AND runtime_instance_id IS NULL AND workload_identity_digest IS NULL)), \n\tCONSTRAINT agent_runs_workload_digest_check CHECK (workload_identity_digest IS NULL OR workload_identity_digest ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT agent_runs_id_tenant_uq UNIQUE (id, tenant_id), \n\tCONSTRAINT agent_runs_id_task_tenant_uq UNIQUE (id, task_id, tenant_id), \n\tCONSTRAINT agent_runs_task_tenant_fk FOREIGN KEY(task_id, tenant_id) REFERENCES omnibase_meta.agent_tasks (id, tenant_id) ON DELETE RESTRICT, \n\tCONSTRAINT agent_runs_workspace_run_tenant_fk FOREIGN KEY(workspace_run_id, tenant_id) REFERENCES omnibase_meta.workspace_runs (id, tenant_id) ON DELETE RESTRICT, \n\tCONSTRAINT agent_runs_node_tenant_fk FOREIGN KEY(node_id, tenant_id) REFERENCES omnibase_meta.workspace_nodes (id, tenant_id) ON DELETE RESTRICT, \n\tCONSTRAINT agent_runs_run_lease_tenant_fk FOREIGN KEY(run_lease_id, tenant_id) REFERENCES omnibase_meta.run_leases (id, tenant_id) ON DELETE RESTRICT\n)",
    "CREATE INDEX agent_runs_task_state_idx ON omnibase_meta.agent_runs (tenant_id, task_id, state, created_at)",
    "CREATE TABLE omnibase_meta.agent_steps (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\ttask_id UUID NOT NULL, \n\tagent_run_id UUID NOT NULL, \n\tstep_number INTEGER NOT NULL, \n\tplan_id UUID NOT NULL, \n\tplan_version INTEGER NOT NULL, \n\tplan_digest VARCHAR(64) NOT NULL, \n\tstate VARCHAR(16) DEFAULT 'pending' NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT agent_steps_state_check CHECK (state IN ('pending', 'ready', 'running', 'succeeded', 'failed', 'cancelled')), \n\tCONSTRAINT agent_steps_number_check CHECK (step_number >= 1), \n\tCONSTRAINT agent_steps_plan_version_check CHECK (plan_version >= 1), \n\tCONSTRAINT agent_steps_plan_digest_check CHECK (plan_digest ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT agent_steps_id_tenant_uq UNIQUE (id, tenant_id), \n\tCONSTRAINT agent_steps_binding_uq UNIQUE (id, task_id, agent_run_id, tenant_id), \n\tCONSTRAINT agent_steps_number_uq UNIQUE (task_id, agent_run_id, step_number, tenant_id), \n\tCONSTRAINT agent_steps_task_tenant_fk FOREIGN KEY(task_id, tenant_id) REFERENCES omnibase_meta.agent_tasks (id, tenant_id) ON DELETE RESTRICT, \n\tCONSTRAINT agent_steps_run_task_tenant_fk FOREIGN KEY(agent_run_id, task_id, tenant_id) REFERENCES omnibase_meta.agent_runs (id, task_id, tenant_id) ON DELETE RESTRICT\n)",
    "CREATE INDEX agent_steps_task_state_idx ON omnibase_meta.agent_steps (tenant_id, task_id, state, step_number)",
    "CREATE TABLE omnibase_meta.agent_step_dependencies (\n\tstep_id UUID NOT NULL, \n\tdepends_on_step_id UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\ttask_id UUID NOT NULL, \n\tagent_run_id UUID NOT NULL, \n\tPRIMARY KEY (step_id, depends_on_step_id, tenant_id), \n\tCONSTRAINT agent_step_dependencies_self_check CHECK (step_id <> depends_on_step_id), \n\tCONSTRAINT agent_step_dependencies_step_fk FOREIGN KEY(step_id, task_id, agent_run_id, tenant_id) REFERENCES omnibase_meta.agent_steps (id, task_id, agent_run_id, tenant_id) ON DELETE RESTRICT, \n\tCONSTRAINT agent_step_dependencies_parent_fk FOREIGN KEY(depends_on_step_id, task_id, agent_run_id, tenant_id) REFERENCES omnibase_meta.agent_steps (id, task_id, agent_run_id, tenant_id) ON DELETE RESTRICT\n)",
    "CREATE TABLE omnibase_meta.agent_attempts (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\ttask_id UUID NOT NULL, \n\tstep_id UUID NOT NULL, \n\tagent_run_id UUID NOT NULL, \n\tattempt_number INTEGER NOT NULL, \n\tstate VARCHAR(16) DEFAULT 'pending' NOT NULL, \n\ttask_lease_id UUID, \n\ttask_fencing_token BIGINT, \n\texpected_previous_state VARCHAR(16) NOT NULL, \n\tdeadline TIMESTAMP WITH TIME ZONE NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT agent_attempts_state_check CHECK (state IN ('pending', 'ready', 'leased', 'dispatching', 'running', 'committed', 'failed', 'unknown', 'cancelled')), \n\tCONSTRAINT agent_attempts_number_check CHECK (attempt_number >= 1), \n\tCONSTRAINT agent_attempts_lease_state_check CHECK ((state IN ('pending', 'ready') AND task_lease_id IS NULL AND task_fencing_token IS NULL) OR (state IN ('leased', 'dispatching', 'running') AND task_lease_id IS NOT NULL AND task_fencing_token IS NOT NULL) OR (state IN ('committed', 'failed', 'unknown', 'cancelled') AND task_lease_id IS NULL AND task_fencing_token IS NULL)), \n\tCONSTRAINT agent_attempts_deadline_check CHECK (deadline > created_at), \n\tCONSTRAINT agent_attempts_id_tenant_uq UNIQUE (id, tenant_id), \n\tCONSTRAINT agent_attempts_binding_uq UNIQUE (id, task_id, agent_run_id, tenant_id), \n\tCONSTRAINT agent_attempts_number_uq UNIQUE (task_id, step_id, attempt_number, tenant_id), \n\tCONSTRAINT agent_attempts_task_tenant_fk FOREIGN KEY(task_id, tenant_id) REFERENCES omnibase_meta.agent_tasks (id, tenant_id) ON DELETE RESTRICT, \n\tCONSTRAINT agent_attempts_step_binding_fk FOREIGN KEY(step_id, task_id, agent_run_id, tenant_id) REFERENCES omnibase_meta.agent_steps (id, task_id, agent_run_id, tenant_id) ON DELETE RESTRICT\n)",
    "CREATE INDEX agent_attempts_step_state_idx ON omnibase_meta.agent_attempts (tenant_id, step_id, state, attempt_number)",
    "CREATE TABLE omnibase_meta.agent_task_leases (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\ttask_id UUID NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tagent_run_id UUID NOT NULL, \n\trun_lease_id UUID NOT NULL, \n\trun_fencing_token BIGINT NOT NULL, \n\tnode_id UUID NOT NULL, \n\tnode_fencing_token BIGINT NOT NULL, \n\tworkspace_generation INTEGER NOT NULL, \n\ttask_fencing_token BIGINT NOT NULL, \n\tstate VARCHAR(16) DEFAULT 'active' NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\theartbeat_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT agent_task_leases_state_check CHECK (state IN ('active', 'expired', 'revoked', 'completed')), \n\tCONSTRAINT agent_task_leases_fencing_check CHECK (task_fencing_token >= 1), \n\tCONSTRAINT agent_task_leases_run_fencing_check CHECK (run_fencing_token >= 1), \n\tCONSTRAINT agent_task_leases_node_fencing_check CHECK (node_fencing_token >= 1), \n\tCONSTRAINT agent_task_leases_workspace_generation_check CHECK (workspace_generation >= 1), \n\tCONSTRAINT agent_task_leases_expiry_check CHECK (expires_at > created_at), \n\tCONSTRAINT agent_task_leases_ttl_ceiling_check CHECK (expires_at - created_at <= interval '300 seconds'), \n\tCONSTRAINT agent_task_leases_heartbeat_window_check CHECK (heartbeat_at IS NULL OR (heartbeat_at >= created_at AND heartbeat_at <= expires_at)), \n\tCONSTRAINT agent_task_leases_completed_heartbeat_check CHECK (state <> 'completed' OR heartbeat_at IS NOT NULL), \n\tCONSTRAINT agent_task_leases_id_tenant_uq UNIQUE (id, tenant_id), \n\tCONSTRAINT agent_task_leases_binding_uq UNIQUE (id, task_id, agent_run_id, tenant_id), \n\tCONSTRAINT agent_task_leases_fencing_uq UNIQUE (task_id, task_fencing_token, tenant_id), \n\tCONSTRAINT agent_task_leases_task_tenant_fk FOREIGN KEY(task_id, tenant_id) REFERENCES omnibase_meta.agent_tasks (id, tenant_id) ON DELETE RESTRICT, \n\tCONSTRAINT agent_task_leases_attempt_binding_fk FOREIGN KEY(attempt_id, task_id, agent_run_id, tenant_id) REFERENCES omnibase_meta.agent_attempts (id, task_id, agent_run_id, tenant_id) ON DELETE RESTRICT, \n\tCONSTRAINT agent_task_leases_run_lease_tenant_fk FOREIGN KEY(run_lease_id, tenant_id) REFERENCES omnibase_meta.run_leases (id, tenant_id) ON DELETE RESTRICT, \n\tCONSTRAINT agent_task_leases_node_tenant_fk FOREIGN KEY(node_id, tenant_id) REFERENCES omnibase_meta.workspace_nodes (id, tenant_id) ON DELETE RESTRICT\n)",
    "CREATE UNIQUE INDEX agent_task_leases_active_attempt_uq ON omnibase_meta.agent_task_leases (attempt_id, tenant_id) WHERE state = 'active'",
    "CREATE INDEX agent_task_leases_task_state_idx ON omnibase_meta.agent_task_leases (tenant_id, task_id, state, created_at)",
    "CREATE TABLE omnibase_meta.agent_task_fencing_cursors (\n\ttask_id UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tnext_fencing_token BIGINT DEFAULT 1 NOT NULL, \n\tlast_claimed_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (task_id, tenant_id), \n\tCONSTRAINT agent_task_fencing_cursor_next_check CHECK (next_fencing_token >= 1), \n\tCONSTRAINT agent_task_fencing_cursor_task_fk FOREIGN KEY(task_id, tenant_id) REFERENCES omnibase_meta.agent_tasks (id, tenant_id) ON DELETE RESTRICT\n)",
    "CREATE TABLE omnibase_meta.agent_task_budget_ledgers (\n\ttask_id UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tdimension VARCHAR(32) NOT NULL, \n\tlimit_value BIGINT NOT NULL, \n\treserved BIGINT DEFAULT 0 NOT NULL, \n\tcommitted BIGINT DEFAULT 0 NOT NULL, \n\treleased BIGINT DEFAULT 0 NOT NULL, \n\tremaining BIGINT NOT NULL, \n\tpolicy_digest VARCHAR(64) NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL, \n\tPRIMARY KEY (task_id, tenant_id, dimension), \n\tCONSTRAINT agent_task_budget_dimension_check CHECK (dimension IN ('input_tokens', 'output_tokens', 'reasoning_tokens', 'total_tokens', 'cost_micros', 'model_calls', 'tool_calls', 'wall_clock_ms', 'artifact_bytes', 'sandbox_jobs', 'max_attempts', 'max_parallel_steps')), \n\tCONSTRAINT agent_task_budget_limit_check CHECK (limit_value >= 1), \n\tCONSTRAINT agent_task_budget_nonnegative_check CHECK (reserved >= 0 AND committed >= 0 AND released >= 0 AND remaining >= 0), \n\tCONSTRAINT agent_task_budget_order_check CHECK (committed <= reserved AND reserved <= limit_value AND released <= committed), \n\tCONSTRAINT agent_task_budget_remaining_check CHECK (remaining = limit_value - reserved), \n\tCONSTRAINT agent_task_budget_policy_digest_check CHECK (policy_digest ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT agent_task_budget_task_fk FOREIGN KEY(task_id, tenant_id) REFERENCES omnibase_meta.agent_tasks (id, tenant_id) ON DELETE RESTRICT\n)",
    "CREATE TABLE omnibase_meta.agent_task_effects (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\ttask_id UUID NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tagent_run_id UUID NOT NULL, \n\toperation_id UUID NOT NULL, \n\trequest_hash VARCHAR(64) NOT NULL, \n\tresult_digest VARCHAR(64), \n\tstate VARCHAR(16) DEFAULT 'reserved' NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT agent_task_effects_state_check CHECK (state IN ('reserved', 'dispatching', 'committed', 'failed', 'unknown')), \n\tCONSTRAINT agent_task_effects_request_hash_check CHECK (request_hash ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT agent_task_effects_result_digest_check CHECK (result_digest IS NULL OR result_digest ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT agent_task_effects_result_state_check CHECK ((state = 'committed' AND result_digest IS NOT NULL) OR (state <> 'committed' AND result_digest IS NULL)), \n\tCONSTRAINT agent_task_effects_id_tenant_uq UNIQUE (id, tenant_id), \n\tCONSTRAINT agent_task_effects_task_fk FOREIGN KEY(task_id, tenant_id) REFERENCES omnibase_meta.agent_tasks (id, tenant_id) ON DELETE RESTRICT, \n\tCONSTRAINT agent_task_effects_attempt_fk FOREIGN KEY(attempt_id, task_id, agent_run_id, tenant_id) REFERENCES omnibase_meta.agent_attempts (id, task_id, agent_run_id, tenant_id) ON DELETE RESTRICT, \n\tCONSTRAINT agent_task_effects_operation_fk FOREIGN KEY(operation_id, tenant_id) REFERENCES omnibase_meta.operations (id, tenant_id) ON DELETE RESTRICT\n)",
    "CREATE INDEX agent_task_effects_attempt_state_idx ON omnibase_meta.agent_task_effects (tenant_id, attempt_id, state)",
    "CREATE TABLE omnibase_meta.agent_checkpoints (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\ttask_id UUID NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tagent_run_id UUID NOT NULL, \n\tcommitted_plan_version INTEGER NOT NULL, \n\tcommitted_plan_digest VARCHAR(64) NOT NULL, \n\tcommitted_attempt_results JSONB NOT NULL, \n\tbudget_snapshot JSONB NOT NULL, \n\tbudget_policy_digest VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT agent_checkpoints_plan_version_check CHECK (committed_plan_version >= 1), \n\tCONSTRAINT agent_checkpoints_plan_digest_check CHECK (committed_plan_digest ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT agent_checkpoints_budget_digest_check CHECK (budget_policy_digest ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT agent_checkpoints_results_check CHECK (jsonb_typeof(committed_attempt_results) = 'array' AND jsonb_array_length(committed_attempt_results) >= 1), \n\tCONSTRAINT agent_checkpoints_budget_check CHECK (jsonb_typeof(budget_snapshot) = 'object'), \n\tCONSTRAINT agent_checkpoints_id_tenant_uq UNIQUE (id, tenant_id), \n\tCONSTRAINT agent_checkpoints_task_fk FOREIGN KEY(task_id, tenant_id) REFERENCES omnibase_meta.agent_tasks (id, tenant_id) ON DELETE RESTRICT, \n\tCONSTRAINT agent_checkpoints_attempt_fk FOREIGN KEY(attempt_id, task_id, agent_run_id, tenant_id) REFERENCES omnibase_meta.agent_attempts (id, task_id, agent_run_id, tenant_id) ON DELETE RESTRICT\n)",
    "CREATE INDEX agent_checkpoints_task_created_idx ON omnibase_meta.agent_checkpoints (tenant_id, task_id, created_at)",
    "CREATE TABLE omnibase_meta.agent_reconciliation_cases (\n\tid UUID DEFAULT gen_random_uuid() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\ttask_id UUID NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tagent_run_id UUID NOT NULL, \n\teffect_id UUID, \n\tstate VARCHAR(16) DEFAULT 'open' NOT NULL, \n\treason_code VARCHAR(64) NOT NULL, \n\tresolution_note TEXT, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL, \n\tresolved_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tCONSTRAINT agent_reconciliation_state_check CHECK (state IN ('open', 'resolved')), \n\tCONSTRAINT agent_reconciliation_reason_check CHECK (reason_code ~ '^[a-z][a-z0-9_]{2,63}$'), \n\tCONSTRAINT agent_reconciliation_resolved_check CHECK ((state = 'open' AND resolved_at IS NULL) OR (state = 'resolved' AND resolved_at IS NOT NULL)), \n\tCONSTRAINT agent_reconciliation_id_tenant_uq UNIQUE (id, tenant_id), \n\tCONSTRAINT agent_reconciliation_task_fk FOREIGN KEY(task_id, tenant_id) REFERENCES omnibase_meta.agent_tasks (id, tenant_id) ON DELETE RESTRICT, \n\tCONSTRAINT agent_reconciliation_attempt_fk FOREIGN KEY(attempt_id, task_id, agent_run_id, tenant_id) REFERENCES omnibase_meta.agent_attempts (id, task_id, agent_run_id, tenant_id) ON DELETE RESTRICT, \n\tCONSTRAINT agent_reconciliation_effect_fk FOREIGN KEY(effect_id, tenant_id) REFERENCES omnibase_meta.agent_task_effects (id, tenant_id) ON DELETE RESTRICT\n)",
    "CREATE INDEX agent_reconciliation_task_state_idx ON omnibase_meta.agent_reconciliation_cases (tenant_id, task_id, state)",
    "ALTER TABLE omnibase_meta.agent_attempts ADD CONSTRAINT agent_attempts_current_lease_fk FOREIGN KEY(task_lease_id, task_id, agent_run_id, tenant_id) REFERENCES omnibase_meta.agent_task_leases (id, task_id, agent_run_id, tenant_id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED",
)

_TRIGGER_SQL: tuple[str, ...] = (
    """
    CREATE OR REPLACE FUNCTION omnibase_meta.agent_task_state_guard()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'UPDATE' THEN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
               OR NEW.workspace_generation IS DISTINCT FROM OLD.workspace_generation
               OR NEW.actor_user_id IS DISTINCT FROM OLD.actor_user_id
               OR NEW.agent_definition_id IS DISTINCT FROM OLD.agent_definition_id
               OR NEW.agent_version_id IS DISTINCT FROM OLD.agent_version_id
               OR NEW.agent_version_digest IS DISTINCT FROM OLD.agent_version_digest
               OR NEW.workspace_agent_binding_id IS DISTINCT FROM OLD.workspace_agent_binding_id
               OR NEW.task_generation IS DISTINCT FROM OLD.task_generation
               OR NEW.plan_id IS DISTINCT FROM OLD.plan_id
               OR NEW.plan_version IS DISTINCT FROM OLD.plan_version
               OR NEW.plan_digest IS DISTINCT FROM OLD.plan_digest
               OR NEW.deadline IS DISTINCT FROM OLD.deadline
               OR NEW.resource_scope_digest IS DISTINCT FROM OLD.resource_scope_digest
               OR NEW.budget_policy_digest IS DISTINCT FROM OLD.budget_policy_digest
               OR NEW.request_hash IS DISTINCT FROM OLD.request_hash
               OR NEW.approval_id IS DISTINCT FROM OLD.approval_id
               OR NEW.creation_operation_id IS DISTINCT FROM OLD.creation_operation_id
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'agent_task immutable identity changed' USING ERRCODE = '55000';
            END IF;
            IF NEW.state <> OLD.state AND NOT (
                (OLD.state = 'created' AND NEW.state IN
                    ('planning', 'awaiting_approval', 'scheduled', 'failed', 'cancelled'))
                OR (OLD.state = 'planning' AND NEW.state IN
                    ('awaiting_approval', 'scheduled', 'failed', 'cancelled'))
                OR (OLD.state = 'awaiting_approval' AND NEW.state IN
                    ('scheduled', 'failed', 'cancelled'))
                OR (OLD.state = 'scheduled' AND NEW.state IN
                    ('running', 'paused', 'failed', 'cancelled'))
                OR (OLD.state = 'running' AND NEW.state IN
                    ('paused', 'blocked_unknown', 'succeeded', 'failed', 'cancelled'))
                OR (OLD.state = 'paused' AND NEW.state IN
                    ('scheduled', 'running', 'failed', 'cancelled'))
            ) THEN
                RAISE EXCEPTION 'invalid agent_task state transition % -> %',
                    OLD.state, NEW.state USING ERRCODE = '55000';
            END IF;
            NEW.updated_at := clock_timestamp();
        END IF;
        RETURN NEW;
    END;
    $$;
    CREATE TRIGGER agent_task_state_guard
    BEFORE UPDATE ON omnibase_meta.agent_tasks
    FOR EACH ROW EXECUTE FUNCTION omnibase_meta.agent_task_state_guard();
    """,
    """
    CREATE OR REPLACE FUNCTION omnibase_meta.agent_run_state_guard()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'UPDATE' THEN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.task_id IS DISTINCT FROM OLD.task_id
               OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
               OR NEW.workspace_generation IS DISTINCT FROM OLD.workspace_generation
               OR NEW.workspace_run_id IS DISTINCT FROM OLD.workspace_run_id
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'agent_run immutable identity changed' USING ERRCODE = '55000';
            END IF;
            IF NEW.state <> OLD.state AND NOT (
                (OLD.state = 'created' AND NEW.state IN ('leased', 'failed', 'cancelled'))
                OR (OLD.state = 'leased' AND NEW.state IN
                    ('running', 'paused', 'failed', 'cancelled'))
                OR (OLD.state = 'running' AND NEW.state IN
                    ('paused', 'succeeded', 'failed', 'cancelled'))
                OR (OLD.state = 'paused' AND NEW.state IN
                    ('running', 'failed', 'cancelled'))
            ) THEN
                RAISE EXCEPTION 'invalid agent_run state transition % -> %',
                    OLD.state, NEW.state USING ERRCODE = '55000';
            END IF;
            NEW.updated_at := clock_timestamp();
        END IF;
        RETURN NEW;
    END;
    $$;
    CREATE TRIGGER agent_run_state_guard
    BEFORE UPDATE ON omnibase_meta.agent_runs
    FOR EACH ROW EXECUTE FUNCTION omnibase_meta.agent_run_state_guard();
    """,
    """
    CREATE OR REPLACE FUNCTION omnibase_meta.agent_step_guard()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        expected_number integer;
    BEGIN
        IF TG_OP = 'INSERT' THEN
            PERFORM 1 FROM omnibase_meta.agent_runs
             WHERE id = NEW.agent_run_id AND task_id = NEW.task_id
               AND tenant_id = NEW.tenant_id FOR UPDATE;
            SELECT COALESCE(MAX(step_number), 0) + 1 INTO expected_number
              FROM omnibase_meta.agent_steps
             WHERE task_id = NEW.task_id AND agent_run_id = NEW.agent_run_id
               AND tenant_id = NEW.tenant_id;
            IF NEW.step_number <> expected_number THEN
                RAISE EXCEPTION 'agent_step number must be contiguous from one'
                    USING ERRCODE = '55000';
            END IF;
        ELSE
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.task_id IS DISTINCT FROM OLD.task_id
               OR NEW.agent_run_id IS DISTINCT FROM OLD.agent_run_id
               OR NEW.step_number IS DISTINCT FROM OLD.step_number
               OR NEW.plan_id IS DISTINCT FROM OLD.plan_id
               OR NEW.plan_version IS DISTINCT FROM OLD.plan_version
               OR NEW.plan_digest IS DISTINCT FROM OLD.plan_digest
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'agent_step immutable identity changed' USING ERRCODE = '55000';
            END IF;
            IF NEW.state <> OLD.state AND NOT (
                (OLD.state = 'pending' AND NEW.state IN ('ready', 'cancelled'))
                OR (OLD.state = 'ready' AND NEW.state IN ('running', 'failed', 'cancelled'))
                OR (OLD.state = 'running' AND NEW.state IN
                    ('succeeded', 'failed', 'cancelled'))
            ) THEN
                RAISE EXCEPTION 'invalid agent_step state transition % -> %',
                    OLD.state, NEW.state USING ERRCODE = '55000';
            END IF;
            NEW.updated_at := clock_timestamp();
        END IF;
        RETURN NEW;
    END;
    $$;
    CREATE TRIGGER agent_step_guard
    BEFORE INSERT OR UPDATE ON omnibase_meta.agent_steps
    FOR EACH ROW EXECUTE FUNCTION omnibase_meta.agent_step_guard();
    """,
    """
    CREATE OR REPLACE FUNCTION omnibase_meta.agent_step_dependency_guard()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        cycle_found boolean;
    BEGIN
        PERFORM 1 FROM omnibase_meta.agent_runs
         WHERE id = NEW.agent_run_id AND task_id = NEW.task_id
           AND tenant_id = NEW.tenant_id FOR UPDATE;
        WITH RECURSIVE ancestors(step_id) AS (
            SELECT NEW.depends_on_step_id
            UNION
            SELECT d.depends_on_step_id
              FROM omnibase_meta.agent_step_dependencies d
              JOIN ancestors a ON d.step_id = a.step_id
             WHERE d.task_id = NEW.task_id
               AND d.agent_run_id = NEW.agent_run_id
               AND d.tenant_id = NEW.tenant_id
        )
        SELECT EXISTS (SELECT 1 FROM ancestors WHERE step_id = NEW.step_id)
          INTO cycle_found;
        IF cycle_found THEN
            RAISE EXCEPTION 'agent_step dependency cycle' USING ERRCODE = '55000';
        END IF;
        RETURN NEW;
    END;
    $$;
    CREATE TRIGGER agent_step_dependency_guard
    BEFORE INSERT OR UPDATE ON omnibase_meta.agent_step_dependencies
    FOR EACH ROW EXECUTE FUNCTION omnibase_meta.agent_step_dependency_guard();
    """,
    """
    CREATE OR REPLACE FUNCTION omnibase_meta.agent_attempt_guard()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        expected_number integer;
    BEGIN
        IF TG_OP = 'INSERT' THEN
            PERFORM 1 FROM omnibase_meta.agent_steps
             WHERE id = NEW.step_id AND task_id = NEW.task_id
               AND agent_run_id = NEW.agent_run_id AND tenant_id = NEW.tenant_id
             FOR UPDATE;
            SELECT COALESCE(MAX(attempt_number), 0) + 1 INTO expected_number
              FROM omnibase_meta.agent_attempts
             WHERE task_id = NEW.task_id AND step_id = NEW.step_id
               AND tenant_id = NEW.tenant_id;
            IF NEW.attempt_number <> expected_number THEN
                RAISE EXCEPTION 'agent_attempt number must be contiguous from one'
                    USING ERRCODE = '55000';
            END IF;
        ELSE
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.task_id IS DISTINCT FROM OLD.task_id
               OR NEW.step_id IS DISTINCT FROM OLD.step_id
               OR NEW.agent_run_id IS DISTINCT FROM OLD.agent_run_id
               OR NEW.attempt_number IS DISTINCT FROM OLD.attempt_number
               OR NEW.expected_previous_state IS DISTINCT FROM OLD.expected_previous_state
               OR NEW.deadline IS DISTINCT FROM OLD.deadline
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'agent_attempt immutable identity changed' USING ERRCODE = '55000';
            END IF;
            IF NEW.state <> OLD.state AND NOT (
                (OLD.state = 'pending' AND NEW.state IN ('ready', 'cancelled'))
                OR (OLD.state = 'ready' AND NEW.state IN ('leased', 'failed', 'cancelled'))
                OR (OLD.state = 'leased' AND NEW.state IN
                    ('dispatching', 'running', 'failed', 'unknown', 'cancelled'))
                OR (OLD.state = 'dispatching' AND NEW.state IN
                    ('running', 'committed', 'failed', 'unknown', 'cancelled'))
                OR (OLD.state = 'running' AND NEW.state IN
                    ('committed', 'failed', 'unknown', 'cancelled'))
            ) THEN
                RAISE EXCEPTION 'invalid agent_attempt state transition % -> %',
                    OLD.state, NEW.state USING ERRCODE = '55000';
            END IF;
            NEW.updated_at := clock_timestamp();
        END IF;
        RETURN NEW;
    END;
    $$;
    CREATE TRIGGER agent_attempt_guard
    BEFORE INSERT OR UPDATE ON omnibase_meta.agent_attempts
    FOR EACH ROW EXECUTE FUNCTION omnibase_meta.agent_attempt_guard();
    """,
    """
    CREATE OR REPLACE FUNCTION omnibase_meta.agent_task_lease_guard()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        cursor_row omnibase_meta.agent_task_fencing_cursors%ROWTYPE;
        run_row omnibase_meta.agent_runs%ROWTYPE;
        newer_exists boolean;
    BEGIN
        IF TG_OP = 'INSERT' THEN
            SELECT * INTO cursor_row
              FROM omnibase_meta.agent_task_fencing_cursors
             WHERE task_id = NEW.task_id AND tenant_id = NEW.tenant_id
             FOR UPDATE;
            IF NOT FOUND
               OR NEW.task_fencing_token <> cursor_row.next_fencing_token - 1
               OR NEW.created_at IS DISTINCT FROM cursor_row.last_claimed_at THEN
                RAISE EXCEPTION 'agent_task lease was not allocated by the locked cursor'
                    USING ERRCODE = '55000';
            END IF;
            SELECT EXISTS (
                SELECT 1 FROM omnibase_meta.agent_task_leases
                 WHERE task_id = NEW.task_id AND tenant_id = NEW.tenant_id
                   AND (created_at >= NEW.created_at
                        OR task_fencing_token >= NEW.task_fencing_token)
            ) INTO newer_exists;
            IF newer_exists THEN
                RAISE EXCEPTION 'agent_task lease chronology or fencing token regressed'
                    USING ERRCODE = '55000';
            END IF;
            SELECT * INTO run_row
              FROM omnibase_meta.agent_runs
             WHERE id = NEW.agent_run_id AND task_id = NEW.task_id
               AND tenant_id = NEW.tenant_id FOR UPDATE;
            IF NOT FOUND OR run_row.state NOT IN ('leased', 'running', 'paused')
               OR run_row.run_lease_id IS DISTINCT FROM NEW.run_lease_id
               OR run_row.run_fencing_token IS DISTINCT FROM NEW.run_fencing_token
               OR run_row.node_id IS DISTINCT FROM NEW.node_id
               OR run_row.node_fencing_token IS DISTINCT FROM NEW.node_fencing_token
               OR run_row.workspace_generation IS DISTINCT FROM NEW.workspace_generation THEN
                RAISE EXCEPTION 'agent_task lease has stale run, node, or workspace fencing'
                    USING ERRCODE = '55000';
            END IF;
        ELSE
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.task_id IS DISTINCT FROM OLD.task_id
               OR NEW.attempt_id IS DISTINCT FROM OLD.attempt_id
               OR NEW.agent_run_id IS DISTINCT FROM OLD.agent_run_id
               OR NEW.run_lease_id IS DISTINCT FROM OLD.run_lease_id
               OR NEW.run_fencing_token IS DISTINCT FROM OLD.run_fencing_token
               OR NEW.node_id IS DISTINCT FROM OLD.node_id
               OR NEW.node_fencing_token IS DISTINCT FROM OLD.node_fencing_token
               OR NEW.workspace_generation IS DISTINCT FROM OLD.workspace_generation
               OR NEW.task_fencing_token IS DISTINCT FROM OLD.task_fencing_token
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'agent_task lease identity is immutable' USING ERRCODE = '55000';
            END IF;
            IF OLD.state <> 'active' THEN
                IF NEW IS DISTINCT FROM OLD THEN
                    RAISE EXCEPTION 'terminal agent_task lease is immutable'
                        USING ERRCODE = '55000';
                END IF;
            ELSIF NEW.state NOT IN ('active', 'expired', 'revoked', 'completed') THEN
                RAISE EXCEPTION 'invalid agent_task lease transition' USING ERRCODE = '55000';
            END IF;
            NEW.updated_at := clock_timestamp();
        END IF;
        RETURN NEW;
    END;
    $$;
    CREATE TRIGGER agent_task_lease_guard
    BEFORE INSERT OR UPDATE ON omnibase_meta.agent_task_leases
    FOR EACH ROW EXECUTE FUNCTION omnibase_meta.agent_task_lease_guard();
    """,
    """
    CREATE OR REPLACE FUNCTION omnibase_meta.agent_attempt_lease_consistency_guard()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        target_attempt omnibase_meta.agent_attempts%ROWTYPE;
        active_count integer;
    BEGIN
        -- Always evaluate against the LIVE row, never the deferred event
        -- snapshot: a deferred constraint trigger re-runs every queued event
        -- (INSERT then UPDATE) at commit time, and the INSERT snapshot of an
        -- attempt that was created ``ready`` and claimed later in the same
        -- transaction would falsely look like a cleared attempt next to the
        -- freshly created active lease.  The live row is the final state.
        IF TG_TABLE_NAME = 'agent_attempts' THEN
            SELECT * INTO target_attempt
              FROM omnibase_meta.agent_attempts
             WHERE id = NEW.id AND task_id = NEW.task_id
               AND agent_run_id = NEW.agent_run_id AND tenant_id = NEW.tenant_id;
        ELSE
            SELECT * INTO target_attempt
              FROM omnibase_meta.agent_attempts
             WHERE id = NEW.attempt_id AND task_id = NEW.task_id
               AND agent_run_id = NEW.agent_run_id AND tenant_id = NEW.tenant_id;
        END IF;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        SELECT COUNT(*) INTO active_count
          FROM omnibase_meta.agent_task_leases
         WHERE attempt_id = target_attempt.id
           AND task_id = target_attempt.task_id
           AND agent_run_id = target_attempt.agent_run_id
           AND tenant_id = target_attempt.tenant_id
           AND state = 'active';
        IF target_attempt.task_lease_id IS NULL THEN
            IF active_count <> 0 THEN
                RAISE EXCEPTION 'active task lease is orphaned from its attempt'
                    USING ERRCODE = '55000';
            END IF;
        ELSE
            IF active_count <> 1 OR NOT EXISTS (
                SELECT 1 FROM omnibase_meta.agent_task_leases
                 WHERE id = target_attempt.task_lease_id
                   AND attempt_id = target_attempt.id
                   AND task_id = target_attempt.task_id
                   AND agent_run_id = target_attempt.agent_run_id
                   AND tenant_id = target_attempt.tenant_id
                   AND task_fencing_token = target_attempt.task_fencing_token
                   AND state = 'active'
            ) THEN
                RAISE EXCEPTION 'attempt current lease does not name its unique active lease'
                    USING ERRCODE = '55000';
            END IF;
        END IF;
        RETURN NULL;
    END;
    $$;
    CREATE CONSTRAINT TRIGGER agent_attempt_lease_consistency_from_attempt
    AFTER INSERT OR UPDATE ON omnibase_meta.agent_attempts
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION omnibase_meta.agent_attempt_lease_consistency_guard();
    CREATE CONSTRAINT TRIGGER agent_attempt_lease_consistency_from_lease
    AFTER INSERT OR UPDATE ON omnibase_meta.agent_task_leases
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION omnibase_meta.agent_attempt_lease_consistency_guard();
    """,
    """
    CREATE OR REPLACE FUNCTION omnibase_meta.agent_task_effect_guard()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'UPDATE' THEN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.task_id IS DISTINCT FROM OLD.task_id
               OR NEW.attempt_id IS DISTINCT FROM OLD.attempt_id
               OR NEW.agent_run_id IS DISTINCT FROM OLD.agent_run_id
               OR NEW.operation_id IS DISTINCT FROM OLD.operation_id
               OR NEW.request_hash IS DISTINCT FROM OLD.request_hash
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'agent_task effect identity is immutable' USING ERRCODE = '55000';
            END IF;
            IF NEW.state <> OLD.state AND NOT (
                (OLD.state = 'reserved' AND NEW.state IN
                    ('dispatching', 'failed', 'unknown'))
                OR (OLD.state = 'dispatching' AND NEW.state IN
                    ('committed', 'failed', 'unknown'))
            ) THEN
                RAISE EXCEPTION 'invalid agent_task effect state transition % -> %',
                    OLD.state, NEW.state USING ERRCODE = '55000';
            END IF;
            NEW.updated_at := clock_timestamp();
        END IF;
        RETURN NEW;
    END;
    $$;
    CREATE TRIGGER agent_task_effect_guard
    BEFORE UPDATE ON omnibase_meta.agent_task_effects
    FOR EACH ROW EXECUTE FUNCTION omnibase_meta.agent_task_effect_guard();
    """,
    """
    CREATE OR REPLACE FUNCTION omnibase_meta.agent_reconciliation_guard()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF NOT EXISTS (
                SELECT 1 FROM omnibase_meta.agent_attempts
                 WHERE id = NEW.attempt_id AND task_id = NEW.task_id
                   AND agent_run_id = NEW.agent_run_id AND tenant_id = NEW.tenant_id
                   AND state = 'unknown'
            ) AND NOT EXISTS (
                SELECT 1 FROM omnibase_meta.agent_task_effects
                 WHERE id = NEW.effect_id AND tenant_id = NEW.tenant_id
                   AND state = 'unknown'
            ) THEN
                RAISE EXCEPTION 'reconciliation requires an unknown attempt or effect'
                    USING ERRCODE = '55000';
            END IF;
        ELSE
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.task_id IS DISTINCT FROM OLD.task_id
               OR NEW.attempt_id IS DISTINCT FROM OLD.attempt_id
               OR NEW.agent_run_id IS DISTINCT FROM OLD.agent_run_id
               OR NEW.effect_id IS DISTINCT FROM OLD.effect_id
               OR NEW.reason_code IS DISTINCT FROM OLD.reason_code
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'reconciliation identity is immutable' USING ERRCODE = '55000';
            END IF;
            IF OLD.state = 'resolved' OR NEW.state <> 'resolved' THEN
                RAISE EXCEPTION 'reconciliation may transition only open to resolved'
                    USING ERRCODE = '55000';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$;
    CREATE TRIGGER agent_reconciliation_guard
    BEFORE INSERT OR UPDATE ON omnibase_meta.agent_reconciliation_cases
    FOR EACH ROW EXECUTE FUNCTION omnibase_meta.agent_reconciliation_guard();
    """,
)

_DROP_FUNCTIONS: tuple[str, ...] = (
    "agent_reconciliation_guard",
    "agent_task_effect_guard",
    "agent_attempt_lease_consistency_guard",
    "agent_task_lease_guard",
    "agent_attempt_guard",
    "agent_step_dependency_guard",
    "agent_step_guard",
    "agent_run_state_guard",
    "agent_task_state_guard",
)

_DROP_TABLES: tuple[str, ...] = (
    "agent_reconciliation_cases",
    "agent_checkpoints",
    "agent_task_effects",
    "agent_task_budget_ledgers",
    "agent_task_fencing_cursors",
    "agent_task_leases",
    "agent_attempts",
    "agent_step_dependencies",
    "agent_steps",
    "agent_runs",
    "agent_tasks",
)


def _migration_schema_scope() -> str:
    config = op.get_context().config
    if config is None:
        raise RuntimeError("migration configuration is unavailable")
    scope = config.attributes.get("migration_schema_scope")
    if scope not in {"global", "tenant"}:
        raise RuntimeError(f"unsupported migration_schema_scope: {scope!r}")
    return scope


def upgrade() -> None:
    """Install the engineering-only durable ledger in the global schema."""
    if _migration_schema_scope() == "tenant":
        return

    op.create_unique_constraint(
        "run_leases_id_tenant_uq",
        "run_leases",
        ["id", "tenant_id"],
        schema=_SCHEMA,
    )
    for statement in _DDL:
        op.execute(sa.text(statement))
    op.create_unique_constraint(
        "agent_task_effects_request_uq",
        "agent_task_effects",
        ["task_id", "request_hash", "tenant_id"],
        schema=_SCHEMA,
    )
    for statement in _TRIGGER_SQL:
        op.execute(sa.text(statement))


def downgrade() -> None:
    """Refuse populated downgrade; remove an empty engineering ledger exactly."""
    if _migration_schema_scope() == "tenant":
        return

    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM omnibase_meta.agent_tasks LIMIT 1)
                   OR EXISTS (SELECT 1 FROM omnibase_meta.agent_runs LIMIT 1)
                   OR EXISTS (SELECT 1 FROM omnibase_meta.agent_steps LIMIT 1)
                   OR EXISTS (SELECT 1 FROM omnibase_meta.agent_step_dependencies LIMIT 1)
                   OR EXISTS (SELECT 1 FROM omnibase_meta.agent_attempts LIMIT 1)
                   OR EXISTS (SELECT 1 FROM omnibase_meta.agent_task_leases LIMIT 1)
                   OR EXISTS (SELECT 1 FROM omnibase_meta.agent_task_fencing_cursors LIMIT 1)
                   OR EXISTS (SELECT 1 FROM omnibase_meta.agent_task_budget_ledgers LIMIT 1)
                   OR EXISTS (SELECT 1 FROM omnibase_meta.agent_task_effects LIMIT 1)
                   OR EXISTS (SELECT 1 FROM omnibase_meta.agent_checkpoints LIMIT 1)
                   OR EXISTS (SELECT 1 FROM omnibase_meta.agent_reconciliation_cases LIMIT 1)
                THEN
                    RAISE EXCEPTION 'P5.2B populated downgrade is forbidden'
                        USING ERRCODE = '55000';
                END IF;
            END;
            $$;
            """
        )
    )
    op.drop_constraint(
        "agent_attempts_current_lease_fk",
        "agent_attempts",
        schema=_SCHEMA,
        type_="foreignkey",
    )
    for table_name in _DROP_TABLES:
        op.drop_table(table_name, schema=_SCHEMA)
    for function_name in _DROP_FUNCTIONS:
        op.execute(sa.text(f"DROP FUNCTION IF EXISTS {_SCHEMA}.{function_name}()"))
    op.drop_constraint(
        "run_leases_id_tenant_uq",
        "run_leases",
        schema=_SCHEMA,
        type_="unique",
    )
