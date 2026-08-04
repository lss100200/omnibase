# P34.7 production Overlay configuration

This directory contains configuration contracts, not production credentials or
evidence. Copy the examples outside the checkout, replace every placeholder with
independently verified production identities, and keep all credentials in the
server-owned secret registry referenced by `credential_reference`.

Each member also declares a pinned Ed25519 attestation public-key file and its
SHA-256. Scored evidence must be signed independently by both members. Private
attestation keys never leave the member hosts.

The production Gate requires all of the following at the same source commit:

- two real, independent target Linux member hosts;
- a production Node Daemon identity on each host, with no test double;
- a DERP host independent from both members and a forced-relay observation;
- a current-source Runner isolation result of exactly `12/12`;
- two independent Network Broker results of exactly `26/26`;
- node revoke, stolen-credential, stale Lease and stale fencing rejection;
- no Sandbox Overlay membership or direct PostgreSQL/Redis/MinIO route;
- the complete fault-injection and capacity/SLA sample set.

`topology.example.json` deliberately contains `placeholder: true`. It can pass
static validation but can never produce a scored production PASS.

Run static validation without contacting any node or provider:

```powershell
python scripts/overlay/p34_7_production_gate.py `
  --repo-root . `
  --config deployment/overlay/production/topology.example.json `
  --report-out C:\tmp\omnibase-p347-overlay-validate.json `
  --validate-only
```

The resulting status is intentionally `blocked/not_proven`. See
`docs/runbooks/p34-7-overlay-sla.md` for the evidence and scored-run workflow.
