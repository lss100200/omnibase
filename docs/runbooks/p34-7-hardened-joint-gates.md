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
- a source seal (repository plus approved commit/tree digests);
- an approved artifact manifest binding each executable path to its SHA-256 and
  the joint boundaries it may serve;
- exact argv templates for the six required boundaries;
- an environment-name allowlist (secret names are always rejected);
- gateway certificate pins (issuer, SAN suffix, bounded lifetime).

A policy is only a trust anchor when its raw bytes hash to a digest pinned in
`joint_gate._APPROVED_TRUST_POLICY_SHA256`.  That set is currently **empty**:
no trust policy has been independently approved, so every bundle — including a
fully self-signed one — remains `blocked/not_proven`.  Approving a policy is an
audited code change, the same way a CA root is pinned.

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
  bundle-supplied trust roots, swapped producer keys, cross-run or
  cross-component replay, stale/revoked/replayed gateway credentials,
  unapproved trust policy, source commit/tree outside the approved seal,
  unapproved executables, argv outside the command templates, unmeasured
  posture, attack or cleanup evidence that is unsigned or does not cross-check
  the inventory, root-env/business-DB posture not measured) are
  `blocked/not_proven` blockers.

Every safety item reported as `not_proven` is added to `blockers`; a pass
requires zero blockers.  In particular `runtime_posture.measured=false` (or an
unsigned posture measurement) can never pass.

## Evidence chain (schema v2 only)

Each run is an immutable directory containing:

- `source.txt` / `artifact.txt` plus `source_manifest`/`artifact_manifest`
  with raw-byte SHA-256, size and a canonical `raw_sha256` binding;
- `receipts/<boundary>.json` — canonical command receipts (schema
  `omnibase.p34-7.command-receipt.v1`) signed by the boundary owner's key and
  cross-bound to the policy executable/argv templates;
- `components/<component>.json` — canonical component evidence (schema
  `omnibase.p34-7.component-evidence.v1`) signed by the component key and
  cross-binding run id, producer, source commit/tree, source/artifact manifest
  digests, component identity, peer identities, owned receipt digests,
  executables, the posture measurement digest and the attack/cleanup result
  digests;
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
- `evidence_seal` — a binding digest over the whole verified chain signed by
  the `sealer` key (schema `omnibase.p34-7.evidence-seal.v1`);
- `signatures/**` — 64-byte raw Ed25519 signatures over the canonical raw bytes
  of each evidence file.

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
