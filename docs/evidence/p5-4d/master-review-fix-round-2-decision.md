# P5.4D Master Review-Fix Round 2 — decision

Date: 2026-08-10

Worktree: `OmniBase Worktrees/Active/p5-4d-product-acceptance-r1`
Branch: `external/p5-4d-product-acceptance-r1`
Pre-HEAD: `65ad654b34b8aaf34c6a102312e982c4bf20a9e4`
Final HEAD: `37ab87fd62b15860449932c8372953c11b6cc602`

Decision:

```text
P5_4D_REVIEW_FIX_ROUND_2_IMPLEMENTED_PENDING_INDEPENDENT_REVIEW
P5_4D_ENGINEERING_PRODUCT_ACCEPTANCE_NOT_YET_MASTER_ACCEPTED
PRODUCTION_RUNTIME_NOT_ACTIVATED
P34_7_BLOCKED_NOT_PROVEN
```

## Historical scope

- `0f571f9` — original Product Acceptance R1 evidence
  (`docs/evidence/p5-4d/product-acceptance-r1-decision.md`); its Final HEAD
  is intentionally NOT rewritten and its test numbers are NOT mixed with
  this round's.
- `65ad654` — Master Review-Fix Round 1 implementation baseline.
- This round's final HEAD — Master Review-Fix Round 2 implementation.

## Round 1 recap (already implemented at 65ad654)

- `settle_terminal_outcome`: Task Lease expiry derails `committed` to
  `unknown`; `finish_attempt` closes Lease+Attempt atomically with the
  settled outcome; `_terminalize` drives budget/effect/reconciliation/
  task/run with the same settled outcome.
- `isUserCancelledError` mapping, streaming Route Handler proxy core
  (lib/proxy.ts), evidence accuracy fixes and the first reseal.

## Round 2 findings and fixes

### P1-1 — Task Lease + Workspace Run Lease double expiry

Reproduction (disposable PostgreSQL, real `LedgerInvocationAdapter.begin`):
when the Task Lease AND the Run Lease are both expired, the terminal
transition reached `submit_run_state`, which refuses expired/stale/revoked
leases through `_validated_run_lease` (LeaseRejected) — the whole session
transaction rolled back and TaskLease/Attempt/Effect/Task/AgentRun/
WorkspaceRun stayed in their pre-transition states, occupying the
interactive slot (proven by the pre-fix rollback scenario, which the new
suite covers via scenario G's follow-on-failure rollback and the
double-expiry reproduction).

Fix: `close_historical_run_holder` (workspaces/service.py), a
server-owned, fail-closed, HISTORICAL-holder-only terminal close used when
`submit_run_state` raises LeaseRejected (never for `committed`):

- only `failed`/`cancelled` (unknown maps to failed) — an expired
  authorization can never be closed as succeeded/stopped;
- exact-holder validation under lock: workspace aggregate, WorkspaceRun,
  RunLease, node binding, workspace generation, run fencing
  (`run.next_fencing_token - 1`), node fencing; any mismatch fails closed;
- RunLease never renewed, never revived, never returned to active
  (active-but-lapsed → revoked; already-terminal stays terminal);
- WorkspaceRun terminalized, runtime/workload bindings cleared → the
  `workspace_runs_one_active_uq` interactive slot is freed;
- TaskLedger, WorkspaceRun and reconciliation commit atomically in the
  caller's transaction; any later failure rolls everything back.

### P1-2 — full persisted row matrix

`_assert_failed_unknown_matrix` reads and asserts: TaskLease (state,
expires_at, heartbeat_at == boundary, fencing token), Attempt (state
unknown, lease binding cleared), Effect (state unknown, result_digest
null — no fabricated committed result), Task (blocked_unknown, never
completed), AgentRun (failed, run_lease_id / run_fencing / node /
node_fencing / runtime_instance / workload_identity all cleared),
WorkspaceRun (failed, desired stopped, bindings cleared, no success),
RunLease (terminal, never active), Reconciliation (exactly one case with
precise reason `agent_alpha_task_lease_expired` /
`agent_alpha_sse_disconnected` and full attempt/task/effect/agent-run
bindings), Budget (reserved/committed/released/remaining; unknown charges
nothing), and the Workspace slot (zero active runs; next invocation
starts immediately).

### P1-3 — lease gate wired into the canonical Gate

- `Makefile test-p5-2b-task-ledger` runs foundation first (fresh-database
  downgrade test) then the lease-gate suite.
- `run_p5_2b_task_ledger_disposable_gate.py` executes BOTH integration
  files, seals both (plus the now-authoritative
  `workspaces/service.py` and `agent_alpha/adapters.py`) in the closed
  source manifest, and records both in the evidence receipt's
  `integration_tests` closed set.
- `test_run_p5_2b_task_ledger_disposable_gate.py` pins the new paths.
- Deleting `settle_terminal_outcome`, restoring expired-committed success
  or breaking the WorkspaceRun cleanup now fails the canonical Gate.
- New immutable run-scoped evidence directory (see below); the previous
  P5.2B evidence (pre-lease-gate) is superseded for this finding and
  remains in Git history only.

### P1-4 — SSE EOF fails closed

`lib/agent-alpha-stream.ts` `consumeAgentAlphaStream`: only a legal `done`
terminal produces success; EOF without a terminal (empty stream or partial
tokens) → `agent_alpha_stream_incomplete` (never "No answer returned.");
malformed JSON / malformed terminal payload → `agent_alpha_stream_malformed`;
duplicate terminal or events after a terminal →
`agent_alpha_stream_after_terminal`; `error` → backend code; `cancelled`
and fetch AbortError → user cancellation. UI text derives from stable
codes only. Tests: delta+done, done-without-delta, EOF-before-events,
partial-then-EOF, error, cancelled, AbortError, malformed, split frames,
multi-event chunks, duplicate/conflicting terminals.

