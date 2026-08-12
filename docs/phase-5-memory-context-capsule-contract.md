# P5.5A Memory / ContextCapsule Contract

## Decision

P5.5A freezes the privacy, provenance, scope and budget contract for personal
Memory without creating Memory persistence or Runtime authority.

```text
P5_5A_MEMORY_CONTRACT_IMPLEMENTED
CONTEXT_CAPSULE_COMPILE_ONLY
MEMORY_CANDIDATE_COMPILE_ONLY
MEMORY_PERSISTENCE_NOT_CREATED
MEMORY_RUNTIME_NOT_CREATED
MIGRATION_HEAD_0012
MIGRATION_0013_ABSENT
PHASE5_FEATURE_GATES_FALSE_FALSE_FALSE
```

The authoritative files are:

- `backend/src/omnibase/production/phase5_memory_contract.py`;
- `deployment/production/phase5-memory-contract.example.json`;
- `scripts/production/validate_p5_5a_memory_contract.py`;
- `backend/tests/test_p5_5a_memory_contract.py`.

## P5.5B current persistence increment

P5.5A remains the historical compile-only contract. P5.5B is a separately
reviewed forward increment that implements the tenant persistence and privacy
lifecycle without creating a Browser Memory API or Runtime injection path.

```text
P5_5B_MEMORY_PERSISTENCE_IMPLEMENTED_PENDING_FINAL_REVIEW
MIGRATION_HEAD_0013
ORM_AND_TRANSACTION_SERVICE_IMPLEMENTED
REAL_POSTGRESQL_SERVICE_JOURNEY_PASSED
DELETE_EXPORT_CRYPTO_ERASURE_IMPLEMENTED
POSTGRES_BACKUP_INVENTORY_CAPTURE_IMPLEMENTED
MEMORY_BROWSER_API_NOT_CREATED
MEMORY_COMPILER_RUNTIME_NOT_CREATED
P5_5C_NOT_STARTED
PHASE5_FEATURE_GATES_FALSE_FALSE_FALSE
```

Migration `0013_memory_context_capsules.py` creates the tenant-scoped Memory
tables, append-only and lifecycle triggers, independent vector lanes and exact
Candidate publication bindings. The internal service permits an Agent to
create only a Candidate. Acceptance requires an exact
`memory.candidate.accept` Operation, a live Owner decision, one consumed
Approval and the same Tenant/Workspace/AgentDefinition/Task/Capsule identities.
Publication closes Candidate, Memory and first MemoryVersion inside the same
caller-owned transaction before the service returns.

Owner export contains only logical identity, state, scope, provenance,
retention, evidence and content digests. Owner deletion atomically blocks
selection, records the committed effect and code-only tombstone, erases
Candidate ciphertext/nonce, removes MemoryVersion content and both vector
lanes, then leaves the Memory identity in `deleted` state. Ambiguous outcomes
remain blocked for reconciliation.

The personal cold-backup controller now has one explicitly online, read-only
`capture-postgres-inventory` command. Under the same cold writer barrier as the
selected dump it binds the dump digest, global and tenant migration heads,
server-owned tenant registry, ten Memory tables, required trigger set and both
vector dimensions. Planning, sealing, verification and restore-new planning
remain offline. The inventory format is first introduced by P5.5B as
`omnibase.postgresql-backup-inventory.v1`; no earlier released inventory
consumer exists.

## P5.5C current bounded Runtime increment

P5.5C consumes the P5.5A contract and P5.5B migration-0013 persistence without
adding a new migration or widening the personal product's authority.

```text
P5_5C_BOUNDED_PERSONAL_MEMORY_RUNTIME_IMPLEMENTED_PENDING_REVIEW
CURRENT_PERSONAL_MIGRATION_HEAD_0015
MIGRATION_0016_ABSENT
MEMORY_COMPILER_EXACT_SCOPE_AND_BUDGET_BOUND
CONTEXT_CAPSULE_PERSISTED_BEFORE_PROVIDER
MEMORY_PROMPT_PROJECTION_UNTRUSTED_DATA_ONLY
EXACT_TERMINAL_REPLAY_COMPILE_COUNT_ZERO
MEMORY_BROWSER_API_ABSENT
AGENT_RUNTIME_ENABLED_FALSE_BY_DEFAULT
AGENT_PLANNER_ENABLED_FALSE
MULTI_AGENT_ENABLED_FALSE
```

Only the exact personal single-Owner canary builder injects the SQL-backed
compiler. It revalidates the live Tenant/schema, human Owner, Workspace,
membership, sealed AgentVersion and current Task/Invocation; applies the closed
Memory scope shapes, current controlled-shared review evidence, Candidate TTL,
fixed candidate ceiling and deterministic item/token/sensitive budgets; then
decrypts with an independent authenticated Memory key and verifies plaintext
SHA-256.

