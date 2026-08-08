# P34.7 production readiness decision

Date: 2026-08-02

Decision:

```text
P34.7 implementation/contracts/local gates: COMPLETE (integrated on latest main)
P34.7 production total Gate: BLOCKED / NOT_PROVEN
production activation: DISABLED
P5 engineering/product Lite path: IMPLEMENTED / on main (engineering-only, no_tool-only)
P5 production Runtime: DISABLED / blocked/not_proven
P34.7 Hardened/high-risk Runtime: BLOCKED / NOT_PROVEN
Planner production activation: DISABLED
Multi-Agent: DISABLED
```

This decision deliberately separates implemented, locally reproducible
engineering controls from evidence that can only be produced in the target
production environment. A valid static contract, unit test, disposable
provider, Docker container, WSL instance, test double, port probe or historical
artifact is not a substitute for the missing production evidence.

## Implemented admission surfaces

- Clean-checkout Git/source/evidence provenance and three-state production
  admission (`ready`, `blocked/not_proven`, `invalid/veto`).
- Four separate Core, Runner, Broker and Gateway identities with fixed
  mTLS/private-AF_UNIX channels and role-specific credential allowlists.
- Provider plan/grant/quota/receipt binding, append-only effect journal,
  committed-marker visibility, copy-on-publish, snapshot capture,
  restore-new-identity and `pending|unknown` no-auto-replay.
- Non-disposable tenant/RAG data-owner admission contract.
- Real-member Overlay/DERP/node-compromise evidence contract, dual independent
  Ed25519 attestations, fault-injection observations and SLA aggregation.
- Browser Workspace control-plane pages without WorkspaceData private-write.
- Python and TypeScript logical Gateway helpers for Artifact and Derived RAG.
- Maintainer map module `production-readiness` and invariants INV-035–INV-038.

## Local verification

- P34.7 focused Backend: `39 passed`.
- Backend non-integration: `1160 passed, 14 skipped, 14 deselected`.
- Backend plus Python SDK Mypy: `155 source files, 0 issues`.
- Provider-focused + Python SDK + OpenAPI: `28 passed`.
- TypeScript SDK: `8 passed`; typecheck PASS.
- Frontend: `44 passed`; typecheck PASS; lint PASS; production build PASS,
  including `/spaces` and `/spaces/[workspaceId]`.
- Changed Python scope: Ruff check and format check PASS.
- Maintainer map: `28 invariants, 20 modules, 265 path specs, 655 matched
  files, 136 entrypoints, 14 discovered HTTP entrypoints, 82 verification
  commands`.
- Maintainer benchmark: `3 plans, 8 scenarios, 6 critical scenarios, 9 unsafe
  vetoes`.

Local untracked reports are intentionally kept under `.tmp/` and are not
release evidence by themselves:

| Gate | State | SHA-256 |
| --- | --- | --- |
| composition validate-only | `blocked/not_proven` | `68f963f33a960104595a230702a3ef175e6a50205a2568f8bf234bf93a48d0ce` |
| disposable provider reference | local PASS; production adapter denied | `fb6598b8653cad436362ccb2190cf6eb346ef2ac6f1d3331927976c3cfb8a641` |
| Overlay validate-only | `blocked/not_proven` | `db978b125f26d1582e6839fb7da8e1c12219c037230170cf262506722b28c907` |

The checked-in composition configuration digest is:

```text
8fc647b27fa13464c5f3153e7c621036b25547d472db139ccf601c1edc9a5d79
```

## Production blockers

All of the following remain required and `not_proven`:

1. Current-source target Linux Runner attack matrix, exactly 12/12.
2. Production Core→Runner mTLS roundtrip.
3. Production Runner→Broker private identity roundtrip.
4. Production Runner→Gateway non-disposable mTLS roundtrip.
5. Production Broker→Gateway non-disposable mTLS roundtrip.
6. Real provider-backed Workspace Artifact/Derived/Promotion/Snapshot/Restore
   rehearsal with reconciliation evidence.
7. Data-owner-authorized non-disposable tenant/RAG smoke.
8. Two real independent Linux members, independent production Node Daemons and
   independent DERP.
9. Forced DERP with direct path disabled, node revoke, stolen-credential
   rejection, stale Lease/fencing rejection, new-identity rejoin and cleanup.
10. Dual independent member signatures over the same canonical evidence.
11. Capacity, concurrency, fault-injection and SLA observations meeting the
    checked-in policy.

## Explicit negatives

- The root `.env` was not read, printed, hashed, staged or committed.
- No normal business database was accessed or migrated.
- No non-disposable tenant/RAG or real user data was accessed.
- No canonical cutover or V2 backfill was performed.
- No production Runner/Broker/Gateway/provider/Overlay activation was
  performed.
- No hostile code was executed by a development Docker/WSL environment.
- No `pending|unknown` effect was automatically replayed.
- Browser private-write remains closed.
- Agent Runtime, Planner, Executor, multi-Agent DAG, product Skill and MCP
  execution remain frozen.

