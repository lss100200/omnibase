# P34.5A4 Linux Runner operator gate

This directory contains the fail-closed probe and fixed attack-matrix entrypoint
for an independent Linux Runner. Passing unit tests does not authorize hostile
workload execution. A real Runner stays unavailable until the operator-installed
launcher, host controls and receipt protocol all pass the checks below.

## Deployment inputs

Copy `linux-runtime-config.example.json` outside the repository and replace every
zero UUID/digest with the actual value. The probe config is a closed JSON object;
unknown or missing keys are rejected.

The deployment must provide:

- a non-root Runner identity;
- a private Runner root owned by that identity and inaccessible to group/other;
- a delegated cgroup v2 directory with `cpu`, `memory` and `pids` controllers,
  writable `cgroup.kill`, and one operation cgroup named by the operation UUID;
- a root-owned, non-group/world-writable isolation launcher whose SHA-256 is
  pinned in the config;
- root-owned, non-group/world-writable seccomp and LSM profile files whose
  SHA-256 values match the isolation profile;
- `NoNewPrivs=1`, seccomp filter mode, and an enforcing AppArmor/SELinux label
  matching `lsm_profile_name` and not containing `unconfined`;
- isolated user, PID, mount and network namespaces for the Runner; and
- a trusted, root-owned, non-group/world-writable namespace reference directory.

For a Runner launched directly as a VM service, the namespace reference may be
`/proc/1/ns`. For a Runner inside a nested container, it must be a read-only bind
mount of the VM host's `/proc/1/ns` at a fixed path such as
`/run/omnibase-host-ns`; the container's own `/proc/1/ns` is not valid host
evidence. The probe compares the Runner namespace identities with the trusted
host references and binds the identities into the attestation digest.

## Probe and attack gate

Run from the repository root with an absolute config path:

```text
python scripts/sandbox/probe_linux_runtime.py --config /etc/omnibase-runner/probe.json
python scripts/sandbox/run_a4_attack_matrix.py --probe-config /etc/omnibase-runner/probe.json
```

Both commands must exit zero. A non-zero probe is a security result, not an
instruction to weaken the checks.

## Launcher protocol

The RuntimeDriver invokes only the pinned launcher with fixed argv:

```text
/usr/libexec/omnibase/omnibase-isolation-launcher execute
/usr/libexec/omnibase/omnibase-isolation-launcher terminate
```

Canonical JSON is delivered on stdin with an empty inherited environment except
for fixed locale values. The request includes canonical UUID values for
`operation_id` and `cgroup_name`. `cgroup_name` is the stable
`runtime_instance_id`, so a later stop/destroy operation with a different
operation ID still targets the cgroup created for the running workload. The
launcher must create and use `<cgroup_root>/<runtime_instance_id>` and must not
move the workload to another cgroup.

The launcher response is one JSON object with exactly these fields:

```text
binding_digest
cgroup_empty
evidence_digest
exit_code
namespace_evidence_digest
namespaces_isolated
operation_id
reason_code
runner_id
runtime_instance_id
stderr_digest
stdout_digest
truncated
```

`cgroup_empty` and `namespaces_isolated` must be the JSON boolean `true`.
Digests are lowercase SHA-256 values. The operation, runtime instance, Runner and
binding digest must match the request. `runner_execution_succeeded` is valid only
with exit code zero. The namespace evidence digest must cover the per-operation
user/PID/mount/network namespace identities created by the launcher.

On timeout, output overflow or pipe failure, the RuntimeDriver writes `1` to the
operation cgroup's `cgroup.kill`, waits for `cgroup.events` to report
`populated 0`, and then terminates the launcher process group. If cgroup-empty
proof cannot be obtained, the result is unavailable/unknown and must never be
recorded as success or automatically replayed.

## Workspace Network Broker deployment seam

P34.5B ships an independent Broker daemon deployment seam, but the main
application intentionally does not activate it automatically. Production-like
single-host wiring must explicitly compose all of the following:

- `SqliteNetworkBudgetLedger` with an absolute `*.sqlite3` path inside a
  pre-created `0700` directory owned by the Broker service identity;
- `FilesystemNetworkNamespaceAttestor` with a private daemon-owned evidence
  directory, live trusted PID/starttime and either the exact `/proc/1/ns/net`
  handle or root/private `/run/omnibase-host-ns/net` strict `dev:ino` snapshot;
- `UnixSocketBrokerTransport` with an absolute socket in a private directory
  owned by a dedicated daemon UID/GID different from the caller; Linux
  `SO_PEERCRED` PID/UID/GID, PID starttime, socket inode continuity and the
  pinned-key nonce challenge must all pass;
- the live authorizer, logical resolver and policy engine already required by
  `ControlledWorkspaceNetworkBroker`.

The namespace daemon writes one bounded file named
`<runtime_instance_id>.network-namespace.json`. The canonical evidence digest
covers the namespace/Runner/Node/runtime/workload identity, trusted PID and
starttime, live network namespace `dev:ino`, generation, all Run/Node/Network
fencing values, policy, direct-overlay denial and validity window. The attestor
opens it with `O_NOFOLLOW`, compares `fstat` before/after read, reopens
`/proc/<pid>/ns/net` and rejects any PID reuse, inode drift or namespace identity
equal to the host network namespace. The Broker repeats attestation immediately
before durable reservation and transport.

The budget database is not a business database. Its aggregate reservation uses
`BEGIN IMMEDIATE`; `pending` and `unknown` rows remain charged and cannot be
deleted or replayed, while an exact `committed` replay returns the stored
receipt without another transport call. Receipt binding is verified before the
one-way commit. A transport, receipt or commit exception must therefore be
reconciled from durable evidence, never repaired by deleting the row.

The local transport sends one credential-free bounded JSON request over
`AF_UNIX`; the daemon authentication key stays outside that payload and signs a
fresh nonce/operation/plan challenge. The client compares socket `dev:ino` and
peer PID/starttime before and after the exchange. It does not itself create an
Internet socket, join a member Overlay, or grant arbitrary egress.
Public-internet/member-Overlay route kinds and
loopback, metadata, link-local, LAN/ULA, multicast/reserved destinations remain
rejected before transport. Keep `UnavailableNetworkBudgetLedger`,
`RejectingNetworkNamespaceAttestor` and `UnavailableBrokerTransport` installed
whenever any deployment proof is missing.

## Explicit non-goals

The A4 local transport uses injected HMAC material and an in-memory replay store
for local/development protocol testing. Remote production deployment still
requires mTLS or an equivalent independent Runner identity, a durable replay
store, audited launcher implementation, and deployment-specific recovery and
rotation procedures. Neither the probe nor the attack matrix accesses tenant
data, business databases, Redis, MinIO, RAG, Docker sockets or the root `.env`.
