# P7.0 Editor-First Desktop Workbench

Status: **design baseline approved; Wave 1 source UI accepted at
`11895e9`; Windows release evidence not yet accepted**.

This document is the product law for the P7.0 desktop workbench. It supersedes
the old roadmap use of "Phase 7" as only an open-source-preparation bucket.
Open-source and community preparation remain later release-readiness work; P7.0
now means the bounded desktop product milestone recorded here.

P7.0 has four Owner-approved goals:

1. replace the practical P6 workbench surface with a polished, minimal,
   editor-first IDE;
2. carry the accepted P6.8 and P6.9 desktop capabilities into that shell;
3. build a new Windows EXE only after the source UI passes review; and
4. install and exercise that package in an explicitly disposable Windows
   environment before making any release claim.

The binding product direction is:

> 中央区域首先是可工作的 Code / Diff / Artifact 编辑与审阅空间；AI 的实时 SSE
> 反馈、工具过程和最终结果位于右侧协作面板。它是 AI 原生 IDE，不是聊天应用套壳，
> 也不是宣传仪表盘。

## 1. Information architecture

The first viewport is a stable IDE shell:

- a compact title bar with the OmniBase logo, Workspace identity and window
  controls;
- a narrow activity rail;
- one mutually exclusive primary sidebar for Explorer, Source Control, Agents,
  or Plan / Blackboard;
- a central editor group with Code, Diff and Artifact tabs;
- a secondary Agent panel on the right for live SSE and bounded Agent state;
- a bottom panel for Terminal, Problems, Output and Agent Log, collapsed by
  default; and
- a status bar for trustworthy connection, scope and task state.

Provider administration, security prose, audit history, dependency graphs and
team overview must not all remain visible in the first viewport. They belong in
focused views, drawers or commands. Repeated cards and marketing composition
are not the workbench layout.

The central editor remains primary even while an Agent is streaming. The Agent
panel may be resized or collapsed, but live Stop must remain reachable for an
active invocation. Narrow layouts may replace simultaneous panels with explicit
tabs or drawers; content must not overlap or silently disappear.

## 2. AI interaction law

The right panel exposes real-time AI behavior without turning the workbench into
a chat wrapper. It may show:

- the current parent invocation and verified Provider/model identity;
- streaming text deltas from the existing SSE lifecycle;
- bounded phase, plan, assignment and employee activity projections;
- blackboard collaboration requests and their real resolution state;
- Stop, retry and other actions already admitted by the native contracts; and
- the durable final answer or explicit failed, cancelled, unknown,
  budget-exhausted or cannot-complete terminal state.

It must not invent typing indicators, tool calls, employees, plans, token usage,
test results or success. A missing trusted projection is an unavailable, empty
or disabled state. The renderer must continue to obey the existing
workspace/conversation epochs, terminal latch, native IPC and SQLite truth.

## 3. Trusted data and unavailable features

Wave 1 is a UI remapping over the existing bridge and projection contracts. It
does not authorize a new backend protocol, SQLite migration, Next-to-native
bypass, renderer credential, direct filesystem access or direct Provider
request.

Explorer, Code, Diff, Artifact and Terminal content may render only when an
existing trusted source supplies it. If the current desktop contracts do not
provide a file tree, editor buffer, diff or terminal stream, the corresponding
command must be hidden or disabled, or show an honest unavailable state. The
approved prototype's sample paths, code, diff, events and test badges are visual
fixtures only and must never be copied into production state.

P6.8 single-parent send / Stop / retry / request-epoch behavior and P6.9
proposal, reservation, parent-call proof, budget, collaboration, success and
recovery laws remain authoritative. Read INV-084 and INV-085 before changing
their projections.

## 4. Visual system

The approved fixed theme is deep black with soft violet and pink-white:

| Token | Approved reference |
|---|---|
| application background | `#100f13` |
| chrome | `#19171d` |
| sidebar | `#151419` |
| editor | `#0d0d11` |
| selected surface | `#2b2638` |
| main text | `#f4eef7` |
| muted text | `#b9afbd` |
| accent violet | `#b8a8ed` |
| accent pink | `#efb8d2` |
| semantic success | `#69c5a3` |

Green is semantic only: success, a passing test or an added diff line. It must
not become the shell's dominant theme. Typography must remain compact and
work-focused, with stable control dimensions and no viewport-width font
scaling. Existing project icon libraries should be used instead of ad hoc SVG
controls.

The approved local visual reference is currently:

`C:\Users\Administrator\.codex\visualizations\2026\08\23\01a02f44-a9df-7f73-b0c9-5930ba8234d8\p7-editor-first-workbench.html`

That absolute path is a review aid, not a runtime dependency. Production must
not load assets from it.

## 5. OmniBase and OMNIA assets

The title bar uses the Owner-provided OmniBase logo. The approved local source
is:

`C:\Users\Administrator\Downloads\file_0000000091dc82099b75c189316341ac.png`

The implementation must copy an approved, optimized derivative into a tracked
application asset path. It must not depend on Downloads or the visualization
directory at runtime.

The workbench also reserves a bounded OMNIA companion interface. Approved local
state art is under:

`E:\Agent IDE\OSelf\assets\states-r0`

The current closed visual state set is `idle`, `running`, `thinking`,
`review-required`, `blocked`, `completed`, `sleeping`, `surprised` and
`goodbye`. Wave 1 may copy the needed optimized assets into the application and
add an adapter over real workbench state. It must support expand and minimize,
must not obstruct editor controls, and must fall back to `idle` or an explicit
offline state when no trustworthy runtime signal exists. Art alone grants no
new runtime capability.

## 6. Delivery waves

### Wave 1 - reviewed source UI

Wave 1 may change the desktop/frontend UI shell, map already admitted actions
and projections, add tracked visual assets and focused tests. It must stop
before commit, push or packaging for independent review. Acceptance requires at
least frontend test/typecheck/lint/build, desktop test/typecheck/build and
`git diff --check`, plus focused evidence for sidebar exclusion, bottom-panel
state, live Agent projection, unavailable-data honesty, OMNIA minimize/expand
and retained Stop/retry behavior.

### Wave 2 - Windows package and isolated lifecycle

Wave 2 begins only after Wave 1 is accepted. It may rebuild the Windows EXE from
the reviewed source and then run install, first launch, runtime readiness,
upgrade/rollback where applicable, shutdown and uninstall in an explicitly
acknowledged disposable Windows VM or account. It must preserve user data and
must not use the normal host as an implicit destructive test target.

P6.5-era packaging evidence does not prove that the P6.8/P6.9/P7 bytes are in a
new package. A fresh artifact digest, build input record and lifecycle evidence
are required.

## 7. Boundaries that remain in force

P7.0 design approval does not clear any release boundary:

```text
PAID_PROVIDER_NOT_PROVEN
AUTHENTICODE_NOT_PROVEN
EXE_MSI_REPACKAGE_NOT_APPROVED
LIVE_HUMAN_ELECTRON_WINDOW_NOT_PROVEN
ENTERPRISE_MULTI_AGENT_DISABLED
```

It also does not announce OmniBase 1.0.0, a signed installer, a production paid
Provider journey, enterprise Planner/DAG authority, MCP-to-Agent execution or
hostile-code Sandbox readiness.
