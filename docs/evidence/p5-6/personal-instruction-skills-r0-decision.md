# P5.6P Personal Instruction Skills R0 Decision

Decision state: **P5_6P_ENGINEERING_ACCEPTED_PENDING_REMOTE_CI**.

## Authorized personal scope

This increment is authorized for one human Owner, one exact Workspace, one
sealed AgentVersion and first-party instruction-only Skills. It does not
authorize workflow/script execution, tools, arbitrary network access, MCP,
Marketplace, Planner, Multi-Agent or enterprise P34.7 activation.

## Delivered engineering evidence

- migration head `0014` with first-party Skill Definition, immutable Version
  and exact Workspace/AgentVersion installation tables;
- install, disable, revoke and rollback lifecycle;
- deterministic read-only Skill bundle resolver;
- bundle digest in the Agent Alpha invocation request hash;
- prompt order `AgentVersion -> Skill -> RAG -> Memory -> user`;
- SSE metadata limited to Skill bundle digest/count;
- focused attacks and one disposable `omnibase_test_p56p_*` PostgreSQL journey;
- Runtime false by default, Planner/Multi-Agent false.

Exact local results:

```text
P5.6P persistence/resolver + Agent Alpha focused = 99 passed
P5 migration-contract compatibility = 457 passed
P34.7 frozen-contract compatibility = 307 passed / 1 Windows-only skip
trust-policy candidate focused = 171 passed
disposable PostgreSQL journey = 1 passed
disposable cleanup = containers 0 / networks 0 / volumes 0
component changed-path Ruff/Mypy/git diff check = passed
```

The PostgreSQL journey upgraded to migration `0014`, created one Owner and two
Workspaces, installed exact sealed Agent and Skill versions, resolved the
deterministic bundle, exercised disable/rollback/revoke, and rejected
cross-Workspace, uninstalled-Agent, unsupported AgentVersion digest and direct
database cross-wire attacks. It
used no real Provider or API key. Agent Alpha focused tests separately prove
prompt order, request-hash binding, SSE digest/count-only projection, empty
bundle compatibility, exact replay and fail-closed resolver drift.

## Production and remote posture

This is sufficient for the bounded local engineering decision, not yet a merge
claim. GitHub required CI remains the full regression authority. Until the PR
is green and merged:

```text
migration head=0014
migration 0015+ absent
AGENT_RUNTIME_ENABLED=false by default
AGENT_PLANNER_ENABLED=false
MULTI_AGENT_ENABLED=false
enterprise P34.7 frozen / blocked_not_proven
approved enterprise trust-policy digest empty
not pushed
not merged
not deployed
```
