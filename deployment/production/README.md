# P34.7 production admission

This directory is a fail-closed deployment contract, not a service launcher.
It does not make Docker Desktop, WSL or an ordinary container safe for hostile
workspace code.

Run the static contract check without reading evidence or hashing the checkout:

```text
python scripts/production/validate_p34_7_composition.py --validate-only
```

Run the formal provenance/evidence check from a clean public checkout:

```text
python scripts/production/validate_p34_7_composition.py --verify --output /secure/operator/path/p34-7-ab-admission.json
```

Exit codes are `0` for a valid static contract or a formally ready production
admission, `2` for `blocked/not_proven`, and `1` for an invalid contract or a
safety veto.  A `ready` report is evidence for a future deployment controller;
this repository intentionally contains no command that automatically starts or
enables the production Runner, Broker or Gateway after validation.

The checked-in example remains `activation_requested=false`. Its current
external blockers include the current-source Linux Runner 12/12 Gate, the
non-disposable Core/Runner/Broker/Gateway round trips, a real provider-backed
Workspace recovery rehearsal, data-owner-authorized tenant/RAG smoke, two real
member nodes with independent DERP/node-compromise evidence, and production
capacity/fault-injection/SLA samples. Existing disposable and component-level
P34.5/P34.7 evidence is useful engineering evidence but cannot satisfy those
missing production claims.

The validator never reads the root `.env`, secret/certificate payloads, a
database or business storage.  It hashes only paths returned by `git ls-files`
for the explicit source scope.  Runtime credentials remain server-owned and
outside the repository.

## P5.0 Phase 5 admission gate

The P5.0 gate (`phase5-admission.example.json`) decides whether Phase 5
engineering may begin.  It does not start any Agent, Planner, Executor, queue,
worker or scheduler.  The three Phase 5 feature gates
(`AGENT_RUNTIME_ENABLED`, `AGENT_PLANNER_ENABLED`, `MULTI_AGENT_ENABLED`) are
independent, server-owned and disabled by default; missing or empty values
equal `false`, unknown values and dependency conflicts fail closed, and P5.0
stays `blocked/not_proven` while the P34.7 formal state is not `ready`.

```text
python scripts/production/validate_p5_0_admission.py --validate-only
python scripts/production/validate_p5_0_admission.py --verify
```

`--verify` additionally hashes the clean checkout, resolves the feature gates
from the server environment (override with `--gate NAME=VALUE`), and verifies
the migration head, OpenAPI snapshot, Python/TypeScript SDK versions,
production composition digest, runbook digest and the P34.7 decision digest.
The checked-in contract keeps `activation_requested=false`, all gates false
and every production evidence item `not_proven`, so the correct current result
is `blocked/not_proven` with zero vetoes.  The validator never reads the root
`.env`, a database or a migration.

## P5.1A Agent Registry contract preflight

The P5.1A contract (`phase5-registry-contract.example.json`) is an offline
preflight for the AgentDefinition -> AgentVersion -> WorkspaceAgentBinding
contracts.  It implements no ORM, migration, service, Browser API or runtime;
it only validates strict DTOs, closed sets, canonical digests over raw UTF-8
bytes, budget ceilings and approval policy.

```text
python scripts/production/validate_p5_1_registry_contract.py --validate-only
python scripts/production/validate_p5_1_registry_contract.py --verify
```

`--verify` additionally hashes the clean checkout, checks the P5.0/P34.7
formal states and sealed digests, and proves no forbidden runtime/ORM/API
package, migration revision drift or agent OpenAPI endpoint was added.  The
correct current result is `blocked/not_proven` with zero vetoes; the validator
never reads the root `.env`, a database, the network or a migration, and it
never starts an Agent, Planner, Executor, queue, worker or scheduler.  When `--output` is used, the operator must
choose a path outside the repository so writing the report cannot invalidate
the source provenance that was just verified.

## P5.1B Agent Registry persistence foundation (internal only)

P5.1B adds the internal persistence foundation only: the three global
`omnibase_meta` tables (`agent_definitions`, `agent_versions`,
`workspace_agent_bindings`, migration `0010`) and the internal
`RegistryPersistenceService`.  There is still no Browser `/api/v1/agents`
router, no OpenAPI agent endpoint, no SDK client, no frontend and no
Invocation/Runtime/Planner/Executor/orchestration surface; all Phase 5
feature gates stay `false` and P5.1 production stays `blocked/not_proven`.

```text
make test-p5-1b-registry                     # disposable sentinel PostgreSQL Gate
python scripts/production/run_p5_1b_registry_disposable_gate.py --run
python scripts/production/run_p5_1b_registry_disposable_gate.py --verify-evidence docs/evidence/p5-1/phase5-registry-persistence-disposable-gate.json
```

The disposable Gate provisions an isolated Compose project with
`omnibase_test_*` names, a sentinel and a restricted non-owner role, migrates
it to head, runs the guarded integration suite, records sealed evidence and
tears everything down (`0/0/0`).  It never touches a business database and
never reads the root `.env`.
