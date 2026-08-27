# P6.1 A-D: native Skills, model adaptation, read-only MCP and release preview

## Decision

P6.1 extends the personal, single-Owner product without reopening the frozen
enterprise P34.7 campaign.

- **A — native Skills:** six source-owned first-party instruction Skills are
  exposed through authenticated Browser catalog/detail endpoints and can be
  installed or disabled for one live Owner/Workspace/sealed AgentVersion using
  migration `0014`. No URL, ZIP, arbitrary path, Marketplace, executable
  instruction or capability expansion is accepted.
- **B — DeepSeek/GPT:** the effective model name, never the base URL, selects a
  conservative request profile. DeepSeek gets a stable prefix,
  thinking/reasoning controls and cache-hit/miss usage. GPT gets an
  outcome-first stable prefix plus Chat-Completions-compatible reasoning effort. Unknown or
  conflicting names receive generic compatible parameters only.
- **C — read-only MCP preview:** one explicitly launched stdio server exposes
  exactly three tools: bounded authorized-file listing, bounded UTF-8 reading
  and metadata-only Git status/log inspection. It is not mounted into Agent Alpha and does not
  reinterpret `no_tool`; Runtime MCP remains disabled.
- **D — Windows preview:** a deterministic canonical ZIP contains only release
  Compose/templates/docs/license. A .NET single-file EXE thin-wrapper source
  verifies the ZIP manifest before extraction. Release Compose has no `build:`
  and requires immutable OCI digests. A portable Microsoft .NET SDK 8.0.424
  archive has been restored outside the repository and its SHA-512 matches the
  official release value. Clean source commit `cf707e2` produced byte-reproducible
  ZIP and EXE artifacts; the EXE passed 20 fresh-target installations and six
  fail-closed archive/target attack cases without partial targets or staging
  residue.

## Model research and stable-prefix rule

DeepSeek official documentation available during implementation states that
disk context caching is automatic and later requests benefit from an identical
persisted prefix. OmniBase therefore injects adaptation as the first stable
system message and leaves changing task input last. DeepSeek-native fields are
sent only for one unambiguous `deepseek` token in the actual model name.

Official OpenAI documentation was retrieved on 2026-08-14 from
`https://developers.openai.com/api/docs/guides/reasoning` and
`https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5`.
It recommends scaling reasoning effort to task difficulty, states that
reasoning models perform better through Responses while Chat Completions
remains supported, and documents verbosity as a Responses text control. The
implementation therefore sends only Chat-Completions-compatible reasoning
effort on this path, never a Responses-only verbosity field.

## Security posture

- Root `.env`, business databases and provider keys are outside this change.
- Migration head remains `0016`; no migration `0017` exists.
- Skill mutation derives tenant-scoped deterministic database UUIDs, validates Tenant, active Owner, Workspace ownership, active
  owner membership and exact sealed Agent binding before idempotency reserve.
- Native Skills have no tools, network, secrets, MCP, Planner or Multi-Agent.
- MCP rejects traversal, absolute/drive paths, links/reparse points, sensitive
  names, binary/large files and arbitrary commands. Git is limited to status/log metadata; argv/environment,
  timeout and output are closed and bounded.
- ZIP inputs are regular files with fixed order/time/mode and stored encoding.
  The real builder reads fixed-path blobs from the declared clean Git commit
  under a closed Git environment instead of trusting mutable worktree bytes. The
  archive excludes image tar, VHDX/WSL data, DBs, models, `node_modules`,
  `.next`, virtualenvs, root `.env` and populated operator env.
- Installer/doctor may report virtual-disk posture but may never compact,
  truncate, relocate or delete Docker/WSL virtual disks.

## Not proven / deferred

- No third-party Skill scan, Marketplace or executable Skill exists.
- MCP is independent preview infrastructure; Agent Runtime integration,
  persisted grants/receipts and migration `0017` are deferred.
- Workspace RAG hosting, arbitrary MCP, Email, network search, browser
  automation, shell and write filesystem are absent.
- OCI images are not built/published and digest placeholders are not filled;
  the offline preflight rejects tags, placeholders and repository drift.
- The EXE wrapper and bounded atomic-move retry are verified against the real
  clean-HEAD binary. It remains a framework-dependent unsigned preview:
  Authenticode is `NotSigned`, publisher identity is not verified, and the
  target machine must provide the .NET 8 runtime.
- No push, PR, merge, deployment or production activation occurred.
