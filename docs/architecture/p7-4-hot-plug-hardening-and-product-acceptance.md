# P7.4 Hot-Plug Hardening and Product Acceptance

Status: authorized hardening and certification lane; production release is not approved.

P7.4 certifies the complete P7.3 Workspace component platform. It may correct a
security defect discovered by certification, expand compatibility, exercise
scale and failure behavior, and improve the Settings experience. It may not be
used to defer or first introduce a registry, component family, lifecycle
transition, permission, revocation, adapter or recovery path required by P7.3.

## 1. Preserved product boundaries

The five component families continue to use one immutable registry and one
Workspace-scoped lifecycle. The renderer remains proposal-only and never gains
package paths, commands, process handles, grants, secrets, native control tokens
or physical database/file locators. Every external effect remains a durable
`begin -> source-owned adapter -> settle` operation. `pending`, `unknown` and
ambiguous effects require explicit reconciliation and are never dispatched
again automatically.

P7.4 does not activate enterprise Planner/Multi-Agent, paid Provider acceptance,
arbitrary executable Skills, arbitrary MCP transports, host virtualization
repair, Authenticode custody, Marketplace authority or production release.
Those claims require their own evidence and authority.

The source-owned Sandbox compatibility journey is exact-package execution, not
an external isolation-provider claim. A Sandbox bundle contains one
inventory-bound `payload/workload.wasm`; registry, broker and helper revalidate
and execute those exact bytes with zero imports and the single declared
`transform` export. Evidence binds workload SHA-256 and transform output across
1.0/1.1, upgrade and rollback. The helper remains a trusted same-host process.
Until a separately controlled P34 provider supplies independent attestation and
resource/kill identities, independent P34 isolation remains `not_proven`.

## 2. Network fencing hardening

Desktop schema v12 adds a durable Network Lease fencing cursor keyed by the
exact Workspace, installation and logical service. Workload fencing and Network
fencing remain independent.

- A fresh cursor issues token `1`; every later activation or recovery for the
  same key issues the prior cursor's `next_fencing_token` and advances it in the
  same `BEGIN IMMEDIATE` transaction as the new Lease.
- Runtime allocation never scans Lease history or derives a Network token from
  a binding generation or workload token.
- The v11-to-v12 migration seeds each cursor above every already issued token.
  It preserves Lease rows and fails closed if the old database contains more
  than one active Lease for the same cursor key.
- Cursor identity is immutable, deletion is forbidden and the only legal update
  is one monotonic allocation step. A failed transaction cannot consume a token.
- Begin, idempotent replay and settlement revalidate the active Lease against
  the cursor's current token. A stale Lease cannot authorize or settle work.

## 3. Compatibility gate

The source gate covers Windows Node 20 and 24, the repository-locked Python and
pnpm environments, fresh schema v12 and every supported v1-v11 upgrade. The
Windows product gate records OS build, architecture, integrity level, display
DPI, viewport, package hashes and runtime manifest hash. Unsupported
prerequisites remain honest `unavailable` states.

No compatibility result may be generalized to an OS, architecture, integrity
level or display configuration that was not actually exercised.

## 4. Scale and latency gate

The deterministic hardening matrix uses a fixed seed and must cover at least:

- 500 catalog components and 1,500 immutable versions;
- 20 Workspaces with 100 installation projections each;
- dependency and conflict graphs at the closed manifest limits, including a
  32-level acyclic chain and rejected cycle/self/duplicate/conflict cases;
- repeated snapshot, proposal and begin/settle operations with p95 reported
  from raw samples rather than rounded summaries.

On the pinned CI/acceptance class, the target ceilings are snapshot p95 <= 250
ms, proposal mutation p95 <= 100 ms and local begin/settle p95 <= 50 ms. A run
outside those ceilings is evidence, not a pass; thresholds may only change in a
reviewed product-law revision with the raw old and new results retained.

## 5. Soak, concurrency and attack gate

Pull requests run a bounded deterministic cycle campaign. Nightly and release
candidate receipts distinguish 8-hour and 24-hour campaigns and cannot be
inferred from the shorter gate. A passing campaign has zero automatically
replayed effects, zero unowned child processes/listeners, zero cross-Workspace
projections and no unexplained post-warm-up memory growth above 10% or 128 MiB,
whichever is larger.

The attack matrix covers malformed and non-canonical manifests, package digest
drift, traversal/ADS/reparse/hardlink/TOCTOU attempts, dependency cycles and
conflicts, stale revisions, double decisions, duplicate operation identities,
cross-Workspace identifiers, grant/revocation/expiry/budget drift, workload and
Network fencing drift, concurrent lifecycle/invocation actions, adapter crash,
timeout, malformed receipt, workload-byte/export/import/inventory drift,
partial activation and every durable crash cutpoint.

## 6. Migration, backup and recovery gate

Fresh v12, all v1-v11 upgrades and a v11 database containing historical and
active Network Leases must pass foreign-key and integrity checks without
rewriting old evidence. Truncated, corrupted, foreign application-id and future
schema databases fail closed. Backup restore always targets a new controlled
copy. Startup fences old authority before any reconstruction, preserves
unknown effects and requires exact native recovery settlement.

## 7. Settings and accessibility gate

Catalog, Installed, Slots, Skills, MCP, Sandbox, Local Adapters, Permissions,
Health, Review, Audit and Recovery remain real data-backed views. Each has
bounded loading, empty, unavailable, error and disabled states. Dense mode must
reduce explanatory copy without hiding authority, risk, Diff or recovery facts.

Automated rendering covers keyboard-only traversal, visible focus, focus
restore, Escape behavior, deterministic tab order, reduced motion, high
contrast, 200% zoom, 1024x700 and 1440x900 layouts. Critical or serious
accessibility violations, clipped controls, overlapping text and placeholder
success states fail the gate. Human visual review remains a separate claim.

## 8. Windows product acceptance

One fresh package from the final clean reviewed commit enters exactly one fresh
disposable Windows target. The run covers Owner review plus real positive and
negative journeys for all five families, P7.1 read-only regression, Workspace
switch isolation, emergency stop, crash/restart recovery, upgrade/rollback,
graceful close, uninstall and retained application data. The receipt binds raw
transcripts, screenshots, source commit, EXE/MSI/runtime manifest/component
package hashes and every relevant command exit code.

Sandbox evidence may prove engineering behavior only. Authenticode key custody,
trusted timestamp, Publisher identity, Marketplace control and production
release authorization require independent external evidence. Until then
`authenticode_verified`, `marketplace_verified`, `production_ready` and
`release_authorized` remain false.

## 9. Acceptance decision

`P7_4_ENGINEERING_EVIDENCE_READY_FOR_REVIEW` requires every automated source,
migration, scale, attack and bounded-soak gate plus one controlled Windows
receipt. `P7_4_ENGINEERING_ACCEPTED` additionally requires Codex/Owner review of
the bound evidence. Neither state is a production release. Missing signing,
Publisher, Marketplace or release authority remains `not_proven`, never waived
or inferred.

## 10. Failure recovery

Forward-fix the source and create a new evidence run. Preserve prior packages,
receipts, cursor state, Lease rows, operation/effect journals and audit records.
Do not reset a fencing cursor, delete evidence, reuse a stale token, replay an
unknown effect, weaken a threshold after failure, repair host virtualization or
publish an unsigned engineering package as a product release.
