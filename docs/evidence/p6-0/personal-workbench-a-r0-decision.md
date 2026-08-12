# P6.0-A Personal Workbench R0 Decision

Date: 2026-08-13

```text
P6_0_A_ENGINEERING_ACCEPTED_PENDING_PRODUCT_BROWSER_REVIEW
P6_0_B_NOT_STARTED
P6_0_C_NOT_STARTED
P6_0_D_NOT_STARTED
MIGRATION_HEAD_0015
MIGRATION_0016_ABSENT
PLANNER_DISABLED
MULTI_AGENT_DISABLED
LOCAL_COMMIT_PENDING
NOT_MERGED
NOT_DEPLOYED
```

The first implementation replaces the `/dashboard` RAG shell with a Personal
Engineering Workbench while reusing the verified Agent Alpha SSE, cancellation
and invocation-ownership boundaries.

Delivered in this slice:

- IDE-shaped top bar, session rail, conversation pane and context/employee rail;
- local tenant/user-scoped session creation, search, pin, archive and restore;
- append-only local session timeline and bounded storage;
- strict closed-set local-state parsing, 80-session/4-MiB budgets and
  deterministic eviction that never silently removes the active or a pinned
  session;
- local-history redaction for Provider keys, bearer/JWT material, database
  URLs, private keys, Capability values, environment secrets and physical
  locators;
- one active parent Agent and nine dormant specialist definitions;
- deterministic NFKC-aware single-`@` routing with unknown/multiple rejection;
- bare, malformed and broadcast `@` rejection plus final specialist-wrapper
  validation against the exact 32,000-character Agent Alpha request limit;
- specialist responsibility/boundary prompt context without Agent-to-Agent wake;
- existing Workspace and installed Agent selection;
- Agent Alpha streaming, cancellation, identity and usage projection;
- immutable invocation Workspace/session ownership, stale-response rejection,
  unmount abort and stable `auth_session_expired` handling without exposing
  the internal missing-refresh-token error;
- honest P6.0-B placeholder text without filesystem scanning or fake buttons.

Local verification from the P6 worktree:

```text
frontend tests = 114 passed
frontend TypeScript = passed
frontend lint = passed
frontend production build = passed
```

Maintainer map and benchmark validation passed (`54` invariants, `46` modules,
`872` path specs; `3` plans, `8` scenarios and `9` unsafe vetoes). Compose
configuration validation with `.env.example` passed.

Independent review Round 1 found two P1 issues: identity scope changes could
reuse the previous Workspace for one Runtime request, and the local redactor
missed Basic Authorization, an un-delimited GitHub token and URI userinfo.
Both were repaired forward-only. Round 2 verified the new identity/workspace
authorization conjunction and all three attack cases and reported no remaining
P0/P1/P2.

No root `.env` was read. No business database was accessed or migrated. No
Provider secret was used. Migration `0016` was not created. The work has not
been pushed, merged or deployed and still requires product/browser review.
