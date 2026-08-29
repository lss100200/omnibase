# P7.0 Editor-First Desktop Workbench

Status: **Wave 1 source UI accepted at `11895e9`; Wave 2 engineering
acceptance passed with recorded deviations for the unsigned 1.0.0 build from
`main@9913742`; production release not approved**.

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

Forward-only note (2026-08-29): this statement remains authoritative for the
accepted P7.0 Wave 1 source and Wave 2 package. The later P7.1 Wave 1 contract
in `docs/architecture/p7-1-local-development-loop.md` supersedes only the
direct-filesystem unavailable state for its exact Owner-picked, read-only,
generation-bound Electron IPC lane. It does not retroactively expand P7.0
evidence or authorize writes, Agent file tools, Terminal, Git or search.

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

#### Wave 2 engineering acceptance R0 (2026-08-29)

The clean source coordinate is
`991374216505d2b9e3dd27111c4aed370fdc7fae`. The fresh unsigned engineering
artifacts are bound by these SHA-256 values:

- EXE: `7bbc1c4f22bac17d831c1882ac5dc1f5be665bceb54fc7ee7bfb25165329b23b`;
- MSI: `fc858c10e1646d4def2ecf2e2824bb106867b43bb7735a2b543483c61f170529`;
- runtime manifest:
  `d64dbab7c52688de30b2d07161cacca7f5b18ab30f6e79bca6b4f775d85c76f7`.

Codex visually reviewed the Owner-captured Windows Sandbox evidence and found
the real P7 editor-first shell, Agent panel, OMNIA, team controls, blackboard
and honest unavailable states rendered without obvious overlap, clipping,
blank content or simulated product data. The Owner also observed 1.0.0 install,
first launch, clean process shutdown, downgrade rejection, rollback-probe
behavior, uninstall and retained application data in the disposable Sandbox.
That lifecycle is Owner-attested evidence, not an independently reproducible
automation transcript.

The accepted deviations are binding: the Sandbox session used the built-in
administrator at high integrity; captured frames do not prove 1440x900,
1024x700 or 140% DPI; no paid/live Provider SSE, Stop or retry journey ran; and
the P6.5-era 1.0.1 upgrade installed but failed to start with
`runtime_exited_before_ready code=32 signal=null`. The first-run page also
retains the stale nonblocking label `DESKTOP LOCAL / P6.7 R0`.

The full decision and evidence classification are recorded in
`docs/reviews/p7-0-wave2-engineering-acceptance-r0.md`.

## 7. Boundaries that remain in force

Wave 2 clears the real-window gate only for the exact unsigned 1.0.0
engineering build above:

```text
P7_0_WAVE_2_ENGINEERING_ACCEPTANCE_PASSED_WITH_RECORDED_DEVIATIONS
P7_0_WAVE_2_REAL_P7_UI_VISUAL_REVIEW_PASSED
LIVE_HUMAN_ELECTRON_WINDOW_PROVEN_FOR_UNSIGNED_1_0_0_ENGINEERING_BUILD
P7_0_WAVE_2_OWNER_OBSERVED_SANDBOX_LIFECYCLE_PASSED
```

The following gates remain open and must not be inferred from that narrow pass:

```text
P7_0_WAVE_2_NON_ADMIN_LIFECYCLE_NOT_PROVEN
P7_0_WAVE_2_VIEWPORT_DPI_NOT_PROVEN
UPGRADE_1_0_1_RUNTIME_START_NOT_PROVEN
PROVIDER_BACKED_SSE_STOP_RETRY_NOT_PROVEN
PAID_PROVIDER_NOT_PROVEN
AUTHENTICODE_NOT_PROVEN
ENTERPRISE_MULTI_AGENT_DISABLED
PRODUCTION_RELEASE_NOT_APPROVED
```

This does not announce a signed or production OmniBase 1.0.0 release, a
production paid Provider journey, enterprise Planner/DAG authority,
MCP-to-Agent execution or hostile-code Sandbox readiness.
