# P5 Personal Production Target R1 Decision

Date: 2026-08-11

## Decision

```text
P5_PERSONAL_PRODUCTION_TARGET_R1_REHEARSAL_PASSED_PENDING_CLEAN_HEAD_REVIEW
PERSONAL_BASE_TARGET_STARTABLE_STOPPABLE_RECOVERABLE_UPGRADEABLE
PERSONAL_RUNTIME_DEFAULT_OFF
AGENT_PLANNER_ENABLED=false
MULTI_AGENT_ENABLED=false
PROVIDER_BACKED_PRODUCTION_JOURNEY_NOT_PROVEN
ENTERPRISE_P34_7_TRACK_FROZEN_BLOCKED_NOT_PROVEN
```

## Implemented in this increment

- hardened non-root backend production image;
- standalone Next.js production image reuse;
- loopback-only full-stack personal production Compose;
- one-shot migration and object-store initialization jobs;
- default-off Runtime and permanently off Planner/Multi-Agent base posture;
- offline target doctor and canonical source/target release receipt;
- exact operator-env and service-coordinate validation;
- offline cold-backup sealing and restore-new planning;
- packaging, controller and attack tests.

## Forward fixes found by real first boot

The disposable target was built and started from the production files rather
than inferred from source tests. Post-merge release verification then exercised
the preserved receipt after the temporary feature ref was deleted. Together
those checks exposed and fixed five real defects:

- the Redis AOF volume initially rejected the non-root Redis process, so a
  capability-restricted one-shot ownership job now prepares the named volume;
- the Alembic job imports the full production Settings object, so its explicit
  environment now includes the required MinIO, Redis, JWT and Provider-key
  shape while keeping all feature gates off;
- two backend workers raced on first-start extension setup, so the single-user
  target now uses one Uvicorn worker rather than claiming unsupported startup
  concurrency;
- Windows clients may send `Expect: 100-continue`, which Node/undici refuses to
  forward. The frontend proxy now strips that header after buffering the small
  request body and logs only a bounded error name/code on upstream failure;
- the release receipt originally required the temporary remote-tracking ref
  name to remain byte-for-byte identical. Verification now keeps the immutable
  commit/tree binding and still requires current public-remote containment,
  while allowing containment to move from a merged feature ref to `origin/main`.

## Verification completed in the disposable production rehearsal

- packaging/controller focused suite: 29 passed;
- frontend suite after the proxy fix: 95 passed;
- maintainer map: valid with 47 invariants, 40 modules, 696 path specs and 281
  entrypoints; maintainer benchmark: 3 plans / 8 scenarios;
- production backend and frontend images built successfully;
- backend image ID/digest:
  `sha256:6c0c31e63e3c88d98f7efd136ea098e4ae831b52051cde7aa6404036251a0d46`;
- frontend image ID/digest:
  `sha256:ec2269627acd6e41d757bb49db1619be9ea71ba32dc43fb313a9288ef922c62c`;
- backend final image runs as UID 10001, has no Git/wget/gcc, has no writable
  `/app`, includes migration `0012`, has no reload and uses one worker;
- first boot passed for PostgreSQL, Redis, MinIO, migration, initialization,
  backend and frontend; only the frontend published a loopback host port;
- registration/login through the production frontend proxy passed and reported
  Runtime profile `locked`, Runtime inactive, production activation false,
  tools false and Multi-Agent false;
- application stop made the endpoint unavailable; restart restored health and
  the registered identity remained usable;
- cold backup manifest
  `9be464a46ffc520fab8ffb9b23df1a694a9f892eaf2a85374c02b16faacf0ca1`
  sealed a PostgreSQL custom dump, complete MinIO object export, release receipt
  and explicit Runtime-off config/state/readiness assets; Redis was absent;
- restore-new into `omnibase_restore_p5prod_r1_precommit` preserved migration
  `0012`, 6 tenant schemas/users, 18 audit rows and all 36 application triggers,
  including the append-only audit/revocation/lineage triggers;
- the restored MinIO object matched the backup SHA-256
  `78df4f8536fe46504f602a7ce81bc519e476bf076f2a061c817ef2686d1a8503`;
- A-to-B rehearsal used a new Compose project, PostgreSQL/Redis/MinIO volumes,
  deployment UUID and random secrets. B restored the same structural facts,
  started successfully on `127.0.0.1:3122`, and passed registration, login and
  default-off Runtime posture checks while A and its backup remained intact.

The source rehearsal used project `omnibase-test-p5prod-r1-precommit`; B used
`omnibase-test-p5prod-r1-upgrade-b`. Neither is relabeled as the accepted Owner
production deployment.

## Still not proven by this decision

- the final clean committed/pushed HEAD and required remote CI;
- `doctor`, canonical release manifest and `verify-release` from that clean
  public remote-tracked HEAD (the controller correctly vetoes a dirty tree);
- a fresh real Provider credential and Provider-backed no-tool invocation;
- explicit Owner acceptance/cutover of a non-disposable target;
- Runtime canary activation, which is intentionally outside the base-target
  default-off acceptance and remains false here;
- Planner, Multi-Agent, tools, Skills or MCP production execution.

The root `.env` was not read. No business database was accessed or migrated;
only explicit `omnibase_test_*`/`omnibase_restore_*` rehearsal identities were
used. No Provider API key was stored, printed or reused. The separate
`omnibase-p54d-acceptance` stack was not stopped or mutated. No enterprise
Trust Policy digest was approved and Runtime/Planner/Multi-Agent remained off.
