# P6.8-D P1 forward-fix re-review R1

**ENGINEERING_ACCEPTANCE_NOT_APPROVED.**

User said「批准」. Independent re-review of product `8e05265` still has an **open P1** on abort **pre-arm**. This document does **not** announce engineering acceptance, P6.7 core-reliability closure, later-release repackage, `OMNIBASE_1_0_0_DISTRIBUTABLE`, Authenticode, live paid Provider, or P7 UX.

This is **R1 of the P1 forward-fix**. It does **not** rewrite `docs/reviews/p6-8-independent-review-r0.md` (`ca4f160`).

| | |
|---|---|
| Date | 2026-08-20 |
| Worktree | `E:\Agent IDE\OmniBase Worktrees\Active\p6-8-cursor-desktop-hardening-r0` |
| Branch | `cursor/p6-8-desktop-single-agent-hardening-r0` |
| Docs HEAD at review start | `7ac2623692cc835fe5a353825d3bccfe18e886c7` (`7ac2623`) |
| Product under review | `8e052656a3945f349520271b03d165aeb9aee5ce` (`8e05265`) |
| Prior independent review | `ca4f16074f492e67fe479ed5460fe54d8d2d3b13` (`ca4f160`) |
| Codex pointer | `codex/p6-8-desktop-single-agent-hardening-r0` left at `2d3b56e` (untouched) |
| Method | Four-lane parent synthesis + file:line spot-check of `8e05265`. Did not re-litigate the whole codebase. Did not change product/runtime code. Did not push. Did not pack EXE. Production build gates were not re-run. |
| Visual | Cursor Canvas `p6-8-p1-forward-fix-rereview.canvas.tsx` (workspace canvases directory; may be outside git). Prior R0 canvas left as historical. |
| Counts | **P0 = 0**. Original P1s: **2 CODE-CLOSED**, **1 remaining P1** (P1-3 pre-arm abort). |

---

## Flag table

| Flag | Verdict |
|---|---|
| `P6_8_STREAM_LIFECYCLE_ACCEPTED` | **NOT ACCEPTED** (pre-arm abort hole) |
| `P6_8_PRE_IDENTITY_CANCEL_PROVEN` | **NOT PROVEN** (**PARTIAL** — armed-stream abort works; pre-arm does not) |
| `P6_8_EVENT_GENERATION_ISOLATION_PROVEN` | **CODE-CLOSED** this re-review; **NOT LIVE-PROVEN** |
| `P6_8_CONVERSATION_SCOPE_ISOLATION_PROVEN` | list P1-1 **CODE-CLOSED**; transcript/live already held |
| `P6_8_ASYNC_PROJECTION_ORDERING_PROVEN` | create/mutation **CODE-CLOSED** for list |
| `P6_8_ARCHIVE_SCOPE_GATE_PROVEN` | **CODE-CLOSED** for list+selection |
| `P6_8_PRODUCTION_BUILD_GATES_PASSED` | **NOT RE-RUN** |
| `P6_7_R1_BACKEND_PROTOCOL_FIXES_NO_REGRESSION` | no new regression observed; not live re-proof |
| `WORKTREE_CLEAN` | **true** at review start (docs-only commit expected on top) |
| `P6_8_DESKTOP_SINGLE_AGENT_ENGINEERING_ACCEPTANCE_PASSED` | **NOT ANNOUNCED** |
| `P6_7_SINGLE_AGENT_CORE_RELIABILITY_CLOSED` | **NOT ANNOUNCED** |
| `APPROVED_FOR_LATER_RELEASE_REPACKAGE` | **NOT ANNOUNCED** |

Never announced by this re-review: `OMNIBASE_1_0_0_DISTRIBUTABLE`, Authenticode, live paid Provider, P7 UX.

---

## P1 closed / partial (3-row)

