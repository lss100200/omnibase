# P5.0 Phase 5 admission decision

Date: 2026-08-02

Decision:

```text
P5.0 admission gate: BLOCKED / NOT_PROVEN
Phase 5 runtime enabled: false
Planner enabled: false
Multi-Agent enabled: false
```

This decision deliberately separates the implemented, locally reproducible
admission gate from the external production evidence that Phase 5 runtime
engineering requires.  The P5.0 gate is the only permitted Phase 5
deliverable; it validates whether Phase 5 may begin and never starts,
preinstalls or dispatches an Agent, Planner, Executor, queue, worker or
scheduler.  INV-025–INV-034 remain Phase 5 planned reservations and are not
marked as implemented.

## Implemented admission surfaces

- Three independent, server-owned, default-off feature gates:
  `AGENT_RUNTIME_ENABLED`, `AGENT_PLANNER_ENABLED`, `MULTI_AGENT_ENABLED`.
  Missing and empty values resolve to `false`; only the exact tokens `"true"`
  and `"false"` are accepted; case, whitespace and other truthy-looking values
  raise a configuration error instead of being guessed.  Planner requires
  Runtime, Multi-Agent requires both, and no master switch exists.
- Strict P5.0 contract (`deployment/production/phase5-admission.example.json`)
  that pins the P34.7 formal state and decision digest, the migration head,
  the OpenAPI snapshot, Python/TypeScript SDK versions, the production
  composition digest, the runbook digest, `critical_veto.expected = 0` and
  nine `not_proven` production evidence items (Runner 12/12, four production
  roundtrips, provider-backed recovery, data-owner tenant/RAG, two real member
  Overlay/DERP/node-compromise, capacity/SLA).
- Evidence Manifest validator
  (`scripts/production/validate_p5_0_admission.py`) with `--validate-only`
  and clean-checkout `--verify` modes, plus a P5.0 threat model
  (`docs/phase-5-threat-model.md`) and maintainer map INV-039.
- The validator never reads the root `.env`, a database, a migration or a
  secret payload; reports always declare `root_env_accessed=false`,
  `business_database_accessed=false`, `business_database_migrated=false`,
  `hostile_code_executed=false` and `phase5_runtime_activated=false`.

## Local verification (implementation commit)

- P5.0 + P34.7 focused Backend: `68 passed`.
- Backend non-integration: `1208 passed, 14 skipped, 14 deselected`.
- Backend Mypy: `152 source files, 0 issues`.
- Changed Python scope: Ruff check and format check PASS.
- Maintainer map: `29 invariants, 21 modules, 276 path specs, 649 matched
  files, 142 entrypoints, 14 discovered HTTP entrypoints, 87 verification
  commands`; benchmark validator `3 plans / 8 scenarios / 6 critical /
  9 unsafe vetoes`.
- `git diff --check` PASS; changed-file credential scan `0 hits`;
  `docker compose --env-file .env.example config --quiet` PASS.

## Clean-checkout verification

The formal validator was run after the implementation commit from the clean
worktree at `4676a72454babab7ca2a5c0ba4136adde14d3020` (branch
`external/p5-0-admission-gate`):

```text
python scripts/production/validate_p5_0_admission.py \
  --verify \
  --output <operator-controlled-path>/p5-0-admission.json
```

Result:

```text
implementation commit: 4676a72454babab7ca2a5c0ba4136adde14d3020
source tree: a877eab13a57ade866970db2d9883476afa23402
source clean: true
source files: 38
source manifest SHA-256: c45e53141f322f48e2d7f9f26c9c655e448fe571b3ec6e15193ba3676881903c
report SHA-256: 8f6ce30565656c1a57c33537972ce97f5e91b42f0f4bc4e917ac6fc727fc8b1a
exit code: 2
state: blocked/not_proven
activation allowed: false
feature gates: agent_runtime=false, agent_planner=false, multi_agent=false
p34_7 formal state: blocked/not_proven
migration head: 0009
blockers: 11
vetoes: 0
root .env accessed: false
business database accessed/migrated: false/false
hostile code executed: false
phase 5 runtime activated: false
```

This is the required reproducible safe-refusal result for the current
evidence set.  It does not unlock Phase 5 runtime or production.  Re-run the
validator whenever a tracked P5.0 source byte changes or new P34.7 production
evidence is admitted; while P34.7 is not `ready`, `blocked/not_proven` is the
only correct P5.0 state.
