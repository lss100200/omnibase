# P6.9-A2 Codex slice acceptance package R0

**Open this file first.** It is the Cursor-produced **completed A2** dossier
for one Codex slice review. It is not a planning memo, not a fail memo, and
not P6.9-D engineering acceptance.

Codex should review **this package** and the A2 product commits below. Do not
re-derive P6.9 planning history except the PLANNED-vs-IMPLEMENTED wording
fix already applied in the first docs commit.

Cursor is **not** the acceptance authority. Cursor claims only the flags in
the status block below.

---

## Status Cursor actually claims

```text
P6_9_A2_CONTRACT_SCHEMA_IPC_COMPLETE
P6_8_SINGLE_AGENT_PATH_NOT_REGRESSED
P6_8_BASE_HEAD_D2A2DB0_UNCHANGED
PERSONAL_MULTI_AGENT_PLANNED
ENTERPRISE_MULTI_AGENT_DISABLED
REPACKAGE_NOT_APPROVED
PUSH_PR_NOT_APPROVED
```

Cursor does **not** claim:

- `PERSONAL_MULTI_AGENT_IMPLEMENTED` (reserved for P6.9-D)
- workbench team checkbox / timeline UI
- live Provider-wave coordinator (`personal-team-coordinator.ts` full B1)
- tools, Sandbox, MCP, Skills, enterprise Planner
- Alembic `0017`
- `APPROVED_FOR_LATER_RELEASE_REPACKAGE`
- push / PR / EXE / MSI / Authenticode
- OmniBase 1.0.0

---

## Coordinates

| | |
|---|---|
| Date | 2026-08-20 |
| Worktree | `E:\Agent IDE\OmniBase Worktrees\Active\p6-9-cursor-personal-team-r0` |
| Branch | `cursor/p6-9-personal-multi-agent-team-r0` |
| Base (do not amend) | `d2a2db04c0fbfc1ee5d398e40710495c388c21b4` (`d2a2db0`) |
| Product-law source | `cursor/p6-9-multi-agent-planning-r0` @ `01f9d3b` |
| P6.8 worktree | `p6-8-cursor-desktop-hardening-r0` left at `d2a2db0` |
| Codex empty pointer | `codex/p6-9-personal-multi-agent-team-r0` left at `d2a2db0` (**untouched**; do not commit on it) |
| Method | Forward-only. No amend of `d2a2db0`. No push. No PR. No EXE/MSI. Root `.env` not read. |

---

## Commit chain (base → A2)

**P6.8 HEAD (frozen):**

- `d2a2db04c0fbfc1ee5d398e40710495c388c21b4` — `style: apply baseline ruff format.`

**A2 (in order):**

1. `1f40b7aaa1cf193580d656dc6b51443ab367b28d` — `docs(p6.9): adopt product law and correct PLANNED vs IMPLEMENTED timing`
2. `13e0ab0c732197c944e6948fbeece37763a72dad` — `feat(desktop-local): add personal team schema and proposal validation`
3. `5bc924783c8f823b95ba06aa2238e59bca8ba22e` — `feat(desktop): expose closed role and team-run IPC contracts`
4. `2ece691cd56c99234888e316400cd74873a4ab10` — `test(p6.9): attack personal team proposal and persistence gates`
5. This file lands as `docs(p6.9): record P6.9-A2 contract-schema-IPC acceptance for Codex` on the same branch. Do not treat the docs SHA as a product SHA.

---

## What A2 shipped

### Schema (desktop-local SQLite v3, not Alembic)

Migration `desktop_0003_personal_agent_team` in
`backend/src/omnibase/desktop_local/schema.py`. `DESKTOP_SCHEMA_VERSION = 3`.

Tables:

- `workspace_agent_role_config`
- `team_run` (budget columns: calls 1–128, wall 1000–3600000 ms, concurrent 1–9, input/output 1–131072)
- `team_plan_revision`
- `team_assignment`
- `team_node`
- `team_collaboration_request`

