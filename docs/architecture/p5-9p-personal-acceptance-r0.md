# P5.9P Personal Production-like Acceptance R0

Status: **implementation ready for independent GitHub Linux execution; P6.0
admission remains pending that receipt**.

P5.9P is the final engineering acceptance for the single-Owner personal
edition. It composes the already-merged personal Agent, Memory, instruction
Skill, restart recovery and production packaging work in one disposable target.
It does not reopen enterprise P34.7, Multi-Agent, Planner, Runner/Broker, MCP,
Marketplace or multi-person approval.

## Acceptance topology

The journey creates two isolated Compose projects from the immutable personal
production target:

```text
Project A: omnibase_test_p59_*
  loopback frontend -> internal backend -> internal deterministic fake Provider
  internal PostgreSQL / Redis / MinIO

Project B: omnibase_restore_p59_*
  new Compose project, new volumes and new database identity
  cold Project-A dump restored with --no-owner --no-privileges
```

The fake Provider has no host port and records only a call counter plus two
boolean observations. It never records prompts, Authorization or credentials.
The acceptance fixture is bind-mounted into a one-shot container and is not
copied into the production backend image.

## Required product journey

The Linux job must prove all of the following in one run:

1. register and log in one human Owner;
2. create one Workspace and install one sealed no-tool AgentVersion;
3. install one first-party, instruction-only, network-denied Skill;
4. explicitly activate the exact personal Runtime canary while Planner and
   Multi-Agent remain false;
5. observe at least three temporally separated SSE chunks through the real
   frontend Route Handler;
6. publish encrypted Workspace-private Memory through the existing
   `create_candidate()` and Owner `confirm_candidate()` lifecycle, including
   Operation, Approval, Grant, MemoryEffect and Audit writes;
7. prove both the Skill marker and decrypted Memory marker reach the internal
   Provider without appearing in the receipt;
8. cancel one live invocation and observe durable `cancelled` convergence;
9. SIGKILL Core while the Provider stream is active, keep the container stopped
   beyond the real TaskLease TTL, then restart it;
10. prove exact replay closes the old invocation as `blocked_unknown`, opens one
    reconciliation and does not increment the Provider call counter;
11. let the Owner issue explicit `retry_of`, proving all-new Task, Attempt,
    Effect, Operation, TaskLease, RunLease and runtime/workload identities;
12. activate the kill switch and prove a subsequent invoke cannot call the
    Provider;
13. recreate the deployment without the Runtime overlay and prove Runtime is
    false;
14. stop writers, create and list a custom-format `pg_dump`, restore it into
    Project B, authenticate, list the preserved Workspace, verify migration
    `0014`, and prove Runtime remains unavailable;
15. compare the stopped source database fingerprint before and after restore;
16. remove both Compose projects, their networks and volumes, and delete the
    run-scoped operator env, canary state and database dump.

Any missing terminal SSE event, Provider auto-replay, reused retry identity,
restart before lease expiry, in-place restore, cleanup leak or Runtime remaining
enabled is a veto.

## Evidence boundary

The local Windows host may run syntax and offline protocol tests. It must not
substitute an unknown PostgreSQL instance when Docker Desktop is unavailable.
The authoritative product evidence is the redacted
`omnibase.p5-9p-personal-acceptance.v1` receipt produced by the GitHub Ubuntu
job from a clean checkout.

The receipt contains product identities, booleans, counts, timing gaps and
digests only. It rejects secret-shaped keys and Provider/database secret
locators. GitHub uploads only that receipt, never operator env files, canary
state or the database dump.

## Admission consequence

P5.9P passing permits a small P6.0 Personal Admission record. It is not itself
a public deployment or cutover. After the journey:

```text
AGENT_RUNTIME_ENABLED=false
AGENT_PLANNER_ENABLED=false
MULTI_AGENT_ENABLED=false
migration head 0014
migration 0015 absent
no real Provider credential used
```
