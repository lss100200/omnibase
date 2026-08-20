# P6.8 Codex global acceptance package R0

**Open this file first.** It is the Cursor-produced dossier for **one Codex global acceptance review**. It is not another independent-review report, not a request to re-derive Cursor P1s, and not engineering acceptance.

Cursor is **not** the acceptance authority. Codex sets or refuses `P6_8_DESKTOP_SINGLE_AGENT_ENGINEERING_ACCEPTANCE_PASSED`. Cursor claims only the flags in the status block below.

Historical Cursor reviews remain on disk and must not be the starting point:

- `docs/reviews/p6-8-independent-review-r0.md` (`ca4f160`) — original four-lane review
- `docs/reviews/p6-8-p1-forward-fix-rereview-r0.md` (`ca7f619`) — re-review that left the abort **pre-arm** hole open; that hole is closed in product `70c99f2`

---

## Status Cursor actually claims

```text
P6_8_CURSOR_P1_FORWARD_FIXES_COMPLETE
P6_8_ABORT_PRE_ARM_LATCH_IMPLEMENTED
P6_8_READY_FOR_CODEX_GLOBAL_ACCEPTANCE_REVIEW
ENGINEERING_ACCEPTANCE_RESERVED_FOR_CODEX
REPACKAGE_NOT_APPROVED
PUSH_PR_NOT_APPROVED
CURRENT_UNSIGNED_INSTALLER_STALE
```

Cursor does **not** claim:

- `P6_8_DESKTOP_SINGLE_AGENT_ENGINEERING_ACCEPTANCE_PASSED`
- `P6_7_SINGLE_AGENT_CORE_RELIABILITY_CLOSED`
- `APPROVED_FOR_LATER_RELEASE_REPACKAGE`
- `OMNIBASE_1_0_0` / `OMNIBASE_1_0_0_DISTRIBUTABLE`
- Authenticode
- live paid Provider
- P7 UX

---

## Coordinates

| | |
|---|---|
| Date | 2026-08-20 |
| Worktree | `E:\Agent IDE\OmniBase Worktrees\Active\p6-8-cursor-desktop-hardening-r0` |
| Branch | `cursor/p6-8-desktop-single-agent-hardening-r0` |
| Product HEAD for this package | `70c99f2f052ce592a337088a371095ff8c4345fc` (`70c99f2`) |
| Codex pointer | `codex/p6-8-desktop-single-agent-hardening-r0` left at `2d3b56e56721cbc450cd664eca052b74a7bfe95c` (**untouched**; do not commit on it) |
| Method | Forward-only product latch + unit/gate re-run. No amend of `ca7f619` / `8e05265` / `385123b`. No push. No PR. No EXE/MSI. Root `.env` not read. |

---

## Commit chain (base → product → docs)

**R2 base (frozen):**

- `2d3b56e56721cbc450cd664eca052b74a7bfe95c` — `fix(p6.7): keep Stop and live identity across desktop scope return.`

**P6.8 product (in order):**

- `99c54afd8577c97784468d53a8d5f38708b468b1` — `fix(p6.8): close pre-identity cancel and stream event races`
- `467a4f59246c6a84fcc9b366b84cc968d6825a67` — `fix(p6.8): fence desktop conversation projections and mutations`
- `8e052656a3945f349520271b03d165aeb9aee5ce` — `fix(p6.8): isolate list mutations and bind identity to send epoch`
- `70c99f2f052ce592a337088a371095ff8c4345fc` — `fix(p6.8): latch abort before provider vault so Stop cannot miss the stream` **(this latch)**

**Docs (history, listed separately; not product):**

- `385123be33bc1d3a35f459bd0174df7047051bc2` — desktop single-agent reliability closure record
- `ca4f16074f492e67fe479ed5460fe54d8d2d3b13` — four-lane independent review
- `7ac2623692cc835fe5a353825d3bccfe18e886c7` — P1 forward-fix pending re-review
- `ca7f619b862cdbf605b9fff990180829c317ca7d` — P1 re-review; abort pre-arm still open
- This file lands as the following docs commit on the same branch (`docs(p6.8): package desktop reliability closure for Codex global acceptance`). Do not treat the docs SHA as a product SHA.

---

## What P6.8-A / B / C intended

P6.8 productionizes P6.7 under races, workspace switching, failure, and production-code gates. It is not a product UX, file-tree, installer, Authenticode, or 1.0.0 phase.

- **P6.8-A (stream lifecycle).** One personal invocation is a finite state: `idle → send → starting_identity → identity → running → Stop → cancelling → cancelled|terminal → convergence → idle`. `sendEpoch` owns identity membership. Origin workspace/conversation is frozen for the send. Identity binds once. Stop during `starting_identity` must remain reachable, keep Send/Retry blocked until the send Promise converges, show `正在停止` until idle, and fire **exactly one** precise durable cancel (`cancelDispatched`) if identity later arrives for that origin+sendEpoch. Hang-before-identity must still abort the in-flight native fetch without an `invocation_[a-f0-9]{32}`. Durable `conversations.cancel` remains a separate channel that **requires** that id.
- **P6.8-B (conversation surface).** Switching conversation/workspace isolates the old transcript immediately. Live text/meta compare origin to the current view at render time. Create, archive, workspace load, detail, send, and retry completions are gated so a late response cannot paint the wrong workspace list or selection.
- **P6.8-C (production-code gates).** Re-run the production TypeScript/Python/.NET unit and type gates on this worktree. No EXE/MSI/Burn. No paid Provider.

