# P7.0 Wave 2 Engineering Acceptance R0

Date: 2026-08-29

Decision:

```text
P7_0_WAVE_2_ENGINEERING_ACCEPTANCE_PASSED_WITH_RECORDED_DEVIATIONS
P7_0_WAVE_2_REAL_P7_UI_VISUAL_REVIEW_PASSED
LIVE_HUMAN_ELECTRON_WINDOW_PROVEN_FOR_UNSIGNED_1_0_0_ENGINEERING_BUILD
P7_0_WAVE_2_OWNER_OBSERVED_SANDBOX_LIFECYCLE_PASSED
```

This is an engineering acceptance for the exact unsigned 1.0.0 build named
below. It is not a production release, Authenticode, paid Provider or general
Windows lifecycle acceptance.

## 1. Source and artifact identity

| Item | Accepted identity |
|---|---|
| source commit | `991374216505d2b9e3dd27111c4aed370fdc7fae` |
| source mode | clean release worktree; repository `trusted-manifest.ts` placeholder preserved |
| EXE | `OmniBase-1.0.0-windows-x64-setup.exe` |
| EXE SHA-256 | `7bbc1c4f22bac17d831c1882ac5dc1f5be665bceb54fc7ee7bfb25165329b23b` |
| MSI SHA-256 | `fc858c10e1646d4def2ecf2e2824bb106867b43bb7735a2b543483c61f170529` |
| runtime manifest SHA-256 | `d64dbab7c52688de30b2d07161cacca7f5b18ab30f6e79bca6b4f775d85c76f7` |

Artifact root:

`E:\Agent IDE\OmniBase Artifacts\p7-0-wave2-windows-package-r1-20260827`

The build report remains authoritative about release posture:
`production_ready=false`, `authenticode_verified=false`,
`clean_windows_lifecycle_verified=false` and
`required_product_journeys_verified=false`. The R0 acceptance does not rewrite
those builder-emitted facts.

## 2. Formal evidence set

Evidence root:

`E:\Agent IDE\OmniBase Artifacts\p7-0-wave2-r3-sandbox-evidence`

The formal image set contains eight hash-verified captures:

- five Owner/P7 UI captures covering first-run Owner creation, Explorer,
  editor-first workbench, task brief, run/debug controls, plan/blackboard,
  Agent panel, OMNIA and the honest unavailable Terminal state;
- two rollback-probe captures; and
- one 1.0.1 runtime-error capture.

Codex visual review found a real Electron P7 shell with no obvious product
overlap, clipping, blank rendering or simulated workbench data. This visual
pass is limited to what the captures actually show. It does not prove exact
viewport dimensions, DPI scaling, live streaming behavior or Provider calls.

The first-run page still displays `DESKTOP LOCAL / P6.7 R0`. This is recorded
as a nonblocking P2 stale label; it does not invalidate the P7 workbench shown
after Owner initialization.

## 3. Owner-observed lifecycle

The Owner personally performed the following in a disposable Windows Sandbox:

- install the exact 1.0.0 EXE under the per-user application location;
- start the real application and initialize the local Owner;
- open and inspect the P7 editor-first workbench and its focused panels;
- close the application and observe child-process convergence;
- install the controlled 1.0.1 upgrade, which completed its installer
  transaction but then failed runtime startup;
- observe downgrade rejection and rollback-probe failure/rollback behavior;
- uninstall and observe installer-owned application/shortcut/registration
  removal, retained `%LOCALAPPDATA%\OmniBase` data and zero remaining product
  processes.

These lifecycle results are Owner-attested. The R3 evidence root does not
contain an independently replayable guest transcript, installer logs, registry
snapshots, retained-data marker contents or a machine-readable zero-process
receipt. The decision therefore uses
`P7_0_WAVE_2_OWNER_OBSERVED_SANDBOX_LIFECYCLE_PASSED`, not an independently
reproduced clean-target claim.

## 4. Recorded deviations and open gates

The Windows Sandbox default account was `WDAGUtilityAccount`, an administrator
running at high integrity. It does not satisfy the originally requested
non-administrator, medium-integrity lifecycle target.

The images are host-side captures. They do not prove a 1440x900 viewport, a
1024x700 viewport or 140% DPI behavior.

No paid/live Provider call was accepted, so real Provider-backed SSE, Stop and
retry remain outside this evidence. The 1.0.1 package was a prior P6.5
engineering artifact and failed to start with:

```text
runtime_exited_before_ready code=32 signal=null
```

The following statuses remain binding:

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

## 5. Evidence hygiene

Failed VM-attempt scripts are troubleshooting material, not formal acceptance
evidence. They were moved without deletion to:

`E:\Agent IDE\OmniBase Artifacts\p7-0-wave2-r3-vm-attempt-tools-not-accepted`

That sibling directory is explicitly excluded from the formal R3 evidence set
and must not be distributed as acceptance evidence. This registration changes
documentation only; it does not change runtime source, rebuild a package, sign
an artifact, create a release/tag or deploy anything.
