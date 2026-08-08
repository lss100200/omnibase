# P34.7 Trust Policy R0 decision

Date: 2026-08-08

Decision:

```text
CANDIDATE_CONTRACT_ONLY_NOT_APPROVED
```

Explicit statements:

- No production private key was generated, printed, staged, committed or
  uploaded (all key material in R0 is placeholder or ephemeral test material
  in disposable temp directories).
- No trust-policy digest was approved; `_APPROVED_TRUST_POLICY_SHA256`
  remains an empty frozenset.
- No production evidence was generated or forged.
- No production Runtime / Planner / Multi-Agent was activated;
  `activation_allowed` stays false.
- Migration head stays `0012`; migration `0013` is absent.
- All three Phase 5 feature gates stay false.
- P34.7 remains `blocked/not_proven`.
- The root `.env` was not read; no business database was accessed or migrated.

The highest positive status produced by this round is
`candidate/valid_not_approved` from
`scripts/production/validate_p34_7_trust_policy_candidate.py` — a valid R0
candidate changes nothing about the P34.7 production decision.

## Review-fix Round 1 (2026-08-08)

Status after the independent review round:

```text
REVIEW_FIX_IMPLEMENTED_PENDING_INDEPENDENT_REVIEW
```

The six review findings were fixed in this round (no push / no PR / no merge;
the change sits in one forward-fix commit on the branch):

- **P1-1** — the object-level validator can no longer claim the candidate raw
  digest was verified: it is structural-only and reports status
  `candidate/structural_valid` with blocker `candidate_digest_unverified`.
  Only the file-level entry verifies `SHA256(raw bytes)` against
  `candidate_policy_raw_sha256` and may produce `candidate/valid_not_approved`.
- **P1-2** — the approval-packet `decision` must equal the candidate
  `lifecycle_state` (veto on mismatch), the review window must open after the
  candidate's `created_at`, `superseded` requires a complete supersession link
  echoed by the packet, `revoked` requires revocation records plus a rollback
  policy, and every non-candidate lifecycle reports `<lifecycle>/not_approved`
  with blocker `lifecycle_not_candidate`.
- **P1-3** — both files must resolve inside the repository root and the
  packet's `candidate_policy_path` must equal the candidate's actual
  repository-relative POSIX path.
- **P1-4** — sensitive environment names are rejected after
  case/separator normalization (`openai_api_key`, `OpenAiApiKey`,
  `postgres_password`, ...) and root `.env` locators are rejected in Windows
  and case variants; argv entries and env names are locator-checked.
- **P2-1** — the artifact-approval set must cover the six required joint
  commands exactly once (no missing, duplicate, unknown or key/path-drifted
  coverage).
- **P2-2** — reviewers are additionally disjoint from producer/key
  `backup_owner_id`.

All previous explicit statements (no approval, empty approved-digest set,
`blocked/not_proven`, migration head `0012`, no feature-gate opening, no
root `.env`, no business database) remain true.

## Review-fix Round 2 (2026-08-08)

Status after the second independent review round:

```text
REVIEW_FIX_ROUND_2_IMPLEMENTED_PENDING_INDEPENDENT_REVIEW
```

Five findings were closed in this round (no push / no PR / no merge; one
forward-fix commit on the branch):

- **P1-1** — every command template's internal `command` must exactly equal
  its map key (six map keys and six internal commands each form the exact
  `_REQUIRED_COMMANDS` closed set); command swaps, internal duplicates,
  internal misses and unknown commands veto even after every section digest,
  the candidate raw digest and the packet digest are resealed.
- **P1-2** — the `revoked` lifecycle is now REACHABLE: inside a revoked
  candidate only, a historical key may declare `lifecycle_state ==
  "revoked"` with empty signing scopes and a non-empty
  `revocation_record_id`; records and revoked keys bind 1:1 (same role, same
  key id, same record id, unique ids, equal counts); non-revoked candidates
  cannot embed revoked keys and current keys keep exactly their frozen role
  scopes.  A real `revoked/not_approved` file-level positive control plus the
  full negative matrix (missing/duplicate record, record-id/role/key-id
  drift, revoked key keeping scopes, revoked key outside a revoked candidate,
  record pointing at a candidate key) were added.
- **P2-1** — a command repeated inside ONE artifact (`["core_runner",
  "core_runner"]`) vetoes before frozenset conversion; cross-artifact
  duplicate coverage still vetoes; both structural and file-level tests
  (with fully resealed digests) were added.
- **P2-2** — lifecycle timeline closure: `superseded_at` / `revoked_at` must
  fall inside the review window (`review_started_at <= event <=
  review_completed_at`) and never precede `created_at`; comparisons run on
  normalized UTC datetimes (Z / +00:00 only, non-zero offsets fail closed),
  bounds are inclusive, and mixed-spelling/equal-instant tests were added.
- **P2-3** — the env allowlist rejects duplicate entries before frozenset
  conversion; the veto survives resealed section/raw digests.

All Round 1 boundaries (structural-only object entry, file-level raw-byte
verification, lifecycle/decision binding, repo containment and path binding,
secret env normalization, Windows `.env` locator rejection, six-artifact
coverage closure, backup-owner/reviewer separation, CLI exit 0 only for
`candidate/valid_not_approved`) remain in force.
