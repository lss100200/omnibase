# P34.7 target-environment and production-evidence plan

Date: 2026-08-09

Status:

```text
INVENTORY_NOT_ASSESSED
PRODUCTION_EVIDENCE_NOT_COLLECTED
```

This document is a planning and inventory surface. It does not authorize
access to a machine, network, provider, tenant, database, object store, key,
certificate or production service. Every item starts fail-closed.

## 1. Environment inventory

Complete this table with stable non-secret identifiers and evidence references.
Do not paste credentials, physical database locators, private object keys,
private IP topology, private keys or root `.env` values.

| Resource | Required property | Owner/authority | Current state | Evidence reference |
| --- | --- | --- | --- | --- |
| Core deployment | Browser/API-only ingress; no hostile execution; independently identified | `UNASSIGNED` | `NOT_ASSESSED` | `NONE` |
| Linux Runner | Independent target Linux; current source/artifact; cgroup v2, namespaces, seccomp/LSM; no Core data credentials | `UNASSIGNED` | `NOT_ASSESSED` | `NONE` |
| Network Broker | Independent default-deny private network namespace and durable budget/replay ledger | `UNASSIGNED` | `NOT_ASSESSED` | `NONE` |
| Capability Gateway | Independent mTLS service with server-owned credential registry and live lease/fencing validation | `UNASSIGNED` | `NOT_ASSESSED` | `NONE` |
| Overlay member A | Real independent Linux member and production Node Daemon | `UNASSIGNED` | `NOT_ASSESSED` | `NONE` |
| Overlay member B | Real independent Linux member and production Node Daemon; not the same host/security domain as A | `UNASSIGNED` | `NOT_ASSESSED` | `NONE` |
| Independent DERP | Real relay under separately controlled production identity | `UNASSIGNED` | `NOT_ASSESSED` | `NONE` |
| Provider/object store | Non-disposable Artifact/Derived/Promotion/Snapshot/Restore target with committed-marker semantics | `UNASSIGNED` | `NOT_ASSESSED` | `NONE` |
| Non-disposable tenant/RAG | Synthetic or approved test tenant; explicit data-owner authorization; no normal business data | `UNASSIGNED` | `NOT_ASSESSED` | `NONE` |
| PKI/certificate boundary | Issuance, SAN/issuer policy, short validity, revocation and certificate inventory | `UNASSIGNED` | `NOT_ASSESSED` | `NONE` |
| Seven signing roles | Approved public keys, custody attestations and separated owners for core/runner/broker/gateway/overlay/recovery_sla/sealer | `UNASSIGNED` | `NOT_ASSESSED` | `NONE` |
| Time source | Reliable UTC source and recorded clock posture for evidence/certificate windows | `UNASSIGNED` | `NOT_ASSESSED` | `NONE` |
| Observability | Bounded logs/metrics/traces without secrets or physical locators | `UNASSIGNED` | `NOT_ASSESSED` | `NONE` |
| Capacity/SLA harness | Concurrent load, fault injection, p95/success-rate policy and signed observations | `UNASSIGNED` | `NOT_ASSESSED` | `NONE` |
| Recovery/cleanup authority | Can revoke, isolate, reconcile and verify zero-residual cleanup | `UNASSIGNED` | `NOT_ASSESSED` | `NONE` |

Allowed inventory states are:

```text
NOT_ASSESSED
MISSING
PLANNED
AVAILABLE_NOT_PROVEN
EVIDENCE_COLLECTED_NOT_REVIEWED
PROVEN
REJECTED
```

`AVAILABLE_NOT_PROVEN` is not PASS. A detected executable, reachable port,
running container or existing VM is only an availability fact.

## 2. Eleven production blockers and required evidence

| # | Blocker | Minimum direct evidence | Current state |
| --- | --- | --- | --- |
| 1 | Current-source Linux Runner 12/12 | Fresh source/artifact manifest; target-host attestation; all 12 attack cases; bounded cleanup; signed Runner evidence | `NOT_ASSESSED` |
| 2 | Core → Runner mTLS | Real independent processes; exact peer identities/certificate pins; request/result binding; stale/revoked credential negatives | `NOT_ASSESSED` |
| 3 | Runner → Broker private identity | Private transport identity, namespace ownership, budget/replay checks and rejection of host/direct egress | `NOT_ASSESSED` |
| 4 | Runner → Gateway non-disposable mTLS | Live Run/Node/Lease/fencing proof, short credential, logical-resource read and revoke/stale negatives | `NOT_ASSESSED` |
| 5 | Broker → Gateway non-disposable mTLS | Independently identified Broker, bounded gateway action and cross-role/credential rejection | `NOT_ASSESSED` |
| 6 | Real provider lifecycle | Artifact/Derived/Promotion/Snapshot/Restore with actual byte digests, committed visibility, reconciliation and restore-new identity | `NOT_ASSESSED` |
| 7 | Data-owner non-disposable tenant/RAG | Explicit owner authorization, exact tenant/workspace/resource/version binding and no physical-locator leakage | `NOT_ASSESSED` |
| 8 | Two real members and DERP | Two independent Linux members, two production Node Daemons, independent DERP and signed topology inventory | `NOT_ASSESSED` |
| 9 | Compromise/rejoin matrix | Forced DERP with direct path off; revoke; stolen credential rejection; stale lease/fencing rejection; new-identity rejoin; cleanup | `NOT_ASSESSED` |
| 10 | Dual independent signatures | Member A and B sign the same canonical payload with approved distinct keys; signer/topology/digest checks pass | `NOT_ASSESSED` |
| 11 | Capacity, fault and SLA | Minimum sample count, concurrency, success rate, p95, allowed outcomes, fault injection and cleanup satisfy policy | `NOT_ASSESSED` |

