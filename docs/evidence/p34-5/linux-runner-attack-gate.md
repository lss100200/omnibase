# P34.5 independent Linux Runner attack Gate

Date: 2026-08-02 (Asia/Shanghai)

The P34.5 Linux Runner isolation Gate passed on an independent Hyper-V
Generation 2 Ubuntu 24.04 VM.  The confirmation round was run after restarting
both the root-only host-namespace reference service and the non-root Runner
service.  The post-restart probe returned `ready=true` with no missing controls,
and the complete 11-case attack matrix passed again.

This confirmation uses the fail-safe launcher revision whose complete execute
lifecycle is cleanup-guarded.  Partial cgroup setup, spawn, pipe/selector,
metadata, receipt and evidence failures first write the operation's
`cgroup.kill` and require `cgroup.events` to prove `populated 0`; only then may
the launcher process group, cgroup directory and runtime directory be removed.
If that proof is unavailable, the request fails as cleanup-unproven and keeps
the runtime evidence directory instead of claiming successful cleanup.

The deployed Runner uses:

- a root-owned, SHA-256-pinned launcher;
- a non-root systemd service under `PrivateUsers`, `PrivateMounts`,
  `PrivateNetwork` and a separate PID namespace;
- an enforcing AppArmor profile, systemd outer seccomp filter and per-workload
  seccomp BPF filter;
- `NoNewPrivileges=1`, a closed capability bounding set and all workload
  capabilities dropped before execution;
- one delegated cgroup v2 subtree per `runtime_instance_id`, with CPU, memory,
  swap, PID and wall/output bounds;
- a new user/PID/mount/network namespace set per execution;
- a read-only ephemeral root containing only static BusyBox, with independent
  size- and inode-bounded `/workspace` and `/tmp` tmpfs mounts;
- no host directory, host device, Docker/Podman socket, database credential,
  member Overlay identity or resolver configuration in the workload root; and
- an empty, mode `0600` Runner-owned `replay.sqlite3` location reserved for the
  production mTLS transport replay store.  It is not a business database.

The process-pressure case terminated at `pids.max` before its wall deadline;
this is a stronger bounded result than waiting for timeout.  Its evidence
showed `pids.events max > 0`, and the runtime cgroup subsequently reported
`populated 0`.  The infinite-output case returned
`runner_output_limit_exceeded`, wrote `cgroup.kill`, and also proved
`populated 0`.  After the confirmation matrix, only the service's
`supervisor` cgroup remained and the runtime directory was empty.

Before redeployment, focused RuntimeDriver, transport and deployment-launcher
fault-injection tests passed (`35 passed`).  The deployment tests include
partial cgroup limit writes, spawn failure, selector failure, communicate
failure, evidence-write failure and an unproven-cleanup negative case.  Ruff,
Ruff format and focused Mypy checks also passed.  These local checks supplement
the target-host matrix; they do not replace it.

The exact redacted result and artifact digests are in
`docs/evidence/p34-5/linux-runner-attack-gate.json`.  Raw per-operation outputs
were not copied into the repository.  The VM report contains only synthetic
attack data and remains mode `0600` under the private Runner state directory.

This Gate proves the deployed VM profile represented by the pinned artifact
digests.  It does not turn Docker Desktop or WSL into a hardened Runner, does
not authorize direct tenant database/RAG access, and does not replace the
separate production mTLS identity, durable replay, Network Broker, Overlay and
Gateway authorization Gates.
