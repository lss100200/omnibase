# P6.9 Personal Multi-Agent Team R0 — Codex acceptance package

**Open this file first.** It is the Cursor-produced engineering dossier for
the **whole** P6.9 R0 slice (A2 contract + B coordinator + C workbench + D
loopback journeys). It supersedes the A2-only drip
`docs/reviews/p6-9-a2-codex-acceptance-package-r0.md`. Do not ask Codex to
review A2 again in isolation.

Cursor is **not** the acceptance authority. Cursor claims only the flags
below. Product law remains `docs/architecture/p6-9-multi-agent-planning.md`.

---

## Status Cursor actually claims

```text
PERSONAL_MULTI_AGENT_PLANNED
PERSONAL_MULTI_AGENT_IMPLEMENTED
ENTERPRISE_MULTI_AGENT_DISABLED
P6_9_A2_CONTRACT_SCHEMA_IPC_COMPLETE
P6_9_B_COORDINATOR_COMPLETE
P6_9_C_TEAM_UI_COMPLETE
P6_9_D_JOURNEYS_PROVEN
P6_9_ROUND1_ATTACK_HOLES_CLOSED
P6_8_SINGLE_AGENT_PATH_NOT_REGRESSED
REPACKAGE_NOT_APPROVED
PUSH_PR_NOT_APPROVED
CURRENT_UNSIGNED_INSTALLER_STALE
```

`PERSONAL_MULTI_AGENT_PLANNED` stays on the product-law page as the pre-D
current flag. This package additionally claims
`PERSONAL_MULTI_AGENT_IMPLEMENTED` because D journeys and unique-invocation
proof passed on a **loopback fake OpenAI-compatible Provider**.

`P6_9_ROUND1_ATTACK_HOLES_CLOSED` means the named Round 1 holes
(pinned transport, disabled/model drift, atomic settle, complete event
identity, wall-time, Stop-during-createNode, empty allow-list,
conversation bind, role-config CAS) have automated fail-closed tests.
It does **not** mean a paid/live Provider window.

Cursor does **not** claim:

- paid / production Provider runs
- Authenticode, EXE, MSI, or a later-release repackage
- a live human Electron window soak
- Alembic `0017`
- enterprise Planner / DAG / `MULTI_AGENT_ENABLED`
- OmniBase 1.0.0

---

## Coordinates

| | |
|---|---|
| Date | 2026-08-20 |
| Worktree | `E:\Agent IDE\OmniBase Worktrees\Active\p6-9-cursor-personal-team-r0` |
| Branch | `cursor/p6-9-personal-multi-agent-team-r0` |
| Base (do not amend) | `d2a2db04c0fbfc1ee5d398e40710495c388c21b4` (`d2a2db0`) |
| A2 complete | `0097955ebd94e0455b769f3903c0e105eafb74d1` (`0097955`) |
| B/C/D package (do not amend) | `2af6c1e3ad306334988af9d458134d2b9c1c4805` (`2af6c1e`) |
| Round 1 docs | lands last on the same branch (HEAD after this file) |
| Product-law source | `cursor/p6-9-multi-agent-planning-r0` @ `01f9d3b` |
| P6.8 worktree | `p6-8-cursor-desktop-hardening-r0` left at `d2a2db0` |
| Codex empty pointer | `codex/p6-9-personal-multi-agent-team-r0` left at `d2a2db0` (**untouched**) |
| Method | Forward-only. No amend of `d2a2db0` / `0097955` / `2af6c1e`. No UI redo. No fixed Owner roster. No push. No PR. No EXE/MSI. Root `.env` not read. |

---

## Commit chain (base → this package)

**P6.8 HEAD (frozen):**

- `d2a2db04c0fbfc1ee5d398e40710495c388c21b4` — `style: apply baseline ruff format.`

**A2 (kept; do not amend):**

