# P5.8P Personal Restart Recovery R0

## Purpose

P5.8P closes one personal-edition failure mode: the Core process may stop after
an Agent invocation has reserved durable ledger and runtime rows but before it
can terminalize them. The next request must not leave the personal Workspace
permanently occupied, and it must not guess that the Provider was never called.

This is a single-Owner recovery path. It is not a scheduler, queue, worker,
enterprise recovery coordinator, multi-Agent runtime, or approval hierarchy.

## Recovery trigger

Recovery runs inside `LedgerInvocationAdapter.begin()` in two bounded cases:

1. an exact idempotency replay finds its original active Attempt; or
2. a new invocation for the same Tenant, Workspace and Owner finds an older
   active Attempt whose Task Lease has expired.

The database `clock_timestamp()` is authoritative. A live Task Lease still
returns `agent_alpha_replay_in_flight`; it is never stolen or shortened.

## Atomic convergence

For an expired exact holder, one caller-owned transaction locks and revalidates
the Attempt, Task Lease, Task, Effect, AgentRun, WorkspaceRun and RunLease. The
transaction then converges the old invocation as follows:

- Attempt and Effect become `unknown`;
- Task becomes `blocked_unknown`;
- Task Lease is terminalized and cannot be revived;
- AgentRun and WorkspaceRun become failure terminal states;
- old RunLease, fencing, runtime instance and workload identity bindings are
  cleared by the existing historical-holder close path; and
- exactly one open reconciliation is retained with reason
  `agent_alpha_restart_lease_expired`.

Any stale lease, fencing, node, generation, actor, Tenant or Workspace binding
fails closed and rolls the transaction back. Recovery never calls the Provider,
retrieves mutable RAG/Memory/Skill input, creates a second Effect, or records a
committed budget outcome.

## Explicit Owner retry

`retry_of` is a request for a new invocation, not permission to mutate or revive
the old Task. The target must be in `blocked_unknown`, `failed` or `cancelled`
and must match the current Tenant, Workspace, Owner, Agent Definition,
AgentVersion ID and digest, installed Workspace binding, resource-scope digest
and budget-policy digest. A `blocked_unknown` target must still have its open
reconciliation record.

A valid retry receives a new idempotency identity and new Task, Attempt,
TaskLease, AgentRun, WorkspaceRun, RunLease, runtime instance, workload identity,
Effect and Operation. The old ledger and reconciliation records remain
immutable. Reusing the old idempotency key with changed retry input is a stable
`task_replay_input_mismatch` conflict.

## Backup and migration binding

The reviewed personal repository head is migration `0014`. Personal target
diagnostics reject `0015+`. Backup manifests bind the raw migration `0014`
bytes plus the three Skill tables and their guard triggers. Restore remains
restore-new only. A closed compatibility entry permits the canonical
`0013 -> 0014` Skill upgrade while arbitrary forward restore remains rejected.

## Verification boundary

Focused offline tests, Ruff, Mypy, compile checks and backup/target contract
tests may be run locally. The recovery scenarios require a random guarded
`omnibase_test_*` PostgreSQL database. If local Docker is unavailable, the
GitHub `postgres-sentinel-integration` check is the authoritative clean-Linux
evidence; local success must not be invented.

Runtime remains false by default. Planner and Multi-Agent remain false.
Enterprise P34.7 remains frozen and is not a prerequisite for this personal
recovery increment.