| ID | Finding | This re-review | Lane |
|---|---|---|---|
| P1-1 | Cross-workspace conversation **list** isolation | **CLOSED** in code. Create/archive/load gated on `workspaceId` + `listGeneration`. Workbench no longer prepends before the gate. Residual: tests do not mount `DesktopWorkbench`. | `be355f01` |
| P1-2 | `sendEpoch` identity membership | **CLOSED** in code. Missing `sendEpoch` no longer matches. Native/runtime stamp epoch. Unbound complete cannot bind send 2. Residual: `retiredSendEpochs` written but unread; delta/terminal membership by bound invocation id, not `sendEpoch`; no workbench mount. | `8282003f` |
| P1-3 | Hang-before-identity abort channel | **PARTIAL**. Armed-stream abort works. Durable cancel still requires `invocation_[a-f0-9]{32}`. Identity-after-Stop still cancels once. **Remaining P1:** pre-arm abort hole. | `61ab2d8a` |

---

## Remaining P1 — abort pre-arm window

`RuntimeManager.sendConversation` awaits `listProviders` + `getProviderVault` (then decrypts) **before** assigning `#streamAbort`. Concurrent `abortInFlightSend` sees a **null** controller, returns `aborted: false`, and has **no pending-abort latch**. Stop then **hides** (`cancelling`, `invocationId` null) because `STOPPABLE` does not include `cancelling`. A later hung messages POST cannot be aborted. The original hang can still happen in this pre-arm window.

Spot-check on product `8e05265` (not falsified):

- `desktop/src/runtime/runtime-manager.ts` **377–402**: `listProviders` / `getProviderVault` / decrypt run; `#streamAbort` is assigned only after that await window (`400–402`).
- Same file **450–468**: `abortInFlightSend` on `controller === null` resolves `{ ok: true, value: { aborted: false } }`. No queue / latch to abort a controller assigned later.
- `frontend/app/desktop/workbench-client.tsx` **534–548**: `stopGeneration` sets live to cancelling, shows `正在停止`, calls `abortInFlightSend`, and issues durable cancel only when `desktopInvocationCancelTarget` is non-null.
- `frontend/lib/desktop-invocation-lifecycle.ts` `STOPPABLE` = `send` / `starting_identity` / `identity` / `running` (**not** `cancelling`). After first Stop, `desktopLiveStopVisible` is false.

**What P1-3 did close (not the remaining P1):**

- Armed-stream abort: once `#streamAbort` is assigned, Stop aborts the native fetch without `invocation_[a-f0-9]{32}`.
- Durable cancel IPC still requires that id (`desktop/src/ipc.ts` `INVOCATION_ID_PATTERN`).
- Identity-after-Stop still cancels once (`bindIdentity` / `takeUnboundCancel`).

Do not rewrite the spec after the fact: deferred cancel-on-identity remains design-compliant. The remaining P1 is the **pre-arm** hole, not a claim that abort-on-click was the written machine.

---

## P1-1 CLOSED (code) — residuals

`listMutationIsCurrent` requires `mounted` + matching `workspaceId` + `listGeneration`. Workspace switch increments `listGeneration`. `applyDesktopConversationCreate` / `applyDesktopConversationArchive` no-op when the generation does not match. Workbench `createConversation` / `archiveCurrentConversation` apply the helper, then `setConversations(applied.conversations)` — they no longer prepend before the gate.

Spot-check: `frontend/lib/desktop-conversation-surface.ts` `listMutationIsCurrent` ~139–147; `applyDesktopConversationCreate` ~194–211; `applyDesktopConversationArchive` ~214–221; `frontend/app/desktop/workbench-client.tsx` create ~333–357 (gate inside apply, then set).

Residual: frontend tests still do not mount `DesktopWorkbench`. Helper-level B11/B12 would fail the old un-gated contract; that is not live-window proof. Record `P6_8_CONVERSATION_SCOPE_ISOLATION_PROVEN` / archive / create-mutation flags as **CODE-CLOSED**, **NOT LIVE-PROVEN**.

---

## P1-2 CLOSED (code) — residuals

`eventMatchesSendEpoch` is `event.sendEpoch !== undefined && event.sendEpoch === state.sendEpoch`. Native `stampSendEpoch` and `RuntimeManager` emit stamp the owning send. Completing an unbound send writes `retiredSendEpochs` and remembers an unbound cancel token so a late origin identity cannot bind send N+1; if Stop was requested, one precise cancel still fires.

