# P34.7 trust-policy ceremony — R0 rehearsal runbook

Status: REHEARSAL ONLY — no production key material.

## Purpose

R0 exercises the trust-policy *governance contract* (candidate schema,
approval packet, validator, negative matrix) without performing a real key
ceremony.  A real ceremony for production keys is a separately approved,
audited operation and is NOT part of R0.

## Hard rules for any R0 key material

1. **No production private keys are generated.**  R0 uses placeholder public
   keys (checked-in examples) or, for validator exercises, ephemeral test
   keys.
2. Ephemeral test keys must be generated in a **disposable temp directory**
   (e.g. `git rev-parse --show-toplevel`-independent OS temp, never inside
   the repository, never inside a worktree).
3. Test keys are **deleted at the end of the test run**; nothing is written
   back into the repository.
4. **Never print, log, upload or commit key material.**  No CI artifact may
   carry a key file; no key file may be staged with `git add`.
5. The validator and all DTOs recursively reject secret-shaped fields
   (`private_key`, `privateKey`, `signingSeed`, `mnemonic`, `passphrase`,
   `api_key`, `bearer_token`, `password`, provider credentials, root `.env`
   locators) — this is enforced by tests, not by discipline.
6. A future real ceremony requires its own approval and a new runbook entry;
   this runbook does not authorize one.

## Rehearsal steps

```text
# 1. Validate the checked-in candidate contract (read-only)
python scripts/production/validate_p34_7_trust_policy_candidate.py \
  --candidate deployment/production/p34-7-trust-policy-candidate.example.json \
  --approval-packet deployment/production/p34-7-trust-policy-approval-packet.example.json \
  --validate-only
# Expected: exit 0, status=candidate/valid_not_approved,
# production_approved=false, approved_digest_written=false,
# activation_allowed=false

# 2. Run the negative matrix
docker compose --env-file .env.example run --rm --no-deps \
  -v .:/workspace -w /workspace/backend backend \
  pytest tests/test_p34_7_trust_policy_candidate.py -q

# 3. Confirm the production pin is untouched
grep '_APPROVED_TRUST_POLICY_SHA256' backend/src/omnibase/production/joint_gate.py
# -> frozenset[str] = frozenset()
```

## What a ceremony is NOT (R0)

- Not a key-generation service; no KMS/HSM adapter exists.
- Not an approval: no digest is ever added to
  `_APPROVED_TRUST_POLICY_SHA256` in R0.
- Not evidence collection: no real production evidence run is produced.
- Not activation: the production Runtime/Planner/Multi-Agent stay disabled.