No blocker may be closed by prose. Each must point to fresh canonical evidence
whose bytes, producer, signer, source, artifact, command receipt, time window
and results are independently verifiable.

## 3. Evidence that is useful but not production proof

The following remain valid engineering evidence, but cannot close the table
above by themselves:

- Docker Desktop or normal Docker Compose;
- WSL or a single-host Linux container;
- mock, fake, test double or in-memory provider;
- disposable PostgreSQL, Headscale, Gateway or object-store Gate;
- historical Runner 11/11 evidence from a previous launcher profile;
- port reachability, executable presence or process liveness;
- self-signed evidence using a trust root shipped inside the bundle;
- a policy candidate whose digest is not independently approved;
- screenshots, manually edited reports or sidecar hashes without valid
  detached signatures.

They can be used for rehearsal and fault discovery, never as a replacement for
the target-environment run.

## 4. Recommended execution order

### Stage 0 — inventory and authority assignment

1. Assign non-secret stable identifiers to every row in section 1.
2. Mark unavailable resources `MISSING`; do not guess or substitute.
3. Record data-owner authorization requirements without accessing data.
4. Confirm independent signers, reviewers and incident authority.
5. Confirm no target is the normal business database or an uncontrolled
   workstation environment.

### Stage 1 — source and artifact freeze

1. Start from a public clean checkout of the exact release candidate.
2. Freeze Git object format, commit/tree, tracked source manifest and build
   inputs.
3. Build the six approved executables in their controlled environment.
4. Record actual path/size/SHA-256 and bind them into the approved artifact
   manifest.
5. Any controlled source or artifact byte change invalidates subsequent
   evidence and requires a new run.

### Stage 2 — component-local target Gates

1. Run current-source Linux Runner 12/12.
2. Run two complete Broker 26/26 rounds under the production profile.
3. Verify Gateway certificate, replay, lease/fencing and credential boundaries.
4. Verify each component cleanup inventory is complete and signed.

### Stage 3 — four-component roundtrips

Collect Core→Runner, Runner→Broker, Runner→Gateway and Broker→Gateway evidence.
Every channel must use the exact approved role identity. Browser JWT/cookies,
static bearer secrets or caller-submitted peer identities are forbidden.

### Stage 4 — provider and tenant/RAG

1. Obtain explicit data-owner authorization for an isolated non-disposable
   test tenant/RAG scope.
2. Run Artifact/Derived/Promotion/Snapshot/Restore operations with exact
   logical identifiers and committed-marker visibility.
3. Exercise `pending|unknown` reconciliation without automatic replay.
4. Restore into new Workspace/resource identities; never revive old Run,
   Lease, credential, network identity or provider handle.

### Stage 5 — real Overlay, compromise and DERP

1. Admit two independent Linux members and production Node Daemons.
2. Force DERP and prove direct path is disabled.
3. Revoke a node and reject its stolen credential.
4. Reject stale Lease/fencing and old identity.
5. Rejoin using a new identity and higher fencing generation.
6. Verify service/peer/network cleanup and zero unauthorized residual access.

### Stage 6 — capacity, SLA and dual signing

1. Run the checked-in concurrency and fault scenarios.
2. Gather the required sample count and p95/success-rate measurements.
3. Have member A and B independently sign the same canonical evidence payload.
4. Have the sealer sign the final evidence seal and cleanup inventory.

### Stage 7 — independent final verification

Run the joint and composition validators from a new clean checkout. The only
acceptable production-ready result is:

```text
state = ready
activation_allowed = true
blockers = []
vetoes = []
```

Anything else preserves `blocked/not_proven` or `invalid/veto`. The validator
does not start a service or enable a Feature Gate.

## 5. Evidence handling and cleanup

- Evidence must live in a new immutable run-scoped directory outside the trust
  policy and outside the repository unless the checked-in contract explicitly
  requires a tracked artifact.
- Never overwrite or delete an old run to make a new run appear complete;
  mark it superseded/incomplete and preserve it for forensics.
- Use canonical JSON and detached signatures; do not trust bundle-supplied
  public keys.
- Reject symlink/reparse/junction/path traversal and non-regular files.
- Keep the root `.env`, business database, real user data and private physical
  locators outside diagnostics and evidence.
- Revoke temporary credentials and confirm cleanup after every run, including
  failure and timeout paths.
- An expired evidence bundle is never re-PASSed; collect a new run.

## 6. Readiness report template

Every controller report must include:

```text
source commit/tree/object format:
target environment identifiers:
approved policy raw SHA-256:
approved artifact manifest SHA-256:
run id and validity window:
11 blocker states:
signer identities and public-key digests:
cleanup inventory:
root .env accessed: false
business database accessed/migrated: false/false
production services activated: false
Phase 5 Feature Gates: false/false/false
migration head: 0012
migration 0013: absent
final validator state:
blockers:
vetoes:
```

## 7. Preparation completion state

This document is complete when the inventory can be filled without secrets and
each blocker has an identified owner, target and evidence command. It does not
require or permit executing the commands yet.

Expected current output:

```text
INVENTORY_NOT_ASSESSED
PRODUCTION_EVIDENCE_NOT_COLLECTED
P34_7_BLOCKED_NOT_PROVEN
PRODUCTION_ACTIVATION_DISABLED
```
