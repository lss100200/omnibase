# P7.2 Workbench Refinement and Workspace Composition

Status: product law frozen; engineering implementation and installed-product
evidence ready for source/CI review. Production release is not approved.

P7.2 turns the P7 editor-first shell into a lower-density personal workbench
with a full-screen settings center and a versioned, declarative Workspace
composition model. It is a forward-only addition to P7.0 and P7.1. It does not
rewrite their evidence and does not authorize reviewed Save, Terminal, Git,
search, arbitrary plugins, MCP execution, Skills execution, Sandbox activation
or enterprise multi-Agent behavior.

## 1. Product outcome

P7.2 must deliver four connected outcomes:

1. A compact default workbench with progressive disclosure, a focus mode and a
   comfortable density alternative. Explanatory prose does not remain visible
   beside routine controls.
2. A central settings editor, not a narrow sidebar form. Application settings
   and current-Workspace settings are visibly separated.
3. An immutable `standard-workbench` template plus one versioned composition
   profile per Workspace. A Workspace may change its presentation without
   mutating the application template or another Workspace.
4. A proposal boundary through which an AI may request presentation changes.
   The AI cannot approve, apply, broaden capabilities or mutate the standard
   template. The Owner reviews an exact diff and approves an exact request
   digest before a new profile revision is created.

The existing editor, conversation, Agent, run history, blackboard, Provider and
read-only local-file capabilities remain real data sources. Unsupported
surfaces remain explicitly unavailable.

## 2. Information architecture

The desktop shell has five stable regions:

- Activity bar: familiar icon-only navigation and small live badges.
- Context sidebar: Explorer, run history and other scan-oriented lists.
- Editor: conversation, code, task brief and the full settings center.
- Agent rail: current Agent/team activity and composer.
- Bottom panel: output and Agent event log until later phases separately admit
  Terminal or Problems.

Settings is an editor destination. Its navigation is grouped as:

- Application: appearance and accessibility preferences owned by the local
  Owner and never writable through a Workspace proposal.
- Current Workspace: profile identity, density override, focus/layout defaults
  and registered Slots.
- Capabilities: observed live capability posture. This is a status surface, not
  an authorization editor.
- Providers: the existing secret-safe Provider form and test actions.
- Extensions: Skills, MCP and local applications. Entries without a trusted
  runtime/data source remain unavailable.
- Sandbox: observed posture only while desktop Sandbox activation is closed.
- Change review: pending composition proposal, exact diff and revision history.
- Audit: append-only composition decisions and applications.

## 3. Template, profile and Slot registry

`standard-workbench` version 1 is compiled into the application. It is immutable
and contains only source-owned Slot identifiers. Workspace state stores an
override profile, never a modified template.

The initial closed Slot registry is:

| Slot | Region | Data source | Initial posture |
| --- | --- | --- | --- |
| `workspace.explorer` | sidebar | native Workspace + P7.1 file bridge | enabled |
| `conversation.transcript` | editor | conversation IPC | enabled |
| `workspace.brief` | editor | team blackboard IPC | enabled |
| `agent.rail` | right | conversation/team live projections | enabled |
| `run.history` | sidebar | team run IPC | enabled |
| `provider.settings` | settings | Provider IPC + native vault | enabled |
| `event.output` | bottom | real local operation log | enabled |
| `event.agent-log` | bottom | real subscribed event stream | enabled |
| `knowledge.ebook` | editor/sidebar | no trusted packaged bridge yet | unavailable |
| `terminal` | bottom | none | unavailable |
| `source-control` | sidebar | none | unavailable |
| `mcp.catalog` | settings | no desktop MCP runtime | unavailable |
| `skills.catalog` | settings | no desktop Skill runtime | unavailable |
| `sandbox.runtime` | settings | no desktop Sandbox controller | unavailable |

A profile may select density, focus/layout defaults and visibility of admitted
optional Slots. It cannot define JavaScript, HTML, CSS, a process command, URL,
filesystem path, database locator, Provider credential, tool permission,
network rule or Sandbox policy. An unknown Slot or setting fails closed.

The `omnibase-ebook` auxiliary application is the first design case for
`knowledge.ebook`: its theme/document/phase navigation and structured exports
are useful, but its current wildcard `postMessage`, unauthenticated reimport,
hard-coded paths and self-installing launcher are not a trusted integration.
P7.2 records it as unavailable until a separately reviewed packaged adapter
uses an exact origin, closed messages, bounded logical identifiers and an
independent runtime capability.

