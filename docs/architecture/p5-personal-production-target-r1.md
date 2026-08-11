# P5 Personal Production Target R1

## Decision

R1 defines the production packaging and operator boundary for the restricted
`personal_single_owner` no-tool product. It turns the already verified Runtime
canary into an installable, startable and recoverable base target. The base
target has now passed a disposable real-container first-boot, restore-new and
A-to-B rehearsal; this is not the same as Owner acceptance of a durable target
or Provider-backed production invocation.

The exact status of this change is:

```text
P5_PERSONAL_PRODUCTION_BASE_R1_ACCEPTED
PERSONAL_BASE_TARGET_STARTABLE_STOPPABLE_RECOVERABLE_UPGRADEABLE
REMOTE_REQUIRED_CI_PASSED
POST_MERGE_RELEASE_RECEIPT_VERIFIED
PERSONAL_RUNTIME_DEFAULT_OFF
PLANNER_DISABLED
MULTI_AGENT_DISABLED
PROVIDER_BACKED_PRODUCTION_JOURNEY_NOT_PROVEN
OWNER_DURABLE_TARGET_CUTOVER_NOT_ACCEPTED
```

## Product scope

The target contains one PostgreSQL database, one Redis cache, one MinIO object
store, one migration job, bounded volume-initialization jobs, one Core backend
and one Next.js frontend. It is for
one authenticated human Owner and the Owner's private AI spaces. It does not
authorize team membership, enterprise authority delegation, hostile-code
Sandbox execution, tools, Skills, MCP, Planner or Multi-Agent execution.

The frozen P34.7 enterprise track remains reusable and unchanged. Its approved
Trust Policy digest remains absent. This personal target follows INV-055 and
INV-056; it does not reinterpret the enterprise joint Gate as passed.

## Network and container boundary

`deployment/personal-production/compose.yml` publishes only the frontend on an
operator-selected loopback port. PostgreSQL, Redis, MinIO and the backend have
no host port. The data network is internal. The backend is the only service
that joins both the data and edge networks, and the frontend reaches the API
through its fixed server-side proxy target.

The backend uses `backend/Dockerfile.production`: a multi-stage image with no
source mount, no reload process, a non-root final user, and no compiler, Git or
download utility in the final stage. The frontend uses the existing standalone
production image. Services use read-only root filesystems, tmpfs for bounded
ephemeral writes, dropped capabilities and `no-new-privileges` where the
upstream image permits it. Persistent writes are confined to named volumes.

## Configuration and release identity

The populated operator env lives outside the repository. The checked-in
example contains placeholders only. The target controller validates an exact
key set, file permissions/ACL, non-placeholder secret posture, PostgreSQL and
Redis URL binding, CORS-to-loopback-port binding, Provider hostname allowlist,
Docker/Compose availability, disk capacity, migration `0012`, absence of
`0013`, default-off feature gates, clean Git provenance and a public remote
reference.

The canonical release receipt binds the Git commit/tree and byte digests of
the production Dockerfiles, Compose file, operator-env shape, Runtime
controller, target controller and backup controller. It contains no secret
values and never starts the target.

## Runtime posture

Base production packaging fixes these values:

```text
AGENT_RUNTIME_ENABLED=false
AGENT_PLANNER_ENABLED=false
MULTI_AGENT_ENABLED=false
PERSONAL_RUNTIME_PROFILE=""
```

A later reviewed canary overlay may set Runtime=true for one exact
Tenant/Workspace/Owner/AgentVersion binding only after the INV-056 config,
readiness seal and append-only activation ledger pass. Planner and Multi-Agent
remain false. Any ambiguity requires the kill marker and deployment-layer
Runtime=false before investigation.

## Backup, restore and upgrade boundary

Redis is transient and is never authoritative backup material. A cold backup
binds the release receipt, PostgreSQL custom dump, complete MinIO export and
the personal Runtime config/state/readiness bytes in one canonical manifest.
The offline controller rejects symlinks/junctions, path escape, duplicate or
unknown fields, unrecorded files and digest/size drift.

Restore always targets a new `omnibase_restore_*` database and a new MinIO
root. It never overwrites the source. Restored or upgraded targets start with
Runtime=false; ambiguous Tasks/Runs/effects are not replayed from Redis or
silently converted to success. Application smoke, owner review and a fresh
activation are required before cutover.

An A-to-B upgrade is therefore:

```text
stop admission -> converge/record work -> cold backup -> verify backup
-> build and seal B -> restore into new B identities -> migrate B
-> smoke with Runtime=false -> owner cutover -> optional fresh canary review
```

## Evidence boundary

The disposable execution recorded in
`docs/evidence/p5-personal-production-target-r1-decision.md` proves that the
current target can build, first-boot, stop/restart, cold-backup, restore-new and
start a separate B project while keeping all Runtime gates off. PR #26 and the
release-receipt durability forward-fix PR #27 passed required remote CI; the
preserved receipt verified again after GitHub deleted the temporary feature
refs and `origin/main` became the public containment ref. The evidence still
does not prove Owner cutover of a durable non-disposable host, a fresh
Provider-backed invocation or Runtime activation. Previously shared Provider
credentials are not valid production inputs and must not be reused.
