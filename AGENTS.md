# OmniBase AI Maintainer Contract

This file is the repository entry point for coding agents. Its purpose is to
make OmniBase repairable from the public source tree without relying on a
private conversation, hidden workspace state, or an original maintainer's
memory.

## Read order

Before changing architecture, authentication, tenancy, migrations, P34, SDKs,
or recovery tooling, read these sources in order:

1. `AGENTS.md`
2. `docs/maintainers/maintenance-map.json`
3. `docs/maintainers/security-invariants.md`
4. `docs/maintainers/ai-maintainer-map.md`
5. The module source, migrations, contracts, and tests named by the map
6. `docs/handover-report.md` for current phase status and verified evidence

If prose and executable behavior disagree, do not guess. Treat source,
database constraints, migrations, contract snapshots, and passing tests as the
runtime evidence; then correct the stale documentation in the same change.

## Non-negotiable boundaries

- Browser and user API traffic enters through `backend/src/omnibase/main.py`
  under `/api/v1`. The root `/health` route is a probe-only compatibility path.
- The Capability Gateway is a separate non-browser ASGI application created by
  `backend/src/omnibase/capability_gateway/app.py:create_gateway_app`. Its
  defaults reject all workloads until trusted attestation and verification
  components are injected. Do not silently mount it into the browser app.
- JWT claims are necessary but not sufficient. Protected browser requests must
  revalidate the live tenant, live user, current role, and tenant schema through
  `backend/src/omnibase/tenants/dependencies.py:get_current_principal`.
- External callers use logical resource, table, column, operation, and grant
  identifiers. Physical PostgreSQL schema/table/column locators must remain
  server-owned and must not appear in public DTOs, SDKs, logs, or errors.
- Capability authorization is tenant/workspace/runtime/action/resource/version
  bound, time limited, budget limited, revocable, and fail-closed. Never replace
  verification with possession of a raw identifier or a browser cookie.
- High-risk operations, approval consumption, idempotency, auditing, and data
  mutation are one security system. Do not update one record in isolation when
  the current service performs an atomic lifecycle transition.
- Audit records are append-only. Migration `0006` installs database enforcement;
  application code must not treat ORM discipline as the only protection.
- Migration scope is a closed set: `global` or `tenant`. Unknown or missing
  scope must fail closed. Production recovery is forward-fix or restore into a
  new `omnibase_restore_*` database, never destructive in-place guessing.
- P34.4 Workspace governance, lifecycle metadata, lease/fencing, trusted-node
  control records, and the fake/local collaboration harness are explicitly
  unlocked. P34.5A0-A4/B/C/D are engineering-sealed: the independent Hyper-V
  Linux Runner passed its 11/11 isolation Gate, the independent PrivateNetwork
  Broker daemon passed two 26/26 namespace/default-deny/identity/budget/replay
  rounds, the Headscale adapter passed a real control-plane Gate with an mTLS
  Node-Daemon test double, and the split-process mTLS Gateway passed the guarded
  disposable schema/rows/RAG/citation read Gate. Production defaults still
  reject until the documented trusted wiring is explicitly assembled. This
  does not authorize a normal Docker/WSL host to run hostile code, expose a
  member Overlay endpoint to a Sandbox, connect a Sandbox or Runner directly to
  PostgreSQL/Redis/MinIO, or treat the disposable Gates as proof of production
  Core-to-Runner/Broker activation, non-disposable tenant/RAG, real member data
  plane, DERP, node-compromise, capacity, or SLA readiness. P34.6
  Workspace-private/derived data contracts, controlled Gateway write seam,
  copy-on-publish promotion, server-generated snapshot inventory,
  restore-new-identity metadata, migration, and isolated verification are
  engineering-unlocked. Production Workspace-data access remains fail-closed
  unless trusted mTLS ingress, live Run/Node/Lease/generation/fencing evidence,
  a short-lived non-delegable workspace-data grant, and an explicitly installed
  controlled adapter are all present. Browser private-write, direct
  Sandbox/Runner access to PostgreSQL/Redis/MinIO, canonical mutation,
  production provider activation, Agent Runtime, and orchestration remain
  frozen. P5.2A is the offline Agent Task/Run/Lease/fencing ledger contract
  preflight only: it never creates a Task Lease or a real Task/Run/Attempt,
  never starts a Planner/Executor/scheduler/worker, and remains
  `blocked/not_proven`; the P5.2 persistence ledger, Agent Runtime and
  invocation APIs stay frozen until the roadmap and handover explicitly
  unlock them.