1. `1f40b7aaa1cf193580d656dc6b51443ab367b28d` — `docs(p6.9): adopt product law and correct PLANNED vs IMPLEMENTED timing`
2. `13e0ab0c732197c944e6948fbeece37763a72dad` — `feat(desktop-local): add personal team schema and proposal validation`
3. `5bc924783c8f823b95ba06aa2238e59bca8ba22e` — `feat(desktop): expose closed role and team-run IPC contracts`
4. `2ece691cd56c99234888e316400cd74873a4ab10` — `test(p6.9): attack personal team proposal and persistence gates`
5. `0097955ebd94e0455b769f3903c0e105eafb74d1` — `docs(p6.9): record P6.9-A2 contract-schema-IPC acceptance for Codex`

**B + C + D (this slice, after A2):**

6. `1aaeabdd272678f4ecee8bed819e1ec6da9f4efd` — `feat(desktop): implement deterministic parent-directed team coordinator`
7. `4d13e1f9a4873da28566de96bd3aed8dc7d96910` — `fix(desktop): bind team node events to roster/node/send epochs`
8. `d5f63e0a98c48ec247ce09dd42227b8d95b13ffb` — `feat(workbench): add explicit personal team controls and node timeline`
9. `fa5aad74bfbee4f537682b39327f06d90b60afc3` — `test(p6.9): prove parent-directed team journeys and attack matrix`
10. `2af6c1e3ad306334988af9d458134d2b9c1c4805` — `docs(p6.9): record personal multi-agent engineering package for Codex`

Stop/scope restoration lives in commit 7 (team FSM) and commit 8 (workbench Stop + `@one` single-call path). They were not mixed with migration+docs.

**Round 1 (after `2af6c1e`; do not amend it):**

11. `6d75a25c99cb6bf5dd1ff58774275aec400ecc4d` — `fix(desktop): reuse pinned Provider transport and reject disabled/model drift`
12. `f5fba0e8c0d5deeb403dc8ed97ea80e94c20cf07` — `fix(desktop-local): atomically settle team node/report/collaboration identity`
13. `38dfcbf72bddb3c37545ca45dda6eec66784b799` — `fix(desktop): require complete team event identity and enforce wall deadline`
14. `6cc087de562b103b635cef6ff7fca62d2d7df74e` — `fix(workbench): converge pre-start failures and empty allow-list behavior`
15. `eae25b833b2dcbec02a2a3bbd285260ae1be6c49` — `test(p6.9): add native-host SQLite journey and missing D2/D4 attacks`
16. This file — `docs(p6.9): update INV-085, AGENTS, maps, handover and acceptance package`

Round 1 did not redo workbench UI and did not return to a fixed Owner roster.

---

## What B shipped — coordinator

New module `desktop/src/runtime/personal-team-coordinator.ts`. It is **not**
inside `RuntimeManager.sendConversation()`. P6.8 single-agent send keeps its
abort latch and one-in-flight conversation path.

Behavior:

- Parent first-pass emits structured JSON only: `answer_directly` | `delegate`.
- Host validates with the A2/B TypeScript clone of the Python validators, then
  persists a plan revision via `submitProposal`.
- Waves execute serial / parallel / mixed. Host **may demote** parallel→serial
  (declared serial, intra-wave deps, or concurrent budget). Host **must not**
  parallelize declared dependencies.
- Each specialist node: unique `nodeId`, `assignmentId`, `invocationId`,
  `nodeEpoch`, `sendEpoch`; independent Provider HTTP call; usage receipt;
  answer digest. Parent calls are counted separately because `team_node`
  CHECK forbids `parent`.
- Blackboard payload to employees: owner objective, role duty, assigned
  subtask, predecessor reports, structured progress. Collaboration requests
  return to the **parent**. Employees cannot peer-launch.
- Employee report `completed | needs_collaboration | blocked`. Parent replan
  between drained waves: `continue | request_followup | finish | cannot_complete`.
- Same specialist reinvoke = new assignment/node/invocation/epochs. Old
  reports are append-only (`team_employee_report` is immutable).
- Fail-stop / unknown: no auto-retry. Process restart maps
  `preparing|running|cancelling` → `unknown` (`recover_interrupted_team_runs`
  in desktop-local lifespan). No auto-replay.
