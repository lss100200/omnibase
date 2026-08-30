# P7.4 Hardening and Windows Evidence R0

Date: 2026-08-30

Status: `P7_4_WINDOWS_PRODUCT_ACCEPTANCE_INCOMPLETE`

This record binds the clean P7.4 hardening source, deterministic certification
matrix, fresh unsigned Windows package and the partial evidence from one
disposable Windows Sandbox instance. It does not issue
`P7_4_ENGINEERING_EVIDENCE_READY_FOR_REVIEW` or
`P7_4_ENGINEERING_ACCEPTED`: the required five-family product journeys were not
completed in the target.

## Source and package identity

- Base: `5ca173a741aca2e176f36f1f1869234bba8deb5b`
- Source commit: `a17d03d000460b8092f58d9ae47ccc2772522c3f`
- Branch: `cursor/p7-3-workspace-hot-plug-r0`
- Source mode: `clean-release`; `source_clean=true`
- Setup EXE SHA-256:
  `e9acda3480cbf26f5592f4d7c5925933d5444bdefe7ab5aa31454b0ba9486aed`
- MSI SHA-256:
  `58567a26a54ba5c659276c43235bb585b52872c67e2330601024b730d00dfcfe`
- Runtime manifest SHA-256:
  `ed4befc1d000845f37ec0a4aa0e0462e7d676e006d11c0225a350cd29b9d4d03`
- Component bundle SHA-256:
  `5a20d4f6442297086d75ce64d746a12c6522d16861c54a6931a7e560121c9ea4`
- Component bundle tree SHA-256:
  `4e00e25e4260559d0f5b383846a3ecf1e39c25d64cc13a5dbe64446a51078bec`

The build report remains honest: `authenticode_verified=false`,
`clean_windows_lifecycle_verified=false`,
`required_product_journeys_verified=false`, `production_ready=false`.

## Automated hardening evidence

The clean certification profile passed with desktop schema v12 and the fixed
500-component/1,510-version, 20-Workspace and 2,000-installation dataset.
Measured p95 values were:

- snapshot: `128.2412 ms` against a `250 ms` ceiling;
- mutation: `30.3887 ms` against a `100 ms` ceiling;
- operation begin: `6.9304 ms` against a `50 ms` ceiling; and
- operation settle: `5.6941 ms` against a `50 ms` ceiling.

The bounded 100-cycle campaign passed with zero automatic replays, zero
unresolved effects and no listener or child-process residue. RSS growth was
`22,896,640` bytes against the `134,217,728` byte ceiling. This bounded result
does not imply the separate eight-hour nightly or 24-hour release-candidate
campaigns.

Focused and full gates also passed: 114 component/migration tests, 379 sealed
P5 contract tests, 76 release/receipt tests, 25 ebook/bundle tests, 229 desktop
tests, 420 frontend tests, typechecks/builds, focused Ruff, Prettier,
maintainer-map validation and maintainer benchmark validation.

## Partial Windows evidence

Exactly one fresh Windows Sandbox instance was started with networking and
clipboard disabled and a read-only input mapping. Its instance identity was
`22c57967-e155-4174-8ccb-bd179c844894`. The target reported Windows
`10.0.22621.0`, user `WDAGUtilityAccount`, `elevated=true`,
`high_integrity=false` and `preexisting_install=false`.

The instance directly proved only the following:

1. the exact setup EXE hash was rechecked in the guest and quiet installation
   returned `0`;
2. the installed executable was present with SHA-256
   `7de6efabefc927c3bbddcebc8d330c0790751590802e3d9a7583a9fb84085e81`;
3. a real `OmniBase` Electron window reached ready state;
4. loopback listeners were present on `127.0.0.1:3000` and
   `127.0.0.1:8765`;
5. the Owner `P74 Owner` and Workspace `P74 Workspace` were initialized;
6. the full-screen Settings center rendered; and
7. Catalog rendered ten source-owned packages covering two immutable versions
   of each of the five families: UI/Canvas, Instruction Skill, MCP, Sandbox and
   Local Adapter.

Four screenshots and their SHA-256 digests are retained:

| Screenshot | SHA-256 |
| --- | --- |
| `00-initial.png` | `d4260d73b4627c534cb3a3348e7c63f65f91e52002134eb9e08d6e651ce785e3` |
| `01-owner-created.png` | `0a2cbce540ddb31b1e1bd226a9a3ef8717ee0f102cf692c99cacc73e93d88c01` |
| `02-settings.png` | `87fae8668f184b30dfc95fa149504c631f3812af6eb9e13173925c1e8954d192` |
| `03-catalog.png` | `d037f0c4d1869853a8e31aba2df63c61d2b9ad6b12425bab4ba65689a40feaaa` |

Evidence is retained at:

`E:\Agent IDE\OmniBase Artifacts\p7-4-sandbox-evidence-r0-a17d03d-20260830`

The Sandbox was closed through its final confirmation dialog. Host verification
then reported zero `WindowsSandbox.exe`, `WindowsSandboxClient.exe` and
`vmmemSandbox` processes.

## Missing product journeys

The instance was closed after the Catalog view. It did not execute package
proposal/review/approval, install, start, invoke, stop, upgrade, rollback or
revoke for any family. It also did not prove P7.1 read-only regression,
cross-Workspace isolation, emergency stop, crash/restart recovery, graceful
application close, controlled uninstall or retained application data. No
SQLite export or strict P7.3 Windows acceptance receipt was produced.

These missing journeys are not inferred from source tests, the visible Catalog,
an install exit code or prior P7.2 evidence. A later controlled run must create
a new receipt and retain this R0 as incomplete evidence rather than overwrite
it.

## Retained boundaries

The source-owned Sandbox journey executes exact inventory-bound, zero-import
WASM through a trusted same-host helper. It is not independent P34
Runner/provider isolation. The external P34 provider remains
`unavailable/not_proven`.

`AUTHENTICODE_NOT_PROVEN`, `PAID_PROVIDER_NOT_PROVEN`,
`ENTERPRISE_MULTI_AGENT_DISABLED`, `MARKETPLACE_NOT_PROVEN` and
`PRODUCTION_RELEASE_NOT_AUTHORIZED` remain in force. No tag, signed release,
Marketplace publication, deployment or production acceptance is created by
this record.
