# P5.1A Agent Registry Contract decision

Date: 2026-08-03

Decision:

```text
P5.1A offline contract preflight: implemented / verified
P5.1 database foundation: not implemented
P5.1 Browser API: not implemented
P5.1 Runtime installation: not implemented
P5.1 production: blocked / not_proven
P5.2+: frozen
```

This decision separates the implemented offline registry contract preflight
from the P5.1 database/API/runtime work, which is not started and not
authorized.  The three Phase 5 feature gates stay `false`, P5.0 and P34.7
remain `blocked/not_proven`, and P5.1A never starts or preinstalls an Agent,
Planner, Executor, queue, worker or scheduler.  INV-025–INV-034 remain Phase 5
planned reservations.

## Implemented admission surfaces

- Offline strict DTOs for `AgentDefinition` (draft/active/disabled/revoked;
  low/medium/high/critical; tenant/workspace scopes), `AgentVersion` immutable
  manifests (draft/sealed/deprecated/revoked; canonical digest over raw UTF-8
  bytes excluding the self-referential digest field; controlled JSON Schema
  subset with local `$ref` only; budget ceilings; wildcard/duplicate tool ID
  rejection) and `WorkspaceAgentBinding` (pending_approval/installed/disabled/
  superseded/revoked; exact version/digest binding; approval policy requiring
  high/critical approvals; disabled/superseded field constraints).
- Strict closed-set contract `deployment/production/phase5-registry-contract.example.json`
  pinning the P34.7 decision and P5.0 admission contract digests, the
  migration revision baseline (0001–0009), budget ceilings, approval policy,
  forbidden source paths and five sealed contract/test digests, plus positive
  registry fixtures.
- Validator `scripts/production/validate_p5_1_registry_contract.py`:
  `--validate-only` never returns ready and always reports
  `contract_valid`, `runtime_activation_allowed=false`,
  `registry_runtime_implemented=false`, `database_schema_applied=false`,
  `public_api_exposed=false`; `--verify` checks Git provenance, formal
  states, sealed digests, migration head/revision set, forbidden source
  paths, OpenAPI agent endpoints and the server environment feature gates,
  reusing the P5.0 per-component symlink/reparse path rules and requiring
  report output outside the repository.
- Threat-model supplement in `docs/phase-5-threat-model.md`, contract
  definition `docs/phase-5-agent-registry-contract.md`, maintainer map
  INV-040 + `phase5-registry-contract-preflight` module, and CI validate-only
  gates.
- The validator never reads the root `.env`, a credential, a database, a
  migration or the network; the module import allowlist (stdlib +
  `omnibase.production`) is enforced by an AST test.

## Local verification (implementation commit)

- P5.1A + P5.0 + P34.7 focused Backend: `188 passed`.
- Backend non-integration: `1328 passed, 14 skipped, 14 deselected`.
- Backend Mypy: `153 source files, 0 issues`.
- Changed Python scope: Ruff check and format check PASS.
- Maintainer map: `30 invariants, 22 modules, 287 path specs, 671 matched
  files, 148 entrypoints, 14 discovered HTTP entrypoints, 92 verification
  commands`; benchmark validator `3 plans / 8 scenarios / 6 critical /
  9 unsafe vetoes`.
- `docker compose --env-file .env.example config --quiet` PASS; compileall
  PASS; `git diff --check` PASS; changed-file credential scan `1 hit` (a
  synthetic `connection_string` negative-test fixture, not a real credential).

## Clean-checkout verification

The formal validator was run from a fresh detached clean worktree at the
implementation commit `86286dd5d0cd7e0d3b655a35cab9322c3018139e`:

```text
python scripts/production/validate_p5_1_registry_contract.py \
  --verify \
  --output <repository-outside-path>/p5-1-registry-contract.json
```

Result:

```text
exit code: 2
state: blocked/not_proven
contract_valid: true
activation_allowed: false
feature gates: agent_runtime=false, agent_planner=false, multi_agent=false
p34_7 formal state: blocked/not_proven
p5_0 formal state: blocked/not_proven
source commit: 86286dd5d0cd7e0d3b655a35cab9322c3018139e
source tree: ec03eb0f1e8733b972d7e6a6cf4fadcc42d0fd66
source clean: true
source files: 25
source manifest SHA-256: 9b370eba9ab7a795ce5fe02dbf536d765ed7033b00445979e7aa1121a1055f74
configuration digest: d59568982ed958da93726e640122252bfe8a157956f34b2afdbbe9a6a057b374
report SHA-256: d52f3b5ac5f2a543fd4049d8506bd6ac6a3697ec48896591f7cd365da88ed228
migration head: 0009
blockers: 7
vetoes: 0
root .env accessed: false
business database accessed/migrated: false/false
external network accessed: false
agent registry runtime created: false
agent API exposed: false
agent runtime activated: false
planner/executor activated: false/false
worker or scheduler started: false
```

Blockers: Agent Runtime gate remains disabled; P34.7 formal state not ready;
P5.0 admission formal state not ready; production evidence not proven; Agent
Registry database foundation not implemented; Agent Browser API not
implemented; Workspace installation service not implemented.

This is the required reproducible safe-refusal result.  It does not unlock
P5.1 database/API/runtime work or Phase 5 runtime.  Re-run the validator
whenever a tracked P5.1A source byte changes or new P34.7/P5.0 evidence is
admitted.

## Independent maintainer review and corrective hardening

An independent review of the three external implementation commits found the
offline architecture sound but identified contract gaps that made the original
evidence insufficient for acceptance. The review repaired the gaps without
adding or unlocking any ORM, migration, service, Browser API, SDK Agent call,
Planner, Executor, worker, scheduler or Runtime surface:

- the CLI `--verify` path now supplies the current process values for all three
  server-owned Feature Gates instead of silently verifying an empty mapping;
- definition/version/binding IDs, tenant logical keys and definition semver
  labels are checked for collection-level uniqueness before dictionary lookup;
- definition→version→binding edges now reject Tenant drift, definition/version
  identity splicing, Workspace installation-scope bypass and version-level risk
  downgrades;
- the controlled JSON Schema validator now validates
  `exclusiveMinimum`/`exclusiveMaximum` as finite numbers instead of merely
  allowing the keyword;
- nested Feature Gate and critical-veto objects are closed sets;
- CLI configuration parent components and report output components reject
  symlink/reparse points, and an existing symlink report target is never
  followed or overwritten.

Post-fix local gates:

```text
P5.1A + P5.0 + P34.7 focused: 199 passed
Backend non-integration: 1339 passed, 14 skipped, 14 deselected
Mypy: 153 source files, 0 issues
Changed Python Ruff check: PASS
Changed Python Ruff format --check: PASS
compileall: PASS
maintainer map: PASS
maintainer benchmark: PASS
Compose config --quiet: PASS
validate-only: blocked/not_proven, contract_valid=true, activation_allowed=false
```

The original three implementation commits remain part of the provenance, but
their pre-fix test totals and clean-checkout report are historical evidence,
not the final independent acceptance result. A fresh clean-checkout report is
recorded after the corrective commit below.
