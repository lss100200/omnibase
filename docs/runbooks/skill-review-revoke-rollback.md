# Skill Review, Revoke and Rollback Runbook

This runbook describes the fail-closed operator posture for first-party native
OmniBase Skills. P5.6A remains compile-only. P5.6P authorizes only internal
personal-edition install/disable/revoke/rollback for sealed first-party
instruction Skills; Browser catalog operations, workflow/script execution,
MCP, Marketplace and enterprise publication remain unavailable.

For the personal edition, the sole live Owner's exact installation transaction
is the behavior-level approval for that Workspace and AgentVersion. It does not
promote a Skill to globally `approved|published`, does not create a transferable
authority token and cannot add capability.

## Review admission

Before a SkillVersion can move beyond `tested`, independently verify:

1. exact source tree and raw-byte digest;
2. dependency lock with no dynamic download or floating version;
3. SBOM, signature and secret scan;
4. strict input/output schemas and bounded budgets;
5. capability, memory, file and network policy diff against the previous
   version and the target AgentVersion;
6. with-Skill/without-Skill paired evaluation, safety negatives and critical
   veto count zero;
7. human review of instructions, typed workflow or Sandbox package;
8. rollback rehearsal to a strictly older reviewed version;
9. clean-checkout source provenance and append-only review evidence.

Do not approve from a manifest field alone. `signature_status=verified`, a
SHA-256-shaped string, model self-report, or a local/disposable smoke is not
proof of publication readiness.

## Immediate revoke

Revoke the exact SkillVersion when its source, signature, dependency lock,
SBOM, evaluation, capability declaration or runtime behavior is ambiguous or
compromised.

- stop new Task admission for the exact version/digest;
- disable affected Workspace installations without deleting history;
- revoke related logical capability grants and pending approvals;
- retain AgentRun, Task, Attempt, Artifact, Audit and pinned version evidence;
- do not mutate running records to pretend they used another SkillVersion;
- move ambiguous provider/tool effects to reconciliation; never auto-replay;
- restore unavailable/rejecting defaults if the runtime boundary is uncertain.

Revocation prevents new use. It does not erase historical evidence or silently
rewrite an already committed Invocation.

## Rollback

Rollback is a new atomic installation transition to a same-definition,
strictly older, reviewed SkillVersion. It must bind:

- tenant and Workspace;
- expected current installation/version/generation;
- target SkillVersion and manifest digest;
- actor, approval where required, request hash and idempotency key;
- append-only Audit and exact rollback reason.

A rollback never edits a SkillVersion in place, lowers a fencing token, revives
a revoked capability, or reuses an active workload identity. Running Tasks stay
pinned to their original version; new Tasks use the newly selected version.

## Recovery boundary

- For contract or source drift, freeze admission and re-verify from a clean
  checkout.
- For persistence defects, prefer forward-fix. Database restore uses a new
  `omnibase_restore_*` database and verified cutover; never destructive in-place
  guessing.
- For script behavior, remove production Sandbox/Runner wiring and revoke
  workload credentials. Never execute the script in Core as a fallback.
- For missing P34.7/P5.4 proof, the correct state is `blocked/not_proven`.