- Abort is armed **before** vault/provider await. Global Stop aborts active
  nodes, skips waiting nodes, and **must not** synthesize.
- Budgets: `maximumProviderCalls`, `maximumWallTimeMs`,
  `maximumConcurrentCalls`, `maximumInputCharacters`,
  `maximumOutputCharacters`. Exhaust reports `budget_exhausted`; no fake
  success; no silent enlarge. Append-budget must stay in bounds and ≥ consumed.
- One live team run at a time (`desktop_team_run_already_active`).
- Secrets never in renderer, logs, or role config. Loopback HTTP only when
  `allowLoopbackHttp` is true.

Schema bump: `desktop_0004_personal_team_runtime` (`DESKTOP_SCHEMA_VERSION = 4`).
Adds node wave/epoch/duration columns, unique `invocation_id`, immutable
`team_employee_report`, durability-only `team_run.parent_final_answer`
(not on the native `parseTeamRun` exact-key payload).

Closed IPC additions (still closed catalog):

- `omnibase:team-runs:execute`
- `omnibase:team-runs:append-budget`

A2 channels remain: `agents.roles.*`, `teamRuns.start|cancel|get|list|subscribe|submitProposal|getBlackboard|recordCollaboration`.

### Parent Proposal vs host validation

1. Coordinator invokes parent with a role-stamped system prompt.
2. Coordinator `extractJsonObject` + `validateParentTeamDecision` /
   `validateParentReplanDecision` (TypeScript).
3. Host `submit_parent_proposal` validates **again** in Python and persists
   only if accepted. Illegal extra keys (`tools`, `directLaunch`, secrets,
   cross-workspace locators, unknown roles, duplicate assignment IDs, missing
   or cyclic deps, infinite budget, infinite replan cap) fail closed and
   create **zero** specialist nodes.

---

## What C shipped — workbench

Practical additions only (not a P7 visual rewrite):

- Team mode checkbox: Owner task-level delegation. Honest copy that parent
  proposes and host validates.
- Optional allow-list of the nine specialists, **default all allowed**.
- Budget line `已用 N / 上限 M 次调用` plus append-budget entry.
- Parent current plan / wave / declared vs effective execution / deps line.
- Employee panel with **text** statuses: 静默 / 等待 / 运行中 / 正在停止 /
  已完成 / 失败 / 需要协作 / 状态未知.
- Timeline: ordinal, role, status, duration, tokens; expand reports.
- Main transcript highlights parent final answer on origin scope.
- Collaboration request lines.
- Global Stop for the whole team run. A→B hides parent live text; Stop stays
  reachable; A return restores parked text. Events must match
  team/roster/wave/node/send identity.
- `@one specialist` still uses the P6.8 single-call send path (role wrapper).
  Multi-`@` still reject. Parent-only mode unchanged.

New modules: `frontend/lib/desktop-team-lifecycle.ts`,
`frontend/lib/desktop-team-surface.ts`. Team FSM is not stuffed into the
single-invocation reducer.

---

## What D proved (loopback)

Proof object asserts `providerCallCount === parentCallCount + executedNodeCount`,
`calls.length === providerCallCount`, unique invocation/node IDs, parent last
when synthesizing, `hiddenCalls: false`. Old run reports cannot enter a new
in-memory host.

### Parent decisions (automated)

| Cell | Test |
|---|---|
| `answer_directly` | `answer_directly uses one parent Provider call and no specialist nodes` |
| one specialist | `one specialist is an independent Provider call with unique node identity` |
| many | `many specialists and all nine keep unique invocation and node IDs` |
| all nine | same |
| parallel pair | `parallel pair uses two specialist nodes and does not hide Provider calls` |
| dep on prior reports | `dependent security wave waits for predecessor reports then parent synthesizes last` |
| mid-run add | `mid-run add and same-specialist reinvoke create new assignment/node/invocation` |
| reinvoke | same |
| accept collab → QA | `parent accepts collaboration and starts QA as a new validated assignment` |
| finish early | `finish early skips synthesis-only extra specialists and still has unique IDs` |
| tokens per node | `tokens are recorded per specialist node and match Provider call count` |

