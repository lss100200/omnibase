# P34.7 Trust Policy R1 preparation plan

Date: 2026-08-09

Status:

```text
DESIGN_PREPARATION_ONLY_NOT_APPROVED
```

This document turns the R0 entry conditions into an executable governance
plan. It does not approve a trust policy, authorize a real key ceremony, add a
digest to `_APPROVED_TRUST_POLICY_SHA256`, collect production evidence, or
activate any Runtime.

## 1. Current main-line truth

The following state is authoritative at the start of R1 preparation:

```text
P34.7 hardened joint Gate code = merged on main
Trust Policy Candidate R0 = merged on main
R0 highest status = candidate/valid_not_approved
_APPROVED_TRUST_POLICY_SHA256 = frozenset()
P34.7 production total Gate = blocked/not_proven
activation_allowed = false
AGENT_RUNTIME_ENABLED = false
AGENT_PLANNER_ENABLED = false
MULTI_AGENT_ENABLED = false
migration head = 0012
migration 0013 = absent
```

R0 remains the contract authority for candidate validation. R1 must not weaken
its seven-role closed set, approval separation, secret scanning, raw-byte
digest verification, artifact/command closure, lifecycle chronology, or
rotation/revocation rules.

## 2. Decisions that remain separate

R1 preserves four independent decisions. Passing one does not imply the next:

| Decision | Output | Does not authorize |
| --- | --- | --- |
| Policy design acceptance | Reviewed proposal and responsibility matrix | Key generation, digest approval, evidence collection, Runtime |
| Key-ceremony authorization | Approved one-run ceremony brief | Policy approval, production evidence, Runtime |
| Trust-policy approval | Audited policy bytes and one approved raw digest | P34.7 PASS, service activation, Phase 5 gates |
| P34.7 production admission | Fresh signed evidence bundle with zero blockers/vetoes | Automatic deployment or Phase 5 activation |

Production Agent Runtime remains a fifth, separately approved decision after a
real P34.7 `ready` result.

## 3. Required authority separation

No single identity may author, produce, review, approve, seal, and activate the
same trust chain. Before a ceremony is authorized, the controller must record
stable logical identities for these responsibilities:

| Responsibility | Minimum separation rule | Assignment status |
| --- | --- | --- |
| Policy author | Not a reviewer; not any producer/key owner or backup owner | `UNASSIGNED` |
| Independent policy reviewers | At least two distinct identities; neither is the author, producer owner, key owner, or backup owner | `UNASSIGNED` |
| Seven producer owners | One owner for each frozen R0 role: core, runner, broker, gateway, overlay, recovery_sla, sealer | `UNASSIGNED` |
| Producer backup owners | Distinct from reviewers; recorded at producer and key level | `UNASSIGNED` |
| Ceremony operator | Executes the approved ceremony steps; cannot alone approve the resulting policy | `UNASSIGNED` |
| Ceremony observers | Independently witness device state, public-key export and cleanup without receiving private-key material | `UNASSIGNED` |
| Custody attestation issuer | Produces verifiable evidence for each claimed custody posture | `UNASSIGNED` |
| Digest-change approver | Reviews the exact raw policy bytes and code diff that would add one digest | `UNASSIGNED` |
| Incident/revocation authority | Can freeze admission and begin revocation without gaining producer signing authority | `UNASSIGNED` |

Logical identity labels are not authentication. R1 must define how each human
or service identity is authenticated and how its approval is recorded outside
the evidence bundle.

## 4. Custody decision record

Every producer role needs one explicitly selected custody mode. Strings such as
`hsm_planned` or `operator_offline` are plans, not proof.

| Custody option | Required proof before policy approval | Key risk |
| --- | --- | --- |
| Offline hardware/token | Device identity, initialization record, non-exportability or controlled export statement, sealed storage and recovery ownership | Loss, unrecorded duplication, weak backup handling |
| Managed KMS/HSM | Provider/account/region/key identifier, access-policy digest, audit-log availability, rotation/revocation behavior and separation of administrators | Provider control-plane compromise or permission drift |
| Runner-local protected key | Host identity, filesystem/device protection, process allowlist, backup/restore and host-rebuild procedure | Node compromise and key extraction |
| External signing service | Service identity, mTLS/auth policy, request authorization, replay controls, audit retention and outage behavior | Remote service compromise or ambiguous signing outcome |

The selected mode may differ by role, but the evidence must be real for every
claim. A placeholder public key or an R0 enum value is never custody evidence.

## 5. R1 work packages

### R1-A — policy and authority proposal

Deliver a candidate-derived proposal that freezes:

- the seven producer roles and exact signing scopes;
- author, reviewer, producer owner and backup-owner assignments;
- selected custody mode and proof requirements for every key;
- approved repository, Git object format, source commits/trees and artifacts;
- six exact joint command templates, environment allowlist and certificate
  policy;
- maximum evidence age and review window;
- rotation, revocation, supersession and rollback authorities;
- emergency freeze and incident escalation contacts.

