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
P6_9_ROUND2_FORWARD_FIX
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

`P6_9_ROUND2_FORWARD_FIX` means Round 2 shipped production code and tests
for the named items, **not** that all ten were closed in one pass. Two-lane
audit P1s on Stop/node CAS and `/reports` bypass were forward-fixed after
`5321aa7`. A later concentrated P6.9 forward-fix (this file) closes residual
Stop/recovery/collab/latch/snapshot/`/reports` replay holes. Item 1 pin match
is honest: production team HTTPS asks desktop-local `is_global_unicast`; the
TS BlockList is a fallback replica; remaining extra-rejects are **examples**,
not an exhaustive IANA list. Item 5 create/settle remains **two** transactions
(create before Provider HTTP, settle after); settle re-binds live
Conversation, current plan, and Provider `is_enabled`. Round 1's
`RuntimeManager plus loopback Provider completes a parent-directed team
journey` test is **not** a native HTTP→SQLite journey; it wraps an
in-memory host as a fake `DesktopNativeClient`. Round 2 adds the true
path. Paid / EXE / live window remain unproven.

Publish boundary flags (unchanged; do not claim otherwise):

```text
PAID_PROVIDER_NOT_PROVEN
AUTHENTICODE_NOT_PROVEN
EXE_MSI_REPACKAGE_NOT_APPROVED
LIVE_HUMAN_ELECTRON_WINDOW_NOT_PROVEN
ENTERPRISE_MULTI_AGENT_DISABLED
```

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
| Round 1 docs (do not amend) | `01210364fcc9b2b739003f6af64ef08903422f14` (`0121036`) |
| Round 2 docs | `5321aa73456075c0e293d580fdc0bee6f2c5fafc` (`5321aa7`) |
| Stop/reports P1s | `b756a6c96341d52ef41d5192291ae96952aa589e` (`b756a6c`) |
| Parked chrome | `afd39ecd28634954aae60edba51eb4a59f53ca3b` (`afd39ec`) |
| P1 tests | `5cfbca4f171a0c9f69de16f8314026d28c3529e0` (`5cfbca4`) |
| Honesty after P1s (do not amend) | `476297afd77bdb51e6f3a0a55f333813350468c6` (`476297a`) |
| Backend residual Stop/collab | `80170a22160355c4b749fea237b2f3e0a16f0bfe` (`80170a2`) |
| Frontend latch/snapshot | `e1910a4016430308206491014a46b7ed20dc93ce` (`e1910a4`) |
| Negative tests | `9dcc46b511b817571f5414925ab31afa4fd94b46` (`9dcc46b`) |
| Concentrated forward-fix (do not amend) | `246c423201b0a7e10ba578d0d3825b08f41a4cb6` (`246c423`) — **audited, not passed** |
| Round 3 backend transaction law | `cc8494665c7aec2523c958a0215dc03a794cedfc` (`cc84946`) |
| Round 3 frontend latch/identity | `5b63739a42f87dcee962ec169a5d286424863e32` (`5b63739`) |
| Round 3 IPC negatives | `b5dc20b760fbc89f9b7e80e342e56a924472b604` (`b5dc20b`) |
| Round 3 record (do not amend) | `0423bdb41970e3192ba2d1117a24ba269ffc8d60` (`0423bdb`) — **audited 2026-08-22, acceptance withheld; see Round 4** |
| Round 4 success closure | `ac746be10258a2c7e236d035d7a5cfb412639554` (`ac746be`) |
| Round 4 resolve binding | `05781da60e15beb0908963a84e8a679836c92bd9` (`05781da`) |
| Round 4 legacy replay / duplicates | `68d4bd1bb5537bdc76f1e35250079c9343d2be8a` (`68d4bd1`) |
| Round 4 collaboration close-out | `56a9c384aad6d7fa6806f29ce009296e02edd131` (`56a9c38`) |
| Round 4 append-budget gate | `a4a50051f7bb4017a74517a54b6cf7c2057bc827` (`a4a5005`) |
| Round 4 IPC reportId negative | `4f2d7ecce65a281364497cbe20a65fea58bb08fe` (`4f2d7ec`) |
| This Round 4 record | lands last on the same branch (HEAD after this file) |
| Product-law source | `cursor/p6-9-multi-agent-planning-r0` @ `01f9d3b` |
| P6.8 worktree | `p6-8-cursor-desktop-hardening-r0` left at `d2a2db0` |
| Codex empty pointer | `codex/p6-9-personal-multi-agent-team-r0` left at `d2a2db0` (**untouched**) |
| Method | Forward-only. No amend of `d2a2db0` / `0097955` / `2af6c1e` / `0121036` / `5321aa7` / `476297a`. No UI redo. No fixed Owner roster. No push. No PR. No EXE/MSI. Root `.env` not read. |

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
16. `01210364fcc9b2b739003f6af64ef08903422f14` — `docs(p6.9): update INV-085, AGENTS, maps, handover and acceptance package`