- P34.4 membership mutations serialize on the tenant-bound Workspace aggregate,
  then re-lock the actor and target membership before evaluating the active-owner
  invariant. Template registration revalidates the live tenant administrator in
  the caller-owned transaction and uses the PostgreSQL natural key for exact
  concurrent replay. Do not replace either rule with a pre-transaction role
  snapshot, an unlocked owner count, or catch-and-ignore `IntegrityError`.
- P34.6 capability-backed private mutation serializes Tenant, tenant User,
  Workspace aggregate, actor WorkspaceMembership, Resource, bindings,
  AuthorizationContext, Operation, and Idempotency in that order. The actor
  membership must still be active and writable on every request; an active
  Grant and tenant User cannot substitute for live Workspace membership.
- P34.7 adds production admission contracts, not production authority. A/B
  require a clean public checkout, tracked-source/evidence digests, four
  separate Core/Runner/Broker/Gateway identities and the fixed mTLS/AF_UNIX
  topology. C/E admit only a disposable local provider reference today;
  provider objects are visible only after an append-only committed marker,
  `pending|unknown` never auto-replays, and non-disposable tenant/RAG requires
  explicit data-owner admission. D/F require two real independent Linux
  members, production Node Daemons, independent DERP, current-source Runner
  12/12, two Broker 26/26 rounds, node-compromise evidence, dual Ed25519
  signatures and SLA samples. Missing external evidence means
  `blocked/not_proven`; it must never be rewritten as P34.7 PASS. Phase 5
  remains PLANNED/FROZEN. P5.0 is the only permitted Phase 5 deliverable: the
  fail-closed admission gate in `backend/src/omnibase/production/
  phase5_admission.py` with three independent, default-off feature gates
  (`AGENT_RUNTIME_ENABLED`, `AGENT_PLANNER_ENABLED`, `MULTI_AGENT_ENABLED`),
  the strict contract `deployment/production/phase5-admission.example.json`
  and the validator `scripts/production/validate_p5_0_admission.py`. It only
  returns an admission decision and never starts an Agent, Planner, Executor,
  queue, worker or scheduler; missing/empty gate values are false, unknown
  values and dependency conflicts fail closed, and P5.0 stays
  `blocked/not_proven` while P34.7 is not `ready`. P5.1A is the only further
  permitted Phase 5 deliverable: the offline Agent Registry contract preflight
  (`backend/src/omnibase/production/phase5_registry_contract.py`, contract
  `deployment/production/phase5-registry-contract.example.json`, validator
  `scripts/production/validate_p5_1_registry_contract.py`). It defines strict
  AgentDefinition/AgentVersion/WorkspaceAgentBinding DTOs only; there is no
  ORM, migration, service, Browser API, SDK call, Planner, Executor, worker,
  scheduler or runtime, and P5.1A stays `blocked/not_proven` while P34.7 and
  P5.0 are not `ready`. P5.1B is the permitted persistence foundation: the
  internal-only Agent Registry ORM (`backend/src/omnibase/agent_registry/`),
  scoped migration `0010` (composite `(id, tenant_id)` foreign keys, database
  trigger state machines, sealed-version immutability, partial unique index
  for the single live binding) and the internal `RegistryPersistenceService`
  (caller-owned transactions binding idempotency, approval consumption,
  `resource_registry` registration and append-only audit). It is **not** a
  public API: no FastAPI router, OpenAPI endpoint, SDK surface, frontend,
  Invocation/Task/Run/Plan/Step/Attempt, Planner/Executor/Dispatcher/
  Scheduler, Celery, Agent Runtime, Model/Tool/Memory/Skill Runtime, MCP or
  shell/SQL/HTTP tools, and no feature gate may be enabled; P5.2+ stays
  frozen, P5.1 production stays `blocked/not_proven`, and the disposable
  PostgreSQL Gate (evidence under `docs/evidence/p5-1/`) does not unlock
  production Registry service, Runtime or orchestration readiness. P5.1C is
  the permitted Browser control API: `/api/v1` catalog reads and the
  Workspace install/disable/upgrade/rollback lifecycle
  (`agent_registry/schemas.py`, `control.py`, `router.py`, mounted in
  `main.py`) plus the Python/TypeScript SDK clients
  (`sdk/python/src/omnibase_sdk/browser_registry.py`,
  `sdk/typescript/src/registry-browser.ts`). It is fail-closed by default:
  the unassembled control plane rejects every endpoint with
  `agent_registry_unavailable` (503) before touching any registry table, and
  the DB-backed service is only injected explicitly. P5.1C never creates
  AgentDefinition/AgentVersion (registration and version sealing stay
  internal), never adds migration `0011`, keeps all three Phase 5 feature
  gates false, revalidates live tenant/user/role/WorkspaceMembership inside
  the caller-owned transaction on every mutation, and its disposable
  `omnibase_test_p51c_*` Gate (evidence under `docs/evidence/p5-1/`) does
  not unlock production Registry, Runtime or orchestration readiness.
