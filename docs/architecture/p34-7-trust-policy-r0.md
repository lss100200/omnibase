# P34.7 Trust Policy R0 — candidate trust-governance contract

Date: 2026-08-08
Status: CANDIDATE_CONTRACT_ONLY_NOT_APPROVED

## 1. R0 goals and non-goals

**Goals**

- Establish the engineering-only *governance contract* for the P34.7 trust
  policy: a frozen, closed-set candidate schema, the seven-role identity and
  signing-scope matrix, key-custody metadata, an external approval packet,
  and the rotation/revocation lifecycle state machine.
- Provide an offline candidate validator
  (`scripts/production/validate_p34_7_trust_policy_candidate.py`) whose
  highest positive status is `candidate/valid_not_approved`.
- Prove with an adversarial negative matrix that no candidate can self-approve,
  no producer can self-approve, no secret-shaped field can enter the contract,
  and no Git/source/digest/lifecycle drift can survive validation.

**Non-goals (explicitly out of R0 scope)**

- Generating, storing, printing or transporting real production private keys
  (no key-generation service, no KMS/HSM adapter, no secrets backend).
- Approving any trust policy or writing any digest into
  `joint_gate._APPROVED_TRUST_POLICY_SHA256` (which stays `frozenset()`).
- Collecting real production evidence or changing the P34.7 production
  decision (stays `blocked/not_proven`).
- Opening the production Runtime, Planner or Multi-Agent, creating migration
  `0013`, adding FastAPI/Browser/SDK/ORM/queue/worker surfaces.
- Treating `custody_kind` strings as proof of an actual HSM/KMS — every
  unproven custody posture is reported `not_proven`.

## 2. Seven-role responsibility matrix (frozen closed set)

The candidate must contain exactly these seven producer roles; an eighth
unknown role is rejected:

| Role | Signing scopes (closed set) |
| --- | --- |
| core | core_runtime_posture, core_runner_request_identity |
| runner | linux_runner_isolation, runner_command_receipt, runner_attack_matrix |
| broker | broker_namespace, broker_identity, broker_budget_replay |
| gateway | gateway_mtls, gateway_certificate, gateway_capability_boundary |
| overlay | overlay_membership, overlay_derp, overlay_node_compromise |
| recovery_sla | provider_recovery, capacity_fault_injection, sla_measurement |
| sealer | evidence_seal, cleanup_inventory |

- Wildcards (`*`, `**`, `*.*`, `any`, `all`) and arbitrary extension scopes
  are rejected.
- A producer may only declare exactly its own row; out-of-role scopes are
  rejected.
- The sealer may only sign the final evidence seal and the explicitly allowed
  cleanup/seal boundary — never producer evidence.
- The seven Ed25519 public keys must all be distinct, exactly 64 lowercase
  hex, non-zero; the sealer must not share a key with any producer.

## 3. Why evidence producers cannot self-approve

The P34.7 joint gate treats the trust policy as the ONLY trust anchor.  A
policy whose producer keys, source seal, artifact manifest and gateway pins
are authored by the same party that produces the evidence can always be
re-issued to match the evidence — that is self-approval.  R0 therefore
requires:

- `author_id` of the candidate must equal the approval-packet author;
- reviewers must be distinct logical identities, disjoint from the author and
  from every producer owner;
- the approval packet is a SEPARATE file; a candidate can never carry its own
  approval root, and an approval packet must never embed trust-root/key
  material.

## 4. Why the policy must live outside the evidence directory

The joint gate refuses any trust policy located under the evidence run
directory: a bundle-shipped policy is operator-authored bytes like everything
else in the bundle and proves nothing.  The candidate contract mirrors this —
the approval packet is resolved as an external file, never as part of the
candidate payload.

## 5. Candidate vs approved

| Property | R0 candidate | Production approved (future, NOT reachable in R0) |
| --- | --- | --- |
| lifecycle_state | draft/candidate/rejected/superseded/revoked | approved/… |
| candidate_only | true | false |
| production_approved | false | true (future) |
| digest in `_APPROVED_TRUST_POLICY_SHA256` | never | requires an audited, reviewed change |
| joint gate result | unchanged (blocked/not_proven) | may pass (future) |

The highest status the R0 validator can return is
`candidate/valid_not_approved`; `production_approved`,
`approved_digest_written` and `activation_allowed` are always `false`.

## 6. Git source seal