Exit condition: two independent reviewers accept the design, but the policy
still remains `candidate/valid_not_approved`.

### R1-B — key-ceremony runbook

Create a separate, single-run ceremony document containing:

- ceremony ID, date/time window and approved physical/logical location;
- exact participants and separation checks;
- device/KMS/HSM preflight and clean-room requirements;
- generation/import procedure for each role;
- public-key and fingerprint export procedure;
- private-key non-display/non-log/non-repository controls;
- custody handoff, backup, recovery and destruction rules;
- abort, partial-completion and incident procedures;
- append-only ceremony receipt and observer attestations;
- post-ceremony inventory and zero-secret repository scan.

Writing this runbook does not authorize executing it. Ceremony execution needs
a separate explicit approval referencing the exact runbook digest.

### R1-C — ceremony execution

Only after separate approval:

1. Verify the exact runbook bytes and participant identities.
2. Establish the approved environment and disable unapproved recording/logging.
3. Generate or register each key in its selected custody boundary.
4. Export only public keys, public fingerprints and custody-attestation
   references.
5. Verify all seven public keys are unique and the sealer is independent.
6. Create the policy candidate and approval packet from the observed facts.
7. Scan all outputs for forbidden secret-shaped data.
8. Preserve append-only receipts and abort on any ambiguity.

No private key, seed, mnemonic, passphrase, recovery phrase, API key, bearer
token, database credential or root `.env` content may enter Git, CI artifacts,
chat, screenshots, normal logs or the evidence bundle.

### R1-D — independent approval review

Reviewers must verify:

- raw policy bytes and their SHA-256;
- candidate/packet path and digest binding;
- all identity-separation rules;
- real custody attestations;
- source and artifact manifests against actual bytes;
- command/env/certificate policies;
- key chronology, rotation/revocation and rollback readiness;
- no secret leakage and no production activation side effects.

The review output must identify the exact policy digest under consideration.
It must not contain a private key or an alternate self-provided trust root.

### R1-E — audited approved-digest change

This is a future separately authorized change, not part of this preparation
plan. Its allowed scope is deliberately narrow:

```text
add exactly one independently approved raw policy SHA-256
to joint_gate._APPROVED_TRUST_POLICY_SHA256
```

The change must:

- start from a fresh, clean, current `main` checkout;
- contain the reviewed policy bytes and immutable approval references;
- prove the new set is the previous set plus exactly one digest;
- preserve all Phase 5 Feature Gates as false;
- preserve migration head `0012` and absence of `0013`;
- produce no production evidence and start no service;
- pass an independent code/security review and the full joint-gate attack
  matrix before merge.

Policy approval makes valid production evidence *admissible*. It does not make
P34.7 pass and does not authorize Runtime activation.

### R1-F — signed production evidence campaign

After policy approval, collect fresh target-environment evidence using the
approved producer keys, artifacts, command templates and time window. The
inventory and eleven-blocker mapping live in
[`p34-7-target-environment-evidence-plan.md`](p34-7-target-environment-evidence-plan.md).

## 6. Mandatory stop conditions

Stop and keep `blocked/not_proven` if any of the following occurs:

- an authority assignment is missing, duplicated or violates separation;
- any custody claim lacks real attestation;
- a private key or secret-shaped value appears in an output;
- policy, packet, source, artifact or command bytes drift after review;
- the ceremony is run outside its approved time/location/participant set;
- an operation outcome is `pending` or `unknown` and has not been reconciled;
- a component identity, certificate, lease, fencing token or signer is stale;
- the evidence window expires;
- any production resource is represented by Docker Desktop, WSL, a mock,
  test double or disposable fixture;
- any request attempts to enable Runtime/Planner/Multi-Agent or create
  migration `0013` as part of R1.

## 7. Explicit approvals still required

The user's approval of this preparation plan authorizes documentation and
read-only inventory only. These remain separate future approvals:

1. Exact Trust Policy R1 design acceptance.
2. Exact key-ceremony runbook execution.
3. Exact approved-digest code change.
4. Access to each non-disposable target environment and data-owner smoke.
5. P34.7 final production admission decision.
6. Any Phase 5 production Runtime activation.

## 8. Completion criteria for this preparation phase

This preparation phase is complete when:

- this plan and the target-environment inventory are reviewed and merged;
- roadmap and handover state match current `main`;
- every authority and infrastructure slot is either assigned/available or
  explicitly `NOT_ASSESSED`/`MISSING`;
- no policy digest is approved and no private key is created;
- P34.7 and Phase 5 production status remain unchanged.

Expected terminal status:

```text
R1_PREPARATION_READY_FOR_AUTHORITY_AND_ENVIRONMENT_ASSIGNMENT
TRUST_POLICY_NOT_APPROVED
P34_7_BLOCKED_NOT_PROVEN
PRODUCTION_ACTIVATION_DISABLED
```
