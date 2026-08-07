# P5.4B Engineering Composition Contract

## Status

P5.4B is an **engineering-only** composition seam for the existing P5.4A typed
single-Agent Executor. It is not production wiring, not a Browser/API feature,
and not an authorization to enable the Agent Runtime. Production activation is
explicitly disabled.

Review-Fix Round 1 has passed the engineering disposable Gate and independent
evidence verification. This changes only the engineering admission result;
production admission remains `blocked/not_proven`, production Runtime remains
disabled, and all three Phase 5 Feature Gates remain false.

The checked-in and disposable evidence boundary is fixed at migration head
`0012`. P5.4B does **not** create migration `0013`, alter the database schema,
or perform a migration. Any future schema change requires a separately
reviewed phase and a new migration contract.

## Composition

The composition root is:

- `backend/src/omnibase/agent_executor/engineering.py`
  - `build_engineering_single_agent_executor`
  - `EngineeringSingleAgentExecutor`
  - `LiveRuntimeAuthorityValidator`
  - `UnavailableEngineeringSingleAgentExecutor`

The builder returns the unavailable implementation unless every dependency is
explicitly admissible:

1. `P5_4B_ENGINEERING_ENABLED` is exactly `true` (or an explicit `enabled=True`);
2. the supplied migration head is exactly `0012`;
3. `AGENT_RUNTIME_ENABLED`, `AGENT_PLANNER_ENABLED` and `MULTI_AGENT_ENABLED`
   are all false, using the existing strict flag vocabulary;
4. a Gateway service, SQLAlchemy session factory and server-owned workload
   credential seam are explicitly injected.

The builder never migrates and never connects merely to inspect the migration
head. Missing or invalid composition dependencies fail closed.

The composed executor retains P5.4A's single capability:

```text
knowledge_search -> workspace.knowledge.search
```

It accepts one validated, immutable plan node and only a low-risk,
`read_only` effect. The Gateway adapter receives a server-owned
`WorkloadCredential`, calls the independent `GatewayService.rag_search` path,
and returns bounded logical DTOs. Browser JWTs, physical locators, provider
secrets, host paths, process/socket handles and arbitrary tool fields are not
composition inputs.

## Authority revalidation

`LiveRuntimeAuthorityValidator` locks and reads the live Workspace, Task,
AgentVersion, installed Workspace binding, Agent Run, Workspace Run and RunLease
rows in a fresh session before each Gateway call. The persisted chain is
`AgentRun.workspace_run_id -> WorkspaceRun.id -> RunLease.run_id`; an
`AgentRun.id` is never treated as the `WorkspaceRun.id`. It requires matching
tenant/workspace/task/run generations, Task actor, proposal version/digest,
resource-scope and budget-policy digests, sealed AgentVersion and installed
binding identities, runtime and workload identity, the current WorkspaceRun
fencing cursor, and an active lease whose expiry is compared with
`clock_timestamp()`. Run/Node fencing must agree with a live active Node and a
verified, unexpired attestation. A stale, revoked, expired or mismatched fact
rejects the call before the Gateway boundary. The formal builder always installs
this validator and does not accept an injected authority-validator bypass.

The runtime identity and workload digest are server-owned and must be bound to
the same P34 Workspace Run and P5 Agent Run. The workload digest is a distinct
identity domain from the mTLS certificate thumbprint: the former binds runtime
execution facts, while the latter binds transport proof and capability-token
`cnf`. Both are mandatory lowercase SHA-256 values and swapping either value
fails closed. Terminalization clears the live lease, fencing and runtime
bindings; an old holder cannot resume the run.

The credential attestor, P5.4B live validator and Gateway Core verification run
in separate transactions. Credential issuance revalidates the P34 Run/Lease/
Node/fencing chain, the P5.4B validator revalidates the persisted Task/Run chain,
and Gateway Core revalidates capability scope, resource policy, budget and
audit. This is layered fail-closed verification, not an atomic authority
closure. A revocation may race between those transactions. P5.4B therefore
keeps production admission blocked/not_proven; it does not hold database locks
across arbitrary RAG/provider work and does not overclaim the residual TOCTOU
risk as solved.

## Evidence and recovery

The Gate has two evidence generations. The historical artifacts under the
legacy `.tmp/p5-4b-engineering-composition-gate` directory are retained and
marked superseded/incomplete. Gate v2 writes every artifact into a unique
run-scoped directory under `.tmp/p5-4b-engineering-composition-gate-v2/`,
records raw command outputs and exit-code sidecars, measures the sentinel
Alembic head and cleanup, and independently recomputes source, artifact and
evidence SHA-256 digests. Failed runs remain retained but cannot verify as a
successful seal. A v2 disposable run proves only the documented sentinel and
composition boundary; it does not unlock production.

The Gate and its evidence explicitly record:

- production Runtime activated: `false`;
- all three Phase 5 Feature Gates: `false`;
- migration head: `0012`;
- migration `0013`: not created;
- root `.env`: not accessed;
- business database: not accessed or migrated;
- workload-container external network: denied by an internal-only Docker
  network;
- Docker image acquisition: disabled with local image preflight and
  `pull_policy/--pull never`;
- backend image, PostgreSQL image, shared venv volume and installed package
  inventory: measured and sealed; the evidence explicitly remains
  `ambient_runtime_dependent=true` because it does not hash every installed
  dependency byte.

Source manifests and evidence SHA-256 values are sealed raw-byte records. Do
not rewrite, normalize, or replace historical evidence chains. If a sealed
source or evidence digest drifts, stop admission, preserve the prior record,
identify the exact changed path, and issue a forward documentation/evidence
fix from a clean checkout. Never weaken verification or manufacture a passed
claim from a placeholder digest.

If a disposable run fails, retain the failure report, clean only the explicitly
named disposable project/resources, and keep production disabled. Recovery is a
new reviewed commit or a new isolated sentinel run; it is not a migration
rollback, direct database repair, provider retry, or production activation.

## Deliberate non-goals

P5.4B adds no Browser route, SDK, queue, worker, scheduler, Planner Runtime,
second tool, Skill/MCP runtime, Shell, SQL, arbitrary HTTP, Sandbox execution,
file access, provider production client, or multi-Agent orchestration. It does
not change `backend/src/omnibase/production/composition.py`.

## Focused verification

Run from a clean checkout with the explicit example environment where Compose
is needed:

```text
python scripts/production/run_p5_4b_engineering_composition_disposable_gate.py --validate-only
docker compose --env-file .env.example run --rm --no-deps -v .:/workspace -w /workspace/backend backend pytest tests/test_p5_4b_gate_v2.py -q
docker compose --env-file .env.example run --rm --no-deps backend pytest tests/test_p5_4b_engineering_composition.py tests/test_p5_4a_typed_executor.py tests/test_p5_4a_gateway_adapter.py -q
python -m compileall -q backend/src/omnibase/agent_executor
python scripts/maintenance/validate_maintainer_map.py --repo-root .
python scripts/maintenance/validate_maintainer_benchmark.py --repo-root .
```

The disposable `--run` and `--verify-evidence` modes are separate admission
steps. A successful engineering Gate proves only the documented sentinel and
composition boundary; it does not unlock production.
