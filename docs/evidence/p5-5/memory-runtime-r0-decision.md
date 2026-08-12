# P5.5C Bounded Personal Memory Runtime R0 Decision

## Decision

```text
P5_5C_ENGINEERING_ACCEPTED_PENDING_REMOTE_REVIEW
PERSONAL_MEMORY_RUNTIME_CANARY_ONLY
CURRENT_PERSONAL_MIGRATION_HEAD_0015
MIGRATION_0016_ABSENT
DEFAULT_RUNTIME_OFF
PLANNER_FALSE
MULTI_AGENT_FALSE
BROWSER_MEMORY_API_ABSENT
NOT_DEPLOYED
```

P5.5C integrates committed migration-0013 Memory into the existing tool-free
personal Agent Alpha path. It does not unfreeze the enterprise P34.7 track and
does not authorize tools, shell, SQL, arbitrary HTTP, MCP, Planner or
Multi-Agent execution.

## Implemented boundary

- `SqlAlchemyMemoryCompiler` revalidates the live Tenant/schema, exact active
  tenant-admin Owner, Workspace/Owner membership, sealed AgentVersion and
  running Task/Invocation.
- Selection accepts only active, non-deleted current Memory versions with an
  accepted source Candidate. The four scope shapes and current exact Owner
  review for controlled-shared Memory are enforced.
- Candidate rows, item count, initial tokens, per-item tokens and sensitive
  items are bounded. Ranking is deterministic and includes Chinese-character
  lexical matching.
- Memory content uses a separate AES-256-GCM key with domain-separated AAD.
  Plaintext UTF-8, size and SHA-256 are verified before prompt projection.
- The Memory policy digest participates in the invocation request hash. The
  Capsule and contiguous item rows commit before provider dispatch. Exact
  terminal replay performs zero compilation and creates no second Capsule.
- P5.9P forward migration `0015` permits a zero-item/zero-token audit Capsule
  for the first invocation when no Memory exists. The compiler still returns
  no Memory projection, so the Provider prompt and SSE metadata remain
  unchanged while the first real Candidate gains a valid source Capsule.
- Memory is injected only as an explicitly untrusted reference-data message.
  SSE metadata is limited to Capsule ID/digest/item count.
- Compiler failure terminalizes the reserved invocation as
  `failed/agent_alpha_memory_compile_failed`; provider/disconnect uncertainty
  continues to use the existing unknown/reconciliation boundary.

## Local evidence

```text
focused compiler + Agent Alpha + personal composition tests = 43 passed
changed-path Mypy = passed
changed-path Ruff check = passed
changed-path Ruff format --check = passed
docker compose --env-file .env.example config --quiet = passed
git diff --check = passed
```

A random disposable migration-0013 PostgreSQL journey passed independently. It
proved encrypted stored Memory, exact scope selection, Capsule/item persistence,
the untrusted prompt projection, incremental two-chunk SSE, explicit cancel and
durable Task convergence to `cancelled`. The disposable containers, network and
volumes were removed.

The journey uses a small database-backed test ledger for the Task state. The
complete production `LedgerInvocationAdapter` Task/Run/Attempt/Lease restart
matrix is not claimed by this evidence; that broader crash/no-replay proof is
assigned to the personal recovery increment and does not widen P5.5C.

## Safety posture

- root `.env` was not read;
- no business database was accessed or migrated;
- only disposable `omnibase_test_*` PostgreSQL identities were used;
- no production Memory key was generated, printed or committed;
- `AGENT_RUNTIME_ENABLED=false` remains the default;
- `AGENT_PLANNER_ENABLED=false` and `MULTI_AGENT_ENABLED=false` remain fixed;
- no Browser Memory API, public search endpoint or external execution surface
  was introduced;
- no push, merge or deployment is represented by this local decision.

GitHub required CI is the full regression authority for the eventual PR.
