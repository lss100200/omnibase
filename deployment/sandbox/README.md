# OmniBase P34.5 hardened Linux Runner

This directory is the auditable deployment profile used by the P34.5 Hyper-V
Linux attack gate.  It is intentionally independent from Docker Desktop, WSL
and the browser/API processes.

The profile installs a root-owned launcher and policy, then runs the launcher
as the non-root `omnibase-runner` account under systemd.  The service has its
own user, mount, network and PID namespaces.  Each execution receives another
user/PID/mount/network namespace set, a stable cgroup named by
`runtime_instance_id`, a read-only ephemeral root, and quota-bounded writable
`/workspace` and `/tmp` tmpfs mounts.  The workload inherits no host
environment, has no host devices or sockets, runs with no-new-privileges, an
enforcing AppArmor profile and a syscall filter, and has all capabilities
dropped before its command is executed.

Install on an Ubuntu 24.04 Runner VM:

```text
sudo ./install-hardened-runner.sh
./run-attack-gate.py --output /var/lib/omnibase-runner/gate-report.json
```

The installer does not access a repository `.env`, tenant data, PostgreSQL,
Redis, MinIO, RAG, Docker/Podman sockets, VPNs or Overlay credentials.  It
only installs the files in this directory and starts the dedicated systemd
unit.  A non-zero probe or attack result is a deployment blocker and must not
be converted into a pass by weakening the policy.

At each boot, the root-only `omnibase-runner-host-ns.service` snapshots the
PID 1 user/PID/mount/network namespace device+inode identities into four
root-owned, read-only files under `/run/omnibase-host-ns`.  The Runner can read
but cannot replace these references.  This avoids granting the non-root Runner
permission to inspect PID 1 namespace handles directly while preserving a
trusted comparison point for the runtime probe.
