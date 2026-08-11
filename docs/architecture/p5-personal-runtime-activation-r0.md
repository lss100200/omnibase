# P5 Personal Runtime Activation R0

## Decision

OmniBase personal edition may run one bounded production canary through the
separately named `personal_single_owner` profile. This profile is not the
engineering Lite lane and is not the frozen enterprise P34.7 trust-policy
lane. It exists for one authenticated human Owner using one Workspace and one
tool-free AgentVersion.

The R0 scope is deliberately exact:

```text
one Tenant
one Workspace
one live active Owner who is also tenant administrator
one sealed AgentVersion
one active interactive invocation
invocation_mode = no_tool
AGENT_RUNTIME_ENABLED = true
AGENT_PLANNER_ENABLED = false
MULTI_AGENT_ENABLED = false
migration head = 0013
migration 0014 absent
default-deny workload network, destinations = []
bounded activation lifetime
append-only rollback receipt
independent irreversible kill marker
```

It does not authorize Sandbox hostile-code execution, shell, SQL, arbitrary
HTTP, MCP, Skills, tools/function calling, Planner, Multi-Agent, member
Overlay access or external side effects.

## Three distinct Runtime lanes

`engineering_lite` remains a development/engineering product demonstration.
It requires the existing Lite conjunction and keeps all production Phase 5
feature gates false.

`personal_single_owner` is the bounded personal production canary described
here. It requires `ENV=production`, Runtime true, Planner and Multi-Agent
false, one exact live Owner scope and an active unexpired canary ledger.

`enterprise_governed` remains frozen. Trust Policy R0/R1 assets, separated
human authorities, key ceremony, target-environment evidence and the approved
digest are not silently reused or bypassed by the personal profile.

An invalid non-empty `PERSONAL_RUNTIME_PROFILE` value fails closed and cannot
fall back to engineering Lite.

## Activation contract

The canonical canary config binds:

- canary, Tenant, Workspace, Owner and AgentVersion UUIDs;
- production/no-tool posture;
- exact concurrency and `top_k` ceilings;
- Runtime=true, Planner=false and Multi-Agent=false plan requirements;
- migration 0013 and migration-0014 absence;
- default-deny/no-destination workload network posture;
- the raw SHA-256 of the sealed Personal Owner readiness config.

The loader accepts canonical minified JSON with keys sorted and exactly one
trailing newline. Unknown fields, pretty-printed rewrites, unsafe feature
flags, non-canonical UUIDs, path escape, readiness digest drift or evidence
drift are vetoes.

The deterministic activation-plan digest is the operator confirmation
boundary. `activate-canary` requires the exact digest printed by `plan`; it
does not infer consent from a boolean or mutable file name.

## State and kill semantics

Each activation receives a new absolute run-scoped state directory:

```text
000001-activate.json
000002-rollback.json       optional terminal event
KILL_SWITCH.json           independent irreversible terminal marker
```

Events use canonical JSON, exclusive creation, contiguous sequence numbers
and a SHA-256 previous-event chain. The filename sequence/event token must
match the content, and non-canonical bytes, future-dated activation, missing
LF or event-name drift fail closed. Supported platforms use a parent-directory
descriptor plus file and directory `fsync`; the fallback rechecks directory
identity before accepting the write. Activation expires automatically.
Rollback is append-only and terminal. Reactivation requires a new state
directory; old history is preserved.

The kill marker wins before ledger parsing. Even a corrupt kill marker keeps
the canary killed. The kill command requires only the absolute state directory,
canary UUID and reason code; it does not trust the config or an intact event
chain.

The hash chain provides integrity and deterministic lifecycle derivation. It
does not provide an independent signature, external timestamp or separate
human authenticity root. The state directory is therefore a server/operator
intent receipt, not a substitute for live authorization.

