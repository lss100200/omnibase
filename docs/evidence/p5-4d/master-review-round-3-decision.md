# P5.4D Master Review Round 3 — independent acceptance decision

Date: 2026-08-10

Worktree: `OmniBase Worktrees/Active/p5-4d-product-acceptance-r1`

Reviewed implementation baseline: `6e940bcad144c9ae5e582601a05d799bce80c0da`

Security forward-fix commit: `4c94b7f` (`fix(workspaces): restrict historical holder recovery`)

Decision:

```text
P5_4D_MASTER_REVIEW_ACCEPTED_ENGINEERING
P5_4D_PRODUCT_ACCEPTANCE_R1_COMPLETE
P5_4D_READY_FOR_PERSONAL_EDITION_CONSOLIDATION
PRODUCTION_RUNTIME_NOT_ACTIVATED
```

## Why Round 2 was not accepted unchanged

The Round 2 implementation added `close_historical_run_holder`, but the
function still treated every `LeaseRejected` as potentially recoverable. It
compared caller arguments to the old RunLease but did not compare the current
persisted `WorkspaceNode.fencing_token`, did not revalidate the current live
attestation, and did not require an `active` RunLease to be expired. A stale
Node or an unrelated rejection could therefore enter a path intended only for
an exact expired/revoked historical holder.

## Forward fix

`close_historical_run_holder` now:

- locks and revalidates the current `WorkspaceNode` through
  `get_active_attested_node`, including active state, verified unexpired
  attestation and no revocation;
- compares the current persisted Node fencing token to the historical
  RunLease-bound token;
- reads `clock_timestamp()` from PostgreSQL under the locked transaction;
- accepts only a RunLease already `revoked`/`expired`, or `active` with
  `expires_at <= database clock`;
- rejects an active unexpired lease, a completed lease, an advanced/revoked
  Node, stale attestation, generation drift, replaced identity or wrong
  binding without terminal writes;
- remains failure-only (`failed`/`cancelled`); committed/succeeded can never
  use the historical path; and
- never renews or revives a RunLease.

The integration suite now changes the persisted Node fencing token and
Workspace generation rather than merely passing a mismatched argument. It
also proves that an exact, active, unexpired RunLease is ineligible and that a
revoked current Node cannot authorize historical close. Rejections preserve
TaskLease/Task/WorkspaceRun/RunLease state and do not release the wrong slot.

## Verification

Static and focused checks on the security forward-fix:

- Ruff check and format check on `workspaces/service.py` and the lease Gate:
  passed.
- Mypy on `workspaces/service.py`: passed, zero issues.
- Frontend `pnpm test`: 87 passed.
- Frontend typecheck and lint: passed.

Canonical disposable PostgreSQL Gate:

- run ID: `20260810100438`;
- database: `omnibase_test_p52b_20260810100438`;
- integration closed set: P5.2B foundation + Task/Run double-lease Gate;
- source manifest SHA-256:
  `144690413cb4ebf59c4470e2b0da8ec0d86924f80fccd6e2a2a1549b4163d848`;
- result: passed;
- cleanup: containers/networks/volumes = `0/0/0`;
- root `.env` accessed: false;
- business database accessed/migrated: false/false.

The previous run `20260810091922` remains a truthful Round 2 artifact but is
superseded for this finding because its source manifest did not contain the
current-Node and live-RunLease restrictions.

## Acceptance boundary

This decision accepts P5.4D engineering product behavior and its disposable
evidence for consolidation. It is not production activation evidence by
itself. Production activation still requires the separately defined personal
Owner approval/activation Gate. Enterprise multi-human authority ceremony,
custody, DERP, multi-member and SLA evidence are not requirements of the
single-Owner personal edition and remain frozen for future team/enterprise
work.

Safety posture at this decision:

```text
AGENT_RUNTIME_ENABLED=false
AGENT_PLANNER_ENABLED=false
MULTI_AGENT_ENABLED=false
migration head=0012
migration 0013=absent
root .env not read
business database not accessed or migrated
not pushed
not merged
not deployed
```
