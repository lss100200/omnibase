# P6.3 Personal Extensions

P6.3 deepens the personal edition without turning OmniBase into an unbounded
plugin host or autonomous multi-Agent system. The product remains one human
Owner, one default-active parent Agent and nine request-scoped specialists that
stay dormant until one explicit `@` mention.

The phase is divided into four bounded increments:

- **P6.3-A — first-party Skill Registry expansion**
- **P6.3-B — read-only MCP expansion and GLM/Claude model profiles**
- **P6.3-C — GitHub and public-preview product truth**
- **P6.3-D — Windows Companion install planning and clean-VM fail-fast**

P6.3 does not add migration `0017`, enable Runtime/Planner/Multi-Agent/MCP
Runtime, connect MCP to Agent Alpha, import third-party executable Skills,
publish OCI images or claim a signed Windows release.

## A. First-party Skill Registry

The source-owned native catalog contains exactly fifteen first-party,
instruction-only Skills. Every entry remains closed to tools, network, secrets,
MCP and Capability expansion. Catalog metadata adds category, tags, recommended
roles and task guidance for Browser discovery, but the persisted immutable
Definition/Version identity remains derived from the sealed source manifest.

Registration compares the complete immutable source projection with any
existing Definition and Version row. Partial identity matches are not accepted.
Resolution and installation enforce two independent ceilings:

- at most eight live Skills for one Agent binding;
- at most 32 KiB of aggregate UTF-8 Skill instructions.

The resolver repeats count, duplicate and byte-budget checks before prompt
composition. An Owner-triggered local scan remains a separate scan-only path;
an unknown local `SKILL.md` cannot enter the first-party registration route.

## B.1 Six-tool read-only MCP preview

The local stdio MCP server remains independently and manually launched. It is
not mounted into Agent Alpha and `MCP_RUNTIME_ENABLED` remains false. Its exact
tool set is:

1. `omnibase_files_list`
2. `omnibase_files_read`
3. `omnibase_git_inspect`
4. `omnibase_files_hash`
5. `omnibase_text_search`
6. `omnibase_git_diff_summary`

The new file hash returns SHA-256 and metadata without file content. Text search
is literal-only and bounded by depth, visited entries, inspected files, bytes,
matches and snippet length. Git diff summary accepts only `worktree|staged`,
uses fixed `--no-renames --no-textconv --no-ext-diff` commands and returns only
name/status/line-count metadata. VCS control directories and secret-like files
remain excluded.

The process has lifetime ceilings for calls, aggregate file bytes and aggregate
Git output. File work is reserved before opening and failure does not refund the
reservation; directory listing has a visited-entry ceiling before sorting. Git
stdout and stderr are charged while they are read, including failed commands,
and an exhausted budget rejects before starting another process. No tool accepts
arbitrary shell, Git flags, regular expressions, glob patterns, network targets,
credentials or write operations.

## B.2 Model-name-first GLM and Claude profiles

The effective user-entered model name remains the primary family input. Unicode
normalization and exact model-name patterns recognize conservative DeepSeek,
GPT, Kimi, GLM and Claude families. Bare brand words, proxy/bridge/emulator
claims and names containing conflicting family evidence resolve to `generic`.
A Provider label or base URL cannot override a recognized or fail-closed model
name. Kimi/Moonshot uses the same closed exact-name grammar in the frontend and
backend. If a requested or observed model name is present but unknown, the
result is terminal `generic`; branded relay/provider hints are not consulted.

Family classification and transport capability are separate facts. The current
gateway is OpenAI-compatible Chat Completions, so GLM and Claude receive only
stable prompt/context guidance. P6.3 does not claim or send unverified:

- GLM `thinking`, `reasoning_effort`, `clear_thinking` or `tool_stream`;
- Anthropic Messages thinking/signature blocks;
- `output_config.effort`, `cache_control`, strict native tools or native MCP;
- official context limits for an opaque relay;
- cache hits when the endpoint did not report them.

Requested and actual model identity must still match exactly. A model name does
not expand tools, filesystem access, MCP, CLI, Vision, Planner or delegation.

## C. Product truth on GitHub and omnibase.chat

The English and Chinese READMEs and `/public-preview` describe the current
personal product instead of the superseded P5-only snapshot:

- migration head `0016`, with `0017` absent;
- file tree, conversation continuity and ChangeSet review;
- one parent plus nine dormant specialists;
- fifteen first-party instruction-only Skills;
- six separately launched read-only MCP tools;
- model-name-first provider profiles;
- the unsigned Windows Companion engineering preview.

The presentation distinguishes **Available**, **Engineering Preview** and
**Deferred**. It must continue to disclose that MCP is not connected to Agent
Alpha, Runtime/Planner/Multi-Agent remain closed, OCI image digests are not
published, Authenticode is not proven and `production_ready=false`.

Updating source does not prove that `omnibase.chat` was rebuilt or deployed.
If the preview host, Docker/WSL or tunnel is unavailable, the correct result is:

```text
PUBLIC_PREVIEW_SOURCE_UPDATED_LIVE_DEPLOYMENT_PENDING
```

## D. Windows Companion install experience

The Companion preserves the P6.2 commands and archive/config integrity checks.
P6.3 adds discoverable help and read-only installation planning for:

```text
user    %LOCALAPPDATA%\Programs\OmniBase
        %LOCALAPPDATA%\OmniBase\config\operator.env

machine %ProgramFiles%\OmniBase
        %ProgramData%\OmniBase\config\operator.env

custom  one explicit absolute local path
```

Machine scope can report that elevation is needed but cannot trigger UAC or
restart itself with greater authority. Planning rejects roots, relative paths,
UNC/network targets, alternate data streams, reparse targets and existing
destinations. Independent review found that repeated path checks cannot close
the final rename TOCTOU window. Therefore mutating `install` is frozen and
returns `install_path_identity_binding_not_implemented` before path/archive
access or any staging/write/move operation. `verify`, `help`, `locations`,
`plan-install`, `init-config` and read-only `doctor` remain available. The
Companion does not write PATH, registry, shortcuts, services, firewall rules or
global IDE/MCP configuration. It does not install, start, repair or mutate
Docker, WSL, Hyper-V or virtual disks.

## Clean Windows VM gate

Clean-machine acceptance is optional and fail-fast. A single read-only preflight
may inspect whether an explicitly named, dedicated clean Windows test VM exists
and whether its ownership/path posture is unambiguous. Any missing cmdlet, VM,
ownership evidence, ACL clarity, guest access or safe disk posture stops the VM
step immediately. Enabling Windows components, restarting the host, modifying
ACLs, creating/moving/resizing a VHDX or touching Docker/WSL is forbidden.

The stopped state is:

```text
CLEAN_WINDOWS_VM_ACCEPTANCE_NOT_PROVEN
NO_VM_OR_VIRTUAL_DISK_MUTATION_PERFORMED
```

Even a successful clean-VM Companion run would not prove published OCI images,
Authenticode, production deployment or Runtime activation.

## Recovery

- Skill drift: disable only the exact installation and preserve immutable
  versions, idempotency and append-only Audit.
- MCP drift: stop the standalone process; do not mount it into Agent Alpha as a
  fallback.
- model-profile drift: fall back to `generic`; never retry with broader native
  fields or another model identity.
- presentation drift: correct source copy and separately report whether the
  public host was deployed.
- Companion drift: discard only the unpopulated preview output and forward-fix
  source; never repair packaging by deleting user data or mutating a virtual
  disk.