Reuses the joint gate's strict object-format semantics:

- `git_object_format` ∈ `{sha1, sha256}`;
- `sha1` OIDs are exactly 40 lowercase hex; `sha256` exactly 64;
- commit/tree values are the original Git OIDs — never re-hashed;
- the repository URL must be exact;
- the candidate's `approved_commits`/`approved_trees` are a CANDIDATE source
  set only — they never constitute production approval;
- unknown format, wrong length, uppercase/non-hex and cross-format drift all
  fail closed.

The checked-in examples bind the current main merge commit
`36b48a720c11a583e104a886b9eb9f8ec88e99b3` and its tree
`643cd44fe617b27110a3aea3c26775e158c83704` with
`candidate_only=true`, `production_approved=false`.

## 7. Artifact / command / env / gateway / freshness contracts

- `artifact_approvals`: repository-relative regular-file paths (no traversal,
  no links, no `.env`), raw-byte SHA-256, bound to required joint boundaries.
- `commands`: exactly the six joint boundaries
  (`core_runner`, `runner_broker`, `runner_gateway`, `broker_gateway`,
  `overlay_data_plane`, `recovery_sla`) with exact argv templates.
- `allowed_env_names`: non-empty strings; secret env names are always
  rejected.
- `gateway`: issuer SHA-256 pin, dot-prefixed SAN suffix, bounded validity
  window.
- `evidence_freshness`: bounded `max_evidence_age_seconds` (1..365 days).

## 8. Key lifecycle

Closed set: `generated, registered, candidate, active, rotating, revoked,
archived`.

- The R0 candidate file may only carry keys in the pre-approval states
  (`generated`, `registered`, `candidate`); `active`/`rotating` are history /
  future-compatibility states that the R0 validator can never construct.
- Every key registration carries: key_id, role, algorithm=ed25519,
  public_key, fingerprint_sha256, owner_id, backup_owner_id, created_at,
  candidate_from, planned_expiry, lifecycle_state, custody_kind,
  allowed_signing_scopes, replaces_key_id, revocation_record_id.
- `custody_kind` ∈ {operator_offline, hsm_planned, kms_planned,
  remote_runner_local, external_signing_service_planned} is planned metadata
  only; unproven custody posture is `not_proven`.

## 9. Rotation and revocation

Legal transitions (closed set):

```
generated -> registered
registered -> candidate
candidate -> rejected | superseded | revoked
active -> rotating | revoked
rotating -> active (replacement key only) | revoked
revoked -> archived
```

Rejected: `revoked -> active`, `archived -> active`, `rejected -> active`,
`candidate -> active` (R0 has no approval authority), self-replacement,
rotation cycles, cross-role replacement, same-public-key replacement, revoked
keys keeping signing scopes, deleting historical revocations, and rewriting
historical policy bytes to fake a new candidate.

## 10. Approval packet

The external approval packet pins: candidate path and raw SHA-256, candidate
schema/version, repository, Git object format, candidate commits/trees,
producer key fingerprints, section digests (artifact manifest, command
templates, env allowlist, gateway policy), max evidence age, author, reviewer
ids, review window, decision and reason, plus supersession/rollback links.
Decision ∈ `{draft, candidate, rejected, superseded, revoked}`;
`approved`/`approved_for_production`/`production_ready`/`passed`/`published`
are forbidden in R0.  The packet must be byte-consistent with the candidate
(including section digests) and must not embed trust-root or secret material.

## 11. Residual risks

- `operator_offline` custody is a plan, not a proof — real custody
  attestation requires the future key ceremony (separately approved).
- Logical identities are stable identifiers, not authentication; R0 does not
  authenticate humans.
- The example keys are placeholders; no real key material exists anywhere in
  the repository.
- The R0 contract does not make the joint gate pass — approved production
  evidence remains missing and blocked/not_proven.

## 12. R1 entry conditions

R1 (trust-policy approval design) requires, as a minimum:

1. Independent trust-policy design/approval proposal reviewed outside the
   engineering branch (separate approval authority).
2. A real key-ceremony runbook and a separately approved ceremony execution
   (no production keys in R0).
3. Real custody attestation for every custody posture claimed.
4. Evidence that the approved policy's raw digest is the ONLY new member of
   `_APPROVED_TRUST_POLICY_SHA256`, as an audited code change.
5. All P34.7 production blockers still explicitly not_proven until real
   production evidence exists.