Round 1 did not redo workbench UI and did not return to a fixed Owner roster.

**Round 2 (after `0121036`; do not amend Round 1):**

17. `12dbe2109b793bc346b71424988aae8b0cd0a51e` — `fix(desktop): use authoritative global-unicast pin and bind vault to enabled Provider`
18. `7b401775537badb7bc7fcb3e864d3e1e85e84017` — `fix(desktop-local): unique settle API, transactional node identity, and replan transition events`
19. `f26f28df506ff1ae313edccaad05a059192e0839` — `fix(desktop): independent team wall-time, per-chunk model check, and parked team buffers`
20. `e0cac2743ff1c5b9dfc92bbddfe65ba3ead94598` — `test(p6.9): add RuntimeManager native SQLite HTTP journey and Round-2 attacks`
21. `5321aa73456075c0e293d580fdc0bee6f2c5fafc` — `docs(p6.9): correct INV-085 handover maps and Codex package overclaims`

**Round 2 P1 forward-fix (after `5321aa7`; do not amend Round 2 docs):**

22. `b756a6c96341d52ef41d5192291ae96952aa589e` — `fix(desktop-local): cancel running nodes with the team run and close report settle bypass`
23. `afd39ecd28634954aae60edba51eb4a59f53ca3b` — `fix(workbench): park full team chrome off origin`
24. `5cfbca4f171a0c9f69de16f8314026d28c3529e0` — `test(p6.9): cover Stop node CAS and reports-without-success`
25. `476297afd77bdb51e6f3a0a55f333813350468c6` — `docs(p6.9): correct Round-2 overclaims after Stop and reports P1s`

**Concentrated P6.9 forward-fix (after `476297a`; do not amend it / `5321aa7` / `d2a2db0`):**

26. `80170a22160355c4b749fea237b2f3e0a16f0bfe` — `fix(desktop-local): CAS residual nodes on cancelled Stop and bind collab/reports`
27. `e1910a4016430308206491014a46b7ed20dc93ce` — `fix(workbench): latch team terminals and bind first snapshot to origin view`
28. `9dcc46b511b817571f5414925ab31afa4fd94b46` — `test(p6.9): cover residual Stop, recovery-by-parent, and terminal latch`
29. `246c423201b0a7e10ba578d0d3825b08f41a4cb6` — `docs(p6.9): record concentrated Stop/recovery/latch fixes for Codex` (this file + INV-085 honesty; pin examples, not an exhaustive IANA list)

**Round 3 P1 forward-fix (after `246c423`; do not amend it):**

30. `cc8494665c7aec2523c958a0215dc03a794cedfc` — `fix(desktop-local): terminal runs need settled children, live resolve CAS, report digest`
31. `5b63739a42f87dcee962ec169a5d286424863e32` — `fix(workbench): latch all team terminals and bind parked identity for durable cancel`
32. `b5dc20b760fbc89f9b7e80e342e56a924472b604` — `test(desktop): add direct nodeId/reportId IPC rejection negatives`
33. `0423bdb41970e3192ba2d1117a24ba269ffc8d60` — `docs(p6.9): record Round 3 forward-fix and correct item 5 two-transaction honesty` (audited 2026-08-22; acceptance withheld)

**Round 4 P1/P2 forward-fix (after `0423bdb`; do not amend it):**

