# P5.6P Personal Instruction Skills R0

Status: **personal-edition engineering implementation; production activation remains explicit and default-off**.

P5.6P is the personal successor to the historical P5.6A compile-only contract.
It gives one live human Owner a small, reversible way to attach first-party
instruction packages to one exact Workspace and sealed AgentVersion. It is not
the Phase 6 Marketplace, an MCP surface, a script runner or an enterprise
approval ceremony.

## Product boundary

The only executable Skill kind in this increment is `instruction`. A resolved
Skill may shape how the Agent answers, but it cannot add or expand tools,
capabilities, network access, secrets, Memory scope, Planner authority,
Multi-Agent authority or Sandbox execution. Browser Skill CRUD is intentionally
absent in R0; installation is an internal caller-owned transaction used by the
personal composition and disposable acceptance journey.

The precedence order is fixed:

```text
Platform Security Kernel and sealed AgentVersion instructions
-> deterministically sorted first-party instruction Skills
-> Workspace RAG reference data
-> untrusted Memory ContextCapsule reference data
-> current user input
```

Skill text is lower priority than the Security Kernel and AgentVersion. It
cannot turn RAG, Memory or user text into authority.

## Persistence and lifecycle

Migration `0014_p5_6p_personal_instruction_skills.py` owns three global control
plane tables:

- `skill_definitions`: tenant-bound first-party logical identity;
- `skill_versions`: immutable exact-version instruction content and digests;
- `workspace_agent_skill_installations`: exact Tenant, Owner, Workspace,
  AgentVersion and SkillVersion binding.

The database and service both enforce first-party, Workspace-only,
instruction-only, network-deny, secret-free and zero-tool posture. A sealed
version is immutable. An installation can be installed, disabled or revoked;
rollback creates a new exact binding to an older same-definition version and
does not edit the old SkillVersion or historical installation.

All reads revalidate the live Tenant/schema, active tenant-admin Owner, active
Workspace Owner membership and sealed AgentVersion. Cross-Tenant,
cross-Workspace, cross-Owner and cross-AgentVersion references fail closed.

## Runtime binding

Only `build_personal_agent_alpha()` installs the SQL-backed Skill resolver.
Default and engineering Agent Alpha compositions remain Skill-free. The
resolver returns an immutable, deterministically ordered bundle whose canonical
digest is included in the invocation request hash. Changing, disabling,
revoking or rolling back an installation therefore conflicts with reuse of an
old idempotency key instead of silently replaying under different behavior.

The model request receives one separate system message containing the sealed
instruction bundle and an explicit statement that the bundle cannot grant
authority. SSE metadata exposes only bundle digest and item count. It never
returns Skill instructions, physical database locators or internal review
material.

## Feature and production posture

- `AGENT_RUNTIME_ENABLED=false` remains the repository default.
- The exact personal canary may set Runtime true through its existing explicit
  activation path.
- `AGENT_PLANNER_ENABLED=false` and `MULTI_AGENT_ENABLED=false` remain fixed.
- No shell, SQL, arbitrary HTTP, MCP, workflow/script Skill, Marketplace or
  third-party installation exists.
- Enterprise P34.7 trust-policy and multi-person authority assets remain
  frozen and are not personal P6 blockers.

## Recovery

If Skill identity, digest, installation or runtime projection is ambiguous,
disable or revoke the affected installation and keep Runtime false outside the
exact personal canary. Preserve immutable SkillVersion and installation
history. Use a forward fix or restore into a new `omnibase_restore_*` database;
never edit a sealed version, revive a revoked row, destructively downgrade a
populated `0014` database or substitute Core script execution.

## Required local evidence

Local verification is intentionally bounded:

1. focused persistence/resolver and Agent Alpha tests;
2. changed-path Ruff and Mypy;
3. one random `omnibase_test_*` disposable PostgreSQL journey covering
   migration `0014`, exact installation, lifecycle and cross-wire attacks;
4. maintainer map/benchmark, Compose config and `git diff --check`.

GitHub required CI is the complete regression authority.

The complete production-like localhost Agent/SSE combination with Memory,
Skill, cancellation, restart/no-replay, kill switch and restore-new belongs to
P5.9P final personal acceptance. P5.6P does not duplicate that wider journey.