## 4. Desktop-local persistence

The authoritative store is the existing desktop-local SQLite database behind
native control. The renderer never receives the native control token or a
physical database path.

P7.2 adds:

- one Owner preference row with a CAS `row_version`;
- immutable Workspace composition revisions with canonical JSON and SHA-256;
- one current-revision pointer per Workspace;
- immutable proposals bound to Workspace, base revision, source kind, desired
  profile and request SHA-256;
- one decision/effect record per proposal; and
- append-only `audit_event` rows for preference changes, proposal creation,
  rejection, application and rollback-as-new-revision.

Every read and mutation revalidates the single live Owner and an active
Workspace. Mutations run in `BEGIN IMMEDIATE`, compare the expected revision or
row version, and append their audit record in the same transaction.

Profile JSON is canonicalized by trusted code. The request digest binds:

```text
schema version
template id and version
Workspace id
base profile revision and digest
source kind and optional source reference
complete desired profile
```

The public bridge uses logical IDs only. It never accepts a raw SQL fragment,
physical path, iframe origin, process command or arbitrary JSON Patch path.
The native client independently canonicalizes every accepted profile and
recomputes the revision profile digest, proposal desired-profile digest and
complete request digest using the same sorted-key JSON contract as the Python
authority. A syntactically valid but semantically impossible profile, Slot
catalog, lifecycle record or digest is rejected before it reaches the renderer.

## 5. Proposal, approval and rollback

The lifecycle is:

```text
current profile
  -> validated immutable proposal
  -> exact diff preview
  -> Owner reject OR Owner approve exact request digest
  -> atomic new immutable revision + current pointer + audit
```

Only the host validates and canonicalizes the desired profile. Proposal
creation does not grant a capability and does not change the UI. Approval is a
separate explicit Owner action. Approval fails if Workspace identity, base
revision, base digest, request digest, template version, proposal state or
current profile has changed.

Rollback never rewrites or deletes a revision. Selecting a prior revision
creates a new proposal whose desired profile equals that prior profile; it then
passes through the same preview and approval lifecycle.

If the result of an external effect is ever `pending` or `unknown`, P7.2 must
display reconciliation-required and must not retry automatically. The initial
P7.2 profile application is a single local SQLite transaction and has no
external effect.

## 6. P3.4 security contract localization

P7.2 reuses the semantics of INV-003 through INV-007 and INV-011 through
INV-024 without claiming that the PostgreSQL enterprise control plane is wired
into desktop-local mode:

- Workspace is the long-lived scope; a Run/process is temporary authority.
- Capability scope, budget, expiry and revocation remain independent from UI
  composition.
- Public contracts carry logical identifiers only.
- Missing registry entries, stale versions and unknown states fail closed.
- Security decisions and applications append audit evidence.
- Approval is bound to one exact operation/request hash.
- Workspace identity and active state are revalidated on every mutation.
- revision/row-version act as local fencing against stale writers.
- workload identity, lease and runtime attestation remain required by any later
  executable integration; a profile cannot manufacture those facts.
- canonical templates and derived Workspace profiles stay separate.
- future snapshots use server-generated inventory and restore a new identity.
- pending/unknown external effects never auto-replay.

The AI and the Workspace are requesters, never approvers or capability issuers.
An assistant proposal source is one completed assistant message in the same
Owner, Workspace and Conversation whose bound invocation is `succeeded`.
Unbound messages, user/system messages, failed/cancelled invocations and content
from another scope cannot become a composition proposal. The frontend may find
the candidate envelope, but the backend revalidates this durable identity before
canonicalizing or storing the proposal.

## 7. Explicitly closed in P7.2

- reviewed file Save/write, rename, delete or batch mutation;
- arbitrary plugin code, remote iframe/CDN, executable Skill or MCP-to-Agent;
- arbitrary shell, Terminal, Git or source-control operations;
- desktop Sandbox activation or host policy editing;
- direct Workspace/Agent access to SQLite, PostgreSQL or Provider credentials;
- a Browser/Next composition route or localStorage authority;
- changing the immutable standard template;
- automatic proposal approval or automatic retry after ambiguity;
- production, signing, public release or enterprise multi-Agent claims.