### P1-5 — Stop → immediate reinvoke ownership

`lib/invocation-state.ts` `InvocationGuard`: unique generation +
per-invocation AbortController; `begin()` refused while running/cancelling;
`stop()` aborts and enters `cancelling` (UI never prematurely idle);
`settle()` clears only the current generation/controller pair, so a stale
invocation's catch/finally can never clear a newer invocation's controller
or overwrite its messages/invocationId. Workbench drives
running/cancelling/idle from the guard phase. Concurrency tests cover the
full Stop/reinvoke timeline.

### P2-1 — compressed response header/body consistency

The proxy forces `Accept-Encoding: identity`; an upstream that ignores it
and still answers with a compressed `Content-Encoding` (gzip/br/deflate)
is failed closed with the stable 502 — decompressed bytes are never
forwarded under a stale compression header, so the browser never
double-decodes and never waits on a wrong Content-Length. Identity/absent
encodings pass through byte-clean; SSE stays unbuffered; Authorization /
Idempotency-Key / Content-Type preserved; hop-by-hop and Connection-named
headers stripped; no backend target URL leak. Tests use a real HTTP
upstream for gzip/br/deflate fail-closed and identity passthrough.

### P2-2 / P2-3 — maintainer documents, evidence and handover

maintenance-map (INV-044 test_paths, p5-task-ledger-persistence and
agent-alpha-workbench source paths/entrypoints/verification/recovery),
ai-maintainer-map (8.2 proxy statement + new 6.16 section) and
security-invariants INV-044 (double-lease semantics) updated in commit
`ca14466`. This evidence file is new; the Round 1 evidence stays
historical; the handover gains a Round 2 section without rewriting past
sections.

## Canonical Gate run (immutable evidence)

- Run ID: `20260810091922` (project `omnibase-p52b-20260810091922`,
  database `omnibase_test_p52b_20260810091922`)
- Immutable run-scoped directory:
  `.tmp/p5-2b-task-ledger-gate/20260810091922/` containing
  `source-manifest.json`, `evidence.json`, `evidence.md`
- Canonical evidence: `docs/evidence/p5-2/phase5-task-ledger-disposable-gate.json`
  — `passed: true`, `migration_head: 0011`,
  `integration_tests: [foundation, lease_gate]`,
  `source_manifest_sha256: 11fecc53469628cc60bd217be357b4e18388c4afa767a5249355ae8e31e1b56e`,
  cleanup `0/0/0`
- `--verify-evidence docs/evidence/p5-2/phase5-task-ledger-disposable-gate.json`
  → exit 0, "P5.2B recorded evidence source seal passed"
- The lease-gate suite was genuinely executed by the canonical Gate (both
  files are in the evidence receipt's integration_tests closed set).

## Verification matrix (final clean HEAD)

Backend:
- focused unit: `test_p5_2b_task_ledger.py` + `test_agent_alpha*.py` +
  `test_p34_4_workspace_service.py` + `test_p5_2a_task_ledger_contract.py`
  → 308 passed / 1 expected seal-drift failure pre-reseal
- disposable PostgreSQL: foundation + lease gate → 14 passed (fresh
  sentinel)
- canonical `make test-p5-2b-task-ledger` equivalent (Gate `--run`) →
  passed (exit 0)
- Gate `--verify-evidence` → exit 0
- P5.2C Agent Alpha disposable Gate static (`--validate-only`) → valid
- full `pytest -m "not integration" -q` → 2402 passed / 2 expected
  seal-drift failures pre-reseal / 21 skipped / 15 deselected
- `mypy src` → 0 issues (196 files)
- Ruff check + format --check on changed/map-listed paths → clean

Frontend:
- `pnpm test` → 87 passed
- `pnpm typecheck` / `pnpm lint` / Prettier check → clean
- `NODE_ENV=production pnpm build` → clean
- SSE incomplete-stream tests, Stop/reinvoke concurrency tests,
  compressed-upstream tests, proxy streaming/abort tests → all included
  in the 87

Repository:
- `docker compose --env-file .env.example config --quiet` → OK
- `validate_maintainer_map.py` → valid (577 path specs, 258 entrypoints)
- `validate_maintainer_benchmark.py` → valid
- `git diff --check` → clean
- `git status --porcelain=v1 --untracked-files=all` → clean at final HEAD

Formal clean-HEAD verifiers (after reseal):
- P5.1A / P5.2A / P5.3A `--verify` → exit 2, `blocked/not_proven`,
  `contract_valid=true`, `vetoes=[]`, `activation_allowed=false`
- P34.7 candidate validator → `candidate/valid_not_approved` (NOT a
  production approval)
- P34.7 joint validate-only → valid, gates false

## Unproven boundaries (unchanged)

Production Runtime/Planner/Multi-Agent remain disabled; migration head
`0012`, `0013` absent; `_APPROVED_TRUST_POLICY_SHA256` empty; no digest
approved; no private keys generated/exported/transferred; root `.env` not
read; no business database accessed or migrated; no push / PR / merge /
deploy; no P34.7 R1 ceremony started; disposable evidence is never
described as production evidence. The double-lease recovery is proven on
the disposable sentinel only; real long-disconnect production behaviour,
capacity, DERP, node-compromise and SLA remain unproven.
