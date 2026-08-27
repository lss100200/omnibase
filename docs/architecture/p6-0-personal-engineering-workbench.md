# P6.0 Personal Engineering Workbench

Status: **P6.0-A-D implemented; local engineering acceptance passed, browser product review pending**

P6.0 turns the P5 personal Runtime into a visible engineering workbench for
one human Owner. It does not revive the enterprise P34.7 lane and does not
enable Planner, autonomous Multi-Agent, MCP or arbitrary tools.

## Product sequence

| Increment | Product boundary                                                                        |
| --------- | --------------------------------------------------------------------------------------- |
| P6.0-A    | Workbench shell, conversations, timeline, parent Agent and dormant specialist employees |
| P6.0-B    | Authorized file tree, file opening and explicit context states                          |
| P6.0-C    | Task ChangeSets, diff review and safe three-way rollback                                |
| P6.0-D    | Five-provider adaptation, gears, context/cost controls and end-to-end acceptance        |

Skills, MCP, SQL visualization, CLI adapters, Email/remote messaging and
browser/desktop control belong to later P6.x work.

## P6.0-A contract

`/dashboard` is the canonical personal workbench. `/agents` remains the Agent
Builder and low-level Runtime diagnostic surface; `/chat` remains the legacy
RAG question surface.

The first session repository is browser-local and versioned. Its storage key
is tenant/user scoped. It stores conversation projection only and never stores
access/refresh tokens, Provider credentials, Capability material or physical
database locators. Corrupted or future-schema records fail safe to a fresh
local session.

Conversation history remains browser-local. Existing Task/Run tables are an
execution ledger, not conversation history, and Memory/ContextCapsules are
curated context rather than raw transcripts. Migration `0016` is separately
authorized only for user-owned, Workspace/AgentVersion/employee-role model
selection metadata; it does not create a conversation service. Cross-device
session sync still requires a later tenant/user/workspace-bound model.

P6.0-A provides exactly one active parent Agent and nine dormant specialists:

1. Product Manager
2. UI/UX Designer
3. Frontend Engineer
4. Backend Application Engineer
5. Data and Storage Engineer
6. Security Architect
7. Test Engineer
8. Operations and Release Engineer
9. Technical Documentation Engineer

A normal message routes to the parent Agent. An explicit `@` routes to exactly
one recognized employee after Unicode NFKC normalization. Unknown, duplicate
or multiple mentions fail before network dispatch. The specialist is a
request-scoped role context over the existing single personal Agent Runtime;
it is not a second autonomous Runtime. Generated text never triggers another
employee. Success, failure and cancellation all return the specialist to
dormant UI state.

## Recovery and limits

The local projection is capped at 80 sessions, 400 terminal messages and 1,200
timeline events per session, 768 KiB per session and 4 MiB for the complete
store. The active and pinned sessions are never silently evicted. When all
capacity is protected, creation or persistence fails visibly until the Owner
unpins or archives a session. Only terminal messages enter the durable
projection; partial SSE chunks are not serialized as success. Provider replay
remains governed by the P5 ledger and is never initiated from recovered browser
history.

The parser uses a closed field and enum set through every nested object. Before
local persistence, Provider credentials, bearer/JWT material, database URLs,
private keys, Capability values, environment secrets and physical locators are
replaced with a non-secret marker. The exact final specialist role wrapper is
validated against the 32,000-character Agent Alpha request limit before any
durable append or network request.

If P6.0-A becomes unsafe or corrupt, remove only the scoped
`omnibase.p6.workbench.v1:<tenant>:<user>` browser record and fall back to the
existing `/agents` diagnostic surface. Do not edit Task, Run, Memory or audit
rows to reconstruct a conversation.

## P6.0-B authorized local files

The file surface is browser-first and starts only from an Owner click on
`showDirectoryPicker`. OmniBase does not scan the computer, home directory or
an unselected Workspace. Handles live only in page memory and are cleared on
tenant or Workspace change; neither handles, file bodies nor physical absolute
paths enter localStorage or public DTOs.

Directories enumerate lazily when expanded. Logical names are normalized and
reject traversal, `.git`, `.ssh`, cloud credential directories, `.env*` and
private-key names. A bounded tree budget limits depth, nodes, files,
directories and declared bytes. Type detection prefers magic bytes and strict
text probes; misleading extensions cannot grant an image, PDF or text
capability.

`OPEN`, `CONTEXT` and `PINNED` are separate states. Opening only previews.
Context and pinned text are re-read immediately before invocation and size,
mtime, type and request budgets are revalidated. Selected text is appended as
JSON `untrusted_workspace_file_context`; it is data, never executable
authority. Images and PDFs can be previewed through revocable object URLs but
never enter the text prompt. Other binaries expose metadata only. A browser
cannot reliably invoke the operating-system default application, so that
control is visibly disabled until a separately reviewed native bridge exists.