The invocation request hash includes the sealed Memory policy digest. A fresh
invocation is reserved before compilation, and the exact ContextCapsule/items
commit before provider dispatch. Exact terminal replay does not compile,
retrieve or create another Capsule. Compiler failure terminalizes the ledger as
`failed/agent_alpha_memory_compile_failed`.

Migration `0015_p5_9p_empty_context_capsules.py` closes the first-Memory
bootstrap cycle. When a fresh invocation has no selectable Memory, the compiler
persists one zero-item, zero-token ContextCapsule with an all-zero sensitivity
summary, commits it before Provider dispatch, and returns no projected Memory
layer. The empty Capsule is an audit/provenance anchor only: it adds no prompt,
no SSE Memory metadata and no authority. The first real MemoryCandidate may then
bind that exact successful Task/Capsule. Exact replay still creates no second
Capsule. Migration `0015` changes only the ContextCapsule token lower bound from
one to zero; `max_tokens` remains positive and every non-empty Capsule retains
the existing item/accounting closure.

Memory text is projected only in process as a separate system message labelled
untrusted reference data below the Platform Security Kernel and AgentVersion.
It cannot grant tools or override instructions. SSE exposes only Capsule ID,
canonical digest and item count. There is no Browser Memory CRUD/search API,
Planner, Multi-Agent, shell, SQL, arbitrary HTTP, MCP or Skill execution in this
increment.

## Identity and scope

Every ContextCapsule is bound to one exact Tenant, human Owner, Workspace,
AgentVersion, Task and Invocation. Selected memories additionally carry their
logical Resource/version, immutable content digest and evidence references.

Allowed scopes are a closed set:

- `user_private`;
- `workspace_private`;
- `agent_private`;
- `controlled_shared`.

`user_private` is Owner-wide and therefore carries neither Workspace nor
AgentVersion. Workspace-private and controlled-shared items bind the Capsule
Workspace without an AgentVersion. An `agent_private` item binds both the same
Workspace and AgentVersion. Cross-Tenant, cross-Owner, cross-Workspace and
cross-AgentVersion selection is rejected before any content could reach a
prompt.

Every `controlled_shared` item also binds a canonical `MemoryReviewEvidence`
record. The record fixes the approving Owner, Tenant, Workspace, Memory ID,
version, content digest, approval decision and review time. The selection
includes both the review ID and canonical review digest, and the review ID must
also appear in its evidence references. An arbitrary UUID or a re-sealed review
for another Memory, Workspace or content digest is rejected.

## Capsule posture

A ContextCapsule is short-lived, non-delegable and explicitly untrusted data.
It cannot replace or override the Platform Security Kernel, AgentVersion
instructions or the typed tool protocol. Each Capsule records:

- compiler policy digest;
- issue and expiry time;
- continuous deterministic item positions;
- selected Memory IDs and versions;
- selection reason;
- sensitivity summary;
- exact token accounting;
- source Resource/version and evidence references;
- canonical Capsule content digest.

The policy owns the initial token budget, retrieval budget, call count, result
tokens, item count, sensitive-item count, deadline and TTL ceilings. A caller
may request less, never more.

## Candidate posture

An Agent may propose a `MemoryCandidate`; it cannot create an active long-term
memory. P5.5A examples accept only `candidate` or `awaiting_confirmation`.
Candidates cannot contain a Provider secret, active-memory identity or inferred
sensitive trait. Sensitive or `controlled_shared` candidates require explicit
human confirmation. Each Candidate must bind an existing Capsule with the exact
same Tenant, Owner, Workspace, AgentVersion, Task, Invocation and Memory Policy;
changing any one of those identities fails closed.

The default policy permanently bans automatic inference of biometric,
financial, health, political, religious and sexual-orientation attributes. It
also requires source evidence, treats all memory as untrusted content and keeps
the Security Kernel above Memory in prompt precedence.

## P5.5A historical boundary and remaining work

P5.5A creates no migration, ORM, database table, vector lane, Browser API,
worker, Runtime compiler or prompt injection path. `--validate-only` returning
exit 0 means only that the static contract is valid. Formal `--verify` remains
`blocked/not_proven` with exit 2 from a clean checkout.

P5.5B supplies persistence, deletion/export, tombstones, independent vector
storage and dump-bound restore-new inventory evidence. It still creates no
Browser Memory governance endpoint, compiler worker, search endpoint or prompt
injection path. P5.5C must separately implement and attack-test that bounded
compiler/search/injection path. Neither increment may silently enable Runtime,
Planner or Multi-Agent.
