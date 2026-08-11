# P5 Personal Runtime Activation R0 Decision

## Decision

```text
P5_PERSONAL_RUNTIME_R0_ENGINEERING_COMPLETE
PERSONAL_SINGLE_OWNER_NO_TOOL_CANARY_ONLY
PERSONAL_SINGLE_OWNER_NO_TOOL_CANARY_VERIFIED_IN_DISPOSABLE_POSTGRESQL
LOGIN_REFRESH_ERROR_MASKING_FIXED
PRODUCTION_TARGET_NOT_ACTIVATED
PLANNER_DISABLED
MULTI_AGENT_DISABLED
SANDBOX_AND_TOOLS_NOT_AUTHORIZED
ENTERPRISE_P34_7_TRACK_FROZEN
MIGRATION_HEAD_0012
MIGRATION_0013_ABSENT
```

## Included engineering boundary

- canonical exact-scope personal canary config and deterministic plan digest;
- bounded append-only activate/rollback ledger;
- independent irreversible kill marker that wins over corrupt state;
- exact production feature-gate conjunction;
- raw-byte verification of a server-mounted minimal Personal Owner readiness
  root during composition and transaction A;
- live Tenant/Workspace/Owner/tenant-admin revalidation under the canonical
  Workspace aggregate lock order;
- fresh-only personal concurrency=1 enforcement using non-terminal
  `WorkspaceRun` state, independent of the generic Workspace quota;
- repeated config/readiness/ledger/time/gate/migration/scope verification
  before Task insert, before transaction-A commit and before provider/stream
  checkpoints;
- canonical event bytes plus filename/event binding and future-time veto;
- exact Tenant/Workspace/Owner/AgentVersion/`top_k` facade;
- existing durable Task/AgentRun/WorkspaceRun/RunLease/workload identity and
  reconciliation lifecycle;
- Browser status and frontend workbench posture for the personal canary;
- filesystem-only control CLI and explicit read-only Compose mount overlay;
- public-auth 401 handling that no longer replaces login errors with the
  internal `No refresh token available` control-flow error.

## Explicitly not proven or authorized

- no real target deployment or provider credential activation;
- no business database access or migration;
- no production Sandbox, shell, SQL, HTTP, MCP, Skill or tool execution;
- no Planner or Multi-Agent;
- no formal P5.4B Capability Gateway Browser composition;
- no externally signed activation ledger or independent timestamp authority;
- no claim that a filesystem kill marker and PostgreSQL commit are one
  linearizable transaction; blocked provider transports observe kill at the
  next checkpoint and operators must remove the overlay/restore Runtime=false;
- no enterprise trust-policy approval, approved digest or key ceremony;
- no migration 0013.

## Verification record

Implementation commit and clean provenance:

```text
commit = 392b6f458e410573cf97ae9a0bde159da604c169
git tree = 07ce8eb5aff04b7dc1ae142fbf46a7116b20bc01
source checkout = clean
remote origin = https://github.com/lss100200/omnibase.git
```

Verified matrix:

- independent final security review: `ACCEPTABLE`, no P0/P1/P2 finding in
  canonical lock order, fresh-only concurrency admission, pre-commit
  revalidation, kill/provider checkpoints or router scope;
- backend clean-HEAD non-integration: `2543 passed, 22 skipped, 15 deselected`;
- frontend: `95 passed`, typecheck, lint and `NODE_ENV=production` build passed;
- Mypy: `Success: no issues found in 200 source files`;
- explicit changed-path Ruff check and format check passed;
- maintainer map: `46 invariants / 39 modules / 672 path specs / 1354 matched
  files / 279 entrypoints / 199 verification commands`; benchmark: `3 plans /
  8 scenarios / 6 critical scenarios / 9 unsafe vetoes`;
- disposable PostgreSQL Gate:
  `omnibase-p5personal-r0final8` / `omnibase_test_p5personal_r0final8`, migration
  `0012`, one persisted personal Runtime integration test passed, five control
  CLI tests passed, and cleanup proved `containers/networks/volumes = 0/0/0`;
- the integration test deliberately sets the generic Workspace
  `max_active_runs=8`; the second fresh invocation is still rejected before a
  second Task row by the personal `max_concurrent_invocations=1` admission;
- P34.7 composition, P5.0, P5.1A, P5.2A, P5.3A and P5.6A clean-HEAD formal
  verifiers exited `2`, remained `blocked/not_proven`, reported `vetoes=[]`
  and did not authorize activation; P5.1A/P5.2A/P5.3A/P5.6A reported
  `contract_valid=true`;
- P34.7 joint validate-only exited `2 blocked/not_proven`; the frozen personal
  Owner config validate-only exited `0`, `contract_valid=true`, migration head
  `0012`.

No root `.env` was read, no business database was accessed or migrated, no
private key or provider secret was generated or transmitted, and no real
target was pushed, merged, deployed or activated. This is an engineering and
disposable-database canary receipt, not a production deployment receipt.
