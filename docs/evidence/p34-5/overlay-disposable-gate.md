# P34.5C disposable Headscale provider Gate

Run `run-20260802-171322` passed from a fresh Windows clone of commit
`2621759024ddf9e5d84fc96e56d00140287c1db2`. Git applied the repository
line-ending contract before the run: the PowerShell Gate wrapper contained 391
CRLF line endings and no bare LF. The scored source manifest therefore
represents the bytes a public Windows clean checkout actually executes.

## Clean-checkout and source seal

- The dedicated Gate Runner was built from checked-in `backend/uv.lock`,
  the complete copied `backend/src` source set, the required tests, Docker
  build inputs, Gate scripts, and `.gitattributes`.
- No ambient `omnibase-backend:latest` image or external
  `omnibase_backend_venv` volume was used.
- The source manifest contains 161 regular files, rejects symlinks, records a
  clean Git commit/tree, and binds immutable upstream image digests.
- Scored raw source manifest SHA-256:
  `a417d45348a97966dcdfa6fa0c287d6fa228dba1442fa44ac5d13cba77ddd6c5`.
- The repository copy is JSON-equivalent but line-ending-normalized by Git;
  its SHA-256 is
  `a31978cb5b2c7d423379f466fde103b6cec65dacaa47796fd5782f9c99fe54c8`.
- Source tree SHA-256:
  `a7df01e3661a642f6dbc5980b8db22721137ea4332a222fe40bd957ecdcc0f5b`.
- The historical seal verifier passed against the fresh-clone bytes.

## Verified provider behavior

- Pinned Headscale 0.26.1 started on an internal-only disposable network with
  zero published host ports and zero real member devices.
- The mTLS Node-Daemon test double performed real Headscale provider-record
  activate, status, rotate, and revoke mutations.
- Activate created a real provider record; rotate expired the old record and
  created a new active record; revoke expired the current record; status read
  Headscale truth rather than trusting only local state.
- The scored lifecycle produced three provider records and six provider
  mutations.
- A simulated response drop after provider commit was not automatically
  replayed. Stale fencing was rejected before mutation.
- Node-Daemon offline behavior failed closed and reconnect recovery passed.
- Sandbox-facing publication rejected direct endpoints, routes, provider
  credentials, and member identity; only logical service metadata crossed the
  boundary.
- The containment scan found no credential, authorization header, private key,
  raw provider key, or URL credential in artifacts or logs.

## Cleanup and boundaries

Final cleanup proved zero remaining containers, zero networks, and zero
disposable volumes. The Gate did not access a business database, the repository
root `.env`, a real member device, or a real member Overlay.

The raw scored report is:

```text
C:\tmp\omnibase-p345-overlay-gate\run-20260802-171322\report.json
SHA-256 e5b702f8450e34fbc4f368eae338ab4da760ea8af32bc6b80d26069fa6ef4a3e
```

This Gate proves the disposable provider control-plane seam only. It does not
prove a hardened production Node-Daemon, a two-member data plane, DERP relay,
node compromise/revocation, production credential rotation, capacity, SLA, or
P34.7 production readiness. Rejecting/unavailable production defaults remain
mandatory.

## Host diagnostic incident

The scored Gate did not read the repository root `.env`. An earlier unrelated
host diagnostic used a bare Compose config command and implicitly expanded
local development credentials into internal tool output. No credential value
entered repository evidence, a commit, a candidate bundle, or an external
provider, but the deployment owner should still rotate the affected local
development credentials.
