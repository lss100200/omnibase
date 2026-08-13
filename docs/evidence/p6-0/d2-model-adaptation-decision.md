# P6.0-D2 Model Adaptation Decision

Date: 2026-08-13

Decision: `P6_0_D2_ENGINEERING_COMPLETE_DATABASE_GATE_PASSED_LOCALLY`

## Delivered

- researched conservative profiles for DeepSeek, GLM, Kimi, GPT and Claude;
- model-name-first, conflict-safe family resolution with generic fallback;
- one parent plus nine specialist role settings over one personal Runtime;
- default saved-credential/model inheritance and per-role saved-credential or
  model overrides;
- no API-key copy or Browser secret field; secret/locator-shaped model names
  fail closed before persistence or Audit;
- exact optimistic create/update/delete versioning;
- exact custom-model probe bound to override, credential/key, Workspace
  generation, installed Binding, AgentVersion and endpoint-policy identity;
- probe and personal Runtime share the pinned HTTPS/no-proxy/no-redirect
  transport and re-resolve the public endpoint policy before dispatch;
- invocation selection bound to role/model/scope/configuration identity;
- tenant migration `0016` for role preferences only, with downgrade guards;
- external P6.0-A audit findings for history compaction, UI/UX mention routing
  and oversized non-secret replies incorporated as regression tests;
- the remaining P3 audit observations were independently rechecked: session
  archive rollback, authenticated-only persistence, storage-read failure,
  idle stop, duplicate-submit feedback and pre-meta cancellation honesty were
  fixed; the already-correct usage and parent-length guards were reverified;
- reviewed personal/P5/P34 migration facts advanced to `0016`, future `0017+`
  remains rejected and the admission/registry/task-ledger/planner SHA chain was
  resealed after the final source and maintainer-map changes.

## Current verified evidence

Final local evidence available on this Windows host:

```text
frontend tests = 157 passed
frontend typecheck = passed
frontend lint = passed
frontend production build = passed (16 application routes)
backend focused model/Alpha/rate-limit/endpoint-policy tests = 104 passed
P5/P34 focused contract regression = 1028 passed, 1 Windows symlink skip
personal target/backup/acceptance controller tests = 68 passed
remaining host non-integration run = 2673 passed, 42 skipped, 15 failed,
  16 deselected after excluding one Linux launcher collection failure
  - 12 failures are POSIX-only P34.5 tests executed on Windows
  - 2 failures are FastAPI host-version lazy-router incompatibilities
  - the final 2 sealed-contract failures were resolved by resealing and their
    focused matrix subsequently passed
changed Python Ruff check / format = passed (65 paths)
maintainer map = valid (58 invariants, 46 modules, 919 path specs,
  2082 matched files, 311 entrypoints, 19 HTTP entrypoints,
  247 verification commands)
maintainer benchmark = valid (3 plans, 8 scenarios, 9 unsafe vetoes)
guarded PostgreSQL D2 attack module = 10 passed in 99.58s against a disposable
  omnibase_test_p60d2_* PostgreSQL sentinel; exact 0016 migration shape,
  cross-user and closed-set constraints, concurrent first-create, stale
  update/delete, delete/recreate during probe, membership/Binding/generation
  drift, tenant-first empty downgrade and populated atomic rollback passed
focused migration/model contracts after the database forward fix = 28 passed
broader D2/Agent Alpha/rate-limit/migration regression = 90 passed
P5 registry/task-ledger/planner sealed-contract regression = 407 passed
Mypy migration env + model settings boundary = passed (2 source files)
changed forward-fix Ruff check / format = passed (3 explicit paths)
disposable PostgreSQL container, networks and tmpfs data = removed with
  down -v --remove-orphans after the Gate
maintainer map = valid (58 invariants, 46 modules, 919 path specs,
  2082 matched files, 311 entrypoints, 19 HTTP entrypoints,
  247 verification commands)
maintainer benchmark = valid (3 plans, 8 scenarios, 6 critical scenarios,
  9 unsafe vetoes)
root .env = not read
business database = not accessed or migrated
push / PR / merge / deploy = not performed
```

Docker Desktop Linux Engine recovered on 2026-08-13. The guarded database Gate
then exposed and forward-fixed three evidence defects: the reviewed schema
assertion omitted the endpoint-policy digest column; the probe attack double
still patched the retired pre-hardening network entrypoint and did not model the
request session's tenant binding after its deliberate mid-probe commit; and the
database-wide downgrade proof ran after append-only audited service cases had
intentionally retained tenants. The final Gate now executes the downgrade proof
before audited cases, preserves the real request-scoped tenant binding, and
accepts only the exact ordinary `alembic downgrade 0015` CLI form for the
tenant-first path. All 10 database attacks passed and cleanup completed.

The earlier complete frontend, focused backend, P5/P34 sealed-contract and
maintainer-map evidence remains the D2 engineering baseline. This local
disposable database PASS is not a business-database migration, public release,
deployment or production evidence.

## Safety posture

```text
single personal Agent Runtime only
Planner disabled
Multi-Agent disabled
Tools / MCP / CLI / Vision disabled
enterprise P34.7 frozen and blocked/not_proven
approved enterprise trust-policy digest absent
not pushed / not merged / not deployed
```
