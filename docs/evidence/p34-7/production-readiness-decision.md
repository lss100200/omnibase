# P34.7 production readiness decision

Date: 2026-08-02

Decision:

```text
P34.7 implementation/contracts/local gates: COMPLETE
P34.7 production total Gate: BLOCKED / NOT_PROVEN
production activation: DISABLED
Phase 5 Agent Runtime: PLANNED / FROZEN
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

## Clean-checkout follow-up

After this change is committed, run the formal validator from a fresh public
clean checkout:

```text
python scripts/production/validate_p34_7_composition.py \
  --verify \
  --output <operator-controlled-path>/p34-7-production-admission.json
```

The expected result remains `blocked/not_proven` until every production
blocker above has current-source, hash-bound evidence. A clean-checkout
`blocked/not_proven` result proves reproducibility and safe refusal; it does not
unlock production or Phase 5.