### Security (automated)

| Cell | Evidence |
|---|---|
| unknown role / dup assignment / missing dep / cycle / tools / cross-workspace / employee direct launch | `illegal parent proposals fail closed without creating specialist nodes` |
| infinite budget | A2 `test_infinite_budget_is_rejected` |
| infinite replan cap | `test_infinite_replan_cap_is_rejected` |
| secret in collab | `secret in collaboration is rejected without a fake success` + A2 `test_secret_in_collaboration_request_is_rejected` |
| identity mix across concurrent nodes | parallel-pair distinct `invocationId`/`nodeId` |
| old wave event on new wave | `old wave events are dropped after a new wave starts` |
| second invoke reuse old invocation | `second invoke cannot reuse an old invocation id on the host` |
| Stop cancels all active | `Stop aborts active nodes, skips waiting nodes, and does not synthesize` |
| waiting nodes never start | `Stop on a serial hang skips waiting nodes and does not synthesize` |
| late success after Stop | abort armed; cancelled nodes are not recorded as succeeded |
| process restart unknown | `test_restart_marks_live_team_run_unknown_without_replay` |
| partial Provider fail | `partial Provider failure fail-stops without retry or fake success` |
| parallel node unknown | `parallel incomplete Provider response marks unknown without replay` |
| old run isolation | `old run reports cannot enter a new run` |
| renderer destroy | workbench `mountedRef` ignores late UI after unmount; **no live Electron window proof** |

### Journey (automated, loopback)

Team on → parent picks frontend+backend then security after both → security
asks QA → parent starts QA → parent synthesizes (`accept_collab_qa`). Second
run Stop → waiting nodes never start (`hang_serial`). Restart → no auto-replay
(Python recover).

Participant counts are 1 (parent-only `answer_directly`) through 10 identities
(parent + nine specialists). Call count may exceed 10 under budget.

---

## Named tests added or extended in B/C/D

**Desktop** `desktop/tests/personal-team-coordinator.test.ts`:

- `answer_directly uses one parent Provider call and no specialist nodes`
- `one specialist is an independent Provider call with unique node identity`
- `many specialists and all nine keep unique invocation and node IDs`
- `parallel pair uses two specialist nodes and does not hide Provider calls`
- `dependent security wave waits for predecessor reports then parent synthesizes last`
- `mid-run add and same-specialist reinvoke create new assignment/node/invocation`
- `parent accepts collaboration and starts QA as a new validated assignment`
- `finish early skips synthesis-only extra specialists and still has unique IDs`
- `illegal parent proposals fail closed without creating specialist nodes`
- `old run reports cannot enter a new run`
- `Stop aborts active nodes, skips waiting nodes, and does not synthesize`
- `team events missing roster/node/send epoch must not match the live identity`
- `secret in collaboration is rejected without a fake success`
- `tokens are recorded per specialist node and match Provider call count`
- `Stop on a serial hang skips waiting nodes and does not synthesize`
- `partial Provider failure fail-stops without retry or fake success`
- `parallel incomplete Provider response marks unknown without replay`
- `second invoke cannot reuse an old invocation id on the host`

**Frontend**

- `team FSM is separate from single-invocation idle and keeps text statuses`
- `old team liveText does not paint a new workspace and Stop stays reachable`
- `events must match team/roster/node/send epoch or they are dropped`
- `parent final answer is the highlighted transcript on origin scope`
- `Stop request marks cancelling without requiring color-only status`
- `old wave events are dropped after a new wave starts`
- `waiting specialist stays 等待 after Stop; running becomes 正在停止`
- `team surface lists ten identities with explicit text status`
- `budget line is numeric remaining, not color-only`

**Python** (added on v4)

- `test_infinite_replan_cap_is_rejected`
- `test_continue_proposal_persists_new_assignments_without_overwriting`
- `test_restart_marks_live_team_run_unknown_without_replay`
- `test_append_budget_rejects_infinite_and_keeps_consumed`
- `test_single_agent_send_path_still_works_with_team_schema` (P6.8 non-regression; still green on schema v4)

