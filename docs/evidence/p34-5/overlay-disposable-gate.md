# P34.5C disposable Headscale provider Gate

Run `run-20260802-034054` passed against pinned Headscale `0.26.1` and an
ephemeral mTLS Node-Daemon test double. Unlike the earlier co-residency Gate,
this run proves a real causal chain from the OmniBase adapter, through the
Node-Daemon wire contract, into Headscale control-plane state.

The precise scope is important: this is a disposable integration Gate using a
Node-Daemon test double. It is not a production Node-Daemon deployment, a real
member Overlay, a public relay deployment, or evidence that arbitrary Sandbox
traffic is safe.

## Verified provider behavior

- Headscale started with isolated SQLite state and HTTP/gRPC reachable only on
  the disposable internal Docker network. No host port was published and no
  real member device was registered.
- The Gate created one disposable Headscale user and a 30-minute API key. The
  API key existed only in Gate process memory before being sent through stdin
  into a disposable provider-secret volume.
- The provider-secret volume was mounted read-only only by the Node-Daemon.
  Gate Runner/Sandbox code never mounted it. The volume was destroyed by final
  `down -v` cleanup.
- `activate` caused the Node-Daemon to create a real Headscale preauth-key
  record and then verified that record as active from Headscale.
- `rotate` created a new Headscale record, expired the old record, and verified
  the old/new revoked/active states from Headscale.
- `revoke` expired the current Headscale record and `status` observed the
  revoked state from Headscale rather than trusting only `state.json`.
- The scored lifecycle produced three provider records and six successful
  provider mutations. Raw API/preauth keys were never included in receipts,
  state probes, reports, logs, or repository evidence.
- A simulated response drop after provider commit crossed the real Headscale
  mutation boundary. The durable SQLite operation ledger then rejected an
  automatic replay of the same operation, so Headscale was not mutated twice.
- Stale fencing was rejected before provider mutation. Stopping and restarting
  the Node-Daemon proved fail-closed offline behavior and reconnect recovery.
- Sandbox-facing publication still rejected direct endpoints, routes, raw
  provider credentials, and member identity. Only logical service metadata
  crossed the Overlay-to-Broker boundary.

## Configuration and containment

- Every Compose invocation, including final cleanup, used the dedicated
  comment-only `deployment/overlay/gate.env`; the repository root `.env` was
  not an implicit Compose source for this scored run.
- Gate Runner image and venv volume were fixed in Compose. Before startup the
  image ID was checked against
  `sha256:406d67c19d7133bfefd4594dd6fa36e5aa4d8908ae1a605906139dfed9cea6f0`.
- `scripts/overlay/validate_disposable_gate.py` verified the explicit env file,
  fixed image/volume, Node-Daemon-only provider secret mount, stdin injection,
  provider mutation evidence, and cleanup checks.
- The final containment scan passed. It found no authorization header, bearer
  credential, private key, raw provider key, URL credential, or other real
  secret in logs, inspected container metadata, source artifacts, or the
  report.

## Cleanup and artifact

Final cleanup proved zero remaining containers, zero networks, and zero
disposable volumes for project `omnibase-p345-overlay-gate`. The Gate did not
access a business database, a real member device, or a real member Overlay.

The sealed report is:

```text
C:\tmp\omnibase-p345-overlay-gate\run-20260802-034054\report.json
SHA-256 3fe977b41ef403558d88d1819e1a3488149060ba853676afca802993f1733eac
```

## Remaining production boundary

This Gate closes the disposable provider-control-plane evidence gap. Production
still requires a hardened Node-Daemon implementation and deployment, real
member-node/relay failure and compromise tests, production credential rotation,
and the wider P34.5 A/B/D attack and data-access Gates. Until those pass, the
unavailable/rejecting production defaults remain mandatory.

## Host diagnostic incident

The Gate script itself did not read the repository root `.env`. Earlier in the
same P34.5 host-diagnostic work, a bare root `docker compose config` command
caused Compose to load and expand local development credentials into an
internal sub-agent tool output. No credential value was persisted in repository
files or Gate artifacts, committed, pushed, placed in a candidate bundle, or
sent to an external provider. The affected local development credentials should
nevertheless be treated as exposed and rotated by the deployment owner.
