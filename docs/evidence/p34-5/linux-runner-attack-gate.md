# P34.5 independent Linux Runner attack Gate

Status: **PENDING / CURRENT SOURCE NOT PROVEN**

The historical Hyper-V Ubuntu 24.04 run passed an 11-case matrix, but it is
bound to launcher SHA-256
`33f4e51feb66a58bd6faed3e54d285fdbce9e67ba722369f969638de6624a969`.
A post-seal audit found that the old launcher validated and digest-bound
`run_as_uid/run_as_gid` while still executing the workload through
`unshare --map-root-user`. The workload therefore ran as namespace UID/GID 0.

The current launcher fixes that defect:

- accepted workload UID/GID values are strict non-bool integers in
  `10000..2^31-1`;
- supplementary groups are cleared before namespace entry;
- `unshare --map-user=<uid> --map-group=<gid>` creates one exact mapping;
- `uid_map`, `gid_map`, `setgroups=deny`, real/effective/saved IDs and
  empty supplementary groups are checked before and after capability drop; and
- invalid/root-like identity requests are rejected by the new `RUN-05` case.

The required current matrix is now 12 cases:

`RUN-03`, `RUN-04`, `RUN-05`, `FS-01`, `FS-02`, `FS-03`,
`NET-01`, `NET-02`, `PROC-01`, `PROC-02`, `HOST-01`, and
`CROSS-01`.

The current launcher SHA-256 is
`fcc4ec5a77bf915cfb1275794e98d183efc0ef2b3ac0ed032f193cfd9e1df10a`.
Because it differs from the historical deployed hash, the old 11/11 result
cannot be reused, renamed, or inferred as a 12/12 pass.

The target VM is visible, but SSH requires a public key whose matching private
key is not available in the current host profile. Interactive console access
and login were not authorized/completed during this run. No current raw
12-case report exists, and `scripts/sandbox/validate_runner_attack_evidence.py`
intentionally rejects this pending record.

Until a real target-host rerun produces a schema-v2 passed summary plus a sealed
12-case raw report:

- production hostile-code Runner activation remains disabled;
- `UnavailableSandboxRunner`, unavailable transport, and rejecting defaults
  remain mandatory;
- Docker Desktop, WSL, unit tests, or source review cannot substitute for the
  Linux Gate; and
- no Sandbox may receive business database, Redis, MinIO, JWT, signing-key,
  root `.env`, host path/socket, or member-Overlay access.

The historical 11-case artifact remains recorded only as obsolete deployment
history. It is not current-source security evidence.
