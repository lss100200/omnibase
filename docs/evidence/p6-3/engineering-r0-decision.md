# P6.3 Personal Extensions Engineering R0 Decision

Date: 2026-08-14
Branch: `codex/p6-3-personal-extensions-r0`
Pre-head: `b3fdd46413885e2649023bb818a70526203b03e8`

## Decision

```text
P6_3_PERSONAL_EXTENSIONS_ENGINEERING_COMPLETE
FIRST_PARTY_SKILLS_EXACT_15
READONLY_MCP_EXACT_6_NOT_CONNECTED_TO_AGENT_ALPHA
GLM_CLAUDE_CHAT_COMPLETIONS_PROMPT_PROFILES_IMPLEMENTED
PUBLIC_PREVIEW_SOURCE_UPDATED_LIVE_DEPLOYMENT_PENDING
CLEAN_WINDOWS_VM_ACCEPTANCE_NOT_PROVEN
NO_VM_OR_VIRTUAL_DISK_MUTATION_PERFORMED
AUTHENTICODE_NOT_SIGNED
OCI_RELEASE_IMAGES_NOT_PUBLISHED
PRODUCTION_READY_FALSE
```

This is an engineering acceptance for the personal extension surfaces. It is
not a production deployment, signed Windows release, MCP Runtime activation,
third-party Skill approval, Planner/Multi-Agent unlock or enterprise P34.7
evidence claim.

## P6.3-A — first-party Skill Registry

- Expanded the source-owned native catalog from six to exactly fifteen
  instruction-only Skills.
- Added bounded category, tags, recommended-role and instruction-byte metadata,
  a stable catalog digest, and list filtering that never searches full Skill
  instructions.
- Existing persisted Definition/Version rows must match the complete immutable
  catalog projection before reuse.
- Installation and fresh invocation resolution independently enforce:
  - at most eight live Skills;
  - at most 32,768 aggregate UTF-8 instruction bytes;
  - no duplicate Definition in the resolved bundle.
- Local Skill discovery remains Owner-triggered, scan-only and unable to use the
  first-party registration path.

## P6.3-B — read-only MCP and model profiles

The standalone stdio MCP preview now exposes exactly six tools:

```text
omnibase_files_list
omnibase_files_read
omnibase_git_inspect
omnibase_files_hash
omnibase_text_search
omnibase_git_diff_summary
```

The new tools retain root/path/open-handle identity checks, sensitive/VCS/link/
reparse exclusion, fixed per-call limits and new process-lifetime call/file/Git
budgets. Search is literal-only; Git diff accepts only `worktree|staged` and
returns no patch content. MCP remains manually launched, is not mounted into
Agent Alpha and leaves `MCP_RUNTIME_ENABLED=false`.

Independent review found and closed two P2 boundary gaps before final sealing:

- native catalog queries now return detached deep snapshots, so mutating a
  returned nested JSON Schema cannot change the source catalog digest or later
  installation projection;
- text search revalidates every descendant directory before scanning and every
  yielded file's complete component chain before opening, closing a local
  junction/symlink replacement race.

The two focused regression files report `44 passed` after these forward fixes.

Backend and frontend family recognition now share conservative exact GLM and
Claude rules, including relay namespaces and Anthropic model-family locators.
Bare names, proxy/bridge/emulator claims and conflicting family names remain
generic. Model family selects stable prompt/context guidance only. The current
Chat Completions transport does not send or claim native GLM thinking/tool
stream or Anthropic Messages thinking/cache/effort/strict-tool/MCP fields.

Official research used for the profile decision:

