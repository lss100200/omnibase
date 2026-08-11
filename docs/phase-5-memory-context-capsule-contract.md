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

## What is not implemented

P5.5A creates no migration, ORM, database table, vector lane, Browser API,
worker, Runtime compiler or prompt injection path. `--validate-only` returning
exit 0 means only that the static contract is valid. Formal `--verify` remains
`blocked/not_proven` with exit 2 from a clean checkout.

P5.5B must separately implement persistence, deletion/export, tombstones,
independent vector storage, restore-new and migration compatibility. P5.5C must
then implement the bounded compiler/search/injection path. Neither increment
may silently enable Runtime, Planner or Multi-Agent.
