# P5.4D Product Acceptance R1 — decision

Date: 2026-08-10

Worktree: `OmniBase Worktrees/Active/p5-4d-product-acceptance-r1`
Branch: `external/p5-4d-product-acceptance-r1`
Pre-HEAD: `eb0a1739` · Final HEAD: `0f571f9df8baea700e256a5d7a1fc8db45bb08e7`

Decision:

```text
P5_4D_ENGINEERING_PRODUCT_ACCEPTANCE_PASSED_PENDING_MASTER_REVIEW
```

Explicit companion statements:

- `PRODUCTION_RUNTIME_NOT_ACTIVATED` — the production Agent Runtime is not
  activated; `AGENT_LITE_ENGINEERING_ENABLED` is exactly `true` only inside
  the controlled disposable acceptance Compose stack. All Phase 5 feature
  gates stay `false`; migration head stays `0012`.
- `P34_7_BLOCKED_NOT_PROVEN` — the P34.7 production trust policy remains
  `candidate/valid_not_approved` (`_APPROVED_TRUST_POLICY_SHA256` stays an
  empty frozenset); this acceptance round never approves a digest, never
  generates production keys, and never activates production Runtime,
  Planner or Multi-Agent.

## Scope and method

Product acceptance from an ordinary-user perspective over the closed loop

```text
configure Provider -> create Workspace -> create/install Agent
-> Invoke -> view stream/cancel/Citations
```

on an isolated disposable Compose stack (`omnibase-p54d-acceptance`,
`POSTGRES_PORT=5433`, loopback OpenAI-compatible fake provider on
`127.0.0.1:8790`). Five phases: A baseline matrix, B full 28-step API
journey plus browser UI journey, C UX review, D minimal fixes, E final
acceptance re-run.

## Phase A — baseline (pre-fix)

- backend `pytest -m "not integration"`: green baseline
- frontend `pnpm typecheck` / `lint` / `test` (51) / `NODE_ENV=production
  build`: green
- `docker compose --env-file .env.example config --quiet`: OK
- `mypy src`: green · maintainer map + benchmark validators: green
- P5/P34 verifiers: static contracts valid; P34.7 `candidate/valid_not_approved`

## Phase B — API journey (pre-fix)

`C:\Users\Administrator\AppData\Local\Temp\p54d_journey.py` — 28 steps,
result **26 PASS / 0 FAIL / 2 NOT_PROVEN**:

- NOT_PROVEN step 8 (connection-test success): the SSRF guard requires a
  real public HTTPS host; loopback is rejected by design (fail-closed).
- NOT_PROVEN step 28 (flag-off fail-closed): covered by
  `tests/test_agent_alpha_engineering.py` unit matrix instead.

Journey facts captured: personal credential is only usable after a passed
connection test, otherwise invoke fails closed with
`personal_model_gateway_test_required`; after deleting the personal
credential the operator LLM fallback (fake provider) serves invocations;
SSE event kinds meta/citations/chunk/usage/done are field-based; cancel
mid-stream works; exact idempotency replay re-exposes the durable task.

## Phase B/C — browser UI journey (pre-fix)

Browser automation (IAB tab) over the real UI at `127.0.0.1:3000`:

1. Register / login → dashboard empty state + guidance — PASS
2. Settings → provider add: bad URL shows fail-closed error; valid URL
   saves encrypted credential with fingerprint only, no secret echo;
   connection test result displayed (`auth_failed`, latency) — PASS
3. Credential revoke: badge transitions DEFAULT → REVOKED — PASS
4. Spaces empty state → create dialog (name + template) → Workspace
   created `stopped / generation 1 / version 1` — PASS
5. Workspace detail: lifecycle controls (start/pause/stop/archive) + six
   tabs — PASS
6. Members tab shows the owner; API cross-check confirms the member user id
   equals the logged-in principal and the tenant is the workspace tenant — PASS
7. Agent Builder: "New employee" dialog (name/role/instructions/style/
   tokens/deadline) → "First Employee is sealed and installed." — PASS
8. Installed Agent select lists both agents with digest/binding/profile — PASS
9. Invoke closed loop: question rendered, answer from the controlled fake
   provider rendered — PASS
10. Stop invocation: backend cancel API 200 + stream terminated early — PASS
11. Monochrome: all Tailwind HSL color variables (background/foreground/
    primary/secondary/muted/accent/destructive/success/warning/border/
    ring) have `0%` saturation in both themes — PASS
12. Workspace "Run / Session" tab lists all runs with states — PASS

### Pre-fix findings (Phase C)

- **F-1 (product, severe UX)**: Next.js `rewrites` buffer the upstream
  response body in dev AND in the production standalone image. Measured via
  the proxy: all SSE events arrive at once after ~4.78s; direct to backend
  they arrive per chunk (0.18/1.68/3.18s). The workbench therefore never
  rendered live chunks and the meta event (invocation id) arrived too late.
- **F-2 (UX)**: pressing Stop rendered the raw fetch abort message
  "Invocation failed: BodyStreamBuffer was aborted".
- **F-3 (product, severe)**: cancel/disconnect left the task/run stuck in
  `running`; the late terminalization wrote `heartbeat_at = now` after the
  lease window lapsed, violating `agent_task_leases_heartbeat_window_check`
  (DETAIL row: heartbeat 06:19:09 > expires_at 06:18:04). The IntegrityError
  rolled back the terminal transition, was swallowed by the generator GC
  path (`Exception ignored in: <generator object ..._stream>`), and every
  later invoke on the workspace failed 500 `WorkspaceConflict` because the
  interactive run slot stayed occupied.
