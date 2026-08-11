# Memory Privacy, Delete and Export Runbook

## Current authority

P5.5A is contract-only. There is currently no Memory database, Browser Memory
API, Memory vector index, compiler worker or production injection path. Do not
interpret the example Capsule or Candidate as persisted user data.

## Privacy rules for later increments

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

## Deletion contract for P5.5B

Deletion must be one governed lifecycle across the structured record, the
independent Memory vector lane, summaries and caches. New Capsules must stop
selecting the deleted version immediately. Old Capsules expire and cannot be
renewed. Preserve only a code-only tombstone and append-only Audit evidence;
do not preserve deleted content in logs or error text.

If any layer returns `pending` or `unknown`, block re-selection and open
reconciliation. Never convert an ambiguous delete to success, replay it
automatically or edit an old Capsule into a passing state.

## Export contract for P5.5B

Export is Owner-initiated and scope-bound. The export must contain logical
identity, version, state, scope, provenance, sensitivity, retention, evidence
references and content digest. Physical locators, secrets, internal keys and
other users' data are forbidden. The export receipt must bind exact bytes and
the live authorization decision.

## Recovery

Keep all Phase 5 Feature Gates false on any scope, digest, budget, deletion or
export ambiguity. Preserve the original records and Audit evidence, disable the
affected Memory surface and use a forward fix or restore-new target. Do not
perform destructive in-place migration rollback on a populated database.
