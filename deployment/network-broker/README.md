# OmniBase P34.5B independent Linux Network Broker

This directory contains the deployable, fail-closed Broker daemon that backs
`UnixSocketBrokerTransport`.  It is separate from Browser/Core ASGI, runs as a
dedicated non-root UID, and starts in a systemd `PrivateNetwork` namespace.
The daemon never joins a member Overlay and never receives database, Redis,
MinIO, JWT, signing-key or provider credentials.

The transport request is not authorization.  Before one TCP connect, a trusted
host controller must provide both:

1. a live workload PID/start-time/network-namespace identity already verified
   by the trusted Runner attestor and bound into the request; and
2. `/run/omnibase-network-broker-permits/<operation UUID>.permit.json`, a
   root-owned, maximum-five-minute permit binding every plan digest, runtime,
   namespace identity, logical service, destination and byte limit.

The daemon reopens `/proc/<PID>/ns/net` and verifies the PID start time both
before and after opening it, then verifies client `SO_PEERCRED`, the exact
permit, live namespace identity, and the root-owned `/run/omnibase-host-ns/net` PID-1
network namespace snapshot.  Its response is authenticated with a pinned local
32-byte HMAC key; the key is root-owned, group-readable only by the daemon, and
is never placed in a request, receipt, log or Gate artifact.  The daemon writes an `O_EXCL`
consumption record before `setns` or `connect`; after a crash that operation is
outcome-unknown and cannot be automatically replayed.  The worker enters only
the pinned network namespace, establishes one TCP connection, performs no
caller-supplied write, reads at most the trusted inbound byte allowance and
returns measured connection/byte counts in the standard Broker receipt.

Install on the already hardened Ubuntu Runner/Broker VM:

```text
sudo ./install-network-broker.sh
sudo systemctl status omnibase-network-broker.service
sudo python3 ../../scripts/network-broker/run-network-broker-attack-gate.py
```

The existing `UnixSocketBrokerTransport` requires a daemon UID/GID distinct
from its caller, socket inode continuity, peer PID/start-time continuity and a
pinned HMAC challenge.  The private `0600` socket therefore admits only a
root-owned trusted local transport process on this profile; ordinary Browser,
Sandbox and member processes cannot connect.  Core-to-Broker production mTLS
and the full two-node Overlay/Gateway deployment remain a later joint Gate.

This profile is intended for the independent Linux VM.  A Docker/WSL smoke may
validate protocol and cleanup mechanics, but it is not evidence that an
ordinary container safely isolates hostile code or constitutes a production
network boundary.