34. `ac746be10258a2c7e236d035d7a5cfb412639554` — `fix(desktop-local): require success closure for succeeded and downgrade unproven recovery`
35. `05781da60e15beb0908963a84e8a679836c92bd9` — `fix(desktop-local): bind collaboration resolve to decision shape, target role and plan`
36. `68d4bd1bb5537bdc76f1e35250079c9343d2be8a` — `fix(desktop-local): fail closed legacy report replay and duplicate collaboration tuples`
37. `56a9c384aad6d7fa6806f29ce009296e02edd131` — `fix(desktop): resolve pending collaborations before team success`
38. `a4a50051f7bb4017a74517a54b6cf7c2057bc827` — `fix(workbench): gate team append budget to the origin view`
39. `4f2d7ecce65a281364497cbe20a65fea58bb08fe` — `test(desktop): add uppercase reportId IPC rejection negative`
40. This file + INV-085 / ai-maintainer-map forward amendments (success closure, resolve binding, legacy fail-closed, schema v7)

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
- Global Stop for the whole team run. A→B parks phase/plan/budget as well as
  live text; Stop stays reachable; A return restores parked chrome. Events
  must match team/roster/wave/node/send identity.
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
| Native SQLite + RuntimeManager + loopback journey | Round 1 `RuntimeManager plus loopback Provider…` is an **in-memory host wrapped as a fake DesktopNativeClient** (IPC-mock-shaped). Round 2 `RuntimeManager DesktopNativeClient desktop-local HTTP SQLite journey records report and audit` is the true HTTP→SQLite path. `test_sqlite_settle_is_atomic_with_report_and_audit` remains the Python settle atomicity test. |
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
| Round 1 "native SQLite journey" wording | overclaim; the RuntimeManager test was in-memory. Round 2 closes this. |
| Round 1 in-memory RuntimeManager test | still in the suite as historical/weaker evidence; item 10 is the true path |
| Item 1 pin dual-impl | Production asks desktop-local `is_global_unicast`. TS `BlockList` is fallback/tests only; extra-rejects vs CPython (`2001:1::1`, `2001:3::1`, `2001:20::1`) are examples, not an exhaustive IANA list |
| Item 5 create/settle | Still **two** SQLite transactions (create before Provider HTTP, settle after). Settle re-binds live Conversation, plan, Provider `is_enabled` |
| Round 2 "closed all ten" | Overclaim on `5321aa7`. Stop/node CAS and `/reports` P1s are forward-fixed after that SHA |

---

## Round 2 forward-fix (after Round 1 `0121036`; do not amend Round 1)

Layered commits on `cursor/p6-9-personal-multi-agent-team-r0`. Fewer than ten
micro-rounds. No UI redo. No fixed Owner roster. No push, PR, or EXE.

### Round 2 tests mapped to the ten items

| Item | What landed | Tests that fail without it |
|---|---|---|
| 1 Global-unicast pin | Production team HTTPS asks `POST /desktop/v1/provider-endpoints/pin` (`endpoint.py` `is_global_unicast`). TS `BlockList` remains a test/fallback replica. Extra-rejects vs CPython (`2001:1::1`, `2001:3::1`, `2001:20::1`) are examples, not an exhaustive IANA list | `team transport pin hook uses backend connect addrs…`; `TS BlockList pin is not CPython is_global_unicast; extra-rejects are examples not an exhaustive IANA list`; `test_pin_endpoint_uses_python_is_global_unicast` |
| 2 Independent wall | Coordinator `AbortController` + timer, Provider `timeoutMs` not min(wall) | `independent wall AbortController expires to budget_exhausted…`; inverse `Provider HTTP timeout is not reported as team wall budget_exhausted` |
| 3 SSE per-chunk model | `readSseText` and `_iter_sse_events` validate every `model` immediately | `SSE model drift mid-stream fails the node instead of succeeding`; `test_sse_model_drift_mid_stream_fails_closed_not_success` |
| 4 Unique success-settle | Legacy update only `failed\|cancelled\|unknown` + CAS. `/reports` is **not** a second success path | `legacy success update is rejected…`; `test_legacy_update_cannot_succeed_or_resurrect_a_settled_node`; **P1-2** `test_report_on_running_node_is_rejected_without_settled_audit`; `POST report on a running in-memory node fails closed without a settle audit` |
| 5 Transactional identity | Schema v5 unique epochs + terminal trigger. **Create and settle remain two transactions** (HTTP between them). Each bind live run, plan, wave, assignment, role, node, invocation, epochs, Provider `is_enabled`, models. Settle re-binds live Conversation row (not regex-only). Terminal create still fail-closed | `test_create_node_binds_live_run_identity_and_forbids_epoch_reuse` |
| 6 Replan transition | Coordinator emits `plan_transition`; frontend accepts new plan after it | `replan plan_transition accepts the new proposal…` |
| 7 Per-type identity | Terminal/node events must match assignment, role, invocation, wave, nodeEpoch, sendEpoch | `node_terminal missing or mismatched identity fields are dropped`; round1 node_delta completeness |
| 8 Parked buffers | Leave origin parks text/nodes/collab **and** phase, planRevisionId, waveId, planSummary, execution, budgets. First B render has no A team chrome. A→B→A restores delta/terminal/final **and** plan/phase | `leaving origin parks team buffers and returning restores delta/terminal/final`; `leaving origin parks phase plan and budget so B has no A team chrome` |
| 9 Vault+enabled snapshot | `load_provider_secret_material` SELECT includes `is_enabled` | `test_disabled_provider_vault_is_bound_to_the_same_snapshot`; `stale list-enabled then vault-disabled fails closed without decrypt` |
| 10 True e2e | Spawn `python -m omnibase.desktop_local.app`, real `DesktopNativeClient`, `RuntimeManager.executeTeamRun`, query SQLite report+audit | `RuntimeManager DesktopNativeClient desktop-local HTTP SQLite journey records report and audit` |
| P1-1 Stop node CAS | `cancel_team_run` CAS `pending\|running` nodes to `cancelled` in the same transaction. `running→cancelled` still allowed when the run is already `cancelling\|cancelled`. Restart recovery does not rewrite `cancelled` → `unknown` | `test_cancel_team_run_cas_running_nodes_and_restart_keeps_cancelled` |

