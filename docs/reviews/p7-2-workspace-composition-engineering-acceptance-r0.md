# P7.2 Workspace Composition Engineering Acceptance R0

Date: 2026-08-30

Status: `P7_2_SOURCE_AND_WINDOWS_ENGINEERING_ACCEPTED_AT_8CB0020_PENDING_REMOTE_CI`

This record binds the reviewed P7.2 Workspace composition source to its
installed-product evidence. It accepts a bounded engineering result, not a
signed or production-ready release, and it does not start P7.3.

## Source and package identity

- Base: `cb1295b4b12df9f080eb0dcf94bc908367c8a7e3`
- Source commit: `8cb0020255202cb197ffdbf46393265db9a0ef28`
- Branch: `cursor/p7-2-workspace-composition-r0`
- Source review: no unresolved P0, P1 or P2 finding
- Package source mode: `engineering-dirty`; `source_clean=false`
- Setup EXE SHA-256:
  `55e45d2b546d7bb2bfe94cf465625433d78f554811b8acd3d9b8953cf55ce568`
- MSI SHA-256:
  `a0725d153680ef4fb5b714100b5fca953f05fd754d27e4fb4ca2451927725a7b`
- Runtime manifest SHA-256:
  `3ab17c2b2ccebeac9d0888f06205cdbb1416d6ae2755c1c75a53b38962232950`

The build report remains honest: `production_ready=false`,
`authenticode_verified=false`, `clean_windows_lifecycle_verified=false` and
`required_product_journeys_verified=false`.

## Defect closure

The first installed R0 journey exposed a real first-frame recovery defect. The
Explorer bootstrap action was gated on the composition projection before that
projection had loaded, so a first-run Owner could not create or recover the
initial Workspace from the installed shell. The forward fix separates the
bootstrap recovery action from profile readiness while retaining the exact
Workspace and composition identity gates for all profile-backed controls.

The replacement R1 package was rebuilt from the reviewed working tree and the
complete installed journey was repeated. No R0 artifact is accepted by this
record.

## Real Electron journey

Exactly one R1 Windows Sandbox instance installed and launched the package. A
real Electron window proved:

1. first-run Explorer recovery and Workspace creation;
2. the central full-screen Settings surface;
3. independent Application and Workspace density settings;
4. exact proposal request SHA-256 and before/after Diff;
5. Owner approval and rejection without automatic application;
6. rollback implemented as a new immutable revision;
7. append-only composition audit history;
8. honest unavailable Extension and Sandbox surfaces;
9. closed Slot gating and immediate cross-Workspace first-frame isolation;
10. the P7.1 read-only folder list/read/release journey; and
11. focus-mode proposal, exact Diff, Owner approval and revision 5, with the
    context sidebar, Agent rail and bottom panel hidden while Settings remained
    reachable.

Before uninstall, the guest reported Electron, RuntimeHost, desktop backend and
Next processes with loopback listeners on ports 3000 and 8765. Controlled
uninstall returned `0`; processes and listeners were empty, the installed
executable was absent and application data was retained. The Sandbox was
closed through its confirmation dialog, after which the host reported no
Sandbox or `vmwp` process.

Evidence is retained at:

`E:\Agent IDE\OmniBase Artifacts\p7-2-workspace-composition-sandbox-evidence-r1-20260830`

The directory contains thirteen screenshots, the guest transcript,
pre-uninstall process/listener evidence and the uninstall receipt.

## Verification

The following local gates passed before the source commit:

- frontend: 387 tests, typecheck, production build and lint with only the two
  existing `<img>` warnings;
- desktop: 149 tests, typecheck and build;
- backend: 25 focused P7.2 tests, focused Ruff check/format and focused mypy;
- sealed P5.0/P5.1/P5.2A contracts: 379 tests;
- maintainer map: 77 invariants, 54 modules, 3818 matched files and 380
  entrypoints;
- maintainer benchmark and P5.0/P5.1/P5.2A/P5.6A validate-only gates;
- focused Prettier check, Python compileall and `git diff --check`.

The native Windows full backend suite cannot be authoritative for Linux-only
launcher and POSIX contracts. Collection first stopped at `ctypes.CDLL(None)`;
with that Linux-only file excluded it reported 3113 passed, 11 failed and 42
skipped. The 11 failures are existing POSIX assumptions (`geteuid`, `killpg`,
AF_UNIX, private modes and Unix absolute paths). Full mypy similarly reports 16
existing POSIX platform errors outside P7.2. Required remote Linux CI remains
mandatory before merge.

## Retained boundaries

P7.2 is the declarative composition control plane. Save/write, Terminal,
Git/search, arbitrary plugin execution, executable Skill/MCP integration,
Sandbox activation and automatic approval remain closed. `knowledge.ebook`
remains unavailable until its trusted read-only adapter is separately reviewed.
The renderer receives neither arbitrary JavaScript authority nor native control
secrets.

The P3.4 security semantics remain binding: logical identity and live scope,
CAS/fencing, separated capabilities, exact approval binding, bounded budgets,
append-only audit, revocation and no automatic replay of `pending` or `unknown`
external effects. This desktop-local SQLite implementation does not claim that
the enterprise PostgreSQL control plane is active.

The R1 Sandbox account was elevated/High integrity. Standard-user
medium-integrity, clean-source packaging, Authenticode, Provider-backed live
calls, public release and production readiness remain not proven.

P7.3 may start only after P7.2 passes remote CI and merges. P7.3 must deliver
the complete hot-plug execution platform in one large release: unified
manifest/registry, all five adapter families, dependency and Slot binding,
install/start/stop/upgrade/rollback/revoke, identity/capability/lease/fencing/
budget enforcement, health isolation, reconciliation, recovery, emergency
stop and Settings management. P7.4 is hardening and acceptance only; no core
registry, adapter, permission, revocation, lifecycle or recovery path may be
deferred to it.
