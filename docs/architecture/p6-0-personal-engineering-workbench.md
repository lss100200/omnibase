# P6.0 Personal Engineering Workbench

Status: **P6.0-A implementation in progress; local frontend verification passed**

P6.0 turns the P5 personal Runtime into a visible engineering workbench for
one human Owner. It does not revive the enterprise P34.7 lane and does not
enable Planner, autonomous Multi-Agent, MCP or arbitrary tools.

## Product sequence

| Increment | Product boundary |
| --- | --- |
| P6.0-A | Workbench shell, conversations, timeline, parent Agent and dormant specialist employees |
| P6.0-B | Authorized file tree, file opening and explicit context states |
| P6.0-C | Task ChangeSets, diff review and safe three-way rollback |
| P6.0-D | Five-provider adaptation, gears, context/cost controls and end-to-end acceptance |

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

This is deliberately not migration `0016`. Existing Task/Run tables are an
execution ledger, not conversation history. Existing Memory and
ContextCapsules are curated context, not raw transcripts. Cross-device sync
will require a later, separate tenant/user/workspace-bound conversation model.

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
