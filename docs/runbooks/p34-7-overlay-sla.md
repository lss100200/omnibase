# P34.7 production Overlay, compromise, capacity and SLA Gate

## Purpose and status boundary

This runbook admits production evidence for the final P34.7 Overlay and
network-runtime boundary. It does not turn a development workstation into a
production Runner and it does not contact, mutate, revoke or provision a node by
itself.

The following evidence remains useful but cannot independently satisfy this Gate:

- the P34.5 A4 historical target Linux isolation artifact;
- either P34.5 Network Broker `26/26` artifact;
- the disposable Headscale control-plane and mTLS Node-Daemon test double;
- Docker Desktop, WSL, a single-host namespace, fake providers or in-memory ledgers.

Until all production inputs in this runbook are independently collected and
admitted, the correct status is `blocked/not_proven`. A failed or missing external
probe is never converted to PASS by a local substitute.

## Safety boundaries

- Never read or pass the repository root `.env`. The scripts in this runbook only
  accept explicit JSON/JSONL inputs and reject files named `.env`.
- Do not place credentials, API keys, certificate private keys, cookies or bearer
  tokens in configuration, samples, transcripts or evidence. Configuration stores
  only a server-owned logical `credential_reference`.
- Do not expose a Sandbox as an Overlay peer or give a Sandbox/Runner a direct
  PostgreSQL, Redis, MinIO, provider or host route.
- Do not run database migrations or access a business database. This Gate has no
  database dependency.
- Collect provider mutation and revocation evidence through the production Core,
  Runner, Broker and Node-Daemon control path. Do not bypass the system with a
  provider CLI and then claim the application performed the action.
- `pending|unknown` mutations remain non-replayable and require reconciliation.
- Run current-source A4 only on the independently attested target Linux Runner.
  Ordinary Docker/WSL output is development evidence and must remain not proven.

## Required infrastructure

The scored topology must contain:

1. Two independently hosted real Linux Overlay members. Their host, machine and
   Node-Daemon identity digests must all be distinct.
2. A production Node Daemon on each member. A test double is a hard rejection.
3. At least one DERP host independent from the two member hosts.
4. Production Headscale control plane reachable only over HTTPS.
5. Server-owned credential references. Raw provider or node credentials are never
   submitted to the Gate.
6. An independent Ed25519 evidence-attestation public key for each member. The
   private keys remain on their respective members and are never copied into the
   checkout, topology or evidence directory.
7. The exact source commit being admitted on the Runner, Broker, Gateway and
   evidence verifier.

Copy `deployment/overlay/production/topology.example.json` outside the checkout.
Replace all identity and endpoint placeholders and set each member's `placeholder`
to `false`. Do not edit the example into a credential file.

## Stage 0: static ValidateOnly

Static validation performs no network or provider action:

```powershell
python scripts/overlay/p34_7_production_gate.py `
  --repo-root . `
  --config C:\secure-operator-config\omnibase-p347-topology.json `
  --report-out C:\tmp\omnibase-p347\validate-only.json `
  --validate-only
```

Success means only that the configuration contract is syntactically valid. The
report deliberately remains:

```text
status = blocked/not_proven
production_overlay_gate_passed = false
```

The checked-in example also contains `placeholder: true`; it can never be used for
a scored PASS.

## Stage 1: current-source Gate inputs

Every scored artifact must bind the same clean source commit and the same
`source_scope_sha256`. The source scope fingerprints the Runner, Sandbox, Broker,
Overlay adapter, production Gate scripts/configuration, `.gitattributes`, backend
lock and packaging inputs. A later evidence-only documentation commit may change
HEAD, but it cannot change any sealed production source byte without invalidating
the Gate.

### Target Linux Runner

Run the current source's complete isolation and attack matrix on the independent
target Linux Runner. The submitted result must contain exactly:

```text
status = passed
passed = 12
total = 12
source_git_commit = <current 40-character commit>
```

An older `11/11`, a result from a different launcher hash, or a result from
Docker/WSL is not accepted.

### Network Broker

Run the unchanged namespace/default-deny/identity/budget/replay matrix twice,
including a daemon restart between rounds. Each submitted result must contain
exactly `26/26` and the same source commit as the Runner Gate. No test may be
removed, renamed out of the scored set or replaced by an in-memory transport.

## Stage 2: real member data plane and DERP

Using the production logical service path:

1. Connect member A to a logical service on member B.
2. Prove the Sandbox never became an Overlay member.
3. Prove the request exposed no physical endpoint or provider handle to the
   workload.
4. Prove no direct PostgreSQL, Redis or MinIO route was available.
5. Disable the direct member path and repeat through DERP.
6. Record the DERP path selected by the Overlay implementation and prove the
   direct path remained disabled for the forced-relay observation.
7. Restore the direct path and verify reconciliation does not replay an unknown
   provider mutation.

The disposable Headscale Gate registered zero real member devices and therefore
cannot be reused for this stage.

## Stage 3: node-compromise matrix

The following outcomes are mandatory:

