# P34.7 hardened production joint gate

This run-scoped validator is an offline evidence admission boundary. It directly checks Core, Runner, Broker, Gateway, Overlay, and recovery/SLA component evidence; source and artifact manifests are raw-hash bound; command exit codes are recorded; migration head `0012`, all three Phase 5 gates false, and production Runtime inactive are mandatory.

## Status

The checked-in production composition and Overlay examples remain fail-closed and `blocked/not_proven`. This validator does not activate services, access a business database, read the root `.env`, execute hostile code, or turn disposable evidence into production proof.

## Run-scoped bundle

Each operator run must use a unique non-overwriting directory containing the source and artifact files named by the manifests. The evidence JSON must use schema `omnibase.p34-7.hardened-joint-evidence.v1` and include every required command and component. Use:

```text
python scripts/production/validate_p34_7_joint_gate.py --run-dir <run-dir> --evidence <run-dir>/evidence.json --output <run-dir>/report.json
```

Exit `0` means every direct prerequisite was proven. Exit `2` means valid evidence is still `blocked/not_proven`; exit `1` means malformed or unsafe evidence is vetoed. Missing real member nodes, DERP, production mTLS roundtrips, non-disposable tenant/RAG, capacity/SLA, recovery, or compromise evidence must remain blocked.