### Round 2 gate counts

Recorded after the Round 2 layered commits. Same constraints as Round 1.
No Docker/WSL/PostgreSQL. No paid keys. No installer. Root `.env` not read.
PowerShell: no `&&`. RuntimeHost optional; not faked.

```text
frontend pnpm test       = 262 passed
frontend pnpm typecheck  = passed
frontend pnpm lint       = passed
desktop  pnpm test       = 86 passed
desktop  pnpm typecheck  = passed
pytest desktop_local     = 147 passed
ruff (touched Python)    = passed
git diff --check         = passed
validate_maintainer_map  = passed (75 invariants, 51 modules)
RuntimeHost              = optional; not faked
```

### Round 2 P1 forward-fix gate counts

Recorded after Stop/node CAS, `/reports` close, parked chrome, and honesty
docs. Same constraints. No Docker/WSL/PostgreSQL. No paid keys. No installer.
Root `.env` not read. PowerShell: no `&&`. RuntimeHost optional; not faked.

```text
frontend pnpm test       = 263 passed
frontend pnpm typecheck  = passed
frontend pnpm lint       = passed
desktop  pnpm test       = 89 passed
desktop  pnpm typecheck  = passed
pytest desktop_local     = 150 passed
ruff (touched Python)    = passed
git diff --check         = passed
RuntimeHost              = optional; not faked
```

---

## Concentrated P6.9 forward-fix (after `476297a`; do not amend)

Small round. Not a UI redo. Not a new mega-round. Product law unchanged:
parent Proposal, host validation, blackboard. Codex pointer and P6.8 remain
at `d2a2db0`.

### Tests that fail if the fix is deleted

