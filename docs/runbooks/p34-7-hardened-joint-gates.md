# P34.7 hardened production joint gate

This run-scoped validator is an offline evidence-authenticity boundary for the
P34.7 hardened joint gate.  It deliberately never derives a production `passed`
result from operator-authored inline assertions, from hash-consistent sidecars,
or from a public key shipped inside the evidence bundle.  Every proof must be a
real, regular, non-link file whose raw bytes are canonical JSON, are
cross-bound by sidecar manifests, and are covered by a detached Ed25519
signature that verifies against a producer public key taken from an externally
configured trust policy.

## Trust model

The trust policy (`deployment/production/p34-7-trust-policy.example.json` is a
shape example) is the only trust anchor.  It must be installed by the gate
operator **outside** the evidence run directory and must contain:

- the allowlisted Ed25519 public key of every producer (`core`, `runner`,
  `broker`, `gateway`, `overlay`, `recovery_sla`, `sealer`);
- a source seal (repository, Git object format, plus approved commit/tree
  OIDs);
- an approved artifact manifest binding each executable path to its SHA-256 and
  the joint boundaries it may serve;
- exact argv templates for the six required boundaries;
- an environment-name allowlist (secret names are always rejected);
- gateway certificate pins (issuer, SAN suffix, bounded lifetime);
- a bounded `max_evidence_age_seconds` that caps how old signed evidence may
  be.

A policy is only a trust anchor when its raw bytes hash to a digest pinned in
`joint_gate._APPROVED_TRUST_POLICY_SHA256`.  That set is currently **empty**:
no trust policy has been independently approved, so every bundle — including a
fully self-signed one — remains `blocked/not_proven`.  Approving a policy is an
audited code change, the same way a CA root is pinned.

The seven producer roles (`core`, `runner`, `broker`, `gateway`, `overlay`,
`recovery_sla`, `sealer`) must have **seven distinct** Ed25519 public keys; at
least the sealer must differ from every producer.  Any duplicate key fails
closed at policy parse time (`invalid/veto`).

## Two operating modes

1. `--validate-only` parses the static contract and an operator-supplied bundle
   layout but never accepts inline evidence as direct execution proof.  It
   always returns `blocked/not_proven`.
2. `--verify-evidence <run-dir> --trust-policy <policy>` may return `passed`
   only when the policy is approved, every detached signature verifies against
   a policy producer key, every canonical component schema parses and
   cross-binds, and every safety item is proven.

If the current host cannot produce the full real evidence chain, that is
expected: stop at `blocked/not_proven`.  Do not manufacture a `passed` result
to satisfy the task.

## Fail-closed verification

The verifier fails closed on every forgery vector:

- structural violations (unknown fields, missing files, symlinks/junctions/
  reparse points on every path component, traversal, absolute escape,
  non-canonical JSON bytes, hash/size drift, duplicate IDs, schema/version
  mismatch, non-UTC timestamps, chronology/order inconsistency, secret env
  names, nonzero exit codes, stale seal bindings) are `invalid/veto`;
- authenticity and safety gaps (missing/unverifiable detached signatures,
  bundle-supplied trust roots, swapped producer keys, duplicate producer keys,
  cross-run or cross-component replay, stale/revoked/replayed/future gateway
  credentials, unapproved trust policy, source commit/tree outside the
  approved seal, unapproved executables, argv outside the command templates,
  unmeasured posture, attack or cleanup evidence that is unsigned or does not
  cross-check the inventory, root-env/business-DB posture not measured) are
  `blocked/not_proven` blockers.

Every safety item reported as `not_proven` is added to `blockers`; a pass
requires zero blockers.  In particular `runtime_posture.measured=false` (or an
unsigned posture measurement) can never pass.

### Executables are bound three ways

Every command receipt's `executable` must satisfy a three-way digest equality:

1. the **actual file bytes** under the run directory are read and SHA-256'd by
   the verifier — a receipt may not declare a digest that differs from the
   file on disk;
2. the receipt-declared digest must equal the digest pinned in the approved
   trust policy for that executable path and boundary;
3. every executable must appear in the **approved artifact manifest**, whose
   `path`/`size`/`sha256` entries are themselves checked against the real
   bytes (`_verify_manifest`).

An executable that exists only in a receipt or policy declaration (but not in
the artifact manifest), or whose on-disk bytes drift from the signed receipt
digest, is `artifact_provenance = not_proven` and blocks.

### Evidence seal binds the full posture