Spot-check: `frontend/lib/desktop-invocation-lifecycle.ts` `eventMatchesSendEpoch` ~195–199; `completeDesktopLiveSend` ~422–450; `bindIdentity` ~494; `desktop/src/runtime/native-client.ts` `stampSendEpoch` ~820–826; `desktop/src/runtime/runtime-manager.ts` `emitWithEpoch` ~403–408.

Residuals (not remaining P1s):

- `retiredSendEpochs` is **written** (`retireSendEpoch` on complete) and **unread** for membership. Identity-after-Stop uses `unboundCancelTokens`.
- Delta / terminal membership is by **bound invocation id**, not `sendEpoch` (`applyDelta` ~527–529; `applyTerminal` ~546–553). Identity membership is the epoch gate.
- Tests still do not mount `DesktopWorkbench`.

Record `P6_8_EVENT_GENERATION_ISOLATION_PROVEN` as **CODE-CLOSED this re-review; NOT LIVE-PROVEN**.

---

## Four-lane attribution

Do **not** cite any lane as engineering acceptance.

### P1-1 list isolation — CLOSED (`be355f01`)

Create/archive/load gated on `workspaceId` + `listGeneration`. Workbench no longer prepends before the gate. Residual: tests do not mount `DesktopWorkbench`.

### P1-2 sendEpoch — CLOSED (`8282003f`)

Missing `sendEpoch` no longer matches. Native/runtime stamp epoch. Unbound complete cannot bind send 2. Durable cancel validation unchanged. Residuals as above.

### P1-3 abort channel — PARTIAL, remaining P1 (`61ab2d8a`)

Armed-stream abort works. Durable cancel still requires `invocation_[a-f0-9]{32}`. Identity-after-Stop still cancels once. **Remaining P1:** pre-arm `#streamAbort` assignment after provider/vault awaits; `abortInFlightSend` returns `aborted: false`; Stop hides; hung POST cannot be aborted.

Parent verdict **follows this dedicated lane**.

### Security Review — 3 P1s CLOSED in code; no new medium+ (`1804c9f8`)

**Discount** unrelated P6.4 / RAG / Agent Alpha noise in its supplied diff.

**Keep:** vault not on preload; Next health-only; R1 pin/CAS present; abort vs durable cancel split; `sendEpoch` stamp.

It did **not** catch the P1-3 pre-arm hole. Parent verdict follows the dedicated P1-3 lane, not this lane’s “3 P1s CLOSED” summary.

---

## Explicit non-acceptance

`P6_8_DESKTOP_SINGLE_AGENT_ENGINEERING_ACCEPTANCE_PASSED` is **not announced**.

`P6_8_STREAM_LIFECYCLE_ACCEPTED` is **NOT ACCEPTED** because the pre-arm abort hole remains a P1. `P6_8_PRE_IDENTITY_CANCEL_PROVEN` is **NOT PROVEN** (PARTIAL).

```text
ENGINEERING_ACCEPTANCE_NOT_APPROVED
REMAINING_P1_ABORT_PRE_ARM
P6_8_STREAM_LIFECYCLE_ACCEPTED = NOT ACCEPTED
P6_8_PRE_IDENTITY_CANCEL_PROVEN = NOT PROVEN (PARTIAL)
P6_8_DESKTOP_SINGLE_AGENT_ENGINEERING_ACCEPTANCE_PASSED = NOT ANNOUNCED
P6_7_SINGLE_AGENT_CORE_RELIABILITY_CLOSED = NOT ANNOUNCED
APPROVED_FOR_LATER_RELEASE_REPACKAGE = NOT ANNOUNCED
OMNIBASE_1_0_0 / Authenticode / live paid Provider / P7 UX = not announced
codex/p6-8-desktop-single-agent-hardening-r0 left at 2d3b56e
no product/runtime code changed by this re-review
no push; no PR; no EXE pack
```
