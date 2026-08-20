# P6.8-D independent reliability review R0

**ENGINEERING_ACCEPTANCE_NOT_APPROVED.**

No P0. Independent review **refuses** P6.8 completion flags that require lifecycle, epoch, or conversation-list isolation proof. This document does not announce engineering acceptance, P6.7 core-reliability closure, later-release repackage, `OMNIBASE_1_0_0_DISTRIBUTABLE`, Authenticode, live paid Provider, or P7 UX.

Reducer 10/10 on A1–A10 and B1–B10 is **not** workbench, Electron, or native-abort proof and does **not** authorize acceptance.

| | |
|---|---|
| Date | 2026-08-20 |
| Worktree | `E:\Agent IDE\OmniBase Worktrees\Active\p6-8-cursor-desktop-hardening-r0` |
| Branch | `cursor/p6-8-desktop-single-agent-hardening-r0` |
| Reviewed HEAD | `385123be33bc1d3a35f459bd0174df7047051bc2` (`385123b`) |
| Codex pointer | `codex/p6-8-desktop-single-agent-hardening-r0` left at `2d3b56e` (untouched) |
| Method | Four-lane parent synthesis + 30-second file:line spot-check. Did not re-litigate the whole codebase. Did not change product/runtime code. Did not push. Did not pack EXE. Production build gates were not re-run. |
| Visual | Cursor Canvas `p6-8-independent-review-r0.canvas.tsx` (workspace canvases directory; may be outside git) |
| Counts | **P0 = 0**, **P1 = 3**, **P2 = 7** |

---

## Flag table

| Flag | Verdict |
|---|---|
| `P6_8_STREAM_LIFECYCLE_ACCEPTED` | **NOT ACCEPTED** (hang-before-identity; FSM/epoch holes) |
| `P6_8_PRE_IDENTITY_CANCEL_PROVEN` | **NOT PROVEN** |
| `P6_8_EVENT_GENERATION_ISOLATION_PROVEN` | **NOT PROVEN** (sendEpoch no-op; unbound rebind) |
| `P6_8_CONVERSATION_SCOPE_ISOLATION_PROVEN` | **PARTIAL** (transcript/live yes; sidebar list no) |
| `P6_8_ASYNC_PROJECTION_ORDERING_PROVEN` | **PARTIAL** (detail/send/retry yes; create/mutation no) |
| `P6_8_ARCHIVE_SCOPE_GATE_PROVEN` | **PARTIAL** (selection stays; list not workspace-bound) |
| `P6_8_PRODUCTION_BUILD_GATES_PASSED` | **IMPLEMENTER-CLAIMED, NOT RE-RUN BY THIS REVIEW** |
| `P6_7_R1_BACKEND_PROTOCOL_FIXES_NO_REGRESSION` | **NO DIFF** vs `ebb211d` in backend/Electron (Lane 1 git; this review confirmed empty `git diff --stat` on `backend`, `desktop`, `packaging`). Not a live re-proof. |
| `WORKTREE_CLEAN` | **true** at review start |
| `P6_8_DESKTOP_SINGLE_AGENT_ENGINEERING_ACCEPTANCE_PASSED` | **NOT ANNOUNCED** |
| `P6_7_SINGLE_AGENT_CORE_RELIABILITY_CLOSED` | **NOT ANNOUNCED** |
| `APPROVED_FOR_LATER_RELEASE_REPACKAGE` | **NOT ANNOUNCED** |

Never announced by this review: `OMNIBASE_1_0_0_DISTRIBUTABLE`, Authenticode, live paid Provider, P7 UX.

---

## Consensus findings (severity-ordered, merged, no duplicates)

### P1 (code)

#### P1-1. Cross-workspace conversation **list** corruption

Create writes the sidebar before the scope gate. Archive / `mutationEpoch` is not workspace-bound. A late create or archive can overwrite another workspace’s conversation list.

Spot-check on this HEAD (not falsified):

- `frontend/app/desktop/workbench-client.tsx` ~326, ~340–346: `createConversation` prepends and `setConversations` **before** `desktopSurfaceProjectionIsCurrent`.
- Same file ~424: archive always `setConversations(applied.conversations)`.
- `frontend/lib/desktop-conversation-surface.ts` ~151–164: `applyDesktopConversationArchive` still writes `conversations` when the epoch is stale **or** the view is not the archived id.
- `selectDesktopConversation` bumps `detailRequestEpoch` only, not `mutationEpoch`, and does not clear conversations by workspace.

Lanes: 1 + B.

#### P1-2. Event generation isolation hole

The written P6.8-A contract required epoch membership (`sendEpoch` plus bound invocation id). Production identity membership is origin workspace + conversation. `sendEpoch` is a no-op on the real event path: `eventMatchesSendEpoch` is true when `event.sendEpoch === undefined`, and native `parseStreamEvent` never sets `sendEpoch`. Unbound invocation IDs are never retired. Late identity can bind a newer send after `completeDesktopLiveSend` with a null id. A6 only covers already-bound then retired ids.

Spot-check on this HEAD (not falsified):

- `frontend/lib/desktop-invocation-lifecycle.ts` ~154–158 (`eventMatchesSendEpoch`); ~292–297 (`beginDesktopLiveSend` refuses `cancelling` / `convergence` but **not** `starting_identity` / `running`).
- `desktop/src/runtime/native-client.ts` `parseStreamEvent` ~741–817: no `sendEpoch` field on identity/delta/terminal objects.

Lanes: A (primary; Lane 1 listed this as P2 — **elevated to P1** because the written P6.8-A contract required epoch membership).

#### P1-3. Hang-before-identity: no abort channel