| Item | What landed | Negative test |
|---|---|---|
| 1 Second Stop / residual CAS | Already-cancelled Stop is idempotent 200 and still CAS-cancels leftover `pending\|running` nodes/assignments | `test_second_stop_on_cancelled_run_cas_residual_live_nodes` |
| 2 Recovery by parent Run | Cancelled parent → residual live `cancelled`; crash/`unknown` parent → residual live `unknown`; already-`cancelled` nodes stay `cancelled` | `test_recovery_maps_residual_nodes_from_parent_run_state` |
| 3 Collab write identity | Independent collab requires live Run + matching node/report. Terminal run or wrong id fails closed | `test_collaboration_write_requires_live_run_and_node_report_identity` |
| 4 Frontend terminal latch | `cancelled\|failed\|unknown\|budget_exhausted` not resurrected by late `completed` | `cancelled failed unknown and budget_exhausted stay latched against a late completed event` |
| 5 First snapshot origin bind | Snapshot for origin A does not bind while the current view is B | `first snapshot from origin A does not bind while viewing workspace B` |
| 6 Unknown / missing Stop | Missing id and already-unknown run are 409 no-ops; unrelated live run stays preparing | `test_stop_missing_and_unknown_run_are_conflict_noops` |
| 7 `/reports` exact replay | Mutated text/status against a settled node is `desktop_team_report_replay_mismatch`; identical replay is idempotent | `test_report_replay_rejects_mutated_body_and_accepts_exact_match` |
| 8 Pin enumeration honesty | TS fallback replica; production pin is desktop-local `is_global_unicast`; extra-rejects are examples, not an exhaustive IANA list | `TS BlockList pin is not CPython is_global_unicast; extra-rejects are examples not an exhaustive IANA list` |
| 9 This table | Every item above has a delete-the-fix-and-fail test named here | this section |

### Concentrated forward-fix gate counts

Recorded after Stop residual CAS, recovery-by-parent, collab identity,
terminal latch, snapshot origin bind, unknown Stop 409, `/reports` exact
replay, and pin-enumeration honesty. Same constraints. No Docker/WSL/PostgreSQL.
No paid keys. No installer. Root `.env` not read. PowerShell: no `&&`.
RuntimeHost optional; not faked.

```text
frontend pnpm test       = 265 passed
frontend pnpm typecheck  = passed
frontend pnpm lint       = passed
desktop  pnpm test       = 89 passed
desktop  pnpm typecheck  = passed
pytest desktop_local     = 155 passed
ruff (touched Python)    = passed
git diff --check         = passed
RuntimeHost              = optional; not faked
```

Publish boundary (unchanged):

```text
PAID_PROVIDER_NOT_PROVEN
AUTHENTICODE_NOT_PROVEN
EXE_MSI_REPACKAGE_NOT_APPROVED
LIVE_HUMAN_ELECTRON_WINDOW_NOT_PROVEN
ENTERPRISE_MULTI_AGENT_DISABLED
```

---

## Round 3 P1 forward-fix (after `246c423`; do not amend it)

The Codex audit of `246c423` reproduced four P1s: `/state` could commit a
terminal parent while nodes/assignments were still live and restart recovery
did not converge it; collaboration resolve still wrote the blackboard after
Stop; the frontend latch only rejected one late success event so replan and
node deltas could resurrect or pollute all six terminals; and the first
snapshot view gate dropped legal origin snapshots while viewing B, so
Stop-before-identity never issued a durable backend cancel. It also confirmed
two P2s (`/reports` replay ignored `collaboration_requests`; this file
claimed "item 5 one-transaction status" against the real two transactions)
and one P3 (no direct IPC `nodeId`/`reportId` negatives). This round
forward-fixes all of them in one concentrated pass. `246c423` itself is
**not** recorded as passed.

| # | Fix | Tests that fail if the fix is deleted |
|---|---|---|
| 1 | `/state` terminal children invariant inside one `BEGIN IMMEDIATE` (`desktop_team_run_children_live`), `/state cancelled` cascading the Stop child CAS, recovery defensive pass for any other terminal parent (`personal_team.py`) | `test_state_succeeded_requires_settled_children_in_one_transaction`, `test_state_quiet_terminals_refuse_live_children`, `test_state_cancelled_converges_residual_live_children`, `test_recovery_converges_live_children_of_any_terminal_parent` |
| 2 | Resolve re-validates a live run in the same write transaction, `parent_decision='pending'` CAS, exact idempotent replay (`desktop_team_collaboration_resolve_conflict`), `resolved_assignment_id` bound to the run | `test_collaboration_resolve_requires_live_run_and_pending_cas` |
| 3 | `desktop_0006_report_collaboration_digest`: settle persists the canonical `(targetRoleId, question, reason)` digest; `/reports` exact replay compares it (legacy NULL rows fall back to stored request rows) | `test_report_replay_rejects_mutated_body_and_accepts_exact_match`, `test_report_replay_compares_collaboration_digest_with_canonical_order`, `test_schema_v3_has_team_tables_without_secret_columns`, `test_restart_is_idempotent_and_preserves_application_migration_record` |
| 4 | Unified six-terminal latch (`completed/succeeded` included, phase and runState) absorbing every late mutable event before any branch | `all six terminals absorb every late mutable event` (12 late event kinds per terminal) |
| 5 | First snapshot binds parked durable identity without a view gate, rejects different team run ids and older roster epochs, calibrates terminal snapshot states, and `pendingDurableTeamCancel` drives exactly one durable cancel from a single workbench effect | `first snapshot from origin A binds parked identity while viewing workspace B`, `a bound run does not rebind a different team run id`, `stop before identity binds the late snapshot and cancels exactly once`, `snapshot first then stop still dispatches durable cancel exactly once`, `a snapshot from an older roster epoch does not rebind a new run` |
| 6 | Direct IPC negatives for missing/malformed/uppercase `nodeId`/`reportId`; RuntimeHost journey schema pin moved to 6 | `IPC rejects missing, malformed, and tampered node/report identity fields`, round-2 RuntimeHost journey probe |
| 7 | Item 5 two-transaction honesty wording + INV-085 / ai-maintainer-map forward amendments + this record | this section |

