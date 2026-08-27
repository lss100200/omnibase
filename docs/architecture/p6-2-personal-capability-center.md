# P6.2 A-D: personal capability center, local continuity and Windows Companion

## Decision

P6.2 closes the most visible personal-edition gaps after P6.1 without turning
the product into an enterprise approval system or an autonomous multi-Agent
runtime. The product remains one human Owner, one bounded Agent Alpha Runtime,
one parent role and nine dormant request-scoped specialist roles.

P6.2 is split into four bounded increments:

- **A — personal capability center and scan-only Skill discovery:** `/skills`
  becomes the Chinese personal capability center. It combines the six sealed
  first-party instruction Skills, the existing ten-role model posture and the
  exact three-tool read-only MCP preview. A local directory is scanned only
  after the Owner invokes the browser directory picker. Only direct child
  directories containing `SKILL.md` are considered. The scanner is UTF-8,
  file-count and byte-budget bounded; executable siblings, capability fields,
  tools, network, secrets, scripts and malformed metadata are rejected. It
  never executes, installs, downloads or sends the selected content over the
  network, and the Browser projection uses opaque source IDs rather than a
  physical path. A structurally safe unknown candidate remains
  `unsupported_unreviewed`; it is not promoted into migration `0014`, whose
  database contract is explicitly first-party.
- **B — bounded conversation and ChangeSet continuity:** a new invocation may
  include at most 24 recent terminal user/Agent messages from the same browser
  session under an independent 12,000-character budget. The messages already
  pass the P6.0 persistence redactor, and changing sessions does not share
  history. Newest messages win deterministically when the budget is exhausted.
  The `fnv1a32` manifest is only a browser-local diagnostic fingerprint, never
  a security digest or authorization receipt. Task-owned ChangeSets are stored
  in the tenant/Workspace-scoped browser key
  `omnibase.p6.changes.v1:<tenant>:<workspace>`, bounded to 40 records and
  4 MiB. Restore validates the complete file/version shape and exact scope
  before the record may be displayed or used by the existing CAS and
  three-way rollback preflight. This does not create server file authority,
  cross-device transcript storage or Provider replay.
- **C — honest model and MCP observability:** the capability center reuses the
  existing P6.0-D2 role settings and provider runtime posture instead of
  creating a second routing system. It reports the default source
  (`personal`, `operator_default` or `unavailable`), ready/pending/unavailable
  role counts and explicit overrides. MCP is shown as the exact closed set
  `omnibase_files_list`, `omnibase_files_read` and `omnibase_git_inspect`.
  Displaying this preview does not connect it to Agent Alpha, change `no_tool`
  or enable `MCP_RUNTIME_ENABLED`.
- **D — Windows engineering-preview Companion:** the existing
  `OmniBase.Setup` source becomes a self-contained `win-x64`, single-file
  Companion with `verify`, `install`, `init-config` and offline `doctor`
  commands. ZIP verification preserves the P6.1 exact manifest schema,
  closed payload set, source-commit grammar, byte limits, compression-ratio
  limit and per-file SHA-256 checks. Install remains staging plus final atomic
  directory move and refuses an existing target. `init-config` uses the OS
  CSPRNG, does not echo secrets and never overwrites an existing file. `doctor`
  verifies the installed payload closed set and digests, validates the exact
  config key set, credentials, encryption-key shape, local CORS, immutable
  image repositories and closed feature gates, then performs bounded read-only
  Docker CLI/daemon, Compose, WSL2 and disk diagnostics. It never pulls images,
  starts services, changes WSL, or mutates VHDX, PATH, firewall or Windows
  services.

## Authority and storage boundaries

The local Skill picker and ChangeSet journal are browser capabilities, not
server trust roots. A malicious or malformed local-storage record fails closed
and cannot grant a file handle. File handles remain memory-only and are lost on
scope change or page authorization release. History and ChangeSet content are
not uploaded to a new backend API in this phase.

Migration head stays `0016`; P6.2 does not create `0017`. Unknown third-party
Skill import, cross-device conversations and durable MCP grants are distinct
future schemas and must not be combined into one ambiguous migration. The
first-party Skill persistence service must never be reused to label arbitrary
local content as `first_party=true`.

The ten role entries remain contexts over one Runtime. No specialist can wake
itself, call another specialist or run in the background. The Owner must use an
exact single `@` mention to select one specialist. Model configuration and
probe identity continue to use the P6.0-D2 API and version fences.

## Windows Companion posture

The Companion command contract is:

```text
verify <release.zip>
install <release.zip> <new-target>
init-config --output <operator.env>
doctor --install <install-dir> [--env-file <operator.env>] [--json]
```

Exit codes are stable classes:

```text
0  READY_FOR_PULL
10 NEEDS_ACTION
20 HOST_OR_DEPENDENCY_UNSUPPORTED
30 INTEGRITY_OR_SECURITY_FAILURE
40 RELEASE_IMAGES_NOT_PUBLISHED
```

`READY_FOR_PULL` means only that a human may consider the next exact-digest
pull step. It does not mean that images were pulled, containers started,
health passed, publisher identity verified or production readiness achieved.
The six real OCI digests are publisher-owned metadata and remain absent from
the preview template. The stable result is therefore
`RELEASE_IMAGES_NOT_PUBLISHED / NOT_READY_FOR_PULL`, not a user configuration
fault. Authenticode remains unsigned and `production_ready=false` remains
mandatory.

## Deferred work

- third-party Skill installation, execution, Marketplace and automatic update;
- workflow/script Skills or any Skill-provided tool/network/secret authority;
- arbitrary MCP servers, MCP-to-Agent integration and Browser process control;
- shell, arbitrary HTTP, write-filesystem, SQL execution and browser automation;
- cross-device transcript sync or server-side local-file rollback authority;
- Planner, autonomous Multi-Agent delegation and enterprise P34.7 ceremonies;
- signed publisher identity, published OCI digests, clean-machine public release
  acceptance and production deployment.

## Verification contract

- frontend unit tests, typecheck, lint, explicit-path Prettier and production
  build;
- focused P6.0/P6.1 backend model, Agent Alpha, native Skill and read-only MCP
  regression without Docker or a business database;
- Windows Companion .NET `8.0.424` build/publish plus offline verify/install/
  init-config and negative archive/config checks;
- `scripts/release/test_build_windows_release.py`;
- maintainer map and maintainer benchmark validators;
- clean worktree, `git diff --check`, migration head `0016`, all Runtime/
  Planner/Multi-Agent/MCP gates unchanged and no push/PR/merge/deploy.