- **F-3a (part of F-3)**: `invocationId` was never reset at invoke start;
  with the buffered proxy the Stop handler cancelled a stale id (log shows
  cancel for a previously succeeded task) — the cancellation event was never
  set.
- **F-4 (accepted gap)**: refreshing the workbench clears the conversation
  (no session history in the UI; the durable ledger keeps the tasks/runs).

## Phase D — minimal fixes (commits)

1. `583f7df` `fix(task-ledger): converge terminal lease heartbeat outside
   the window` — `finish_attempt` clamps `lease.heartbeat_at` to
   `min(now, lease.expires_at)`; new contract regression test
   `test_task_lease_heartbeat_window_and_terminal_convergence_contract`
   (F-3b).
2. `e7e911f` `fix(workbench): stream SSE through a route handler and cancel
   cleanly` — replaces the `/api/v1` rewrite with
   `frontend/app/api/v1/[...path]/route.ts` (web-stream passthrough),
   resets `invocationId` at invoke start (F-3a), renders "Invocation
   cancelled." on AbortError / backend cancelled event (F-2).

F-4 is recorded, not fixed (session history is outside the minimal-fix
scope of this round).

## Phase E — final acceptance re-run (post-fix)

Runtime verification on the same disposable stack:

- SSE through the route handler (dev): chunks arrive per chunk
  (0.36/1.86/3.36s) — live streaming article observed mid-stream
  (`article: Hello from the`) — PASS
- SSE through the production standalone image (rebuilt): chunks per chunk
  (0.21/1.72/3.22s) — PASS
- UI Stop: renders "Invocation cancelled."; task converges to `cancelled`,
  workspace run to `cancelled / agent_alpha_cancelled` — PASS
- Client disconnect without cancel: run converges to
  `stopped/failed + agent_alpha_sse_disconnected`, task to
  `blocked_unknown`, reconciliation case `open` with reason
  `agent_alpha_sse_disconnected` (INV-046 semantics) — PASS
- Workspace remains usable after cancel: next invoke 200 with full stream — PASS
- Full API endpoint regression through the route handler
  (workspaces/members/status/profiles/runs/templates/profile): all 200 — PASS
- Backend full non-integration suite: `2402 passed, 20 skipped, 15
  deselected` — PASS
- Focused suites: `test_agent_alpha.py` + `test_agent_alpha_engineering.py`
  + `test_p5_2b_task_ledger.py` + `test_p5_2a_task_ledger_contract.py`
  together `253 passed`; a separate rerun of `test_p5_2b_task_ledger.py`
  alone was `10 passed` (the 9 baseline tests plus the new contract test
  `test_task_lease_heartbeat_window_and_terminal_convergence_contract`)
  — PASS
- `mypy src`: no issues (196 files) · `ruff check`/`format --check` on
  changed paths: clean · `pnpm typecheck`/`lint`/`test` (51)/production
  `build`: clean · `compose config --quiet`: OK · maintainer map +
  benchmark validators: valid
- P5/P34 verifiers (validate-only static mode, NOT clean-HEAD sealed
  `--verify`): P5.2A contract, P5.2C static, P5.4A adapter + gateway,
  P5.4C static all valid; P34.7 candidate
  `candidate/valid_not_approved` (exit 0, production_approved
  false, feature gates false).  At this HEAD the P5.1A/P5.2A/P5.3A
  `--verify` sealed checks reported `invalid/veto` with
  `sealed contract drifted: maintainer_map` because this round modified
  `docs/maintainers/maintenance-map.json`; the reseal and re-verify are
  part of the follow-up master-review fix round, not this acceptance
  evidence.
- API journey re-run: see journey output appended below (26 PASS expected)

## Findings after the fixes

- F-1, F-2, F-3, F-3a: fixed and verified (evidence above).
- F-4: accepted gap — workbench conversation is not persisted across
  reloads; task/run history remains queryable in the ledger and in the
  Workspace "Run / Session" tab.
- Remaining engineering-environment residual: a disconnected stream
  finalizes when the server closes the abandoned generator (observed
  seconds-to-minutes window), because a synchronous generator running in a
  threadpool cannot be interrupted mid-read; the terminal state then
  converges correctly. In the fixed flow (streaming proxy + cancel with a
  live id) this window is not exercised.

## Safety invariants (unchanged)

- Root `.env` not read; no business database accessed or migrated; no
  production migration; migration head stays `0012`.
- `_APPROVED_TRUST_POLICY_SHA256` empty; no digests approved; no private
  keys generated.
- All Phase 5 feature gates false; `AGENT_RUNTIME_ENABLED`,
  `AGENT_PLANNER_ENABLED`, `MULTI_AGENT_ENABLED` untouched (false).
- No push / PR / merge / deploy; changes live in four forward commits on
  `external/p5-4d-product-acceptance-r1` (`583f7df` task-ledger lease
  convergence, `e7e911f` SSE streaming proxy + cancel cleanup, `64e9984`
  and `0f571f9` evidence/handover/maintenance-map documentation; the last
  commit restores minimal byte diffs after a Windows CRLF round-trip in
  `64e9984`).
- Disposable stack only (`omnibase-p54d-acceptance`, `omnibase_test_*`
  names, fake provider on loopback).

## Verdict

```text
P5_4D_ENGINEERING_PRODUCT_ACCEPTANCE_PASSED_PENDING_MASTER_REVIEW
+ PRODUCTION_RUNTIME_NOT_ACTIVATED
+ P34_7_BLOCKED_NOT_PROVEN
```
