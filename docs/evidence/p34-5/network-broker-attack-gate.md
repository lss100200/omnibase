# P34.5B independent Linux Network Broker attack Gate

The scored run and the restart confirmation run both passed all 26 checks on
the existing `OmniBase-P34.5-Runner` Hyper-V Ubuntu 24.04 VM.  The confirmation
artifact SHA-256 is
`573e69892812823018cab2a201082b21777fad1dbc3479b5cb74fcb17fa2c3de`.

The sealed deployment inputs used by both passing rounds were:

- daemon `deployment/network-broker/omnibase-network-broker.py`:
  `162498b0f0e08e761ec6c8b35fe1469b9d70f12b9676359d0d6f9ecdb968a055`;
- systemd service `deployment/network-broker/omnibase-network-broker.service`:
  `aba7b1343bafc470715afa1e421f9f4afa48abc3abe6e97cd8c64066a90149e4`;
- Gate `scripts/network-broker/run-network-broker-attack-gate.py`:
  `565de902e9bfbb6d8caa6fc21cbcfe1d923a3b249c7ad8ce91e3590ec7890ccc`.

## Proven boundary

- The daemon runs as the dedicated non-root `omnibase-network-broker` account
  (`uid=999`, `gid=988`) under systemd `PrivateNetwork=yes` and
  `NoNewPrivileges=yes`.
- The supervisor has only `CAP_SYS_ADMIN` for `setns` and `CAP_SYS_PTRACE` for
  live PID/start-time namespace continuity checks.  Its permitted address
  families are `AF_UNIX`, `AF_INET`, and `AF_INET6`.
- The private AF_UNIX protocol binds an exact operation/plan, a fresh random
  challenge, live PID/start-time and positive `device:inode` namespace
  identity, logical service destination, and a root-owned maximum-five-minute
  permit.
- The daemon reopens `/proc/<PID>/ns/net`, checks PID start-time before and
  after the open, rejects the root-owned `/run/omnibase-host-ns/net` identity,
  consumes the operation with `O_EXCL`, forks, enters the verified namespace,
  and only then opens one TCP socket.
- The host namespace snapshot is opened with `O_NOFOLLOW` and one descriptor
  is retained across `fstat/read/fstat`; owner, link count, mode, size,
  device/inode and modification continuity are checked before use.
- Consumed-marker writes handle short writes, `fsync` the complete file, and
  then `fsync` the parent directory before `setns` or any connection side
  effect.
- The production config file is accepted only from a root-owned parent that is
  not group/world writable, and the config file itself must be a single-link,
  root-owned, non-writable regular file.
- Although `CAP_SYS_ADMIN` remains required for `setns`, the systemd syscall
  filter explicitly blocks mount, unmount, pivot-root and the modern mount API.
- The response carries measured connection/inbound/outbound byte counts and an
  HMAC-SHA256 challenge response from a pinned 32-byte local key.  The key was
  not printed, hashed into evidence, returned in a receipt, or copied into the
  repository.  Only its owner/group/mode/size metadata was checked.

## Attack results

The Gate passed:

- a real TCP connection to a globally-classified address assigned only to the
  disposable workload namespace loopback;
- direct public and host-network connection failure from the workload
  namespace because it had no egress/default route;
- public-route and member-Overlay route rejection;
- independent loopback, metadata, RFC1918 LAN, IPv6 ULA, multicast and reserved
  destination rejection;
- connection-budget and inbound-byte-budget exceed rejection;
- correct daemon challenge verification plus forged response and wrong-key
  rejection;
- stale PID, incorrect PID start-time, incorrect namespace identity, host
  namespace and cross-runtime binding rejection;
- untrusted socket UID rejection, daemon peer PID/start-time continuity and
  socket inode continuity;
- durable consumed-operation no-replay behavior and post-run service health.

## Cleanup and scope

The disposable namespace process stopped, Gate permit and consumed-operation
files were removed, and no disposable `unshare` process remained.  The Broker
service was active after cleanup, with zero Gate files under the permit and
consumed directories.

The Gate did not read the repository root `.env`, access PostgreSQL, Redis,
MinIO, a business database, an external Provider, or a member Overlay.  The
public/host checks were failed connection attempts from a namespace with no
route; no external connection succeeded.

This proves the Broker daemon's Linux namespace-connect, default-deny,
authentication, budget, replay and cleanup boundary on the hardened target VM.
It does not by itself prove two real Overlay member nodes, DERP recovery,
Core-to-Broker production mTLS activation, non-disposable production
tenant/RAG, or that ordinary Docker Desktop/WSL can safely execute arbitrary
hostile code.  The separate P34.5D split-process mTLS Gate proves only its
guarded disposable tenant/RAG read boundary.
