# P34.7 production admission

This directory is a fail-closed deployment contract, not a service launcher.
It does not make Docker Desktop, WSL or an ordinary container safe for hostile
workspace code.

Run the static contract check without reading evidence or hashing the checkout:

```text
python scripts/production/validate_p34_7_composition.py --validate-only
```

Run the formal provenance/evidence check from a clean public checkout:

```text
python scripts/production/validate_p34_7_composition.py --verify --output /secure/operator/path/p34-7-ab-admission.json
```

Exit codes are `0` for a valid static contract or a formally ready production
admission, `2` for `blocked/not_proven`, and `1` for an invalid contract or a
safety veto.  A `ready` report is evidence for a future deployment controller;
this repository intentionally contains no command that automatically starts or
enables the production Runner, Broker or Gateway after validation.

The checked-in example remains `activation_requested=false`. Its current
external blockers include the current-source Linux Runner 12/12 Gate, the
non-disposable Core/Runner/Broker/Gateway round trips, a real provider-backed
Workspace recovery rehearsal, data-owner-authorized tenant/RAG smoke, two real
member nodes with independent DERP/node-compromise evidence, and production
capacity/fault-injection/SLA samples. Existing disposable and component-level
P34.5/P34.7 evidence is useful engineering evidence but cannot satisfy those
missing production claims.

The validator never reads the root `.env`, secret/certificate payloads, a
database or business storage.  It hashes only paths returned by `git ls-files`
for the explicit source scope.  Runtime credentials remain server-owned and
outside the repository.
