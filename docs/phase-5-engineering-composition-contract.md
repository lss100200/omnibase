# P5.4B Engineering Composition Contract

## Status

P5.4B is an **engineering-only** composition seam for the existing P5.4A typed
single-Agent Executor. It is not production wiring, not a Browser/API feature,
and not an authorization to enable the Agent Runtime. Production activation is
explicitly disabled.

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

`LiveRuntimeAuthorityValidator` reads the live task, Agent Run, Workspace Run
lease and Workspace Node rows in a fresh session before each Gateway call. It
requires matching tenant/workspace/task/run generations, an active unexpired
lease, matching run and node fencing tokens, a live runtime identity and a
verified active node. A stale, revoked, expired or mismatched fact rejects the
call before the Gateway boundary.

The runtime identity and workload digest are server-owned and must be bound to
the same P34 Workspace Run and P5 Agent Run. Terminalization clears the live
lease, fencing and runtime bindings; an old holder cannot resume the run.

## Evidence and recovery

The current-baseline disposable Gate is
`scripts/production/run_p5_4b_engineering_composition_disposable_gate.py`. It
uses only an isolated `omnibase_test_p54b_*` PostgreSQL sentinel, upgrades that
sentinel to `0012`, runs the focused integration suite, and verifies mandatory
`0/0/0` cleanup. It must never be described as a production Gate.

The Gate and its evidence explicitly record:

- production Runtime activated: `false`;
- all three Phase 5 Feature Gates: `false`;
- migration head: `0012`;
- migration `0013`: not created;
- root `.env`: not accessed;
- business database: not accessed or migrated;
- external network: not accessed.

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
not change `backend/src/omnibase/production/composition.py` or the disposable
Gate implementation as part of this documentation contract.

## Focused verification

Run from a clean checkout with the explicit example environment where Compose
is needed:

```text
python scripts/production/run_p5_4b_engineering_composition_disposable_gate.py --validate-only
python -m pytest backend/tests/test_p34_7_production_composition.py -q
python -m pytest backend/tests/test_p5_4a_typed_executor.py backend/tests/test_p5_4a_gateway_adapter.py -q
python -m compileall -q backend/src/omnibase/agent_executor
python scripts/maintenance/validate_maintainer_map.py --repo-root .
python scripts/maintenance/validate_maintainer_benchmark.py --repo-root .
```

The disposable `--run` and `--verify-evidence` modes are separate admission
steps. A successful engineering Gate proves only the documented sentinel and
composition boundary; it does not unlock production.