## Clean-checkout verification

The formal validator was run after the implementation commit from a clean
checkout with the configured public remote:

```text
python scripts/production/validate_p34_7_composition.py \
  --verify \
  --output <operator-controlled-path>/p34-7-production-admission.json
```

Result:

```text
implementation commit: 63790b49a73927dcd0c3c67d2093edb5dec8d8e6
source tree: be394f19ce5ac741d752fb3e67dd86572b6f3907
source clean: true
source files: 123
source manifest SHA-256: 8dd165724700d7c139a8ca5044128ffd59f58b9880870d0447ca52fe77650132
report SHA-256: a6efe6c50fc452bb1f356ff001336a648186689d8a962b022ac915835e58e319
exit code: 2
state: blocked/not_proven
activation allowed: false
blockers: 10
vetoes: 0
evaluator-key files in scope: 0
root .env accessed: false
business database accessed/migrated: false/false
```

This is the required reproducible safe-refusal result for the current evidence
set. It does not unlock production or Phase 5. Re-run the validator whenever a
tracked production source byte changes or new production evidence is admitted.

## Round 2 review-fix: joint gate is trust-anchored (2026-08-07)

The external review rejected the Round 1 hash-sidecar design because the same
operator can forge files and matching hashes. The joint gate now verifies
detached Ed25519 signatures over canonical JSON evidence bytes against an
independently installed trust policy (allowlisted producer keys, source seal,
approved artifact manifest, argv templates, env allowlist, gateway certificate
pins) located outside the evidence directory. The policy bytes must match a
digest pinned in `joint_gate._APPROVED_TRUST_POLICY_SHA256`, which is empty:
no trust policy is approved, so every bundle - including a fully self-signed
one - remains `blocked/not_proven`. `scripts/production/forge_p34_7_evidence_bundle.py`
forges complete bundles to prove they can never pass. The overall P34.7
decision is unchanged: `BLOCKED / NOT_PROVEN`, production activation DISABLED.
The P5 engineering/product Lite path is implemented and on main, but it is
engineering-only; P5 production Runtime, Planner production activation and
Multi-Agent remain DISABLED / blocked/not_proven.

## Round 3 review-fix: hardened production joint gates (2026-08-07)

The external review required hardening before the joint gate can be considered
a pass-capable authenticity boundary. All ten items are implemented and
covered by tests:

1. `_verify_receipt_executable` now reads the ACTUAL executable file bytes and
   computes SHA-256; a pass requires actual digest == receipt digest ==
   approved policy digest.
2. Every executable must appear in the approved artifact manifest, whose
   path/size/sha256 entries are verified against the real bytes; executables
   may no longer exist only in receipt/policy declarations.
3. The evidence seal's canonical binding (`joint_gate.compute_seal_binding()`)
   covers schema/schema_version, environment, disposable, full provenance
   (repository/source_commit/source_tree/dirty) and all current top-level
   security posture; any outer-field rewrite fails the recorded binding
   digest / detached signature.
4. The trust policy's seven producer roles (six components + sealer) must have
   seven unique Ed25519 public keys; duplicates fail closed at policy parse.
5. Gateway certificates must satisfy `valid_from <= now < valid_until`;
   future certificates are rejected while issuer/SAN/max-lifetime/revocation/
   replay checks remain.
6. A TRUE positive control test proves the signed, manifest-bound,
   seal-consistent chain reaches `passed` when the policy digest is approved
   in-process via monkeypatch; the test digest is never committed into
   `_APPROVED_TRUST_POLICY_SHA256`, which remains empty.
7. Nine post-approval attack tests (swapped executable bytes, executable
   absent from the artifact manifest, environment/disposable/dirty outer-field
   rewrites without key rewrite, all seven roles sharing one key, sealer
   sharing a key with a producer, future `valid_from`, and
   executable/manifest/receipt three-way digest drift) all yield `passed=false`
   or `ConfigurationError`.
8. `_APPROVED_TRUST_POLICY_SHA256` remains an empty set; no real trust policy
   was approved by this round.
9. P34.7 focused tests: `65 passed, 1 skipped` (Windows symlink covered by
   reparse guards); the full maintainer-map matrix, mypy, Ruff check/format
   and map/benchmark validators pass (details in the handover report).
10. The P34.7 / Phase 5 sealed digest chain was recomputed from the final
    bytes and the ordinary forward-fix commit appended.

The overall P34.7 decision is UNCHANGED: `BLOCKED / NOT_PROVEN`, production
activation DISABLED. P5 production Runtime, Planner production activation and
Multi-Agent remain DISABLED / blocked/not_proven; the P5 engineering/product
Lite path is engineering-only and never a production activation. No fixture
received production `passed`; no trust policy was approved; the root `.env`
was not read and no business database was accessed or migrated.

## Integration R1: hardened joint gates enter the unified main line (2026-08-08)

