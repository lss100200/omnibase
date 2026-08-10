# P34.7 Trust Policy R1-A assignment contract

Date: 2026-08-10

Status:

```text
R1_A_ASSIGNMENT_CONTRACT_VALID_NOT_ACCEPTED
AUTHORITY_ASSIGNMENTS_INCOMPLETE
ENVIRONMENT_INVENTORY_NOT_ASSESSED
TRUST_POLICY_NOT_APPROVED
P34_7_BLOCKED_NOT_PROVEN
PRODUCTION_ACTIVATION_DISABLED
```

## Purpose

R1-A closes the machine-readable gap between the R0 candidate contract and a
future real trust-policy review. It records which independent authorities,
custody choices, target-environment resources and production blocker facts
must exist. It does not authenticate a person by itself and does not authorize
the R1-B key ceremony, R1-C execution, R1-E digest change, R1-F production
evidence campaign or Agent Runtime.

The canonical example intentionally leaves every real assignment
`UNASSIGNED`, every custody selection `NOT_ASSESSED`, every target resource
`NOT_ASSESSED` and all eleven blockers `NOT_ASSESSED`. This is the honest state:
the contract shape is valid, but the real-world facts have not been supplied.

## Decision boundaries

```text
R0 candidate bytes structurally valid
  != R1-A authority/environment assignment complete

R1-A assignment ready for independent design review
  != trust policy approved
  != key ceremony authorized

Trust policy approved digest installed
  != P34.7 production evidence ready

P34.7 production Gate ready
  != Runtime activation authorized
```

The R1-A validator always derives these fields as false:

- `trust_policy_approved`
- `approved_digest_written`
- `key_ceremony_authorized`
- `production_evidence_authorized`
- `activation_allowed`
- `runtime_activated`

It also requires migration head `0012`, absence of migration `0013`, and all
three Phase 5 Feature Gates to remain false.

## Authority contract

The authority closed set contains:

- one policy author;
- exactly two independent policy reviewers;
- primary and backup owners for each frozen R0 producer role;
- one ceremony operator and exactly two ceremony observers;
- a custody-attestation issuer for each producer role;
- one digest-change approver;
- one incident/revocation authority.

Each assignment has a state from `UNASSIGNED`, `ASSIGNED_NOT_VERIFIED`,
`VERIFIED`, or `REJECTED`. `UNASSIGNED` must use the literal identity marker
and cannot carry a subject or authentication evidence. `VERIFIED` requires a
format-restricted logical identity, canonical subject, assessed authentication
kind and content-addressed authentication reference.

Separation compares canonical subjects and authentication-reference digests,
not only display labels. The validator rejects author self-review, reviewer and
producer/backup overlap, primary/backup overlap, operator/observer overlap,
digest approver concentration, incident authority holding producer ownership,
and custody self-attestation for the same role.

This contract still does not provide an independent identity root. A future
R1 review package must add a separately pinned authority registry and detached,
replay-bound review receipts before a real design acceptance can be proven.

## Custody contract

The exact seven R0 roles remain:

```text
core
runner
broker
gateway
overlay
recovery_sla
sealer
```

Each role must select one mode from:

```text
offline_hardware
managed_kms_hsm
runner_local_protected
external_signing_service
```

Selection is not proof. `VERIFIED` additionally requires a content-addressed
attestation reference and an independently separated issuer assignment. The
example does not select or attest any custody mode and therefore cannot reach
the design-review-ready posture.

## Target-environment inventory

The inventory is an exact fifteen-slot closed set:

1. Core deployment
2. Linux Runner
3. Network Broker
4. Capability Gateway
5. Overlay member A
6. Overlay member B
7. Independent DERP
8. Provider/object store
9. Non-disposable tenant/RAG
10. PKI/certificate boundary
11. Seven signing roles
12. Time source
13. Observability
14. Capacity/SLA harness
15. Recovery/cleanup authority

Resource identifiers are logical and format restricted. Physical IPs, ports,
database locators, object keys, credentials and `.env` paths are not accepted
public inventory facts. A PROVEN resource requires owner, access authority,
security domain, content-addressed evidence and explicit production
equivalence. Non-disposable tenant/RAG additionally requires a data-owner
authority. Overlay members A/B and DERP cannot share a security domain.

Docker Desktop, WSL, mocks, test doubles, fixtures and disposable resources
cannot be promoted to PROVEN production infrastructure by naming them in the
contract.

## Eleven blocker mapping

The assignment contains exactly eleven independent blocker records:

1. current-source Linux Runner 12/12;
2. Core to Runner production mTLS;
3. Runner to Broker production identity;
4. Runner to Gateway non-disposable mTLS;
5. Broker to Gateway non-disposable mTLS;
6. provider-backed non-disposable workspace recovery;
7. data-owner-authorized tenant/RAG smoke;
8. two real Overlay members plus independent DERP;
9. Overlay compromise/rejoin matrix;
10. dual independent member signatures;
11. production capacity/fault/SLA.

Every blocker has a frozen producer role, command and resource mapping.
`EVIDENCE_COLLECTED_NOT_REVIEWED` is not closed. A blocker can be PROVEN only
when every mapped resource is independently PROVEN and a content-addressed
evidence reference exists. Items 8, 9 and 10 remain three separate facts even
though the downstream composition currently aggregates their evidence.

## File and secret boundary

The file entry accepts only a canonical UTF-8 JSON object in a regular,
non-link, non-reparse file resolving inside the repository. It reuses the R0
secret scanner and strict parser primitives. Secret-shaped fields, private-key
material, API/bearer/database/provider credentials and root `.env` locators
fail closed without echoing values.

## CLI semantics

```powershell
python scripts/production/validate_p34_7_trust_policy_r1_assignment.py `
  --assignment deployment/production/p34-7-trust-policy-r1-assignment.example.json `
  --validate-only
```

`--validate-only` exits 0 when the offline contract is structurally valid,
including the expected `valid_incomplete` state. This is not a production
PASS. `--verify` exits 2 until the independently authenticated real assignment
facts are complete. Invalid structure exits 1.

## Next authorized boundary

The next step after independent review of this implementation is not key
generation. It is to decide whether R1-A should add the separately pinned
authority registry and detached review-receipt contracts. Any real ceremony,
target-environment access, production evidence collection, approved-digest
change or Runtime activation still requires separate explicit authorization.
