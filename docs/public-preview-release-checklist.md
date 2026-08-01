# OmniBase Public Preview Release Checklist

This checklist separates code readiness from the external act of publishing a
repository. Passing local tests does not authorize a push, make a dirty branch
safe to publish, or turn unfinished Sandbox/Agent Runtime work into a supported
feature.

## Release position

Target label: `Public Preview` / `v0.1.0-alpha.1`.

The preview includes the current knowledge-workbench, RAG, authentication,
tenant-aware API foundations, controlled-data and capability contracts, SDKs,
maintainer map, security invariants, CI gates, and recovery guidance.

The preview explicitly excludes claims of a production-grade arbitrary-code
Sandbox, Overlay Network, Agent Runtime, unrestricted SQL API, business
database migration approval, or autonomous write round.

## Verified locally on 2026-08-01

- Apache-2.0 license present.
- Current worktree scan found no real API key, Bearer value, private key, AWS
  access key, or GitHub token pattern; the root `.env` was not read.
- Backend Mypy: `97 source files`, `0 issues`.
- Backend non-integration tests: `684 passed`, `8 skipped`, `11 deselected`.
- Disposable PostgreSQL gate: migrations through `0006`, downgrade/re-upgrade
  safety `1 passed`, remaining P34 integration tests `46 passed / 1 deselected`;
  the isolated Compose container, network, and tmpfs database were removed.
- Maintainer map validator: `10 invariants`, `12 modules`, `40 entrypoints`,
  `12 discovered HTTP entrypoints`.
- Benchmark protocol validator: `3 plans`, `8 scenarios`, `6 critical`,
  `9 unsafe vetoes`.
- Changed infrastructure scope passes Ruff and Ruff format checks.
- Frontend: `43/43` tests, typecheck, lint, and production build passed.
- TypeScript SDK: independent pnpm frozen install, build, `7/7` tests, and
  typecheck passed.
- `.env.example` empty optional values use separate comment lines and can be
  parsed without turning comments into configuration values.
- `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, `AGENTS.md`, maintainer maps,
  and security invariants provide public maintenance entry points.

## Must be completed before the first public push

1. The first public repository is fixed at
   `https://github.com/lss100200/omnibase`; README uses the exact clone URL.
2. Do **not** push the current branch history as-is. It contains 26 tracked
   `.omo` files and at least one non-placeholder email marker. Preserve the
   current local branch as private engineering history and create a clean
   public baseline from an explicit allowlist.
3. Exclude `.omo/`, `.zcode/`, `.tmp/`, generated benchmark workspaces, model
   weights, local databases, caches, `node_modules/`, `.next/`, SDK `dist/`, and
   local continuation/session state from the public baseline.
4. Decide whether repository-native `skills/` drafts ship in the first preview.
   They must not be described as installed or approved. Their current validator
   reports a lifecycle inconsistency for `omnibase-migration-sentinel`, so the
   safest first preview excludes the draft pack until that result is reviewed.
5. Run a final allowlist secret and personal-data scan against the exact public
   tree and commit object, not merely the current working directory.
6. Create the public baseline commit locally, inspect its complete tree, and
   rerun the CI-equivalent gates from that exact commit.
7. Enable GitHub Private Vulnerability Reporting before changing repository
   visibility to public.
8. Protect the default branch, require the infrastructure-gates workflow, and
   disable force pushes and branch deletion for the default branch.
9. Push only after explicit authorization of the exact remote URL, branch, and
   public visibility. Do not publish provider credentials, model artifacts,
   evaluator runtime artifacts, or local benchmark workspaces.
10. Create a prerelease tag/release with the frozen-boundary warning and known
    limitations; do not label the preview as production-ready or 1.0.

## Recommended clean-baseline contents

Include:

- `.github/`
- `backend/`
- `frontend/`
- `sdk/`
- `scripts/database/` and reviewed `scripts/maintenance/`
- `docs/` excluding local-only generated artifacts
- `AGENTS.md`, `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, `LICENSE`
- `.env.example`, `.gitignore`, Docker Compose files, and `Makefile`

Conditionally include:

- `skills/` only after its draft lifecycle and structural validator are
  internally consistent; never include `_shared/eval-workspaces/`.

Exclude:

- all paths listed in repository `AGENTS.md` under repository safety;
- credentials or personalized test data even if they are technically ignored;
- large local model files and Ollama artifacts;
- old `.omo` history, continuation state, and private execution evidence.

## First-release messaging

The release description should state, in plain language:

- this is a continuously updated public preview;
- the infrastructure baseline is the primary release value;
- APIs and migrations may still evolve before 1.0;
- backup and recovery are the operator's responsibility;
- Sandbox/Agent Runtime claims remain frozen;
- contributors and AI agents must follow the maintainer map and security
  invariants;
- no hosted service, SLA, or warranty is provided.