The four frozen input commits (09cd09d, 6418a91, a2c5a3b, 867a506) of
`external/p34-7-hardened-joint-gates` (HEAD 867a506661e4d958404387133370c3d566070a02)
were ported, in order, onto the verified latest `origin/main` (PR #18 merge
commit dfd4b20bf7ffced7717b0adfbd88b19a9eaabbaa) as ordinary cherry-picks on
branch `codex/p34-7-joint-gate-integration-r1`. The joint-gate module, tests,
forge/validate scripts, contracts and runbook are byte-identical to the frozen
final state; conflicts were confined to sealed-digest lines in the Phase 5
contract examples, resolved by keeping the latest-main file bytes (and thus the
latest-main digests). This integration records:

1. The P34.7 hardened joint Gate is integrated into the latest-main-derived
   engineering branch.
2. This means the Gate CODE entered the unified main line only; it is not
   production evidence.
3. `joint_gate._APPROVED_TRUST_POLICY_SHA256` remains an empty set.
4. P34.7 remains `blocked/not_proven`.
5. Production activation remains disabled (`activation_allowed = false`).
6. Migration `0013` was not created; migration head stays `0012`.
7. The P5 Lite/no-tool/read-only `knowledge_search` productization does NOT
   unfreeze the Hardened Sandbox.
8. Shell, SQL, arbitrary HTTP, host-file writes, high-risk Skill/MCP and
   hostile-code execution must still wait for a real P34.7 PASS.
9. Real Linux Runner 12/12, two-member Overlay, DERP, node-compromise,
   non-disposable tenant/RAG and SLA evidence are still missing/not_proven.
10. The next stage is independent trust-policy design/approval and real
    production evidence collection, not opening Feature Gates.

All three Phase 5 production Feature Gates (`AGENT_RUNTIME_ENABLED`,
`AGENT_PLANNER_ENABLED`, `MULTI_AGENT_ENABLED`) remain `false`; production
Runtime, Planner and Multi-Agent remain disabled; the run was not pushed,
merged or PR'd.

## Integration Review-Fix Round 2: object format, freshness window, expiry boundary (2026-08-08)

Three review findings were closed on the latest-main engineering branch
`codex/p34-7-joint-gate-integration-r1` (forward-fix on top of the Integration
R1 state) without changing the P34.7 decision:

1. **P1-A Git object format** — `provenance.git_object_format`,
   `trust-policy.source_seal.git_object_format` and every component evidence
   `git_object_format` now bind the same closed-set format `sha1 | sha256`;
   `sha1` accepts exactly 40 lowercase hex, `sha256` exactly 64; commit/tree
   keep the original Git OID (never re-hashed), while source/artifact
   manifests stay raw-byte SHA-256.  Unknown formats, wrong lengths,
   uppercase OIDs and policy/evidence/component/seal format drift all fail
   closed.  The current repository is SHA-1
   (`git rev-parse --show-object-format` -> `sha1`), and REAL 40-hex
   `HEAD`/`HEAD^{tree}` OIDs are proven to parse into the envelope, the
   policy source seal and the signed component/seal chain — still
   `blocked/not_proven` while `_APPROVED_TRUST_POLICY_SHA256` is empty.
2. **P1-B evidence freshness** — every bundle now carries
   `run_started_at`/`run_completed_at`/`evidence_issued_at`/
   `evidence_valid_until` with the structural invariant
   `run_started_at <= run_completed_at <= evidence_issued_at <
   evidence_valid_until`; every receipt/posture/attack/cleanup timestamp must
   lie inside the run window; `now` must satisfy
   `evidence_issued_at <= now < evidence_valid_until`; both the evidence age
   and the window length are capped by the trust policy's bounded
   `max_evidence_age_seconds`.  The wall clock is read exactly once per
   verification through the `now` clock seam of `verify_joint_evidence`; the
   seal binding covers the four window fields and the Git object format, with
   the sealed posture derived at the issue-time clock so re-verification never
   invalidates a valid seal.  Expired/future/over-long/stale bundles,
   cross-window receipts, un-signed outer time rewrites and policy max-age
   drift are all rejected; the same unexpired bundle can be idempotently
   re-verified offline but an expired bundle is never re-PASSed.
3. **P2 certificate exact-expiry boundary** — the implementation now enforces
   the documented `valid_from <= now < valid_until`: `valid_until == now` is
   already expired and fails closed, `valid_from == now` is an allowed
   first-valid instant (both boundaries have dedicated tests).

Everything else is unchanged: `_APPROVED_TRUST_POLICY_SHA256` remains an empty
set; migration head stays `0012`; migration `0013` is absent; all three Phase
5 feature gates remain false; production Runtime/Planner/Multi-Agent remain
disabled; no real production evidence was generated or forged; the root `.env`
was not read and no business database was accessed or migrated; nothing was
pushed, merged or PR'd.  P34.7 stays `blocked/not_proven`.