Role config stores Provider id, model override, gear, thinking depth and a
verification digest. It does **not** store API Key, ciphertext, nonce, DPAPI
blob, or vault handle. One live team run per conversation
(`preparing|running|cancelling`).

### Typed contracts

Shared between backend validation and desktop/frontend IPC DTOs
(`desktop/src/shared/personal-team.ts`,
`backend/src/omnibase/desktop_local/personal_team.py`):

- `ParentTeamDecision` (`answer_directly` | `delegate`)
- `TeamWaveProposal` / `TeamAssignmentProposal`
- `ParentReplanDecision`
- `EmployeeTeamReport` / `EmployeeCollaborationRequest`
- `PersonalTeamBlackboard` read model

Parent output is a restricted structured Proposal. Host validates. Blackboard
is the collaboration surface. Specialists do not launch peers.

### Per-role Provider / model

Missing role row or `provider_id` null inherits the default Provider. Model
override reuses that Provider's credentials. Renderer/host see fingerprint
only. Role `test` records a **binding digest**, not a live LLM call.

### Closed IPC (exact names; no `ipc.invoke(arbitrary)`)

Product names:

- `agents.roles.list|get|update|test`
- `teamRuns.start|cancel|get|list|subscribe`
- extras (still closed-set): `teamRuns.submitProposal|getBlackboard|recordCollaboration`

Electron channels:

| Product | Channel |
|---|---|
| `agents.roles.list` | `omnibase:agents:roles:list` |
| `agents.roles.get` | `omnibase:agents:roles:get` |
| `agents.roles.update` | `omnibase:agents:roles:update` |
| `agents.roles.test` | `omnibase:agents:roles:test` |
| `teamRuns.start` | `omnibase:team-runs:start` |
| `teamRuns.cancel` | `omnibase:team-runs:cancel` |
| `teamRuns.get` | `omnibase:team-runs:get` |
| `teamRuns.list` | `omnibase:team-runs:list` |
| `teamRuns.submitProposal` | `omnibase:team-runs:submit-proposal` |
| `teamRuns.getBlackboard` | `omnibase:team-runs:get-blackboard` |
| `teamRuns.recordCollaboration` | `omnibase:team-runs:record-collaboration` |
| `teamRuns.subscribe` | event `omnibase:team-runs:event` |

Path: renderer → origin-checked preload → Electron main → `/desktop/v1` →
SQLite. Native control token stays main-only.

HTTP under `/desktop/v1/workspaces/{id}`:

- `GET|POST /agent-roles`, `GET|POST /agent-roles/{role_id}`, `POST .../test`
- `GET|POST /team-runs`, `GET /team-runs/{id}`, `POST .../cancel`
- `POST .../proposals`, `GET .../blackboard`, `POST .../collaboration-requests`

Thin A2 path: **submit parent proposal → validate → persist revision**.
Invalid proposals are audited (`validated=0`) and do **not** insert
`team_assignment`. No live LLM waves.

### Host validators

Closed nine specialists (`product`, `ux`, `frontend`, `backend`, `data`,
`security`, `qa`, `operations`, `docs`). Parent is not a specialist.
Unique assignment IDs. Dependencies must exist. Cycles rejected. Call /
input / output / wall budgets bounded. No cross-workspace locators. Tools
and side effects rejected. Secrets, paths and unauthorized locators rejected
in proposals and collaboration questions. Employee `directLaunch` rejected.

Representative codes: `desktop_team_unknown_role`,
`desktop_team_parent_not_specialist`, `desktop_team_duplicate_assignment_id`,
`desktop_team_missing_dependency`, `desktop_team_dependency_cycle`,
`desktop_team_tools_forbidden`, `desktop_team_cross_workspace`,
`desktop_team_infinite_budget`, `desktop_team_employee_direct_launch`,
`desktop_team_secret_or_path_forbidden`,
`desktop_team_input_budget_exceeded`, `desktop_team_output_budget_exceeded`,
`desktop_team_call_budget_exceeded`.

INV-085 `p69-personal-parent-directed-team-boundary` is recorded in
`docs/maintainers/security-invariants.md` and the maintainer maps.

---

## Attack tests (must fail without the gates)

