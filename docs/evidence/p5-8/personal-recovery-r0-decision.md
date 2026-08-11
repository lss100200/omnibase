# P5.8P Personal Recovery R0 Decision

Date: 2026-08-12

## Decision

```text
P5_8P_IMPLEMENTED_PENDING_REMOTE_POSTGRESQL_CI
PERSONAL_RESTART_RECOVERY_NO_AUTOMATIC_PROVIDER_REPLAY
PRODUCTION_RUNTIME_NOT_ACTIVATED
```

## Included

- exact-replay recovery of an expired active personal invocation;
- recovery of expired old Workspace holders before a new personal invocation;
- atomic `unknown` / `blocked_unknown` convergence with one reconciliation;
- explicit same-scope Owner retry using all-new ledger and runtime identities;
- migration-head `0014` personal target diagnostics;
- backup binding for migration `0014`, Skill tables and Skill guard triggers;
- canonical restore-new compatibility from `0013` to `0014`.

## Explicitly excluded

- automatic Provider replay of pending or unknown work;
- startup-wide scanning, queues, workers or a general recovery coordinator;
- Planner, Multi-Agent, tools, MCP, workflow/script Skills or Marketplace;
- enterprise P34.7 authority, key ceremony, Runner/Broker/DERP or SLA work;
- production deployment or business-database migration.

## Current evidence

```text
backup/target + Agent Alpha focused tests: 73 passed
personal target controller tests: 49 passed
Agent Alpha core unit tests: 24 passed
Ruff check and format --check on changed paths: passed
targeted Mypy with missing third-party imports ignored: passed
compileall and git diff --check: passed
```

The P5.6P predecessor merged through PR #31 as `main@9809c3e` with all required
checks green. The guarded P5.8P PostgreSQL restart/retry scenarios K/L/M are
committed as tests but have not yet been run locally because Docker Desktop is
unresponsive and this session cannot restart its daemon. No unknown local
PostgreSQL instance was used as a substitute. The P5.8P pull request's required
PostgreSQL sentinel CI remains the acceptance authority for these scenarios.

## Safety posture

```text
root .env not read
business database not accessed or migrated
migration head 0014
migration 0015 absent
AGENT_RUNTIME_ENABLED=false by default
AGENT_PLANNER_ENABLED=false
MULTI_AGENT_ENABLED=false
enterprise P34.7 frozen / blocked_not_proven
approved enterprise trust-policy digest empty
no real Provider credential used
not deployed
```