One pre-existing test was forward-fixed with item 1:
`test_create_node_binds_live_run_identity_and_forbids_epoch_reuse` used to
post `/state failed` over a live node and now settles the node first, because
that old expectation was exactly the audited hole.

### Round 3 gate counts

Recorded after the fixes above. Same constraints. No Docker/WSL/PostgreSQL.
No paid keys. No installer. Root `.env` not read. PowerShell: no `&&`.
RuntimeHost optional; not faked.

```text
frontend pnpm test       = 270 passed
frontend pnpm typecheck  = passed
frontend pnpm lint       = passed
frontend pnpm build      = passed
desktop  pnpm test       = 90 passed
desktop  pnpm typecheck  = passed
desktop  pnpm build      = passed
pytest desktop_local     = 161 passed
ruff (touched Python)    = passed
Ruff format check        = passed
git diff --check         = passed
validate_maintainer_map  = passed (75 invariants, 51 modules, 1218 path specs, 3127 matched files, 358 entrypoints, 21 discovered HTTP entrypoints, 296 verification commands)
validate_maintainer_benchmark = passed (3 plans, 8 scenarios, 6 critical scenarios, 9 unsafe vetoes)
```

Publish boundary (unchanged):

```text
PAID_PROVIDER_NOT_PROVEN
AUTHENTICODE_NOT_PROVEN
EXE_MSI_REPACKAGE_NOT_APPROVED
LIVE_HUMAN_ELECTRON_WINDOW_NOT_PROVEN
ENTERPRISE_MULTI_AGENT_DISABLED
```

This round does not claim P6.9 engineering acceptance, a paid Provider
window, Authenticode, EXE/MSI, or enterprise multi-agent; it only lands the
forward-fix package the audit asked for.

---

## Round 4 P1/P2 forward-fix (after `0423bdb`; do not amend it)

Date: 2026-08-22. The Codex audit of `0423bdb` confirmed all six Round 3
fixes, their commit chain and gates, but withheld acceptance on one new P1
and two P2s: `succeeded` only proved "no live children" (an empty Run could
succeed with no plan and no answer; failed/cancelled/unknown children and
frozen pending collaborations could be declared success; recovery preserved
a disproven `succeeded`), resolve accepted contradictory decision shapes and
cross-role/cross-plan targets, and the legacy NULL-digest replay baseline
could drift through later legal collaboration writes. Five P3s were also
reported. `0423bdb` is registered above as audited-not-passed; this round
lands the blocking fixes plus the P3s in one concentrated pass.

