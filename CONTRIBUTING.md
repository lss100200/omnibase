# Contributing to OmniBase

Thank you for helping improve OmniBase. The project is in public preview: the
infrastructure baseline is usable and continuously tested, while Workspace,
Sandbox, Overlay Network, and Agent Runtime remain deliberately frozen until
their security gates are implemented.

## Good first contributions

- documentation corrections and clearer deployment instructions;
- deterministic tests and regression fixtures;
- accessibility, localization, and UI reliability fixes;
- maintainer-map coverage and validator improvements;
- small bug fixes that preserve existing API and security contracts.

For new architecture, public APIs, migrations, authentication, tenancy,
controlled-data writes, recovery behavior, capability semantics, or frozen P34
scope, open an Issue or design discussion before writing a large patch.

## Required reading

Coding agents and human contributors must establish context in this order:

1. [`AGENTS.md`](AGENTS.md)
2. [`docs/maintainers/maintenance-map.json`](docs/maintainers/maintenance-map.json)
3. [`docs/maintainers/security-invariants.md`](docs/maintainers/security-invariants.md)
4. [`docs/maintainers/ai-maintainer-map.md`](docs/maintainers/ai-maintainer-map.md)
5. the source, migrations, contracts, and tests listed by the target module
6. [`docs/handover-report.md`](docs/handover-report.md) for current evidence and
   frozen work

If prose and executable behavior disagree, do not guess. Use source, database
constraints, migration behavior, contract snapshots, and tests as runtime
evidence, then correct stale documentation in the same change.

## Development workflow

1. Create a focused branch and keep unrelated local changes out of the patch.
2. Copy `.env.example` to a local ignored `.env`; never commit real credentials.
3. Make the smallest change that solves the demonstrated problem.
4. Preserve tenant predicates, logical/physical identifier separation,
   transaction boundaries, lock order, idempotency, append-only audit behavior,
   fail-closed defaults, and recovery constraints.
5. Run the target module's verification commands from the maintainer map.
6. Update the map in the same patch if an entrypoint, dependency, public
   interface, invariant, verification command, or recovery path changes.
7. Report commands actually run, their results, and anything not verified.

Canonical container-first checks include:

```text
docker compose run --rm --no-deps backend mypy src
docker compose run --rm --no-deps backend pytest -m "not integration" -q
docker compose run --rm --no-deps -v .:/workspace -w /workspace backend python scripts/maintenance/validate_maintainer_map.py --repo-root .
docker compose run --rm --no-deps -v .:/workspace -w /workspace backend python scripts/maintenance/validate_maintainer_benchmark.py --repo-root .
```

Frontend and SDK commands are defined in their package files and in
`.github/workflows/infrastructure-gates.yml`. Destructive database integration
tests may run only through the guarded disposable-database Makefile target.

## Repository hygiene

- Never commit `.env`, credentials, cookies, authorization headers, private
  keys, personal datasets, model weights, or database files.
- Never include `.omo/`, `.zcode/`, `.tmp/`, generated evaluation workspaces,
  `node_modules/`, `.next/`, SDK `dist/`, caches, or local runtime state.
- Do not use `git add .`; stage explicit paths and inspect the cached diff.
- Do not claim tests, migrations, restores, deployments, or tool reads that
  were not actually executed and recorded.
- AI-assisted contributions are welcome, but the contributor remains
  responsible for evidence, licensing, security boundaries, and correctness.

## Pull requests

Keep pull requests reviewable. Include:

- the problem and intended behavior;
- affected modules and security invariants;
- migration or compatibility impact;
- tests and verification commands with actual results;
- rollback or recovery notes when stateful behavior changes;
- known limitations and follow-up work.

By contributing, you agree that your contribution is licensed under the
repository's [Apache License 2.0](LICENSE).
