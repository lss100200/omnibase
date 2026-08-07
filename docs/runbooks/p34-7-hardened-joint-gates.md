# P34.7 hardened production joint gate

This run-scoped validator is an offline evidence-authenticity boundary for the
P34.7 hardened joint gate.  It deliberately never derives a production `passed`
result from operator-authored inline assertions: every proof must be a real,
regular, non-link file inside an immutable run directory whose raw bytes are
hashed and cross-bound by sidecar manifests.

## Two operating modes

The validator exposes two mutually exclusive modes that are never blurred:

1. `--validate-only` parses the static contract and an operator-supplied bundle
   layout but never accepts inline evidence as direct execution proof.  It
   always returns `blocked/not_proven` because direct evidence was not
   executed.
2. `--verify-evidence <run-dir>` may return `passed` only when every mandatory
   real, sealed, component-specific artifact exists under `<run-dir>` and all
   cross-component identities, hashes, chronology, semantics, attack results
   and cleanup checks verify against the actual file bytes.

If the current host cannot produce the full real evidence chain, that is
expected: stop at `blocked/not_proven`.  Do not manufacture a `passed` result to
satisfy the task.

## Fail-closed verification

The verifier fails closed on every forgery vector, including: unknown schema
fields, missing files, symlinks/junctions/reparse points, duplicate IDs, path
traversal, absolute-path escape, non-canonical paths, mutable references,
raw-byte hash or size mismatch, manifest `raw_sha256` not binding the file list,
unsupported schema version, ambiguous or non-UTC timestamps, command-order
inconsistency, executable/stdout/stderr digest drift, per-component identity
mismatch, run-id rebinding, certificate stale/revoked/replayed posture, missing
or failed attack results, cleanup residue, and `evidence_seal` provenance that
does not match the envelope.

Safety claims (`root_env_accessed`, `business_database_accessed`,
`business_database_migrated`, `production_runtime_activated`,
`hostile_code_executed`, cleanup residue) are never hardcoded into a `passed`
result.  They are reported as `not_proven` unless an approved sealed
measurement proves them, and `not_proven` blocks `passed`.

## Immutable run-scoped bundle

Each operator run must use a unique non-overwriting directory containing the
raw source/artifact files plus the per-component evidence files named by the
manifests.  The evidence JSON must use schema
`omnibase.p34-7.hardened-joint-evidence.v2` and bind:

- `run_id`, `schema_version`, `environment=production`, `disposable=false`;
- `provenance.source_commit/source_tree` (clean GitHub checkout),
  `provenance.dirty=false`;
- `source_manifest`/`artifact_manifest` with raw-byte SHA-256 and size per file
  plus a `raw_sha256` binding the canonical file list;
- `commands` for the six required joint boundaries (`core_runner`,
  `runner_broker`, `runner_gateway`, `broker_gateway`, `overlay_data_plane`,
  `recovery_sla`), each binding executable digest/path, argv, working
  directory, secret-free env-name manifest, monotonic start/end timestamps,
  bounded timeout, exit code, and stdout/stderr file digest+size;
- `components` for the six joint gates (`core`, `runner`, `broker`, `gateway`,
  `overlay`, `recovery_sla`), each with a frozen per-component schema, producer
  identity, the same `component_run_id`, sha256 identity, trust roots, the
  gateway component additionally asserting certificate posture (public
  fingerprint, issuer, SAN, validity window, not-revoked) and replay posture,
  plus a host OS/kernel/arch record and a raw evidence file;
- `migration_head=0012`, the three Phase 5 feature gates all `false`, and a
  measured (not hardcoded) `runtime_posture`;
- `attack_matrix` with `passed` status, the required negative attack outcomes
  (`node_compromise`, `credential_theft`, `revocation_replay`, `derp_failover`,
  `cross_component_replay`) and a raw evidence file;
- `cleanup` with zero containers/networks/processes/volumes/databases/test
  identities and a raw evidence file;
- `evidence_seal` matching the envelope `run_id` and provenance.

Use:

```text
python scripts/production/validate_p34_7_joint_gate.py \
  --verify-evidence <run-dir> --evidence <run-dir>/evidence.json \
  --output <run-dir>/report.json
python scripts/production/validate_p34_7_joint_gate.py \
  --validate-only --evidence <contract.json>
```

## Status

The checked-in production composition and Overlay examples remain fail-closed
and `blocked/not_proven`.  This validator does not activate services, access a
business database, read the root `.env`, execute hostile code, or turn
disposable evidence into production proof.  No production evidence run
directory exists in the repository; therefore P34.7 cannot report `passed`
without a directly executed and fully verified real evidence chain.

## Sealed-input drift note

`docs/maintainers/maintenance-map.json` is a sealed source-file input to the
P5.4B disposable-gate evidence (`docs/evidence/p5-2/phase5-agent-alpha-engineering-gate.json`)
and a tracked pathspec in the P5.1A/P5.2A formal contracts.  Any change to the
maintainer map invalidates the recorded P5.4B source manifest and can only be
re-sealed by re-running the P5.4B disposable gate in a guarded
`omnibase_test_*` sentinel environment; the recorded real-run evidence is
preserved as sealed history and is not overwritten by hand-computed digests.
The joint-gate contract test `test_sealed_source_manifest_drift_is_detected`
proves that a drifted sealed source manifest is rejected (fail-closed) rather
than silently accepted.
