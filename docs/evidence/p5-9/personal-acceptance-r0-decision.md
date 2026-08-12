# P5.9P Personal Acceptance R0 Decision

Date: 2026-08-12

## Current decision

```text
P5_9P_IMPLEMENTED_PENDING_GITHUB_LINUX_ACCEPTANCE
P6_0_PERSONAL_ADMISSION_PENDING
PRODUCTION_RUNTIME_NOT_ACTIVATED
```

## Implemented evidence path

- a disposable production Compose overlay with an internal deterministic fake
  Provider and a one-shot acceptance fixture;
- a host runner that drives the real loopback frontend/API/SSE product path;
- sealed first-party instruction Skill and encrypted scoped Memory projection;
- durable cancellation;
- Core SIGKILL, real TaskLease expiry, restart recovery and no automatic
  Provider replay;
- explicit Owner `retry_of` with all-new execution identities;
- kill switch and deployment-layer Runtime disablement;
- cold custom-format dump, restore-new, authenticated smoke and source database
  fingerprint comparison;
- exact Compose/volume cleanup and removal of run-scoped secret material;
- a redacted receipt that rejects secret-shaped fields and locators.

## Current local evidence

```text
Python syntax compilation: passed
offline acceptance harness tests: 9 passed
locked OpenAI SDK 1.109.1 fake-Provider stream smoke: passed (5 chunks)
base Compose posture: Runtime/Planner/Multi-Agent false; Provider has no host port
canary Compose posture: Runtime true only in overlay; Planner/Multi-Agent false
GitHub production-like acceptance: not run yet
```

Docker Desktop on the Windows host did not answer `docker version` within the
bounded timeout. No unknown local PostgreSQL service, root `.env`, business
database or real Provider credential was used as a substitute. The journey is
therefore deliberately pending the clean Ubuntu GitHub job.

## Required promotion evidence

Promotion to `P5_9P_PERSONAL_PRODUCTION_LIKE_ACCEPTANCE_PASSED` requires the
`personal-production-acceptance` job to finish successfully and upload a
receipt whose acceptance field is exactly that value. Required repository CI
must also be green. Until then this file must not be interpreted as a PASS.

## Preserved posture

```text
migration head 0014
migration 0015 absent
AGENT_RUNTIME_ENABLED=false by default and after acceptance
AGENT_PLANNER_ENABLED=false
MULTI_AGENT_ENABLED=false
enterprise P34.7 frozen
root .env not read
business database not accessed or migrated
no deployment or cutover
```