The `evidence_seal` canonical binding covers `schema`/`schema_version`,
`environment`, `disposable`, the full provenance (`repository`,
`git_object_format`, `source_commit`, `source_tree`, `dirty`), the complete
evidence validity window (`run_started_at`, `run_completed_at`,
`evidence_issued_at`, `evidence_valid_until`), the source/artifact manifest
digests, the command/component/posture/attack/cleanup digest chain, migration
head, feature gates and **every current top-level security posture** derived
from the verified chain (`signature_authenticity`, `artifact_provenance`,
`command_semantics`, `certificate_posture`, `replay_posture`,
`runtime_posture`, `production_runtime_inactive`, `hostile_code_not_executed`,
`root_env_not_accessed`, `business_database_not_accessed`,
`business_database_not_migrated`, `attack_results`, `cleanup_complete`).
The posture sealed by the binding is derived at the **issue-time clock**
(`window.issued_at`), never at the verification `now`: the recorded binding is
a pure function of the evidence, so re-verifying the same bundle later (or
with an expired/future clock) can never invalidate the seal itself — an
expired bundle keeps its valid seal and fails on `evidence_freshness` instead.
Because the verifier recomputes this binding from the verified data, ANY
outer-field rewrite (environment `staging`->`production`, `disposable`
`true`->`false`, `dirty` `true`->`false`, a validity-window field, the Git
object format, or any posture-relevant drift) changes the recomputed bytes and
fails the recorded binding digest / detached signature.
`joint_gate.compute_seal_binding()` is the single canonical builder shared by
the verifier, the adversarial forger and the tests.

### Gateway certificate window

The gateway certificate must satisfy `valid_from <= now < valid_until`; a
future certificate (`valid_from` in the future) is rejected exactly like an
expired one.  The expiry boundary is strict: `valid_until == now` is **already
expired** and fails closed (`valid_until <= now` is rejected); `valid_from ==
now` is an allowed first-valid instant.  The issuer, SAN suffix, maximum
lifetime, revocation and replay checks remain mandatory.

### Git object format

`provenance.git_object_format`, the trust-policy `source_seal.git_object_format`
and every component evidence `git_object_format` must all declare the same
member of the closed set `sha1 | sha256`:

- `sha1` accepts exactly **40 lowercase hex** characters (the current
  repository's real format — `git rev-parse --show-object-format` reports
  `sha1`), `sha256` exactly **64 lowercase hex**;
- commit/tree values are preserved as the original Git OIDs — never re-hashed
  or transformed;
- source/artifact manifests keep using raw-byte SHA-256 (not weakened);
- unknown formats, wrong lengths, uppercase/non-hex characters and any
  policy/evidence/component/seal format drift all fail closed
  (`invalid/veto`).

### Evidence freshness window

Every bundle carries a frozen validity window: `run_started_at`,
`run_completed_at`, `evidence_issued_at` and `evidence_valid_until`, with the
structural invariant `run_started_at <= run_completed_at <= evidence_issued_at
< evidence_valid_until`:

- every command receipt and every posture/attack/cleanup timestamp must lie
  inside `[run_started_at, run_completed_at]` (cross-window receipts veto);
- `now` must satisfy `evidence_issued_at <= now < evidence_valid_until`;
- the evidence age (`now - evidence_issued_at`) and the window length
  (`evidence_valid_until - evidence_issued_at`) must not exceed the trust
  policy's bounded `max_evidence_age_seconds`;
- stale/expired bundles, bundles with a future `issued_at`, and window-length
  overruns are rejected; a bundle whose outer time fields are rewritten
  without re-signing fails the seal binding;
- the wall clock is read exactly **once** per verification through the
  `now` clock seam of `verify_joint_evidence`; the same unexpired bundle can be
  idempotently re-verified offline, but an expired bundle can never be
  re-PASSed (`evidence_freshness` becomes a blocker).

## Evidence chain (schema v2 only)

Each run is an immutable directory containing:

- `source.txt` / `artifact.txt` plus `source_manifest`/`artifact_manifest`
  with raw-byte SHA-256, size and a canonical `raw_sha256` binding;
- a frozen validity window in the envelope: `run_started_at`,
  `run_completed_at`, `evidence_issued_at`, `evidence_valid_until`;
- `receipts/<boundary>.json` — canonical command receipts (schema
  `omnibase.p34-7.command-receipt.v1`) signed by the boundary owner's key and
  cross-bound to the policy executable/argv templates and the run window;
- `components/<component>.json` — canonical component evidence (schema
  `omnibase.p34-7.component-evidence.v1`) signed by the component key and
  cross-binding run id, producer, Git object format, source commit/tree,
  source/artifact manifest digests, component identity, peer identities, owned
  receipt digests, executables, the posture measurement digest and the
  attack/cleanup result digests;
- `measurements/posture.json` — the signed posture measurement (schema
  `omnibase.p34-7.posture-measurement.v1`) produced by `core`, proving
  production Runtime inactive, no hostile code, root `.env` and business
  database not accessed/migrated;
- `attack/attack-matrix.json` — signed attack results (schema
  `omnibase.p34-7.attack-matrix.v1`) produced by `runner`, cross-checked
  against the per-attack inventory;
