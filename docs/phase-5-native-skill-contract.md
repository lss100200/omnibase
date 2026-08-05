# P5.6A First-Party Native Skill Contract

Status: **engineering-only, compile-only, blocked/not_proven**.

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
`0012`, all three Phase 5 Feature Gates false, and absence of Skill runtime,
Browser API or migration `0013`. A valid result still returns
`blocked/not_proven` with exit `2` because P34.7, P5.4 and Skill persistence/API
proof are absent. A safety veto returns `invalid/veto` with exit `1`.

Neither mode reads the root `.env`, connects to PostgreSQL/Redis/MinIO, calls a
model provider, installs a Skill, executes a declared verification command, or
starts Agent/Planner/Executor/Scheduler/Worker processes.

## Next admission sequence

1. Merge and independently verify the P5.3 compile-only Planner contract.
2. Implement and gate P5.4 typed single-Agent Executor with logical Capability
   Tool Gateway only; no shell, SQL or arbitrary HTTP.
3. P5.6B may add persistence and migration `0013` only after explicit user
   authorization and a disposable PostgreSQL Gate.
4. P5.6C may add Browser catalog/install/disable/rollback API and UI after the
   persistence contract is frozen.
5. P5.6D may pin an approved instruction Skill into Agent Alpha by exact
   SkillVersion/digest. The Skill cannot add tools or capability.
6. Workflow Skills wait for P5.3/P5.4 proof. Script Skills wait for production
   P34.5/P34.7 Runner/Sandbox proof. MCP and third-party Marketplace wait for
   Phase 6.
