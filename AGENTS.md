# OmniBase AI Maintainer Contract

This file is the repository entry point for coding agents. Its purpose is to
make OmniBase repairable from the public source tree without relying on a
private conversation, hidden workspace state, or an original maintainer's
memory.

## Read order

Before changing architecture, authentication, tenancy, migrations, P34, SDKs,
or recovery tooling, read these sources in order:

1. `AGENTS.md`
2. `docs/maintainers/maintenance-map.json`
3. `docs/maintainers/security-invariants.md`
4. `docs/maintainers/ai-maintainer-map.md`
5. The module source, migrations, contracts, and tests named by the map
6. `docs/handover-report.md` for current phase status and verified evidence

If prose and executable behavior disagree, do not guess. Treat source,
database constraints, migrations, contract snapshots, and passing tests as the
runtime evidence; then correct the stale documentation in the same change.

## Non-negotiable boundaries

- Browser and user API traffic enters through `backend/src/omnibase/main.py`
  under `/api/v1`. The root `/health` route is a probe-only compatibility path.
- The Capability Gateway is a separate non-browser ASGI application created by
  `backend/src/omnibase/capability_gateway/app.py:create_gateway_app`. Its
  defaults reject all workloads until trusted attestation and verification
  components are injected. Do not silently mount it into the browser app.
- JWT claims are necessary but not sufficient. Protected browser requests must
  revalidate the live tenant, live user, current role, and tenant schema through
  `backend/src/omnibase/tenants/dependencies.py:get_current_principal`.
- External callers use logical resource, table, column, operation, and grant
  identifiers. Physical PostgreSQL schema/table/column locators must remain
  server-owned and must not appear in public DTOs, SDKs, logs, or errors.
- Capability authorization is tenant/workspace/runtime/action/resource/version
  bound, time limited, budget limited, revocable, and fail-closed. Never replace
  verification with possession of a raw identifier or a browser cookie.
- High-risk operations, approval consumption, idempotency, auditing, and data
  mutation are one security system. Do not update one record in isolation when
  the current service performs an atomic lifecycle transition.
- Audit records are append-only. Migration `0006` installs database enforcement;
  application code must not treat ORM discipline as the only protection.
- Migration scope is a closed set: `global` or `tenant`. Unknown or missing
  scope must fail closed. Production recovery is forward-fix or restore into a
  new `omnibase_restore_*` database, never destructive in-place guessing.
- P34.4/P34.5 Workspace Runtime, Sandbox, Overlay Network, and Agent Runtime are
  frozen until the roadmap and handover explicitly unlock them.

## Safe change workflow

1. Locate the target module in `docs/maintainers/maintenance-map.json`.
2. Read every listed invariant and dependency before editing.
3. Preserve tenant predicates, logical/physical identifier separation,
   transaction boundaries, lock order, idempotency, audit writes, and
   fail-closed behavior.
4. Make the smallest change that fixes the proven issue. Do not hide type
   errors with broad `Any`, global ignores, or unexplained `type: ignore`.
5. Run the module's `verification` commands from the map, then the wider gate
   when a shared boundary changed.
6. If entrypoints, dependencies, public interfaces, invariants, or recovery
   steps changed, update the maintainer map in the same commit.
7. Report what was verified and what was not. Never claim a production
   migration, backup restore, Skill installation, or deployment without direct
   evidence.

## Repository safety

- Do not read, print, stage, or commit the root `.env` unless the user explicitly
  requests a secret-specific operation. Use `.env.example` for configuration
  shape.
- Never stage `.omo/`, `.zcode/`, `.tmp/`, generated eval workspaces, model
  weights, `node_modules/`, `.next/`, SDK `dist/`, or local database material.
- Do not use `git add .`. Stage explicit paths and inspect the cached diff.
- Do not run destructive database tests against a normal database. Use the
  sentinel Compose project and `omnibase_test_*` names enforced by the Makefile.
- Do not push, publish, deploy, rotate credentials, or migrate a business
  database unless the user explicitly authorizes that external state change.

## Canonical verification entrypoints

The project is container-first; a host Python environment is not required.

```text
docker compose run --rm --no-deps backend mypy src
docker compose run --rm --no-deps backend pytest -m "not integration" -q
docker compose run --rm --no-deps -v .:/workspace -w /workspace backend python scripts/maintenance/validate_maintainer_map.py --repo-root .
docker compose run --rm --no-deps -v .:/workspace -w /workspace backend python scripts/maintenance/validate_maintainer_benchmark.py --repo-root .
```

The exact focused Ruff baseline enforced by CI is in
`.github/workflows/infrastructure-gates.yml`; module-specific commands are in the
machine map. Run `ruff check` and `ruff format --check` with explicit changed or
map-listed Python paths; never pass an unexpanded placeholder to the shell. Do
not claim full-repository Ruff cleanliness unless `ruff check src tests` was
actually run and passed. Database-destructive integration tests
must use the guarded Makefile target and its explicit disposable
credentials/project name. Frontend and SDK commands are listed in the map
because their package managers and working directories differ.
