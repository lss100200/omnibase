# P5 Personal Runtime Activation R0 Decision

## Decision

```text
P5_PERSONAL_RUNTIME_R0_IMPLEMENTATION_PRESENT_PENDING_FINAL_VERIFICATION
PERSONAL_SINGLE_OWNER_NO_TOOL_CANARY_ONLY
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

The final clean-HEAD verification matrix, commit SHA and disposable evidence
run identifier are appended only after implementation, documentation, sealed
contracts and the full verification matrix converge. Until then this document
must not be interpreted as a production deployment receipt.