---

## Former P1s — how each was closed (file:line)

These were the Cursor-found P1s. They are **code-closed and unit-locked** on `70c99f2`. Codex should verify this closure, not hunt the same holes from scratch. Residuals that need a live window or installer are listed under evidence gaps, not as open Cursor P1s.

### P1-1 — cross-workspace conversation list isolation — CODE-CLOSED (`8e05265`)

Create/archive list writes require matching `workspaceId` + `listGeneration`. Workspace load requires matching `workspaceId` + `workspaceLoadEpoch`. Workspace switch increments `listGeneration`. Workbench applies the helper, then `setConversations(applied.conversations)` — it does not prepend before the gate.

| Location | Lines |
|---|---|
| `frontend/lib/desktop-conversation-surface.ts` `listMutationIsCurrent` | 138–147 |
| same file `applyDesktopConversationCreate` | 194–211 |
| same file `applyDesktopConversationArchive` | 214–221 |
| same file `applyDesktopWorkspaceLoad` (`workspaceId` + `workspaceLoadEpoch`) | 173–191 |
| `frontend/app/desktop/workbench-client.tsx` `createConversation` (gate inside apply, then set) | 333–357 |

### P1-2 — `sendEpoch` identity membership — CODE-CLOSED (`8e05265`)

Missing `sendEpoch` is not a wildcard. Native emit and RuntimeManager stamp the owning send. Completing an unbound send retires that epoch and remembers an unbound cancel token so a late origin identity cannot bind send N+1; if Stop was requested, one precise cancel still fires.

| Location | Lines |
|---|---|
| `frontend/lib/desktop-invocation-lifecycle.ts` `eventMatchesSendEpoch` | 195–199 |
| same file `completeDesktopLiveSend` (retire epoch + unbound cancel token) | 425–454 |
| same file `bindIdentity` / `takeUnboundCancel` (exactly one `cancelDispatched`) | 459–523 |
| `desktop/src/runtime/native-client.ts` `stampSendEpoch` | 820–826 |
| `desktop/src/runtime/runtime-manager.ts` `emitWithEpoch` | 468–473 |

### P1-3 — hang-before-identity abort channel + **pre-arm latch** — CODE-CLOSED (`99c54af` abort channel, `70c99f2` latch)

Armed-stream abort was already present: Stop aborts the native fetch without an invocation id; durable cancel still requires `invocation_[a-f0-9]{32}`; identity-after-Stop still cancels once.

The remaining hole on `8e05265` was **pre-arm**: `sendConversation` awaited `listProviders` + `getProviderVault` before assigning `#streamAbort`. Concurrent `abortInFlightSend` saw `controller === null`, returned `{ aborted: false }`, and had no latch. Workbench moved to `cancelling` and hid Stop. A later hung messages POST could not be aborted.

`70c99f2` closes that hole:

1. `#armStreamAbort()` runs **before** any provider/vault await (`sendConversation` 422).
2. `abortInFlightSend` / `#requestStreamAbort` abort a live controller, or set `#pendingAbort` while `#sendInFlight` and no controller yet, and return `{ aborted: true }` in those cases (514–522, 678–687). Idle abort still returns `{ aborted: false }` and does not poison the next send.
3. `listProviders` / `getProviderVault` are raced against the abort signal (`raceAbort` 65–91). A hung provider or vault await still settles the send Promise as a local cancelled result; the messages POST is skipped.
4. If abort arrives after vault but before/during POST, the same controller is passed into `client.sendConversation`.
5. Workbench does not treat `{ aborted: false }` as success: it retries abort on a microtask, and Stop stays visible while `cancelling` and `invocationId === null`.

| Location | Lines |
|---|---|
| `desktop/src/runtime/runtime-manager.ts` `raceAbort` | 65–91 |
| same file `sendConversation` arm-before-await | 413–487 |
| same file `abortInFlightSend` | 514–522 |
| same file `#armStreamAbort` / `#pendingAbort` | 660–687 |
| `desktop/src/ipc.ts` durable cancel requires `invocation_[a-f0-9]{32}` | 118, 429–441, 628–638 |
| same file abort-in-flight is a separate no-arg channel | 641–647 |
| `frontend/lib/desktop-invocation-lifecycle.ts` Stop visible while unbound cancelling | 284–292 |
| `frontend/app/desktop/workbench-client.tsx` `stopGeneration` (do not ignore `aborted: false`) | 534–558 |

---

## Tests that lock those races

**Must fail without pre-arm `#armStreamAbort` before listProviders/getProviderVault + `raceAbort`** (new in `70c99f2`):