| # | Fix | Tests that fail if the fix is deleted |
|---|---|---|
| 1 | Success closure in one `BEGIN IMMEDIATE` (`desktop_team_success_closure_open`): validated current plan, non-empty `parent_final_answer`, current-plan assignments completed (needs_collaboration only with no pending request), current-plan nodes succeeded, zero pending collaborations; `desktop_0007` recovery-only `succeeded→unknown` trigger relaxation with an explicit API terminal-transition conflict guard; recovery downgrades disproven success, keeps proven success | `test_state_succeeded_requires_success_closure`, `test_state_terminal_transition_from_terminal_is_conflict`, `test_recovery_downgrades_succeeded_without_success_proof`, `test_recovery_keeps_proven_success_intact` |
| 2 | Resolve binds decision shape (accept_start/merge_existing ⇔ assignment; handle_self/decline ⇔ NULL), target role (`assignment.employee_role_id == target_role_id`), current plan, and per-decision assignment state | `test_collaboration_resolve_binds_decision_shape_role_and_plan` |
| 3 | Legacy NULL-digest `/reports` replay fails closed (`desktop_team_report_replay_legacy_unverifiable`); duplicate `(targetRoleId, question, reason)` tuples rejected at validation (`desktop_team_collaboration_duplicate`) | `test_report_replay_legacy_null_digest_fails_closed`, `test_report_settle_rejects_duplicate_collaboration_tuples` |
| 4 | Coordinator closes pending collaborations before success: blackboard requests now carry `id`, `resolveTeamCollaboration` on the native client, `resolveCollaboration` on the host, every pending request resolved `handle_self` before both `succeeded` commits | `parent accepts collaboration and starts QA as a new validated assignment` (asserts handle_self for all), `a completed journey with no collaboration requests performs no resolves` |
| 5 | Append budget is origin-only (`desktopTeamAppendBudgetTarget`; B view no longer sends `{workspaceId: B, teamRunId: A}`); terminal first snapshot latches before a late cancelled (future recovery-emitter contract) | `append budget target is origin-only and uses the origin workspace id`, `a terminal first snapshot latches before a late cancelled event` |
| 6 | Eighth IPC negative: uppercase `reportId` | `IPC rejects missing, malformed, and tampered node/report identity fields` |
| 7 | INV-085 forward amendments (success closure, resolve binding, legacy fail-closed, duplicate set semantics, TypeScript-replica pin wording) + ai-maintainer-map schema v7 + this record | this section |

The Round 3 recovery expectation was corrected with item 1:
`test_recovery_converges_live_children_of_any_terminal_parent` pinned
"succeeded parent stays succeeded" — the audit's reproduction D showed that
as the bug — and is now `test_recovery_downgrades_succeeded_without_success_proof`.

### Round 4 gate counts

Recorded after the fixes above. Same constraints. No Docker/WSL/PostgreSQL.
No paid keys. No installer. Root `.env` not read. PowerShell: no `&&`.
RuntimeHost optional; not faked.

```text
frontend pnpm test       = 272 passed
frontend pnpm typecheck  = passed
frontend pnpm lint       = passed
frontend pnpm build      = passed
desktop  pnpm test       = 91 passed
desktop  pnpm typecheck  = passed
desktop  pnpm build      = passed
pytest desktop_local     = 167 passed
ruff (touched Python)    = passed
Ruff format check        = passed
git diff --check         = passed
validate_maintainer_map  = passed (75 invariants, 51 modules, 1218 path specs, 3127 matched files, 358 entrypoints, 21 discovered HTTP entrypoints, 296 verification commands)
validate_maintainer_benchmark = passed (3 plans, 8 scenarios, 6 critical scenarios, 9 unsafe vetoes)
```

Publish boundary (unchanged):

```text
PAID_PROVIDER_NOT_PROVEN
AUTHENTICODE_NOT_PROVEN
EXE_MSI_REPACKAGE_NOT_APPROVED
LIVE_HUMAN_ELECTRON_WINDOW_NOT_PROVEN
ENTERPRISE_MULTI_AGENT_DISABLED
```

Round 4 is a forward-fix, not an acceptance claim. P6.9 engineering
acceptance remains withheld until the next independent audit re-runs the
closure, resolve-binding and replay attacks against these commits.

---

## What Codex should review

The whole R0 slice on `cursor/p6-9-personal-multi-agent-team-r0` from
`d2a2db0` through this HEAD, including Round 1, Round 2, the Stop/reports
P1 forward-fix, the concentrated P6.9 forward-fix, and the Round 3 P1
forward-fix recorded below. Product law:
`docs/architecture/p6-9-multi-agent-planning.md`.
Do not reopen A2 as a separate drip. Do not drip a new A-only memo. Do not
announce OmniBase 1.0.0, Authenticode, EXE, or enterprise multi-agent. Round 2
did **not** close all ten items in one pass. The P1s and the concentrated
round are forward-fixed; item 1 pin match and item 5 two-transaction status
are honest; paid/EXE/live window still unproven.