- `cleanup/cleanup-inventory.json` — signed cleanup inventory (schema
  `omnibase.p34-7.cleanup-inventory.v1`) produced by `sealer`, counts
  cross-checked against the inventory and all zero;
- `evidence_seal` — a binding digest over the whole verified chain (including
  schema_version/environment/disposable/full provenance and the complete
  current security posture) signed by the `sealer` key (schema
  `omnibase.p34-7.evidence-seal.v1`);
- `signatures/**` — 64-byte raw Ed25519 signatures over the canonical raw bytes
  of each evidence file;
- `bin/<boundary>` — the actual executable files, bound by every receipt's
  executable digest AND by the artifact manifest (path/size/sha256); the
  verifier hashes the real bytes on disk.

Inline attack status/counts, inline `secret_free` flags, inline exit
codes/argv/timestamps or inline `evidence_seal.status` are not part of the
schema: the signed evidence files are the only source of these facts.

Use:

```text
python scripts/production/validate_p34_7_joint_gate.py \
  --verify-evidence <run-dir> --evidence <run-dir>/evidence.json \
  --trust-policy <operator-installed-policy.json> \
  --output <run-dir>/report.json
python scripts/production/validate_p34_7_joint_gate.py \
  --validate-only --evidence <contract.json>
```

## Adversarial negative-proof tool

`scripts/production/forge_p34_7_evidence_bundle.py` fabricates a complete
bundle from scratch — every file, sidecar, cross-binding and matching hash —
with no real execution behind it.  It is the review negative proof: the joint
gate must report `blocked/not_proven` for the unsigned bundle, for forged
signature bytes, for bundle-supplied trust roots, for swapped producer keys,
for cross-run/cross-component replay, for stale certificates, for modified raw
bytes and for missing safety evidence.  It never receives `passed`.

## Post-approval positive control and attack matrix

The test suite contains exactly one TRUE positive control
(`test_positive_control_signed_chain_passes_after_policy_approval`): with the
trust policy approved **in-process only** (the test monkeypatches
`_APPROVED_TRUST_POLICY_SHA256` with the test policy digest and restores it at
teardown — the production approved set stays empty), a fully signed,
artifact-manifest-bound, seal-consistent chain reaches `passed`.  Around it,
post-approval attack tests prove that every single drift flips the result to
`passed=false` or `invalid/veto`:

- replacing the actual `bin/core_runner` bytes without changing the receipt;
- an executable absent from the artifact manifest;
- environment `staging`->`production`, `disposable` `true`->`false` and
  `dirty` `true`->`false` rewrites without any key rewrite (both the envelope
  veto and the seal-binding veto are asserted);
- all seven roles sharing one Ed25519 key, and the sealer sharing a key with a
  producer (both fail closed at policy parse);
- a gateway certificate whose `valid_from` is in the future, and the exact
  expiry boundary (`valid_until == now` fails closed, `valid_from == now` is
  allowed);
- any drift among the executable/manifest/receipt three-way digests.

The Review-Fix Round 2 matrix additionally proves:

- the current repository is SHA-1 (`git rev-parse --show-object-format`) and
  REAL 40-hex `HEAD` / `HEAD^{tree}` OIDs enter the envelope, the policy source
  seal and the signed chain (still `blocked/not_proven` while the approved set
  is empty);
- `sha1`+64-hex, `sha256`+40-hex, unknown formats, uppercase OIDs and
  policy/component/seal object-format drift all fail closed;
- expired bundles, future `issued_at`, age over the policy maximum,
  cross-window receipts, over-long validity windows, outer time-field rewrites
  without re-signing and policy max-age drift are all rejected;
- idempotent offline re-verification of the same unexpired bundle, and the
  guarantee that an expired bundle is never re-PASSed.

## Status

The checked-in production composition and Overlay examples remain fail-closed
and `blocked/not_proven`.  This validator does not activate services, access a
business database, read the root `.env`, execute hostile code, or turn
disposable evidence into production proof.  No approved trust policy and no
production evidence run directory exist in the repository; therefore P34.7
cannot report `passed` until an independently installed and approved trust
policy plus a fully signed real evidence chain exist.

## Sealed-input drift note

`docs/maintainers/maintenance-map.json` is a sealed source-file input to the
P5.4B disposable-gate evidence (`docs/evidence/p5-2/phase5-agent-alpha-engineering-gate.json`)
and a tracked pathspec in the P5.1A/P5.2A/P5.3A formal contracts.  Any change
to the maintainer map invalidates the recorded P5.4B source manifest and can
only be re-sealed by re-running the P5.4B disposable gate in a guarded
`omnibase_test_*` sentinel environment; the recorded real-run evidence is
preserved as sealed history and is not overwritten by hand-computed digests.
The joint-gate test `test_modified_raw_bytes_are_rejected` proves that a
drifted sealed source manifest is rejected (fail-closed) rather than silently
accepted.
