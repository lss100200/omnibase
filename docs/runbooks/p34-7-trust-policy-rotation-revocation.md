# P34.7 trust-policy rotation and revocation — R0 contract runbook

Status: CONTRACT ONLY — rotation/revocation of real production keys requires
a separately approved ceremony.  R0 validates the lifecycle state machine and
the rotation/revocation records; it never performs or approves one.

## Legal transitions (closed set)

```text
generated -> registered
registered -> candidate
candidate -> rejected | superseded | revoked
active -> rotating | revoked
rotating -> active (replacement key only) | revoked
revoked -> archived
```

Rejected transitions: `revoked -> active`, `archived -> active`,
`rejected -> active`, `candidate -> active` (R0 has no approval authority).

## Operational playbooks (governance contract for the future)

### Key compromise

1. Freeze admission; do not delete or rewrite any evidence or revocation
   history (audit records are append-only).
2. Record a `RevocationRecord` (`revoked_at`, reason, superseded key).  In a
   candidate whose `lifecycle_state == "revoked"` the referenced key is a
   REVOKED historical key: `lifecycle_state == "revoked"`, empty
   `allowed_signing_scopes`, non-empty `revocation_record_id` matching the
   record id, bound 1:1 with exactly one record.  The revoked key must not
   keep signing scopes and can never appear in the producer signing
   allowlist; the record's `revoked_at` must fall inside the approval review
   window.
3. Decide the successor posture:
   - NO successor (one key in the role): the record's
     `superseded_by_key_id` must be `null`; no successor registration and no
     replacement plan may point at the revoked key;
   - WITH successor (two keys in the role): the record MUST name the second
     key, the second key's `replaces_key_id` MUST point back at the revoked
     key, and the revoked key's rotation entry MUST exist and name the
     successor.  The successor must already be a `candidate`-state key at
     `revoked_at` (`created_at <= candidate_from <= revoked_at`,
     `planned_expiry > revoked_at` or null), and the rotation entry's
     `planned_at` must not precede `revoked_at`.
4. Plan a replacement via `RotationEntry` — same role, distinct public key,
   no self-replacement, no cycle.
5. Re-verify from a new clean checkout; the joint gate stays
   `blocked/not_proven` until a real approved policy and evidence exist.

### Sealer compromise

The sealer signs the final evidence seal and the cleanup inventory only.  A
compromised sealer invalidates seal authenticity: revoke the sealer key,
supersede the policy, and treat every seal signed with the old key as
unproven.

### Policy drift

Policy raw bytes are pinned by `_APPROVED_TRUST_POLICY_SHA256` (currently
empty).  Any drift changes the digest: the drifted policy is a DIFFERENT
policy and is never an anchor.  R0 candidates carry `supersedes_policy_sha256`
/ `rollback_policy_sha256` links so supersession and rollback are explicit
contract fields.

### Source withdrawal

A withdrawn source commit/tree is removed from the candidate source seal via
supersession; previously sealed evidence for the withdrawn source stays
preserved and is reported as unproven, never silently accepted.

### Artifact compromise

Artifact approvals bind path/size/SHA-256 to real bytes; a compromised
artifact changes its digest, the candidate's `artifact_manifest_sha256`
binding fails, and the candidate is vetoed until a new candidate re-pins the
artifact.

### Emergency block

Any suspicion of compromise: stop admission, preserve evidence and revocation
history, re-verify from a clean checkout, and do not approve anything.  R0's
empty approved set already guarantees the joint gate cannot pass.

### Forward-fix

Fixes are ordinary forward-fix commits on the engineering branch; historical
policy bytes and revocation records are never rewritten in place.

### Old evidence preservation

Old evidence, manifests, receipts and signatures are preserved for
forensics; they may be re-verified idempotently while unexpired but can never
be re-PASSed after expiry (`evidence_freshness` blocks).

### Supersession

`SupersessionLink` records `supersedes_policy_sha256`, `superseded_at` and
reason; a superseded candidate can never be re-approved in R0.

## Recovery prerequisites

Real recovery (re-approval, key ceremony, evidence collection) requires:

1. Separate approval of the key ceremony and trust-policy approval.
2. Real custody attestation for every claimed custody posture.
3. Real production evidence satisfying the P34.7 joint gate.
4. An audited change adding exactly one approved policy digest to
   `_APPROVED_TRUST_POLICY_SHA256`.