The filesystem marker and PostgreSQL reservation do not share a transaction,
so R0 does not claim a linearizable cross-system instant kill. Instead the
marker is rechecked after canonical database locks, before any fresh Task
insert, again before transaction-A commit, before the provider boundary and at
stream checkpoints. A detected kill never permits a new provider call; an
already reserved invocation is terminalized through the existing failed or
unknown/reconciliation lifecycle. Operators must also remove the overlay or
restore Runtime=false; a provider call already blocked inside a third-party
transport may only observe the marker at the next checkpoint.

## Live request composition

The Browser dependency selects the personal builder only when the profile is
exactly `personal_single_owner`. The builder then independently verifies:

- the three server-owned feature gates have the exact personal conjunction;
- the config, state and minimal readiness-evidence root are absolute
  server-owned mounts;
- the Personal Owner readiness config and its referenced evidence still match
  their raw-byte SHA-256 bindings;
- the ledger is active and unexpired and binds the same config/plan/canary;
- the current request matches the configured Tenant/Workspace/Owner;
- the tenant is live and migration head is exactly 0013;
- the Workspace has exactly one active membership, role Owner;
- that Owner is the current actor and a live tenant administrator;
- a Model Gateway is configured.

Task Ledger transaction A acquires its canonical Tenant -> actor -> Workspace
-> actor membership -> Binding -> Definition -> Version lock order before the
personal guard runs. The guard then reloads the server-owned config/readiness
bytes, derives time from the same PostgreSQL session, rechecks the active
ledger, exact gates, production environment and migration 0013, and evaluates
the one-Owner invariant under the already locked Workspace aggregate. It also
requires zero non-terminal WorkspaceRuns before any fresh Task insert. A
second invocation therefore cannot use a membership phantom or rely on a
larger generic Workspace quota. Exact replay returns before this fresh-only
slot guard and never creates a second execution.

The facade filters profile discovery to the configured AgentVersion, rejects
all other Tenant/Workspace/Owner/AgentVersion values and applies the configured
`top_k` ceiling. The existing Task, AgentRun, WorkspaceRun, RunLease, workload
identity, budget/effect and reconciliation lifecycle remains authoritative.

## Knowledge boundary

R0 uses the existing Core-owned read-only `RagKnowledgeRetriever`, the bounded
P5.5C Memory compiler and the tool-free Model Gateway. The Memory compiler is
available only in this exact personal composition, selects committed migration-
0013 Memory under the sealed policy and persists a short-lived ContextCapsule
before provider dispatch. The prompt marks that projection as untrusted
reference data; it cannot override the Security Kernel or AgentVersion.

This lane does not route Browser knowledge retrieval through the formal P5.4B
Capability Gateway composition. Consequently it must not be described as
proving Sandbox execution, high-risk Capability consumption, formal Gateway
production composition or workload access to PostgreSQL, Redis, MinIO or
member Overlay endpoints. There is still no Browser Memory CRUD surface.

The full Personal Owner Gate remains required before a future personal
Sandbox/high-risk operation. That future path must preserve exact Approval,
Capability, resource/version, budget, Run/Node fencing and workload identity
reservation; this no-tool canary does not weaken it.

## Deployment boundary

The base `docker-compose.yml` passes empty personal profile/config/state/readiness
values and keeps all three Phase 5 gates false. It does not mount activation
assets. An operator must deliberately apply
`deployment/production/personal-runtime-canary.compose.example.yml` with an
absolute canonical config file, a unique state directory and a minimal
immutable readiness root containing only the referenced readiness config and
evidence in their repository-relative paths. All three are mounted read-only
into the backend; lifecycle writes occur through the separate filesystem-only
controller.

No module mutates process environment variables, launches a service, reads the
root `.env`, migrates a database or installs an enterprise approved digest.

## Recovery

For ordinary stop, append a rollback reason. For ambiguity, compromise,
ledger damage or inability to verify the live Owner, write the kill marker.
Remove or disable the operator overlay and restore Runtime=false before
investigation. Preserve config, all event bytes and the kill marker. Never
delete or edit an earlier event into a passing state and never reuse a
terminal state directory.