**Python** `backend/tests/test_desktop_local_personal_team.py` (14):

| Test | Gate |
|---|---|
| `test_schema_v3_has_team_tables_without_secret_columns` | role config has no key/ciphertext/nonce/DPAPI/vault columns |
| `test_unknown_role_is_rejected` | unknown role |
| `test_parent_cannot_be_specialist` | parent not in specialist list |
| `test_duplicate_assignment_id_is_rejected` | unique assignment IDs |
| `test_missing_dependency_is_rejected` | deps exist |
| `test_dependency_cycle_is_rejected` | no cycles |
| `test_tool_request_is_rejected` | reject tools |
| `test_cross_workspace_locator_is_rejected` | no cross-workspace |
| `test_infinite_budget_is_rejected` | bounded budgets |
| `test_employee_direct_launch_is_rejected` | no peer-launch |
| `test_secret_in_collaboration_request_is_rejected` | secrets in collaboration questions |
| `test_role_config_rejects_secret_columns_and_inherits_fingerprint_only` | inherit default Provider; fingerprint only; reject `api_key` on update |
| `test_valid_parent_proposal_persists_and_illegal_proposals_do_not_create_assignments` | persist+validate; illegal plan has no assignments |
| `test_single_agent_send_path_still_works_with_team_schema` | P6.8 single-agent send still works on schema v3 |

**Desktop** `desktop/tests/personal-team-ipc.test.ts` (2):

| Test | Gate |
|---|---|
| `closed IPC catalog includes role and team-run channels and still has send` | closed catalog; `conversationSend` retained |
| `IPC rejects unknown role, infinite budget, and employee dispatch envelopes` | IPC-layer unknown role / infinite budget / `directLaunch` extra key |

Existing P6.8 frontend lifecycle/surface tests and the 85 prior
`test_desktop_local_{foundation,safety,app,provider,conversation}.py` tests
remain the single-agent regression net.

---

## Gate counts from this run (product `2ece691` + this docs commit)

Recorded 2026-08-20 on this worktree. No Docker/WSL/PostgreSQL. No paid keys.
No installer. Root `.env` not read. RuntimeHost not re-run (optional; not
faked).

```text
frontend pnpm test = 248 passed
frontend pnpm typecheck = passed
frontend pnpm lint = passed
desktop pnpm test = 47 passed
desktop pnpm typecheck = passed
backend desktop_local foundation/safety/app/provider/conversation/personal_team = 99 passed
  85 prior P6.8 desktop_local tests + 14 new personal-team tests
  python -m pytest backend/tests/test_desktop_local_*.py -q
git diff --check = passed (verified on this docs commit)
ruff check/format on touched desktop_local + those tests = passed
validate_maintainer_map.py = passed (75 invariants)
```

Desktop test count moved 45 → 47 because of the two personal-team IPC tests.
Frontend 248 includes the P6.8 single-agent suite (no workbench team UI
tests because no team UI shipped).

---

## Honest A2 boundaries

- No workbench team checkbox, roster picker, or timeline UI.
- No coordinator executing real Provider waves. A2 is
  contract / schema / IPC plus thin validate+persist.
- `PERSONAL_MULTI_AGENT_PLANNED` is still true.
- `PERSONAL_MULTI_AGENT_IMPLEMENTED` is reserved for P6.9-D.
- Next remains product-blind.
- Enterprise Planner / `MULTI_AGENT_ENABLED` remain disabled.
- P6.8 single-agent path is covered by the existing 85 desktop_local tests
  plus `test_single_agent_send_path_still_works_with_team_schema` and the
  retained `conversationSend` IPC catalog assertion.

---

## Wording Codex should not re-litigate

`PERSONAL_MULTI_AGENT_PLANNED` is **current**, not “expected after D”.
A2 replaced `P6_9_NOT_STARTED` with `P6_9_A2_CONTRACT_SCHEMA_IPC_COMPLETE`.
Keep `PLANNED` until D. Only after P6.9-D engineering acceptance may one
consider `PERSONAL_MULTI_AGENT_IMPLEMENTED`.