- node revocation rejects the revoked identity;
- a copied/stolen old node credential is rejected after revocation;
- the old Network Lease is rejected;
- stale Node and Run fencing tokens are rejected;
- an ambiguous operation is not replayed automatically;
- rejoining creates a new node/runtime/workload identity and higher fencing;
- revoke propagation is measured from the control-plane commit to the last
  successful rejection observation;
- cleanup leaves no Gate-owned process, network, container or volume.

Any acceptance of a revoked/stolen identity, stale Lease/fencing, or replay of an
ambiguous operation is a Critical Veto rather than an ordinary SLA miss.

## Stage 4: capacity, SLA and failure injection

Create one JSON object per line using schema
`omnibase.p34-7.overlay-sla-sample.v1`. Each observation has the following shape:

```json
{
  "schema": "omnibase.p34-7.overlay-sla-sample.v1",
  "attempt_id": "immutable-unique-attempt-id",
  "scenario": "forced_derp_relay",
  "environment": "production",
  "duration_ms": 412,
  "success": true,
  "expected_effect_verified": true,
  "outcome": "committed",
  "transport_path": "derp",
  "source_node_id": "member-a",
  "target_node_id": "member-b",
  "concurrency": 4,
  "direct_infrastructure_route_observed": false,
  "secret_exposure_observed": false
}
```

The mandatory scenario set is:

- `member_direct_logical_service`
- `forced_derp_relay`
- `node_daemon_restart`
- `node_revoke_propagation`
- `network_partition_fail_closed`
- `broker_restart_pending_no_replay`
- `runner_forced_kill_cleanup`
- `gateway_timeout_unknown_no_replay`
- `node_credential_theft_after_revoke`

Generate the threshold report:

```powershell
python scripts/overlay/p34_7_sla_report.py `
  --policy deployment/overlay/production/sla-policy.example.json `
  --samples C:\tmp\omnibase-p347\production-samples.jsonl `
  --report-out C:\tmp\omnibase-p347\sla-report.json
```

The checked-in policy freezes the first production baseline. Changing a threshold
requires review and a schema/version decision; it must not be lowered merely to
make an existing run pass.

Duplicate attempt identities, a success claim without verified effect, secret
exposure or a direct infrastructure route are Critical Vetoes. Missing samples,
insufficient concurrency, low success ratio or excessive p95 latency remain
`blocked/not_proven`.

## Stage 5: evidence admission

The production evidence bundle uses schema
`omnibase.p34-7.overlay-production-evidence.v1` and includes:

- `environment: production` and `disposable: false`;
- exact current source commit and SHA-256 of the canonical topology JSON;
- `source_git_dirty: false` and the current production `source_scope_sha256`;
- identity-only observations for both configured members;
- one detached Ed25519 signature from each member over the exact canonical
  evidence object with the top-level `signatures` field removed;
- current-source A4 `12/12`;
- two Broker `26/26` rounds;
- member direct-path and forced-DERP observations;
- the complete node-compromise outcome;
- the exact mandatory fault-scenario set;
- zero Gate-owned cleanup residue and an empty secret scan.

Admit the bundle only after the SLA report passes:

```powershell
python scripts/overlay/p34_7_production_gate.py `
  --repo-root . `
  --config C:\secure-operator-config\omnibase-p347-topology.json `
  --evidence C:\tmp\omnibase-p347\production-evidence.json `
  --sla-report C:\tmp\omnibase-p347\sla-report.json `
  --report-out C:\tmp\omnibase-p347\production-overlay-report.json
```

The final report can be `passed`, `blocked/not_proven`, or `veto`. Only `passed`
sets `production_overlay_gate_passed=true`.

The scored CLI verifies both detached signatures with `openssl pkeyutl`. Each
signature entry contains only `node_id`, `algorithm: ed25519`, the detached
signature file path and SHA-256, and `signed_payload_sha256`. Missing signatures,
duplicate signers, public-key digest drift, payload drift or signature failure is
rejected before scoring. Public keys are not credentials; private keys must never
enter the operator workstation evidence bundle.

## Critical Veto catalogue

- Sandbox is an Overlay member.
- Direct PostgreSQL, Redis, MinIO, provider or host route is present.
- A physical Overlay endpoint is exposed instead of a logical service.
- A revoked node or stolen credential is accepted.
- A stale Lease or stale fencing holder succeeds.
- An ambiguous operation is replayed automatically.
- A revoked identity is reused when a node rejoins.
- A success is claimed without a verified effect.
- Secret or credential material enters configuration, evidence or observations.
- Gate-owned processes, networks, containers or volumes remain after cleanup.

## Recovery and reporting

If a stage fails:

1. Stop new production activation.
2. Revoke the affected node, workload and short-lived capability identities.
3. Preserve immutable raw observations and their hashes.
4. Mark provider outcomes that cannot be proven as `unknown`; do not replay.
5. Restore service with a new node/runtime/workload identity and higher fencing.
6. Re-run the complete affected stage and both Broker rounds. Do not patch a
   historical report into PASS.

Always report which stages ran, the current source commit, artifact hashes,
remaining external blockers and all Vetoes. Never describe ValidateOnly,
disposable, Docker, WSL, fake-provider or single-host results as production PASS.