A2 attack tests remain: unknown role, duplicate assignment, missing/cycle deps,
tools, cross-workspace, infinite budget, employee direct launch, secret in
collab, role-config fingerprint-only.

---

## What Round 1 closed

Round 1 is attack-hole closure on the already shipped A2+B+C+D slice. Product
law is unchanged: parent Proposal + host validation + blackboard.
`ENTERPRISE_MULTI_AGENT_DISABLED` remains. Workbench still has the practical
C controls (checkbox, optional allow-list, timeline, text statuses); Round 1
only converges pre-start failures and fail-closes an explicit empty
allow-list. It does not redesign UI and does not freeze a 2–5 Owner roster.

| Hole | Fail-closed behavior |
|---|---|
| DNS rebinding | Team HTTPS reuses the P6.8 pin: DNS once, validated public IPs, SNI/`Host` stay the hostname. Loopback / private / link-local / multicast / reserved fail closed unless allowed loopback HTTP. |
| Explicit disabled Provider | `role.providerId` that is disabled → `desktop_provider_disabled`. No silent inherit. Unset/null still inherits the default. |
| Model identity drift | Missing or mismatched actual vs requested model → `desktop_provider_model_identity_drift`. `{}` / non-chat-completions is `desktop_provider_response_invalid`, not success. |
| Atomic settle | Node update + employee report + collaboration requests + `team_node_settled` audit are one SQLite transaction. Partial node-without-report or report-without-audit rolls back. |
| Role-config CAS | `expected_row_version` required. Mismatch → `409 desktop_role_config_cas_conflict`. No lost update. |
| Incomplete event identity | Stream/IPC events must carry workspace, conversation, teamRun, rosterEpoch, planRevisionId, waveId, assignmentId, nodeId, sendEpoch. Missing any field is dropped, not projected. |
| Strict wall-time | `maximumWallTimeMs` exceeded stops further nodes, reports `budget_exhausted`, no fake success. |
| Stop during `createNode` | Abort latches like P6.8 (arm before vault/provider await). After create, cancelled without `node_starting` / `node_identity`. |
| Empty allow-list | Explicit `[]` → `desktop_team_allow_list_empty` before `startTeamRun`. Unset still defaults to all nine. |
| Multi-conversation raw start | A start bound to conversation A must not attach to B (`desktop_team_conversation_identity_mismatch`). Pre-start failures converge preparing/running to terminal idle. |

### Round 1 tests mapped to attacks

Tests in commit 15 fail without commits 11–14.

| Attack | Test |
|---|---|
| DNS rebinding to loopback / private / link-local | `DNS rebinding to loopback, private, or link-local is rejected and public pins keep the hostname` (`desktop/tests/personal-team-round1-attacks.test.ts`) |
| Disabled explicit Provider | `explicit disabled Provider fails closed instead of inheriting another` (same) |
| Missing / mismatched actual model | `missing or mismatched actual model fails the chat instead of succeeding` (same; `{}` is `desktop_provider_response_invalid`) |
| Provider failure after node creation | `Provider failure after node creation fail-stops without fake success` (same) |
| Incomplete response after node creation | `incomplete Provider body after node creation is unknown, not success` (same) |
| Report validation / audit append failure | `settle/audit failure after node creation is not success` (same); `test_report_validation_failure_does_not_leave_a_succeeded_node`; `test_audit_append_failure_rolls_back_settle` (`backend/tests/test_desktop_local_personal_team.py`) |
| Stop during `createNode` | `Stop during createNode latches abort and does not emit node identity` (round1 desktop) |
| Missing roster / plan / wave / assignment / node / send epoch (each drop) | `missing roster, plan, wave, assignment, node, or send epoch each fails identity match` (round1 desktop); `missing roster, plan, wave, assignment, node, or send epoch each drops the event` (`frontend/lib/desktop-team-lifecycle.test.ts`) |
| Strict team wall-time | `strict team wall-time stops further nodes without fake success` (round1 desktop) |
| Multi-conversation raw start | `raw start bound to conversation A cannot attach to conversation B` (round1 desktop) |
| Empty allow-list | `empty specialist allow-list fails closed before any team run`; `IPC forwards an explicit empty allow-list instead of parse-failing it`; `test_empty_allow_list_fails_closed_without_defaulting_all_nine`; `test_unset_allow_list_still_defaults_to_all_nine`; `pre-start failure converges preparing to idle instead of hanging` |
| Native SQLite + RuntimeManager + loopback journey | `test_sqlite_settle_is_atomic_with_report_and_audit`; `RuntimeManager plus loopback Provider completes a parent-directed team journey` |
| Role config CAS conflict | `test_role_config_cas_conflict_does_not_lost_update` |

