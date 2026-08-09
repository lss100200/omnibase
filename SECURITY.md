# OmniBase Security Policy

OmniBase is currently a public preview. The repository contains hardened
authentication, tenant isolation, controlled-data, capability, audit, and
recovery foundations, but it does **not** yet ship a production-grade sandbox
for arbitrary untrusted code. Docker development containers are not a security
boundary for hostile workloads.

## Supported versions

Security fixes are currently made only on the latest default-branch revision.
No compatibility or security support is promised for old commits, local forks,
or unreleased roadmap capabilities.

## Reporting a vulnerability

Use GitHub Private Vulnerability Reporting for this repository. Repository
owners must enable **Security → Private vulnerability reporting** before making
the repository public.

Do not open a public Issue for a suspected vulnerability. A useful report
includes:

- the affected commit and deployment mode;
- the relevant endpoint, capability, tenant/workspace boundary, or migration;
- minimal reproduction steps using synthetic data;
- expected and observed behavior;
- impact and likely blast radius;
- logs with credentials, cookies, authorization headers, physical database
  locators, personal data, and local `.env` values removed.

Do not access another person's data, test against systems you do not own, run
destructive database operations outside the guarded disposable-test workflow,
or publish an exploit before maintainers have had a reasonable opportunity to
investigate.

## High-priority security areas

Reports are especially important when they involve:

- authentication, token validation, or live-principal revalidation;
- tenant/workspace isolation or cross-tenant access;
- logical-to-physical database locator disclosure;
- approval, operation, idempotency, audit, or transaction bypass;
- Capability Gateway attestation, scope, budget, expiry, or revocation;
- unsafe migration, backup, or in-place restore behavior;
- secret exposure through logs, errors, SDK DTOs, frontend bundles, or Git;
- a claim that Sandbox, Overlay Network, or Agent Runtime isolation is ready for
  production while the total P34.7 production Gate remains `blocked/not_proven`.

## Maintainer response

Maintainers will acknowledge reports on a best-effort basis, reproduce the
issue in an isolated environment, record the affected security invariant, and
prefer a coordinated fix with tests, maintainer-map updates, recovery guidance,
and a public advisory when appropriate. Public preview currently carries no
formal response-time SLA.

The repository's machine-readable maintenance entry point is
[`AGENTS.md`](AGENTS.md); the normative security boundaries are in
[`docs/maintainers/security-invariants.md`](docs/maintainers/security-invariants.md).
