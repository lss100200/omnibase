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
  internal), does not own migrations, keeps all three Phase 5 feature gates
  false, revalidates live tenant/user/role/WorkspaceMembership inside
  the caller-owned transaction on every mutation, and its disposable
  `omnibase_test_p51c_*` Gate (evidence under `docs/evidence/p5-1/`) does
  not unlock production Registry, Runtime or orchestration readiness. The
  user-approved P5 Fast Track separately permits the engineering-only P5.2B
  durable Task ledger (`backend/src/omnibase/task_ledger/`, migration `0011`),
  internal tool-free Model Gateway (`backend/src/omnibase/model_gateway/`) and
  tool-free single-Agent Alpha (`backend/src/omnibase/agent_alpha/` plus the
  Browser workbench). P5.2C adds the engineering-only Agent Alpha runtime on
  top of the same tables: `AGENT_ALPHA_ENGINEERING_ENABLED` (strict true/false)
  plus `ENV=development`, all three Phase 5 Feature Gates false, a configured
  Model Gateway and migration head `0011` are required before
  `build_engineering_agent_alpha()` may assemble the DB-backed service, which
  writes only through migration `0011` `TaskLedgerPersistenceService`
  transactions (durable reservation before the provider boundary; revalidate
  and terminalize after), reproduces the exact task_create payload on replay
  from the committed idempotency record, never re-dispatches an in-flight or
  unknown attempt, and is sealed by the `omnibase-p52c-*` disposable Gate
  (evidence under `docs/evidence/p5-2/`). This exception does not activate
  production Runtime:
  all Phase 5 Feature Gates remain false, `get_agent_alpha` stays unavailable
  by default, provider secrets stay server-owned, requested/actual model
  identity must match exactly, and no shell/SQL/arbitrary-HTTP tool, MCP,
  Skill, Planner, scheduler, worker, DAG or multi-Agent runtime is authorized.
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
- P34.7 Trust Policy R1-A is an offline assignment contract only. Its canonical
  example deliberately keeps every real authority `UNASSIGNED`, every custody
  selection and target resource `NOT_ASSESSED`, and every production blocker
  open. A valid R1-A contract is not an authenticated authority registry, Trust
  Policy approval, key-ceremony authorization, production evidence, P34.7 PASS
  or Runtime activation. R1-A rejects input-declared authority/custody
  `VERIFIED`, environment/blocker `PROVEN`, and any `production_equivalent=true`;
  those facts require separately pinned registry, review-receipt, attestation
  and signed-evidence validators. A fully populated proposal is at most
  `complete_not_authenticated`, with every independent-verification field false.
  Do not infer real authorities from the user, an AI
  session, Docker/WSL, mocks, test doubles or disposable fixtures. Keep
  `_APPROVED_TRUST_POLICY_SHA256` empty, migration head `0012`, migration `0013`
  absent and all Phase 5 Feature Gates false unless a later task explicitly and
  separately authorizes the relevant external state change.
- P5.6A freezes a compile-only first-party native Skill contract. It does not
  authorize Skill persistence, migration `0013`, Browser Skill APIs,
  installation, execution, MCP, Marketplace or production Runtime. Instruction
  Skills cannot request tools or capability; workflow/script versions cannot
  be approved or published. Skill execution remains blocked until the roadmap
  explicitly proves the required P5.4 and P34.7 boundaries.
- The separately authorized personal successor is now at P6.3 with migration
  head `0016` and migration `0017` absent. It permits exactly fifteen
  source-owned first-party instruction-only Skills, at most eight live Skills
  and 32 KiB aggregate instructions per Agent binding, plus a manually launched
  six-tool read-only MCP preview. MCP is not mounted into Agent Alpha and
  `MCP_RUNTIME_ENABLED` remains false. DeepSeek/GPT/Kimi/GLM/Claude model-name
  profiles select conservative prompt/context guidance only on the current Chat
  Completions transport; they do not prove native thinking, cache, effort,
  strict tools or MCP. The Windows Companion may verify a closed release
  archive and report safe user/machine/custom plans, but mutating `install` is
  frozen until handle-relative path identity binding is implemented and proven.
  It must not elevate, write system integration, start Docker/WSL, or mutate
  Hyper-V/VHDX. Runtime, Planner and
  Multi-Agent remain disabled; third-party Skill import, executable Skills,
  MCP-to-Agent integration, published OCI images, Authenticode and live public
  deployment require separate evidence. Read INV-077 through INV-080 before
  changing these P6.3 boundaries.

