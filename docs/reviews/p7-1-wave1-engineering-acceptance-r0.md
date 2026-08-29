# P7.1 Wave 1 Engineering Acceptance R0

Date: 2026-08-29

Status: `P7_1_WAVE_1_WINDOWS_ENGINEERING_EVIDENCE_READY_FOR_REVIEW`

This record binds the P7.1 Wave 1 read-only local-file source and its R3
installed-product evidence. It does not approve production release or any later
write, Agent-tool, Terminal, Git or search gate.

## Source and package identity

- Source commit: `2e6693ebc9235f4220ffb8401fe9ac59efe14a6d`
- Branch/PR: `cursor/p7-1-local-development-loop-r0`, PR #48
- Source mode: clean release; `source_clean=true`
- Setup EXE SHA-256:
  `4b745d2fd33f13d1e38371a019f81e4fbe21dca366bc12216262cbdc4d1100a4`
- MSI SHA-256:
  `f169fd79dcc6168d2fc5027bda82eedf9369feafe9c6b79403d719b4c3a3c083`
- Runtime manifest SHA-256:
  `75ce437ce2d59e412fcd0e8a411e87fc431ae0531c988ffa0eaff526eff6b850`
- The build report remained honest: `production_ready=false` and
  `authenticode_verified=false`.

## Defect closure

The first installed journey authorized and enumerated Fixture A but failed when
opening a BOM-containing `src/main.ts`. Native `TextDecoder("utf-8")` removed the
BOM while the bridge retained the original byte count and SHA-256; the renderer
correctly rejected that non-byte-faithful response.

Commit `2e6693e` uses `ignoreBOM: true` so the decoded string retains the BOM and
adds a regression test that verifies content, UTF-8 byte count, size and digest.
Desktop 144 tests, frontend 374 tests, desktop/frontend typecheck and build,
frontend lint, maintainer-map validation and `git diff --check` passed before
the R3 build.

## Real Electron journey

The target was a fresh, disposable Windows Sandbox running Windows 11
Enterprise `10.0.22621` as `WDAGUtilityAccount`. The R3 setup hash matched in the
guest and Burn returned `0`. A real Electron window titled `OmniBase` opened.

The UI journey then proved:

1. Owner `owner` and Workspace A were visible with OMNIA.
2. The Owner authorized `C:\P71FixtureA`; Explorer enumerated its bounded tree.
3. `src/main.ts` opened read-only as `39 B`, SHA prefix `e8e34f1c55ed`, and the
   BOM-containing content rendered without the prior verification error.
4. Creating `workspace-b` immediately cleared Workspace A's projected tree and
   code buffer.
5. The Owner authorized `C:\P71FixtureB`; its own `src/README.md` tree appeared.
6. Releasing that authorization cleared the tree and buffer, returned Code to
   `unavailable` / “未打开文件”, and restored the folder-picker empty state.
7. Returning to the prior Workspace did not project Fixture B. The native
   implementation uses a single volatile binding, so Workspace switches release
   the previous authorization rather than persisting a per-Workspace handle.
8. The OmniBase application window closed through the real UI.

The host artifact directory contains the guest transcript, structured
`evidence.json`, acceptance summary and seven 2032x1074 screenshots whose hashes
were independently recomputed with zero mismatches:

`E:\Agent IDE\OmniBase Artifacts\p7-1-wave1-sandbox-evidence-r3-20260829`

## Retained boundaries

The Sandbox account was elevated/High integrity. Therefore the original
non-admin medium-integrity journey remains not proven. This run also does not
prove in-guest `1440x900`, `1024x700` or `140%` DPI, full process-tree
convergence after close, uninstall/data retention, Provider/API-key operation,
Authenticode, production release readiness or enterprise multi-agent.

P7.1 Wave 1 remains read-only. Save/write, Agent file tools, Terminal, Git,
search, watchers, rename/delete, Next/backend file routes and database
migrations remain closed. Linux packaging is planning-only and receives no
authority from this Windows evidence.