Stop cannot abort the native fetch and cannot issue cancel IPC without `invocation_[a-f0-9]{32}`. `#streamAbort.abort()` exists only inside `cancelConversation`. If identity never arrives (hung TTFB / no-identity), the Provider may keep running while Send stays disabled in `cancelling`.

Spot-check on this HEAD (not falsified): `frontend/app/desktop/workbench-client.tsx` ~507–520.

**Spec nuance (must be recorded honestly):** P6.8-A written design is **deferred cancel-on-identity** (keep receiving origin identity, then cancel once). Immediate abort-on-Stop was **not** in the written state machine. Do not pretend the spec required abort-on-click. Still: the invariant “must never: no cancel sent, Provider still running, Send already re-enabled” **fails** if identity never arrives. Report as **design-compliant deferred cancel** + **P1 hang-before-identity / no abort channel**. `P6_8_PRE_IDENTITY_CANCEL_PROVEN` stays **NOT PROVEN** because native abort/IPC is unproven and the hang path is real.

Lanes: A P1 + Security Review medium (P6.8 overlap only) + Lane 1 P2.

### P2

1. Send/Retry double-submit race (`beginDesktopLiveSend` does not refuse `starting_identity` / `running`).
2. `liveRef` rewind (written from render and IPC; a stale render can rewind identity / `cancelDispatched`).
3. `正在停止` banner not cleared if synthetic cancel is a `send()` result rather than an event.
4. A9 rewrite uses local `cancelRequested`, not IPC `accepted`.
5. FSM leftover booleans; machine never assigns `send` / `identity` phases.
6. One global `mutationEpoch` can drop an earlier successful create.
7. Tests never mount `DesktopWorkbench` (implementer A/B 10/10 overclaim). A1–A3/A6/A9 fail the required UI+native+epoch bar; A4/A5/A7/A8/A10 pass reducer with evidence gaps. B7 view-stays-B passes; list isolation fails. Pre-identity terminal is dropped when `invocationId` is null.

### Evidence gaps

- Live Electron production-mode window journey (no installer, no paid key).
- Human send/stop after identity in a real desktop window.
- Vault usable after an Electron process restart.
- Hang-cancel through TestClient incremental SSE (still unproven from P6.7).
- Stale unsigned installer.
- Production build gates: implementer-claimed; **not re-run by this review**.

Security lane RAG / P6.4 practice / Agent Alpha / changesets are **out of scope noise** and are not P6.8 evidence.

### What holds

- Click B first-render live/transcript isolation is wired on the real paint path (`desktopInvocationLiveProjection` in `workbench-client.tsx` ~195, not waiting for `useEffect`).
- Detail / send / retry epoch gates are present (create / mutation fences are not workspace-bound).
- R1 backend/Electron files unchanged vs `ebb211d` (Lane 1 git; this review empty `--stat` on `backend`, `desktop`, `packaging`). DNS pin, terminal proof, disconnect `abandon_if_running`, cancel CAS still present. Not a live re-proof.
- Vault: no renderer vault IPC on preload (code). Not proven after process restart.
- Next desktop proxy still product-blind / health-only (code). Next hardening noted by Lane 1: allowlist `GET /health/ready`.
- Workbench **does** call the new FSM + surface helpers (not dead code). 10+10 tests still never mount `DesktopWorkbench`.

---

## Four-lane attribution

Do **not** cite any lane as engineering acceptance.

### Lane 1 — overall wiring

No P0. Workbench does call FSM + surface helpers. 10+10 tests never mount `DesktopWorkbench`.

- **P1:** Mutation fence is not workspace-bound (create/archive list overwrite). Feeds consensus P1-1.
- **P2:** `sendEpoch` isolation dead on real event path; Send/Retry gate racy; `liveRef` from render and IPC; pre-identity Stop does not abort native fetch until identity. `sendEpoch` elevated to consensus P1-2; hang path folded into P1-3.
- **R1:** `git diff ebb211d HEAD` is frontend + docs only. Confirmed here as empty `--stat` on backend/desktop/packaging.

### Lane A — Stop / identity

No P0. Four P1s, all retained:

1. Pre-identity Stop never aborts native stream or cancel IPC.
2. Production identity membership is origin workspace+conversation; `sendEpoch` is a no-op.
3. Unbound invocation IDs never retired; late identity can bind a newer send.
4. A1–A10 are pure reducer tests, not workbench/Electron. Handover overclaims.

A1–A3/A6/A9 fail as required (UI+native+epoch). A4/A5/A7/A8/A10 pass reducer, with evidence gaps.

### Lane B — conversation surface

Transcript + live projection isolation on Click B **holds on the real paint path**. B1–B6, B8–B9 pass in workbench source. B7 view-stays-B passes; **list isolation fails**.

- **P1:** In-flight create writes sidebar before scope gate. Feeds consensus P1-1.
- **P2:** Archive list not workspace-bound; global `mutationEpoch` drops first create; B tests never mount workbench.

### Lane 4 — Security Review (discount scope error)

This lane mixed **unrelated P6.4 practice / Agent Alpha RAG / changesets** that are not the P6.8 diff. **Do not treat RAG/practice findings as P6.8 evidence.**

Kept overlapping P6.8 items only:

- Vault isolation pass (code).
- Next product-blind pass (code).
- Pre-identity Stop does not abort native SSE (medium; agrees with Lane A).
- `sendEpoch` optional match caveat (agrees with Lane A / Lane 1).
- R1 vs `ebb211d` was **not** verified by this lane; Lane 1 did verify no-diff; this review confirmed empty `--stat`.

---

## Explicit non-acceptance

`P6_8_DESKTOP_SINGLE_AGENT_ENGINEERING_ACCEPTANCE_PASSED` is **not announced**.

Implementer language that A1–A10 and B1–B10 “passed” therefore P6.8 is closed is **not current status**. Those unit results remain a record of reducer/helper tests only.