- Z.AI model selection, [thinking](https://docs.z.ai/guides/capabilities/thinking),
  [context caching](https://docs.z.ai/guides/capabilities/cache),
  [function calling](https://docs.z.ai/guides/capabilities/function-calling),
  [tool streaming](https://docs.z.ai/guides/capabilities/stream-tool) and
  [OpenAI Python SDK compatibility](https://docs.z.ai/guides/develop/openai/python);
- Anthropic [model overview](https://platform.claude.com/docs/en/about-claude/models/overview),
  [extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking),
  [effort](https://platform.claude.com/docs/en/build-with-claude/effort),
  [prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching),
  [strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use),
  [MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector)
  and [OpenAI SDK compatibility](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk).

Every URL above was rechecked on 2026-08-14 and returned HTTP 200. These
documents explain vendor capabilities; they do not prove that an opaque relay
implements those capabilities or that the current Chat Completions transport
may safely send their native fields.

## P6.3-C — GitHub and public preview source

- Updated English and Chinese README product facts from migration `0012` to
  `0016` and replaced the obsolete compile-only Skill description.
- Added the personal file/conversation/ChangeSet, 1+9 role, fifteen-Skill,
  six-tool MCP, model-name-first and Windows Companion presentation.
- Updated `/public-preview` metadata and bilingual content with explicit
  Available / Engineering Preview / Deferred states.
- The public page source was built successfully. No Docker/WSL/tunnel operation
  was attempted because the known host VM boundary is not required for source
  acceptance and must not be repaired through VHDX mutation.

Therefore:

```text
PUBLIC_PREVIEW_SOURCE_UPDATED_LIVE_DEPLOYMENT_PENDING
```

GitHub About metadata must not lead the public `main` source. Its external
description/topic update remains deferred until this source is published.

## P6.3-D — Windows Companion

Existing `verify`, `install`, `init-config`, `doctor` and compatibility commands
remain. New commands:

```text
help
locations [--json]
plan-install --scope user|machine|custom [--target <absolute-path>] [--json]
```

The Companion reports conventional user/machine locations, marks machine scope
as elevation-required planning only, and validates custom targets. Install and
planning reject relative/root/UNC/network/ADS/reparse/existing targets. No UAC,
PATH, registry, shortcut, service, firewall, Docker, WSL or VHDX mutation was
added.

One clean-Windows VM preflight was attempted. Windows PowerShell 5.1 lacked the
PowerShell 7 `$IsWindows` variable used by the first source revision, so the
probe stopped with exit `20` and the mandatory no-mutation state. Platform
detection and localized ACL handling were then forward-fixed in source; only a
PowerShell AST parse was run afterward. The Hyper-V/VM probe was not repeated.

```text
CLEAN_WINDOWS_VM_ACCEPTANCE_NOT_PROVEN
NO_VM_OR_VIRTUAL_DISK_MUTATION_PERFORMED
```

Self-contained artifact independently rechecked:

```text
path: E:\Agent IDE\Artifacts\OmniBase-P63-Companion-D-R0\OmniBase.Setup.exe
size: 67,535,942 bytes
SHA-256: e85efe0282be1e2ab48e485986f3c6bbbd71f6195aff04b25f6c1e5c73ae0e02
Authenticode: NotSigned
```

## Verification

Integrated focused backend matrix:

```text
95 passed
```

It includes native Skill catalog/persistence/resolution, Model Gateway, Agent
Alpha personal regression and all MCP attack tests.

The final first-party catalog is exactly fifteen entries and its source-owned
digest is:

```text
abd8923479f6040d4f747f28f27054101f01fba710528f06bb870a42d471ab98
```

Changed-source static checks:

```text
Ruff 0.8.6 format/check = 14 explicit Python paths passed
Mypy --follow-imports=skip = 9 changed source files, 0 issues
```

Frontend:

```text
172 passed
TypeScript typecheck passed
Next lint passed with zero warnings/errors
production build passed; 17 routes; /public-preview statically generated
```

Windows release/Companion:

```text
17 passed, 1 skipped (existing Windows POSIX-only fsmonitor fixture)
dotnet format --verify-no-changes passed
dotnet Release build: 0 warnings, 0 errors
PowerShell AST parse passed
```

Maintainer and sealed-contract checks before the final clean-HEAD verification:

```text
maintainer map = valid (70 invariants, 49 modules, 1059 path specs,
  3628 matched files, 333 entrypoints, 275 verification commands)
maintainer benchmark = valid (3 plans, 8 scenarios, 6 critical scenarios,
  9 unsafe vetoes)
P5.1A registry contract tests = 129 passed
P5.2A task-ledger contract tests = 200 passed
P5.3A planner contract tests = 78 passed
P5.1A / P5.2A / P5.3A validate-only = contract_valid=true,
  state=blocked/not_proven, activation_allowed=false, vetoes=[]
git diff --check = passed
```

The ordered final-byte chain is maintenance map and security invariants,
Registry, Task Ledger, then Planner. Formal `--verify` results are recorded
only after a clean local commit; validate-only is not presented as clean-HEAD
source provenance.

## Safety state

```text
migration head = 0016
migration 0017 absent
AGENT_RUNTIME_ENABLED = false
AGENT_PLANNER_ENABLED = false
MULTI_AGENT_ENABLED = false
MCP_RUNTIME_ENABLED = false
Agent Alpha = no_tool
root .env not read
business database not accessed or migrated
Docker / WSL / Hyper-V / VHDX mutation not performed
third-party Skill installation absent
MCP-to-Agent integration absent
not deployed
```
