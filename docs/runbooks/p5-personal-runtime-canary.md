# P5 Personal Runtime Canary Runbook

## Preconditions

This runbook is only for the restricted personal single-Owner no-tool canary.
It does not authorize Planner, Multi-Agent, Sandbox, tools, shell, SQL,
arbitrary HTTP, MCP, Skills or enterprise activation.

Before starting, verify that the intended target has:

- one live Tenant and exactly one active Owner membership in the selected
  Workspace;
- that same Owner as an active tenant administrator;
- one sealed tool-free AgentVersion;
- a configured Model Gateway/provider posture;
- migration head 0013 and no migration 0014;
- a separately generated `MEMORY_CONTENT_ENCRYPTION_KEY` for production that is
  not reused as the JWT or Provider-credential key;
- a private operator-controlled location for one canonical config and one new
  empty activation state directory;
- a minimal immutable readiness root preserving the relative paths for
  `deployment/production/personal-single-owner.example.json` and its referenced
  `docs/evidence/p34-7/personal-owner-disposable-gate.json`.

Do not use the repository root `.env`. Do not point these commands at an
ordinary business database; the controller is filesystem-only.

## 1. Prepare the canonical config

Copy `deployment/production/personal-runtime-canary.example.json` to an
operator-controlled absolute path outside the repository. Replace every
placeholder UUID with the exact live canary/Tenant/Workspace/Owner/AgentVersion
UUID. Preserve minified sorted canonical JSON and exactly one trailing newline.

The example's `owner_readiness.sha256` is bound to the checked-in
`deployment/production/personal-single-owner.example.json`. If that source
changes, perform an independent review and update the digest; do not suppress
the drift check.

Validate without a database or network connection:

```powershell
python scripts/production/manage_p5_personal_runtime.py validate `
  --config E:\operator\omnibase\canary.json `
  --repo-root "E:\Agent IDE\OmniBase Worktrees\Active\p5-personal-runtime-activation-r0"
```

## 2. Review the activation plan

```powershell
python scripts/production/manage_p5_personal_runtime.py plan `
  --config E:\operator\omnibase\canary.json `
  --repo-root "E:\Agent IDE\OmniBase Worktrees\Active\p5-personal-runtime-activation-r0"
```

Verify the exact scope, `no_tool`, Runtime=true, Planner=false,
Multi-Agent=false, max lifetime, concurrency=1, `top_k` ceiling and migration
0013. Record the printed `plan_sha256` out of band.

## 3. Create one new run-scoped directory and activate

Use a directory that has never held another activation. Do not reuse a rolled
back, expired or killed directory.

```powershell
$canaryState = 'E:\operator\omnibase\runs\20260810T160000Z-personal-r0'
New-Item -ItemType Directory -Path $canaryState

python scripts/production/manage_p5_personal_runtime.py activate-canary `
  --config E:\operator\omnibase\canary.json `
  --repo-root "E:\Agent IDE\OmniBase Worktrees\Active\p5-personal-runtime-activation-r0" `
  --state-dir $canaryState `
  --confirm-plan-sha256 <exact-plan-sha256>
```

Activation writes one `000001-activate.json` receipt. It does not start the
backend or change environment variables.

## 4. Apply the explicit backend overlay

Set only the three host mount locators used by the overlay. The config file,
state directory and minimal readiness root are read-only inside the backend.

```powershell
$env:PERSONAL_RUNTIME_CANARY_HOST_CONFIG='E:\operator\omnibase\canary.json'
$env:PERSONAL_RUNTIME_CANARY_HOST_STATE=$canaryState
$env:PERSONAL_RUNTIME_READINESS_HOST_ROOT='E:\operator\omnibase\readiness-root'

docker compose --env-file .env.example `
  -f docker-compose.yml `
  -f deployment/production/personal-runtime-canary.compose.example.yml `
  config --quiet
```

Review the rendered configuration before any `up`. The overlay sets only:

```text
ENV=production
AGENT_RUNTIME_ENABLED=true
AGENT_PLANNER_ENABLED=false
MULTI_AGENT_ENABLED=false
PERSONAL_RUNTIME_PROFILE=personal_single_owner
PERSONAL_RUNTIME_READINESS_ROOT=/run/omnibase-personal/readiness-root
```

Starting or deploying a real target is a separate external state change and
requires the target, provider configuration and operator authorization to be
explicitly identified.

## 5. Verify status

Filesystem status:

```powershell
python scripts/production/manage_p5_personal_runtime.py status `
  --config E:\operator\omnibase\canary.json `
  --repo-root "E:\Agent IDE\OmniBase Worktrees\Active\p5-personal-runtime-activation-r0" `
  --state-dir $canaryState
```

Only `state=active` plus `binding_valid=true` exits 0. Inactive, expired,
rolled-back and killed are valid non-active states and exit 2. Invalid/tampered
state exits 1.

The authenticated Workspace status endpoint must additionally report:

```text
runtime_profile=personal_single_owner
personal_runtime_state=active
personal_runtime_active=true
production_activation_allowed=true
tools_enabled=false
multi_agent_enabled=false
supported_invocation_modes=[no_tool]
```

These fields are scoped to that exact request; they contain no credential,
Approval, Capability, lease, fencing, locator or workload identity material.

For one test invocation with a matching committed Memory, verify that SSE
arrives incrementally and its `meta` exposes at most `context_capsule_id`,
`context_capsule_digest` and `context_capsule_item_count`. Memory plaintext,
Memory/version/review identifiers and encryption material must not appear in
SSE or logs. Stop the invocation once and verify the durable Task converges to
`cancelled`; exact terminal replay must not create a second Capsule.

## 6. Roll back normally

```powershell
python scripts/production/manage_p5_personal_runtime.py rollback `
  --config E:\operator\omnibase\canary.json `
  --repo-root "E:\Agent IDE\OmniBase Worktrees\Active\p5-personal-runtime-activation-r0" `
  --state-dir $canaryState `
  --reason-code operator_requested
```

Rollback appends `000002-rollback.json`. It cannot be undone in that directory.

## 7. Kill independently

Use kill for compromise, uncertain state, config drift or a damaged event
ledger. It deliberately does not require a readable config or valid ledger.

```powershell
python scripts/production/manage_p5_personal_runtime.py kill `
  --state-dir $canaryState `
  --canary-id <exact-canary-uuid> `
  --reason-code emergency_operator_kill
```

Any `KILL_SWITCH.json` presence wins. Preserve it. Also remove the overlay or
restore `AGENT_RUNTIME_ENABLED=false` at the deployment layer.

The marker is checked before fresh reservation, before transaction-A commit,
before provider dispatch and at stream checkpoints. It is not a cross-system
instant process kill: if a third-party provider call is already blocked, the
service observes the marker at the next checkpoint. Removing the overlay,
restoring Runtime=false and restarting the target process are the operator
steps that stop further admission while the durable invocation converges.

## 8. Preserve evidence and recover

Preserve the canonical config, plan output, every event byte, status output,
deployment configuration review and kill marker. Never edit an old event or
delete the marker to reactivate. Diagnose and create a new config review and a
new run-scoped directory.
