# P5 Personal Production Target Runbook

This runbook is for the loopback-only personal production target. It does not
authorize tools, Sandbox hostile code, Planner, Multi-Agent, Skills or MCP.

## 1. Prepare operator-owned paths

Create distinct directories outside the repository for release receipts,
target state and backups. Copy
`deployment/personal-production/operator.env.example` to a fourth
operator-controlled location and replace every placeholder. Never populate or
read the repository root `.env` for this target.

The operator env must use a new Provider credential encryption key and strong
independent PostgreSQL, Redis, MinIO and JWT secrets. The Provider endpoint
allowlist contains hostnames only. A Provider API credential is configured
later through the authenticated product UI; do not place it in this env file.

## 2. Run the offline doctor

Run only from a clean commit contained by a fetched remote-tracking ref:

```powershell
python scripts/production/manage_p5_personal_target.py doctor `
  --repo-root <absolute-clean-checkout> `
  --release-dir <absolute-release-dir> `
  --state-dir <absolute-state-dir> `
  --backup-dir <absolute-backup-parent> `
  --secret-env <absolute-operator-env>
```

The command prints redacted facts only. A missing key, placeholder, permissive
ACL, dirty checkout, non-public HEAD, migration drift, gate drift or
insufficient capacity is a veto.

## 3. Create and verify a release receipt

```powershell
python scripts/production/manage_p5_personal_target.py release-manifest `
  --repo-root <absolute-clean-checkout> `
  --release-dir <absolute-release-dir> `
  --state-dir <absolute-state-dir> `
  --backup-dir <absolute-backup-parent> `
  --secret-env <absolute-operator-env> `
  --output <absolute-release-dir>\release.json

python scripts/production/manage_p5_personal_target.py verify-release `
  --repo-root <absolute-clean-checkout> `
  --manifest <absolute-release-dir>\release.json
```

This receipt is source/target preflight evidence. After image construction,
record the immutable image IDs/digests in the execution receipt; a source
receipt alone is not an image provenance claim.

## 4. Render and build without touching another stack

Use a unique Compose project name and the explicit operator env:

```powershell
$env:COMPOSE_PROJECT_NAME='omnibase-personal-prod-r1'
docker compose --env-file <absolute-operator-env> `
  -f deployment/personal-production/compose.yml config --quiet
docker compose --env-file <absolute-operator-env> `
  -f deployment/personal-production/compose.yml build
```

Before `up`, inspect the rendered model and confirm that only the frontend has
a host port, it is bound to `127.0.0.1`, data services are internal-only, and
Runtime/Planner/Multi-Agent are false.

## 5. Start and verify the base target

Starting the target is an external state change. Use the same explicit project,
env and Compose file for every command. The migration and MinIO initialization
jobs must exit successfully before the backend starts.

```powershell
docker compose --env-file <absolute-operator-env> `
  -f deployment/personal-production/compose.yml up -d
docker compose --env-file <absolute-operator-env> `
  -f deployment/personal-production/compose.yml ps
```

Verify the loopback frontend, health/readiness, registration/login, one Owner,
one Workspace and one sealed no-tool AgentVersion. Base-target acceptance must
also prove that invocation is unavailable while Runtime=false.

## 6. Stop and restart

Stop new Runtime admission first. If a canary was active, roll it back or write
the kill marker, restore Runtime=false, then wait for durable Task/Run
convergence. Preserve unknown/reconciliation records.

```powershell
docker compose --env-file <absolute-operator-env> `
  -f deployment/personal-production/compose.yml stop frontend backend
docker compose --env-file <absolute-operator-env> `
  -f deployment/personal-production/compose.yml start backend frontend