- `abortInFlightSend during getProviderVault prevents later messages fetch and settles send` — abort while vault is pending; `{ aborted: true }`; messages POST never starts; send Promise settles cancelled with `sendEpoch`.
- `abortInFlightSend during hung listProviders settles send without starting messages fetch` — Stop-before-identity cannot miss a hung provider lookup; send Promise still settles.

`#pendingAbort` is defensive and untested. These tests do not lock that field.

**Idle abort remains safe:**

- `idle abortInFlightSend does not latch and poison the next send`
- `abortInFlightSend without a live stream does not require an invocation id` (existing; still `{ aborted: false }`)

**Durable cancel still rejects missing/invalid invocation id; abort-in-flight stays no-arg:**

- `abort-in-flight send does not require an invocation id; durable cancel still does`

**Identity after unbound Stop still cancels exactly once (must keep passing):**

- `P6.8-A1 send then Stop before identity then identity cancels exactly once`
- `P6.8-A identity after unbound Stop still cancels exactly once`
- `P6.8-A Stop before identity then aborted send Promise returns to idle` (Stop stays visible while cancelling+unbound; hidden after idle)
- `abort before identity does not call invocation cancel and stamps sendEpoch`
- `native stream identity events carry the sendEpoch from the owning send`
- `P6.8-A omitted sendEpoch identity cannot bind a newer pending send`
- `P6.8-A unbound complete then late identity cannot bind send 2`

**List isolation (P1-1):**

- `P6.8-B11 late create on A does not mutate workspace B list`
- `P6.8-B12 late archive on workspace A does not mutate workspace B list or selection`
- `P6.8-B overlapping create and archive on the current workspace both apply`
- `P6.8-B late workspace A load does not replace workspace B list`

Frontend tests still do not mount `DesktopWorkbench`. That is recorded as an evidence gap, not as an open P1 for Cursor to re-report.

---

## Gate counts from this run (product `70c99f2`)

Recorded 2026-08-20 on this worktree. No Docker/WSL/PostgreSQL. No paid keys. No installer. Root `.env` not read.

```text
frontend pnpm test = 245 passed
frontend pnpm typecheck = passed
frontend pnpm lint = passed
frontend pnpm build = passed
desktop pnpm test = 45 passed
desktop pnpm typecheck = passed
desktop pnpm build = passed
backend desktop_local foundation/safety/app/provider/conversation = 85 passed
  python -m pytest backend/tests/test_desktop_local_{foundation,safety,app,provider,conversation}.py -q
git diff --check = passed
RuntimeHost = 24/24 passed
  pinned SDK = C:\Users\Administrator\AppData\Local\OmniBaseBuildTools\dotnet-sdk-8.0.424\dotnet.exe
  command = dotnet run --project packaging/windows/OmniBase.RuntimeHost.Tests/OmniBase.RuntimeHost.Tests.csproj -c Release
  --nologo was not passed
```

Desktop test count moved 42 → 45 because of the three latch tests above.

---

## Evidence gaps (out of Cursor unit scope)

These belong in **Codex global review and/or a later RC**. They are not “Cursor report failed, please re-find P1s.” Cursor unit tests cannot close them.

- **Live Electron window / human send-stop.** Reducer, RuntimeManager, and native-client tests are not a production-mode window against a disposable app-data directory. Human Send then Stop (before and after identity) is unproven live.
- **Vault after process restart (live).** Secret-vault unit tests exist; an Electron process restart that still decrypts the same Provider is unproven live.
- **Hang-cancel TestClient SSE** remains historically open from P6.7 (incremental SSE cancel through the backend TestClient path).
- **Unsigned installer is stale.** Current on-disk EXE/MSI (if any) does not include P6.8 product commits. No Authenticode. No `1.0.0` / `OMNIBASE_1_0_0_DISTRIBUTABLE` claim. Repackage is not approved.
- **No paid Provider in this package.** Loopback / fake OpenAI-compatible coverage only.

---

## What Codex should do in the global review

1. Start here. Treat prior Cursor review files as history of how the holes were found and closed.
2. Confirm the four product commits on top of R2 `2d3b56e`, especially latch `70c99f2`.
3. Decide whether unit-closed P1-1 / P1-2 / P1-3 plus the gate table above are enough for `P6_8_DESKTOP_SINGLE_AGENT_ENGINEERING_ACCEPTANCE_PASSED`, or refuse because live-window / vault-restart / hang-cancel SSE / installer gaps remain.
4. Leave `codex/p6-8-desktop-single-agent-hardening-r0` at `2d3b56e` unless Codex itself chooses to move it.
5. Do not announce `OMNIBASE_1_0_0`, Authenticode, live paid Provider, P7 UX, push, PR, or repackage unless Codex separately approves those.

```text
ENGINEERING_ACCEPTANCE_RESERVED_FOR_CODEX
P6_8_DESKTOP_SINGLE_AGENT_ENGINEERING_ACCEPTANCE_PASSED = NOT CLAIMED BY CURSOR
codex/p6-8-desktop-single-agent-hardening-r0 left at 2d3b56e
no push; no PR; no EXE/MSI
root .env not read; business database not accessed or migrated
```
