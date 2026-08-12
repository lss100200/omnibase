# P5.9P Personal Acceptance R0 Decision

Date: 2026-08-12

## Current decision

```text
P5_9P_PERSONAL_PRODUCTION_LIKE_ACCEPTANCE_PASSED
P5_9P_REQUIRED_CI_GREEN
P6_0_NOT_STARTED
PRODUCTION_RUNTIME_NOT_ACTIVATED
```

The authoritative clean-checkout evidence is GitHub Actions workflow run
[`31608502738`](https://github.com/lss100200/omnibase/actions/runs/31608502738)
at exact branch head `9eb7238c164a0dbf380d406104f65f7823a89bbc`.
The required backend, frontend/TypeScript SDK, Compose, guarded disposable
PostgreSQL sentinel and `personal-production-acceptance` jobs all completed
successfully. The personal job completed in 6 minutes 31 seconds and its
workflow cleanup assertion also passed.

## Verified receipt

GitHub uploaded exactly one artifact:

```text
artifact id = 9147061833
artifact name = p5-9p-personal-acceptance-31608502738-1
artifact ZIP SHA-256 = fe3e4822ce2eff58c832f158dd328e980d47ac16425f286777664ae8013c745f
receipt JSON SHA-256 = 1174f6dac2be15bd97a49d83d9a59fd7e3282e3e029137fa900309303895cc57
receipt bytes = 1852
receipt top-level field count = 20
```

The downloaded ZIP digest exactly matched GitHub's artifact digest. The JSON
was parsed independently and passed an exact top-level field closed set,
schema/version checks, nested acceptance assertions and a forbidden secret-key
and locator scan.

Verified receipt facts:

- acceptance is exactly
  `P5_9P_PERSONAL_PRODUCTION_LIKE_ACCEPTANCE_PASSED`;
- incremental frontend SSE emitted three chunks with bounded positive gaps;
- durable cancellation ended as Task `cancelled` with terminal event
  `cancelled`;
- Core interruption converged the old Task to `blocked_unknown`, created one
  reconciliation, made no automatic Provider replay and allowed only an
  explicit Owner retry with a different Task identity;
- the kill switch reached `killed` and blocked a later Provider call;
- one sealed instruction Skill and one scoped Memory item were exercised;
- writers were stopped before the non-empty custom-format PostgreSQL dump;
- restore-new used an `omnibase_restore_*` database, authenticated the restored
  Owner, preserved Workspace/Skill/Memory, kept Runtime false, reported
  migration head `0015` and proved the source database fingerprint unchanged;
- Runtime was false after acceptance; Planner and Multi-Agent were false
  throughout;
- root `.env` was not read, no business database was accessed or migrated, and
  no real Provider credential was used.

The receipt contained no Authorization/JWT/API-key/password field, database or
Redis locator, prompt text, Memory plaintext or Skill instruction content.

## Forward fixes proven by the final run

The final run also proves two fail-closed forward fixes found by earlier CI:

1. The guarded PostgreSQL sentinel now runs the real empty migration-0013
   downgrade/re-upgrade proof before the shared suite creates retained audited
   Memory data. The populated downgrade veto remains unchanged. The final
   sentinel passed in 15 minutes 22 seconds.
2. Cold backup no longer relies on `docker cp` reading a PostgreSQL `/tmp`
   tmpfs file. Binary custom-format `pg_dump` stdout is written exclusively to
   a run-scoped host file, which must be a non-symlink regular file with
   positive size. `pg_restore --list` and restore-new consume those exact bytes
   through stdin and check their return codes.

## Preserved posture

```text
migration head 0015
migration 0016 absent
AGENT_RUNTIME_ENABLED=false by default and after acceptance
AGENT_PLANNER_ENABLED=false
MULTI_AGENT_ENABLED=false
enterprise P34.7 frozen; approved trust-policy digest empty
root .env not read
business database not accessed or migrated
no real Provider credential
no deployment or cutover
P6 work not started
```

This PASS is a disposable personal-edition engineering acceptance. It is not a
public deployment, production Runtime activation, enterprise P34.7 approval,
Planner/Multi-Agent admission, Marketplace/MCP admission or a P6.0 record.
