# P34.5B Broker attack Gate

`run-network-broker-attack-gate.py` is intended for the independent Ubuntu
Runner/Broker VM after `deployment/network-broker/install-network-broker.sh`.
It requires root to create the disposable workload network namespace and to
stage the short-lived root-owned operation permits.

The Gate verifies default rejection of public/member routes, cross-runtime and
host namespace rejection, private socket UID enforcement, a real TCP connect
that succeeds only after entering the requested workload namespace, measured
byte/connection receipts, durable no-replay consumption, service health and
complete cleanup.  It assigns a globally-classified test address only to the
loopback device inside the disposable namespace and performs no external
network access.

This evidence is specific to the hardened Linux target.  Running protocol
tests in Docker Desktop or WSL does not prove production hostile-code
isolation.
