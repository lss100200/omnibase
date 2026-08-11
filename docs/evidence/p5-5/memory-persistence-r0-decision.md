# P5.5B Memory Persistence R0 Decision

Date: 2026-08-11

## Decision

```text
P5_5B_ENGINEERING_ACCEPTED_READY_FOR_REMOTE_REVIEW
MIGRATION_HEAD_0013
OWNER_GOVERNED_CANDIDATE_PUBLICATION_IMPLEMENTED
DELETE_EXPORT_CRYPTO_ERASURE_IMPLEMENTED
POSTGRES_BACKUP_INVENTORY_CAPTURE_IMPLEMENTED
MEMORY_BROWSER_API_NOT_CREATED
MEMORY_COMPILER_SEARCH_INJECTION_NOT_CREATED
P5_5C_NOT_STARTED
RUNTIME_PLANNER_MULTI_AGENT_DISABLED
IMPLEMENTATION_COMMIT_E8209EF
DOCUMENTATION_COMMIT_82E3243
CONTROL_PLANE_HEAD_SYNC_COMMIT_364A353
NOT_PUSHED_NOT_MERGED_NOT_DEPLOYED
```

## Implemented

- tenant migration `0013_memory_context_capsules.py` with ten Memory tables,
  exact Tenant/schema guards, append-only/lifecycle checks, Candidate-to-Memory
  publication closure and independent `vector(1024)`/`vector(1536)` lanes;
- ORM models and an internal caller-owned transaction service for Candidate
  creation, live-Owner confirmation, logical export and delete/crypto-erasure;
- exact Agent Definition requester support in the existing Control Plane and a
  closed logical audit vocabulary for Memory identifiers/digests;
- acceptance binding across Capsule, Task, Agent Definition, Tenant, Workspace,
  Resource/version, high-risk Operation, Owner-decided Approval and canonical
  request hash;
- controlled-shared Review evidence binding and source-Capsule permanence;
- atomic delete closure across Effect, tombstone, Candidate ciphertext/nonce,
  MemoryVersion content and both vector lanes;
- personal production migration-head hardlock synchronization from `0012` to
  reviewed head `0013`, while rejecting migration `0014+`;
- backup plan/manifest v2 with legacy v1 read compatibility;
- first-release `omnibase.postgresql-backup-inventory.v1` online capture,
  binding dump digest, global/tenant heads, server-owned tenant registry,
  Memory table/trigger inventory and vector dimensions;
- restore-new evidence mode for distinct `omnibase_restore_*` databases.

## Final local verification

```text
P5.5B migration/service/Control Plane focused: 157 passed
personal backup/restore attack tests: 29 passed
P5.1A/P5.2A/P5.3A focused sealed-contract regression: 407 passed
P5.0/P5.5A/P5.6A compatibility focused: 161 passed
complete disposable PostgreSQL migration/service/attack Gate: 20 passed
shared P34.1 Control Plane + Personal Owner PostgreSQL integration: 7 passed
backend non-integration: 2670 passed, 23 skipped, 15 deselected
frontend: typecheck, lint, 95 tests and production build passed
Mypy: 204 source files, no issues
explicit changed-path Ruff check/format: passed
maintainer map: 49 invariants, 42 modules, 739 path specs, 287 entrypoints
maintainer benchmark and Compose config: passed
git diff --check: passed
P5.0/P5.1A/P5.2A/P5.3A/P5.5A/P5.6A clean-HEAD verifiers:
  exit 2 blocked/not_proven, source.clean=true, vetoes=[]
P34 composition/joint: blocked/not_proven; personal Owner contract valid;
  Trust Candidate valid_not_approved; R1 assignment not_proven
```

The formal PostgreSQL journey used only a randomly named disposable
`omnibase_test_p55b_*` database/role and verified create Candidate, Owner
Operation/Approval consumption, confirmation/publication, logical export,
delete/crypto-erasure and committed database state. Its Compose project,
network and volume were removed afterward.

The full disposable Gate initially exposed a test-isolation defect: the formal
service journey produced immutable audit rows before the empty downgrade proof,
so the production populated-downgrade hardlock correctly rejected that later
test. The final suite runs the empty downgrade proof first, then the audited
service journey. No production hardlock was relaxed. The clean rerun passed all
20 cases and removed its exact container, network and volume.

The wider shared integration run found one stale P34.1 current-head assertion
still pinned to `0012`. Forward fix `364a353` updates only that assertion to
the reviewed `0013`; the clean rerun passed 7/7. No P34.1 historical DDL or
production behavior changed.

## Remote review still required

- push the ordinary branch without force;
- create a ready-for-review PR against the latest compatible `main`;
- pass all required remote CI and independent review;
- merge, then re-fetch and verify main HEAD, migration `0013`, empty enterprise
  approved digest and false Runtime/Planner/Multi-Agent gates.

The atomic implementation/migration-hardlock/documentation/seal change was
committed forward-only as `e8209ef` (`feat(p5.5b): persist governed memory and
backup evidence`). Documentation checkpoint `82e3243` and integration-head
forward fix `364a353` follow it. No amend, rebase, reset, stash or clean was
used.

No root `.env` or previously exposed Provider credential was read. No business
database was accessed or migrated. Runtime, Planner and Multi-Agent remain
false. The enterprise P34.7 evidence track remains frozen/blocked and is not a
dependency of this personal P5.5B increment.