```

After restart, repeat authenticated smoke and prove that no ambiguous attempt
was replayed.

## 7. Cold backup

Stop application admission and writers before exporting. Use
`manage_p5_personal_backup.py plan-backup` to create a new backup root. Place
the verified release receipt, PostgreSQL custom dump, complete MinIO export and
Runtime config/state/readiness assets in its fixed layout. While the same cold
writer barrier remains active, run `capture-postgres-inventory` with an
explicit operator-controlled `DATABASE_URL`. It records a read-only repeatable
snapshot bound to the exact dump, migration head, tenant registry/schemas and,
from migration `0013`, the Memory table/trigger/vector inventory. Then run
offline `seal-assets` and `verify-backup`.

```powershell
$env:DATABASE_URL='<explicit source database URL>'
python scripts/production/manage_p5_personal_backup.py capture-postgres-inventory `
  --repo-root <absolute-clean-checkout> `
  --postgres-dump <absolute-backup-root>\postgres\database.dump `
  --output <absolute-backup-root>\postgres\inventory.json `
  --source-database <exact-source-database> `
  --capture-mode source_backup
```

This is the controller's only online subcommand. It must not load the root
`.env`, print connection material or run after writers resume. Plan, seal,
verify and restore planning remain offline.

On Windows, do not create a custom PostgreSQL dump through PowerShell binary
redirection. Write the dump inside the PostgreSQL container, copy it through a
non-tmpfs container path with `docker cp`, verify it with `pg_restore --list`,
then remove only that exact temporary container file. Preserve MinIO object
paths when exporting; do not flatten an object such as
`rehearsal/probe.txt` into the backup root.

Redis is intentionally omitted. If a Redis dump appears in the backup root,
verification fails.

## 8. Restore and upgrade

Create a canonical PostgreSQL database inventory and run `plan-restore`. The
target database must be a previously absent `omnibase_restore_*` name and the
MinIO restore root must be new and outside both repository and backup. After
restoring, capture a second PostgreSQL inventory from that new database with
`--capture-mode restore_new_evidence`; it must independently prove the same
tenant/migration/Memory structure and must not reuse the source inventory.

Restore into a separate Compose project/volume set. Keep Runtime=false. Verify
migration revision, tenant schemas, append-only triggers, object inventory and
authenticated product smoke before any cutover. For an A-to-B upgrade, retain
A and its cold backup until B passes. Never overwrite A and never reuse A's
Runtime state directory as an active B directory.

The B operator env must use new secrets, deployment UUID, database name,
bucket and loopback port. Start only B data services before restore, import the
verified PostgreSQL dump and MinIO export, then run the normal migration and
initialization jobs before starting backend/frontend. A passing B rehearsal
does not delete A or authorize automatic cutover.

## 9. Evidence and cleanup

Preserve source receipt, image digests, rendered Compose summary, migration and
one-shot job exits, health/product smoke, stop/restart behavior, backup
manifest, restore verification and upgrade/cutover decision. Report failed or
unexecuted steps explicitly. Do not label a disposable or fake-Provider run as
real Provider production evidence.

## 10. P5.9P disposable personal acceptance

The final personal engineering acceptance is intentionally automated on a
clean GitHub Ubuntu runner:

```bash
python scripts/production/run_p5_9p_personal_acceptance.py \
  --repo-root "$GITHUB_WORKSPACE" \
  --work-root "$RUNNER_TEMP/omnibase-p5-9p-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
```

It creates random `omnibase_test_p59_*` and `omnibase_restore_p59_*` database
names and two unique Compose projects. It never reads the root `.env` or uses a
real Provider credential. The internal fake Provider is not reachable from the
host.

The journey deliberately SIGKILLs Core during an active SSE stream. The
acceptance overlay sets `backend.restart=no`; the runner verifies that Core
remains stopped, waits longer than the real 90-second TaskLease TTL, then
restarts it. Exact replay must close the old invocation without another
Provider call. Only the Owner's explicit `retry_of` may create a new execution,
and every execution and fencing identity must differ from the old one.

After kill-switch verification, recreate the backend without the Runtime
overlay before cold backup. Restore into Project B with a new database and
volumes, authenticate, verify the Workspace and migration `0014`, and confirm
Runtime remains false. Compare the stopped source database fingerprint before
and after restore.

Successful cleanup removes both Compose projects, networks and volumes plus
operator env, canary state and dump. Retain and upload only
`p5-9p-acceptance-receipt.json`. A cleanup leak changes the job result to
failure. This acceptance is production-like engineering evidence for P6.0
Personal Admission, not a deployment or real-Provider cutover.