## Gate counts from B/C/D (`2af6c1e`)

Recorded 2026-08-20 on this worktree before Round 1. No Docker/WSL/PostgreSQL.
No paid keys. No installer. Root `.env` not read. PowerShell: no `&&`.

```text
frontend pnpm test       = 257 passed
frontend pnpm typecheck  = passed
frontend pnpm lint       = passed
frontend pnpm build      = passed
desktop  pnpm test       = 65 passed
desktop  pnpm typecheck  = passed
desktop  pnpm build      = passed
pytest desktop_local     = 103 passed
ruff (touched Python)    = passed
git diff --check         = passed
RuntimeHost              = 24/24
  command = pinned SDK C:\Users\Administrator\AppData\Local\OmniBaseBuildTools\dotnet-sdk-8.0.424\dotnet.exe
            run --project packaging/windows/OmniBase.RuntimeHost.Tests/OmniBase.RuntimeHost.Tests.csproj -c Release
            (no --nologo)
```

A2 baseline for comparison: frontend 248, desktop 47, pytest 99.

## Gate counts from Round 1 (this file)

Recorded 2026-08-20 on this worktree after commits 11–15. Same constraints.
No Docker/WSL/PostgreSQL. No paid keys. No installer. Root `.env` not read.
PowerShell: no `&&`. RuntimeHost was optional and was not faked.

```text
frontend pnpm test       = 259 passed
frontend pnpm typecheck  = passed
frontend pnpm lint       = passed
desktop  pnpm test       = 78 passed
desktop  pnpm typecheck  = passed
pytest desktop_local     = 109 passed
ruff (touched Python)    = passed
git diff --check         = passed
validate_maintainer_map  = passed (75 invariants, 51 modules)
validate_maintainer_benchmark = passed (8 scenarios)
RuntimeHost              = optional; not faked
```

---

## P6.8 non-regression

- `RuntimeManager.sendConversation()` is unchanged as the single-agent path.
- Team execute uses `PersonalTeamCoordinator` + independent HTTP.
- `abortInFlightSend` still aborts the P6.8 stream latch and now also
  `requestStop()` on a live coordinator.
- Python `test_single_agent_send_path_still_works_with_team_schema`.
- Existing desktop abort-in-flight and frontend P6.8-A/B lifecycle tests stayed
  green.

---

## Remaining evidence gaps (honest)

| Gap | Status |
|---|---|
| Paid / production Provider | unproven (loopback only) |
| Authenticode | unproven |
| EXE / MSI / later-release repackage | not approved; current unsigned installer stale |
| Live human Electron window soak | unproven |
| Renderer destroy as a real BrowserWindow | `mountedRef` only |
| Cross-workspace live native SQLite isolation beyond validators | validator + IPC; not a two-window soak |
| Round 1 named attack holes | closed in automated tests; paid/live still unproven |

---

## What Codex should review

The whole R0 slice on `cursor/p6-9-personal-multi-agent-team-r0` from
`d2a2db0` through this HEAD, including Round 1. Product law:
`docs/architecture/p6-9-multi-agent-planning.md`. Do not reopen A2 as a
separate drip. Do not drip a new A-only memo. Do not announce OmniBase
1.0.0, Authenticode, EXE, or enterprise multi-agent. Round 1 closes the
named attack holes; it does not prove a paid/live window.
