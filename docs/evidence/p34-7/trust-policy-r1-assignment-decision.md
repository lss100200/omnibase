# P34.7 Trust Policy R1-A assignment decision

Date: 2026-08-10

Decision:

```text
R1_A_ASSIGNMENT_CONTRACT_IMPLEMENTED_PENDING_INDEPENDENT_REVIEW
R1_A_REVIEW_FIX_ROUND_1_IMPLEMENTED_PENDING_FINAL_VERIFICATION
R1_A_ASSIGNMENT_CONTRACT_VALID_NOT_ACCEPTED
AUTHORITY_ASSIGNMENTS_INCOMPLETE
CUSTODY_ASSIGNMENTS_NOT_VERIFIED
ENVIRONMENT_INVENTORY_NOT_ASSESSED
PRODUCTION_BLOCKERS_NOT_CLOSED
TRUST_POLICY_NOT_APPROVED
P34_7_PRODUCTION_TOTAL_GATE_BLOCKED_NOT_PROVEN
PRODUCTION_ACTIVATION_DISABLED
```

The R1-A code, canonical example, offline CLI and attack tests establish a
strict assignment contract. The example intentionally records no real person,
service, custody device or target environment. It therefore validates as
`r1_assignment/valid_incomplete`; this is an engineering contract result, not
an acceptance of the design or production evidence.

Master review found that the first implementation accepted proposal-declared
`VERIFIED` and `PROVEN` states after checking only a digest supplied by the same
proposal. Round 1 closes that overclaim: R1-A now rejects self-declared verified
authorities/custody, proven resources/blockers and production equivalence. A
fully populated proposal can reach only
`r1_assignment/complete_not_authenticated`. The report separately fixes these
facts as false until future independently pinned validators exist:

```text
authority_separation_verified = false
authority_authentication_verified = false
independent_review_receipts_verified = false
custody_attestations_verified = false
environment_evidence_verified = false
production_blockers_closed = false
```

The decision does not:

- authenticate an authority;
- approve a Trust Policy or install its digest;
- authorize or execute a key ceremony;
- generate, store, print or transmit private keys;
- authorize access to a non-disposable target environment;
- collect production evidence;
- close any of the eleven P34.7 blockers;
- create migration `0013`;
- enable Runtime, Planner or Multi-Agent;
- deploy or migrate a business database.

Frozen posture:

```text
_APPROVED_TRUST_POLICY_SHA256 = frozenset()
migration head = 0012
migration 0013 = absent
AGENT_RUNTIME_ENABLED = false
AGENT_PLANNER_ENABLED = false
MULTI_AGENT_ENABLED = false
production Runtime = disabled
```

Original R1-A pre-commit engineering verification:

```text
R1-A focused tests = 46 passed
R1-A + R0 + joint-gate regression = 296 passed, 1 skipped
P5.1A/P5.2A/P5.3A sealed-contract regression = 407 passed
backend non-integration = 2448 passed, 20 skipped, 15 deselected
Mypy = 197 source files, no issues
focused Ruff check/format = passed
maintainer map = valid (44 invariants, 38 modules)
maintainer benchmark = valid
Compose config = valid
CI workflow YAML = valid
```

These results prove the engineering contract and regression posture only. They
do not supply an authority registry, custody attestation, target-environment
evidence or production Gate PASS.

Round 1 adds focused attacks for arbitrary authority/custody/evidence digests,
reordered blocker resource mappings, engineering substitute assignments,
production-equivalence claims and a fully populated but unauthenticated
proposal.

Round 1 pre-commit engineering verification:

```text
R1-A focused tests = 51 passed
R1-A + R0 + joint-gate regression = 307 passed, 1 skipped
P5.1A/P5.2A/P5.3A sealed-contract regression = 407 passed
backend non-integration with full-repository mount = 2454 passed, 20 skipped, 15 deselected
Mypy = 197 source files, no issues
focused Ruff check/format = passed
maintainer map = valid (44 invariants, 38 modules)
maintainer benchmark = valid
Compose config = valid
R1-A canonical validate-only = exit 0, valid_incomplete
```

The canonical bare backend-image non-integration command was also attempted,
but collection could not load three repository-root P34.7 scripts absent from
that image. The same suite passed with the maintainer-map full-repository mount;
the initial collection result is not represented as a code-test failure.

The implementation must receive independent code/security review before it is
eligible for push or PR. A later real R1 design acceptance requires a separate
authority trust root and independently signed, replay-bound review receipts;
the current placeholder example cannot supply those facts.
