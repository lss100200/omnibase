# P5.6A First-Party Native Skill Contract

Status: **P5.6A historical compile-only predecessor; P5.6P personal successor in progress**.

P5.6A freezes the first product-level OmniBase Skill contract without adding a
Skill runtime or claiming production readiness. The contract exists so later
persistence, Browser APIs and Agent integration cannot invent a broader or
incompatible authority model.

## Product meaning

A Skill is a versioned, reviewable behavior package that an Agent may reference
by exact version and digest. It is not an operating-system plugin, a credential
container, an MCP server, an arbitrary download, or a way to bypass the
Capability Gateway. P5.6A accepts first-party manifests only.

The frozen kinds are:

- `instruction`: bounded instructions and strict input/output schemas. It has
  no tools, capability requirements or tool-call budget and cannot increase the
  AgentVersion's authority.
- `workflow`: a future typed plan fragment. P5.6A may parse a `tested` manifest,
  but it cannot approve, publish, expand or dispatch it.
- `script`: a future Sandbox-only workload. P5.6A may parse a `tested` manifest,
  but Core never executes it and it cannot be approved or published.

Third-party Marketplace and MCP integration remain Phase 6 work.

## Contract boundary

The executable contract is
`backend/src/omnibase/production/phase5_skill_contract.py`. It freezes:

- logical UUID identities and unique stable keys;
- strict SemVer and immutable SkillVersion content;
- raw UTF-8 instruction digest;
- local, closed JSON Schema with bounded depth, local-only acyclic `$ref`,
  closed object properties and bounded patterns/collections;
- exact required logical tool IDs and capability requirements, with wildcard
  and privileged pseudo-identifiers rejected;
- server-owned token, tool-call, wall-clock and cost ceilings;
- network `deny`, no secrets, source/lock/SBOM digest fields and typed
  shell-free verification profiles with fixed argv and `network_allowed=false`;
- same-definition, strictly older rollback references;
- Workspace-only installation scope and first-party provenance;
- input-order-independent canonical digesting.

P5.6A deliberately refuses `approved` and `published`. Those states require a
later Gate that verifies real sealed source, dependency lock, SBOM, signature,
secret scan, paired evaluation, human review and rollback evidence. A string
such as `signature_status=verified` is not evidence by itself.

## Admission behavior

The example contract is
`deployment/production/phase5-skill-contract.example.json`. It contains the
`Workspace Librarian` instruction Skill as a `tested`, `unverified` contract
fixture. Its digest fields are deterministic fixture values, not production
attestation or publication evidence.

Run:

```text
python scripts/production/validate_p5_6a_skill_contract.py --validate-only
python scripts/production/validate_p5_6a_skill_contract.py --verify
```

`--validate-only` parses the contract and returns exit `0` with state
`blocked/not_proven`. It does not hash Git source or inspect feature-gate
environment values.

`--verify` requires a clean checkout, the exact public remote, migration head
`0014` and all three Phase 5 Feature Gates false. It validates only the P5.6A
manifest contract and never authorizes or starts the P5.6P successor. A valid
result still returns `blocked/not_proven` with exit `2`; that historical result
is not a veto against the separately reviewed personal persistence/runtime
increment. A safety veto returns `invalid/veto` with exit `1`.

Neither mode reads the root `.env`, connects to PostgreSQL/Redis/MinIO, calls a
model provider, installs a Skill, executes a declared verification command, or
starts Agent/Planner/Executor/Scheduler/Worker processes.

## Personal successor

The explicitly authorized P5.6P successor combines the smallest useful parts
of the former B/C/D sequence for the personal edition: migration `0014`,
internal first-party instruction persistence, exact Workspace/AgentVersion
installation, disable/revoke/rollback and personal Agent Alpha prompt
projection. It adds no Browser Skill catalog in R0 and grants no tools,
capabilities or network authority.

Workflow/script Skills, MCP, third-party Marketplace and enterprise publication
review remain frozen. See
`docs/architecture/p5-6p-personal-instruction-skills-r0.md`.