- P6.4 is the narrow personal Agent practice lane. It permits one or 3-6
  separately metered, serial Model Gateway calls in one Owner-declared roster
  while Planner, enterprise Multi-Agent and MCP remain false. Model output is
  proposal-only: citations are scored locally, artifacts are rendered from a
  closed schema, and Workspace writes require exact CAS and rollback inside a
  disposable root. Read INV-081 and
  `docs/architecture/p6-4-personal-agent-practice.md` before changing this lane.
  The loopback live-matrix runner can never set `production_accepted=true`;
  only the outer disposable-target controller may do so after the strict final
  receipt validator proves all six journeys, Provider/document cleanup, canary
  closure, every gate false and zero labeled Compose resources. If Docker is
  not already healthy, stop before activation and do not start or repair
  Docker Desktop, WSL, Hyper-V or VHDX to manufacture P6.4 evidence. A
  Workspace-private Browser document is visible or deletable only through its
  exact live membership binding; failed initial metadata commits must remove
  the uploaded object or veto. Canonical v1 chunks take precedence over a v2
  shadow for the same source document. Node invocation/task identities are
  unique, streamed metadata order is fail-closed, and the outer controller
  requires a clean unchanged source HEAD plus a read-only healthy Linux Docker
  preflight before creating any disposable target material.
- P6.5 is the per-user Windows desktop distribution lane. Read INV-082 and
  `docs/architecture/p6-5-windows-desktop-distribution.md` before changing its
  SQLite backend, Next desktop proxy, Electron shell, RuntimeHost, payload,
  PyInstaller or WiX authoring. The instance token is server-owned, never a
  Browser credential, and readiness requires a fresh challenge-HMAC proof over
  a digest-pinned loopback runtime. Installation is per-user under
  `%LOCALAPPDATA%\Programs\OmniBase`; `%LOCALAPPDATA%\OmniBase` is application
  data that normal uninstall must retain. Docker, WSL, PostgreSQL, BGE-M3 and
  enhanced Sandbox components remain optional and must not be silently
  installed or started. Unsigned, dirty-source, incomplete-product-journey or
  untested installer artifacts are engineering outputs, not a distributable
  OmniBase 1.0.0 EXE.

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
- Recursive filesystem deletion is a separately authorized destructive action,
  not a normal cleanup primitive. Before deleting any directory tree, resolve
  and print the exact literal absolute target, prove it is neither a workspace
  root nor an ancestor/sibling container of repositories, worktrees, release
  artifacts, user libraries or unrelated data, and inventory the intended
  entries with the same shell and path semantics that will perform the delete.
  Never pass a PowerShell-enumerated path to `cmd.exe rd`, a batch builtin or a
  second shell; quoting and extended-path reinterpretation can widen the target.
  Do not use a glob, unresolved environment variable, command substitution,
  junction/reparse traversal or a parent directory to work around long paths.
  Delete only one already-verified literal target per operation. Prefer a
  recoverable same-volume move to a clearly named quarantine/trash directory;
  permanent deletion requires explicit user authorization naming the exact
  target after the inventory is shown. If containment, ownership, link state or
  recovery scope is uncertain, stop. See INV-073.
- Treat Docker Desktop, WSL, Hyper-V and other VM disk images as stateful
  infrastructure, not disposable cache files. Before any VHD/VHDX/VDI/VMDK
  maintenance, identify the owner, distinguish system and data disks, stop all
  writers, verify the exact absolute path and mount state, inventory referenced
  containers/volumes, and create a length-checked backup with a tested restore
  path. Never delete, truncate, replace or shrink a mounted or unknown virtual
  disk. `compact` reclaims host blocks but does not impose a capacity limit;
  capacity changes must use a platform-supported filesystem-aware operation and
  be verified at the guest filesystem and container-format layers. A restored
  disk must preserve or explicitly restore the VM service ACL/SID before boot.
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