## P6.0-C local ChangeSets and rollback

The current Runtime has no file tool and does not write local files. P6.0-C
therefore records only an Owner-reviewed local text edit after a successful
Task. Each ChangeSet binds the live tenant, Workspace, Task ID and invocation
ID, seals the exact task-start state `B` and reviewed after-state `A`, and
computes a canonical SHA-256 manifest. The UI never infers file edits from an
Agent's natural-language answer.

Before writing, the selected file is re-read and compared with its displayed
snapshot. After writing, the result digest is independently re-read. The
ChangeSet exposes Before/After audit content. Rollback observes current state
`C`, validates the owner and manifest, and performs bounded three-way text
merge with `A` as base and `B` as the rollback target. Non-overlapping user
changes survive; overlap, binary input, path drift, digest drift or owner drift
fail closed. A final compare-and-swap re-read happens immediately before the
rollback write.

The File System Access API has no multi-file transaction and cannot eliminate
the last write-time race or guarantee recovery from browser crash, permission
loss or disk exhaustion. P6.0 labels this honestly: an interrupted write is
`recovery_required`, keeps the sealed before-state in memory and offers a
conditional restore only when the live digest still matches. This is personal
local recovery, not enterprise atomic mutation.

## P6.0-D2 model profiles, per-role settings and four gears

The product recognizes DeepSeek, GLM, Kimi, GPT and Claude using the user-entered
model name first. The observed actual model returned by the Provider is still
the runtime identity authority. An explicit family override is only a fallback
when the model name is unrecognized; Provider name or base URL is a weak final
hint, and conflicting family tokens fail closed to the generic profile.

The parent and nine dormant specialists each expose one fixed model-setting
entry. By default all ten inherit the same saved Provider URL, encrypted key and
model. A role may reference another saved credential and/or override its model
name, then return to inheritance. The role table stores only logical IDs,
model/family metadata, optimistic version and exact-test evidence. It never
copies API keys, ciphertext or nonces and Browser DTOs never return them.

An overridden model name is `pending` until the exact requested model is tested
through its selected credential. The test result binds the override row ID and
version, credential/key identity, Provider/base URL and model ID. Mutation,
deletion/recreation, credential rotation, membership loss, Workspace generation
drift or Agent binding drift invalidates the result. Runtime dispatch freezes
the same role/model/configuration identity into the invocation request digest,
and the Provider adapter still requires the actual returned model ID to match.

Migration `0016` creates only this tenant-owned preference table and a composite
credential/user ownership constraint. Its populated downgrade and global-before-
tenant downgrade paths fail closed; recovery remains forward-fix or restore-new.
It does not authorize Planner, Multi-Agent, Skills, MCP, CLI, Vision, arbitrary
tools, enterprise Trust Policy approval or production evidence.

The selected `economy`, `standard`, `deep` or `audit` gear actually controls
the allowed Agent Alpha `top_k`, local file-context budget and a concise prompt
guidance block. It does not claim a native provider reasoning API.

Model-name recognition selects conservative prompt guidance only; it is not
proof of native reasoning, structured output, context size, cache, tool or
vision capability. Exact-model capability controls remain disabled until a
separate adapter proves them. The target output token value is an interface
budget only because Agent Alpha does not expose a corresponding request field.
Tools, MCP, CLI, Vision and autonomous delegation remain false.

The exact employee role message, generic adaptation and compiled file context
are counted together and must remain at or below 32,000 characters before any
network request. Usage accepts only finite, non-negative input/output/total and
optional reasoning token values. Monetary cost stays `rate_not_configured`
unless an explicit rate table is supplied; OmniBase never guesses vendor
pricing.

## Maintainer boundary and current state

`frontend/lib/p6-files.ts`, `p6-file-handles.ts`, `p6-changesets.ts` and
`p6-model-profiles.ts` are the executable B/C/D contracts. Secret-name
rejection occurs before `getFile()`. Shared tree admission is serialized in a
monotonic budget. A selected text file binds its reviewed SHA-256 digest in
addition to size and mtime, and async request preparation revalidates the live
tenant/user, session, Workspace, Agent and gear scope before dispatch.

Local engineering acceptance does not activate Planner, Multi-Agent, MCP, CLI,
native model controls or automatic Agent filesystem mutation. Migration `0016`
authorizes only scoped role model preferences. Owner browser review, push,
merge and deployment remain separate decisions.
