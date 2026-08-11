# P5.5B Memory Persistence R0 Decision

Date: 2026-08-11

## Decision

```text
P5_5B_MEMORY_PERSISTENCE_COMMITTED_PENDING_FULL_REGRESSION_AND_REMOTE_REVIEW
MIGRATION_HEAD_0013
OWNER_GOVERNED_CANDIDATE_PUBLICATION_IMPLEMENTED
DELETE_EXPORT_CRYPTO_ERASURE_IMPLEMENTED
POSTGRES_BACKUP_INVENTORY_CAPTURE_IMPLEMENTED
MEMORY_BROWSER_API_NOT_CREATED
MEMORY_COMPILER_SEARCH_INJECTION_NOT_CREATED
P5_5C_NOT_STARTED
RUNTIME_PLANNER_MULTI_AGENT_DISABLED
IMPLEMENTATION_COMMIT_E8209EF
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

## Verification completed before this document

```text
P5.5B migration/source contract: 49 passed
P5.5B service + Control Plane + backup focused: 134 passed
P5.1A/P5.2A/P5.3A focused sealed-contract regression: 407 passed
formal ORM/service PostgreSQL lifecycle + live inventory capture: 1 passed
complete disposable PostgreSQL migration/service/attack Gate: 20 passed
Ruff on focused source/tests: passed
Mypy on eight focused source files: passed
git diff --check: passed at the pre-document checkpoint
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

## Final review still required

- run wider Control Plane, backup/restore-new, backend, frontend, type, format,
  Compose and maintainer-map regressions;
- recalculate every affected P5/P34 raw-byte seal from final bytes;
- create forward-only commits, push a PR, pass remote CI and merge only after
  independent final review.

The atomic implementation/migration-hardlock/documentation/seal change was
committed forward-only as `e8209ef` (`feat(p5.5b): persist governed memory and
backup evidence`). No amend, rebase, reset, stash or clean was used.

No root `.env` or previously exposed Provider credential was read. No business
database was accessed or migrated. Runtime, Planner and Multi-Agent remain
false. The enterprise P34.7 evidence track remains frozen/blocked and is not a
dependency of this personal P5.5B increment.
