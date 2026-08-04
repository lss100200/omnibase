# P34.5C disposable Headscale provider Gate

Run `run-20260802-190724` passed from a fresh ordinary Windows clone of
commit `cc48baa9bbd78d8824393311220ba523dfb186de` (tree
`fd6e2b3ef0e390a9879c5cb4fa1b845ff1a42d62`). Git applied the repository
checkout contract before execution: non-empty Python files and all shell
scripts were LF, PowerShell scripts were CRLF, the clone was clean, and
`core.autocrlf=true`.

## Clean-checkout and source seal

- The dedicated Gate Runner was built from the public checkout, locked Backend
  dependencies, complete required source/tests, Docker inputs, Gate scripts,
  `.gitattributes`, and pinned upstream image digests.
- No ambient Backend image, external virtual environment, host source mount,
  real member device, published host port, or business database was used.
- Source manifest SHA-256:
  `d0d1f54c08629f7d6158d143f1db928197648403e36b3598e01be54e9a8d8740`.
- Source tree SHA-256:
  `8cce097c80959061cef3f3751979ca99eeea723b9942cc29a68e2dedde02470f`.
- Git source was clean and bound to the exact commit/tree above.

## Verified provider behavior

- Pinned Headscale 0.26.1 ran on an internal-only disposable network.
- The mTLS Node-Daemon test double performed real provider-record activate,
  status, rotate, and revoke mutations.
- Activate created a record; rotate expired the old record and created the new
  active record; revoke expired the current record; status used Headscale
  truth.
- The lifecycle produced three provider records and six provider API
  mutations. Receipts were redacted.
- A simulated ambiguous provider response was not replayed automatically.
- Offline fail-closed behavior and reconnect recovery passed.
- Containment and configuration-seal checks passed.

## Cleanup and boundaries

- Formal report SHA-256:
  `246f1d9b9a8bddcf9517cc7d0361ec6699660faf7a17785cecf24549216c3f38`.
- Cleanup proved remaining containers/networks/disposable volumes `0/0/0`.
- Root `.env` accessed by the Gate: `false`.
- Business database accessed: `false`.
- Real member devices: `0`; published host ports: `0`.

This disposable Gate proves the provider control-plane seam for the sealed
source only. It does not prove a hostile-code production Runner, real member
data plane, DERP behavior, node compromise/revocation, production credential
rotation, non-disposable tenant/RAG, capacity/SLA, or P34.7 readiness.

An earlier host diagnostic in this project history implicitly expanded root
`.env` through a bare Compose config command. That historical incident remains
separate from this scored Gate's `root_env_accessed_by_script=false` result and
must not be erased or reinterpreted.
