# Memory Privacy, Delete and Export Runbook

## Current authority

P5.5A remains the historical compile-only contract. P5.5B adds tenant migration
`0013`, ORM models, an internal transaction service, independent vector lanes,
Owner confirmation, logical export, deletion/crypto-erasure and cold-backup
inventory capture. P5.5C adds the bounded compiler and prompt projection only
inside the exact personal single-Owner canary. There is still no Browser Memory
API or public Memory search endpoint; Runtime is false by default and Planner/
Multi-Agent remain false.

## Privacy rules

1. Revalidate the live Tenant, live Owner, Workspace membership and exact
   AgentVersion before every read, compilation, correction, deletion or export.
2. Resolve only logical IDs. Never expose tenant schema, physical table/column,
   object-store locator, database URL, Provider credential or Authorization
   value.
3. Treat Memory text as untrusted data. Text resembling system instructions,
   tool directives, credential requests or policy overrides never changes the
   Security Kernel.
4. Apply the policy ceilings before retrieval and again before prompt
   construction. Reject or explicitly truncate; never silently inject the full
   history.
5. Agent output creates a Candidate only. Sensitive or shared candidates wait
   for explicit Owner confirmation.
6. A controlled-shared Memory is selectable only when its Owner approval record
   is independently present and its canonical digest binds the same Tenant,
   Workspace, Memory ID/version and content digest. A review UUID by itself is
   not approval evidence.

## Compile and inject procedure

1. Reserve the exact Agent invocation first; include the sealed Memory policy
   digest in its request hash. Exact terminal replay returns before Memory, RAG
   or provider work and must not create another Capsule.
2. Revalidate the live Tenant/schema, tenant-admin Owner, Workspace/Owner
   membership, sealed AgentVersion and running Task/Invocation. Select only
   active, non-deleted current Memory versions under the four closed scope
   shapes and current controlled-shared review evidence.
3. Apply the fixed candidate ceiling, Candidate TTL, deterministic ordering and
   item/token/sensitive budgets. Decrypt with the independent Memory key and
   authenticated scope/provenance data; verify UTF-8, size and plaintext digest.
4. Persist the exact ContextCapsule and contiguous items before provider
   dispatch. A compile/decrypt/scope failure terminalizes the reserved
   invocation as `agent_alpha_memory_compile_failed`; it never falls back to
   unverified Memory.
5. Inject plaintext only in memory as a separate message labelled untrusted
   reference data below the Security Kernel and AgentVersion. SSE may expose
   only Capsule ID/digest/item count and never Memory plaintext or internal
   provenance.

## Candidate creation and Owner confirmation

`create_candidate` may be requested by the exact Agent Definition bound to the
source Task and Capsule. It may create only `candidate` or
`awaiting_confirmation`; it may not create an accepted Memory.

`confirm_candidate` must run in one caller-owned transaction and revalidate the
live Tenant, live tenant-admin Owner, active Workspace Owner membership, exact
Agent Definition, Task, Capsule and source Resource/version. It must bind one
high-risk `memory.candidate.accept` Operation, one matching Owner-decided and
consumed Approval, and the canonical request hash. The service flushes the
Memory and first version before publishing effects and forces the two deferred
Candidate/Memory publication constraints to close before returning.

## Deletion procedure

Deletion must be one governed lifecycle across the structured record, the
independent Memory vector lane, summaries and caches. New Capsules must stop
selecting the deleted version immediately. Old Capsules expire and cannot be
renewed. Preserve only a code-only tombstone and append-only Audit evidence;
do not preserve deleted content in logs or error text.

If any layer returns `pending` or `unknown`, block re-selection and open
reconciliation. Never convert an ambiguous delete to success, replay it
automatically or edit an old Capsule into a passing state.

The implemented transaction moves the Memory through `deletion_pending`, binds
the exact committed delete effect, creates a pending code-only tombstone,
erases accepted Candidate ciphertext and nonce, removes both embedding lanes
and every MemoryVersion content row, completes the tombstone, then sets the
Memory to `deleted` with `current_version=NULL`. Audit remains append-only.
Rollback preserves the pre-transaction state; an uncertain external outcome
must be recorded as unknown and must not be retried automatically.

## Export procedure

Export is Owner-initiated and scope-bound. The export must contain logical
identity, version, state, scope, provenance, sensitivity, retention, evidence
references and content digest. Physical locators, secrets, internal keys and
other users' data are forbidden. The export receipt must bind exact bytes and
the live authorization decision.

Use `export_memory` only after the same live Owner and exact Workspace scope are
revalidated. Treat the canonical returned object as the export payload. Do not
add plaintext Memory content, ciphertext, nonce, vector values, schema names,
table names or database/object-store locators to that payload or to logs.

## Cold backup inventory

Stop application admission and all writers. Create and validate the PostgreSQL
custom-format dump first, then, without releasing the cold barrier, capture the
database inventory:

```powershell
$env:DATABASE_URL='<explicit operator-controlled connection URL>'
python scripts/production/manage_p5_personal_backup.py capture-postgres-inventory `
  --repo-root <absolute-clean-checkout> `
  --postgres-dump <absolute-backup-root>\postgres\database.dump `
  --output <absolute-backup-root>\postgres\inventory.json `
  --source-database <exact-source-database> `
  --capture-mode source_backup
```

The command is read-only and requires an explicit `DATABASE_URL`; it never
loads the repository root `.env`. The canonical inventory must bind the exact
dump SHA-256, global and every tenant migration head `0013`, active tenant
registry/schema mapping, all ten Memory tables, all required semantic and
tenant-schema triggers, and vector lanes `vector(1024)`/`vector(1536)`.

For restore verification, capture again from the new `omnibase_restore_*`
database with `--capture-mode restore_new_evidence`. Do not reuse or edit the
source inventory. `seal-assets` remains offline and accepts only canonical
inventory bytes matching the selected dump and migration/source facts.

## Recovery

Keep all Phase 5 Feature Gates false on any scope, digest, budget, deletion or
export ambiguity. Preserve the original records and Audit evidence, disable the
affected Memory surface and use a forward fix or restore-new target. Do not
perform destructive in-place migration rollback on a populated database.