- Read, Sandbox, and Workspace-data capability profiles are mutually exclusive.
  Promotion may only create a new `controlled_shared` Resource and must not
  modify the source or create/reclassify `canonical_readonly`. External effects
  left `pending` or `unknown` require reconciliation and must never be replayed
  automatically. Snapshot inventory is server-generated; restore always creates
  a new Workspace, generation, and Resource IDs and never revives Run, Lease,
  token, runtime/workload identity, socket, PID, provider handle, or network
  identity.
- Run and Network leases are independently fenced. A Run lease is bound to the
  current Node fencing token and a Network lease is a logical authorization
  allocated from `network_lease_cursors`; P34.4 never activates a provider while
  signing that logical Network lease. Every use revalidates the current live
  attestation. Terminal Runs cannot return to a running state or retain runtime
  or workload identity metadata.

## Safe change workflow

1. Locate the target module in `docs/maintainers/maintenance-map.json`.
2. Read every listed invariant and dependency before editing.
3. Preserve tenant predicates, logical/physical identifier separation,
   transaction boundaries, lock order, idempotency, audit writes, and
   fail-closed behavior.
4. Make the smallest change that fixes the proven issue. Do not hide type
   errors with broad `Any`, global ignores, or unexplained `type: ignore`.
5. Run the module's `verification` commands from the map, then the wider gate
   when a shared boundary changed.
6. If entrypoints, dependencies, public interfaces, invariants, or recovery
   steps changed, update the maintainer map in the same commit.
7. Report what was verified and what was not. Never claim a production
   migration, backup restore, Skill installation, or deployment without direct
   evidence.

## Repository safety

- Do not read, print, stage, or commit the root `.env` unless the user explicitly
  requests a secret-specific operation. Use `.env.example` for configuration
  shape.
- Never stage `.omo/`, `.zcode/`, `.tmp/`, generated eval workspaces, model
  weights, `node_modules/`, `.next/`, SDK `dist/`, or local database material.
- From the repository root, every Compose diagnostic, `config`, `run`, `exec`,
  `up`, `logs`, or `ps` command must explicitly use
  `docker compose --env-file .env.example ...` unless it uses a documented
  disposable overlay-specific Compose file and env file. Never run bare
  `docker compose config --format json`: Compose would implicitly expand the
  root `.env` and could expose secrets through diagnostic output.
- Do not use `git add .`. Stage explicit paths and inspect the cached diff.
- Do not run destructive database tests against a normal database. Use the
  sentinel Compose project and `omnibase_test_*` names enforced by the Makefile.
- Do not push, publish, deploy, rotate credentials, or migrate a business
  database unless the user explicitly authorizes that external state change.

## Canonical verification entrypoints

The project is container-first; a host Python environment is not required.

```text
docker compose --env-file .env.example run --rm --no-deps backend mypy src
docker compose --env-file .env.example run --rm --no-deps backend pytest -m "not integration" -q
docker compose --env-file .env.example run --rm --no-deps -v .:/workspace -w /workspace backend python scripts/maintenance/validate_maintainer_map.py --repo-root .
docker compose --env-file .env.example run --rm --no-deps -v .:/workspace -w /workspace backend python scripts/maintenance/validate_maintainer_benchmark.py --repo-root .
```

The exact focused Ruff baseline enforced by CI is in
`.github/workflows/infrastructure-gates.yml`; module-specific commands are in the
machine map. Run `ruff check` and `ruff format --check` with explicit changed or
map-listed Python paths; never pass an unexpanded placeholder to the shell. Do
not claim full-repository Ruff cleanliness unless `ruff check src tests` was
actually run and passed. Database-destructive integration tests
must use the guarded Makefile target and its explicit disposable
credentials/project name. Frontend and SDK commands are listed in the map
because their package managers and working directories differ.