These closures define the boundary of the P7.2 composition foundation, not a
multi-release deferral of the product. P7.3 owns the complete personal
Workspace hot-plug platform described by
`docs/architecture/p7-3-workspace-hot-plug-platform.md`: registry, package and
manifest validation, component lifecycle, typed Slot activation, Skill, MCP,
Sandbox and trusted local-adapter execution, capability grants, revocation,
recovery and Owner controls. Core hot-plug capability must be complete before
P7.4 starts. P7.4 may harden, scale and certify that platform, but must not be
the first implementation of any of those core paths.

## 8. Required verification

- schema upgrade from versions 1 through 9 to the P7.2 schema and fresh v10;
- malformed/unknown profile, Slot, field and request rejection;
- cross-Workspace, archived-Workspace, stale revision, stale digest, replay,
  double approval and AI self-approval attacks;
- canonical digest determinism and exact-diff projection;
- immutable template/revision/proposal and append-only audit enforcement;
- rollback creates a later revision and preserves all history;
- preload exact channel/input/output validation and hostile renderer tests;
- first-frame Workspace switch cannot project the prior profile;
- settings center, density/focus and unavailable capability pure-state tests;
- frontend and desktop test/typecheck/build, desktop-local focused backend tests,
  both maintainer validators, sealed-contract checks and `git diff --check`;
- one fresh installed Windows Electron journey after source review.

## 9. Recovery

On profile or bridge ambiguity, keep the last verified current revision, clear
the renderer projection and require a fresh read. Reject stale proposals and
create a new one from the current profile. Never edit historical JSON or audit
rows, reset a row version, infer success from the rendered layout, enable a
missing capability, replay an unknown effect or mutate another Workspace to
repair the current one.

## 10. Engineering evidence

The first dirty engineering package (`R0`) installed and launched, but exposed
a real bootstrap defect: before the verified composition snapshot arrived, a
null profile disabled every Slot, including the recovery-critical Explorer.
The installed first-run UI therefore lacked the Workspace creation/selection
entrypoint. The forward fix keeps only required source-owned Slots available
while composition is loading or failed; optional and unavailable Slots remain
closed. A pure-state regression proves that Explorer, transcript and settings
survive this recovery posture while Agent/event extensions do not.

The replacement dirty engineering package (`R1`) was built from the exact
working tree over `main@cb1295b4b12df9f080eb0dcf94bc908367c8a7e3`:

- setup EXE SHA-256:
  `55e45d2b546d7bb2bfe94cf465625433d78f554811b8acd3d9b8953cf55ce568`;
- MSI SHA-256:
  `a0725d153680ef4fb5b714100b5fca953f05fd754d27e4fb4ca2451927725a7b`;
- runtime manifest SHA-256:
  `3ab17c2b2ccebeac9d0888f06205cdbb1416d6ae2755c1c75a53b38962232950`.

One Windows Sandbox instance installed and launched the package. A real
Electron window then proved first-run Explorer recovery, Application density,
Application/Workspace separation, exact request-SHA Diff, Owner approve and
reject, rollback-as-new-revision, append-only audit, honest unavailable
Extension/Sandbox views, Slot gating, immediate cross-Workspace isolation,
read-only P7.1 folder authorization/release and focus mode. Focus mode was
approved as revision 5, removed the context sidebar, Agent rail and bottom
panel, and left the central Settings return path reachable.

Before uninstall, the guest reported the Electron process tree plus
RuntimeHost, desktop backend and Next, with loopback listeners on 3000 and
8765. Controlled uninstall returned `0`; those processes/listeners were empty,
the installed executable was absent and `%LOCALAPPDATA%\OmniBase` remained.
The only Sandbox was then explicitly closed and the host had no
`WindowsSandbox*` or `vmwp` process.

Host evidence is retained at
`E:\Agent IDE\OmniBase Artifacts\p7-2-workspace-composition-sandbox-evidence-r1-20260830`.
It includes thirteen screenshots, guest transcript, pre-uninstall process/
listener evidence and the uninstall receipt. This is an elevated disposable
Sandbox and a dirty unsigned package. The build report correctly remains
`source_clean=false`, `source_mode=engineering-dirty`,
`production_ready=false`, `authenticode_verified=false` and
`required_product_journeys_verified=false`. It does not prove a clean-source
release artifact, standard-user/medium-integrity execution, Authenticode,
Provider-backed live calls, public release or production readiness.
