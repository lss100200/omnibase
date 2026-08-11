# P5.5A ContextCapsule Contract R0 Decision

Date: 2026-08-11

## Decision

```text
P5_5A_MEMORY_CONTRACT_IMPLEMENTED_PENDING_CLEAN_HEAD_REVIEW
CONTEXT_CAPSULE_AND_MEMORY_CANDIDATE_CONTRACT_ONLY
MEMORY_PERSISTENCE_NOT_CREATED
MEMORY_BROWSER_API_NOT_EXPOSED
MEMORY_RUNTIME_NOT_CREATED
MIGRATION_HEAD_0012
MIGRATION_0013_ABSENT
RUNTIME_PLANNER_MULTI_AGENT_DISABLED
```

## Implemented

- closed Memory Policy budgets and privacy posture;
- exact ContextCapsule Tenant/Owner/Workspace/AgentVersion/Task/Invocation
  binding;
- scope-specific selection shape, including Owner-wide `user_private` and exact
  Workspace/AgentVersion rules for the other scopes;
- sealed controlled-shared Owner review evidence bound to Tenant, Workspace,
  Memory ID/version and content digest;
- deterministic selection positions, source/version/evidence and content
  digest vocabulary;
- TTL, token, item and sensitive-item accounting;
- non-delegable, untrusted-data prompt posture;
- Agent-created MemoryCandidate metadata that cannot self-activate;
- exact Candidate-to-Capsule Tenant/Owner/Workspace/AgentVersion/Task/
  Invocation/Policy binding;
- mandatory confirmation for sensitive and controlled-shared candidates;
- forbidden sensitive-inference categories;
- offline CLI and attack-focused tests.

## Local verification before commit

```text
backend/tests/test_p5_5a_memory_contract.py: 65 passed
Ruff check: passed
Ruff format --check: passed
```

Clean-HEAD full regression, sealed Phase 5 contract reseal, remote CI and PR
review remain pending at this decision point. No root `.env`, Provider
credential or business database was read. No migration, persistence, vector
index, Browser API or Runtime path was created.
